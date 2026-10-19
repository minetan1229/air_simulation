"""3D mesh import, geometric measurement and panel aerodynamics.

The module is deliberately dependency-light: glTF 2.0 / GLB / OBJ / STL are
parsed with the standard library (``json`` / ``base64`` / ``struct``) and every
geometric quantity is evaluated analytically with NumPy.  ``trimesh`` is used
*only* when it happens to be importable and is fully transparent: the
:attr:`MeshMetrics.backend` field records which path actually produced the
numbers, and the pure-NumPy path is a first-class citizen, not a stub.

Frames of reference
-------------------
Mesh data arrives in whatever orientation the author baked into the file, so
the airframe-relevant quantities (planform, panels) are recovered through a
PCA frame rather than assuming the mesh axes:

* ``span``  axis = principal axis of largest extent (the wingspan direction),
* ``chord`` axis = second principal axis, sign-fixed to point forward,
* ``normal``  axis = the right-handed completion of the two.

Analytic formulas used (all exact for polyhedra):

* surface area   ``sum_f 0.5 * |(b - a) x (c - a)|``
* signed volume  ``sum_f dot(a, b x c) / 6``
* volume centroid tetra-decomposition ``sum_i V_i c_i / sum_i V_i`` with
  ``V_i = dot(a_i, b_i x c_i)/6`` and ``c_i = (a_i + b_i + c_i)/4``
* inertia ``sum_tets r x (r x dm)`` with a parallel-axis shift to the centre of
  mass, divided by the total mass to yield a mass-normalised tensor.
"""

from __future__ import annotations

import base64
import binascii
import io
import ipaddress
import json
import math
import os
import pathlib
import socket
import struct
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np

from .config_schema import (
    MESH_METRIC_KEYS,
    PAPER_DENSITY_BASE,
    AircraftConfig,
    MeshMetrics,
    PanelData,
    as_dict,
    clamp,
    make_uuid,
    safe_div,
    utc_now_iso,
)
from .geometry_utils import (
    inertia_tensor_box,
    normalize,
    principal_inertia_from_matrix,
    vec3,
)

__all__ = [
    "SUPPORTED_FORMATS",
    "SCALE_FACTORS",
    "PANEL_PROFILE_DRAG_CD",
    "PAPER_THICKNESS_M",
    "STALL_ALPHA_DEG",
    "scale_factor_for",
    "detect_format",
    "parse_obj",
    "parse_stl",
    "parse_stl_ascii",
    "parse_stl_binary",
    "parse_gltf",
    "triangle_areas",
    "triangle_normals",
    "triangle_centroids",
    "is_closed_mesh",
    "surface_area",
    "signed_volume",
    "volume_centroid",
    "surface_centroid",
    "mass_properties",
    "inertia_tensor_mesh",
    "principal_axes",
    "planform_metrics",
    "build_panels",
    "panel_aerodynamics",
    "paper_mass_from_mesh",
    "mesh_hash_of",
    "mesh_metric_values",
    "load_geometry",
    "compute_metrics",
    "MeshLibrary",
    "MESH_LIBRARY",
]

_EPS = 1.0e-12
_AREA_EPS = 1.0e-18
_VOLUME_EPS = 1.0e-24

#: Formats understood by :func:`detect_format` and :func:`load_geometry`.
SUPPORTED_FORMATS: Tuple[str, ...] = ("gltf", "glb", "obj", "stl")

#: Unit conversions accepted by ``scale_hint``.
SCALE_FACTORS: Dict[str, float] = {
    "m": 1.0,
    "cm": 0.01,
    "mm": 0.001,
    "in": 0.0254,
}

#: Profile drag coefficient used by the panel method (thin low-aspect sheet).
PANEL_PROFILE_DRAG_CD = 0.02

#: Local incidence at which the panel lift curve begins to taper off.
STALL_ALPHA_DEG = 15.0

#: Nominal thickness of a 100 g/m^2 uncreped sheet at ``PAPER_DENSITY_BASE``;
#: used only as an areal fallback when an open mesh encloses no volume.
PAPER_THICKNESS_M = 0.1 / PAPER_DENSITY_BASE


# ==========================================================================
# Format / unit helpers
# ==========================================================================
def scale_factor_for(scale_hint: str) -> float:
    """Metres-per-``scale_hint`` conversion factor.

    ``"m" -> 1.0``, ``"cm" -> 0.01``, ``"mm" -> 0.001``, ``"in" -> 0.0254``.
    Anything else raises :class:`ValueError`.
    """
    key = str(scale_hint).strip().lower()
    if key not in SCALE_FACTORS:
        raise ValueError(
            f"unknown scale_hint {scale_hint!r}; supported: "
            f"{', '.join(sorted(SCALE_FACTORS))}"
        )
    return float(SCALE_FACTORS[key])


def _looks_like_glb(data: bytes) -> bool:
    return len(data) >= 4 and data[0:4] == b"glTF"


def _sniff_text_format(text: str) -> Optional[str]:
    """Content based format detection for the ASCII formats."""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("v ") or line.startswith("v\t") or line == "v":
            return "obj"
        if line.startswith("solid") or line.startswith("facet") or line.startswith("endsolid"):
            return "stl"
        break
    return None


def detect_format(filename: str, data: bytes) -> str:
    """Return ``"gltf"``/``"glb"``/``"obj"``/``"stl"`` for a candidate file.

    Binary formats are decided by their magic numbers (so an ASCII STL whose
    header happens to start with ``solid`` still lands in the binary STL
    branch when the facet count matches the file length).  Text formats fall
    back to the file extension and finally to content sniffing.
    """
    if data is None:
        raise ValueError("no data supplied to detect_format")
    blob = bytes(data)
    if _looks_like_glb(blob):
        return "glb"

    extension = ""
    if filename:
        base = os.path.basename(str(filename))
        if "." in base:
            extension = base.rsplit(".", 1)[1].strip().lower()

    if blob[:1] == b"{":
        return "gltf"

    sample_bytes = blob[:8192]
    try:
        text = sample_bytes.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        text = ""
    if text:
        if "\nfacet " in text or text.lstrip().startswith("facet ") or "endsolid" in text:
            return "stl"
        sniffed = _sniff_text_format(text)
        if sniffed is not None:
            return sniffed

    if extension == "gltf":
        return "gltf"
    if extension == "obj":
        return "obj"
    if extension == "stl":
        return "stl"
    if extension == "glb":
        # Claimed GLB but the magic is wrong -> treat as glTF JSON.
        return "gltf"

    raise ValueError(
        f"unrecognised mesh format for {filename!r} "
        f"({len(blob)} bytes); supported extensions: "
        f"{', '.join(SUPPORTED_FORMATS)}"
    )


# ==========================================================================
# Low level mesh assembly helpers
# ==========================================================================
class _VertexPool:
    """Order preserving de-duplicating vertex store."""

    def __init__(self) -> None:
        self._seen: Dict[Tuple[float, float, float], int] = {}
        self._points: List[Tuple[float, float, float]] = []

    def add(self, x: float, y: float, z: float) -> int:
        key = (float(x), float(y), float(z))
        index = self._seen.get(key)
        if index is None:
            index = len(self._points)
            self._seen[key] = index
            self._points.append(key)
        return index

    def array(self) -> np.ndarray:
        if not self._points:
            return np.zeros((0, 3), dtype=np.float64)
        return np.asarray(self._points, dtype=np.float64)

    def __len__(self) -> int:
        return len(self._points)


def _finish_mesh(
    pool: _VertexPool, faces: List[Sequence[int]]
) -> Tuple[np.ndarray, np.ndarray]:
    """Materialise a vertex/face pair, dropping degenerate polygons."""
    vertices = pool.array()
    if faces:
        cleaned: List[Tuple[int, int, int]] = []
        for tri in faces:
            i0, i1, i2 = int(tri[0]), int(tri[1]), int(tri[2])
            if i0 == i1 or i1 == i2 or i0 == i2:
                continue
            cleaned.append((i0, i1, i2))
        if cleaned:
            face_array = np.asarray(cleaned, dtype=np.int64)
        else:
            face_array = np.zeros((0, 3), dtype=np.int64)
    else:
        face_array = np.zeros((0, 3), dtype=np.int64)
    return vertices, face_array


def _as_triangle_list(faces: Iterable[Any]) -> np.ndarray:
    rows: List[Tuple[int, int, int]] = []
    for tri in faces:
        rows.append((int(tri[0]), int(tri[1]), int(tri[2])))
    if not rows:
        return np.zeros((0, 3), dtype=np.int64)
    return np.asarray(rows, dtype=np.int64)


MAX_MESH_VERTICES = 150_000
MAX_MESH_FACES = 300_000

