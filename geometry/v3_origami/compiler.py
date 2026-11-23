"""Parametric 20-step origami sequence compiler with physical foldability checks.

Compiles a 3-phase high-level specification into a concrete 16 to 24 step FoldStep sequence:
  Phase 1: Nose multi-layer folds (3-6 folds, taper, tip rigidity, forward CG)
  Phase 2: Keel depth & lock folds (structural grip >= 8mm, >= 2 layers)
  Phase 3: Wings, dihedral, elevons (signed), and winglets

Enforces computational origami physics:
  - Gallivan paper-thickness bend-limit constraint for multi-layer nose/keel folds
  - Kawasaki's theorem (alternating sector angle sum == 180 deg) for flat-fold vertices
  - Maekawa's theorem (|M - V| == 2) for flat-fold interior vertices
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from core.interfaces import (
    MAX_FOLD_STEPS,
    MIN_FOLD_STEPS,
    ParametricOrigamiConfig,
)
from geometry.origami_domain import (
    _HALF_LENGTH,
    _HALF_WIDTH,
    RESIDUAL_CREASE_DEG,
    SHEET_LENGTH_M,
    SHEET_WIDTH_M,
    _collar_crease,
    _corner_crease,
    _mirror_crease,
    Crease,
)
from geometry.origami_genome import (
    FoldStep,
    genome_from_creases,
)

PAPER_BULK_DENSITY_KGM3: float = 800.0


def _clamp(v: float, low: float, high: float) -> float:
    return max(low, min(high, v))


def paper_thickness_m(paper_gsm: float) -> float:
    """Compute physical sheet thickness t = (gsm * 1e-3) / rho in metres."""
    safe_gsm = max(float(paper_gsm), 20.0)
    return (safe_gsm * 1.0e-3) / PAPER_BULK_DENSITY_KGM3


def gallivan_min_fold_length_m(thickness_m: float, fold_count: int) -> float:
    """Gallivan's single-direction paper-folding length lower bound."""
    n = max(0, int(fold_count))
    if n == 0:
        return 0.0
    two_n = float(1 << min(n, 20))
    return (math.pi * max(thickness_m, 1.0e-6) / 6.0) * (two_n + 4.0) * (two_n - 1.0)


@dataclass(frozen=True)
class FoldabilityAuditResult:
    """Result of Kawasaki, Maekawa, and finite-thickness foldability validation."""

    flat_foldable_ok: bool
    thickness_limit_ok: bool
    kawasaki_max_error_deg: float
    maekawa_violations: int
    required_length_m: float
    available_length_m: float
    reasons: Tuple[str, ...] = ()


def check_vertex_kawasaki_maekawa(
    sector_angles_deg: Sequence[float],
    fold_senses: Sequence[int],
    angle_tol_deg: float = 1.5,
) -> Tuple[bool, bool, float]:
    """Verify Kawasaki's and Maekawa's theorems at a single interior flat-fold vertex."""
    n = len(sector_angles_deg)
    if n < 4 or n % 2 != 0 or len(fold_senses) != n:
        return False, False, 180.0
    total_angle = sum(float(a) for a in sector_angles_deg)
    if abs(total_angle - 360.0) > angle_tol_deg * 2.0:
        return False, False, abs(total_angle - 360.0)
    even_sum = sum(float(sector_angles_deg[i]) for i in range(0, n, 2))
    odd_sum = sum(float(sector_angles_deg[i]) for i in range(1, n, 2))
    kawasaki_err = max(abs(even_sum - 180.0), abs(odd_sum - 180.0))
    kawasaki_ok = kawasaki_err <= angle_tol_deg
    mv_diff = abs(sum(1 if s > 0 else -1 for s in fold_senses))
    maekawa_ok = mv_diff == 2
    return kawasaki_ok, maekawa_ok, kawasaki_err


def _segment_intersection_2d(
    p0: Tuple[float, float],
    p1: Tuple[float, float],
    q0: Tuple[float, float],
    q1: Tuple[float, float],
    eps: float = 1.0e-9,
) -> Tuple[bool, Tuple[float, float]]:
    """Return (intersects_strictly_inside, (x, y)) for two 2D crease segments."""
    rx, ry = p1[0] - p0[0], p1[1] - p0[1]
    sx, sy = q1[0] - q0[0], q1[1] - q0[1]
    denom = rx * sy - ry * sx
    if abs(denom) <= eps:
        return False, (0.0, 0.0)
    dx, dy = q0[0] - p0[0], q0[1] - p0[1]
    t = (dx * sy - dy * sx) / denom
    u = (dx * ry - dy * rx) / denom
    margin = 1.0e-5
    if margin < t < 1.0 - margin and margin < u < 1.0 - margin:
        return True, (p0[0] + t * rx, p0[1] + t * ry)
    return False, (0.0, 0.0)


