"""Sequential 2.5D Origami Folding Kernel (Correct-by-Construction).

Implements the 2.5D layer-stack state machine with half-space polygon clipping,
rigid reflection transforms, and exact inverse affine mapping to flat A4 UV sheet space.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

Vec2 = Tuple[float, float]
Mat3 = Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]


def _matmul3(a: Mat3, b: Mat3) -> Mat3:
    """Multiply two 3x3 matrices."""
    res = [[0.0] * 3 for _ in range(3)]
    for r in range(3):
        for c in range(3):
            res[r][c] = a[r][0] * b[0][c] + a[r][1] * b[1][c] + a[r][2] * b[2][c]
    return (
        (res[0][0], res[0][1], res[0][2]),
        (res[1][0], res[1][1], res[1][2]),
        (res[2][0], res[2][1], res[2][2]),
    )


def _matvec3(m: Mat3, v: Tuple[float, float, float]) -> Tuple[float, float, float]:
    """Multiply a 3x3 matrix by a 3D homogeneous vector."""
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def identity3() -> Mat3:
    """Return 3x3 identity matrix."""
    return (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )


def make_reflection_matrix(p0: Vec2, d: Vec2) -> Mat3:
    """Create 3x3 homogeneous matrix for 2D reflection across line through p0 along direction d."""
    norm = math.hypot(d[0], d[1])
    if norm < 1e-12:
        return identity3()
    dx, dy = d[0] / norm, d[1] / norm
    # Unit normal vector
    nx, ny = -dy, dx
    dot_p0_n = p0[0] * nx + p0[1] * ny

    return (
        (1.0 - 2.0 * nx * nx, -2.0 * nx * ny, 2.0 * nx * dot_p0_n),
        (-2.0 * nx * ny, 1.0 - 2.0 * ny * ny, 2.0 * ny * dot_p0_n),
        (0.0, 0.0, 1.0),
    )


@dataclass
class FoldedPolygon2D:
    """A 2D polygon piece on the current working plane."""

    fid: int
    vertices: List[Vec2]
    z_index: int
    face_up: bool
    # Inverse affine mapping from current working plane (x, y) to original A4 UV (u, v)
    uv_affine_inv: Mat3 = field(default_factory=identity3)
    group: str = "sheet"

    @property
    def area(self) -> float:
        """Signed / absolute polygon area using shoelace formula."""
        n = len(self.vertices)
        if n < 3:
            return 0.0
        acc = 0.0
        for i in range(n):
            j = (i + 1) % n
            acc += self.vertices[i][0] * self.vertices[j][1] - self.vertices[j][0] * self.vertices[i][1]
        return 0.5 * abs(acc)

    def to_uv(self, p: Vec2) -> Vec2:
        """Map a working plane point to original flat A4 sheet UV space."""
        hom = (p[0], p[1], 1.0)
        u, v, _ = _matvec3(self.uv_affine_inv, hom)
        return (u, v)


@dataclass(frozen=True)
class MacroFoldStep:
    """High-level folding action representing a single human cognitive step."""

    step_index: int
    name: str
    fold_type: str  # 'axis_symmetric' or 'bilateral_symmetric'
    line_p0: Vec2
    line_p1: Vec2
    angle_deg: float
    mountain: bool
    group_target: Optional[str] = None
    desc: str = ""


class Sequential2DOrigamiKernel:
    """2.5D layer-stack state machine simulating sequential flat and structural folds."""

    def __init__(self) -> None:
        # Initial sheet in normalized UV [0, 1] x [0, 1]
        # Nose at v=1.0, trailing edge at v=0.0
        init_poly = FoldedPolygon2D(
            fid=0,
            vertices=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
            z_index=0,
            face_up=True,
            uv_affine_inv=identity3(),
            group="body",
        )
        self.polygons: List[FoldedPolygon2D] = [init_poly]
        self.next_fid = 1
        self.history: List[Dict[str, Any]] = []

    def clip_polygon(
        self, poly: FoldedPolygon2D, p0: Vec2, direction: Vec2
    ) -> Tuple[Optional[List[Vec2]], Optional[List[Vec2]], Optional[Tuple[Vec2, Vec2]]]:
        """Split a polygon by line (p0, direction) using Sutherland-Hodgman clipping.

        Returns:
            (stay_verts, move_verts, cut_segment)
            stay_verts: Vertices on fixed side ((p - p0) . n <= 0)
            move_verts: Vertices on moving side ((p - p0) . n > 0)
            cut_segment: Segment (c0, c1) along the fold line if sliced
        """
        pts = poly.vertices
        n_pts = len(pts)
        if n_pts < 3:
            return None, None, None

        norm = math.hypot(direction[0], direction[1])
        if norm < 1e-12:
            return pts, None, None
        nx, ny = -direction[1] / norm, direction[0] / norm

        def dist(p: Vec2) -> float:
            return (p[0] - p0[0]) * nx + (p[1] - p0[1]) * ny

        dists = [dist(p) for p in pts]
        eps = 1e-9

        all_stay = all(d <= eps for d in dists)
        all_move = all(d >= -eps for d in dists)

        if all_stay:
            return list(pts), None, None
        if all_move:
            return None, list(pts), None

        stay_pts: List[Vec2] = []
        move_pts: List[Vec2] = []
        cut_pts: List[Vec2] = []

        for i in range(n_pts):
            curr_p = pts[i]
            next_p = pts[(i + 1) % n_pts]
            d_curr = dists[i]
            d_next = dists[(i + 1) % n_pts]

            if d_curr <= eps:
                stay_pts.append(curr_p)
            if d_curr >= -eps:
                move_pts.append(curr_p)

            # Check edge intersection
            if (d_curr < -eps and d_next > eps) or (d_curr > eps and d_next < -eps):
                t = d_curr / (d_curr - d_next)
                ix = curr_p[0] + t * (next_p[0] - curr_p[0])
                iy = curr_p[1] + t * (next_p[1] - curr_p[1])
                inter = (ix, iy)
                stay_pts.append(inter)
                move_pts.append(inter)
                cut_pts.append(inter)

        cut_seg = (cut_pts[0], cut_pts[1]) if len(cut_pts) >= 2 else None
        return (stay_pts if len(stay_pts) >= 3 else None,
                move_pts if len(move_pts) >= 3 else None,
                cut_seg)

    def execute_fold(
        self,
        p0: Vec2,
        direction: Vec2,
        mountain: bool = True,
        group_filter: Optional[str] = None,
        target_group: Optional[str] = None,
    ) -> List[Tuple[Vec2, Vec2]]:
        """Execute a fold operation on working polygons.

        Returns list of UV crease lines produced by this fold.
        """
        refl = make_reflection_matrix(p0, direction)
        new_polygons: List[FoldedPolygon2D] = []
        uv_creases: List[Tuple[Vec2, Vec2]] = []

        max_z = max(p.z_index for p in self.polygons) if self.polygons else 0

        for poly in self.polygons:
            if group_filter is not None and poly.group != group_filter:
                new_polygons.append(poly)
                continue

            stay_v, move_v, cut_seg = self.clip_polygon(poly, p0, direction)

            if cut_seg is not None:
                # Map cut segment to original A4 UV space
                uv0 = poly.to_uv(cut_seg[0])
                uv1 = poly.to_uv(cut_seg[1])
                uv_creases.append((uv0, uv1))

            if stay_v is not None:
                new_polygons.append(
                    FoldedPolygon2D(
                        fid=self.next_fid,
                        vertices=stay_v,
                        z_index=poly.z_index,
                        face_up=poly.face_up,
                        uv_affine_inv=poly.uv_affine_inv,
                        group=poly.group,
                    )
                )
                self.next_fid += 1

            if move_v is not None:
                # Reflect moving vertices across line
                reflected_verts: List[Vec2] = []
                for v in move_v:
                    rx, ry, _ = _matvec3(refl, (v[0], v[1], 1.0))
                    reflected_verts.append((rx, ry))

                # Reverse vertex winding after reflection to maintain consistent orientation
                reflected_verts.reverse()

                # Update inverse affine transform
                new_affine = _matmul3(poly.uv_affine_inv, refl)
                grp = target_group or poly.group

                # Stack reflected piece on top or bottom
                new_z = max_z + 1 if mountain else -abs(poly.z_index) - 1

                new_polygons.append(
                    FoldedPolygon2D(
                        fid=self.next_fid,
                        vertices=reflected_verts,
                        z_index=new_z,
                        face_up=not poly.face_up,
                        uv_affine_inv=new_affine,
                        group=grp,
                    )
                )
                self.next_fid += 1

        self.polygons = new_polygons
        return uv_creases


def compile_macro_origami(
    config: Any,
) -> Tuple[List[MacroFoldStep], List[Any]]:
    """Compile parametric configuration into MacroFoldSteps and corresponding FoldSteps.

    Bilevel architecture:
      - MacroFoldSteps: 6 to 10 human cognitive steps (bilateral & axis symmetric)
      - FoldSteps: 10 to 16 atomic steps for 3D physics engine and 6DoF RK4 simulation.
    """
    from geometry.origami_domain import (
        _HALF_LENGTH,
        _HALF_WIDTH,
        RESIDUAL_CREASE_DEG,
        SHEET_LENGTH_M,
        _collar_crease,
        _corner_crease,
        _mirror_crease,
        Crease,
    )
    from geometry.origami_genome import FoldStep, genome_from_creases

    keel_cfg = config.keel
    wing_cfg = config.wing
    nose_cfg = config.nose

    keel_depth_ratio = max(0.05, min(0.15, float(keel_cfg.keel_depth_ratio)))
    keel_beta = max(1.0, min(15.0, keel_depth_ratio * 40.0))

    span_ratio = max(0.5, min(0.9, float(wing_cfg.wingspan_ratio)))
    wing_u = max(0.30, min(0.48, 0.50 - 0.20 * span_ratio))
    dihedral = max(-5.0, min(15.0, float(wing_cfg.dihedral_deg)))
    elevon = 0.5 * (
        max(-10.0, min(15.0, float(wing_cfg.elevon_left_deg)))
        + max(-10.0, min(15.0, float(wing_cfg.elevon_right_deg)))
    )
    winglet_u = max(0.04, min(0.12, 0.08 * (1.0 - span_ratio * 0.2)))
    winglet_angle = max(10.0, min(85.0, float(wing_cfg.winglet_angle_deg)))

    body_angle = 180.0 - keel_beta
    body_half = body_angle * 0.5
    wing_angle = body_half + dihedral

    nose_len = max(0.20, min(0.35, float(nose_cfg.nose_length_ratio)))
    fold_count = int(max(3, min(6, float(nose_cfg.fold_count))))

    nose_base_v = 0.5 + (_HALF_LENGTH - _HALF_WIDTH) / SHEET_LENGTH_M
    eff_nose_v = max(0.50, min(0.80, nose_base_v * (0.85 + 0.30 * nose_len)))

    crease_l = _corner_crease(eff_nose_v)
    crease_r = _mirror_crease(crease_l)
    collar_l = _collar_crease(crease_l[0], crease_l[1])
    collar_r = _mirror_crease(collar_l)

    elevon_v = 0.16
    trailing_v = 0.055
    winglet_v_max = min(1.0, max(0.5, crease_l[0][1]))

    # 1. Build High-Level Human Cognitive MacroFoldSteps (6 to 10 steps)
    macro_steps: List[MacroFoldStep] = [
        MacroFoldStep(
            step_index=1,
            name="centerline_crease",
            fold_type="axis_symmetric",
            line_p0=(0.5, 0.0),
            line_p1=(0.5, 1.0),
            angle_deg=180.0,
            mountain=False,
            desc="センターラインの谷折り（中央の折り目付け）",
        ),
        MacroFoldStep(
            step_index=2,
            name="nose_corner_folds",
            fold_type="bilateral_symmetric",
            line_p0=crease_l[0],
            line_p1=crease_l[1],
            angle_deg=180.0,
            mountain=False,
            desc="機首斜め三角折り（左右対称に中央へ手前折り）",
        ),
        MacroFoldStep(
            step_index=3,
            name="nose_collar_folds",
            fold_type="bilateral_symmetric",
            line_p0=collar_l[0],
            line_p1=collar_l[1],
            angle_deg=180.0,
            mountain=False,
            desc="機首サイド襟折り（二重ロック折り）",
        ),
    ]

    # Optional nose lock
    if fold_count >= 4:
        tip_v = eff_nose_v + 0.05
        macro_steps.append(
            MacroFoldStep(
                step_index=len(macro_steps) + 1,
                name="nose_lock_fold",
                fold_type="axis_symmetric",
                line_p0=(0.40, tip_v),
                line_p1=(0.60, tip_v),
                angle_deg=180.0,
                mountain=True,
                desc="先端ロック折り（機首の剛性向上と重心前方化）",
            )
        )

    # Body keel fold
    macro_steps.append(
        MacroFoldStep(
            step_index=len(macro_steps) + 1,
            name="body_keel_fold",
            fold_type="axis_symmetric",
            line_p0=(0.5, 0.0),
            line_p1=(0.5, 1.0),
            angle_deg=body_angle,
            mountain=True,
            desc=f"胴体キール折り（裏返して半分に山折り、キール角 {keel_beta:.1f}°）",
        )
    )

    # Wings fold down
    macro_steps.append(
        MacroFoldStep(
            step_index=len(macro_steps) + 1,
            name="wing_folds",
            fold_type="bilateral_symmetric",
            line_p0=(wing_u, 0.0),
            line_p1=(0.5, 1.0),
            angle_deg=wing_angle,
            mountain=True,
            desc="主翼の折り下げ（持ち手キールを残して外側へ水平折り）",
        )
    )

    # Winglets
    macro_steps.append(
        MacroFoldStep(
            step_index=len(macro_steps) + 1,
            name="winglet_folds",
            fold_type="bilateral_symmetric",
            line_p0=(winglet_u, 0.0),
            line_p1=(winglet_u, winglet_v_max),
            angle_deg=winglet_angle,
            mountain=False,
            desc=f"翼端ウィングレット（両端を斜め上 {winglet_angle:.1f}° に谷折り立ち上げ）",
        )
    )

    # Elevons / Reflex trim
    macro_steps.append(
        MacroFoldStep(
            step_index=len(macro_steps) + 1,
            name="elevon_reflex_trim",
            fold_type="bilateral_symmetric",
            line_p0=(winglet_u, elevon_v),
            line_p1=(wing_u, elevon_v),
            angle_deg=abs(elevon),
            mountain=False,
            desc=f"エレボントリム（主翼後縁を指先で約1〜2mm反らせる {elevon:.1f}°）",
        )
    )

    # 2. Decompose MacroFoldSteps into Atomic Creases for 3D physics genome
    creases: List[Crease] = [
        Crease(key="body", p0=(0.5, 0.0), p1=(0.5, 1.0), angle_deg=body_angle, side=0, up=False, level=0, set_group_pair=("body_l", "body_r"), residual_deg=RESIDUAL_CREASE_DEG),
        Crease(key="corner_l", p0=crease_l[0], p1=crease_l[1], angle_deg=180.0, side=1, up=True, level=1, groups=("body_l",), set_group="flap_l"),
        Crease(key="corner_r", p0=crease_r[0], p1=crease_r[1], angle_deg=180.0, side=1, up=False, level=1, groups=("body_r",), set_group="flap_r"),
        Crease(key="collar_l", p0=collar_l[0], p1=collar_l[1], angle_deg=180.0, side=1, up=True, level=2, groups=("flap_l",), set_group="collar_l"),
        Crease(key="collar_r", p0=collar_r[0], p1=collar_r[1], angle_deg=180.0, side=1, up=False, level=2, groups=("flap_r",), set_group="collar_r"),
    ]

    if fold_count >= 4:
        tip_v = eff_nose_v + 0.05
        creases.append(
            Crease(key="nose_lock_l", p0=(0.40, tip_v), p1=(0.50, tip_v + 0.04), angle_deg=180.0, side=1, up=True, level=3, groups=("collar_l",), set_group="nose_tip_l")
        )
        creases.append(
            Crease(key="nose_lock_r", p0=(0.60, tip_v), p1=(0.50, tip_v + 0.04), angle_deg=180.0, side=1, up=False, level=3, groups=("collar_r",), set_group="nose_tip_r")
        )

    creases.extend([
        Crease(key="wing_l", p0=(wing_u, 0.0), p1=(0.5, 1.0), angle_deg=wing_angle, side=1, up=True, level=0, groups=("body_l",), set_group="wing_l"),
        Crease(key="wing_r", p0=(1.0 - wing_u, 0.0), p1=(0.5, 1.0), angle_deg=wing_angle, side=-1, up=True, level=0, groups=("body_r",), set_group="wing_r"),
        Crease(key="winglet_l", p0=(winglet_u, 0.0), p1=(winglet_u, winglet_v_max), angle_deg=winglet_angle, side=1, up=True, level=0, groups=("wing_l",), set_group="winglet_l"),
        Crease(key="winglet_r", p0=(1.0 - winglet_u, 0.0), p1=(1.0 - winglet_u, winglet_v_max), angle_deg=winglet_angle, side=-1, up=True, level=0, groups=("wing_r",), set_group="winglet_r"),
        Crease(key="elevon_l", p0=(winglet_u, elevon_v), p1=(wing_u, elevon_v), angle_deg=abs(elevon), side=-1, up=False, level=0, groups=("wing_l", "winglet_l"), set_group="elevon_l"),
        Crease(key="elevon_r", p0=(1.0 - wing_u, elevon_v), p1=(1.0 - winglet_u, elevon_v), angle_deg=abs(elevon), side=-1, up=False, level=0, groups=("wing_r", "winglet_r"), set_group="elevon_r"),
        Crease(key="trailing_l", p0=(winglet_u, trailing_v), p1=(wing_u, trailing_v), angle_deg=15.0, side=-1, up=False, level=0, groups=("elevon_l",), set_group="trailing_l"),
        Crease(key="trailing_r", p0=(1.0 - wing_u, trailing_v), p1=(1.0 - winglet_u, trailing_v), angle_deg=15.0, side=-1, up=False, level=0, groups=("elevon_r",), set_group="trailing_r"),
    ])

    g = genome_from_creases(creases, key="v3_macro_origami")
    fold_steps = list(g.steps)

    return macro_steps, fold_steps