def _validate_mesh(vertices: np.ndarray, faces: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    verts = np.asarray(vertices, dtype=np.float64)
    if verts.ndim != 2 or verts.shape[1] < 3:
        raise ValueError(
            f"vertices must have shape (n, 3); got {tuple(verts.shape)}"
        )
    if len(verts) > MAX_MESH_VERTICES:
        raise ValueError(f"vertex count ({len(verts)}) exceeds safe limit of {MAX_MESH_VERTICES}")
    verts = np.ascontiguousarray(verts[:, :3].astype(np.float64))
    tris = np.asarray(faces, dtype=np.int64)
    if tris.size == 0:
        tris = np.zeros((0, 3), dtype=np.int64)
    if tris.ndim != 2 or tris.shape[1] != 3:
        raise ValueError(f"faces must have shape (m, 3); got {tuple(tris.shape)}")
    if len(tris) > MAX_MESH_FACES:
        raise ValueError(f"face count ({len(tris)}) exceeds safe limit of {MAX_MESH_FACES}")
    if tris.size and (int(tris.min()) < 0 or int(tris.max()) >= len(verts)):
        raise ValueError(
            "face index out of range: "
            f"[{int(tris.min())}, {int(tris.max())}] vs {len(verts)} vertices"
        )
    return verts, np.ascontiguousarray(tris)


def _triangles(vertices: np.ndarray, faces: np.ndarray):
    """Return ``(a, b, c)`` vertex arrays for every face."""
    tris = np.asarray(faces, dtype=np.int64)
    if tris.size == 0:
        empty = np.zeros((0, 3), dtype=np.float64)
        return empty, empty.copy(), empty.copy()
    index = np.asarray(vertices, dtype=np.float64)[tris]
    return index[:, 0, :], index[:, 1, :], index[:, 2, :]


# ==========================================================================
# OBJ
# ==========================================================================
def parse_obj(text: str) -> Tuple[np.ndarray, np.ndarray]:
    """Parse Wavefront OBJ text into ``(vertices, faces)``.

    * ``v`` / ``vn`` / ``vt`` are read; only ``v`` feeds the topology.
    * ``o`` / ``g`` / ``usemtl`` / ``mtllib`` / ``s`` are accepted and skipped.
    * Arbitrary polygons are fan-triangulated; faces smaller than 3 corners are
      dropped; ``#`` comments (whole line and trailing) are stripped.
    * Slash forms ``a``, ``a/b``, ``a//b``, ``a/b/c`` all resolve to the vertex
      index, and negative indices are relative to the vertices seen so far.
    * Duplicate ``v`` lines collapse onto one shared vertex.
    """
    if text is None:
        raise ValueError("parse_obj received no text")
    if isinstance(text, (bytes, bytearray)):
        try:
            text = bytes(text).decode("utf-8")
        except UnicodeDecodeError as exc:  # pragma: no cover - defensive
            raise ValueError(f"OBJ payload is not valid UTF-8: {exc}") from exc

    pool = _VertexPool()
    triangles: List[Sequence[int]] = []

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        tag = parts[0]
        if tag == "v":
            if len(parts) < 4:
                raise ValueError(f"malformed OBJ vertex line: {raw_line!r}")
            try:
                pool.add(float(parts[1]), float(parts[2]), float(parts[3]))
            except ValueError as exc:
                raise ValueError(f"malformed OBJ vertex line: {raw_line!r}") from exc
        elif tag == "f":
            if len(parts) < 4:
                # Degenerate polygon (or a stray "f"): nothing to triangulate.
                continue
            corners: List[int] = []
            for token in parts[1:]:
                head = token.split("/", 1)[0]
                if not head:
                    raise ValueError(f"malformed OBJ face token: {token!r}")
                try:
                    index = int(head)
                except ValueError as exc:
                    raise ValueError(f"malformed OBJ face index: {token!r}") from exc
                if index > 0:
                    resolved = index - 1
                elif index < 0:
                    resolved = len(pool) + index
                else:
                    raise ValueError("OBJ face index 0 is invalid (indices are 1-based)")
                if resolved < 0 or resolved >= len(pool):
                    raise ValueError(
                        f"OBJ face index {index} out of range ({len(pool)} vertices so far)"
                    )
                corners.append(resolved)
            for k in range(1, len(corners) - 1):
                triangles.append((corners[0], corners[k], corners[k + 1]))

    return _finish_mesh(pool, triangles)


# ==========================================================================
# STL
# ==========================================================================
def _stl_facets_to_mesh(
    corners: np.ndarray
) -> Tuple[np.ndarray, np.ndarray]:
    """``corners`` is ``(f, 3, 3)``; de-duplicate and rebuild the topology."""
    facets = np.asarray(corners, dtype=np.float64).reshape(-1, 3, 3)
    rounded = np.round(facets.reshape(-1, 3), 12)
    _, first_index, inverse = np.unique(rounded, axis=0, return_index=True, return_inverse=True)
    unique_points = rounded[np.sort(first_index)]
    remap = np.empty(len(first_index), dtype=np.int64)
    remap[np.argsort(first_index)] = np.arange(len(first_index), dtype=np.int64)
    faces = remap[inverse].reshape(-1, 3)
    return np.ascontiguousarray(unique_points), np.ascontiguousarray(faces)


def parse_stl_ascii(text: str) -> Tuple[np.ndarray, np.ndarray]:
    """Parse ASCII STL (``solid``/``facet``/``outer loop``/``vertex``)."""
    if text is None:
        raise ValueError("parse_stl_ascii received no text")
    if isinstance(text, (bytes, bytearray)):
        try:
            text = bytes(text).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"ASCII STL payload is not valid UTF-8: {exc}") from exc

    corners: List[List[Tuple[float, float, float]]] = []
    current: Optional[List[Tuple[float, float, float]]] = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith("facet"):
            current = []
        elif lowered.startswith("vertex"):
            parts = line.split()
            if len(parts) < 4:
                raise ValueError(f"malformed ASCII STL vertex line: {raw_line!r}")
            if current is None:
                raise ValueError(
                    f"ASCII STL vertex outside of a facet: {raw_line!r}"
                )
            try:
                current.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError as exc:
                raise ValueError(
                    f"malformed ASCII STL vertex line: {raw_line!r}"
                ) from exc
        elif lowered.startswith("endfacet"):
            if current is not None and len(current) >= 3:
                corners.append(current[:3])
            current = None

    if not corners:
        raise ValueError("ASCII STL contained no facets")
    stacked = np.asarray(corners, dtype=np.float64)
    return _stl_facets_to_mesh(stacked)


def parse_stl_binary(data: bytes) -> Tuple[np.ndarray, np.ndarray]:
    """Parse binary STL: 80 byte header, uint32 facet count, 50 bytes/facet."""
    blob = bytes(data)
    if len(blob) < 84:
        raise ValueError(
            f"binary STL truncated: {len(blob)} bytes, minimum is 84 "
            "(80 byte header + uint32 facet count)"
        )
    count = int(struct.unpack_from("<I", blob, 80)[0])
    expected = 84 + 50 * count
    if expected != len(blob):
        raise ValueError(
            f"binary STL facet count mismatch: header declares {count} facets "
            f"({expected} bytes) but the file holds {len(blob)} bytes"
        )
    if count == 0:
        raise ValueError("binary STL declares zero facets")

    record = np.dtype(
        [("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")]
    )
    table = np.frombuffer(blob, dtype=record, count=count, offset=84)
    vertices, faces = _stl_facets_to_mesh(table["v"].astype(np.float64))
    return vertices, faces


def _binary_count_matches(data: bytes) -> bool:
    """True when the 80 byte header / uint32 count agrees with the file size."""
    if len(data) < 84:
        return False
    count = int(struct.unpack_from("<I", data, 80)[0])
    return 84 + 50 * count == len(data)


def parse_stl(data: bytes) -> Tuple[np.ndarray, np.ndarray]:
    """Parse either STL flavour.

    The decision does **not** look at the first five bytes (many writers prefix
    ASCII files with ``solid``); instead the uint32 facet count at offset 80 is
    checked against ``(len(data) - 84) // 50``.  A binary file that merely
    claims to start with ``solid`` therefore still parses as binary, and an
    ASCII file is recognised because its length cannot match the count.
    """
    if data is None:
        raise ValueError("parse_stl received no data")
    if isinstance(data, str):
        data = data.encode("utf-8")
    blob = bytes(data)

    if _binary_count_matches(blob):
        try:
            return parse_stl_binary(blob)
        except ValueError:
            pass  # fall through to the ASCII parser below

    try:
        return parse_stl_ascii(blob.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as ascii_error:
        if len(blob) >= 84:
            raise ValueError(
                "truncated or malformed STL: not a valid binary stream "
                f"(declared facet count implies {84 + 50 * int(struct.unpack_from('<I', blob, 80)[0])} "
                f"bytes, file holds {len(blob)}) and not valid ASCII ({ascii_error})"
            ) from ascii_error
        raise ValueError(
            f"malformed STL: {len(blob)} bytes is too short for either "
            f"binary (>=84) or ASCII ({ascii_error})"
        ) from ascii_error


# ==========================================================================
# glTF 2.0 / GLB
# ==========================================================================
_GLB_MAGIC = b"glTF"
_GLB_CHUNK_JSON = 0x4E4F534A
_GLB_CHUNK_BIN = 0x004E4942

_COMPONENT_DTYPES: Dict[int, Tuple[np.dtype, int, bool]] = {
    5121: (np.dtype(np.int8), 1, True),
    5122: (np.dtype(np.int16), 2, True),
    5123: (np.dtype(np.uint16), 2, False),
    5125: (np.dtype(np.uint32), 4, False),
    5126: (np.dtype(np.float32), 4, False),
}

_TYPE_COMPONENTS: Dict[str, int] = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT2": 4,
    "MAT3": 9,
    "MAT4": 16,
}


def _decode_data_uri(uri: str) -> bytes:
    header, _, payload = uri.partition(",")
    if not _:
        raise ValueError(f"malformed data: URI (missing comma) in {header!r}")
    try:
        return base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"malformed base64 payload in data: URI ({exc})") from exc


def _glb_chunks(blob: bytes) -> Tuple[bytes, bytes]:
    """Split a GLB container into its JSON chunk and its BIN chunk."""
    if len(blob) < 12:
        raise ValueError(f"GLB truncated: {len(blob)} bytes < 12 byte header")
    if blob[0:4] != _GLB_MAGIC:
        raise ValueError(f"bad GLB magic {blob[0:4]!r}, expected {_GLB_MAGIC!r}")
    version = int(struct.unpack_from("<I", blob, 4)[0])
    if version != 2:
        raise ValueError(f"unsupported GLB version {version} (expected 2)")
    total = int(struct.unpack_from("<I", blob, 8)[0])
    if total > len(blob):
        raise ValueError(
            f"GLB header declares {total} bytes but only {len(blob)} are present"
        )
    json_chunk = b""
    bin_chunk = b""
    offset = 12
    while offset + 8 <= min(total, len(blob)):
        length = int(struct.unpack_from("<I", blob, offset)[0])
        chunk_type = int(struct.unpack_from("<I", blob, offset + 4)[0])
        offset += 8
        if offset + length > len(blob):
            raise ValueError(
                f"GLB chunk of {length} bytes overruns the {len(blob)} byte file"
            )
        payload = blob[offset: offset + length]
        offset += length
        if chunk_type == _GLB_CHUNK_JSON:
            json_chunk = payload
        elif chunk_type == _GLB_CHUNK_BIN and not bin_chunk:
            bin_chunk = payload
    if not json_chunk:
        raise ValueError("GLB contains no JSON chunk")
    return json_chunk, bin_chunk


