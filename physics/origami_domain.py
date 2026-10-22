"""Procedural origami crease-pattern folding engine.

This module turns a single sheet of paper (A4, 1:sqrt(2)) into a folded
paper-airplane mesh by *actually* simulating the fold programme:

* the sheet is cut into convex faces along the crease pattern,
* every crease becomes a node of a hinge tree,
* the hinge tree is evaluated as a chain of rigid transforms
  ``T_child = T_parent * Rot(crease_line, phi)``,
* multi-layer stacks (the folded nose) are separated along the local normal
  by an explicit *layer pitch* so coplanar layers can never z-fight,
* every face is emitted as a solid slab (top skin, bottom skin and an edge
  strip) so the paper cross section has a real, visible thickness,
* creases are closed by a rolled-fold arc, which gives the crease line its
  normal gradient (highlight + cavity shading).

The module is pure geometry: it never imports the flight dynamics, so the
existing 6DoF / aero-elastic behaviour is untouched.

Coordinate conventions
----------------------
Sheet space is the normalised unit square ``(u, v)`` with ``u`` across the
short edge and ``v`` along the long edge (the nose sits at ``v = 1``).
Sheet space maps into three-space as::

    x = (v - 0.5) * SHEET_LENGTH_M   (fore/aft, +x is the nose)
    y = 0                            (up)
    z = (u - 0.5) * SHEET_WIDTH_M    (spanwise, +z is the right wing)

so the sheet starts out lying flat in the horizontal plane and every fold is
a rotation about an axis that lies in that plane.  The frame is right handed
and therefore *not* the NED simulator frame; the renderer applies the
``Rx(+90 deg)`` mapping documented in ``static/js/viewport.js``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

Vec2 = Tuple[float, float]
Vec3 = Tuple[float, float, float]
Mat3 = Tuple[Vec3, Vec3, Vec3]

# --------------------------------------------------------------------------
# Physical / visual constants
# --------------------------------------------------------------------------

#: Real thickness of a sheet of office paper (metres).
PAPER_THICKNESS_M: float = 1.0e-4

#: Thickness used for the rendered solid.  Real paper is invisible at the
#: working camera distance, so the solid is amplified by a constant factor
#: that keeps it inside the 0.1 mm .. 0.3 mm band required by the spec.
VISUAL_THICKNESS_M: float = 3.0e-4

#: Air gap kept between two stacked sheets of paper.
LAYER_AIR_GAP_M: float = 1.2e-4

#: Centre-to-centre distance of two consecutive layers of a stack.
LAYER_PITCH_M: float = VISUAL_THICKNESS_M + LAYER_AIR_GAP_M

#: Half angle of the residual V shaped gap between the two keel walls.
KEEL_V_GAP_DEG: float = 3.0

#: Residual crease angle kept by a centre reference crease that was folded
#: and opened again (paper never lies perfectly flat afterwards).
RESIDUAL_CREASE_DEG: float = 0.6

#: Physical A4 sheet size (metres).
SHEET_WIDTH_M: float = 0.210
SHEET_LENGTH_M: float = 0.297

#: Number of segments used to tessellate a rolled crease fold.
ROLL_SAMPLES: int = 5

#: Angle between the two panel directions of a crease below which the rolled
#: crease is declared degenerate.
#:
#: A rolled crease is closed by the cubic Bezier that leaves the parent panel
#: tangentially and enters the child panel tangentially.  Its two inner
#: control points sit on the intersection ``v`` of the two panel directions,
#: so ``v`` must stay finite and close to the crease.  When the two panels
#: are nearly parallel the intersection runs off to an undefined distance and
#: the Bezier degenerates into a spike that reaches metres in front of the
#: nose.  The rolled crease first diverges at an angle of 1e-8 rad, so the
#: degeneracy test has to reject *at least* that angle; the threshold below
#: is 1e-3 rad (0.057 deg), five orders of magnitude coarser and still far
#: below the angle of any crease that carries paper.
CREASE_DEGENERATE_ANGLE_RAD: float = 1.0e-3

#: Degeneracy threshold of the crease intersection, expressed the way
#: :func:`_line_intersection_3d` measures it: ``denom = |d1 x d2|^2`` is the
#: square of the sine of the angle between the two panel directions, so
#: ``denom = 1e-6`` corresponds to the 1e-3 rad of
#: :data:`CREASE_DEGENERATE_ANGLE_RAD`.
CREASE_DEGENERATE_DENOM: float = 1.0e-6

#: Amplitude of the bulge a folded paper packet develops between its creases.
#: A stack of folded sheets is never flat: the layers bow apart towards the
#: middle of the panel, which is what gives a folded nose its soft pocket.
PACKET_CAMBER_M: float = 2.0e-4

#: Distance over which the packet grows from the crease line to full camber.
CAMBER_SPAN_M: float = 0.012

#: Parallelism tolerance used when deciding that two faces are coplanar.
PARALLEL_EPS: float = 0.995

#: Depth tolerance (metres) used when ordering two stacked faces.
DEPTH_EPS: float = 2.0e-6

#: Archetype identifiers.
ARCHETYPE_DART = "classic_dart"
ARCHETYPE_NAKAMURA = "nakamura_lock"
ARCHETYPE_SKY_KING = "sky_king"

ARCHETYPE_LABELS: Dict[str, str] = {
    ARCHETYPE_DART: "Classic Dart / やり飛行機",
    ARCHETYPE_NAKAMURA: "Nakamura Lock / へそ飛行機",
    ARCHETYPE_SKY_KING: "Sky King / 競技用グライダー",
}

#: Mapping from the legacy ``shape_flag`` to an origami archetype.
SHAPE_FLAG_ARCHETYPE: Dict[int, str] = {
    0: ARCHETYPE_DART,
    1: ARCHETYPE_NAKAMURA,
    2: ARCHETYPE_SKY_KING,
}

#: Reverse mapping of :data:`SHAPE_FLAG_ARCHETYPE`, so the catalogue does not
#: have to search the mapping at every call (§P3-11).
ARCHETYPE_SHAPE_FLAG: Dict[str, int] = {
    archetype: flag for flag, archetype in SHAPE_FLAG_ARCHETYPE.items()
}

ARCHETYPES: Tuple[str, ...] = (ARCHETYPE_DART, ARCHETYPE_NAKAMURA, ARCHETYPE_SKY_KING)


# --------------------------------------------------------------------------
# Small vector helpers
# --------------------------------------------------------------------------


def _sub2(a: Vec2, b: Vec2) -> Vec2:
    return (a[0] - b[0], a[1] - b[1])


def _cross2(a: Vec2, b: Vec2) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _len2(a: Vec2) -> float:
    return math.hypot(a[0], a[1])


def _unit2(a: Vec2) -> Vec2:
    n = _len2(a)
    if n <= 1e-12:
        return (0.0, 0.0)
    return (a[0] / n, a[1] / n)


def _add3(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub3(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale3(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot3(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross3(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _len3(a: Vec3) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def _unit3(v: Vec3) -> Vec3:
    n = _len3(v)
    if n <= 1e-12:
        return (0.0, 0.0, 1.0)
    return (v[0] / n, v[1] / n, v[2] / n)


def _finite3(v: Vec3) -> bool:
    """True when every component of ``v`` is a finite number."""
    return math.isfinite(v[0]) and math.isfinite(v[1]) and math.isfinite(v[2])


def _mat_mul(a: Mat3, b: Mat3) -> Mat3:
    return (
        (
            a[0][0] * b[0][0] + a[0][1] * b[1][0] + a[0][2] * b[2][0],
            a[0][0] * b[0][1] + a[0][1] * b[1][1] + a[0][2] * b[2][1],
            a[0][0] * b[0][2] + a[0][1] * b[1][2] + a[0][2] * b[2][2],
        ),
        (
            a[1][0] * b[0][0] + a[1][1] * b[1][0] + a[1][2] * b[2][0],
            a[1][0] * b[0][1] + a[1][1] * b[1][1] + a[1][2] * b[2][1],
            a[1][0] * b[0][2] + a[1][1] * b[1][2] + a[1][2] * b[2][2],
        ),
        (
            a[2][0] * b[0][0] + a[2][1] * b[1][0] + a[2][2] * b[2][0],
            a[2][0] * b[0][1] + a[2][1] * b[1][1] + a[2][2] * b[2][1],
            a[2][0] * b[0][2] + a[2][1] * b[1][2] + a[2][2] * b[2][2],
        ),
    )


def _mat_apply(m: Mat3, v: Vec3) -> Vec3:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def _mat_identity() -> Mat3:
    return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _rot_axis_angle(axis: Vec3, angle: float) -> Mat3:
    """Rodrigues rotation matrix about a unit ``axis``."""
    x, y, z = axis
    c = math.cos(angle)
    s = math.sin(angle)
    t = 1.0 - c
    return (
        (t * x * x + c, t * x * y - s * z, t * x * z + s * y),
        (t * x * y + s * z, t * y * y + c, t * y * z - s * x),
        (t * x * z - s * y, t * y * z + s * x, t * z * z + c),
    )


def _sheet_to_3d(u: float, v: float) -> Vec3:
    return ((v - 0.5) * SHEET_LENGTH_M, 0.0, (u - 0.5) * SHEET_WIDTH_M)


def _polygon_area(poly: Sequence[Vec2]) -> float:
    total = 0.0
    count = len(poly)
    for i in range(count):
        a = poly[i]
        b = poly[(i + 1) % count]
        total += _cross2(a, b)
    return 0.5 * total


def _clip_halfplane(
    poly: Sequence[Vec2], p0: Vec2, p1: Vec2, keep_positive: bool
) -> List[Vec2]:
    """Clip a convex polygon against the infinite line ``p0 -> p1``."""
    if not poly:
        return []
    direction = _sub2(p1, p0)
    out: List[Vec2] = []
    count = len(poly)
    for i in range(count):
        a = poly[i]
        b = poly[(i + 1) % count]
        da = _cross2(direction, _sub2(a, p0))
        db = _cross2(direction, _sub2(b, p0))
        in_a = da >= 0.0 if keep_positive else da <= 0.0
        in_b = db >= 0.0 if keep_positive else db <= 0.0
        if in_a:
            out.append(a)
        if in_a != in_b:
            denom = da - db
            if abs(denom) > 1e-15:
                t = da / denom
                out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def _dot2(a: Vec2, b: Vec2) -> float:
    return a[0] * b[0] + a[1] * b[1]


@dataclass
class Crease:
    """A single crease line of the flat pattern.

    ``side`` selects which half plane moves:

    * ``+1`` the left half plane (``cross2(dir, p - p0) > 0``) moves,
    * ``-1`` the right half plane moves,
    * ``0``  the crease is symmetric: both halves move by half the angle
      (this is how the centre keel fold keeps a natural V shaped gap).

    ``up`` states whether the moving half is rotated towards ``+y`` (the
    "valley" direction for a sheet lying flat on the table).
    """

    key: str
    p0: Vec2
    p1: Vec2
    angle_deg: float
    side: int = 1
    up: bool = True
    level: int = 0
    groups: Optional[Tuple[str, ...]] = None
    set_group: Optional[str] = None
    set_group_pair: Optional[Tuple[str, str]] = None
    residual_deg: float = 0.0


@dataclass
class CreaseLink:
    """A shared edge between two faces, produced by cutting along a crease."""

    key: str
    a: Vec3
    b: Vec3
    neighbour_fid: int = -1


@dataclass
class Face:
    """A convex piece of the sheet in its current three-space shape.

    ``rot`` / ``trans`` hold the placement of the piece.  The fold programme
    is evaluated sequentially in three-space: every crease cuts the pieces it
    crosses along the *current* position of its crease line and rotates the
    moving half about that line.  Because the line is shared by all crossed
    layers, a folded stack creases as one straight line.
    """

    fid: int
    poly3d: List[Vec3]
    rot: Mat3 = field(default_factory=_mat_identity)
    trans: Vec3 = (0.0, 0.0, 0.0)
    parent: Optional[int] = None
    group: str = "base"
    level: int = 0
    role: str = "panel"
    depth: int = 0
    links: List[CreaseLink] = field(default_factory=list)


@dataclass
class HingeNode:
    """A node of the hinge tree produced by the crease pattern compiler."""

    nid: int
    parent: Optional[int]
    p0: Vec2
    p1: Vec2
    phi_deg: float
    level: int
    name: str


@dataclass
class CreasePattern:
    """A compiled flat pattern: convex faces plus the hinge tree."""

    key: str
    label: str
    faces: List[Face]
    nodes: List[HingeNode]
    creases: List[Crease]

    def face_by_id(self, fid: int) -> Optional[Face]:
        for face in self.faces:
            if face.fid == fid:
                return face
        return None


# --------------------------------------------------------------------------
# Pattern compiler
# --------------------------------------------------------------------------


def _crease_frame(face: Face, crease: Crease) -> Tuple[Vec3, Vec3, Vec3]:
    """Current origin, axis and side normal of a crease line.

    The side normal points towards the flat "left" half plane of the crease
    (``cross2(dir, p - p0) > 0``) expressed in the current three-space.
    """
    origin = _map_flat(face, crease.p0[0], crease.p0[1])
    other = _map_flat(face, crease.p1[0], crease.p1[1])
    axis = _unit3((other[0] - origin[0], other[1] - origin[1], other[2] - origin[2]))
    du = crease.p1[0] - crease.p0[0]
    dv = crease.p1[1] - crease.p0[1]
    norm = math.hypot(du, dv)
    if norm <= 1e-12:
        du, dv, norm = 0.0, 1.0, 1.0
    du /= norm
    dv /= norm
    # The sheet is not square, so the flat (u, v) normal is *not* orthogonal
    # to the crease in three-space.  The side normal is therefore rebuilt in
    # the plane of the sheet and only its sign is taken from the flat
    # pattern.
    plane_normal = _unit3(_mat_apply(face.rot, (0.0, 1.0, 0.0)))
    side = _unit3(_cross3(plane_normal, axis))
    flat_left = _mat_apply(face.rot, (du * SHEET_LENGTH_M, 0.0, -dv * SHEET_WIDTH_M))
    if _dot3(side, flat_left) < 0.0:
        side = _scale3(side, -1.0)
    return origin, axis, side


def _rotate_polygon(
    origin: Vec3, axis: Vec3, phi_deg: float, poly: Sequence[Vec3]
) -> List[Vec3]:
    """Rotate a world space polygon about a line."""
    rot = _rot_axis_angle(axis, math.radians(phi_deg))
    out: List[Vec3] = []
    for p in poly:
        d = (p[0] - origin[0], p[1] - origin[1], p[2] - origin[2])
        r = _mat_apply(rot, d)
        out.append((origin[0] + r[0], origin[1] + r[1], origin[2] + r[2]))
    return out


def _apply_fold(
    face: Face, origin: Vec3, axis: Vec3, phi_deg: float
) -> Tuple[Mat3, Vec3]:
    """Rotate ``face`` about a crease line given in world space."""
    rot = _rot_axis_angle(axis, math.radians(phi_deg))
    trans = (
        origin[0] - rot[0][0] * origin[0] - rot[0][1] * origin[1] - rot[0][2] * origin[2],
        origin[1] - rot[1][0] * origin[0] - rot[1][1] * origin[1] - rot[1][2] * origin[2],
        origin[2] - rot[2][0] * origin[0] - rot[2][1] * origin[1] - rot[2][2] * origin[2],
    )
    world_rot = _mat_mul(rot, face.rot)
    world_trans = (
        rot[0][0] * face.trans[0] + rot[0][1] * face.trans[1] + rot[0][2] * face.trans[2] + trans[0],
        rot[1][0] * face.trans[0] + rot[1][1] * face.trans[1] + rot[1][2] * face.trans[2] + trans[1],
        rot[2][0] * face.trans[0] + rot[2][1] * face.trans[1] + rot[2][2] * face.trans[2] + trans[2],
    )
    return world_rot, world_trans


def _map_flat(face: Face, u: float, v: float) -> Vec3:
    """Map a flat pattern point into the current three-space of a face."""
    local = _sheet_to_3d(u, v)
    r = _mat_apply(face.rot, local)
    return (r[0] + face.trans[0], r[1] + face.trans[1], r[2] + face.trans[2])


def _split_polygon_3d(
    poly: Sequence[Vec3], origin: Vec3, axis: Vec3, side: Vec3
) -> Tuple[List[Vec3], List[Vec3], Optional[Tuple[Vec3, Vec3]]]:
    """Cut a planar polygon with a world space line.

    Returns the two half polygons (the ``side`` positive one first) and the
    shared edge, or empty lists when the line does not cut the polygon.
    """
    if len(poly) < 3:
        return [], [], None
    e1 = _unit3(_cross3(axis, side))
    e2 = _unit3(_cross3(e1, axis))
    points = [_dot3(_sub3(p, origin), e2) for p in poly]
    if all(value > 1e-12 for value in points) or all(value < -1e-12 for value in points):
        return [], [], None
    plus: List[Vec3] = []
    minus: List[Vec3] = []
    count = len(poly)
    crossings: List[Tuple[float, Vec3]] = []
    for i in range(count):
        a = poly[i]
        b = poly[(i + 1) % count]
        da = points[i]
        db = points[(i + 1) % count]
        if da >= 0.0:
            plus.append(a)
        if da <= 0.0:
            minus.append(a)
        if (da > 0.0) != (db > 0.0) and abs(da - db) > 1e-15:
            t = da / (da - db)
            point = (
                a[0] + (b[0] - a[0]) * t,
                a[1] + (b[1] - a[1]) * t,
                a[2] + (b[2] - a[2]) * t,
            )
            plus.append(point)
            minus.append(point)
            crossings.append((_dot3(_sub3(point, origin), e1), point))
    if len(plus) < 3 or len(minus) < 3:
        return [], [], None
    if len(crossings) < 2:
        return [], [], None
    crossings.sort(key=lambda item: item[0])
    shared = (crossings[0][1], crossings[-1][1])
    return plus, minus, shared


def _compile_pattern(
    key: str,
    label: str,
    sheet: List[Vec2],
    creases: List[Crease],
    alpha: float = 1.0,
) -> CreasePattern:
    """Cut the sheet along every crease and evaluate the fold programme.

    The creases are listed in the order in which they are physically folded.
    Every crease cuts the pieces it crosses along the current position of its
    crease line and rotates the moving half about that line, so the compiled
    pattern is a faithful simulation of the folding sequence.
    """
    sheet3d = [_sheet_to_3d(u, v) for u, v in sheet]
    faces: List[Face] = [
        Face(fid=0, poly3d=list(sheet3d), parent=None, group="base", level=0, role="sheet")
    ]
    nodes: List[HingeNode] = []
    next_fid = 1
    next_nid = 0

    for crease in creases:
        # The reference piece owns the crease: it is the least folded piece
        # that is really cut by the crease line.  Its copy of the line is the
        # physical fold line that every crossed layer creases along.
        reference: Optional[Face] = None
        reference_key: Tuple[int, float] = (0, 0.0)
        for face in faces:
            if crease.groups is not None and face.group not in crease.groups:
                continue
            own_origin, own_axis, own_side = _crease_frame(face, crease)
            own_plus, own_minus, own_shared = _split_polygon_3d(
                face.poly3d, own_origin, own_axis, own_side
            )
            if not own_plus or not own_minus or own_shared is None:
                continue
            rank = (face.depth, -_poly3d_area(face.poly3d))
            if reference is None or rank < reference_key:
                reference_key = rank
                reference = face
        if reference is None:
            continue
        origin, axis, side = _crease_frame(reference, crease)

        produced: List[Face] = []
        for face in faces:
            if crease.groups is not None and face.group not in crease.groups:
                produced.append(face)
                continue
            plus, minus, shared = _split_polygon_3d(face.poly3d, origin, axis, side)
            if not plus or not minus or shared is None:
                produced.append(face)
                continue
            moved_group = crease.set_group or face.group
            if crease.side == 0:
                # Symmetric crease: both halves move, each by half the angle.
                for part, is_plus in ((plus, True), (minus, False)):
                    if crease.set_group_pair is not None:
                        moved_group = crease.set_group_pair[0 if is_plus else 1]
                    phi = _symmetric_phi(crease, is_plus) * alpha
                    rot, trans = _apply_fold(face, origin, axis, phi)
                    node = HingeNode(
                        nid=next_nid,
                        parent=face.parent,
                        p0=crease.p0,
                        p1=crease.p1,
                        phi_deg=phi,
                        level=crease.level,
                        name=crease.key,
                    )
                    next_nid += 1
                    nodes.append(node)
                    produced.append(
                        Face(
                            fid=next_fid,
                            poly3d=_rotate_polygon(origin, axis, phi, part),
                            rot=rot,
                            trans=trans,
                            parent=node.nid,
                            group=moved_group,
                            level=max(face.level, crease.level),
                            role=crease.key,
                            depth=face.depth + 1,
                        )
                    )
                    next_fid += 1
            else:
                moving = plus if crease.side > 0 else minus
                static = minus if crease.side > 0 else plus
                phi = _signed_phi(crease) * alpha
                rot, trans = _apply_fold(face, origin, axis, phi)
                node = HingeNode(
                    nid=next_nid,
                    parent=face.parent,
                    p0=crease.p0,
                    p1=crease.p1,
                    phi_deg=phi,
                    level=crease.level,
                    name=crease.key,
                )
                next_nid += 1
                nodes.append(node)
                child = Face(
                    fid=next_fid,
                    poly3d=_rotate_polygon(origin, axis, phi, moving),
                    rot=rot,
                    trans=trans,
                    parent=node.nid,
                    group=moved_group,
                    level=max(face.level, crease.level),
                    role=crease.key,
                    depth=face.depth + 1,
                )
                next_fid += 1
                produced.append(child)
                parent = Face(
                    fid=next_fid,
                    poly3d=list(static),
                    rot=face.rot,
                    trans=face.trans,
                    parent=face.parent,
                    group=face.group,
                    level=face.level,
                    role=face.role,
                    links=list(face.links),
                )
                next_fid += 1
                produced.append(parent)
                # Record the shared edge on both halves so the mesh builder can
                # close the crease with a rolled fold.
                child.links.append(CreaseLink(crease.key, shared[0], shared[1], parent.fid))
                parent.links.append(CreaseLink(crease.key, shared[0], shared[1], child.fid))
        faces = produced

    return CreasePattern(key=key, label=label, faces=faces, nodes=nodes, creases=creases)


def fold_sequence(pattern: CreasePattern) -> List[Dict[str, Any]]:
    """Return the ordered fold programme (the hinge tree) of a pattern.

    Every hinge the pattern compiler created is reported in the order it was
    folded: the crease it belongs to, the signed rotation it applied at the
    given fold progression and its depth in the hinge tree.  This is the
    hinge-tree algorithm the pattern docstring refers to; it makes the
    compiled programme observable instead of leaving it unread (§P3-11).
    """
    return [
        {
            "nid": node.nid,
            "parent": node.parent,
            "crease": node.name,
            "phi_deg": node.phi_deg,
            "level": node.level,
        }
        for node in pattern.nodes
    ]


def _signed_phi(crease: Crease) -> float:
    """Signed rotation angle (degrees) applied to the moving half plane."""
    magnitude = crease.angle_deg
    if crease.side > 0:
        return magnitude if crease.up else -magnitude
    return -magnitude if crease.up else magnitude


def _symmetric_phi(crease: Crease, is_left: bool) -> float:
    """Signed rotation of one half of a symmetric crease."""
    half = crease.angle_deg * 0.5
    if is_left:
        return half if crease.up else -half
    return -half if crease.up else half


# --------------------------------------------------------------------------
# Folding
# --------------------------------------------------------------------------


@dataclass
class FoldedFace:
    """A face of the sheet after the hinge tree has been evaluated."""

    fid: int
    poly3d: List[Vec3]
    normal: Vec3
    front: Vec3
    level: int
    group: str
    role: str
    parent: Optional[int]
    centroid3d: Vec3
    area_m2: float


def _fold_faces(pattern: CreasePattern) -> List[FoldedFace]:
    """Describe every compiled face for the layer assignment."""
    folded: List[FoldedFace] = []
    for face in pattern.faces:
        normal = _face_normal(face.poly3d)
        front = _unit3(_mat_apply(face.rot, (0.0, 1.0, 0.0)))
        folded.append(
            FoldedFace(
                fid=face.fid,
                poly3d=list(face.poly3d),
                normal=normal,
                front=front,
                level=face.level,
                group=face.group,
                role=face.role,
                parent=face.parent,
                centroid3d=_poly3d_centroid(face.poly3d),
                area_m2=_poly3d_area(face.poly3d),
            )
        )
    return folded


def _poly3d_centroid(poly: Sequence[Vec3]) -> Vec3:
    count = len(poly)
    cx = sum(p[0] for p in poly) / count
    cy = sum(p[1] for p in poly) / count
    cz = sum(p[2] for p in poly) / count
    return (cx, cy, cz)


def _poly3d_area(poly: Sequence[Vec3]) -> float:
    """Area of a planar polygon (Newell)."""
    nx = 0.0
    ny = 0.0
    nz = 0.0
    count = len(poly)
    for i in range(count):
        a = poly[i]
        b = poly[(i + 1) % count]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    return 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)


def _face_normal(poly: Sequence[Vec3]) -> Vec3:
    """Newell normal of a planar polygon (right-hand winding)."""
    nx = 0.0
    ny = 0.0
    nz = 0.0
    count = len(poly)
    for i in range(count):
        a = poly[i]
        b = poly[(i + 1) % count]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    return _unit3((nx, ny, nz))


# --------------------------------------------------------------------------
# Layer assignment (the Z-fighting proof lives here)
# --------------------------------------------------------------------------


def _project_polygon(
    poly: Sequence[Vec3], normal: Vec3, flip: bool = False
) -> List[Vec2]:
    """Project a planar polygon onto the plane perpendicular to ``normal``."""
    n = _unit3(normal)
    helper = (0.0, 0.0, 1.0) if abs(n[2]) < 0.9 else (1.0, 0.0, 0.0)
    e1 = _unit3(_cross3(n, helper))
    e2 = _cross3(n, e1)
    sign = -1.0 if flip else 1.0
    return [(_dot3(p, e1), sign * _dot3(p, e2)) for p in poly]


def _convex_overlap(a: Sequence[Vec2], b: Sequence[Vec2]) -> bool:
    """Separating-axis overlap test for two convex polygons."""
    if len(a) < 3 or len(b) < 3:
        return False
    for poly in (a, b):
        count = len(poly)
        for i in range(count):
            p0 = poly[i]
            p1 = poly[(i + 1) % count]
            axis = (-(p1[1] - p0[1]), p1[0] - p0[0])
            norm = math.hypot(axis[0], axis[1])
            if norm <= 1e-12:
                continue
            axis = (axis[0] / norm, axis[1] / norm)
            a_lo = min(_dot2(p, axis) for p in a)
            a_hi = max(_dot2(p, axis) for p in a)
            b_lo = min(_dot2(p, axis) for p in b)
            b_hi = max(_dot2(p, axis) for p in b)
            if a_hi < b_lo - 1e-9 or b_hi < a_lo - 1e-9:
                return False
    return True


@dataclass
class LayerAssignment:
    """Stack index of every folded face plus the reference normals."""

    index: Dict[int, int]
    reference: Dict[int, Vec3]
    max_layers: int


def assign_layers(
    faces: Sequence[FoldedFace],
    group_order: Optional[Dict[str, int]] = None,
) -> LayerAssignment:
    """Order the folded faces into a stack and derive their layer index.

    Two faces can only fight for the depth buffer when they are parallel and
    overlap in projection.  Those faces form a *cluster*.  Inside a cluster
    the faces are ordered by their signed depth along the cluster reference
    normal; the layer index is the length of the longest path in the
    resulting DAG, which guarantees that any two overlapping faces are
    separated by at least one full layer pitch.

    ``group_order`` is an optional hint: the panels of a group with a higher
    rank are stacked *below* the panels of a group with a lower rank when
    they are coplanar.  It is how a fold programme that says "fold this
    panel on top" overrides the default tie break without breaking the
    separation guarantee.
    """
    count = len(faces)
    index: Dict[int, int] = {face.fid: 0 for face in faces}
    reference: Dict[int, Vec3] = {face.fid: face.normal for face in faces}
    if count == 0:
        return LayerAssignment(index=index, reference=reference, max_layers=0)

    def rank(face: FoldedFace) -> int:
        if not group_order:
            return 0
        return int(group_order.get(face.group, 0))

    # 1. cluster the parallel, overlapping faces.
    parent = list(range(count))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(count):
        for j in range(i + 1, count):
            fi = faces[i]
            fj = faces[j]
            if abs(_dot3(fi.normal, fj.normal)) < PARALLEL_EPS:
                continue
            union(i, j)

    clusters: Dict[int, List[int]] = {}
    for i in range(count):
        clusters.setdefault(find(i), []).append(i)

    # 2. inside each cluster pick a reference normal and order the faces.
    for members in clusters.values():
        if len(members) == 1:
            i = members[0]
            reference[faces[i].fid] = faces[i].normal
            continue
        lead = max(members, key=lambda i: faces[i].area_m2)
        ref = faces[lead].normal
        for i in members:
            reference[faces[i].fid] = ref
        projections: Dict[int, List[Vec2]] = {}
        for i in members:
            projections[i] = _project_polygon(faces[i].poly3d, ref)
        depths: Dict[int, float] = {}
        for i in members:
            face = faces[i]
            sign = 1.0 if _dot3(face.normal, ref) >= 0.0 else -1.0
            depths[i] = sign * _dot3(_sub3(face.centroid3d, faces[lead].centroid3d), ref)

        # 3. build the "A is below B" DAG.
        overlaps: Dict[Tuple[int, int], bool] = {}
        for a in members:
            for b in members:
                if a == b:
                    continue
                overlaps[(a, b)] = _convex_overlap(projections[a], projections[b])
        edges: Dict[int, List[int]] = {i: [] for i in members}
        indegree: Dict[int, int] = {i: 0 for i in members}
        for a in members:
            for b in members:
                if a == b:
                    continue
                da = depths[a]
                db = depths[b]
                below = False
                if not overlaps.get((a, b), False):
                    continue
                if db > da + DEPTH_EPS:
                    below = True
                elif abs(db - da) <= DEPTH_EPS:
                    # Coplanar layers (a flat fold): break the tie with the
                    # fold level, the stack order hint and the face id so the
                    # order is deterministic.
                    fa, fb = faces[a], faces[b]
                    if fa.level < fb.level or (
                        fa.level == fb.level
                        and (rank(fa), fa.fid) < (rank(fb), fb.fid)
                    ):
                        below = True
                if below:
                    edges[a].append(b)
                    indegree[b] += 1

        # 4. longest path -> layer index.
        queue = [i for i in members if indegree[i] == 0]
        order: List[int] = []
        while queue:
            node = queue.pop()
            order.append(node)
            for nxt in edges[node]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)
        if len(order) != len(members):
            # Cyclic fallback: order strictly by depth then level then id.
            order = sorted(members, key=lambda i: (depths[i], faces[i].level, faces[i].fid))

        longest: Dict[int, int] = {}
        for node in order:
            value = 0
            for prev in members:
                if node in edges[prev]:
                    value = max(value, longest.get(prev, 0) + 1)
            longest[node] = value
            index[faces[node].fid] = value

    max_layers = max(index.values()) + 1 if index else 0
    return LayerAssignment(index=index, reference=reference, max_layers=max_layers)


# --------------------------------------------------------------------------
# Mesh construction
# --------------------------------------------------------------------------


@dataclass
class OrigamiMesh:
    """A renderable origami mesh with per-vertex shading data.

    The mesh is a **double-sided sheet of finite thickness**: every folded
    face contributes an upper and a lower skin, the creases are closed by
    rolled strips and the cut edges by rectangular walls.  It is therefore
    not a single closed 2-manifold -- the walls are not vertex welded to the
    skins -- but every triangle is wound consistently with the surface it
    belongs to, so the shading never turns inside out (§P2-18).
    """

    archetype: str
    alpha: float
    positions: List[Vec3] = field(default_factory=list)
    normals: List[Vec3] = field(default_factory=list)
    uvs: List[Vec2] = field(default_factory=list)
    indices: List[Tuple[int, int, int]] = field(default_factory=list)
    layer_counts: List[float] = field(default_factory=list)
    crease_weights: List[float] = field(default_factory=list)
    elastic_weights: List[float] = field(default_factory=list)
    twist_weights: List[float] = field(default_factory=list)
    paper_sides: List[float] = field(default_factory=list)
    face_ids: List[int] = field(default_factory=list)
    #: The ordered fold programme (the hinge tree) of this mesh: one entry
    #: per hinge, in the order the creases were folded (§P3-11).
    folds: List[Dict[str, Any]] = field(default_factory=list)
    layer_pitch_m: float = LAYER_PITCH_M
    paper_thickness_m: float = VISUAL_THICKNESS_M
    max_layers: int = 1
    scale: float = 1.0

    @property
    def vertex_count(self) -> int:
        return len(self.positions)

    @property
    def triangle_count(self) -> int:
        return len(self.indices)

    def bounds(self) -> Tuple[Vec3, Vec3]:
        if not self.positions:
            return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        lo = list(self.positions[0])
        hi = list(self.positions[0])
        for p in self.positions:
            for axis in range(3):
                if p[axis] < lo[axis]:
                    lo[axis] = p[axis]
                if p[axis] > hi[axis]:
                    hi[axis] = p[axis]
        return (tuple(lo), tuple(hi))  # type: ignore[return-value]

    def surface_area(self) -> float:
        total = 0.0
        for a, b, c in self.indices:
            p0 = self.positions[a]
            p1 = self.positions[b]
            p2 = self.positions[c]
            e1 = _sub3(p1, p0)
            e2 = _sub3(p2, p0)
            total += 0.5 * _len3(_cross3(e1, e2))
        return total


class _MeshBuilder:
    """Accumulates vertices / normals / uv / indices for the origami mesh."""

    def __init__(self) -> None:
        self.positions: List[Vec3] = []
        self.normals: List[Vec3] = []
        self.uvs: List[Vec2] = []
        self.indices: List[Tuple[int, int, int]] = []
        self.layer_counts: List[float] = []
        self.crease_weights: List[float] = []
        self.elastic_weights: List[float] = []
        self.twist_weights: List[float] = []
        self.paper_sides: List[float] = []
        self.face_ids: List[int] = []

    def vertex(
        self,
        position: Vec3,
        normal: Vec3,
        uv: Vec2,
        layer: float,
        crease: float,
        elastic: float,
        twist: float,
        side: float,
        face_id: int,
    ) -> int:
        index = len(self.positions)
        self.positions.append(position)
        self.normals.append(normal)
        self.uvs.append(uv)
        self.layer_counts.append(layer)
        self.crease_weights.append(crease)
        self.elastic_weights.append(elastic)
        self.twist_weights.append(twist)
        self.paper_sides.append(side)
        self.face_ids.append(face_id)
        return index

    def triangle(self, a: int, b: int, c: int) -> None:
        if a == b or b == c or a == c:
            return
        if _same_point3(self.positions[a], self.positions[b], 1e-12):
            return
        if _same_point3(self.positions[b], self.positions[c], 1e-12):
            return
        if _same_point3(self.positions[a], self.positions[c], 1e-12):
            return
        self.indices.append((a, b, c))

    def oriented_triangle(self, a: int, b: int, c: int, outward: Vec3) -> None:
        """Emit a triangle whose winding agrees with ``outward``.

        The skins, the crease rolls and the cut walls are stitched from
        independently computed directions, so a triangle can end up wound
        against the surface it belongs to.  Such a triangle shades as if it
        were seen from the back, which is what made a large part of the
        folded sheet render inside out (§P2-18).  Flipping the winding keeps
        the geometry untouched and the shading consistent.
        """
        if a == b or b == c or a == c:
            return
        normal = _cross3(
            _sub3(self.positions[b], self.positions[a]),
            _sub3(self.positions[c], self.positions[a]),
        )
        if _dot3(normal, outward) < 0.0:
            b, c = c, b
        self.triangle(a, b, c)


@dataclass
class _RollSample:
    """One sample of a rolled crease fold."""

    parent_point: Vec3
    child_point: Vec3
    mid: Vec3
    normal: Vec3
    plus: Vec3
    minus: Vec3


def _cubic_bezier(p0: Vec3, q1: Vec3, q2: Vec3, p3: Vec3, t: float) -> Vec3:
    u = 1.0 - t
    a = u * u * u
    b = 3.0 * u * u * t
    c = 3.0 * u * t * t
    d = t * t * t
    return (
        a * p0[0] + b * q1[0] + c * q2[0] + d * p3[0],
        a * p0[1] + b * q1[1] + c * q2[1] + d * p3[1],
        a * p0[2] + b * q1[2] + c * q2[2] + d * p3[2],
    )


def _cubic_bezier_tangent(p0: Vec3, q1: Vec3, q2: Vec3, p3: Vec3, t: float) -> Vec3:
    u = 1.0 - t
    a = 3.0 * u * u
    b = 6.0 * u * t
    c = 3.0 * t * t
    return (
        a * (q1[0] - p0[0]) + b * (q2[0] - q1[0]) + c * (p3[0] - q2[0]),
        a * (q1[1] - p0[1]) + b * (q2[1] - q1[1]) + c * (p3[1] - q2[1]),
        a * (q1[2] - p0[2]) + b * (q2[2] - q1[2]) + c * (p3[2] - q2[2]),
    )


def _line_intersection_3d(
    p: Vec3, d1: Vec3, q: Vec3, d2: Vec3
) -> Optional[Vec3]:
    """Intersection of the line ``p + s*d1`` with the line ``q + u*d2``.

    ``None`` is returned when the two lines are parallel within
    :data:`CREASE_DEGENERATE_DENOM` -- their intersection is then undefined
    and the rolled crease would spike -- and whenever the arithmetic does not
    produce a finite point.  A non-finite intersection is never handed back
    to the caller, so the crease can only fall back on the bounded control
    polygon of :func:`_roll_samples`.
    """
    cross = _cross3(d1, d2)
    denom = _dot3(cross, cross)
    if not math.isfinite(denom) or denom <= CREASE_DEGENERATE_DENOM:
        return None
    delta = _sub3(q, p)
    s = _dot3(_cross3(delta, d2), cross) / denom
    if not math.isfinite(s):
        return None
    point = (p[0] + s * d1[0], p[1] + s * d1[1], p[2] + s * d1[2])
    if not _finite3(point):
        return None
    return point


def _roll_samples(
    parent_pts: Sequence[Vec3],
    child_pts: Sequence[Vec3],
    parent_normal: Vec3,
    child_normal: Vec3,
    crease_dir: Vec3,
    thickness: float,
) -> List[_RollSample]:
    """Build the samples of a rolled crease fold.

    The mid curve is a cubic Bezier that leaves the parent panel and enters
    the child panel tangentially, so the fold is G1 continuous.  The two
    boundary curves are the mid curve offset by +/- half the paper thickness.
    """
    samples: List[_RollSample] = []
    count = len(parent_pts)
    for i in range(count):
        t = i / (count - 1) if count > 1 else 0.0
        p = parent_pts[i]
        c = child_pts[i]
        chord = _sub3(c, p)
        length = _len3(chord)
        if length <= 1e-9:
            mid = p
            normal = parent_normal
            samples.append(_RollSample(p, c, mid, normal, p, p))
            continue
        # Directions of the two panels inside the cross-section plane.
        d1 = _unit3(_cross3(crease_dir, parent_normal))
        d2 = _unit3(_cross3(crease_dir, child_normal))
        v = _line_intersection_3d(p, d1, c, d2)
        # The intersection is only a usable control point when it stays close
        # to the crease: the rolled arc is a few paper thicknesses wide, so a
        # control point further away than three of those widths would pull the
        # curve out of the paper.  Such an intersection is dropped and the
        # curve falls back on the chord between the two panels.
        if v is not None and (
            _len3(_sub3(v, p)) > 3.0 * length or _len3(_sub3(v, c)) > 3.0 * length
        ):
            v = None
        if v is not None:
            q1 = _add3(p, _scale3(_sub3(v, p), 1.0 / 3.0))
            q2 = _add3(c, _scale3(_sub3(v, c), 1.0 / 3.0))
        else:
            # Control polygon of the straight crease: the Bezier then follows
            # the chord between the two panels exactly, which is the finite
            # and physically bounded limit of a degenerate rolled crease.
            q1 = _add3(p, _scale3(chord, 1.0 / 3.0))
            q2 = _add3(p, _scale3(chord, 2.0 / 3.0))
        mid = _cubic_bezier(p, q1, q2, c, t)
        tangent = _cubic_bezier_tangent(p, q1, q2, c, t)
        normal = _cross3(crease_dir, tangent)
        if not (_finite3(mid) and _finite3(tangent) and _finite3(normal)):
            # A rolled crease must never inject a non-finite vertex into the
            # mesh: fall back on the straight crease between the two panels.
            mid = _add3(p, _scale3(chord, t))
            normal = parent_normal
        if _len3(normal) <= 1e-12:
            normal = parent_normal
        else:
            normal = _unit3(normal)
            if _dot3(normal, parent_normal) < 0.0:
                normal = _scale3(normal, -1.0)
        plus = _add3(mid, _scale3(normal, 0.5 * thickness))
        minus = _sub3(mid, _scale3(normal, 0.5 * thickness))
        samples.append(_RollSample(p, c, mid, normal, plus, minus))
    return samples


def build_mesh(
    archetype: str,
    alpha: float = 1.0,
    params: Optional[Dict[str, float]] = None,
    scale: float = 1.0,
    span_m: Optional[float] = None,
) -> OrigamiMesh:
    """Build the folded origami mesh of one archetype.

    ``alpha`` is the folding progression: ``0`` is the flat sheet, ``1`` the
    fully folded aeroplane.  ``scale`` multiplies the whole model, ``span_m``
    scales it so that the *wing span of the folded model* matches the given
    value exactly (the folded span is narrower than the sheet it was cut
    from, so scaling by the sheet width would undersize the aeroplane).
    """
    alpha = max(0.0, min(1.0, float(alpha)))
    pattern = build_pattern(archetype, params, alpha)
    return build_pattern_mesh(pattern, alpha, scale, archetype, span_m)


def compile_creases(
    creases: Sequence[Crease],
    sheet: Optional[Sequence[Vec2]] = None,
    alpha: float = 1.0,
    key: str = "program",
    label: str = "program",
) -> CreasePattern:
    """Compile an arbitrary fold programme into a folded crease pattern.

    This is the public entry point of the folding engine: the creases are
    listed in the order in which they are physically folded, and every crease
    cuts the pieces it crosses along the *current* position of its crease
    line.  A genome of the procedural engine is therefore nothing but an
    ordered list of creases.

    ``sheet`` defaults to the unit square in sheet space.
    """
    alpha = max(0.0, min(1.0, float(alpha)))
    outline = list(sheet) if sheet else [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    return _compile_pattern(key, label, outline, list(creases), alpha)


def build_pattern_mesh(
    pattern: CreasePattern,
    alpha: float = 1.0,
    scale: float = 1.0,
    archetype: str = "program",
    span_m: Optional[float] = None,
) -> OrigamiMesh:
    """Build the renderable mesh of an already compiled fold programme."""
    alpha = max(0.0, min(1.0, float(alpha)))
    faces = _fold_faces(pattern)
    layers = assign_layers(faces)

    thickness = VISUAL_THICKNESS_M
    pitch = LAYER_PITCH_M
    # The stack only separates while the paper is being folded, so the layer
    # offset is scaled with the fold progression.
    separation = pitch * alpha

    face_by_id = {face.fid: face for face in faces}
    offset: Dict[int, Vec3] = {}
    for face in faces:
        ref = layers.reference[face.fid]
        amount = layers.index[face.fid] * separation
        offset[face.fid] = _scale3(ref, amount)

    # Group the crease links by the unordered pair of faces they connect.
    pairs: Dict[Tuple[int, int], Tuple[Vec3, Vec3]] = {}
    for pattern_face in pattern.faces:
        for link in pattern_face.links:
            pair = (
                min(pattern_face.fid, link.neighbour_fid),
                max(pattern_face.fid, link.neighbour_fid),
            )
            pairs.setdefault(pair, (link.a, link.b))

    builder = _MeshBuilder()

    # Pre-compute the rolled crease samples for every crease pair.
    roll_samples: Dict[Tuple[int, int], List[_RollSample]] = {}
    for key, edge in pairs.items():
        fid_a, fid_b = key
        face_a = face_by_id.get(fid_a)
        face_b = face_by_id.get(fid_b)
        if face_a is None or face_b is None:
            continue
        parent_pts: List[Vec3] = []
        child_pts: List[Vec3] = []
        for i in range(ROLL_SAMPLES):
            t = i / (ROLL_SAMPLES - 1)
            base = (
                edge[0][0] + (edge[1][0] - edge[0][0]) * t,
                edge[0][1] + (edge[1][1] - edge[0][1]) * t,
                edge[0][2] + (edge[1][2] - edge[0][2]) * t,
            )
            parent_pts.append(_add3(base, offset[fid_a]))
            child_pts.append(_add3(base, offset[fid_b]))
        crease_dir = _unit3(_sub3(edge[1], edge[0]))
        roll_samples[key] = _roll_samples(
            parent_pts,
            child_pts,
            face_a.normal,
            face_b.normal,
            crease_dir,
            thickness,
        )

    # Build the skin of every face and close the sheet with the crease rolls.
    for face in faces:
        ref = layers.reference[face.fid]
        layer = float(layers.index[face.fid])
        sigma = 1.0 if _dot3(face.normal, ref) >= 0.0 else -1.0
        side = 1.0 if _dot3(face.front, ref) >= 0.0 else -1.0
        skin = _build_face_skin(
            face,
            pattern,
            ref,
            layer,
            side,
            sigma,
            offset,
            roll_samples,
            alpha,
            builder,
            thickness,
        )
        if len(skin.top) < 3:
            continue
        top_center = builder.vertex(
            _poly3d_centroid([builder.positions[i] for i in skin.top]),
            ref,
            _uv_of(face.centroid3d),
            layer + 1.0,
            0.0,
            0.0,
            0.0,
            side,
            face.fid,
        )
        bottom_center = builder.vertex(
            _poly3d_centroid([builder.positions[i] for i in skin.bottom]),
            _scale3(ref, -1.0),
            _uv_of(face.centroid3d),
            layer + 1.0,
            0.0,
            0.0,
            0.0,
            -side,
            face.fid,
        )
        for i in range(len(skin.top)):
            j = (i + 1) % len(skin.top)
            if _same_point3(
                builder.positions[skin.top[i]], builder.positions[skin.top[j]], 1e-9
            ):
                continue
            builder.oriented_triangle(top_center, skin.top[i], skin.top[j], ref)
            builder.oriented_triangle(
                bottom_center, skin.bottom[j], skin.bottom[i], _scale3(ref, -1.0)
            )
        for ta, tb, bb, ba in skin.cut_edges:
            if _same_point3(
                builder.positions[ta], builder.positions[tb], 1e-9
            ) or _same_point3(builder.positions[ta], builder.positions[ba], 1e-9):
                continue
            cut_dir = _unit3(_sub3(builder.positions[tb], builder.positions[ta]))
            outward = _unit3(_cross3(cut_dir, ref))
            if _dot3(outward, _sub3(builder.positions[ta], face.centroid3d)) < 0.0:
                outward = _scale3(outward, -1.0)
            builder.oriented_triangle(ta, tb, bb, outward)
            builder.oriented_triangle(ta, bb, ba, outward)
            for index in (ta, tb, bb, ba):
                builder.normals[index] = outward

    # Emit the rolled crease strips (they close the surface between skins).
    for key, samples in roll_samples.items():
        fid_a, fid_b = key
        face_a = face_by_id[fid_a]
        face_b = face_by_id[fid_b]
        ref_a = layers.reference[fid_a]
        side_a = 1.0 if _dot3(face_a.front, ref_a) >= 0.0 else -1.0
        layer_a = float(layers.index[fid_a])
        layer_b = float(layers.index[fid_b])
        top_layers = max(layer_a, layer_b) + 1.0

        plus_ids: List[int] = []
        minus_ids: List[int] = []
        for sample in samples:
            plus_ids.append(
                builder.vertex(
                    sample.plus,
                    sample.normal,
                    _uv_of(sample.mid),
                    top_layers,
                    1.0,
                    0.0,
                    0.0,
                    side_a,
                    fid_a,
                )
            )
            minus_ids.append(
                builder.vertex(
                    sample.minus,
                    _scale3(sample.normal, -1.0),
                    _uv_of(sample.mid),
                    top_layers,
                    1.0,
                    0.0,
                    0.0,
                    -side_a,
                    fid_a,
                )
            )
        for i in range(len(samples) - 1):
            builder.oriented_triangle(
                plus_ids[i], plus_ids[i + 1], minus_ids[i + 1], samples[i].normal
            )
            builder.oriented_triangle(
                plus_ids[i], minus_ids[i + 1], minus_ids[i], samples[i].normal
            )

    mesh = OrigamiMesh(
        archetype=archetype,
        alpha=alpha,
        positions=builder.positions,
        normals=builder.normals,
        uvs=builder.uvs,
        indices=builder.indices,
        layer_counts=builder.layer_counts,
        crease_weights=builder.crease_weights,
        elastic_weights=builder.elastic_weights,
        twist_weights=builder.twist_weights,
        paper_sides=builder.paper_sides,
        face_ids=builder.face_ids,
        folds=fold_sequence(pattern),
        layer_pitch_m=pitch,
        paper_thickness_m=thickness,
        max_layers=layers.max_layers,
        scale=scale,
    )
    _assign_elastic_weights(mesh, faces)
    factor = scale
    if span_m is not None and span_m > 0.0:
        # Match the wing span of the *folded* model, not the width of the
        # sheet it was cut from.
        lo, hi = mesh.bounds()
        current = hi[2] - lo[2]
        if current > 1e-9:
            factor *= span_m / current
    if abs(factor - 1.0) > 1e-12:
        mesh.positions = [_scale3(p, factor) for p in mesh.positions]
        mesh.paper_thickness_m = thickness * factor
        mesh.layer_pitch_m = pitch * factor
        mesh.scale = factor
    return mesh


def _uv_of(point: Vec3) -> Vec2:
    """Planar UV used for the procedural paper grain."""
    return (
        point[0] / SHEET_LENGTH_M + 0.5,
        point[2] / SHEET_WIDTH_M + 0.5,
    )


@dataclass
class _FaceSkin:
    """The top / bottom boundary rings of one face plus its cut edges."""

    top: List[int] = field(default_factory=list)
    bottom: List[int] = field(default_factory=list)
    cut_edges: List[Tuple[int, int, int, int]] = field(default_factory=list)


def _build_face_skin(
    face: FoldedFace,
    pattern: CreasePattern,
    ref: Vec3,
    layer: float,
    side: float,
    sigma: float,
    offset: Dict[int, Vec3],
    roll_samples: Dict[Tuple[int, int], List[_RollSample]],
    alpha: float,
    builder: _MeshBuilder,
    thickness: float,
) -> _FaceSkin:
    """Build the boundary rings of a face.

    A crease edge is replaced by the corresponding rolled fold curve so the
    skin and the roll share their boundary exactly; a cut edge of the sheet
    keeps its straight segment and receives a side strip.
    """
    skin = _FaceSkin()
    half = 0.5 * thickness
    flat_face = pattern.face_by_id(face.fid)
    links = flat_face.links if flat_face is not None else []
    count = len(face.poly3d)
    # Packet camber only develops while the sheet is being folded, so it
    # scales with the fold progression and vanishes at the flat sheet.
    camber = alpha * PACKET_CAMBER_M * (1.0 + layer)
    segments = [(link.a, link.b) for link in links]
    for i in range(count):
        a = face.poly3d[i]
        b = face.poly3d[(i + 1) % count]
        link = _match_link(links, a, b)
        if link is not None:
            key = (min(face.fid, link.neighbour_fid), max(face.fid, link.neighbour_fid))
            samples = roll_samples.get(key)
            if samples is not None:
                reversed_order = _dot3(_sub3(a, samples[0].parent_point), _sub3(b, a)) < 0.0
                ordered = list(reversed(samples)) if reversed_order else list(samples)
                plus = [s.plus for s in ordered]
                minus = [s.minus for s in ordered]
                if sigma > 0.0:
                    top_pts, bottom_pts = plus, minus
                else:
                    top_pts, bottom_pts = minus, plus
                for j in range(len(top_pts)):
                    if skin.top and _same_point3(
                        builder.positions[skin.top[-1]], top_pts[j], 1e-9
                    ):
                        continue
                    skin.top.append(
                        builder.vertex(
                            top_pts[j],
                            ref,
                            _uv_of(top_pts[j]),
                            layer + 1.0,
                            1.0,
                            0.0,
                            0.0,
                            side,
                            face.fid,
                        )
                    )
                    if skin.bottom and _same_point3(
                        builder.positions[skin.bottom[-1]], bottom_pts[j], 1e-9
                    ):
                        continue
                    skin.bottom.append(
                        builder.vertex(
                            bottom_pts[j],
                            _scale3(ref, -1.0),
                            _uv_of(bottom_pts[j]),
                            layer + 1.0,
                            1.0,
                            0.0,
                            0.0,
                            -side,
                            face.fid,
                        )
                    )
                continue
        pa = _add3(a, offset[face.fid])
        pb = _add3(b, offset[face.fid])
        # Layered packets bow out of the sheet plane between their creases.
        # The bulge is proportional to the layer count, fades to zero at the
        # crease (``_camber_weight``) and is injected along the face normal so
        # the sheet thickness stays constant while the mid-surface cambered.
        wa = _camber_weight(pa, segments)
        wb = _camber_weight(pb, segments)
        ta = _add3(pa, _scale3(ref, half + camber * wa))
        tb = _add3(pb, _scale3(ref, half + camber * wb))
        ba = _sub3(pa, _scale3(ref, half - camber * wa))
        bb = _sub3(pb, _scale3(ref, half - camber * wb))
        crease_a = _crease_weight(links, a)
        crease_b = _crease_weight(links, b)
        if skin.top and _same_point3(builder.positions[skin.top[-1]], ta, 1e-9):
            continue
        top_a = builder.vertex(
            ta, ref, _uv_of(ta), layer + 1.0, crease_a, 0.0, 0.0, side, face.fid
        )
        top_b = builder.vertex(
            tb, ref, _uv_of(tb), layer + 1.0, crease_b, 0.0, 0.0, side, face.fid
        )
        bottom_a = builder.vertex(
            ba, _scale3(ref, -1.0), _uv_of(ba), layer + 1.0, crease_a, 0.0, 0.0, -side, face.fid
        )
        bottom_b = builder.vertex(
            bb, _scale3(ref, -1.0), _uv_of(bb), layer + 1.0, crease_b, 0.0, 0.0, -side, face.fid
        )
        skin.top.append(top_a)
        skin.top.append(top_b)
        skin.bottom.append(bottom_a)
        skin.bottom.append(bottom_b)
        skin.cut_edges.append((top_a, top_b, bottom_b, bottom_a))
    return skin


def _assign_elastic_weights(mesh: OrigamiMesh, faces: Sequence[FoldedFace]) -> None:
    """Fill the spanwise bending weight and the chordwise twist weight."""
    wing_faces = [
        face
        for face in faces
        if face.group.startswith("wing") or face.group.startswith("elevon")
    ]
    if not wing_faces:
        return
    z_values: List[float] = []
    for face in wing_faces:
        for point in face.poly3d:
            z_values.append(abs(point[2]))
    z_root = min(z_values)
    z_tip = max(z_values)
    span = max(z_tip - z_root, 1e-9)
    x_values = [p[0] for face in wing_faces for p in face.poly3d]
    x_le = max(x_values)
    x_te = min(x_values)
    chord = max(x_le - x_te, 1e-9)
    group_of: Dict[int, str] = {face.fid: face.group for face in faces}
    for index, position in enumerate(mesh.positions):
        fid = mesh.face_ids[index]
        group = group_of.get(fid, "")
        if not (group.startswith("wing") or group.startswith("elevon")):
            continue
        station = max(0.0, min(1.0, (abs(position[2]) - z_root) / span))
        mesh.elastic_weights[index] = station * station
        twist = max(0.0, min(1.0, (x_le - position[0]) / chord))
        mesh.twist_weights[index] = twist


def _camber_weight(
    point: Vec3, segments: Sequence[Tuple[Vec3, Vec3]]
) -> float:
    """How far a point sits from the nearest crease line (0 .. 1).

    The weight is zero on the crease itself and rises smoothly towards the
    middle of the panel, which is where a folded paper packet bulges.
    """
    if not segments:
        return 0.0
    distance = min(
        _point_segment_distance_3d(point, a, b) for a, b in segments
    )
    if distance >= CAMBER_SPAN_M:
        return 1.0
    t = distance / CAMBER_SPAN_M
    return t * t * (3.0 - 2.0 * t)


def _crease_weight(links: Sequence[CreaseLink], point: Vec3) -> float:
    """Weight of a vertex relative to the crease lines of its own face."""
    best = 1.0
    for link in links:
        best = min(best, _point_segment_distance_3d(point, link.a, link.b))
    radius = 0.004
    if best >= radius:
        return 0.0
    return max(0.0, 1.0 - best / radius)


def _point_segment_distance_3d(p: Vec3, a: Vec3, b: Vec3) -> float:
    ab = _sub3(b, a)
    denom = _dot3(ab, ab)
    if denom <= 1e-18:
        return _len3(_sub3(p, a))
    t = max(0.0, min(1.0, _dot3(_sub3(p, a), ab) / denom))
    closest = _add3(a, _scale3(ab, t))
    return _len3(_sub3(p, closest))


def _match_link(
    links: Sequence[CreaseLink], a: Vec3, b: Vec3, eps: float = 1e-7
) -> Optional[CreaseLink]:
    """Find the crease link that produced the polygon edge ``a -> b``."""
    for link in links:
        if (_same_point3(link.a, a, eps) and _same_point3(link.b, b, eps)) or (
            _same_point3(link.a, b, eps) and _same_point3(link.b, a, eps)
        ):
            return link
    return None


def _same_point3(a: Vec3, b: Vec3, eps: float = 1e-7) -> bool:
    return (
        abs(a[0] - b[0]) <= eps and abs(a[1] - b[1]) <= eps and abs(a[2] - b[2]) <= eps
    )



#: Sheet half extents in the sheet plane (x = fore/aft, z = spanwise).
_HALF_LENGTH = SHEET_LENGTH_M * 0.5
_HALF_WIDTH = SHEET_WIDTH_M * 0.5


def _sheet_plane_to_uv(point: Tuple[float, float]) -> Vec2:
    """Convert a point of the sheet plane (x, z) into flat pattern (u, v)."""
    return (point[1] / SHEET_WIDTH_M + 0.5, point[0] / SHEET_LENGTH_M + 0.5)


def _uv_to_sheet_plane(point: Vec2) -> Tuple[float, float]:
    """Convert a flat pattern point (u, v) into the sheet plane (x, z)."""
    return ((point[1] - 0.5) * SHEET_LENGTH_M, (point[0] - 0.5) * SHEET_WIDTH_M)


def _clip_line_to_rect(
    point: Tuple[float, float], direction: Tuple[float, float]
) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """Clip a line to the sheet rectangle in the sheet plane."""
    hits: List[Tuple[float, float]] = []
    corners = [
        (-_HALF_LENGTH, -_HALF_WIDTH),
        (_HALF_LENGTH, -_HALF_WIDTH),
        (_HALF_LENGTH, _HALF_WIDTH),
        (-_HALF_LENGTH, _HALF_WIDTH),
    ]
    for i in range(4):
        a = corners[i]
        b = corners[(i + 1) % 4]
        ex = b[0] - a[0]
        ez = b[1] - a[1]
        denom = direction[0] * ez - direction[1] * ex
        if abs(denom) < 1e-12:
            continue
        rx = a[0] - point[0]
        rz = a[1] - point[1]
        t = (rx * ez - rz * ex) / denom
        u = (rx * direction[1] - rz * direction[0]) / denom
        if -1e-9 <= u <= 1.0 + 1e-9:
            hits.append((point[0] + direction[0] * t, point[1] + direction[1] * t))
    unique: List[Tuple[float, float]] = []
    for hit in hits:
        if not any(
            abs(hit[0] - q[0]) < 1e-9 and abs(hit[1] - q[1]) < 1e-9 for q in unique
        ):
            unique.append(hit)
    if len(unique) < 2:
        return None
    unique.sort(key=lambda q: (q[0], q[1]))
    return (unique[0], unique[-1])


def _perpendicular_bisector_3d(
    p: Tuple[float, float], q: Tuple[float, float]
) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Midpoint and direction of the bisector of two points in the sheet plane."""
    mid = ((p[0] + q[0]) * 0.5, (p[1] + q[1]) * 0.5)
    dx = q[0] - p[0]
    dz = q[1] - p[1]
    norm = math.hypot(dx, dz)
    if norm <= 1e-12:
        return mid, (1.0, 0.0)
    return mid, (dz / norm, -dx / norm)


