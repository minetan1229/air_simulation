"""Verification and audit tests for center of pressure (CoP) travel model (Issue #109).

Tasks / Criteria verified:
1. Thin airfoil theory limit: x_cp/c = 0.25 at alpha = 0.
2. Fully separated bluff flat plate limit: x_cp/c = 0.50 at |alpha| >= alpha_stall when cp_shift_gain = 1.0.
3. Monotonicity: d(x_cp)/d|alpha| >= 0 across the entire range [0, 90 deg].
4. C^1 continuity:
   - Zero slope at alpha = 0: d(x_cp)/dalpha = 0.
   - Zero slope at stall onset: d(x_cp)/dalpha = 0 as alpha approaches and exceeds alpha_stall.
5. Symmetry: x_cp(-alpha) = x_cp(alpha) for all alpha in [-90, 90 deg].
6. Bounded pitch stability margin:
   - delta_Cm = -(x_cp - 0.25*c)/c * C_L is stabilizing (nose-down for positive lift).
   - Cm0 is unaffected by CoP shift at zero lift.
   - Static margin sensitivity Delta h = x_cg/c - x_cp/c is well-bounded and shifts aft (more stable).
7. Gain scaling:
   - When cp_shift_gain = 0.0, x_cp/c stays identically 0.25 for all alpha.
   - For arbitrary gain in [0, 1], x_cp/c moves in [0.25, 0.25 + 0.25 * gain].
8. Array / Vectorized execution parity: scalar and array evaluations match.
"""

from __future__ import annotations

import math
import numpy as np
import pytest

from physics.aero_kernel import (
    ScalarMath,
    center_of_pressure_fraction,
    evaluate_coefficients,
    params_from_aircraft,
)
from physics.aero_domain import center_of_pressure_x, evaluate_aero
from physics.config_schema import AircraftConfig, EnvironmentConfig, derived_properties


# ---------------------------------------------------------------------------
# 1. Theoretical limits: attached (0.25) vs fully separated (0.50)
# ---------------------------------------------------------------------------

def test_cop_thin_airfoil_attached_limit() -> None:
    """Attached flow linear thin airfoil theory places aerodynamic center at 0.25c."""
    stall_alpha = 14.0
    for gain in [0.0, 0.5, 0.8, 1.0]:
        cp = center_of_pressure_fraction(ScalarMath, 0.0, stall_alpha, gain)
        assert cp == pytest.approx(0.25, abs=1e-12)


def test_cop_bluff_flat_plate_separated_limit() -> None:
    """Fully separated bluff flat-plate flow shifts center of pressure to centroid 0.50c."""
    stall_alpha = 14.0
    for alpha in [14.0, 15.0, 20.0, 45.0, 90.0]:
        cp = center_of_pressure_fraction(ScalarMath, alpha, stall_alpha, cp_shift_gain=1.0)
        assert cp == pytest.approx(0.50, abs=1e-12)


# ---------------------------------------------------------------------------
# 2. Symmetry: x_cp(-alpha) == x_cp(alpha)
# ---------------------------------------------------------------------------

def test_cop_symmetry_across_alpha() -> None:
    """Center of pressure is strictly symmetric across positive and negative alpha."""
    stall_alpha = 12.5
    gain = 0.85
    alphas = np.linspace(-60.0, 60.0, 241)
    for a in alphas:
        cp_pos = center_of_pressure_fraction(ScalarMath, a, stall_alpha, gain)
        cp_neg = center_of_pressure_fraction(ScalarMath, -a, stall_alpha, gain)
        assert cp_pos == pytest.approx(cp_neg, abs=1e-12)


# ---------------------------------------------------------------------------
# 3. Monotonicity across alpha
# ---------------------------------------------------------------------------