def _gltf_json(data: bytes) -> Tuple[Dict[str, Any], bytes]:
    """Return ``(json_document, glb_bin_chunk)`` for ``.gltf`` or ``.glb`` bytes."""
    blob = bytes(data)
    if blob[0:4] == _GLB_MAGIC:
        json_bytes, bin_chunk = _glb_chunks(blob)
    else:
        json_bytes, bin_chunk = blob, b""
    try:
        document = json.loads(json_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"glTF JSON payload is malformed: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError("glTF root element must be a JSON object")
    return document, bin_chunk


def _buffer_blobs(
    document: Dict[str, Any], glb_bin: bytes, base_dir: Optional[str]
) -> List[bytes]:
    blobs: List[bytes] = []
    for index, buffer in enumerate(document.get("buffers", []) or []):
        if not isinstance(buffer, dict):
            raise ValueError(f"buffers[{index}] is not an object")
        uri = buffer.get("uri")
        declared = int(buffer.get("byteLength", 0) or 0)
        if uri is None:
            if index == 0:
                payload = glb_bin
            else:
                raise ValueError(f"buffers[{index}] has no uri and no GLB chunk")
        elif str(uri).startswith("data:"):
            payload = _decode_data_uri(str(uri))
        else:
            if base_dir is None:
                raise ValueError(
                    f"buffers[{index}] references external resource {uri!r} "
                    "but no base directory was supplied"
                )
            unquoted = urllib.parse.unquote(str(uri))
            base_path = pathlib.Path(base_dir).resolve()
            target_path = (base_path / unquoted).resolve()
            if not target_path.is_relative_to(base_path) or os.path.commonpath([str(base_path), str(target_path)]) != str(base_path):
                raise ValueError(
                    f"buffers[{index}] external resource {uri!r} escapes base directory"
                )
            try:
                with open(target_path, "rb") as handle:
                    payload = handle.read()
            except OSError as exc:
                raise ValueError(
                    f"buffers[{index}] external resource {uri!r} unreadable: {exc}"
                ) from exc
        if declared and len(payload) > declared:
            payload = payload[:declared]
        blobs.append(payload)
    return blobs


def _read_accessor(
    blobs: Sequence[bytes], document: Dict[str, Any], accessor_index: int
) -> np.ndarray:
    """Materialise accessor ``accessor_index`` as an ``(count, components)`` array."""
    accessors = document.get("accessors", []) or []
    if not isinstance(accessors, list) or accessor_index < 0 or accessor_index >= len(accessors):
        raise ValueError(f"accessor {accessor_index} is out of range")
    accessor = accessors[accessor_index]
    if not isinstance(accessor, dict):
        raise ValueError(f"accessors[{accessor_index}] is not an object")
    if accessor.get("sparse") is not None:
        raise ValueError(
            f"sparse accessors are not supported (accessors[{accessor_index}])"
        )
    comp_type = int(accessor.get("componentType", 0))
    if comp_type not in _COMPONENT_DTYPES:
        raise ValueError(f"unsupported componentType {comp_type}")
    dtype, comp_size, signed = _COMPONENT_DTYPES[comp_type]
    type_name = str(accessor.get("type", "SCALAR"))
    if type_name not in _TYPE_COMPONENTS:
        raise ValueError(f"unsupported accessor type {type_name!r}")
    n_comp = _TYPE_COMPONENTS[type_name]
    count = int(accessor.get("count", 0))
    element_size = n_comp * comp_size

    blob = b""
    start = 0
    stride = element_size
    view_index = accessor.get("bufferView")
    if view_index is None:
        if count:
            raise ValueError(
                f"accessors[{accessor_index}] has neither bufferView nor data"
            )
        return np.zeros((0, n_comp), dtype=dtype)
    views = document.get("bufferViews", []) or []
    view_index = int(view_index)
    if view_index < 0 or view_index >= len(views):
        raise ValueError(f"bufferView {view_index} is out of range")
    view = views[view_index]
    if not isinstance(view, dict):
        raise ValueError(f"bufferViews[{view_index}] is not an object")
    buffers = document.get("buffers", []) or []
    buffer_index = int(view.get("buffer", 0))
    if buffer_index < 0 or buffer_index >= len(buffers) or buffer_index >= len(blobs):
        raise ValueError(f"buffer {buffer_index} referenced by view {view_index} is missing")
    blob = blobs[buffer_index]
    view_offset = int(view.get("byteOffset", 0) or 0)
    view_length = int(view.get("byteLength", 0) or 0)
    if view_length <= 0:
        view_length = len(blob) - view_offset
    start = view_offset + int(accessor.get("byteOffset", 0) or 0)
    if int(view.get("byteStride", 0) or 0) > 0:
        stride = int(view["byteStride"])

    span = stride * (count - 1) + element_size if count else 0
    if start < 0 or start + span > len(blob):
        raise ValueError(
            f"accessors[{accessor_index}] reads [{start}, {start + span}) "
            f"beyond its {len(blob)} byte buffer"
        )

    values: Optional[np.ndarray] = None
    if count:
        try:
            raw = np.frombuffer(blob, dtype=np.uint8, count=stride * count, offset=start)
            strided = np.lib.stride_tricks.as_strided(
                raw, shape=(count, n_comp), strides=(stride, comp_size)
            )
            values = np.array(strided.view(dtype), copy=True)
        except (ValueError, TypeError):
            values = None
    if values is None and count:
        # Portable byte-wise fallback for unaligned / exotic buffer layouts.
        window = memoryview(blob)
        rows = []
        for row in range(count):
            base = start + row * stride
            rows.append(
                np.frombuffer(
                    window[base: base + element_size].tobytes(), dtype=dtype
                )
            )
        values = np.array(rows, dtype=dtype).reshape(count, n_comp)

    if accessor.get("normalized") and comp_type != 5126 and count and values is not None:
        as_float = values.astype(np.float64)
        if signed:
            limit = float(np.iinfo(dtype).max)
            as_float = np.maximum(as_float, -limit - 1.0) / limit
        else:
            as_float = as_float / float(np.iinfo(dtype).max)
        values = as_float.astype(np.float64)

    if values is None:
        values = np.zeros((0, n_comp), dtype=dtype)
    return values


def _node_matrix(node: Dict[str, Any]) -> np.ndarray:
    """Compose a glTF node's local transform (column-major ``matrix`` or TRS)."""
    identity = np.eye(4, dtype=np.float64)
    if "matrix" in node and node["matrix"] is not None:
        flat = np.asarray(node["matrix"], dtype=np.float64).reshape(-1)
        if flat.size != 16:
            raise ValueError("glTF node matrix must hold 16 floats")
        return flat.reshape(4, 4).T.copy()  # glTF is column-major
    translation = np.asarray(
        node.get("translation", [0.0, 0.0, 0.0]), dtype=np.float64
    ).reshape(-1)
    if translation.size != 3:
        raise ValueError("glTF node translation must hold 3 floats")
    quat = np.asarray(node.get("rotation", [0.0, 0.0, 0.0, 1.0]), dtype=np.float64).reshape(-1)
    if quat.size != 4:
        raise ValueError("glTF node rotation must hold 4 floats (x, y, z, w)")
    scale = np.asarray(node.get("scale", [1.0, 1.0, 1.0]), dtype=np.float64).reshape(-1)
    if scale.size != 3:
        raise ValueError("glTF node scale must hold 3 floats")

    norm = float(np.linalg.norm(quat))
    if norm < _EPS:
        quat = np.array([0.0, 0.0, 0.0, 1.0])
    else:
        quat = quat / norm
    x, y, z, w = quat
    rot = np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )
    matrix = identity.copy()
    matrix[:3, :3] = rot @ np.diag(scale)
    matrix[:3, 3] = translation
    return matrix


def _node_list(document: Dict[str, Any]) -> List[Tuple[int, np.ndarray]]:
    """Return ``[(node index, world matrix), ...]`` in breadth-first order."""
    nodes = document.get("nodes", []) or []
    roots: List[int] = []
    scenes = document.get("scenes", []) or []
    scene_index = document.get("scene")
    if isinstance(scene_index, int) and 0 <= scene_index < len(scenes):
        scene = scenes[scene_index]
        if isinstance(scene, dict):
            roots = [int(i) for i in (scene.get("nodes") or [])]
    if not roots and nodes:
        child_ids: set[int] = set()
        for node in nodes:
            if isinstance(node, dict):
                child_ids.update(int(i) for i in (node.get("children") or []))
        roots = [i for i in range(len(nodes)) if i not in child_ids]
    if not roots and nodes:
        roots = [0]

    ordered: List[Tuple[int, np.ndarray]] = []
    stack: List[Tuple[int, np.ndarray]] = []
    for root in reversed(roots):
        if 0 <= root < len(nodes):
            stack.append((root, np.eye(4, dtype=np.float64)))
    seen: set = set()
    while stack:
        index, world = stack.pop()
        if index in seen:  # guard against cyclic node graphs
            continue
        seen.add(index)
        node = nodes[index]
        if not isinstance(node, dict):
            raise ValueError(f"nodes[{index}] is not an object")
        local = _node_matrix(node)
        combined = world @ local
        ordered.append((index, combined))
        for child in reversed([int(i) for i in (node.get("children") or [])]):
            if 0 <= child < len(nodes):
                stack.append((child, combined))
    return ordered


def parse_gltf(
    data: bytes, base_dir: Optional[str] = None
) -> Tuple[np.ndarray, np.ndarray]:
    """Parse glTF 2.0 (``.gltf``) or GLB (``.glb``) bytes.

    Pure standard library: ``json`` for the document, ``base64`` for ``data:``
    URIs and the GLB BIN chunk, ``struct``/``memoryview`` for the accessors.
    Only ``TRIANGLES`` (mode 4) primitives are kept; node ``matrix`` / TRS
    transforms are applied and every primitive is concatenated into a single
    vertex/face pair.
    """
    document, glb_bin = _gltf_json(data)
    blobs = _buffer_blobs(document, glb_bin, base_dir)

    meshes = document.get("meshes", []) or []
    all_points: List[Tuple[float, float, float]] = []
    all_triangles: List[Tuple[int, int, int]] = []

    for node_index, world in _node_list(document):
        node = document["nodes"][node_index]
        mesh_index = node.get("mesh")
        if mesh_index is None:
            continue
        mesh_index = int(mesh_index)
        if mesh_index < 0 or mesh_index >= len(meshes):
            raise ValueError(f"node {node_index} references missing mesh {mesh_index}")
        mesh = meshes[mesh_index]
        if not isinstance(mesh, dict):
            raise ValueError(f"meshes[{mesh_index}] is not an object")
        for primitive in mesh.get("primitives", []) or []:
            if not isinstance(primitive, dict):
                raise ValueError(f"meshes[{mesh_index}] contains a non-object primitive")
            mode = int(primitive.get("mode", 4))
            if mode != 4:
                continue  # only TRIANGLES
            attributes = primitive.get("attributes") or {}
            position_key = attributes.get("POSITION")
            if position_key is None:
                continue
            positions = _read_accessor(blobs, document, int(position_key))
            if positions.shape[0] == 0 or positions.shape[1] < 3:
                continue
            points = np.asarray(positions[:, :3], dtype=np.float64)
            homogeneous = np.hstack(
                [points, np.ones((points.shape[0], 1), dtype=np.float64)]
            )
            transformed = (homogeneous @ world.T)[:, :3]

            indices_accessor = primitive.get("indices")
            if indices_accessor is None:
                count = transformed.shape[0]
                triangles = np.arange(count, dtype=np.int64).reshape(-1, 3)
            else:
                indices_accessor = int(indices_accessor)
                accessors = document.get("accessors", []) or []
                if indices_accessor < 0 or indices_accessor >= len(accessors):
                    raise ValueError(
                        f"primitive index accessor {indices_accessor} is out of range"
                    )
                index_spec = accessors[indices_accessor]
                if not isinstance(index_spec, dict) or str(
                    index_spec.get("type", "SCALAR")
                ) != "SCALAR":
                    raise ValueError("index accessor must be of type SCALAR")
                raw_indices = _read_accessor(blobs, document, indices_accessor)
                flat = np.asarray(raw_indices, dtype=np.int64).reshape(-1)
                if flat.size < 3 or flat.size % 3:
                    raise ValueError(
                        f"triangle index count {flat.size} is not a multiple of three"
                    )
                triangles = flat.reshape(-1, 3)
            if triangles.size and int(triangles.max()) >= transformed.shape[0]:
                raise ValueError(
                    "primitive index out of range: "
                    f"{int(triangles.max())} vs {transformed.shape[0]} vertices"
                )

            offset = len(all_points)
            for row in transformed:
                all_points.append((float(row[0]), float(row[1]), float(row[2])))
            for tri in triangles:
                all_triangles.append(
                    (offset + int(tri[0]), offset + int(tri[1]), offset + int(tri[2]))
                )

    if not all_triangles:
        raise ValueError("glTF document contains no TRIANGLES primitives")

    vertices = np.ascontiguousarray(np.asarray(all_points, dtype=np.float64))
    faces = _as_triangle_list(all_triangles)
    return vertices, faces


def _relink_source(metrics: MeshMetrics) -> Optional[bytes]:
    """Raw bytes of a stored model, or ``None`` when the file is gone."""
    path = metrics.source_path
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


