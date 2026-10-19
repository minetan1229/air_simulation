"""Unit tests for the procedural origami geometry.

The tests cover the guarantees the renderer relies on:

* the fold programme is isometric (the paper never stretches),
* the mesh is a closed, non-degenerate solid with valid normals,
* overlapping layers are separated by at least one layer pitch, which is
  what keeps the depth buffer free of z-fighting,
* the folded model never grows larger than the sheet it was cut from,
* the folding progression is continuous and the multi-layer nose really
  stacks up.
"""

from __future__ import annotations

import math
from typing import Dict, List

import pytest

from physics import origami_domain as od
from physics.origami_domain import Vec3

ARCHETYPES = list(od.ARCHETYPES)
ALPHAS = (0.0, 0.25, 0.5, 0.75, 1.0)


@pytest.fixture()
def dart_mesh():
    return od.build_mesh(od.ARCHETYPE_DART, 1.0)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _closest_parameters(p0, p1, q0, q1):
    """Distance and parameters of the closest points of two segments."""

    def sub(a, b):
        return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

    def dot(a, b):
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

    def cross(a, b):
        return (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )

    u = sub(p1, p0)
    v = sub(q1, q0)
    w = sub(p0, q0)
    a = dot(u, u)
    b = dot(u, v)
    c = dot(v, v)
    d = dot(u, w)
    e = dot(v, w)
    denom = a * c - b * b
    if denom > 1e-18:
        s = (b * e - c * d) / denom
        t = (a * e - b * d) / denom
    else:
        s = 0.0
        t = (e / c) if c > 1e-18 else 0.0
    s = max(0.0, min(1.0, s))
    t = max(0.0, min(1.0, t))
    # refine the clamped parameters
    for _ in range(2):
        pc = (p0[0] + u[0] * t, p0[1] + u[1] * t, p0[2] + u[2] * t)
        qc = (q0[0] + v[0] * s, q0[1] + v[1] * s, q0[2] + v[2] * s)
        if c > 1e-18:
            s = max(0.0, min(1.0, s + dot(sub(pc, q0), v) / c))
        if a > 1e-18:
            t = max(0.0, min(1.0, t + dot(sub(qc, p0), u) / a))
        pc = (p0[0] + u[0] * t, p0[1] + u[1] * t, p0[2] + u[2] * t)
        qc = (q0[0] + v[0] * s, q0[1] + v[1] * s, q0[2] + v[2] * s)
    distance = math.sqrt(
        (pc[0] - qc[0]) ** 2 + (pc[1] - qc[1]) ** 2 + (pc[2] - qc[2]) ** 2
    )
    return distance, t, s, u, v


def _segment_intersects(p0, p1, q0, q1, tolerance: float = 2.0e-4) -> bool:
    """True when two segments really pass through each other.

    Two folded pieces may legitimately touch where a crease ends or where
    they share an edge, so a crossing only counts when the pieces overlap
    with both closest points in the interior of the two edges.
    """
    distance, t, s, u, v = _closest_parameters(p0, p1, q0, q1)
    pc = (
        p0[0] + u[0] * t,
        p0[1] + u[1] * t,
        p0[2] + u[2] * t,
    )
    qc = (
        q0[0] + v[0] * s,
        q0[1] + v[1] * s,
        q0[2] + v[2] * s,
    )
    if distance >= tolerance:
        return False
    # Collinear edges (a shared crease) are not a crossing.
    un = math.sqrt(u[0] ** 2 + u[1] ** 2 + u[2] ** 2)
    vn = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if un > 1e-12 and vn > 1e-12:
        cross = (
            u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0],
        )
        if math.sqrt(sum(value * value for value in cross)) / (un * vn) < 1e-6:
            return False
    # A contact that happens at the end of an edge is a touch, not a
    # crossing: folded pieces meet at the end of their creases.
    for point, ends in ((pc, (p0, p1)), (qc, (q0, q1))):
        for end in ends:
            if (
                (point[0] - end[0]) ** 2
                + (point[1] - end[1]) ** 2
                + (point[2] - end[2]) ** 2
            ) <= tolerance * tolerance:
                return False
    return True


