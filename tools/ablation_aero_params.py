"""Ablation check for AeroParams freeze profile (48 -> 14 core fields).

Compares full AeroParams evaluation (including realistic non-zero C-group terms
across multiple flight regimes: nominal glide, low-Re near-ground, and nonzero
angular rates) against AeroParams.frozen_profile() where all 21 ablation
candidates are explicitly disabled/zeroed.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from physics.aero_kernel import (
    ABLATION_CANDIDATE_AERO_FIELDS,
    CORE_AERO_FIELDS,
    INTERNAL_AERO_FIELDS,
    AeroParams,
    CoreAeroParams,
    evaluate_coefficients,
)


def _spearman_rank_corr(a: List[float], b: List[float]) -> float:
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    da = ra - np.mean(ra)
    db = rb - np.mean(rb)
    denom = math.sqrt(float(np.sum(da * da) * np.sum(db * db)))
    return float(np.sum(da * db) / denom) if denom > 0 else 1.0


def run_ablation() -> Dict[str, object]:
    """Run ablation across 5 representative paper-airplane archetypes."""
    archetypes: List[Tuple[str, CoreAeroParams]] = [
        ("dart_fast", CoreAeroParams(area=0.014, chord=0.14, span=0.10, aspect=0.71, oswald=0.72, cL0=0.02, cl_alpha=1.45, cl_max=0.85, stall_alpha_deg=18.0, cd0=0.022, cm0=0.01, cm_alpha=-0.12, cm_q=-1.2, elevon_deg=1.5)),
        ("balanced_glider", CoreAeroParams(area=0.022, chord=0.11, span=0.20, aspect=1.82, oswald=0.78, cL0=0.05, cl_alpha=2.45, cl_max=0.95, stall_alpha_deg=14.0, cd0=0.025, cm0=0.02, cm_alpha=-0.18, cm_q=-1.8, elevon_deg=2.0)),
        ("high_aspect_floater", CoreAeroParams(area=0.026, chord=0.09, span=0.29, aspect=3.23, oswald=0.82, cL0=0.08, cl_alpha=3.35, cl_max=1.05, stall_alpha_deg=12.5, cd0=0.028, cm0=0.03, cm_alpha=-0.22, cm_q=-2.1, elevon_deg=1.0)),
        ("heavy_nose_dart", CoreAeroParams(area=0.012, chord=0.15, span=0.08, aspect=0.53, oswald=0.70, cL0=0.00, cl_alpha=1.15, cl_max=0.80, stall_alpha_deg=20.0, cd0=0.024, cm0=0.00, cm_alpha=-0.28, cm_q=-1.0, elevon_deg=0.5)),
        ("wide_delta", CoreAeroParams(area=0.019, chord=0.13, span=0.15, aspect=1.18, oswald=0.75, cL0=0.04, cl_alpha=1.95, cl_max=0.92, stall_alpha_deg=16.0, cd0=0.023, cm0=0.015, cm_alpha=-0.15, cm_q=-1.5, elevon_deg=1.8)),
    ]

    # Realistic active C-group perturbations (compressible, low-Re, cross-coupling, ground cushion)
    c_group_active = dict(
        compressible=True,
        low_re_enabled=True,
        cm_roll_rate=0.02,
        cm_yaw_rate=0.01,
        cl_pitch_rate=0.02,
        cl_yaw_rate=0.02,
        cn_roll_rate=-0.02,
        cn_pitch_rate=0.01,
        polhamus_kv=0.3,
        re_cd0_exponent=0.10,
        ground_cushion_lift=0.05,
        ground_pitch_coupling=0.02,
    )

    ld_full_nom: List[float] = []
    ld_frozen_nom: List[float] = []
    ld_full_edge: List[float] = []
    ld_frozen_edge: List[float] = []
    cm_deltas: List[float] = []

    for _, core in archetypes:
        p_full = AeroParams.from_core(core, **c_group_active)
        p_frozen = AeroParams.frozen_profile(core)

        # Regime 1: Nominal mid-flight glide
        c_full = evaluate_coefficients(
            np, p_full, alpha_deg=6.0, beta_deg=0.0, p_rate=0.0, q_rate=0.0, r_rate=0.0,
            speed=8.0, reynolds=5.0e4, mach=8.0 / 340.3, ground_height_m=1.5,
        )
        c_frozen = evaluate_coefficients(
            np, p_frozen, alpha_deg=6.0, beta_deg=0.0, p_rate=0.0, q_rate=0.0, r_rate=0.0,
            speed=8.0, reynolds=5.0e4, mach=8.0 / 340.3, ground_height_m=1.5,
        )
        ld_full_nom.append(float(c_full["cl"]) / max(float(c_full["cd"]), 1e-9))
        ld_frozen_nom.append(float(c_frozen["cl"]) / max(float(c_frozen["cd"]), 1e-9))

        # Regime 2: Low-Re, near-ground, non-zero angular rates
        e_full = evaluate_coefficients(
            np, p_full, alpha_deg=10.0, beta_deg=2.0, p_rate=1.0, q_rate=1.0, r_rate=0.5,
            speed=5.0, reynolds=1.5e4, mach=5.0 / 340.3, ground_height_m=0.10,
        )
        e_frozen = evaluate_coefficients(
            np, p_frozen, alpha_deg=10.0, beta_deg=2.0, p_rate=1.0, q_rate=1.0, r_rate=0.5,
            speed=5.0, reynolds=1.5e4, mach=5.0 / 340.3, ground_height_m=0.10,
        )
        ld_full_edge.append(float(e_full["cl"]) / max(float(e_full["cd"]), 1e-9))
        ld_frozen_edge.append(float(e_frozen["cl"]) / max(float(e_frozen["cd"]), 1e-9))
        cm_deltas.append(abs(float(e_full["cm"]) - float(e_frozen["cm"])))

    rho_nom = _spearman_rank_corr(ld_full_nom, ld_frozen_nom)
    rho_edge = _spearman_rank_corr(ld_full_edge, ld_frozen_edge)
    max_rel_nom = max(abs(a - b) / max(abs(a), 1e-9) for a, b in zip(ld_full_nom, ld_frozen_nom))
    return {
        "core_count": len(CORE_AERO_FIELDS),
        "internal_count": len(INTERNAL_AERO_FIELDS),
        "ablation_count": len(ABLATION_CANDIDATE_AERO_FIELDS),
        "spearman_rho": rho_nom,
        "spearman_rho_edge": rho_edge,
        "max_ld_rel_diff": max_rel_nom,
        "max_cm_abs_diff_edge": max(cm_deltas),
        "ld_full": ld_full_nom,
        "ld_frozen": ld_frozen_nom,
    }


if __name__ == "__main__":
    res = run_ablation()
    print(
        f"Partition: core={res['core_count']}, internal={res['internal_count']}, "
        f"ablation={res['ablation_count']}"
    )
    print(
        f"Spearman rank corr: nominal={res['spearman_rho']:.4f}, edge={res['spearman_rho_edge']:.4f} | "
        f"nominal max L/D rel diff={res['max_ld_rel_diff']:.4f}, edge max |dCm|={res['max_cm_abs_diff_edge']:.4f}"
    )
