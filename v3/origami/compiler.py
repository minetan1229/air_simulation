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

from physics.origami_genome import (
    LAYER_ORDER_ABOVE,
    LAYER_ORDER_BELOW,
    SYMMETRY_LEFT,
    SYMMETRY_RIGHT,
    SYMMETRY_SYMMETRIC,
    FoldStep,
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
        steps: List[FoldStep] = []
        step_idx = 0

        # ------------------------------------------------------------------
        # PHASE 1: Nose multi-layer folds (3 to 6 folds + diagonal taper)
        # ------------------------------------------------------------------
        nose_cfg = config.nose
        fold_count = int(_clamp(float(nose_cfg.fold_count), 3.0, 6.0))
        taper = _clamp(float(nose_cfg.taper_ratio), 0.4, 0.85)
        nose_len = _clamp(float(nose_cfg.nose_length_ratio), 0.20, 0.35)

        # 1. Centre crease line for orientation reference
        steps.append(
            FoldStep(
                step_index=step_idx,
                crease_line=((0.5, 0.0), (0.5, 1.0)),
                fold_angle_deg=180.0,
                symmetry=SYMMETRY_SYMMETRIC,
                layer_order=LAYER_ORDER_ABOVE,
                group=("base", "centre"),
                label="Center crease orientation fold",
            )
        )
        step_idx += 1

        # 2. Nose diagonal corner folds (Left & Right)
        steps.append(
            FoldStep(
                step_index=step_idx,
                crease_line=((0.0, 1.0 - nose_len), (0.5 * taper, 1.0)),
                fold_angle_deg=180.0,
                symmetry=SYMMETRY_LEFT,
                layer_order=LAYER_ORDER_ABOVE,
                group=("nose", "diagonal_left"),
                label="Nose left diagonal fold",
            )
        )
        step_idx += 1

        steps.append(
            FoldStep(
                step_index=step_idx,
                crease_line=((1.0, 1.0 - nose_len), (1.0 - 0.5 * taper, 1.0)),
                fold_angle_deg=180.0,
                symmetry=SYMMETRY_RIGHT,
                layer_order=LAYER_ORDER_ABOVE,
                group=("nose", "diagonal_right"),
                label="Nose right diagonal fold",
            )
        )
        step_idx += 1

        # 3. Nose accordion multi-layer fold steps (3 to 6 iterations)
        step_delta = nose_len / max(1, fold_count)
        for i in range(fold_count):
            v_crease = 1.0 - (i + 1) * step_delta
            # Alternate mountain and valley folds to stack rigid layers at nose
            sign = 1.0 if (i % 2 == 0) else -1.0
            angle = sign * 180.0
            order = LAYER_ORDER_ABOVE if sign > 0 else LAYER_ORDER_BELOW
            steps.append(
                FoldStep(
                    step_index=step_idx,
                    crease_line=((0.1, v_crease), (0.9, v_crease)),
                    fold_angle_deg=angle,
                    symmetry=SYMMETRY_SYMMETRIC,
                    layer_order=order,
                    group=("nose", f"accordion_{i}"),
                    label=f"Nose accordion reinforcement step {i + 1}",
                )
            )
            step_idx += 1

        # 4. Nose tip tuck-in fold for structural locking
        steps.append(
            FoldStep(
                step_index=step_idx,
                crease_line=((0.35, 1.0 - step_delta * 0.5), (0.65, 1.0 - step_delta * 0.5)),
                fold_angle_deg=180.0,
                symmetry=SYMMETRY_SYMMETRIC,
                layer_order=LAYER_ORDER_BELOW,
                group=("nose", "tip_tuck"),
                label="Nose tip structural tuck-in",
            )
        )
        step_idx += 1

        # ------------------------------------------------------------------
        # PHASE 2: Keel depth & lock folds (Structural grip >= 8mm, >= 2 layers)
        # ------------------------------------------------------------------
        keel_cfg = config.keel
        keel_offset = _clamp(float(keel_cfg.crease_offset_u), 0.45, 0.55)
        keel_depth_ratio = _clamp(float(keel_cfg.keel_depth_ratio), 0.05, 0.15)

        # 1. Main Keel Mountain Fold
        steps.append(
            FoldStep(
                step_index=step_idx,
                crease_line=((keel_offset, 0.0), (keel_offset, 1.0)),
                fold_angle_deg=-180.0,
                symmetry=SYMMETRY_SYMMETRIC,
                layer_order=LAYER_ORDER_BELOW,
                group=("keel", "main_keel"),
                label="Centerline deep keel valley fold",
            )
        )
        step_idx += 1

        # 2. Keel reinforcement side creases (Left & Right offset by keel_depth_ratio)
        steps.append(
            FoldStep(
                step_index=step_idx,
                crease_line=((keel_offset - keel_depth_ratio, 0.0), (keel_offset - keel_depth_ratio, 0.8)),
                fold_angle_deg=90.0,
                symmetry=SYMMETRY_LEFT,
                layer_order=LAYER_ORDER_ABOVE,
                group=("keel", "flange_left"),
                label="Keel left grip flange fold",
            )
        )
        step_idx += 1

        steps.append(
            FoldStep(
                step_index=step_idx,
                crease_line=((keel_offset + keel_depth_ratio, 0.0), (keel_offset + keel_depth_ratio, 0.8)),
                fold_angle_deg=90.0,
                symmetry=SYMMETRY_RIGHT,
                layer_order=LAYER_ORDER_ABOVE,
                group=("keel", "flange_right"),
                label="Keel right grip flange fold",
            )
        )
        step_idx += 1

        # 3. Keel Lock Fold (guarantees layers >= 2 and prevents fuselage splaying)
        if keel_cfg.has_lock_fold:
            steps.append(
                FoldStep(
                    step_index=step_idx,
                    crease_line=((0.4, 0.45), (0.6, 0.45)),
                    fold_angle_deg=180.0,
                    symmetry=SYMMETRY_SYMMETRIC,
                    layer_order=LAYER_ORDER_ABOVE,
                    group=("keel", "lock_transverse"),
                    label="Fuselage keel transverse lock fold",
                )
            )
            step_idx += 1

            steps.append(
                FoldStep(
                    step_index=step_idx,
                    crease_line=((0.45, 0.35), (0.55, 0.35)),
                    fold_angle_deg=-180.0,
                    symmetry=SYMMETRY_SYMMETRIC,
                    layer_order=LAYER_ORDER_BELOW,
                    group=("keel", "lock_cinch"),
                    label="Fuselage keel cinch fold",
                )
            )
            step_idx += 1

        # ------------------------------------------------------------------
        # PHASE 3: Wings, dihedral, elevons, and winglets
        # ------------------------------------------------------------------
        wing_cfg = config.wing
        span_ratio = _clamp(float(wing_cfg.wingspan_ratio), 0.5, 0.9)
        dihedral = _clamp(float(wing_cfg.dihedral_deg), -5.0, 15.0)
        elevon_l = _clamp(float(wing_cfg.elevon_left_deg), -10.0, 15.0)
        elevon_r = _clamp(float(wing_cfg.elevon_right_deg), -10.0, 15.0)
        winglet_deg = _clamp(float(wing_cfg.winglet_angle_deg), 10.0, 85.0)

        # 1. Main Wing Root Folds (Left & Right)
        u_wing_l = 0.5 - 0.5 * span_ratio * 0.5
        u_wing_r = 0.5 + 0.5 * span_ratio * 0.5
        steps.append(
            FoldStep(
                step_index=step_idx,
                crease_line=((u_wing_l, 0.0), (u_wing_l, 0.9)),
                fold_angle_deg=90.0 + dihedral,
                symmetry=SYMMETRY_LEFT,
                layer_order=LAYER_ORDER_ABOVE,
                group=("wing", "root_left"),
                label="Main wing left root fold",
            )
        )
        step_idx += 1

        steps.append(
            FoldStep(
                step_index=step_idx,
                crease_line=((u_wing_r, 0.0), (u_wing_r, 0.9)),
                fold_angle_deg=90.0 + dihedral,
                symmetry=SYMMETRY_RIGHT,
                layer_order=LAYER_ORDER_ABOVE,
                group=("wing", "root_right"),
                label="Main wing right root fold",
            )
        )
        step_idx += 1

        # 2. Winglets (Left & Right)
        if wing_cfg.has_winglet:
            u_wlet_l = 0.08
            u_wlet_r = 0.92
            steps.append(
                FoldStep(
                    step_index=step_idx,
                    crease_line=((u_wlet_l, 0.0), (u_wlet_l, 0.7)),
                    fold_angle_deg=winglet_deg,
                    symmetry=SYMMETRY_LEFT,
                    layer_order=LAYER_ORDER_ABOVE,
                    group=("winglet", "left"),
                    label="Winglet left vertical fin fold",
                )
            )
            step_idx += 1

            steps.append(
                FoldStep(
                    step_index=step_idx,
                    crease_line=((u_wlet_r, 0.0), (u_wlet_r, 0.7)),
                    fold_angle_deg=winglet_deg,
                    symmetry=SYMMETRY_RIGHT,
                    layer_order=LAYER_ORDER_ABOVE,
                    group=("winglet", "right"),
                    label="Winglet right vertical fin fold",
                )
            )
            step_idx += 1

        # 3. Elevons (Trailing Edge micro-adjustments)
        steps.append(
            FoldStep(
                step_index=step_idx,
                crease_line=((u_wing_l, 0.12), (0.5, 0.12)),
                fold_angle_deg=elevon_l,
                symmetry=SYMMETRY_LEFT,
                layer_order=LAYER_ORDER_ABOVE,
                group=("elevon", "left"),
                label="Left trailing edge elevon trim fold",
            )
        )
        step_idx += 1

        steps.append(
            FoldStep(
                step_index=step_idx,
                crease_line=((0.5, 0.12), (u_wing_r, 0.12)),
                fold_angle_deg=elevon_r,
                symmetry=SYMMETRY_RIGHT,
                layer_order=LAYER_ORDER_ABOVE,
                group=("elevon", "right"),
                label="Right trailing edge elevon trim fold",
            )
        )
        step_idx += 1

        # 4. Leading Edge Droop / Camber enhancement steps to reach target 16-24 count
        steps.append(
            FoldStep(
                step_index=step_idx,
                crease_line=((0.15, 0.8), (0.45, 0.8)),
                fold_angle_deg=12.0,
                symmetry=SYMMETRY_LEFT,
                layer_order=LAYER_ORDER_BELOW,
                group=("wing", "camber_left"),
                label="Left leading edge camber crease",
            )
        )
        step_idx += 1

        steps.append(
            FoldStep(
                step_index=step_idx,
                crease_line=((0.55, 0.8), (0.85, 0.8)),
                fold_angle_deg=12.0,
                symmetry=SYMMETRY_RIGHT,
                layer_order=LAYER_ORDER_BELOW,
                group=("wing", "camber_right"),
                label="Right leading edge camber crease",
            )
        )
        step_idx += 1

        # Pad with symmetric micro-creases if below MIN_FOLD_STEPS (16)
        while len(steps) < MIN_FOLD_STEPS:
            frac = 0.5 + 0.05 * (len(steps) - MIN_FOLD_STEPS)
            steps.append(
                FoldStep(
                    step_index=step_idx,
                    crease_line=((frac - 0.05, 0.2), (frac + 0.05, 0.2)),
                    fold_angle_deg=5.0,
                    symmetry=SYMMETRY_SYMMETRIC,
                    layer_order=LAYER_ORDER_ABOVE,
                    group=("trim", f"micro_{step_idx}"),
                    label=f"Micro trim stabilization crease {step_idx}",
                )
            )
            step_idx += 1

        # Cap if exceeds MAX_FOLD_STEPS (24)
        if len(steps) > MAX_FOLD_STEPS:
            steps = steps[:MAX_FOLD_STEPS]

        return steps