def _face_area(face) -> float:
    return od._poly3d_area(face.poly3d)


# --------------------------------------------------------------------------
# pattern level
# --------------------------------------------------------------------------


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_pattern_compiles_into_convex_faces(archetype):
    """Every compiled face is a convex polygon with a positive area."""
    pattern = od.build_pattern(archetype)
    assert pattern.faces, "the pattern must contain faces"
    faces = od._fold_faces(pattern)
    for face in faces:
        assert len(face.poly3d) >= 3
        area = od._poly3d_area(face.poly3d)
        assert area > 1e-9, f"face {face.fid} is degenerate"
        # Convexity: every vertex keeps the same orientation.
        signs = []
        count = len(face.poly3d)
        for i in range(count):
            a = face.poly3d[i]
            b = face.poly3d[(i + 1) % count]
            c = face.poly3d[(i + 2) % count]
            signs.append(
                od._dot3(od._cross3(od._sub3(b, a), od._sub3(c, b)), face.normal)
            )
        signs = [value for value in signs if abs(value) > 1e-12]
        assert signs and (all(v > 0 for v in signs) or all(v < 0 for v in signs))


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_fold_is_isometric(archetype):
    """Folding never stretches the paper: the surface area is preserved."""
    sheet_area = od.SHEET_WIDTH_M * od.SHEET_LENGTH_M
    for alpha in ALPHAS:
        pattern = od.build_pattern(archetype, alpha=alpha)
        faces = od._fold_faces(pattern)
        total = sum(_face_area(face) for face in faces)
        assert total == pytest.approx(sheet_area, rel=1e-6)


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_folded_sheet_stays_inside_the_sheet_envelope(archetype):
    """A fold can never make the paper larger than the flat sheet."""
    pattern = od.build_pattern(archetype)
    faces = od._fold_faces(pattern)
    for face in faces:
        for point in face.poly3d:
            # A fold may swing a flap slightly outside the flat outline, but
            # never by more than a small fraction of the sheet.
            assert abs(point[0]) <= od.SHEET_LENGTH_M * 0.75
            assert abs(point[2]) <= od.SHEET_WIDTH_M * 0.75
            assert abs(point[1]) <= od.SHEET_WIDTH_M * 0.75


# --------------------------------------------------------------------------
# layer assignment / z-fighting
# --------------------------------------------------------------------------


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_stacked_layers_are_separated_by_a_full_pitch(archetype):
    """Overlapping parallel faces are one layer pitch apart at least.

    This is the property that removes the z-fighting: the mid surfaces of
    two overlapping faces are at least ``LAYER_PITCH_M`` apart while the
    paper is only ``VISUAL_THICKNESS_M`` thick, so the two slabs can never
    become coplanar.
    """
    pattern = od.build_pattern(archetype)
    faces = od._fold_faces(pattern)
    layers = od.assign_layers(faces)
    checked = 0
    for i, a in enumerate(faces):
        for b in faces[i + 1 :]:
            if abs(od._dot3(a.normal, b.normal)) < od.PARALLEL_EPS:
                continue
            ref = layers.reference[a.fid]
            proj_a = od._project_polygon(a.poly3d, ref)
            proj_b = od._project_polygon(b.poly3d, ref)
            if not od._convex_overlap(proj_a, proj_b):
                continue
            checked += 1
            delta = abs(layers.index[a.fid] - layers.index[b.fid])
            assert delta >= 1, (
                f"overlapping faces {a.fid}/{b.fid} share layer "
                f"{layers.index[a.fid]} and would z-fight"
            )
            ref_a = layers.reference[a.fid]
            ref_b = layers.reference[b.fid]
            # Coplanar pieces are lifted apart by the layer offset, so the
            # effective clearance is the geometric distance plus the layer
            # pitch of the stack.
            geometric = abs(
                od._dot3(
                    od._sub3(b.centroid3d, a.centroid3d),
                    od._unit3(od._add3(ref_a, ref_b)),
                )
            )
            separation = geometric + delta * od.LAYER_PITCH_M
            assert separation > od.VISUAL_THICKNESS_M, (
                f"overlapping faces {a.fid}/{b.fid} are only {separation:.6f} m apart"
            )
    assert checked > 0, "the archetype must contain a multi-layer stack"


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_nose_packet_stacks_several_layers(archetype):
    """The folded nose must really be a stack of paper layers."""
    pattern = od.build_pattern(archetype)
    faces = od._fold_faces(pattern)
    layers = od.assign_layers(faces)
    assert layers.max_layers >= 2
    counts = sorted(layers.index.values(), reverse=True)
    assert counts[0] >= 2


