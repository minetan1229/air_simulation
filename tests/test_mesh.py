"""Numerical verification of :mod:`physics.mesh_domain`.

Every fixture is built in memory; only :class:`MeshLibrary` touches the disk and
it always uses pytest's ``tmp_path``.  Where an analytic result exists it is
compared against the implementation (relative tolerance 1e-12 for exact
polyhedral integrals, 1e-9 for the inertia tensor); where none exists the test
only asserts documented invariants and is explicit about it.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import math
import os
import struct

import numpy as np
import pytest

from physics.config_schema import (
    MESH_METRIC_KEYS,
    PAPER_DENSITY_BASE,
    AircraftConfig,
    as_dict,
)
from physics.geometry_utils import inertia_tensor_box
from physics.mesh_domain import (
    MeshLibrary,
    build_panels,
    compute_metrics,
    detect_format,
    inertia_tensor_mesh,
    is_closed_mesh,
    load_geometry,
    mass_properties,
    mesh_hash_of,
    mesh_metric_values,
    panel_aerodynamics,
    paper_mass_from_mesh,
    parse_gltf,
    parse_obj,
    parse_stl,
    parse_stl_ascii,
    parse_stl_binary,
    planform_metrics,
    scale_factor_for,
    signed_volume,
    surface_area,
    surface_centroid,
    triangle_normals,
    volume_centroid,
)

TRIMESH_AVAILABLE = importlib.util.find_spec("trimesh") is not None

BOX_FACES = (
    (0, 3, 2, 1),
    (4, 5, 6, 7),
    (0, 1, 5, 4),
    (1, 2, 6, 5),
    (2, 3, 7, 6),
    (3, 0, 4, 7),
)


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------
def box_mesh(dx, dy, dz, offset=(0.0, 0.0, 0.0)):
    """Closed axis aligned box with **shared** vertices and outward windings."""
    ox, oy, oz = (float(v) for v in offset)
    vertices = np.array(
        [
            [0, 0, 0],
            [dx, 0, 0],
            [dx, dy, 0],
            [0, dy, 0],
            [0, 0, dz],
            [dx, 0, dz],
            [dx, dy, dz],
            [0, dy, dz],
        ],
        dtype=np.float64,
    )
    vertices = vertices + np.array([ox, oy, oz])
    faces = np.array(
        [[quad[0], quad[i], quad[i + 1]] for quad in BOX_FACES for i in (1, 2)],
        dtype=np.int64,
    )
    return vertices, faces


def tetra_mesh():
    """Unit tetrahedron, outward windings."""
    vertices = np.array(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float64
    )
    faces = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], dtype=np.int64)
    return vertices, faces


def flat_wing(span=0.20, root_chord=0.05, tip_chord=0.03, nx=8, ny=8, z=0.0):
    """Single sided tapered wing; x is **forward**, so the LE sits at +x."""
    vertices = []
    for i in range(nx + 1):
        fx = i / nx
        for j in range(ny + 1):
            fy = j / ny
            y = -0.5 * span + fy * span
            chord = root_chord + (tip_chord - root_chord) * fy
            vertices.append((chord - fx * chord, y, z))
    faces = []
    stride = ny + 1
    for i in range(nx):
        for j in range(ny):
            a = i * stride + j
            b = a + 1
            c = a + stride
            d = c + 1
            faces.append([a, d, b])
            faces.append([a, c, d])
    return np.array(vertices, dtype=np.float64), np.array(faces, dtype=np.int64)


def ascii_stl(vertices, faces) -> str:
    lines = ["solid test"]
    for tri in faces:
        normal = triangle_normals(vertices, tri[None, :])[0]
        lines.append(f"  facet normal {normal[0]:.9e} {normal[1]:.9e} {normal[2]:.9e}")
        lines.append("    outer loop")
        for corner in vertices[np.asarray(tri)]:
            lines.append(
                f"      vertex {corner[0]:.9e} {corner[1]:.9e} {corner[2]:.9e}"
            )
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append("endsolid test")
    return "\n".join(lines) + "\n"


def binary_stl(vertices, faces, header: bytes = b"solid not really ascii") -> bytes:
    count = len(faces)
    out = bytearray()
    out += header.ljust(80, b"\x00")[:80]
    out += struct.pack("<I", count)
    for tri in faces:
        normal = triangle_normals(vertices, tri[None, :])[0]
        out += struct.pack("<3f", *normal)
        for corner in vertices[tri]:
            out += struct.pack("<3f", *corner)
        out += struct.pack("<H", 0)
    return bytes(out)


def gltf_document(positions, indices, matrix=None, byte_stride=None):
    """Tiny glTF 2.0 document with one base64 ``data:`` buffer."""
    position_array = np.asarray(positions, dtype=np.float32)
    index_array = np.asarray(indices, dtype=np.uint16)
    if byte_stride is None:
        position_blob = position_array.tobytes()
        position_stride = None
    else:
        # Interleaved POSITION/normal pairs: [x, y, z, nx, ny, nz].
        normals = np.tile(np.array([[0.0, 0.0, 1.0]], np.float32), (len(position_array), 1))
        interleaved = np.hstack([position_array, normals]).astype(np.float32)
        position_blob = interleaved.tobytes()
        position_stride = byte_stride
    payload = position_blob + index_array.tobytes()
    uri = "data:application/octet-stream;base64," + base64.b64encode(payload).decode("ascii")

    buffer_views = [
        {"buffer": 0, "byteOffset": 0, "byteLength": len(position_blob)},
        {
            "buffer": 0,
            "byteOffset": len(position_blob),
            "byteLength": len(index_array.tobytes()),
        },
    ]
    accessors = [
        {
            "bufferView": 0,
            "componentType": 5126,
            "count": int(position_array.shape[0]),
            "type": "VEC3",
            "min": [float(v) for v in position_array.min(axis=0)],
            "max": [float(v) for v in position_array.max(axis=0)],
        },
        {
            "bufferView": 1,
            "componentType": 5123,
            "count": int(index_array.shape[0]),
            "type": "SCALAR",
        },
    ]
    if position_stride is not None:
        buffer_views[0]["byteStride"] = int(position_stride)
    node = {"mesh": 0}
    if matrix is not None:
        node["matrix"] = [float(v) for v in matrix]
    document = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [node],
        "meshes": [
            {
                "primitives": [
                    {"attributes": {"POSITION": 0}, "indices": 1, "mode": 4}
                ]
            }
        ],
        "buffers": [{"byteLength": len(payload), "uri": uri}],
        "bufferViews": buffer_views,
        "accessors": accessors,
    }
    return document


def glb_bytes(document) -> bytes:
    json_chunk = json.dumps(document).encode("utf-8")
    json_chunk += b" " * ((4 - len(json_chunk) % 4) % 4)
    total = 12 + 8 + len(json_chunk)
    out = bytearray(b"glTF")
    out += struct.pack("<II", 2, total)
    out += struct.pack("<II", len(json_chunk), 0x4E4F534A)
    out += json_chunk
    return bytes(out)


# --------------------------------------------------------------------------
# Analytic polyhedral integrals
# --------------------------------------------------------------------------
def test_cube_surface_area_volume_and_closure():
    for side in (2.0, 0.37, 1.0e-3):
        vertices, faces = box_mesh(side, side, side)
        assert is_closed_mesh(vertices, faces) is True
        assert surface_area(vertices, faces) == pytest.approx(
            6.0 * side * side, rel=1e-12
        )
        assert abs(signed_volume(vertices, faces)) == pytest.approx(
            side**3, rel=1e-12
        )


def test_cube_volume_centroid_is_the_centre():
    vertices, faces = box_mesh(2.0, 0.5, 1.25, offset=(3.0, -2.0, 7.0))
    cg = volume_centroid(vertices, faces)
    expected = np.array([3.0 + 1.0, -2.0 + 0.25, 7.0 + 0.625])
    assert np.max(np.abs(cg - expected)) < 1e-12


def test_unit_tetrahedron_volume_and_centroid():
    vertices, faces = tetra_mesh()
    assert is_closed_mesh(vertices, faces) is True
    assert abs(signed_volume(vertices, faces)) == pytest.approx(1.0 / 6.0, rel=1e-12)
    cg = volume_centroid(vertices, faces)
    assert np.max(np.abs(cg - np.array([0.25, 0.25, 0.25]))) < 1e-12


def test_surface_centroid_of_a_lopsided_triangle():
    vertices = np.array([[0.0, 0.0, 0.0], [3.0, 0.0, 0.0], [0.0, 0.0, 4.0]])
    faces = np.array([[0, 1, 2]], dtype=np.int64)
    cg = surface_centroid(vertices, faces)
    assert np.allclose(cg, (1.0, 0.0, 4.0 / 3.0), atol=1e-12)


def test_inertia_tensor_matches_analytic_box():
    for dims, offset in (
        ((2.0, 2.0, 2.0), (0.0, 0.0, 0.0)),
        ((0.4, 1.2, 0.25), (2.0, 1.0, -3.0)),
        ((1.5, 0.05, 0.02), (3.0, -2.0, 7.0)),
    ):
        vertices, faces = box_mesh(*dims, offset=offset)
        cg = np.asarray(offset, dtype=np.float64) + np.asarray(dims) / 2.0
        tensor = inertia_tensor_mesh(vertices, faces, 1.0, cg)
        analytic = inertia_tensor_box(1.0, *dims)
        scale = float(np.max(np.abs(analytic)))
        assert np.max(np.abs(tensor - analytic)) / scale < 1e-9
        assert np.max(np.abs(tensor - tensor.T)) < 1e-18
        assert np.min(np.linalg.eigvalsh(tensor)) > 0.0


def test_tetrahedron_inertia_is_symmetric_positive_definite():
    vertices, faces = tetra_mesh()
    tensor = inertia_tensor_mesh(vertices, faces, 1.0, (0.25, 0.25, 0.25))
    assert np.max(np.abs(tensor - tensor.T)) < 1e-18
    assert np.min(np.linalg.eigvalsh(tensor)) > 0.0
    # Unverified in closed form: only the invariance and the positivity are
    # asserted here, the box comparison above carries the analytic check.
    shifted = inertia_tensor_mesh(vertices, faces, 2.5, (0.25, 0.25, 0.25))
    assert np.allclose(shifted, tensor, rtol=1e-12)


def test_mass_properties_closed_cube():
    vertices, faces = box_mesh(0.5, 0.5, 0.5)
    props = mass_properties(vertices, faces, PAPER_DENSITY_BASE, cg_mode="volume")
    assert props["is_closed"] is True
    assert props["cg_mode_used"] == "volume"
    assert props["abs_volume_m3"] == pytest.approx(0.125, rel=1e-12)
    assert props["mass_kg"] == pytest.approx(PAPER_DENSITY_BASE * 0.125, rel=1e-12)
    assert props["surface_area_m2"] == pytest.approx(1.5, rel=1e-12)
    assert np.allclose(props["cg_m"], (0.25, 0.25, 0.25), atol=1e-12)


def test_mass_properties_open_mesh_falls_back_to_surface_centroid():
    vertices, faces = flat_wing()
    props = mass_properties(vertices, faces, PAPER_DENSITY_BASE, cg_mode="volume")
    assert props["is_closed"] is False
    assert props["cg_mode_used"] == "surface"
    assert props["cg_mode_requested"] == "volume"
    assert np.all(np.isfinite(props["cg_m"]))

    forced = mass_properties(vertices, faces, PAPER_DENSITY_BASE, cg_mode="surface")
    assert np.allclose(forced["cg_m"], props["cg_m"], atol=1e-15)


def test_degenerate_inputs_never_raise_or_produce_nan():
    zero_area = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
    faces = np.array([[0, 1, 2]], dtype=np.int64)
    assert surface_area(zero_area, faces) == 0.0
    assert signed_volume(zero_area, faces) == 0.0
    assert np.all(np.isfinite(volume_centroid(zero_area, faces)))
    assert np.all(np.isfinite(surface_centroid(zero_area, faces)))

    props = mass_properties(zero_area, faces, PAPER_DENSITY_BASE)
    assert np.all(np.isfinite(props["cg_m"]))
    assert props["mass_kg"] == 0.0
    assert props["is_closed"] is False

    metrics = compute_metrics(zero_area, faces, name="degenerate")
    assert np.all(np.isfinite(np.asarray(list(mesh_metric_values(metrics).values()))))
    assert metrics.panel_count >= 1

    assert build_panels(zero_area, faces) != []


def test_empty_mesh_is_rejected_by_the_metric_pipeline():
    empty_v = np.zeros((0, 3))
    empty_f = np.zeros((0, 3), dtype=np.int64)
    assert build_panels(empty_v, empty_f) == []
    assert surface_area(empty_v, empty_f) == 0.0
    flat = np.asarray(inertia_tensor_mesh(empty_v, empty_f, 1.0, (0.0, 0.0, 0.0)))
    assert flat.shape == (3, 3)
    assert flat.ravel().tolist() == [0.0] * 9
    # A completely empty mesh has no principal frame: the failure is explicit
    # rather than a silent zero-filled metric record.
    with pytest.raises(ValueError):
        compute_metrics(empty_v, empty_f)


# --------------------------------------------------------------------------
# Planform
# --------------------------------------------------------------------------
def test_flat_wing_planform_metrics():
    span, root, tip = 0.20, 0.05, 0.03
    vertices, faces = flat_wing(span=span, root_chord=root, tip_chord=tip)
    metrics = planform_metrics(vertices, faces)

    exact_area = 0.5 * (root + tip) * span
    assert metrics["planform_area_m2"] == pytest.approx(exact_area, rel=1e-12)
    assert metrics["mean_chord_m"] == pytest.approx(
        metrics["planform_area_m2"] / metrics["wingspan_m"], rel=1e-15
    )
    assert metrics["aspect_ratio"] == pytest.approx(
        metrics["wingspan_m"] ** 2 / metrics["planform_area_m2"], rel=1e-15
    )
    assert metrics["taper_ratio"] == pytest.approx(
        metrics["tip_chord_m"] / metrics["root_chord_m"], rel=1e-12
    )
    # The PCA span axis is very slightly tilted by the chordwise variance, so
    # the span/chord values are only checked against the design values loosely
    # (the exact relations above are the strict, analytic part).
    assert metrics["wingspan_m"] == pytest.approx(span, rel=2e-2)
    assert metrics["root_chord_m"] == pytest.approx(root, rel=0.15)
    assert metrics["tip_chord_m"] == pytest.approx(tip, rel=0.15)
    assert metrics["percentile_low"] == 5.0 and metrics["percentile_high"] == 95.0


def test_closed_box_planform_reports_the_silhouette_not_the_double_count():
    vertices, faces = box_mesh(1.0, 1.0, 1.0)
    metrics = planform_metrics(vertices, faces)
    assert metrics["planform_area_m2"] == pytest.approx(1.0, rel=1e-12)


def wing_prism(root_chord=120.0, tip_chord=60.0, span=150.0, thickness=4.0):
    """Closed tapered wing prism with only two span stations (a stress case).

    The PCA span axis is tilted by the taper, so the outermost 10 % band holds
    vertices that all share one chord coordinate -- exactly the case that used
    to collapse ``root_chord_m``/``tip_chord_m`` to zero.
    """
    vertices = np.array(
        [
            (x, y, z)
            for chord, y in ((root_chord, 0.0), (tip_chord, span))
            for x, z in ((0.0, 0.0), (chord, 0.0), (chord, thickness), (0.0, thickness))
        ],
        dtype=np.float64,
    )
    quads = ((0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (3, 2, 6, 7), (1, 5, 6, 2), (0, 3, 7, 4))
    faces = np.array(
        [[q[0], q[i], q[i + 1]] for q in quads for i in (1, 2)], dtype=np.int64
    )
    return vertices, faces


def test_tapered_wing_prism_reports_a_real_taper_ratio():
    root_chord, tip_chord, span = 120.0, 60.0, 150.0
    vertices, faces = wing_prism(root_chord, tip_chord, span)
    assert is_closed_mesh(vertices, faces) is True
    metrics = planform_metrics(vertices, faces)

    assert metrics["taper_ratio"] > 0.0
    assert metrics["root_chord_m"] > metrics["tip_chord_m"]
    assert metrics["root_chord_m"] == pytest.approx(root_chord, rel=0.1)
    assert metrics["tip_chord_m"] == pytest.approx(tip_chord, rel=0.1)
    assert metrics["taper_ratio"] == pytest.approx(0.5, rel=0.05)
    # Closed prism -> the silhouette area, not the double counted front+back.
    assert metrics["planform_area_m2"] == pytest.approx(
        0.5 * (root_chord + tip_chord) * span, rel=1e-12
    )
    assert metrics["aspect_ratio"] == pytest.approx(
        metrics["wingspan_m"] ** 2 / metrics["planform_area_m2"], rel=1e-15
    )
    # Documented limitation: the span is the PCA-projection extent, which the
    # taper tilts past the pure geometric span (sqrt(150^2 + 30^2) = 153 here).
    assert metrics["wingspan_m"] >= span * 0.9
    assert metrics["wingspan_m"] < span * 1.3


# --------------------------------------------------------------------------
# Format / unit detection
# --------------------------------------------------------------------------
def test_scale_factor_for_exact_values():
    assert scale_factor_for("m") == 1.0
    assert scale_factor_for("cm") == 0.01
    assert scale_factor_for("mm") == 0.001
    assert scale_factor_for("in") == 0.0254
    assert scale_factor_for("  MM ") == 0.001
    for bad in ("km", "furlong", "", "miles"):
        with pytest.raises(ValueError):
            scale_factor_for(bad)


def test_detect_format_by_magic_and_extension():
    vertices, faces = tetra_mesh()
    obj_text = "v 0 0 0\nv 1 0 0\nv 0 1 0\nv 0 0 1\nf 1 3 2\nf 1 2 4\nf 1 4 3\nf 2 3 4\n"
    assert detect_format("x.obj", obj_text.encode()) == "obj"
    assert detect_format("x.obj", ascii_stl(vertices, faces).encode()) == "stl"
    assert detect_format("noext", ascii_stl(vertices, faces).encode()) == "stl"
    assert detect_format("x.glb", b"glTF" + b"\x00" * 32) == "glb"
    assert detect_format("x.gltf", json.dumps({"asset": {"version": "2.0"}}).encode()) == "gltf"
    assert detect_format("lying.stl", binary_stl(vertices, faces)) == "stl"
    with pytest.raises(ValueError):
        detect_format("x.bin", b"\x00\x01\x02\x03not a mesh at all")
    with pytest.raises(ValueError):
        detect_format("x.unknown", b"")


# --------------------------------------------------------------------------
# OBJ
# --------------------------------------------------------------------------
OBJ_QUAD_CUBE = """# a cube with a quad face and a sloppy normal attribute
mtllib none.mtl
o cube
v 0.0 0.0 0.0
v 2.0 0.0 0.0
v 2.0 1.0 0.0
v 0.0 1.0 0.0
v 0.0 0.0 1.0
v 2.0 0.0 1.0
v 2.0 1.0 1.0
v 0.0 1.0 1.0
vt 0.0 0.0
vn 0.0 0.0 1.0
usemtl paper
s off
f 1/1/1 4/1/1 3/1/1 2/1/1
f 5//1 6//1 7//1 8//1
f 1//1 2//1 6//1 5//1
f 2 3 7 6
f 3 4 8 7
f 4 1 5 8
"""


def test_parse_obj_counts_and_quad_triangulation():
    vertices, faces = parse_obj(OBJ_QUAD_CUBE)
    assert vertices.dtype == np.float64 and faces.dtype == np.int64
    assert vertices.shape == (8, 3)
    assert faces.shape == (12, 3)  # six quads -> two triangles each
    assert int(faces.max()) == 7 and int(faces.min()) == 0
    assert is_closed_mesh(vertices, faces) is True
    # 2 x 1 x 1 box -> 2 * (2*1 + 2*1 + 1*1) = 10 m^2
    assert surface_area(vertices, faces) == pytest.approx(10.0, rel=1e-12)
    assert abs(signed_volume(vertices, faces)) == pytest.approx(2.0, rel=1e-12)


def test_parse_obj_negative_relative_indices_and_pentagon():
    text = (
        "v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0.5 1.0 0.7\nv 0 1 0\n"
        "f -5 -4 -3 -2 -1\n"  # pentagon, all relative
    )
    vertices, faces = parse_obj(text)
    assert vertices.shape == (5, 3)
    assert faces.shape == (3, 3)  # pentagon -> three triangles
    assert int(faces.max()) == 4 and int(faces.min()) == 0
    # Independent recomputation: fan from corner 0 gives triangles (0,1,2),
    # (0,2,3) and (0,3,4).
    fan = [np.array([0, 1, 2]), np.array([0, 2, 3]), np.array([0, 3, 4])]
    expected = 0.0
    for tri in fan:
        a, b, c = vertices[tri[0]], vertices[tri[1]], vertices[tri[2]]
        expected += 0.5 * float(np.linalg.norm(np.cross(b - a, c - a)))
    assert surface_area(vertices, faces) == pytest.approx(expected, rel=1e-12)
    assert expected == pytest.approx(1.4846430886726, rel=1e-9)


def test_parse_obj_deduplicates_vertices_and_skips_unknown_tags():
    text = (
        "g group\n"
        "v 0 0 0\nv 0 0 0\nv 1 0 0\nv 0 1 0\n"
        "vp 0.5 0.5 0.5\nvt 0 0\nvn 0 0 1\nl 1 2\nusemtl x\n"
        "f 1 2 3  # trailing comment\n"
    )
    vertices, faces = parse_obj(text)
    assert vertices.shape == (3, 3)
    assert faces.shape == (1, 3)


def test_parse_obj_rejects_broken_input():
    with pytest.raises(ValueError):
        parse_obj("v 1 2\nv 0 0 0\n")
    with pytest.raises(ValueError):
        parse_obj("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 9\n")
    with pytest.raises(ValueError):
        parse_obj("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1/2/3 1 1\n".replace("1/2/3", "0/1"))


# --------------------------------------------------------------------------
# STL
# --------------------------------------------------------------------------
def test_ascii_and_binary_stl_agree_on_geometry_and_orientation():
    vertices, faces = tetra_mesh()
    text = ascii_stl(vertices, faces)
    a_verts, a_faces = parse_stl_ascii(text)
    b_verts, b_faces = parse_stl(binary_stl(vertices, faces))

    for parsed in ((a_verts, a_faces), (b_verts, b_faces)):
        assert parsed[0].shape == (4, 3)
        assert parsed[1].shape == (4, 3)
    assert np.max(np.abs(a_verts - b_verts)) < 1e-9
    assert sorted(map(tuple, a_verts.tolist())) == sorted(
        map(tuple, b_verts.tolist())
    )
    # identical outward orientation -> identical signed volume and centroid
    assert signed_volume(*parse_stl(text)) == pytest.approx(1.0 / 6.0, rel=1e-6)
    assert signed_volume(b_verts, b_faces) == pytest.approx(1.0 / 6.0, rel=1e-6)
    assert np.allclose(
        volume_centroid(a_verts, a_faces),
        volume_centroid(b_verts, b_faces),
        atol=1e-6,
    )


def test_binary_stl_with_lying_ascii_header_still_parses_as_binary():
    vertices, faces = tetra_mesh()
    blob = binary_stl(vertices, faces, header=b"solid definitely not ascii")
    assert blob.startswith(b"solid")
    parsed_verts, parsed_faces = parse_stl(blob)
    assert parsed_faces.shape == (4, 3)
    assert abs(signed_volume(parsed_verts, parsed_faces)) == pytest.approx(
        1.0 / 6.0, rel=1e-6
    )
    direct_verts, direct_faces = parse_stl_binary(blob)
    assert np.allclose(parsed_verts, direct_verts)
    assert np.array_equal(parsed_faces, direct_faces)


def test_truncated_binary_stl_raises_a_clear_error():
    vertices, faces = tetra_mesh()
    blob = binary_stl(vertices, faces)
    with pytest.raises(ValueError) as excinfo:
        parse_stl(blob[:-20])
    message = str(excinfo.value)
    assert "binary" in message or "truncated" in message
    with pytest.raises(ValueError):
        parse_stl_binary(blob + b"junk")
    with pytest.raises(ValueError):
        parse_stl_binary(blob[:40])
    with pytest.raises(ValueError):
        parse_stl_ascii("solid empty\nendsolid empty\n")


def test_stl_deduplicates_the_repeated_corner_triangles():
    vertices, faces = box_mesh(1.0, 1.0, 1.0)
    parsed_verts, parsed_faces = parse_stl(ascii_stl(vertices, faces))
    assert parsed_faces.shape == (12, 3)
    assert parsed_verts.shape[0] == 8
    assert surface_area(parsed_verts, parsed_faces) == pytest.approx(6.0, rel=1e-9)


# --------------------------------------------------------------------------
# glTF 2.0 / GLB
# --------------------------------------------------------------------------
def test_parse_gltf_base64_buffer_float32_positions():
    positions = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    indices = [0, 2, 1, 0, 1, 3, 0, 3, 2, 1, 2, 3]
    document = gltf_document(positions, indices)
    payload = json.dumps(document).encode("utf-8")
    vertices, faces = parse_gltf(payload)
    assert vertices.shape == (4, 3)
    assert faces.shape == (4, 3)
    assert np.allclose(
        vertices, np.asarray(positions, dtype=np.float64), rtol=1e-6, atol=1e-6
    )
    assert abs(signed_volume(vertices, faces)) == pytest.approx(1.0 / 6.0, rel=1e-6)


def test_glb_and_gltf_produce_identical_geometry():
    positions = [[0.0, 0.0, 0.0], [0.25, 0.0, 0.0], [0.0, 0.3, 0.0], [0.0, 0.0, 0.1]]
    indices = [0, 2, 1, 0, 1, 3, 0, 3, 2, 1, 2, 3]
    document = gltf_document(positions, indices)
    from_json = parse_gltf(json.dumps(document).encode("utf-8"))
    from_glb = parse_gltf(glb_bytes(document))
    assert np.array_equal(from_json[0], from_glb[0])
    assert np.array_equal(from_json[1], from_glb[1])


def test_parse_gltf_applies_a_non_identity_node_matrix():
    positions = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    indices = [0, 1, 2]
    translate = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 2.0, 3.0, 4.0, 1.0]
    vertices, _faces = parse_gltf(
        json.dumps(gltf_document(positions, indices, matrix=translate)).encode("utf-8")
    )
    assert np.allclose(vertices, np.asarray(positions) + np.array([2.0, 3.0, 4.0]))

    scale_rotate = [
        0.0, -2.0, 0.0, 0.0,
        2.0, 0.0, 0.0, 0.0,
        0.0, 0.0, 5.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]
    vertices, _faces = parse_gltf(
        json.dumps(gltf_document(positions, indices, matrix=scale_rotate)).encode("utf-8")
    )
    # Column major: column 0 = (0, -2, 0), column 1 = (2, 0, 0), column 2 = 5 z.
    assert np.allclose(
        vertices, np.array([[0.0, 0.0, 0.0], [0.0, -2.0, 0.0], [2.0, 0.0, 0.0]])
    )


def test_parse_gltf_handles_interleaved_byte_stride():
    positions = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    indices = [0, 1, 2]
    document = gltf_document(positions, indices, byte_stride=24)  # VEC3 pos + VEC3 nrm
    vertices, faces = parse_gltf(json.dumps(document).encode("utf-8"))
    assert np.allclose(vertices, np.asarray(positions), atol=1e-6)
    assert faces.shape == (1, 3)


def test_parse_gltf_skips_non_triangle_modes_and_bad_payloads():
    positions = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    document = gltf_document(positions, [0, 1, 2])
    document["meshes"][0]["primitives"][0]["mode"] = 1  # LINES
    with pytest.raises(ValueError):
        parse_gltf(json.dumps(document).encode("utf-8"))

    with pytest.raises(ValueError):
        parse_gltf(b"not json at all")
    with pytest.raises(ValueError):
        parse_gltf(b"glTF" + struct.pack("<II", 2, 8) + b"")  # truncated header

    broken = gltf_document(positions, [0, 1, 2])
    broken["buffers"][0]["uri"] = "data:application/octet-stream;base64,@@@@"
    with pytest.raises(ValueError):
        parse_gltf(json.dumps(broken).encode("utf-8"))


def test_parse_gltf_reads_an_external_bin_resource(tmp_path):
    positions = np.asarray([[0.0, 0.0, 0.0], [0.5, 0.0, 0.0], [0.0, 0.5, 0.0]], np.float32)
    indices = np.asarray([0, 1, 2], np.uint16)
    blob = positions.tobytes() + indices.tobytes()
    (tmp_path / "model.bin").write_bytes(blob)
    document = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "buffers": [{"byteLength": len(blob), "uri": "model.bin"}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": positions.nbytes},
            {"buffer": 0, "byteOffset": positions.nbytes, "byteLength": indices.nbytes},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
    }
    path = tmp_path / "model.gltf"
    path.write_text(json.dumps(document), encoding="utf-8")
    vertices, faces = parse_gltf(path.read_bytes(), base_dir=str(tmp_path))
    assert np.allclose(vertices, positions, atol=1e-6)
    assert faces.shape == (1, 3)


# --------------------------------------------------------------------------
# Panels
# --------------------------------------------------------------------------
def test_build_panels_grid_invariants():
    vertices, faces = flat_wing()
    panels = build_panels(vertices, faces, nx=16, ny=12)
    assert len(panels) > 1
    assert [p.index for p in panels] == list(range(len(panels)))
    total = surface_area(vertices, faces)
    assert sum(p.area_m2 for p in panels) == pytest.approx(total, rel=1e-9)
    for panel in panels:
        assert panel.area_m2 > 0.0
        norm = float(np.linalg.norm(panel.normal))
        assert norm == pytest.approx(1.0, abs=1e-12)
        assert 0.0 < panel.chordwise_frac <= 1.0
        assert 0.0 < panel.spanwise_frac <= 1.0
        assert 0.0 < panel.arc_frac <= 1.0
        assert np.all(np.isfinite(panel.centroid_m))
    leading = [p for p in panels if p.leading_edge]
    assert leading, "the first chordwise bin must exist"
    for panel in leading:
        assert panel.chordwise_frac == pytest.approx(0.5 / 16)
        assert panel.aero_lambda == 0.0
    for panel in panels:
        if not panel.leading_edge:
            assert panel.aero_lambda == 1.0
    assert abs(panels[-1].arc_frac - 1.0) < 1e-12


def test_build_panels_respects_a_coarse_grid_and_open_meshes():
    vertices, faces = flat_wing()
    coarse = build_panels(vertices, faces, nx=2, ny=2)
    assert len(coarse) == 4
    assert [p.index for p in coarse] == [0, 1, 2, 3]
    single = build_panels(vertices, faces, nx=1, ny=1)
    assert len(single) == 1
    assert single[0].area_m2 == pytest.approx(surface_area(vertices, faces), rel=1e-9)
    assert single[0].leading_edge is True
    # A single triangle is an open mesh and must still panelise.
    tri = np.array([[0, 1, 2]], dtype=np.int64)
    assert build_panels(vertices, tri, nx=4, ny=4)[0].area_m2 > 0.0


def test_panel_aerodynamics_lift_moment_and_scaling():
    vertices, faces = flat_wing()
    panels = build_panels(vertices, faces, nx=8, ny=8)
    q = 30.0

    zero = panel_aerodynamics(panels, 0.0, q)
    assert zero["total_lift_n"] == pytest.approx(0.0, abs=1e-12)
    assert zero["total_drag_n"] > 0.0
    assert zero["backend"] == "numpy" and zero["count"] == len(panels)

    lifts = []
    for alpha in (2.0, 5.0, 10.0, 14.0):
        result = panel_aerodynamics(panels, alpha, q)
        assert result["total_lift_n"] > 0.0
        lifts.append(result["total_lift_n"])
    assert lifts == sorted(lifts)
    assert lifts[-1] > lifts[0]

    # Linear in dynamic pressure.
    single = panel_aerodynamics(panels, 6.0, q)
    double = panel_aerodynamics(panels, 6.0, 2.0 * q)
    assert double["total_lift_n"] == pytest.approx(2.0 * single["total_lift_n"], rel=1e-12)
    assert double["total_drag_n"] == pytest.approx(2.0 * single["total_drag_n"], rel=1e-12)

    # Panel level rows carry the documented keys.
    row = single["panels"][0]
    for key in (
        "index",
        "area_m2",
        "normal",
        "alpha_local_deg",
        "cl",
        "cl_rot",
        "lambda_N",
        "moment_Nm",
    ):
        assert key in row
    assert all(math.isfinite(float(row[key])) for key in ("cl", "cl_rot", "lambda_N"))
    assert single["max_panel_lambda_n"] > 0.0

    # Profile drag is exactly cd * q * total area.
    expected_drag = 0.02 * q * sum(p.area_m2 for p in panels)
    assert single["total_drag_n"] == pytest.approx(expected_drag, rel=1e-12)


def test_panel_aerodynamics_moment_sign_with_cg_ahead_of_the_centre():
    vertices, faces = flat_wing()
    panels = build_panels(vertices, faces, nx=8, ny=8)
    centroids = np.asarray([p.centroid_m for p in panels])
    centre = centroids.mean(axis=0)
    # Reference cg 25 % of the half chord forward of the panel centre: with the
    # mesh's +x pointing forward this puts the cg ahead of the loading centre.
    forward_cg = centre + np.array([0.25 * 0.04, 0.0, 0.0])
    aft_cg = centre - np.array([0.25 * 0.04, 0.0, 0.0])

    nose_down = panel_aerodynamics(panels, 6.0, 40.0, cg_m=forward_cg)
    nose_up = panel_aerodynamics(panels, 6.0, 40.0, cg_m=aft_cg)
    assert nose_down["total_moment_nm"] < 0.0
    assert nose_up["total_moment_nm"] > 0.0
    # The magnitudes differ because the reference cg also scales the rotational
    # augmentation, so only the sign is invariant.
    assert nose_down["total_lift_n"] > 0.0
    assert nose_up["total_lift_n"] > 0.0
    assert nose_down["reference_cg_m"][0] > nose_up["reference_cg_m"][0]


def test_panel_aerodynamics_honours_environment_scaling():
    from physics.config_schema import EnvironmentConfig

    vertices, faces = flat_wing()
    panels = build_panels(vertices, faces, nx=6, ny=6)
    base = panel_aerodynamics(panels, 5.0, 50.0)
    env = EnvironmentConfig(lift_scale=2.0, drag_scale=3.0, compressibility_enabled=False)
    scaled = panel_aerodynamics(panels, 5.0, 50.0, env=env)
    assert scaled["total_lift_n"] == pytest.approx(2.0 * base["total_lift_n"], rel=1e-12)
    assert scaled["total_drag_n"] == pytest.approx(3.0 * base["total_drag_n"], rel=1e-12)

    compressible = EnvironmentConfig(
        lift_scale=1.0, drag_scale=1.0, compressibility_enabled=True,
        temperature_K=288.15, pressure_Pa=101325.0,
    )
    result = panel_aerodynamics(panels, 5.0, 500.0, env=compressible)
    assert result["prandt_glauert"] >= 1.0
    assert result["total_lift_n"] > panel_aerodynamics(panels, 5.0, 500.0)["total_lift_n"]

    assert panel_aerodynamics([], 5.0, 10.0)["count"] == 0


def test_panel_local_alpha_is_written_back_to_the_panel_objects():
    vertices, faces = flat_wing()
    panels = build_panels(vertices, faces, nx=4, ny=4)
    panel_aerodynamics(panels, 9.0, 25.0)
    assert all(p.local_alpha_deg != 0.0 for p in panels)
    assert all(abs(p.local_alpha_deg - 9.0) < 1e-9 for p in panels)


# --------------------------------------------------------------------------
# compute_metrics / MeshMetrics
# --------------------------------------------------------------------------
def test_compute_metrics_populates_every_contract_field():
    vertices, faces = flat_wing()
    metrics = compute_metrics(
        vertices, faces, name="wing", source_format="obj", paper_gsm=120.0
    )
    values = mesh_metric_values(metrics)
    assert set(values) == set(MESH_METRIC_KEYS)
    assert all(math.isfinite(v) for v in values.values())
    assert metrics.model_id and metrics.name == "wing"
    assert metrics.source_format == "obj"
    assert metrics.vertex_count == vertices.shape[0]
    assert metrics.face_count == faces.shape[0]
    assert metrics.paper_gsm == 120.0
    assert metrics.created_at.endswith("Z") and len(metrics.created_at) == 20
    assert metrics.backend in ("trimesh", "numpy")
    assert metrics.mass_kg == pytest.approx(
        paper_mass_from_mesh(metrics.planform_area_m2, 120.0), rel=1e-15
    )
    assert paper_mass_from_mesh(0.02, 100.0) == pytest.approx(0.016, rel=1e-15)
    assert len(metrics.inertia_tensor) == 9
    assert all(math.isfinite(float(v)) for v in metrics.inertia_tensor)
    tensor = np.asarray(metrics.inertia_tensor).reshape(3, 3)
    assert np.max(np.abs(tensor - tensor.T)) < 1e-18
    assert len(metrics.cg_m) == 3
    assert metrics.panel_count == len(metrics.panels) > 0
    payload = as_dict(metrics)
    assert payload["panels"][0]["area_m2"] == metrics.panels[0].area_m2


def test_mesh_hash_is_stable_and_mirror_sensitive():
    vertices, faces = flat_wing()
    first = compute_metrics(vertices, faces)
    again = compute_metrics(vertices.copy(), faces.copy())
    assert first.mesh_hash == again.mesh_hash
    assert len(first.mesh_hash) == 64
    int(first.mesh_hash, 16)

    mirrored = vertices.copy()
    mirrored[:, 1] *= -1.0
    mirrored_faces = faces[:, ::-1].copy()  # keep the normals outward
    mirror = compute_metrics(mirrored, mirrored_faces)
    assert mirror.mesh_hash != first.mesh_hash
    assert mirror.surface_area_m2 == pytest.approx(first.surface_area_m2, rel=1e-12)
    assert mesh_hash_of(vertices, faces) == first.mesh_hash


def test_overrides_change_the_metric_and_are_recorded():
    vertices, faces = flat_wing()
    plain = compute_metrics(vertices, faces)
    assert plain.overrides == {}

    patched = compute_metrics(
        vertices,
        faces,
        overrides={
            "mass_kg": 0.5,
            "wingspan_m": 0.33,
            "cg_x_m": 0.02,
            "taper_ratio": 0.42,
        },
    )
    assert patched.mass_kg == 0.5
    assert patched.wingspan_m == 0.33
    assert patched.taper_ratio == 0.42
    assert patched.cg_m[0] == 0.02
    assert patched.overrides["mass_kg"] == 0.5
    assert patched.overrides["cg_x_m"] == 0.02
    assert set(patched.overrides) == {"mass_kg", "wingspan_m", "taper_ratio", "cg_x_m"}
    assert plain.mass_kg != 0.5

    vector = compute_metrics(vertices, faces, overrides={"cg_m": [1.0, 2.0, 3.0]})
    assert vector.cg_m == (1.0, 2.0, 3.0)
    assert vector.overrides["cg_z_m"] == 3.0

    with pytest.raises(ValueError):
        compute_metrics(vertices, faces, overrides={"not_a_metric": 1.0})


def test_cg_mode_volume_versus_surface_differs_on_an_asymmetric_mesh():
    # The unit tetrahedron is closed and asymmetric: its volume centroid sits at
    # (1/4, 1/4, 1/4) while the area centroid cannot.
    vertices, faces = tetra_mesh()
    volume_mode = compute_metrics(vertices, faces, cg_mode="volume")
    surface_mode = compute_metrics(vertices, faces, cg_mode="surface")
    assert volume_mode.cg_mode == "volume"
    assert surface_mode.cg_mode == "surface"
    assert np.allclose(volume_mode.cg_m, (0.25, 0.25, 0.25), atol=1e-12)
    assert not np.allclose(volume_mode.cg_m, surface_mode.cg_m, atol=1e-6)
    assert np.all(np.isfinite(surface_mode.cg_m))

    # A volume request on an open mesh degrades to the surface centroid.
    open_mesh = flat_wing()
    requested_volume = compute_metrics(open_mesh[0], open_mesh[1], cg_mode="volume")
    requested_surface = compute_metrics(open_mesh[0], open_mesh[1], cg_mode="surface")
    assert requested_volume.cg_mode == "surface"
    assert np.allclose(requested_volume.cg_m, requested_surface.cg_m, atol=1e-15)

    # Translation invariance of the closed-box volume centroid.
    box_v, box_f = box_mesh(2.0, 2.0, 2.0, offset=(5.0, -3.0, 2.0))
    on_closed = compute_metrics(box_v, box_f, cg_mode="volume")
    assert np.allclose(on_closed.cg_m, (6.0, -2.0, 3.0), atol=1e-12)


# --------------------------------------------------------------------------
# Fallback honesty
# --------------------------------------------------------------------------
def test_load_geometry_reports_the_backend_that_actually_ran():
    vertices, faces = flat_wing()
    obj_text = (
        "\n".join(f"v {v[0]} {v[1]} {v[2]}" for v in vertices)
        + "\n"
        + "\n".join(f"f {t[0] + 1} {t[1] + 1} {t[2] + 1}" for t in faces)
        + "\n"
    )
    loaded_v, loaded_f, info = load_geometry(obj_text.encode("utf-8"), filename="wing.obj")
    assert info["backend"] in ("trimesh", "numpy")
    assert (info["backend"] == "numpy") is (not TRIMESH_AVAILABLE)
    assert info["format"] == "obj"
    assert info["scale_factor"] == 1.0
    assert info["vertex_count"] == vertices.shape[0]
    assert info["face_count"] == faces.shape[0]
    assert loaded_v.shape == vertices.shape and loaded_f.shape == faces.shape
    assert surface_area(loaded_v, loaded_f) == pytest.approx(
        surface_area(vertices, faces), rel=1e-12
    )

    mm = load_geometry(obj_text.encode("utf-8"), filename="wing.obj", scale_hint="mm")
    assert np.allclose(mm[0], loaded_v * 0.001, atol=1e-12)
    assert mm[2]["scale_factor"] == 0.001
    with pytest.raises(ValueError):
        load_geometry(obj_text.encode("utf-8"), filename="wing.obj", scale_hint="furlong")

    with pytest.raises(ValueError):
        load_geometry(b"", filename="empty.obj")
    with pytest.raises(ValueError):
        load_geometry(b"v 0 0 0\n", filename="points.obj")


# --------------------------------------------------------------------------
# MeshLibrary
# --------------------------------------------------------------------------
def obj_payload(vertices, faces) -> bytes:
    lines = [f"v {v[0]:.12g} {v[1]:.12g} {v[2]:.12g}" for v in vertices]
    lines += [f"f {t[0] + 1} {t[1] + 1} {t[2] + 1}" for t in faces]
    return ("\n".join(lines) + "\n").encode("utf-8")


def test_mesh_library_lifecycle_on_tmp_path(tmp_path):
    root = tmp_path / "models"
    library = MeshLibrary(str(root))
    vertices, faces = flat_wing()
    payload = obj_payload(vertices, faces)

    assert library.list_models() == []
    assert library.selected() is None

    first = library.import_model_bytes(payload, "wing.obj")
    second = library.import_model_bytes(payload, "wing-copy.obj")
    assert first.model_id != second.model_id
    assert len(library.list_models()) == 2
    assert {row["model_id"] for row in library.list_models()} == {
        first.model_id,
        second.model_id,
    }
    for row in library.list_models():
        assert "panels" not in row  # payload stays compact
        assert row["panel_count"] == first.panel_count

    # Real files on disk.
    assert os.path.isfile(os.path.join(str(root), f"{first.model_id}.json"))
    assert os.path.isfile(os.path.join(str(root), f"{first.model_id}.obj"))
    assert os.path.getsize(os.path.join(str(root), f"{first.model_id}.obj")) == len(payload)

    stored = library.get_metrics(first.model_id)
    assert stored.mesh_hash == first.mesh_hash
    assert stored.planform_area_m2 == pytest.approx(first.planform_area_m2, rel=1e-15)
    payload_json = as_dict(stored)
    assert json.loads(json.dumps(payload_json))["model_id"] == first.model_id
    assert len(payload_json["panels"]) == first.panel_count

    assert library.selected() is None
    assert library.select(second.model_id).model_id == second.model_id
    assert library.selected().model_id == second.model_id

    with pytest.raises(KeyError):
        library.get_metrics("does-not-exist")
    with pytest.raises(KeyError):
        library.select("does-not-exist")
    assert library.delete("does-not-exist") is False

    assert library.delete(first.model_id) is True
    assert len(library.list_models()) == 1
    assert not os.path.isfile(os.path.join(str(root), f"{first.model_id}.json"))


def test_mesh_library_rebuilds_the_index_and_maps_to_aircraft(tmp_path):
    root = tmp_path / "cache"
    vertices, faces = flat_wing()
    payload = obj_payload(vertices, faces)

    library = MeshLibrary(str(root))
    metrics = library.import_model_bytes(payload, "wing.obj", paper_gsm=150.0)
    assert metrics.paper_gsm == 150.0

    revived = MeshLibrary(str(root))
    assert len(revived.list_models()) == 1
    restored = revived.get_metrics(metrics.model_id)
    assert restored.mesh_hash == metrics.mesh_hash
    assert restored.surface_area_m2 == pytest.approx(metrics.surface_area_m2, rel=1e-15)
    assert restored.panel_count == metrics.panel_count
    assert restored.created_at == metrics.created_at
    assert np.allclose(restored.cg_m, metrics.cg_m, atol=1e-15)
    assert np.allclose(restored.inertia_tensor, metrics.inertia_tensor, atol=1e-18)

    base = AircraftConfig(name="custom", mass_kg=0.007, elevon_deg=3.0)
    config = revived.to_aircraft_config(metrics.model_id, base)
    assert isinstance(config, AircraftConfig)
    assert config is not base
    assert config.name == "custom" and config.elevon_deg == 3.0
    assert config.mass_kg == pytest.approx(metrics.mass_kg, rel=1e-15)
    assert config.wing_area_m2 == pytest.approx(metrics.planform_area_m2, rel=1e-15)
    assert config.wingspan_m == pytest.approx(metrics.wingspan_m, rel=1e-15)
    assert config.taper_ratio == pytest.approx(metrics.taper_ratio, rel=1e-15)
    assert config.aspect_ratio == pytest.approx(metrics.aspect_ratio, rel=1e-15)
    assert config.sheet_area_m2 > 0.0
    principal = np.sort(np.linalg.eigvalsh(np.asarray(metrics.inertia_tensor).reshape(3, 3)))
    assert config.roll_inertia_kgm2 == pytest.approx(principal[0], rel=1e-9)
    assert config.pitch_inertia_kgm2 == pytest.approx(principal[1], rel=1e-9)
    assert config.yaw_inertia_kgm2 == pytest.approx(principal[2], rel=1e-9)
    assert base.mass_kg == 0.007  # the original template is untouched

    default_config = revived.to_aircraft_config(metrics.model_id)
    assert default_config.mass_kg == pytest.approx(metrics.mass_kg, rel=1e-15)


def test_mesh_library_import_from_path_and_overrides(tmp_path):
    root = tmp_path / "models"
    vertices, faces = flat_wing()
    source = tmp_path / "wing.obj"
    source.write_bytes(obj_payload(vertices, faces))

    library = MeshLibrary(str(root))
    from_path = library.import_model(str(source), scale_hint="m")
    assert from_path.source_format == "obj"
    assert from_path.planform_area_m2 > 0.0

    patched = library.apply_overrides(from_path.model_id, {"mass_kg": 0.021})
    assert patched.mass_kg == 0.021
    assert patched.overrides == {"mass_kg": 0.021}
    assert library.get_metrics(from_path.model_id).mass_kg == 0.021

    scaled = library.import_model(str(source), scale_hint="cm")
    assert scaled.scale_factor == 0.01
    assert scaled.units == "cm"
    assert scaled.wingspan_m == pytest.approx(from_path.wingspan_m * 0.01, rel=1e-9)

    with pytest.raises(ValueError):
        library.import_model(str(source), scale_hint="parsec")
    with pytest.raises(ValueError):
        library.import_model(str(tmp_path / "missing.obj"))
    with pytest.raises(ValueError):
        library.import_model_url("file:///etc/passwd")


def test_mesh_library_import_from_url_uses_urllib(tmp_path):
    root = tmp_path / "models"
    library = MeshLibrary(str(root))
    vertices, faces = flat_wing()
    payload = obj_payload(vertices, faces)

    class _Response:
        def __init__(self, blob):
            self._blob = blob

        def read(self):
            return self._blob

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    import urllib.request as request_module

    original = request_module.urlopen
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append((getattr(request, "full_url", request), timeout))
        return _Response(payload)

    request_module.urlopen = fake_urlopen
    try:
        metrics = library.import_model_url("http://example.test/wing.obj", timeout=3.5)
    finally:
        request_module.urlopen = original

    assert calls and calls[0][1] == 3.5
    assert metrics.planform_area_m2 > 0.0
    assert metrics.model_id in {row["model_id"] for row in library.list_models()}


def test_mesh_library_global_instance_is_usable():
    from physics.mesh_domain import MESH_LIBRARY

    assert isinstance(MESH_LIBRARY, MeshLibrary)
    assert isinstance(MESH_LIBRARY.root, str)
    assert isinstance(MESH_LIBRARY.list_models(), list)