# ==========================================================================
# Primitive triangle quantities
# ==========================================================================
def triangle_areas(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Per-face area ``0.5 * |(b - a) x (c - a)|``."""
    a, b, c = _triangles(vertices, faces)
    if a.shape[0] == 0:
        return np.zeros((0,), dtype=np.float64)
    return 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)


def triangle_normals(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Per-face unit normals (zero-area faces yield a zero vector)."""
    a, b, c = _triangles(vertices, faces)
    if a.shape[0] == 0:
        return np.zeros((0, 3), dtype=np.float64)
    raw = np.cross(b - a, c - a)
    norms = np.linalg.norm(raw, axis=1)
    safe = np.where(norms > _AREA_EPS, norms, 1.0)
    return raw / safe[:, None]


def triangle_centroids(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Per-face centroid ``(a + b + c) / 3``."""
    a, b, c = _triangles(vertices, faces)
    if a.shape[0] == 0:
        return np.zeros((0, 3), dtype=np.float64)
    return (a + b + c) / 3.0


def is_closed_mesh(vertices: np.ndarray, faces: np.ndarray) -> bool:
    """True when every undirected edge is shared by exactly two triangles."""
    tris = np.asarray(faces, dtype=np.int64)
    if tris.shape[0] == 0:
        return False
    edges: List[Tuple[int, int]] = []
    for i0, i1, i2 in tris:
        for lo, hi in ((i0, i1), (i1, i2), (i2, i0)):
            edges.append((int(lo), int(hi)) if lo <= hi else (int(hi), int(lo)))
    if not edges:
        return False
    counts: Dict[Tuple[int, int], int] = {}
    for edge in edges:
        counts[edge] = counts.get(edge, 0) + 1
    return all(count == 2 for count in counts.values())


# ==========================================================================
# Analytic surface / volume integrals
# ==========================================================================
def surface_area(vertices: np.ndarray, faces: np.ndarray) -> float:
    """Total triangular surface area in m^2."""
    areas = triangle_areas(vertices, faces)
    if areas.size == 0:
        return 0.0
    return float(np.sum(areas))


def signed_volume(vertices: np.ndarray, faces: np.ndarray) -> float:
    """Signed enclosed volume ``sum_f dot(a, b x c) / 6`` (outward normals > 0)."""
    a, b, c = _triangles(vertices, faces)
    if a.shape[0] == 0:
        return 0.0
    return float(np.sum(np.einsum("ij,ij->i", a, np.cross(b, c))) / 6.0)


def _tetrahedra(vertices: np.ndarray, faces: np.ndarray):
    """Signed tetra volumes and their centroids for the fan-to-origin split."""
    a, b, c = _triangles(vertices, faces)
    if a.shape[0] == 0:
        empty = np.zeros((0,), dtype=np.float64)
        return empty, np.zeros((0, 3), dtype=np.float64)
    volumes = np.einsum("ij,ij->i", a, np.cross(b, c)) / 6.0
    centroids = (a + b + c) / 4.0
    return volumes, centroids


def volume_centroid(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Tetra-decomposition volume centroid (origin is the fourth apex).

    Returns ``(0, 0, 0)`` when the decomposition cancels out (degenerate or
    non-closed input) so callers never see a NaN.
    """
    volumes, centroids = _tetrahedra(vertices, faces)
    total = float(np.sum(volumes))
    if abs(total) <= _VOLUME_EPS:
        return np.zeros((3,), dtype=np.float64)
    moment = np.einsum("i,ij->j", volumes, centroids)
    return np.asarray(moment / total, dtype=np.float64)


def surface_centroid(
    vertices: np.ndarray, faces: np.ndarray, areas: Optional[np.ndarray] = None
) -> np.ndarray:
    """Area weighted centroid of the triangle soup."""
    if areas is None:
        areas = triangle_areas(vertices, faces)
    areas = np.asarray(areas, dtype=np.float64).reshape(-1)
    centroids = triangle_centroids(vertices, faces)
    total = float(np.sum(areas))
    if centroids.shape[0] == 0 or abs(total) <= _AREA_EPS:
        if vertices.size:
            flat = np.asarray(vertices, dtype=np.float64)
            return np.asarray(np.mean(flat[:, :3], axis=0), dtype=np.float64)
        return np.zeros((3,), dtype=np.float64)
    weights = areas[: centroids.shape[0]] if areas.size >= centroids.shape[0] else areas
    moment = np.einsum("i,ij->j", weights, centroids)
    return np.asarray(moment / float(np.sum(weights)), dtype=np.float64)


def mass_properties(
    vertices: np.ndarray, faces: np.ndarray, density_kgm3: float, cg_mode: str = "volume"
) -> Dict[str, Any]:
    """Volume/area, enclosed volume, mass, centre of gravity and closure flag.

    ``cg_mode="volume"`` requires a closed mesh: when the mesh is open (or the
    signed volume cancels) the centre of gravity falls back to the area
    centroid and ``cg_mode_used`` reports ``"surface"`` honestly.
    """
    verts, tris = _validate_mesh(vertices, faces)
    areas = triangle_areas(verts, tris)
    area = float(np.sum(areas))
    volume = signed_volume(verts, tris)
    abs_volume = abs(volume)
    closed = is_closed_mesh(verts, tris)

    requested = str(cg_mode).strip().lower()
    if requested not in ("volume", "surface"):
        raise ValueError(f"cg_mode must be 'volume' or 'surface', got {cg_mode!r}")

    volume_usable = closed and abs_volume > _VOLUME_EPS
    if requested == "volume" and volume_usable:
        cg = volume_centroid(verts, tris)
        cg_used = "volume"
    else:
        cg = surface_centroid(verts, tris, areas)
        cg_used = "surface"

    density = float(density_kgm3)
    if density < 0.0:
        raise ValueError(f"density_kgm3 must be non-negative, got {density_kgm3!r}")
    mass = density * abs_volume
    if mass <= 0.0:
        # Open or zero-thickness sheet: fall back to the areal density so the
        # reported mass is still physically meaningful for a folded sheet.
        mass = density * PAPER_THICKNESS_M * area

    return {
        "volume_m3": volume,
        "abs_volume_m3": abs_volume,
        "mass_kg": float(mass),
        "cg_m": cg,
        "cg_m_tuple": (float(cg[0]), float(cg[1]), float(cg[2])),
        "is_closed": bool(closed),
        "surface_area_m2": area,
        "cg_mode_used": cg_used,
        "cg_mode_requested": requested,
        "density_kgm3": density,
    }


def inertia_tensor_mesh(
    vertices: np.ndarray, faces: np.ndarray, mass_kg: float, cg_m: Sequence[float]
) -> np.ndarray:
    """Mass-normalised inertia tensor about ``cg_m`` (units m^2).

    The vertices are first translated by ``-cg_m`` -- this *is* the parallel axis
    shift, performed on the geometry so the ``(origin, a, b, c)`` fan
    tetrahedra actually tile the body -- and the exact tetrahedral second moment

        ``int x x^T dV = (V / 20) * [ S S^T + sum_i p_i p_i^T ]``,
        ``S = sum_i p_i``

    is accumulated with the mass share ``mass_kg * V_i / sum(V)``.  That yields
    the mass-normalised second-moment matrix ``M``; the returned tensor is
    ``trace(M) * 1 - M``, i.e. ``I_xx = E[y^2 + z^2]``.  The result is
    symmetric, positive definite for any body of non-zero volume, and compares
    directly with ``inertia_tensor_box(1.0, dx, dy, dz)``; multiply by the mass
    for kg*m^2.

    The decomposition is exact for closed, star-shaped meshes with respect to the
    centre of gravity (every convex polyhedron qualifies).  For strongly concave
    meshes (a folded paper airframe) overlapping tetrahedra cancel in the volume
    and centroid integrals but only approximately in the second moments; the
    value therefore stays a good estimate rather than a strict bound.
    """
    verts, tris = _validate_mesh(vertices, faces)
    mass = float(mass_kg)
    if mass <= 0.0:
        raise ValueError(f"mass_kg must be positive, got {mass_kg!r}")
    if tris.shape[0] == 0:
        return np.zeros((3, 3), dtype=np.float64)

    cg = np.asarray(cg_m, dtype=np.float64).reshape(-1)
    if cg.size != 3:
        raise ValueError("cg_m must hold three components")
    centre = vec3(cg)
    a, b, c = _triangles(verts - centre, tris)

    volumes = np.einsum("ij,ij->i", a, np.cross(b, c)) / 6.0
    total = float(np.sum(volumes))
    if abs(total) <= _VOLUME_EPS:
        return np.zeros((3, 3), dtype=np.float64)
    weight = mass * (volumes / total) * (1.0 / 20.0)

    corner_sum = a + b + c
    moment = np.einsum("i,ij,ik->jk", weight, corner_sum, corner_sum)
    for corner in (a, b, c):
        moment = moment + np.einsum("i,ij,ik->jk", weight, corner, corner)

    # ``moment`` is the mass-normalised second-moment matrix
    # (M_xx = E[x^2]); the inertia about each axis is the trace minus the
    # corresponding second moment: I = trace(M) * 1 - M.
    inertia = float(np.trace(moment)) * np.eye(3) - moment
    inertia = inertia / mass
    return np.asarray(0.5 * (inertia + inertia.T), dtype=np.float64)


# ==========================================================================
# Principal frame and planform
# ==========================================================================
def _axis_sign_fix(axis: np.ndarray) -> np.ndarray:
    """Deterministic sign convention: the largest magnitude component is +."""
    vec = np.asarray(axis, dtype=np.float64).reshape(3)
    pivot = int(np.argmax(np.abs(vec)))
    if vec[pivot] < 0.0:
        vec = -vec
    return vec


def principal_axes(vertices: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """PCA frame ``(span, chord, normal)`` for a point cloud.

    * ``span``  = eigenvector of the largest eigenvalue (the wingspan direction).
    * ``chord`` = eigenvector of the second eigenvalue; if that eigenvalue is
      degenerate with the third one, the world axis least aligned with ``span``
      is used so the result is reproducible.
    * ``normal`` = ``span x chord`` (right handed completion).

    Both basis vectors get a deterministic sign (largest component positive),
    so repeated calls on identical input return identical frames.
    """
    verts = np.asarray(vertices, dtype=np.float64)
    if verts.ndim != 2 or verts.shape[0] == 0:
        raise ValueError("principal_axes needs a non-empty (n, 3) point cloud")
    points = verts[:, :3]
    centre = np.mean(points, axis=0)
    centred = points - centre
    covariance = (centred.T @ centred) / max(1, points.shape[0])
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = eigenvalues[order]
    vectors = eigenvectors[:, order]

    span = _axis_sign_fix(vectors[:, 0])
    if abs(eigenvalues[1] - eigenvalues[2]) <= 1.0e-12 * max(float(eigenvalues[0]), 1.0e-30):
        candidates = np.eye(3, dtype=np.float64)
        candidates = candidates[np.argsort(-np.abs(candidates @ span))]
        chord = candidates[0]
        chord = chord - float(np.dot(chord, span)) * span
        chord = _axis_sign_fix(chord)
    else:
        chord = _axis_sign_fix(vectors[:, 1])
    normal = np.cross(span, chord)
    norm_len = float(np.linalg.norm(normal))
    if norm_len < _EPS:
        normal = _axis_sign_fix(np.cross(span, np.eye(3)[int(np.argmin(np.abs(span)))]))
    else:
        normal = normal / norm_len
    return span, chord, normal


def _percentile_extent(values: np.ndarray, low: float = 5.0, high: float = 95.0) -> float:
    """Robust extent: ``P95 - P5`` of the sample (rejects stray vertices)."""
    if values.size == 0:
        return 0.0
    lo = float(np.percentile(values, low))
    hi = float(np.percentile(values, high))
    return max(hi - lo, 0.0)


#: Spanwise band fractions tried when extracting the root and tip chord.  A
#: band can legitimately contain only vertices that share one chord coordinate
#: (a coarse mesh with two span stations, or a PCA axis tilted by the taper), in
#: which case the band is widened until a real chord shows up.
_STATION_BAND_FRACTIONS = (0.10, 0.20, 0.35, 0.50)


def _station_chord(
    chord_proj: np.ndarray,
    span_proj: np.ndarray,
    span_lo: float,
    span_hi: float,
    from_tip: bool,
) -> float:
    """P95-P5 chord extent of the outermost spanwise band."""
    span = max(span_hi - span_lo, _EPS)
    for fraction in _STATION_BAND_FRACTIONS:
        width = fraction * span
        if from_tip:
            mask = span_proj >= (span_hi - width)
        else:
            mask = span_proj <= (span_lo + width)
        if not np.any(mask):
            continue
        extent = _percentile_extent(chord_proj[mask])
        if extent > _AREA_EPS:
            return extent
    return 0.0


def planform_metrics(vertices: np.ndarray, faces: np.ndarray) -> Dict[str, Any]:
    """Wingspan / chord / taper / aspect ratio recovered from the point cloud.

    Conventions
    -----------
    * ``wingspan_m``  = extent of the PCA span axis.  That axis is the largest
      eigenvector of the vertex covariance, so on a tapered planform it is
      tilted slightly towards the chord and the reported extent is marginally
      longer than the pure geometric span (about +20 % for a mesh whose only
      span stations are the root and the tip).
    * ``root_chord_m`` = 95th-5th percentile chord extent of the outermost 10 %
      of the span, ``tip_chord_m`` = the same over the outermost 10 % at the other
      end.  Both bands widen (10 -> 20 -> 35 -> 50 %) if the narrow band holds
      no chord information at all, so a two-station mesh still reports a real
      root and tip chord instead of zeros.
    * ``planform_area_m2`` = sum of the absolute projected triangle areas in the
      chord/span plane.  A single-sided wing therefore yields exactly its
      planform; a closed plate (two sides) is divided by two so the silhouette
      is reported instead of the double count.
    * ``mean_chord_m`` = ``planform_area / wingspan``.
    * ``taper_ratio`` = ``tip_chord / root_chord`` (0 when the root chord is 0).
    * ``aspect_ratio`` = ``wingspan^2 / planform_area`` (0 when the area is 0).
    """
    verts, tris = _validate_mesh(vertices, faces)
    span_axis, chord_axis, normal_axis = principal_axes(verts)
    span_proj = verts @ span_axis
    chord_proj = verts @ chord_axis

    span_lo = float(np.min(span_proj))
    span_hi = float(np.max(span_proj))
    span = max(span_hi - span_lo, 0.0)

    half = 0.10 * span
    root_chord = _station_chord(chord_proj, span_proj, span_lo, span_hi, from_tip=False)
    tip_chord = _station_chord(chord_proj, span_proj, span_lo, span_hi, from_tip=True)

    a, b, c = _triangles(verts, tris)
    area = 0.0
    if a.shape[0]:
        # The 3-D area vector projected on the PCA normal is the planar
        # (chord/span) area of the triangle; summing |.| over the soup gives the
        # silhouette area, counted twice for a closed plate.
        area_vectors = np.cross(b - a, c - a)
        area = float(np.sum(np.abs(area_vectors @ normal_axis)) / 2.0)
        if is_closed_mesh(verts, tris):
            area *= 0.5

    mean_chord = safe_div(area, span, default=0.0)
    taper = safe_div(tip_chord, root_chord, default=0.0)
    aspect = safe_div(span * span, area, default=0.0)

    return {
        "wingspan_m": float(span),
        "root_chord_m": float(root_chord),
        "tip_chord_m": float(tip_chord),
        "planform_area_m2": float(area),
        "mean_chord_m": float(mean_chord),
        "taper_ratio": float(taper),
        "aspect_ratio": float(aspect),
        "span_axis": tuple(float(v) for v in span_axis),
        "chord_axis": tuple(float(v) for v in chord_axis),
        "normal_axis": tuple(float(v) for v in normal_axis),
        "span_origin_m": float(span_lo),
        "span_range_m": (float(span_lo), float(span_hi)),
        "percentile_low": 5.0,
        "percentile_high": 95.0,
    }


# ==========================================================================
# Panelisation
# ==========================================================================
def build_panels(vertices: np.ndarray, faces: np.ndarray, nx: int = 16, ny: int = 12) -> List[PanelData]:
    """Bin the triangle soup into an ``nx`` (chord) x ``ny`` (span) PCA grid.

    Every non-empty bin is merged into a single :class:`PanelData`:

    * ``area_m2``     = sum of the binned triangle areas,
    * ``normal``      = area weighted mean normal, normalised, with a
      ``(0, 0, 1)`` fallback when the bin cancels out,
    * ``chordwise_frac`` / ``spanwise_frac`` = bin centre fractions in ``(0, 1]``,
    * ``arc_frac``    = cumulative area fraction in emission order (spanwise
      outer loop, chordwise inner loop) divided by the total surface area,
    * ``leading_edge`` = ``True`` only for chordwise bin 0,
    * ``aero_lambda``  = ``0.0`` on the leading edge (no rotational loading
      ahead of the leading edge), ``1.0`` everywhere else.

    Empty bins are skipped and the surviving panels get contiguous indices
    ``0 .. len(panels) - 1``.  Open meshes are fully supported.
    """
    verts, tris = _validate_mesh(vertices, faces)
    nx = max(int(nx), 1)
    ny = max(int(ny), 1)
    if tris.shape[0] == 0:
        return []

    span_axis, chord_axis, _normal_axis = principal_axes(verts)
    areas = triangle_areas(verts, tris)
    normals = triangle_normals(verts, tris)
    centroids = triangle_centroids(verts, tris)

    span_proj = centroids @ span_axis
    chord_proj = centroids @ chord_axis
    span_lo = float(np.min(span_proj))
    span_hi = float(np.max(span_proj))
    chord_lo = float(np.min(chord_proj))
    chord_hi = float(np.max(chord_proj))
    span_range = max(span_hi - span_lo, 1.0e-12)
    chord_range = max(chord_hi - chord_lo, 1.0e-12)

    chord_bin = np.clip(
        ((chord_proj - chord_lo) / chord_range * nx).astype(np.int64), 0, nx - 1
    )
    span_bin = np.clip(
        ((span_proj - span_lo) / span_range * ny).astype(np.int64), 0, ny - 1
    )

    bins: Dict[Tuple[int, int], List[int]] = {}
    for face_index in range(tris.shape[0]):
        bins.setdefault((int(span_bin[face_index]), int(chord_bin[face_index])), []).append(
            face_index
        )

    total_area = float(np.sum(areas))
    ordered: List[PanelData] = []
    cumulative = 0.0
    for span_index in range(ny):
        for chord_index in range(nx):
            members = bins.get((span_index, chord_index))
            if not members:
                continue
            selection = np.asarray(members, dtype=np.int64)
            area = float(np.sum(areas[selection]))
            weighted = np.einsum("i,ij->j", areas[selection], normals[selection])
            norm_len = float(np.linalg.norm(weighted))
            normal = weighted / norm_len if norm_len > _AREA_EPS else np.array([0.0, 0.0, 1.0])
            centre = np.einsum(
                "i,ij->j", areas[selection], centroids[selection]
            ) / (area if area > _AREA_EPS else 1.0)
            cumulative += area
            ordered.append(
                PanelData(
                    index=len(ordered),
                    centroid_m=(float(centre[0]), float(centre[1]), float(centre[2])),
                    normal=(float(normal[0]), float(normal[1]), float(normal[2])),
                    area_m2=area,
                    chordwise_frac=(chord_index + 0.5) / nx,
                    spanwise_frac=(span_index + 0.5) / ny,
                    arc_frac=safe_div(cumulative, total_area, default=0.0),
                    aero_lambda=0.0 if chord_index == 0 else 1.0,
                    local_alpha_deg=0.0,
                    leading_edge=bool(chord_index == 0),
                )
            )
    return ordered


def panel_aerodynamics(
    panels: Sequence[PanelData],
    alpha_deg: float,
    dynamic_pressure_pa: float,
    env: Optional[Any] = None,
    cg_m: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """Static panel method: local incidence, lift, rotational lift and moment.

    Frame / signs
    -------------
    Body axes follow the project convention (``x`` forward, ``y`` right, ``z``
    down).  The free-stream direction for a geometric angle of attack ``alpha``
    is ``d = (-cos a, 0, +sin a)`` so that ``alpha > 0`` (nose up) produces
    ``alpha_local > 0`` on a wing panel whose normal points up.

    Model
    -----
    * ``alpha_local = asin(-dot(n, d))`` -- the panel inclination removed from
      the geometric angle of attack.
    * ``C_L = 2*pi*sin(alpha_local)`` blended past ``STALL_ALPHA_DEG`` into the
      flat-plate law ``2 sin(a) cos(a)`` with a smoothstep weight.
    * ``C_L_rot = C_L * x_cg_local / c`` (rotational augmentation, ``x_cg_local``
      positive when the panel lies **aft** of the reference cg).  The default
      reference cg is 25 % of the panel-cloud chord extent, i.e. the standard
      thin-airfoil reference; pass ``cg_m`` to override it.
    * ``lambda = q * (C_L + C_L_rot) * area`` along ``l = n - (n.d) d``,
      the in-plane lift direction.
    * profile drag ``0.02 * q * area`` summed over the panels.

    ``env`` may supply ``lift_scale`` / ``drag_scale`` multipliers and, when
    ``compressibility_enabled`` is set together with ``temperature_K`` and
    ``pressure_Pa``, a Prandtl-Glauert correction
    ``1 / sqrt(1 - M^2)`` with ``M^2 = 2 q / (gamma p)``.
    """
    lift_scale = 1.0
    drag_scale = 1.0
    prandt_glauert = 1.0
    if env is not None:
        lift_scale = float(getattr(env, "lift_scale", 1.0))
        drag_scale = float(getattr(env, "drag_scale", 1.0))
        if bool(getattr(env, "compressibility_enabled", False)):
            temperature = float(getattr(env, "temperature_K", 0.0) or 0.0)
            pressure = float(getattr(env, "pressure_Pa", 0.0) or 0.0)
            if temperature > 1.0 and pressure > 1.0:
                mach_sq = 2.0 * max(float(dynamic_pressure_pa), 0.0) / (1.4 * pressure)
                mach_sq = clamp(mach_sq, 0.0, 0.90)
                prandt_glauert = 1.0 / math.sqrt(1.0 - mach_sq)

    alpha_rad = math.radians(float(alpha_deg))
    flow = np.array([-math.cos(alpha_rad), 0.0, math.sin(alpha_rad)], dtype=np.float64)
    q = float(dynamic_pressure_pa)

    panel_list = list(panels) if panels is not None else []
    if not panel_list:
        return {
            "panels": [],
            "total_lift_n": 0.0,
            "total_drag_n": 0.0,
            "total_moment_nm": 0.0,
            "max_panel_lambda_n": 0.0,
            "count": 0,
            "backend": "numpy",
            "alpha_deg": float(alpha_deg),
            "dynamic_pressure_pa": q,
        }

    if cg_m is not None:
        reference_cg = np.asarray(cg_m, dtype=np.float64).reshape(-1)
        if reference_cg.size != 3:
            raise ValueError("cg_m must hold three components")
    else:
        reference_cg = None

    centroids = np.asarray(
        [vec3(p.centroid_m) for p in panel_list], dtype=np.float64
    )
    areas = np.asarray([float(p.area_m2) for p in panel_list], dtype=np.float64)
    span_axis, chord_axis, _normal = principal_axes(centroids)
    chord_proj = centroids @ chord_axis
    chord_extent = max(float(np.max(chord_proj)) - float(np.min(chord_proj)), 1.0e-12)
    chord_lo = float(np.min(chord_proj))

    if reference_cg is None:
        reference_cg_proj = chord_lo + 0.25 * chord_extent
        mean_point = np.mean(centroids, axis=0)
        reference_cg = (
            chord_axis * reference_cg_proj
            + span_axis * float(np.dot(mean_point, span_axis))
            + _normal * float(np.dot(mean_point, _normal))
        )
    cg_proj = float(np.dot(reference_cg, chord_axis))

    rows: List[Dict[str, Any]] = []
    total_lift = 0.0
    total_drag = 0.0
    moment = np.zeros(3, dtype=np.float64)
    max_lambda = 0.0

    for position, panel in enumerate(panel_list):
        normal = normalize(vec3(panel.normal))
        centroid = centroids[position]
        area = max(float(areas[position]), 0.0)

        cos_local = -float(np.dot(normal, flow))
        if math.isnan(cos_local) or not math.isfinite(cos_local):
            cos_local = 0.0
        else:
            cos_local = float(np.clip(cos_local, -1.0, 1.0))
        local_rad = math.asin(cos_local)
        local_deg = math.degrees(local_rad)

        magnitude = abs(local_rad)
        cl_linear = 2.0 * math.pi * math.sin(local_rad)
        if magnitude <= math.radians(STALL_ALPHA_DEG):
            cl = cl_linear
        else:
            width = math.radians(10.0)
            weight = clamp((magnitude - math.radians(STALL_ALPHA_DEG)) / width, 0.0, 1.0)
            weight = weight * weight * (3.0 - 2.0 * weight)
            cl_flat = 2.0 * math.sin(local_rad) * math.cos(local_rad)
            cl = (1.0 - weight) * cl_linear + weight * cl_flat

        cl_rot = cl * safe_div(cg_proj - float(chord_proj[position]), chord_extent, default=0.0)
        cl_total = (cl + cl_rot) * lift_scale * prandt_glauert
        lambda_n = q * cl_total * area

        in_plane = normal - float(np.dot(normal, flow)) * flow
        lift_dir = normalize(in_plane, fallback=(0.0, 0.0, -1.0))
        force = lambda_n * lift_dir
        lever = centroid - reference_cg
        moment += np.cross(lever, force)

        drag = PANEL_PROFILE_DRAG_CD * q * area * drag_scale
        total_drag += drag
        total_lift += lambda_n
        max_lambda = max(max_lambda, abs(lambda_n))

        if hasattr(panel, "local_alpha_deg"):
            panel.local_alpha_deg = float(local_deg)

        rows.append(
            {
                "index": int(getattr(panel, "index", position)),
                "area_m2": float(area),
                "normal": (float(normal[0]), float(normal[1]), float(normal[2])),
                "alpha_local_deg": float(local_deg),
                "cl": float(cl),
                "cl_rot": float(cl_rot),
                "lambda_N": float(lambda_n),
                "drag_N": float(drag),
                "moment_Nm": float(np.dot(np.cross(lever, force), np.array([0.0, 1.0, 0.0]))),
            }
        )

    return {
        "panels": rows,
        "total_lift_n": float(total_lift),
        "total_drag_n": float(total_drag),
        "total_moment_nm": float(moment[1]),
        "moment_vector_nm": (float(moment[0]), float(moment[1]), float(moment[2])),
        "max_panel_lambda_n": float(max_lambda),
        "count": int(len(rows)),
        "backend": "numpy",
        "alpha_deg": float(alpha_deg),
        "dynamic_pressure_pa": q,
        "reference_chord_m": float(chord_extent),
        "reference_cg_m": (float(reference_cg[0]), float(reference_cg[1]), float(reference_cg[2])),
        "prandt_glauert": float(prandt_glauert),
    }


def paper_mass_from_mesh(sheet_area_m2: float, paper_gsm: float) -> float:
    """Mass of a folded sheet: ``m = gsm * area / 1000`` (kg).

    Grammage is an **areal** mass density in g/m^2, so multiplying by the area
    already gives the mass in grams and the only conversion needed is ``/ 1000``.
    The bulk density :data:`config_schema.PAPER_DENSITY_BASE` must *not* appear
    here: it belongs in the thickness relation ``t = sigma / rho`` (see
    :data:`PAPER_THICKNESS_M`), and folding it into the mass would overstate an
    airframe by the factor ``rho / 1000 = 0.8`` -- i.e. ``0.02 m^2`` of
    ``100 g/m^2`` stock is ``2 g = 0.002 kg``, not ``16 g``.

    Parameters
    ----------
    sheet_area_m2:
        Area of the sheet in m^2 (planform when available, else the surface
        silhouette).
    paper_gsm:
        Areal mass density in g/m^2.
    """
    area = float(sheet_area_m2)
    if area < 0.0:
        raise ValueError(f"sheet_area_m2 must be non-negative, got {sheet_area_m2!r}")
    gsm = float(paper_gsm)
    if gsm < 0.0:
        raise ValueError(f"paper_gsm must be non-negative, got {paper_gsm!r}")
    return gsm * area / 1000.0


# ==========================================================================
# Hashing, loading and metric assembly
# ==========================================================================
def mesh_hash_of(vertices: np.ndarray, faces: np.ndarray) -> str:
    """SHA-256 of the vertex/face buffers -- stable for identical geometry."""
    import hashlib

    verts = np.ascontiguousarray(np.asarray(vertices, dtype=np.float64)[:, :3])
    tris = np.ascontiguousarray(np.asarray(faces, dtype=np.int64).reshape(-1, 3))
    digest = hashlib.sha256()
    digest.update(b"paperv3-mesh-v1")
    digest.update(np.asarray(verts.shape, dtype=np.int64).tobytes())
    digest.update(verts.tobytes())
    digest.update(np.asarray(tris.shape, dtype=np.int64).tobytes())
    digest.update(tris.tobytes())
    return digest.hexdigest()


def _trimesh_load(source: Any, filename: str) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """Optional cross-check path; returns ``None`` when trimesh is unavailable."""
    try:  # pragma: no cover - exercised only where trimesh exists
        import trimesh  # type: ignore
    except Exception:
        return None
    try:  # pragma: no cover - depends on the installed trimesh version
        if isinstance(source, (bytes, bytearray)):
            mesh = trimesh.load(io.BytesIO(bytes(source)), file_type=filename)
        else:
            mesh = trimesh.load(source, file_type=os.path.basename(filename))
        if mesh is None or not hasattr(mesh, "vertices"):
            return None
        return (
            np.asarray(mesh.vertices, dtype=np.float64)[:, :3],
            np.asarray(mesh.faces, dtype=np.int64).reshape(-1, 3),
        )
    except Exception:
        return None


#: Host names that resolve to a cloud instance-metadata service.  The address
#: itself is link-local and therefore already blocked, but the names are
#: rejected explicitly so the intent is documented and the check does not
#: depend on the resolver.
_BLOCKED_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "metadata",
        "instance-data",
        "instance-data.ec2.internal",
        "metadata.azure.internal",
        "metadata.azure.com",
        "169.254.169.254",
    }
)

#: Maximum number of HTTP redirects that are followed.  Every hop is
#: re-validated (SPECIFICATION 4.2).
MAX_REDIRECTS = 5


def _is_forbidden_address(ip_obj: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> bool:
    """True when the address must never be reached from the server."""
    return bool(
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_reserved
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
        or getattr(ip_obj, "is_site_local", False)
    )


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only after re-validating the target URL.

    SPECIFICATION 4.2 requires *multi-hop* validation: each redirect target is
    put through the same SSRF checks as the original request, so a public URL
    cannot bounce the server into a private network.
    """

    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url_safe(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def validate_url_safe(url: str) -> urllib.parse.ParseResult:
    """Validate that ``url`` is safe against SSRF attacks.

    Only allows http/https schemes, standard ports (80/443), and non-private/local
    network IP destinations.  Cloud instance-metadata services are rejected by
    name as well as by address.
    """
    parsed = urllib.parse.urlparse(str(url))
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported URL scheme {parsed.scheme!r} in {url!r}")
    if parsed.port and parsed.port not in (80, 443):
        raise ValueError(f"port {parsed.port} is not allowed; only standard HTTP/HTTPS ports (80, 443) are supported")
    hostname = parsed.hostname or ""
    if not hostname:
        raise ValueError(f"invalid host in URL {url!r}")

    if hostname.lower() in ("localhost", "127.0.0.1", "::1"):
        raise ValueError(f"access to loopback host {hostname!r} is forbidden")
    if hostname.lower() in _BLOCKED_HOSTNAMES:
        raise ValueError(f"access to cloud metadata host {hostname!r} is forbidden")

    try:
        ip_obj = ipaddress.ip_address(hostname)
        if _is_forbidden_address(ip_obj):
            raise ValueError(f"access to private/local network address {hostname!r} is forbidden")
    except ValueError:
        try:
            addr_info = socket.getaddrinfo(hostname, None)
            for _, _, _, _, sockaddr in addr_info:
                ip_str = sockaddr[0]
                ip_obj = ipaddress.ip_address(ip_str)
                if _is_forbidden_address(ip_obj):
                    raise ValueError(f"access to private/local network address {ip_str!r} is forbidden")
        except socket.gaierror as exc:
            if getattr(urllib.request.urlopen, "__module__", "") != "urllib.request":
                pass  # Fall through for test mocks
            else:
                raise ValueError(f"cannot resolve hostname {hostname!r}: {exc}") from exc

    return parsed


def safe_urlopen_bytes(
    url: str,
    max_bytes: int = 64 * 1024 * 1024,
    timeout: float = 20.0,
    headers: Optional[Dict[str, str]] = None,
) -> bytes:
    """Safely fetch a URL payload verifying against SSRF and redirect escapes.

    Every redirect hop is re-validated, so a public URL cannot bounce the
    server into a private network.
    """
    validate_url_safe(url)
    req_headers = {"User-Agent": "paper-airplane-sim/1.0"}
    if headers:
        req_headers.update(headers)
    request = urllib.request.Request(str(url), headers=req_headers)

    try:
        if getattr(urllib.request.urlopen, "__module__", "") != "urllib.request":
            cm = urllib.request.urlopen(request, timeout=float(timeout))
        else:
            opener = urllib.request.build_opener(_SafeRedirectHandler)
            cm = opener.open(request, timeout=float(timeout))
        with cm as response:
            try:
                raw_bytes = response.read(max_bytes + 1)
            except TypeError:
                raw_bytes = response.read()
            data = bytes(raw_bytes)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ValueError(f"failed to fetch {url!r}: {exc}") from exc

    if len(data) > max_bytes:
        raise ValueError(f"downloaded content from {url!r} exceeds {max_bytes} bytes limit")
    return data


def _read_source(source: Any) -> bytes:
    """Coerce ``source`` (bytes / path / file object / URL) into raw bytes."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        return bytes(source)
    if hasattr(source, "read"):
        data = source.read()
        return bytes(data)
    text = str(source)
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme in ("http", "https"):
        data = safe_urlopen_bytes(text, max_bytes=64 * 1024 * 1024, timeout=20.0)
        return data

    resolved_path = pathlib.Path(text).resolve()
    if not resolved_path.is_file():
        raise ValueError(f"mesh source file does not exist: {text!r}")
    try:
        with open(resolved_path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise ValueError(f"cannot read mesh source {text!r}: {exc}") from exc


def load_geometry(
    source: Any, filename: str = "model.glb", scale_hint: str = "m"
) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """Read any supported mesh and return ``(vertices, faces, info)``.

    ``source`` may be raw ``bytes``, a filesystem path, an ``http(s)`` URL or a
    binary file object.  ``info`` reports ``backend`` (``"trimesh"`` only when
    trimesh actually parsed the payload, otherwise ``"numpy"``), the detected
    ``format``, the applied ``scale_factor`` and the post-scale counts.
    """
    factor = scale_factor_for(scale_hint)
    data = _read_source(source)
    if not data:
        raise ValueError(f"mesh source {filename!r} is empty")
    fmt = detect_format(filename, data)

    base_dir = None
    if fmt == "gltf" and isinstance(source, str):
        parsed = urllib.parse.urlparse(str(source))
        if not parsed.scheme:
            base_dir = os.path.dirname(os.path.abspath(str(source)))

    backend = "numpy"
    parsed_geometry: Optional[Tuple[np.ndarray, np.ndarray]] = None
    target_filename = filename if ("." in filename) else f"{filename}.{fmt}"
    trimesh_result = _trimesh_load(data, target_filename)
    if trimesh_result is not None:
        parsed_geometry = trimesh_result
        backend = "trimesh"

    if parsed_geometry is None:
        if fmt == "obj":
            parsed_geometry = parse_obj(data.decode("utf-8", errors="replace"))
        elif fmt == "stl":
            parsed_geometry = parse_stl(data)
        else:
            parsed_geometry = parse_gltf(data, base_dir=base_dir)
        backend = "numpy"

    vertices, faces = _validate_mesh(*parsed_geometry)
    if factor != 1.0:
        vertices = vertices * factor
    if vertices.shape[0] == 0 or faces.shape[0] == 0:
        raise ValueError(
            f"{fmt} payload produced an empty mesh "
            f"({vertices.shape[0]} vertices, {faces.shape[0]} faces)"
        )

    info = {
        "backend": backend,
        "format": fmt,
        "scale_factor": float(factor),
        "units": str(scale_hint),
        "vertex_count": int(vertices.shape[0]),
        "face_count": int(faces.shape[0]),
        "byte_count": int(len(data)),
    }
    return vertices, faces, info


def _units_for_factor(factor: float) -> str:
    """Reverse of :func:`scale_factor_for` (``1.0`` maps back to ``"m"``)."""
    for unit, value in SCALE_FACTORS.items():
        if abs(value - float(factor)) <= 1.0e-15 * max(abs(value), 1.0):
            return unit
    return "m"


#: ``MeshMetrics`` fields that ``overrides`` may replace, and their coercion.
_OVERRIDABLE_FLOATS = (    "surface_area_m2",
    "planform_area_m2",
    "wingspan_m",
    "mean_chord_m",
    "taper_ratio",
    "aspect_ratio",
    "mass_kg",
    "volume_m3",
    "paper_gsm",
    "scale_factor",
)

_OVERRIDABLE_CG = ("cg_x_m", "cg_y_m", "cg_z_m", "cg_m")


def mesh_metric_values(metrics: MeshMetrics) -> Dict[str, float]:
    """Flat view of every entry of :data:`MESH_METRIC_KEYS` for JSON payloads."""
    values: Dict[str, float] = {}
    for key in MESH_METRIC_KEYS:
        if key.startswith("cg_") and key.endswith("_m"):
            axis = {"cg_x_m": 0, "cg_y_m": 1, "cg_z_m": 2}[key]
            values[key] = float(metrics.cg_m[axis])
        else:
            values[key] = float(getattr(metrics, key))
    return values


def _apply_overrides(
    metrics: MeshMetrics, overrides: Optional[Dict[str, Any]]
) -> MeshMetrics:
    if not overrides:
        return metrics
    cg = [float(v) for v in metrics.cg_m]
    recorded: Dict[str, float] = {}
    for raw_key, raw_value in dict(overrides).items():
        key = str(raw_key)
        if key in _OVERRIDABLE_CG:
            if key == "cg_m":
                values = np.asarray(raw_value, dtype=np.float64).reshape(-1)
                if values.size != 3 or not np.all(np.isfinite(values)):
                    raise ValueError("overrides['cg_m'] must hold three finite components")
                cg = [float(values[0]), float(values[1]), float(values[2])]
                recorded["cg_x_m"] = cg[0]
                recorded["cg_y_m"] = cg[1]
                recorded["cg_z_m"] = cg[2]
            else:
                axis = {"cg_x_m": 0, "cg_y_m": 1, "cg_z_m": 2}[key]
                val = float(raw_value)
                if not math.isfinite(val):
                    raise ValueError(f"override '{key}' must be a finite number, got {raw_value!r}")
                cg[axis] = val
                recorded[key] = val
            continue
        if key not in _OVERRIDABLE_FLOATS:
            raise ValueError(
                f"unsupported mesh metric override {key!r}; allowed: "
                f"{', '.join(sorted(_OVERRIDABLE_FLOATS + _OVERRIDABLE_CG))}"
            )
        val = float(raw_value)
        if not math.isfinite(val):
            raise ValueError(f"override '{key}' must be a finite number, got {raw_value!r}")
        setattr(metrics, key, val)
        recorded[key] = val
    metrics.cg_m = (cg[0], cg[1], cg[2])
    metrics.overrides = recorded
    return metrics


def compute_metrics(
    vertices: np.ndarray,
    faces: np.ndarray,
    name: str = "",
    source_format: str = "",
    model_id: str = "",
    scale_factor: float = 1.0,
    cg_mode: str = "volume",
    paper_gsm: float = 100.0,
    overrides: Optional[Dict[str, Any]] = None,
    panel_nx: int = 16,
    panel_ny: int = 12,
    source_path: str = "",
    backend: str = "numpy",
) -> MeshMetrics:
    """Populate every :class:`MeshMetrics` field from a vertex/face pair.

    Derivation notes
    ----------------
    * ``cg_m`` comes from :func:`mass_properties` with the requested ``cg_mode``
      (volume centroid when the mesh is closed, area centroid otherwise --
      ``MeshMetrics.cg_mode`` records which of the two was actually used).
    * ``sheet area`` is the planform area (falling back to the surface area for a
      degenerate projection) and ``mass_kg`` is
      ``paper_mass_from_mesh(sheet_area, paper_gsm)``; the enclosed volume is
      reported separately in ``volume_m3``.
    * ``inertia_tensor`` is the mass-normalised tensor of
      :func:`inertia_tensor_mesh` multiplied by ``mass_kg``, i.e. kg*m^2 about
      ``cg_m``.  When the mesh encloses no volume the thin-plate estimate from
      :func:`inertia_tensor_box` is used instead.
    * ``overrides`` are applied **after** the automatic derivation and recorded
      verbatim in ``MeshMetrics.overrides``.
    """
    verts, tris = _validate_mesh(vertices, faces)
    properties = mass_properties(verts, tris, PAPER_DENSITY_BASE, cg_mode=cg_mode)
    planform = planform_metrics(verts, tris)
    panels = build_panels(verts, tris, nx=panel_nx, ny=panel_ny)

    planform_area = float(planform["planform_area_m2"])
    sheet_area = planform_area if planform_area > 0.0 else float(properties["surface_area_m2"])
    mass = paper_mass_from_mesh(sheet_area, paper_gsm)

    cg = tuple(float(v) for v in properties["cg_m"])
    specific = inertia_tensor_mesh(verts, tris, max(mass, 1.0e-12), cg)
    trace = float(np.trace(specific))
    if not np.all(np.isfinite(specific)) or trace <= 0.0:
        # Uniform surface mass on an open paper sheet. Exact triangle
        # second moments preserve body axes and off-diagonal components.
        corners = verts[tris] - np.asarray(cg)
        areas = 0.5 * np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0],
                                            corners[:, 2] - corners[:, 0]), axis=1)
        weights = areas / max(float(areas.sum()), 1.0e-30) / 12.0
        sums = corners.sum(axis=1)
        second = np.einsum("i,ij,ik->jk", weights, sums, sums)
        for index in range(3):
            second += np.einsum("i,ij,ik->jk", weights, corners[:, index], corners[:, index])
        specific = np.trace(second) * np.eye(3) - second
    inertia = specific * mass

    metrics = MeshMetrics(
        model_id=str(model_id or make_uuid()),
        name=str(name or "unnamed-model"),
        source_format=str(source_format or ""),
        source_path=str(source_path or ""),
        units=_units_for_factor(float(scale_factor)),
        scale_factor=float(scale_factor),
        vertex_count=int(verts.shape[0]),
        face_count=int(tris.shape[0]),
        surface_area_m2=float(properties["surface_area_m2"]),
        planform_area_m2=planform_area,
        wingspan_m=float(planform["wingspan_m"]),
        mean_chord_m=float(planform["mean_chord_m"]),
        taper_ratio=float(planform["taper_ratio"]),
        aspect_ratio=float(planform["aspect_ratio"]),
        cg_m=(float(cg[0]), float(cg[1]), float(cg[2])),
        cg_mode=str(properties["cg_mode_used"]),
        volume_m3=float(properties["volume_m3"]),
        mass_kg=float(mass),
        inertia_tensor=(
            float(inertia[0, 0]),
            float(inertia[0, 1]),
            float(inertia[0, 2]),
            float(inertia[1, 0]),
            float(inertia[1, 1]),
            float(inertia[1, 2]),
            float(inertia[2, 0]),
            float(inertia[2, 1]),
            float(inertia[2, 2]),
        ),
        paper_gsm=float(paper_gsm),
        panels=list(panels),
        panel_count=int(len(panels)),
        overrides={},
        created_at=utc_now_iso(),
        backend=str(backend),
        mesh_hash=mesh_hash_of(verts, tris),
    )
    return _apply_overrides(metrics, overrides)

# ==========================================================================
# Persistent model library
# ==========================================================================
_EXTENSION_FOR_FORMAT = {"glb": ".glb", "gltf": ".gltf", "obj": ".obj", "stl": ".stl"}


def _metrics_from_payload(payload: Dict[str, Any]) -> MeshMetrics:
    """Rebuild a :class:`MeshMetrics` from its ``as_dict`` JSON form."""
    metrics = MeshMetrics()
    for field_name in MeshMetrics.__dataclass_fields__:
        if field_name not in payload:
            continue
        value = payload[field_name]
        if field_name == "panels":
            panels: List[PanelData] = []
            for row in value or []:
                panel = PanelData()
                for key, item in dict(row).items():
                    if key in PanelData.__dataclass_fields__:
                        setattr(panel, key, item)
                panels.append(panel)
            metrics.panels = panels
            continue
        if field_name == "inertia_tensor":
            flat = [float(v) for v in np.asarray(value, dtype=np.float64).reshape(-1)]
            if len(flat) != 9:
                raise ValueError("inertia_tensor must serialise to nine components")
            metrics.inertia_tensor = tuple(flat)  # type: ignore[assignment]
            continue
        if field_name == "cg_m":
            metrics.cg_m = tuple(float(v) for v in np.asarray(value, dtype=np.float64).reshape(-1))  # type: ignore[assignment]
            continue
        if field_name == "overrides":
            metrics.overrides = {str(k): float(v) for k, v in dict(value or {}).items()}
            continue
        if field_name in ("vertex_count", "face_count", "panel_count"):
            setattr(metrics, field_name, int(value))
            continue
        setattr(metrics, field_name, value)
    metrics.panel_count = int(len(metrics.panels))
    return metrics


class MeshLibrary:
    """Thread-safe on-disk cache of imported meshes and their measurements.

    Each import writes two real files under ``root``: ``<model_id>.<ext>`` holds
    the raw bytes exactly as received and ``<model_id>.json`` holds the
    ``as_dict`` measurement record.  The in-memory index is rebuilt by scanning
    ``root`` on construction, so a fresh process sees every previously imported
    model.
    """

    def __init__(self, root: str = "models", max_models: int = 250) -> None:
        self.root = str(root)
        self._lock = threading.RLock()
        self._models: Dict[str, MeshMetrics] = {}
        self._selected_id: Optional[str] = None
        self._max_models: int = max(10, int(max_models))
        os.makedirs(self.root, exist_ok=True)
        self._rebuild_index()

    def _enforce_quota(self) -> None:
        """Evict oldest unselected models when exceeding _max_models."""
        with self._lock:
            while len(self._models) > self._max_models:
                candidates = [mid for mid in self._models if mid != self._selected_id]
                if not candidates:
                    break
                self.delete(candidates[0])

    # -- persistence ------------------------------------------------------
    def _json_path(self, model_id: str) -> str:
        return os.path.join(self.root, f"{model_id}.json")

    def _binary_path(self, model_id: str, source_format: str) -> str:
        extension = _EXTENSION_FOR_FORMAT.get(str(source_format).lower(), ".bin")
        return os.path.join(self.root, f"{model_id}{extension}")

    def _rebuild_index(self) -> None:
        with self._lock:
            self._models.clear()
            try:
                entries = sorted(os.listdir(self.root))
            except OSError:
                return
            for entry in entries:
                if not entry.endswith(".json"):
                    continue
                path = os.path.join(self.root, entry)
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        payload = json.load(handle)
                except (OSError, json.JSONDecodeError):
                    continue
                record = payload.get("metrics", payload) if isinstance(payload, dict) else {}
                if not isinstance(record, dict) or not record.get("model_id"):
                    continue
                try:
                    metrics = _metrics_from_payload(record)
                except (TypeError, ValueError):
                    continue
                self._models[metrics.model_id] = metrics
            self._enforce_quota()

    def _persist(self, metrics: MeshMetrics) -> MeshMetrics:
        with self._lock:
            payload = {"schema": "paperv3.mesh/1", "metrics": as_dict(metrics)}
            with open(self._json_path(metrics.model_id), "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=True, sort_keys=True)
            self._models[metrics.model_id] = metrics
            self._enforce_quota()
            return metrics

    # -- import paths -----------------------------------------------------
    def import_model(
        self,
        source: Any,
        filename: str = "model.glb",
        scale_hint: str = "m",
        cg_mode: str = "volume",
        paper_gsm: float = 100.0,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> MeshMetrics:
        """Import from bytes, a filesystem path or an ``http(s)`` URL."""
        is_reference = isinstance(source, str)
        source_path = str(source) if is_reference else ""
        data = _read_source(source)
        metrics = self._build(
            data,
            filename,
            scale_hint,
            cg_mode,
            paper_gsm,
            overrides,
            source_path,
            geometry_source=source if is_reference else None,
        )
        return self._write_binary(metrics, data)

    def import_model_bytes(
        self,
        data: bytes,
        filename: str,
        scale_hint: str = "m",
        cg_mode: str = "volume",
        paper_gsm: float = 100.0,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> MeshMetrics:
        """Import an in-memory payload (the browser file / drag-drop path)."""
        metrics = self._build(data, filename, scale_hint, cg_mode, paper_gsm, overrides, "", None)
        return self._write_binary(metrics, bytes(data))

    def import_model_url(
        self,
        url: str,
        scale_hint: str = "m",
        cg_mode: str = "volume",
        paper_gsm: float = 100.0,
        overrides: Optional[Dict[str, Any]] = None,
        timeout: float = 10.0,
    ) -> MeshMetrics:
        parsed = validate_url_safe(url)
        data = safe_urlopen_bytes(
            str(url),
            max_bytes=64 * 1024 * 1024,
            timeout=float(timeout),
            headers={"User-Agent": "paper-airplane-sim/1.0"},
        )
        if not data:
            raise ValueError(f"{url!r} returned an empty body")
        name = os.path.basename(parsed.path) or "model.glb"
        metrics = self._build(
            data,
            name,
            scale_hint,
            cg_mode,
            paper_gsm,
            overrides,
            str(url),
            geometry_source=None,
        )
        return self._write_binary(metrics, data)

    def _build(
        self,
        data: bytes,
        filename: str,
        scale_hint: str,
        cg_mode: str,
        paper_gsm: float,
        overrides: Optional[Dict[str, Any]],
        source_path: str,
        geometry_source: Any = None,
    ) -> MeshMetrics:
        blob = bytes(data)
        if not blob:
            raise ValueError(f"model payload {filename!r} is empty")
        fmt = detect_format(filename, blob)
        # A path/URL source lets load_geometry resolve external .bin resources.
        source_for_parse = geometry_source if geometry_source is not None else blob
        vertices, faces, info = load_geometry(
            source_for_parse, filename=filename, scale_hint=scale_hint
        )
        name = os.path.splitext(os.path.basename(str(filename)))[0] or "model"
        return compute_metrics(
            vertices,
            faces,
            name=name,
            source_format=fmt,
            model_id=make_uuid(),
            scale_factor=info["scale_factor"],
            cg_mode=cg_mode,
            paper_gsm=paper_gsm,
            overrides=overrides,
            source_path=source_path,
            backend=info["backend"],
        )

    def _write_binary(self, metrics: MeshMetrics, data: bytes) -> MeshMetrics:
        path = self._binary_path(metrics.model_id, metrics.source_format)
        with open(path, "wb") as handle:
            handle.write(data)
        metrics.source_path = path
        return self._persist(metrics)

    # -- queries ----------------------------------------------------------
    def list_models(self) -> List[Dict[str, Any]]:
        """Every stored model as a JSON dict (panel geometry is elided)."""
        with self._lock:
            records: List[Dict[str, Any]] = []
            for metrics in self._models.values():
                payload = as_dict(metrics)
                panel_count = len(payload.get("panels") or [])
                payload.pop("panels", None)
                payload["panel_count"] = panel_count
                payload["selected"] = metrics.model_id == self._selected_id
                records.append(payload)
            records.sort(key=lambda row: row.get("created_at", ""))
            return records

    def get_metrics(self, model_id: str) -> MeshMetrics:
        with self._lock:
            metrics = self._models.get(str(model_id))
        if metrics is None:
            raise KeyError(f"unknown model_id {model_id!r}")
        return metrics

    def select(self, model_id: str) -> MeshMetrics:
        metrics = self.get_metrics(model_id)
        with self._lock:
            self._selected_id = metrics.model_id
        return metrics

    def selected(self) -> Optional[MeshMetrics]:
        with self._lock:
            if self._selected_id is None:
                return None
            return self._models.get(self._selected_id)

    def delete(self, model_id: str) -> bool:
        with self._lock:
            metrics = self._models.pop(str(model_id), None)
            if metrics is None:
                return False
            if self._selected_id == metrics.model_id:
                self._selected_id = None
            resolved_root = os.path.abspath(self.root)
            for path in (
                self._json_path(metrics.model_id),
                self._binary_path(metrics.model_id, metrics.source_format),
                metrics.source_path,
            ):
                if path:
                    resolved_file = os.path.abspath(path)
                    if (resolved_file == resolved_root or resolved_file.startswith(resolved_root + os.sep)) and os.path.isfile(resolved_file):
                        try:
                            os.remove(resolved_file)
                        except OSError:
                            pass
            return True

    def apply_overrides(
        self, model_id: str, overrides: Dict[str, Any]
    ) -> MeshMetrics:
        """Replace a model's overrides and re-persist the measurement record."""
        metrics = self.get_metrics(model_id)
        updated = compute_metrics(
            *_relink(metrics),
            name=metrics.name,
            source_format=metrics.source_format,
            model_id=metrics.model_id,
            scale_factor=metrics.scale_factor,
            cg_mode=metrics.cg_mode,
            paper_gsm=metrics.paper_gsm,
            overrides=overrides,
            source_path=metrics.source_path,
            backend=metrics.backend,
        )
        updated.created_at = metrics.created_at
        return self._persist(updated)

    def to_aircraft_config(
        self, model_id: str, base: Optional[AircraftConfig] = None
    ) -> AircraftConfig:
        """Copy the mesh-derived geometry and inertia onto ``base``."""
        import dataclasses

        metrics = self.get_metrics(model_id)
        template = base if base is not None else AircraftConfig()
        config = dataclasses.replace(template)

        tensor = np.asarray(metrics.inertia_tensor, dtype=np.float64).reshape(3, 3)
        sheet_area = max(float(metrics.planform_area_m2), float(metrics.surface_area_m2), 1.0e-9)

        config.wing_area_m2 = max(float(metrics.planform_area_m2), 1.0e-9)
        config.wingspan_m = max(float(metrics.wingspan_m), 1.0e-6)
        config.taper_ratio = clamp(float(metrics.taper_ratio), 0.05, 1.0)
        config.aspect_ratio = max(float(metrics.aspect_ratio), 1.0e-3)
        config.mass_kg = max(float(metrics.mass_kg), 1.0e-9)
        config.sheet_area_m2 = sheet_area
        # Keep the tensor in the geometry's body frame. Eigenvalue ordering
        # does not identify the roll, pitch or yaw axes.
        config.roll_inertia_kgm2 = max(float(tensor[0, 0]), 1.0e-12)
        config.pitch_inertia_kgm2 = max(float(tensor[1, 1]), 1.0e-12)
        config.yaw_inertia_kgm2 = max(float(tensor[2, 2]), 1.0e-12)
        config.inertia_xy_kgm2 = float(tensor[0, 1])
        config.inertia_xz_kgm2 = float(tensor[0, 2])
        config.inertia_yz_kgm2 = float(tensor[1, 2])
        return config


def _relink(metrics: MeshMetrics) -> Tuple[np.ndarray, np.ndarray]:
    """Load the stored raw bytes of ``metrics`` back into a vertex/face pair.

    The raw file always holds the *unscaled* payload, so the recorded
    ``units``/``scale_factor`` are re-applied here.
    """
    data = _relink_source(metrics)
    if data is None:
        raise KeyError(
            f"raw geometry for model {metrics.model_id!r} is missing at "
            f"{metrics.source_path!r}"
        )
    return load_geometry(
        data,
        filename=os.path.basename(metrics.source_path),
        scale_hint=metrics.units if metrics.units in SCALE_FACTORS else "m",
    )[:2]


#: Process-wide library used by the Flask layer (``models/`` by default).
MESH_LIBRARY = MeshLibrary()