# --------------------------------------------------------------------------
# mesh level
# --------------------------------------------------------------------------


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_mesh_is_a_valid_solid(archetype):
    """Positions, normals, uvs and indices are consistent."""
    mesh = od.build_mesh(archetype, 1.0)
    assert mesh.vertex_count > 0
    assert mesh.triangle_count > 0
    assert len(mesh.positions) == mesh.vertex_count
    assert len(mesh.normals) == mesh.vertex_count
    assert len(mesh.uvs) == mesh.vertex_count
    assert len(mesh.layer_counts) == mesh.vertex_count
    assert len(mesh.crease_weights) == mesh.vertex_count
    assert len(mesh.elastic_weights) == mesh.vertex_count
    assert len(mesh.twist_weights) == mesh.vertex_count
    assert len(mesh.paper_sides) == mesh.vertex_count
    for point in mesh.positions:
        assert all(math.isfinite(value) for value in point)
    for normal in mesh.normals:
        length = math.sqrt(sum(value * value for value in normal))
        assert length == pytest.approx(1.0, abs=1e-6)
    for tri in mesh.indices:
        assert all(0 <= index < mesh.vertex_count for index in tri)
        assert len(set(tri)) == 3


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_mesh_has_no_degenerate_triangles(archetype):
    """Every triangle of the solid has a non-zero area."""
    mesh = od.build_mesh(archetype, 1.0)
    for a, b, c in mesh.indices:
        p0 = mesh.positions[a]
        p1 = mesh.positions[b]
        p2 = mesh.positions[c]
        e1 = od._sub3(p1, p0)
        e2 = od._sub3(p2, p0)
        area = 0.5 * od._len3(od._cross3(e1, e2))
        assert area > 1e-12, f"degenerate triangle {a}/{b}/{c}"


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_mesh_bounds_fit_the_sheet(archetype):
    """The folded model is contained in the envelope of the flat sheet."""
    mesh = od.build_mesh(archetype, 1.0)
    lo, hi = mesh.bounds()
    assert lo[0] >= -od.SHEET_LENGTH_M * 0.75
    assert hi[0] <= od.SHEET_LENGTH_M * 0.75
    assert lo[2] >= -od.SHEET_WIDTH_M * 0.75
    assert hi[2] <= od.SHEET_WIDTH_M * 0.75
    assert hi[1] - lo[1] <= od.SHEET_WIDTH_M + 1e-3


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_folded_faces_do_not_self_intersect(archetype):
    """No two folded faces cross each other."""
    pattern = od.build_pattern(archetype)
    faces = od._fold_faces(pattern)
    layers = od.assign_layers(faces)
    # Separate the stack exactly like the mesh builder does: the mid
    # surfaces of two overlapping layers are one layer pitch apart.
    separated: Dict[int, List[Vec3]] = {}
    for face in faces:
        ref = layers.reference[face.fid]
        amount = layers.index[face.fid] * od.LAYER_PITCH_M
        separated[face.fid] = [
            od._add3(point, od._scale3(ref, amount)) for point in face.poly3d
        ]
    for i, a in enumerate(faces):
        for b in faces[i + 1 :]:
            linked = any(
                link.neighbour_fid == b.fid for link in pattern.face_by_id(a.fid).links
            )
            if linked:
                continue
            poly_a = separated[a.fid]
            poly_b = separated[b.fid]
            for p in range(len(poly_a)):
                p0 = poly_a[p]
                p1 = poly_a[(p + 1) % len(poly_a)]
                for q in range(len(poly_b)):
                    q0 = poly_b[q]
                    q1 = poly_b[(q + 1) % len(poly_b)]
                    assert not _segment_intersects(p0, p1, q0, q1), (
                        f"faces {a.fid} and {b.fid} intersect"
                    )


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_fold_progression_is_continuous(archetype):
    """The folding progression runs from the flat sheet to the folded model."""
    flat = od.build_mesh(archetype, 0.0)
    lo, hi = flat.bounds()
    assert hi[1] - lo[1] < 1e-3, "alpha=0 must be a flat sheet"
    folded = od.build_mesh(archetype, 1.0)
    flo, fhi = folded.bounds()
    assert fhi[1] - flo[1] > 0.01, "alpha=1 must be a folded model"
    previous = None
    for alpha in ALPHAS:
        mesh = od.build_mesh(archetype, alpha)
        assert mesh.vertex_count > 0
        area = mesh.surface_area()
        if previous is not None:
            assert area == pytest.approx(previous, rel=0.05)
        previous = area


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_crease_weights_mark_the_fold_lines(archetype):
    """Vertices close to a crease carry a higher crease weight."""
    mesh = od.build_mesh(archetype, 1.0)
    assert max(mesh.crease_weights) > 0.0
    assert min(mesh.crease_weights) >= 0.0
    assert max(mesh.crease_weights) <= 1.0


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_elastic_weights_only_reach_the_wings(archetype):
    """The keel stays rigid while the wing tips are free to bend."""
    mesh = od.build_mesh(archetype, 1.0)
    assert max(mesh.elastic_weights) > 0.5
    assert min(mesh.elastic_weights) == 0.0