def _corner_crease(nose_v: float) -> Tuple[Vec2, Vec2]:
    """Crease that folds the upper corner onto the centre line.

    The crease is the perpendicular bisector of the corner and its landing
    point, computed in the *real* geometry of the sheet, so the corner lands
    exactly on the centre line even though the sheet is not square.
    """
    corner = _uv_to_sheet_plane((0.0, 1.0))
    target = _uv_to_sheet_plane((0.5, nose_v))
    mid, direction = _perpendicular_bisector_3d(corner, target)
    clipped = _clip_line_to_rect(mid, direction)
    if clipped is None:
        return ((0.0, 0.5), (0.5, 1.0))
    return (_sheet_plane_to_uv(clipped[0]), _sheet_plane_to_uv(clipped[1]))


def _mirror_crease(crease: Tuple[Vec2, Vec2]) -> Tuple[Vec2, Vec2]:
    """Mirror a crease across the vertical centre line of the sheet."""
    return (
        (1.0 - crease[1][0], crease[1][1]),
        (1.0 - crease[0][0], crease[0][1]),
    )


def _collar_crease(corner_p0: Vec2, corner_p1: Vec2) -> Tuple[Vec2, Vec2]:
    """Crease that folds the slanted edge of the nose onto the centre line.

    The crease bisects the angle between the slanted edge of the folded
    corner and the centre line, and passes through the nose tip of the folded
    packet, so the slanted edge lands exactly on the centre line.
    """
    a = _uv_to_sheet_plane(corner_p0)
    b = _uv_to_sheet_plane(corner_p1)
    # The nose tip is the end of the corner crease that lies on the nose
    # edge of the sheet.  The collar crease bisects the angle between the
    # corner crease and the nose edge, because the nose edge is what ends up
    # on the centre line once the corner has been folded over.
    if a[0] >= b[0]:
        tip, other = a, b
    else:
        tip, other = b, a
    edge = (other[0] - tip[0], other[1] - tip[1])
    norm = math.hypot(*edge)
    if norm <= 1e-12:
        edge = (1.0, 0.0)
    else:
        edge = (edge[0] / norm, edge[1] / norm)
    nose = (0.0, -1.0) if tip[1] <= 0.0 else (0.0, 1.0)
    to_edge = (-edge[0], -edge[1])
    to_centre = nose
    bisector = (to_edge[0] + to_centre[0], to_edge[1] + to_centre[1])
    norm = math.hypot(*bisector)
    if norm <= 1e-12:
        bisector = (0.0, -1.0)
    else:
        bisector = (bisector[0] / norm, bisector[1] / norm)
    clipped = _clip_line_to_rect(tip, bisector)
    if clipped is None:
        return ((0.5, 1.0), (0.5, 0.0))
    return (_sheet_plane_to_uv(clipped[0]), _sheet_plane_to_uv(clipped[1]))


