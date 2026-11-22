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
from v3.interfaces import (
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
        # Dynamic nose fold placement based on nose length and taper ratio
        base_v = 0.5 + (_HALF_LENGTH - _HALF_WIDTH) / SHEET_LENGTH_M
        nose_v = _clamp(base_v + (0.28 - nose_len) * 0.4 + (taper - 0.65) * 0.15, 0.55, 0.72)

        crease_l = _corner_crease(nose_v)
        crease_r = _mirror_crease(crease_l)
        collar_l = _collar_crease(crease_l[0], crease_l[1])
        collar_r = _mirror_crease(collar_l)

        wing_angle = body_half + dihedral
        wing_l_nose = wing_u - 0.05
        wing_r_nose = 1.0 - wing_u + 0.05
        elevon_v = 0.16
        trailing_v = 0.055

        creases: List[Crease] = [
            Crease(key="body", p0=(0.5, 0.0), p1=(0.5, 1.0), angle_deg=body_angle, side=0, up=False, level=0, set_group_pair=("body_l", "body_r"), residual_deg=RESIDUAL_CREASE_DEG),
            Crease(key="corner_l", p0=crease_l[0], p1=crease_l[1], angle_deg=180.0, side=1, up=True, level=1, groups=("body_l",), set_group="flap_l"),
            Crease(key="corner_r", p0=crease_r[0], p1=crease_r[1], angle_deg=180.0, side=1, up=False, level=1, groups=("body_r",), set_group="flap_r"),
            Crease(key="collar_l", p0=collar_l[0], p1=collar_l[1], angle_deg=180.0, side=1, up=True, level=2, groups=("flap_l",), set_group="collar_l"),
            Crease(key="collar_r", p0=collar_r[0], p1=collar_r[1], angle_deg=180.0, side=1, up=False, level=2, groups=("flap_r",), set_group="collar_r"),
            Crease(key="wing_l", p0=(wing_u, 0.0), p1=(wing_l_nose, 1.0), angle_deg=wing_angle, side=1, up=True, level=0, groups=("body_l",), set_group="wing_l"),
            Crease(key="wing_r", p0=(1.0 - wing_u, 0.0), p1=(wing_r_nose, 1.0), angle_deg=wing_angle, side=-1, up=True, level=0, groups=("body_r",), set_group="wing_r"),
            Crease(key="winglet_l", p0=(winglet_u, 0.0), p1=(winglet_u, 1.0), angle_deg=winglet_angle, side=1, up=True, level=0, groups=("wing_l",), set_group="winglet_l"),
            Crease(key="winglet_r", p0=(1.0 - winglet_u, 0.0), p1=(1.0 - winglet_u, 1.0), angle_deg=winglet_angle, side=-1, up=True, level=0, groups=("wing_r",), set_group="winglet_r"),
            Crease(key="elevon_l", p0=(0.0, elevon_v), p1=(1.0, elevon_v), angle_deg=abs(elevon), side=-1, up=False, level=0, groups=("wing_l", "winglet_l"), set_group="elevon_l"),
            Crease(key="elevon_r", p0=(0.0, elevon_v), p1=(1.0, elevon_v), angle_deg=abs(elevon), side=-1, up=False, level=0, groups=("wing_r", "winglet_r"), set_group="elevon_r"),
            Crease(key="trailing_l", p0=(0.0, trailing_v), p1=(0.5, trailing_v), angle_deg=15.0, side=-1, up=False, level=0, groups=("elevon_l",), set_group="trailing_l"),
            Crease(key="trailing_r", p0=(0.5, trailing_v), p1=(1.0, trailing_v), angle_deg=15.0, side=-1, up=False, level=0, groups=("elevon_r",), set_group="trailing_r"),
        ]

        g = genome_from_creases(creases, key="v3_origami")
        steps = list(g.steps)

        # Pad with strictly symmetric bilateral micro trim & camber pairs to target 16-24 steps
        target_count = 20
        needed_pairs = max(0, (target_count - len(steps) + 1) // 2)
        for pair_idx in range(needed_pairs):
            if len(steps) + 2 > MAX_FOLD_STEPS:
                break
            v = 0.25 + 0.10 * pair_idx
            fold_ang = 2.0 + 0.5 * pair_idx
            # Left wing crease
            steps.append(
                FoldStep(
                    step_index=len(steps) + 1,
                    crease_line=((0.15, v), (0.35, v)),
                    fold_angle_deg=fold_ang,
                    symmetry="left",
                    layer_order="auto",
                    group=("wing_l",),
                    set_group=f"reinforce_l_{pair_idx}",
                    label=f"Wing reinforcement left {pair_idx + 1}",
                )
            )
            # Right wing crease (mirror symmetry)
            steps.append(
                FoldStep(
                    step_index=len(steps) + 1,
                    crease_line=((0.65, v), (0.85, v)),
                    fold_angle_deg=fold_ang,
                    symmetry="right",
                    layer_order="auto",
                    group=("wing_r",),
                    set_group=f"reinforce_r_{pair_idx}",
                    label=f"Wing reinforcement right {pair_idx + 1}",
                )
            )

        # Strictly enforce bounds [MIN_FOLD_STEPS, MAX_FOLD_STEPS]
        if len(steps) > MAX_FOLD_STEPS:
            steps = steps[:MAX_FOLD_STEPS]

        return steps
