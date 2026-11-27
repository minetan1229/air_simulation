"""Ablation check for AeroParams freeze profile (48 -> 14 core fields).

Compares full AeroParams evaluation against AeroParams.frozen_profile() (where
all 21 ablation-candidate C-group fields are disabled/zeroed) across a suite of
representative paper-airplane configurations and verifies Spearman rank
correlation and aerodynamic polar stability.
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

    alpha_deg = 6.0
    speed = 8.0
    mach = speed / 340.3
    reynolds = 5.0e4

    ld_full: List[float] = []
    ld_frozen: List[float] = []
    max_rel_diff = 0.0

    for name, core in archetypes:
        p_full = AeroParams.from_core(core, compressible=True)
        p_frozen = AeroParams.frozen_profile(core)

        c_full = evaluate_coefficients(
            np, p_full, alpha_deg=alpha_deg, beta_deg=0.0, p_rate=0.0, q_rate=0.0, r_rate=0.0,
            speed=speed, reynolds=reynolds, mach=mach, ground_height_m=1.5,
        )
        c_frozen = evaluate_coefficients(
            np, p_frozen, alpha_deg=alpha_deg, beta_deg=0.0, p_rate=0.0, q_rate=0.0, r_rate=0.0,
            speed=speed, reynolds=reynolds, mach=mach, ground_height_m=1.5,
        )
        r_full = float(c_full["cl"]) / max(float(c_full["cd"]), 1e-9)
        r_frozen = float(c_frozen["cl"]) / max(float(c_frozen["cd"]), 1e-9)
        ld_full.append(r_full)
        ld_frozen.append(r_frozen)
        rel = abs(r_full - r_frozen) / max(abs(r_full), 1e-9)
        max_rel_diff = max(max_rel_diff, rel)

    rho = _spearman_rank_corr(ld_full, ld_frozen)
    return {
        "core_count": len(CORE_AERO_FIELDS),
        "internal_count": len(INTERNAL_AERO_FIELDS),
        "ablation_count": len(ABLATION_CANDIDATE_AERO_FIELDS),
        "spearman_rho": rho,
        "max_ld_rel_diff": max_rel_diff,
        "ld_full": ld_full,
        "ld_frozen": ld_frozen,
    }


if __name__ == "__main__":
    res = run_ablation()
    print(
        f"Partition: core={res['core_count']}, internal={res['internal_count']}, "
        f"ablation={res['ablation_count']}"
    )
    print(
        f"Spearman rank correlation (full vs frozen): {res['spearman_rho']:.6f}, "
        f"max L/D rel diff: {res['max_ld_rel_diff']:.6e}"
    )