def _archetype_creases(
    archetype: str,
    keel_beta_deg: float,
    fold_leading_deg: float,
    fold_tip_deg: float,
    elevon_deg: float,
    fold_trailing_deg: float,
    dihedral_deg: float,
) -> List[Crease]:
    """Build the ordered crease list of one archetype.

    The creases are listed in the order in which they are physically folded:
    the body is creased first, the nose is folded flat on the table, and the
    wings are folded down last.  Every crease is restricted to the pieces
    that belong to it, so a fold never cuts a flap that was already folded
    onto the stack.
    """
    creases: List[Crease] = []

    keeler = max(keel_beta_deg, 0.0)
    body_angle = 180.0 - keeler
    body_half = body_angle * 0.5

    # -- 1. centre keel fold (the body) -----------------------------------
    creases.append(
        Crease(
            key="body",
            p0=(0.5, 0.0),
            p1=(0.5, 1.0),
            angle_deg=body_angle,
            side=0,
            up=False,
            level=0,
            set_group_pair=("body_l", "body_r"),
            residual_deg=RESIDUAL_CREASE_DEG,
        )
    )

    # -- 2. nose corner folds ----------------------------------------------
    # The corner is folded onto the centre line.  ``nose_v`` is the position
    # on the centre line where the corner lands; it is chosen so that the
    # crease ends exactly on the centre line and never crosses it.
    if archetype == ARCHETYPE_DART:
        # The nose edge lands along the centre line, so the corner travels
        # half the sheet width back from the tip.
        nose_v = 0.5 + (_HALF_LENGTH - _HALF_WIDTH) / SHEET_LENGTH_M
    elif archetype == ARCHETYPE_NAKAMURA:
        nose_v = 0.52
    else:
        nose_v = 0.60
    crease_l = _corner_crease(nose_v)
    crease_r = _mirror_crease(crease_l)
    creases.append(
        Crease(
            key="corner_l",
            p0=crease_l[0],
            p1=crease_l[1],
            angle_deg=180.0,
            side=1,
            up=True,
            level=1,
            groups=("body_l",),
            set_group="flap_l",
        )
    )
    creases.append(
        Crease(
            key="corner_r",
            p0=crease_r[0],
            p1=crease_r[1],
            angle_deg=180.0,
            side=1,
            up=False,
            level=1,
            groups=("body_r",),
            set_group="flap_r",
        )
    )

    # -- 3. double nose / collar / belly fold ------------------------------
    collar_angle = max(0.0, 180.0 - fold_leading_deg)
    if archetype == ARCHETYPE_DART:
        collar_l = _collar_crease(crease_l[0], crease_l[1])
        collar_r = _mirror_crease(collar_l)
        for key, p0, p1, side, group, up in (
            ("collar_l", collar_l[0], collar_l[1], 1, "flap_l", True),
            ("collar_r", collar_r[0], collar_r[1], 1, "flap_r", False),
        ):
            creases.append(
                Crease(
                    key=key,
                    p0=p0,
                    p1=p1,
                    angle_deg=collar_angle,
                    side=side,
                    up=up,
                    level=2,
                    groups=(group,),
                    set_group="collar_" + ("l" if side > 0 else "r"),
                )
            )
    else:
        # Nakamura lock / Sky King: the nose is folded back on itself so the
        # leading edge becomes blunt and the layers stack up.
        if archetype == ARCHETYPE_NAKAMURA:
            belly_v = 0.62
            belly_angle = 180.0
        else:
            belly_v = 0.70
            belly_angle = 165.0
        creases.append(
            Crease(
                key="belly",
                p0=(0.0, belly_v),
                p1=(1.0, belly_v),
                angle_deg=belly_angle,
                side=1,
                up=False,
                level=1,
                groups=("body_l", "body_r", "flap_l", "flap_r"),
            )
        )
        if archetype == ARCHETYPE_NAKAMURA:
            # The belly fold traps the folded nose packet in the centre of
            # the sheet.  The lock is a central triangle: a symmetric base
            # crease folds the nose tip in, and two stepped side creases tuck
            # one side's layer under the other so the packet cannot spring
            # open.
            lock_half = 0.085
            lock_v = 0.5 + lock_half * 1.5
            lock_cv = 0.5 + lock_half * 0.9
            lock_w = lock_half * 0.35
            # Center base crease: both halves fold by half the angle, forming
            # the central locking plane.
            creases.append(
                Crease(
                    key="lock_center",
                    p0=(0.5 - lock_w, lock_v),
                    p1=(0.5 + lock_w, lock_v),
                    angle_deg=24.0,
                    side=0,
                    up=False,
                    level=2,
                    set_group_pair=("lock_l", "lock_r"),
                    groups=("flap_l", "flap_r"),
                )
            )
            creases.append(
                Crease(
                    key="lock_l",
                    p0=(0.5 - lock_w, lock_v),
                    p1=(0.5, lock_cv),
                    angle_deg=24.0,
                    side=1,
                    up=True,
                    level=2,
                    groups=("flap_l",),
                    set_group="lock_l",
                )
            )
            creases.append(
                Crease(
                    key="lock_r",
                    p0=(0.5 + lock_w, lock_v),
                    p1=(0.5, lock_cv),
                    angle_deg=24.0,
                    side=-1,
                    up=True,
                    level=2,
                    groups=("flap_r",),
                    set_group="lock_r",
                )
            )

    # -- 4. main wing fold --------------------------------------------------
    if archetype == ARCHETYPE_NAKAMURA:
        wing_u = 0.34
    elif archetype == ARCHETYPE_SKY_KING:
        wing_u = 0.40
    else:
        wing_u = 0.42
    #: Leading-edge sweep applied to the wing fold near the nose (sheet
    #: units).  The wing crease angles back toward the tail at the wingtip so
    #    the planform is a swept wing instead of a rectangle.
    _LEADING_SWEEP_U: float = 0.05
    wing_angle = body_half + dihedral_deg
    # The nose end of the wing crease sweeps back (toward the tail at the
    # wingtip), mirroring a real dart's swept leading edge.
    wing_l_nose = wing_u - _LEADING_SWEEP_U
    wing_r_nose = 1.0 - wing_u + _LEADING_SWEEP_U
    creases.append(
        Crease(
            key="wing_l",
            p0=(wing_u, 0.0),
            p1=(wing_l_nose, 1.0),
            angle_deg=wing_angle,
            side=1,
            up=True,
            level=0,
            groups=("body_l",),
            set_group="wing_l",
        )
    )
    creases.append(
        Crease(
            key="wing_r",
            p0=(1.0 - wing_u, 0.0),
            p1=(wing_r_nose, 1.0),
            angle_deg=wing_angle,
            side=-1,
            up=True,
            level=0,
            groups=("body_r",),
            set_group="wing_r",
        )
    )

    # -- 5. winglets and elevon trim ---------------------------------------
    if archetype == ARCHETYPE_SKY_KING:
        winglet_u = 0.07
        winglet_angle = 90.0
    elif archetype == ARCHETYPE_NAKAMURA:
        winglet_u = 0.06
        winglet_angle = 70.0
    else:
        winglet_u = 0.07
        winglet_angle = 80.0
    winglet_angle += fold_tip_deg
    creases.append(
        Crease(
            key="winglet_l",
            p0=(winglet_u, 0.0),
            p1=(winglet_u, 1.0),
            angle_deg=winglet_angle,
            side=1,
            up=True,
            level=0,
            groups=("wing_l",),
            set_group="winglet_l",
        )
    )
    creases.append(
        Crease(
            key="winglet_r",
            p0=(1.0 - winglet_u, 0.0),
            p1=(1.0 - winglet_u, 1.0),
            angle_deg=winglet_angle,
            side=-1,
            up=True,
            level=0,
            groups=("wing_r",),
            set_group="winglet_r",
        )
    )

    elevon_v = 0.16
    creases.append(
        Crease(
            key="elevon_l",
            p0=(0.0, elevon_v),
            p1=(1.0, elevon_v),
            angle_deg=abs(elevon_deg),
            side=-1,
            up=False,
            level=0,
            groups=("wing_l", "winglet_l"),
            set_group="elevon_l",
        )
    )
    creases.append(
        Crease(
            key="elevon_r",
            p0=(0.0, elevon_v),
            p1=(1.0, elevon_v),
            angle_deg=abs(elevon_deg),
            side=-1,
            up=False,
            level=0,
            groups=("wing_r", "winglet_r"),
            set_group="elevon_r",
        )
    )

    trailing_v = 0.055
    # The trailing edge is folded on both wings.  Each half is creased on its
    # own wing so the left and right elevons are mirror images: a single
    # full-width crease with a global moving side would tessellate the two
    # halves differently and break left/right symmetry.
    creases.append(
        Crease(
            key="trailing_l",
            p0=(0.0, trailing_v),
            p1=(0.5, trailing_v),
            angle_deg=fold_trailing_deg,
            side=-1,
            up=False,
            level=0,
            groups=("elevon_l",),
            set_group="trailing_l",
        )
    )
    creases.append(
        Crease(
            key="trailing_r",
            p0=(0.5, trailing_v),
            p1=(1.0, trailing_v),
            angle_deg=fold_trailing_deg,
            side=-1,
            up=False,
            level=0,
            groups=("elevon_r",),
            set_group="trailing_r",
        )
    )
    return creases


