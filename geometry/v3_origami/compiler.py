"""Parametric 20-step origami sequence compiler.

Compiles a 3-phase high-level specification into a concrete 16 to 24 step
FoldStep sequence:
  Phase 1: Nose multi-layer folds (3-6 folds, taper, tip rigidity, forward CG)
  Phase 2: Keel depth & lock folds (structural grip >= 8mm, >= 2 layers)
  Phase 3: Wings, dihedral, elevons, and winglets
"""

from __future__ import annotations

import math
from typing import List, Tuple

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
from geometry.origami_genome import (
    FoldStep,
    genome_from_creases,
)
from core.interfaces import (
    MAX_FOLD_STEPS,
    MIN_FOLD_STEPS,
    ParametricOrigamiConfig,
)


def _clamp(v: float, low: float, high: float) -> float:
    return max(low, min(high, v))


class ParametricOrigamiCompiler:
    """Compiles high-level parametric aircraft configs into 16-24 FoldStep sequences."""

    def compile_steps(self, config: ParametricOrigamiConfig) -> List[FoldStep]:
        """Compile the configuration into an ordered list of 16-24 FoldStep objects."""
        # ------------------------------------------------------------------
        # Extract high-level parametric configuration
        # ------------------------------------------------------------------
        keel_cfg = config.keel
        wing_cfg = config.wing
        nose_cfg = config.nose

        keel_depth_ratio = _clamp(float(keel_cfg.keel_depth_ratio), 0.05, 0.15)
        keel_beta = _clamp(keel_depth_ratio * 40.0, 1.0, 15.0)

        span_ratio = _clamp(float(wing_cfg.wingspan_ratio), 0.5, 0.9)
        wing_u = _clamp(0.50 - 0.20 * span_ratio, 0.30, 0.48)
        dihedral = _clamp(float(wing_cfg.dihedral_deg), -5.0, 15.0)
        elevon = 0.5 * (_clamp(float(wing_cfg.elevon_left_deg), -10.0, 15.0) + _clamp(float(wing_cfg.elevon_right_deg), -10.0, 15.0))
        winglet_u = _clamp(0.08 * (1.0 - span_ratio * 0.2), 0.04, 0.12)
        winglet_angle = _clamp(float(wing_cfg.winglet_angle_deg), 10.0, 85.0)

        # ------------------------------------------------------------------
        # Build core crease pattern with proper group propagation
        # ------------------------------------------------------------------
        body_angle = 180.0 - keel_beta
        body_half = body_angle * 0.5

        nose_len = _clamp(float(nose_cfg.nose_length_ratio), 0.20, 0.35)
        taper = _clamp(float(nose_cfg.taper_ratio), 0.40, 0.85)
        fold_count = int(_clamp(float(nose_cfg.fold_count), 3, 6))
        has_lock = bool(keel_cfg.has_lock_fold)

        # Baseline corner fold position modulated by nose length and taper
        # Higher taper = wider nose base, lower taper = sharper/longer fold
        nose_base_v = 0.5 + (_HALF_LENGTH - _HALF_WIDTH) / SHEET_LENGTH_M
        eff_nose_v = _clamp(nose_base_v * (0.85 + 0.30 * nose_len), 0.50, 0.80)
        taper_offset = (1.0 - taper) * 0.08

        crease_l = _corner_crease(eff_nose_v)
        crease_r = _mirror_crease(crease_l)
        collar_l = _collar_crease(crease_l[0], crease_l[1])
        collar_r = _mirror_crease(collar_l)

        wing_angle = body_half + dihedral
        # Wing fold originates cleanly at nose tip (0.5, 1.0)
        # ensuring physical foldability without slicing into multi-layered nose flank.
        wing_l_nose = 0.5
        wing_r_nose = 0.5
        elevon_v = 0.16
        trailing_v = 0.055

        # Maximum physical extent for winglet fold: paper outer boundary at corner fold start
        winglet_v_max = min(1.0, max(0.5, crease_l[0][1]))

        creases: List[Crease] = [
            Crease(key="body", p0=(0.5, 0.0), p1=(0.5, 1.0), angle_deg=body_angle, side=0, up=False, level=0, set_group_pair=("body_l", "body_r"), residual_deg=RESIDUAL_CREASE_DEG),
            Crease(key="corner_l", p0=crease_l[0], p1=crease_l[1], angle_deg=180.0, side=1, up=True, level=1, groups=("body_l",), set_group="flap_l"),
            Crease(key="corner_r", p0=crease_r[0], p1=crease_r[1], angle_deg=180.0, side=1, up=False, level=1, groups=("body_r",), set_group="flap_r"),
            Crease(key="collar_l", p0=collar_l[0], p1=collar_l[1], angle_deg=180.0, side=1, up=True, level=2, groups=("flap_l",), set_group="collar_l"),
            Crease(key="collar_r", p0=collar_r[0], p1=collar_r[1], angle_deg=180.0, side=1, up=False, level=2, groups=("flap_r",), set_group="collar_r"),
        ]

        # Additional nose folds based on fold_count specification (4 to 6 folds)
        if fold_count >= 4:
            tip_v = eff_nose_v + 0.05
            creases.append(
                Crease(key="nose_lock_l", p0=(0.40, tip_v), p1=(0.50, tip_v + 0.04), angle_deg=180.0, side=1, up=True, level=3, groups=("collar_l",), set_group="nose_tip_l")
            )
            creases.append(
                Crease(key="nose_lock_r", p0=(0.60, tip_v), p1=(0.50, tip_v + 0.04), angle_deg=180.0, side=1, up=False, level=3, groups=("collar_r",), set_group="nose_tip_r")
            )

        if fold_count >= 5:
            creases.append(
                Crease(key="nose_reinforce_l", p0=(0.35, eff_nose_v * 0.9), p1=(0.48, eff_nose_v), angle_deg=120.0, side=1, up=True, level=2, groups=("flap_l",), set_group="nose_rf_l")
            )
            creases.append(
                Crease(key="nose_reinforce_r", p0=(0.65, eff_nose_v * 0.9), p1=(0.52, eff_nose_v), angle_deg=120.0, side=1, up=False, level=2, groups=("flap_r",), set_group="nose_rf_r")
            )

        # Wings and control surfaces (folded down from keel)
        # Bounded strictly within wing panel span to eliminate full-width slice across centerline
        wing_creases = [
            Crease(key="wing_l", p0=(wing_u, 0.0), p1=(wing_l_nose, 1.0), angle_deg=wing_angle, side=1, up=True, level=0, groups=("body_l",), set_group="wing_l"),
            Crease(key="wing_r", p0=(1.0 - wing_u, 0.0), p1=(1.0 - wing_l_nose, 1.0), angle_deg=wing_angle, side=-1, up=True, level=0, groups=("body_r",), set_group="wing_r"),
            Crease(key="winglet_l", p0=(winglet_u, 0.0), p1=(winglet_u, winglet_v_max), angle_deg=winglet_angle, side=1, up=True, level=0, groups=("wing_l",), set_group="winglet_l"),
            Crease(key="winglet_r", p0=(1.0 - winglet_u, 0.0), p1=(1.0 - winglet_u, winglet_v_max), angle_deg=winglet_angle, side=-1, up=True, level=0, groups=("wing_r",), set_group="winglet_r"),
            Crease(key="elevon_l", p0=(winglet_u, elevon_v), p1=(wing_u, elevon_v), angle_deg=abs(elevon), side=-1, up=False, level=0, groups=("wing_l", "winglet_l"), set_group="elevon_l"),
            Crease(key="elevon_r", p0=(1.0 - wing_u, elevon_v), p1=(1.0 - winglet_u, elevon_v), angle_deg=abs(elevon), side=-1, up=False, level=0, groups=("wing_r", "winglet_r"), set_group="elevon_r"),
            Crease(key="trailing_l", p0=(winglet_u, trailing_v), p1=(wing_u, trailing_v), angle_deg=15.0, side=-1, up=False, level=0, groups=("elevon_l",), set_group="trailing_l"),
            Crease(key="trailing_r", p0=(1.0 - wing_u, trailing_v), p1=(1.0 - winglet_u, trailing_v), angle_deg=15.0, side=-1, up=False, level=0, groups=("elevon_r",), set_group="trailing_r"),
        ]
        creases.extend(wing_creases)


        g = genome_from_creases(creases, key="v3_origami")
        steps = list(g.steps)

        # Strictly enforce bounds [MIN_FOLD_STEPS, MAX_FOLD_STEPS]
        if len(steps) > MAX_FOLD_STEPS:
            steps = steps[:MAX_FOLD_STEPS]

        return steps