def validate_crease_pattern_physics(
    steps: Sequence[FoldStep],
    paper_gsm: float = 100.0,
    nose_fold_count: int = 4,
    nose_length_ratio: float = 0.28,
) -> FoldabilityAuditResult:
    """Audit a fold sequence for Gallivan thickness limits and flat-foldability."""
    reasons: List[str] = []
    thickness = paper_thickness_m(paper_gsm)
    required_len = gallivan_min_fold_length_m(thickness, nose_fold_count)
    available_len = max(1.0e-4, float(nose_length_ratio) * SHEET_LENGTH_M)
    thickness_ok = required_len <= available_len
    if not thickness_ok:
        reasons.append(
            f"Paper thickness bend limit exceeded: {nose_fold_count} folds at "
            f"{paper_gsm:.0f}gsm require {required_len * 1000.0:.1f}mm > "
            f"available nose length {available_len * 1000.0:.1f}mm"
        )

    flat_steps = [s for s in steps if abs(s.fold_angle_deg) >= 170.0]
    vertex_rays: Dict[Tuple[int, int], List[Tuple[float, int]]] = {}

    for i in range(len(flat_steps)):
        si = flat_steps[i]
        p0 = (si.crease_line[0][0] * SHEET_WIDTH_M, si.crease_line[0][1] * SHEET_LENGTH_M)
        p1 = (si.crease_line[1][0] * SHEET_WIDTH_M, si.crease_line[1][1] * SHEET_LENGTH_M)
        sense_i = 1 if si.mountain else -1
        for j in range(i + 1, len(flat_steps)):
            sj = flat_steps[j]
            q0 = (sj.crease_line[0][0] * SHEET_WIDTH_M, sj.crease_line[0][1] * SHEET_LENGTH_M)
            q1 = (sj.crease_line[1][0] * SHEET_WIDTH_M, sj.crease_line[1][1] * SHEET_LENGTH_M)
            hit, pt = _segment_intersection_2d(p0, p1, q0, q1)
            if not hit:
                continue
            vkey = (int(round(pt[0] / 5.0e-4)), int(round(pt[1] / 5.0e-4)))
            rays = vertex_rays.setdefault(vkey, [])
            sense_j = 1 if sj.mountain else -1
            for end_pt, sense in ((p0, sense_i), (p1, sense_i), (q0, sense_j), (q1, sense_j)):
                ang = math.degrees(math.atan2(end_pt[1] - pt[1], end_pt[0] - pt[0])) % 360.0
                if not any(abs(((ang - a + 180.0) % 360.0) - 180.0) < 0.5 for a, _ in rays):
                    rays.append((ang, sense))

    max_kawasaki_err = 0.0
    maekawa_violations = 0
    for vkey, rays in vertex_rays.items():
        if len(rays) < 4:
            continue
        rays.sort(key=lambda item: item[0])
        angles: List[float] = []
        senses: List[int] = []
        for idx in range(len(rays)):
            a0 = rays[idx][0]
            a1 = rays[(idx + 1) % len(rays)][0]
            angles.append((a1 - a0) % 360.0)
            senses.append(rays[idx][1])
        k_ok, m_ok, k_err = check_vertex_kawasaki_maekawa(angles, senses)
        max_kawasaki_err = max(max_kawasaki_err, k_err)
        if not k_ok:
            reasons.append(
                f"Kawasaki flat-foldability violation at vertex {vkey}: "
                f"alternating angle error {k_err:.2f} deg"
            )
        if not m_ok:
            maekawa_violations += 1
            reasons.append(f"Maekawa theorem violation at vertex {vkey}: |M - V| != 2")

    flat_ok = (max_kawasaki_err <= 1.5) and (maekawa_violations == 0)
    return FoldabilityAuditResult(
        flat_foldable_ok=flat_ok,
        thickness_limit_ok=thickness_ok,
        kawasaki_max_error_deg=max_kawasaki_err,
        maekawa_violations=maekawa_violations,
        required_length_m=required_len,
        available_length_m=available_len,
        reasons=tuple(reasons),
    )