def archetype_catalog() -> List[Dict[str, Any]]:
    """Describe the available paper aeroplane archetypes."""
    catalog: List[Dict[str, Any]] = []
    for key in ARCHETYPES:
        catalog.append(
            {
                "key": key,
                "label": ARCHETYPE_LABELS[key],
                "shape_flag": ARCHETYPE_SHAPE_FLAG.get(key, 0),
                "paper_thickness_m": VISUAL_THICKNESS_M,
                "layer_pitch_m": LAYER_PITCH_M,
                "layer_air_gap_m": LAYER_AIR_GAP_M,
                "sheet_width_m": SHEET_WIDTH_M,
                "sheet_length_m": SHEET_LENGTH_M,
            }
        )
    return catalog


def mesh_payload(mesh: OrigamiMesh) -> Dict[str, Any]:
    """Serialise a folded mesh into a JSON friendly payload."""
    lo, hi = mesh.bounds()
    flat_positions: List[float] = []
    for point in mesh.positions:
        flat_positions.extend(point)
    flat_normals: List[float] = []
    for normal in mesh.normals:
        flat_normals.extend(normal)
    flat_uvs: List[float] = []
    for uv in mesh.uvs:
        flat_uvs.extend(uv)
    flat_indices: List[int] = []
    for tri in mesh.indices:
        flat_indices.extend(tri)
    return {
        "archetype": mesh.archetype,
        "alpha": mesh.alpha,
        "positions": flat_positions,
        "normals": flat_normals,
        "uvs": flat_uvs,
        "indices": flat_indices,
        "layer_counts": [float(v) for v in mesh.layer_counts],
        "crease_weights": [float(v) for v in mesh.crease_weights],
        "elastic_weights": [float(v) for v in mesh.elastic_weights],
        "twist_weights": [float(v) for v in mesh.twist_weights],
        "paper_sides": [float(v) for v in mesh.paper_sides],
        "paper_thickness_m": mesh.paper_thickness_m,
        "layer_pitch_m": mesh.layer_pitch_m,
        "max_layers": mesh.max_layers,
        "scale": mesh.scale,
        "folds": list(mesh.folds),
        "bounds": {"min": list(lo), "max": list(hi)},
        "stats": {
            "vertices": mesh.vertex_count,
            "triangles": mesh.triangle_count,
            "surface_area_m2": mesh.surface_area(),
        },
    }


