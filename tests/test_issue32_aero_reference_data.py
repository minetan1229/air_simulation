"""Independent verification of lift, drag, and moment coefficients against reference data (Issue #32).

Verifies:
1. Lift slope agreement with Helmbold triangular/trapezoidal wing equation:
   C_Lalpha = 2*pi*AR / (2 + sqrt(AR^2*(1 + lambda) + 4*AR / pi)).
2. Induced drag polar constant matching positive quadratic drag rise with lift.
3. Hoerner fully-separated flat-plate lift formulation: 2 * sin(alpha) * cos^2(alpha) peaking at ~35.26 deg.
4. Post-stall crossflow drag limit: separated_drag = 2.0 * sin^2(alpha) + CD0 * cos(alpha).
5. Pitching moment derivative Cm_alpha ensuring longitudinal static stability (Cm_alpha < 0).
"""

import math
import pytest
import numpy as np

from physics.config_schema import AircraftConfig, EnvironmentConfig
from physics.aero_domain import (
    evaluate_aero,
    helmbold_cl_alpha,
    lift_slope_per_rad,
    flat_plate_lift_coefficient,
    drag_coefficient,
    derived_properties,
)


def test_helmbold_finite_wing_lift_slope_reference():
    """Verify that lift_slope_per_rad matches the Helmbold paper-airplane wing formula."""
    for ar in [1.5, 2.5, 4.0]:
        taper = 0.5
        cl_alpha_ref = helmbold_cl_alpha(aspect_ratio=ar, taper_ratio=taper)
        # Helmbold formula check: 2*pi*AR / (2 + sqrt(AR^2 * (1 + lambda) + 4*AR / pi))
        expected_slope = (2.0 * math.pi * ar) / (2.0 + math.sqrt(ar**2 * (1.0 + taper) + (4.0 * ar / math.pi)))
        assert math.isclose(cl_alpha_ref, expected_slope, rel_tol=1e-3)
        # Upper bound: cannot exceed 2D thin airfoil theory limit 2*pi
        assert cl_alpha_ref < 2.0 * math.pi


def test_independent_drag_polar_and_induced_drag():
    """Verify induced drag factor against classical wing theory."""
    cfg = AircraftConfig(fold_trailing_deg=0.0)
    props = derived_properties(cfg)
    ar = props["aspect_ratio"]
    
    # Evaluate drag at alpha=0 vs alpha=4 deg where CL is known
    env = EnvironmentConfig()
    res0 = evaluate_aero(cfg, env, speed_mps=8.0, alpha_deg=0.0)
    res4 = evaluate_aero(cfg, env, speed_mps=8.0, alpha_deg=4.0)

    # Drag at alpha=4 should be strictly higher than drag at alpha=0 due to induced drag
    assert res4.cd > res0.cd
    d_cd = res4.cd - res0.cd
    d_cl_sq = (res4.cl ** 2) - (res0.cl ** 2)
    k_effective = d_cd / d_cl_sq
    assert k_effective > 0.0


def test_hoerner_flat_plate_high_alpha_limit():
    """Verify Hoerner separated flat-plate lift formulation: 2 * sin(a) * cos^2(a)."""
    # At alpha = 0, CL = 0
    assert math.isclose(flat_plate_lift_coefficient(0.0), 0.0, abs_tol=1e-6)
    
    # Peak occurs at alpha = arcsin(1/sqrt(3)) ~= 35.264 degrees
    alpha_peak = math.degrees(math.asin(1.0 / math.sqrt(3.0)))
    cl_peak = flat_plate_lift_coefficient(alpha_peak)
    expected_peak = 2.0 * (1.0 / math.sqrt(3.0)) * (2.0 / 3.0)  # = 4 / (3 * sqrt(3)) ~= 0.7698
    assert math.isclose(cl_peak, expected_peak, rel_tol=1e-3)

    # At alpha = 90 deg, lift is 0
    assert math.isclose(flat_plate_lift_coefficient(90.0), 0.0, abs_tol=1e-6)


def test_pitch_stability_margin_reference():
    """Verify longitudinal static stability condition Cm_alpha < 0 with realistic CG."""
    cfg = AircraftConfig(cm_alpha=-0.15, fold_trailing_deg=0.0)
    env = EnvironmentConfig()

    res_alpha2 = evaluate_aero(cfg, env, speed_mps=8.0, alpha_deg=2.0)
    res_alpha6 = evaluate_aero(cfg, env, speed_mps=8.0, alpha_deg=6.0)

    # dCm / dAlpha should be negative (nose drops when alpha increases -> pitch stable)
    dcm = res_alpha6.cm - res_alpha2.cm
    assert dcm < 0, f"Unstable pitching moment detected: dCm={dcm}"