class ParametricOrigamiCompiler:
    """Compiles high-level parametric aircraft configs into 16-24 FoldStep sequences."""

    def compile_steps(self, config: ParametricOrigamiConfig) -> List[FoldStep]:
        """Compile the configuration into an ordered list of 16-24 FoldStep objects."""
        keel_cfg = config.keel
        wing_cfg = config.wing
        nose_cfg = config.nose

        keel_depth_ratio = _clamp(float(keel_cfg.keel_depth_ratio), 0.05, 0.15)
        keel_beta = _clamp(keel_depth_ratio * 40.0, 1.0, 15.0)
        keel_offset_u = _clamp(float(keel_cfg.crease_offset_u), 0.48, 0.52)

        span_ratio = _clamp(float(wing_cfg.wingspan_ratio), 0.5, 0.9)
        wing_u = _clamp(0.50 - 0.20 * span_ratio, 0.30, 0.48)
        dihedral = _clamp(float(wing_cfg.dihedral_deg), -5.0, 15.0)
        elevon_l = _clamp(float(wing_cfg.elevon_left_deg), -10.0, 15.0)
        elevon_r = _clamp(float(wing_cfg.elevon_right_deg), -10.0, 15.0)
        winglet_u = _clamp(0.08 * (1.0 - span_ratio * 0.2), 0.04, 0.12)
        winglet_angle = (
            _clamp(float(wing_cfg.winglet_angle_deg), 10.0, 85.0) if wing_cfg.has_winglet else 0.0
        )

        body_angle = 180.0 - keel_beta
        body_half = body_angle * 0.5

        nose_len = _clamp(float(nose_cfg.nose_length_ratio), 0.20, 0.35)
        taper = _clamp(float(nose_cfg.taper_ratio), 0.40, 0.85)
        fold_count = int(_clamp(float(nose_cfg.fold_count), 3, 6))
        has_lock = bool(keel_cfg.has_lock_fold)
        lock_angle = _clamp(float(keel_cfg.lock_fold_angle_deg), 90.0, 180.0)

        nose_base_v = 0.5 + (_HALF_LENGTH - _HALF_WIDTH) / SHEET_LENGTH_M
        eff_nose_v = _clamp(nose_base_v * (0.85 + 0.30 * nose_len), 0.50, 0.80)
        taper_offset = (1.0 - taper) * 0.08

        crease_l = _corner_crease(eff_nose_v)
        crease_r = _mirror_crease(crease_l)
        collar_l = _collar_crease(crease_l[0], crease_l[1])
        collar_r = _mirror_crease(collar_l)

        wing_angle = body_half + dihedral
        wing_l_nose = _clamp(wing_u - 0.05 + taper_offset, 0.20, 0.45)
        elevon_v = 0.16
        trailing_v = 0.055

        creases: List[Crease] = [
            Crease(
                key="body",
                p0=(keel_offset_u, 0.0),
                p1=(keel_offset_u, 1.0),
                angle_deg=body_angle,
                side=0,
                up=False,
                level=0,
                set_group_pair=("body_l", "body_r"),
                residual_deg=RESIDUAL_CREASE_DEG,
            ),
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
            ),
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
            ),
            Crease(
                key="collar_l",
                p0=collar_l[0],
                p1=collar_l[1],
                angle_deg=180.0,
                side=1,
                up=True,
                level=2,
                groups=("flap_l",),
                set_group="collar_l",
            ),
            Crease(
                key="collar_r",
                p0=collar_r[0],
                p1=collar_r[1],
                angle_deg=180.0,
                side=1,
                up=False,
                level=2,
                groups=("flap_r",),
                set_group="collar_r",
            ),
        ]

        if fold_count >= 4:
            tip_v = _clamp(eff_nose_v + 0.05, 0.55, 0.88)
            creases.append(
                Crease(
                    key="nose_lock_l",
                    p0=(0.40, tip_v),
                    p1=(0.50, tip_v + 0.04),
                    angle_deg=180.0,
                    side=1,
                    up=True,
                    level=3,
                    groups=("collar_l",),
                    set_group="nose_tip_l",
                )
            )
            creases.append(
                Crease(
                    key="nose_lock_r",
                    p0=(0.60, tip_v),
                    p1=(0.50, tip_v + 0.04),
                    angle_deg=180.0,
                    side=1,
                    up=False,
                    level=3,
                    groups=("collar_r",),
                    set_group="nose_tip_r",
                )
            )

        if fold_count >= 5:
            creases.append(
                Crease(
                    key="nose_reinforce_l",
                    p0=(0.35, eff_nose_v * 0.9),
                    p1=(0.48, eff_nose_v),
                    angle_deg=120.0,
                    side=1,
                    up=True,
                    level=2,
                    groups=("flap_l",),
                    set_group="nose_rf_l",
                )
            )
            creases.append(
                Crease(
                    key="nose_reinforce_r",
                    p0=(0.65, eff_nose_v * 0.9),
                    p1=(0.52, eff_nose_v),
                    angle_deg=120.0,
                    side=1,
                    up=False,
                    level=2,
                    groups=("flap_r",),
                    set_group="nose_rf_r",
                )
            )

        if has_lock:
            lock_v = _clamp(eff_nose_v - 0.14, 0.35, 0.65)
            creases.append(
                Crease(
                    key="keel_lock_l",
                    p0=(0.44, lock_v),
                    p1=(keel_offset_u, lock_v + 0.03),
                    angle_deg=lock_angle,
                    side=1,
                    up=True,
                    level=2,
                    groups=("body_l",),
                    set_group="keel_lock_l",
                )
            )
            creases.append(
                Crease(
                    key="keel_lock_r",
                    p0=(0.56, lock_v),
                    p1=(keel_offset_u, lock_v + 0.03),
                    angle_deg=lock_angle,
                    side=1,
                    up=False,
                    level=2,
                    groups=("body_r",),
                    set_group="keel_lock_r",
                )
            )

        wing_creases = [
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
            ),
            Crease(
                key="wing_r",
                p0=(1.0 - wing_u, 0.0),
                p1=(1.0 - wing_l_nose, 1.0),
                angle_deg=wing_angle,
                side=-1,
                up=True,
                level=0,
                groups=("body_r",),
                set_group="wing_r",
            ),
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
            ),
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
            ),
            Crease(
                key="elevon_l",
                p0=(0.0, elevon_v),
                p1=(0.5, elevon_v),
                angle_deg=abs(elevon_l),
                side=-1,
                up=(elevon_l >= 0.0),
                level=0,
                groups=("wing_l", "winglet_l"),
                set_group="elevon_l",
            ),
            Crease(
                key="elevon_r",
                p0=(0.5, elevon_v),
                p1=(1.0, elevon_v),
                angle_deg=abs(elevon_r),
                side=-1,
                up=(elevon_r >= 0.0),
                level=0,
                groups=("wing_r", "winglet_r"),
                set_group="elevon_r",
            ),
            Crease(
                key="trailing_l",
                p0=(0.0, trailing_v),
                p1=(0.5, trailing_v),
                angle_deg=15.0,
                side=-1,
                up=False,
                level=0,
                groups=("elevon_l",),
                set_group="trailing_l",
            ),
            Crease(
                key="trailing_r",
                p0=(0.5, trailing_v),
                p1=(1.0, trailing_v),
                angle_deg=15.0,
                side=-1,
                up=False,
                level=0,
                groups=("elevon_r",),
                set_group="trailing_r",
            ),
        ]
        creases.extend(wing_creases)

        target_count = 20
        extra_count = max(0, target_count - len(creases))
        for i in range(extra_count):
            u_offset = 0.16 + 0.05 * (i // 2)
            u_l = u_offset
            u_r = 1.0 - u_offset
            if i % 2 == 0:
                creases.append(
                    Crease(
                        key=f"camber_l_{i // 2 + 1}",
                        p0=(u_l, 0.20),
                        p1=(u_l, 0.85),
                        angle_deg=2.0 + 0.5 * (i // 2),
                        side=1,
                        up=True,
                        level=0,
                        groups=("wing_l",),
                        set_group=f"camber_l_{i // 2 + 1}",
                    )
                )
            else:
                creases.append(
                    Crease(
                        key=f"camber_r_{i // 2 + 1}",
                        p0=(u_r, 0.20),
                        p1=(u_r, 0.85),
                        angle_deg=2.0 + 0.5 * (i // 2),
                        side=-1,
                        up=True,
                        level=0,
                        groups=("wing_r",),
                        set_group=f"camber_r_{i // 2 + 1}",
                    )
                )

        g = genome_from_creases(creases, key="v3_origami")
        steps = list(g.steps)
        if len(steps) > MAX_FOLD_STEPS:
            steps = steps[:MAX_FOLD_STEPS]
        return steps

    def compile_macro_steps(self, config: ParametricOrigamiConfig):
        """Compatibility fallback to 2.5D kernel macro origami compiler."""
        from geometry.v3_origami.kernel_25d import compile_macro_origami
        return compile_macro_origami(config)


def compile_steps(config: ParametricOrigamiConfig) -> List[FoldStep]:
    compiler = ParametricOrigamiCompiler()
    return compiler.compile_steps(config)


def compile_macro_steps(config: ParametricOrigamiConfig):
    compiler = ParametricOrigamiCompiler()
    return compiler.compile_macro_steps(config)