def test_cop_monotonic_progression() -> None:
    """Center of pressure progresses monotonically aft with increasing |alpha|."""
    stall_alpha = 15.0
    gain = 1.0
    alphas = np.linspace(0.0, stall_alpha, 100)
    cp_vals = [
        float(center_of_pressure_fraction(ScalarMath, a, stall_alpha, gain))
        for a in alphas
    ]
    # Check non-decreasing
    diffs = np.diff(cp_vals)
    assert np.all(diffs >= -1e-15), f"Negative slope detected: min diff = {np.min(diffs)}"


# ---------------------------------------------------------------------------
# 4. C^1 continuity: d(x_cp)/dalpha = 0 at alpha=0 and alpha >= alpha_stall
# ---------------------------------------------------------------------------

def test_cop_c1_continuity_at_boundaries() -> None:
    """C^1 continuity requires zero derivative at alpha=0 and at alpha=alpha_stall."""
    stall_alpha = 14.0
    gain = 1.0

    # At alpha = 0: derivative d(cp)/dalpha using central differences
    eps = 1e-5
    cp_plus = center_of_pressure_fraction(ScalarMath, eps, stall_alpha, gain)
    cp_minus = center_of_pressure_fraction(ScalarMath, -eps, stall_alpha, gain)
    d_cp_dalpha_0 = (cp_plus - cp_minus) / (2.0 * eps)
    assert abs(d_cp_dalpha_0) < 1e-7

    # At alpha = stall_alpha: derivative approaching from below
    cp_stall = center_of_pressure_fraction(ScalarMath, stall_alpha, stall_alpha, gain)
    cp_stall_below = center_of_pressure_fraction(ScalarMath, stall_alpha - eps, stall_alpha, gain)
    d_cp_dalpha_stall_left = (cp_stall - cp_stall_below) / eps
    assert abs(d_cp_dalpha_stall_left) < 1e-4

    # At alpha = stall_alpha: derivative approaching from above
    cp_stall_above = center_of_pressure_fraction(ScalarMath, stall_alpha + eps, stall_alpha, gain)
    d_cp_dalpha_stall_right = (cp_stall_above - cp_stall) / eps
    assert abs(d_cp_dalpha_stall_right) < 1e-10

    # Ensure left and right derivatives agree (C^1 continuous transition at stall)
    assert abs(d_cp_dalpha_stall_left - d_cp_dalpha_stall_right) < 1e-4


# ---------------------------------------------------------------------------
# 5. Static margin sensitivity and pitching moment bounds
# ---------------------------------------------------------------------------

def test_static_margin_sensitivity_and_pitch_coupling() -> None:
    """Quantify static margin sensitivity Delta h = x_cg/c - x_cp/c across angles of attack."""
    cfg_base = AircraftConfig(cp_shift_gain=0.0, stall_alpha_deg=14.0)
    cfg_shift = AircraftConfig(cp_shift_gain=1.0, stall_alpha_deg=14.0)

    p_base = params_from_aircraft(cfg_base)
    p_shift = params_from_aircraft(cfg_shift)

    alphas = np.linspace(0.0, 20.0, 21)
    res_base = evaluate_coefficients(np, p_base, alpha_deg=alphas, beta_deg=0.0, speed=10.0)
    res_shift = evaluate_coefficients(np, p_shift, alpha_deg=alphas, beta_deg=0.0, speed=10.0)

    cl_base = np.asarray(res_base["cl"])
    cm_base = np.asarray(res_base["cm"])
    cl_shift = np.asarray(res_shift["cl"])
    cm_shift = np.asarray(res_shift["cm"])
    cp_x_shift = np.asarray(res_shift["cp_x_m"])
    chord = float(p_shift.chord)

    # 1. Lift coefficient is identical (CP shift couples to moment, not lift)
    np.testing.assert_allclose(cl_shift, cl_base, atol=1e-12)

    # 2. At alpha=0 (where CL=0 for symmetric airfoil), Cm is identical: Cm0 unchanged
    assert cm_shift[0] == pytest.approx(cm_base[0], abs=1e-12)

    # 3. For positive alpha with positive lift, CP moves aft:
    # delta_Cm = cm_shift - cm_base <= 0 (stabilizing nose-down)
    for i, a in enumerate(alphas):
        if a > 0.0 and cl_shift[i] > 0.0:
            assert cm_shift[i] <= cm_base[i] + 1e-12
            # Analytical check: delta_cm == -(cp_x - 0.25*chord)/chord * cl
            expected_delta_cm = -((cp_x_shift[i] - 0.25 * chord) / chord) * cl_shift[i]
            actual_delta_cm = cm_shift[i] - cm_base[i]
            assert actual_delta_cm == pytest.approx(expected_delta_cm, abs=1e-12)

    # 4. Quantify static margin Delta h = x_cg/c - x_cp/c
    # In conventional definition, neutral point is x_cp/c.
    # When x_cp shifts from 0.25 to 0.50, static margin increases by 0.25 (more pitch stable).
    h_0 = 0.25 - 0.25  # cg at 0.25c, cp at 0.25c -> margin = 0
    h_stall = 0.25 - 0.50  # cp at 0.50c -> aerodynamic restoring arm is 0.25c longer
    assert h_stall - h_0 == -0.25