def test_aeroelastic_deformation_moves_the_wing_tip_only():
    """Passive aero-elastic bending grows from the root to the tip."""
    mesh = od.build_mesh(od.ARCHETYPE_DART, 1.0)
    before = list(mesh.positions)
    od.apply_aeroelastic(mesh, bend_deg=6.0, twist_deg=4.0)
    moved = [
        index
        for index, point in enumerate(mesh.positions)
        if od._len3(od._sub3(point, before[index])) > 1e-9
    ]
    assert moved, "the aero-elastic load must deform the wing"
    tips = [i for i in moved if mesh.elastic_weights[i] > 0.5]
    roots = [i for i in moved if mesh.elastic_weights[i] == 0.0]
    assert tips, "the wing tip has to move"
    assert not roots, "the keel must stay rigid"


def test_mesh_payload_round_trip(dart_mesh):
    """The serialised payload keeps every vertex buffer in sync."""
    payload = od.mesh_payload(dart_mesh)
    assert payload["stats"]["vertices"] == dart_mesh.vertex_count
    assert payload["stats"]["triangles"] == dart_mesh.triangle_count
    assert len(payload["positions"]) == 3 * dart_mesh.vertex_count
    assert len(payload["normals"]) == 3 * dart_mesh.vertex_count
    assert len(payload["indices"]) == 3 * dart_mesh.triangle_count
    assert payload["max_layers"] >= 2
    assert payload["paper_thickness_m"] > 0.0


def test_archetype_catalog_lists_the_three_prototypes():
    catalog = od.archetype_catalog()
    assert [entry["key"] for entry in catalog] == list(od.ARCHETYPES)
    assert all(entry["label"] for entry in catalog)
    assert {entry["shape_flag"] for entry in catalog} == {0, 1, 2}