def apply_aeroelastic(
    mesh: OrigamiMesh, bend_deg: float = 0.0, twist_deg: float = 0.0
) -> OrigamiMesh:
    """Deform a mesh with the passive aero-elastic bending and twist.

    The deformation is driven by the per-vertex elastic weight (spanwise
    station of the wing) and twist weight (chordwise station), so the
    deformation is C1 continuous and the keel stays rigid.

    This is a **utility of the aero-elastic pipeline and of the unit tests**,
    not part of the production render path: the renderer keeps the mesh
    undeformed and transports the physical values to the shader instead
    (§A-15, the physics stays one-directional).  Calling it therefore changes
    the geometry on purpose and only where the caller asks for it.
    """
    if bend_deg == 0.0 and twist_deg == 0.0:
        return mesh
    bend = math.radians(bend_deg)
    twist = math.radians(twist_deg)
    positions: List[Vec3] = []
    for index, point in enumerate(mesh.positions):
        elastic = mesh.elastic_weights[index]
        chord = mesh.twist_weights[index]
        normal = mesh.normals[index]
        offset = elastic * elastic * bend * 0.25
        lateral = elastic * chord * twist
        positions.append(
            (
                point[0] + normal[0] * lateral,
                point[1] + normal[1] * lateral + offset,
                point[2] + normal[2] * lateral,
            )
        )
    mesh.positions = positions
    return mesh


def build_pattern(
    archetype: str,
    params: Optional[Dict[str, float]] = None,
    alpha: float = 1.0,
) -> CreasePattern:
    """Compile the flat crease pattern of one archetype."""
    params = params or {}
    keel_beta = float(params.get("keel_beta_deg", KEEL_V_GAP_DEG))
    fold_leading = float(params.get("fold_leading_deg", 0.0))
    fold_tip = float(params.get("fold_tip_deg", 0.0))
    elevon = float(params.get("elevon_deg", 0.0))
    fold_trailing = float(params.get("fold_trailing_deg", 15.0))
    dihedral = float(params.get("dihedral_deg", 5.0))
    creases = _archetype_creases(
        archetype, keel_beta, fold_leading, fold_tip, elevon, fold_trailing, dihedral
    )
    sheet = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    return _compile_pattern(
        archetype, ARCHETYPE_LABELS.get(archetype, archetype), sheet, creases, alpha
    )