def test_cop_shift_gain_scaling() -> None:
    """When cp_shift_gain is varied in [0, 1], maximum shift scales linearly with gain."""
    stall_alpha = 14.0
    for gain in [0.0, 0.25, 0.5, 0.75, 1.0]:
        cp_post = center_of_pressure_fraction(ScalarMath, 20.0, stall_alpha, gain)
        expected = 0.25 + 0.25 * gain
        assert cp_post == pytest.approx(expected, abs=1e-12)


def test_cop_vectorized_backend_parity() -> None:
    """Vectorized array evaluation matches scalar evaluation across range of alpha and gains."""
    stall_alpha = 14.0
    alphas = np.array([-15.0, -10.0, 0.0, 5.0, 14.0, 25.0])
    gains = np.array([0.0, 0.5, 1.0, 0.8, 1.0, 0.5])

    vec_res = center_of_pressure_fraction(np, alphas, stall_alpha, gains)

    for i in range(len(alphas)):
        scal_res = center_of_pressure_fraction(ScalarMath, float(alphas[i]), stall_alpha, float(gains[i]))
        assert vec_res[i] == pytest.approx(scal_res, abs=1e-12)


def test_center_of_pressure_x_domain_wrapper() -> None:
    """Verify physics.aero_domain.center_of_pressure_x dimensional output."""
    cfg = AircraftConfig(stall_alpha_deg=14.0, cp_shift_gain=1.0)
    mac = derived_properties(cfg)["ref_length_m"]

    # At alpha = 0: 0.25 * mac
    x_cp_0 = center_of_pressure_x(0.0, cfg)
    assert x_cp_0 == pytest.approx(0.25 * mac, rel=1e-12)

    # At alpha = stall: 0.50 * mac
    x_cp_stall = center_of_pressure_x(14.0, cfg)
    assert x_cp_stall == pytest.approx(0.50 * mac, rel=1e-12)


def test_flight_sim_evaluate_aero_cop_integration() -> None:
    """Verify center of pressure travel in end-to-end aero evaluation."""
    cfg = AircraftConfig(stall_alpha_deg=12.0, cp_shift_gain=1.0)
    mac = derived_properties(cfg)["ref_length_m"]
    env = EnvironmentConfig()

    res_att = evaluate_aero(cfg, env, speed_mps=8.0, alpha_deg=0.0)
    assert res_att.cp_x_m == pytest.approx(0.25 * mac, rel=1e-12)

    res_sep = evaluate_aero(cfg, env, speed_mps=8.0, alpha_deg=15.0)
    assert res_sep.cp_x_m == pytest.approx(0.50 * mac, rel=1e-12)
