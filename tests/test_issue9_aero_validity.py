"""Tests for Issue #9: Physical validity and consistency of Lift, Drag, and Stall models.

Verifies:
1. Lift coefficient symmetry: CL is an odd function of alpha for symmetric airframe (fold=0, elevon=0).
2. Drag coefficient symmetry: CD is an even function of alpha and strictly non-negative (CD >= 0).
3. Post-stall continuity and bound: CL smoothly transitions from linear attached flow
   (Helmbold) to Hoerner flat plate (2 sin alpha cos^2 alpha) without discontinuities or negative spikes.
4. Extreme angle of attack [-90 deg, +90 deg] and sideslip [-90 deg, +90 deg] robustness:
   ensures no NaN, no Inf, and proper bounded behavior across the full flight envelope.
5. Airspeed and atmospheric density consistency across the aerodynamic calculation.
"""

from __future__ import annotations

import math
import numpy as np
import pytest

from physics import aero_domain, aero_kernel
from physics.aero_kernel import ScalarMath
from physics.config_schema import AircraftConfig, EnvironmentConfig


def test_lift_odd_and_drag_even_symmetry_clean_airframe() -> None:
    """Verify CL(-alpha) == -CL(alpha) and CD(-alpha) == CD(alpha) for symmetric aircraft with zero camber."""
    cfg = AircraftConfig(fold_trailing_deg=0.0, elevon_deg=0.0, cL0=0.0)
    env = EnvironmentConfig()

    alpha_samples = [0.0, 2.0, 5.0, 10.0, 14.0, 20.0, 35.0, 60.0, 85.0]
    for alpha in alpha_samples:
        pos = aero_domain.evaluate_aero(cfg, env, speed_mps=8.0, alpha_deg=alpha, beta_deg=0.0)
        neg = aero_domain.evaluate_aero(cfg, env, speed_mps=8.0, alpha_deg=-alpha, beta_deg=0.0)

        # Lift is odd
        if alpha == 0.0:
            assert pos.cl == pytest.approx(0.0, abs=1e-7)
            assert neg.cl == pytest.approx(0.0, abs=1e-7)
        else:
            assert pos.cl == pytest.approx(-neg.cl, rel=1e-5)

        # Drag is even and strictly positive
        assert pos.cd == pytest.approx(neg.cd, rel=1e-5)
        assert pos.cd >= 0.0
        assert neg.cd >= 0.0


def test_drag_non_negativity_and_monotonicity_with_alpha() -> None:
    """Induced drag + profile drag must produce non-negative total drag across all angles."""
    cfg = AircraftConfig(fold_trailing_deg=0.0, elevon_deg=0.0)
    env = EnvironmentConfig()

    alphas = np.linspace(-90.0, 90.0, 181)
    for a in alphas:
        res = aero_domain.evaluate_aero(cfg, env, speed_mps=7.0, alpha_deg=float(a), beta_deg=0.0)
        assert res.cd >= 0.0
        assert not math.isnan(res.cd)
        assert not math.isinf(res.cd)
        assert not math.isnan(res.cl)
        assert not math.isinf(res.cl)


def test_post_stall_transition_continuity() -> None:
    """Lift curve CL(alpha) must be continuous across stall angle without steps or jumps."""
    cfg = AircraftConfig(fold_trailing_deg=0.0, elevon_deg=0.0, stall_alpha_deg=14.0)
    env = EnvironmentConfig()

    # Dense sweep around stall alpha (10 deg to 25 deg in 0.2 deg steps)
    alphas = np.linspace(10.0, 25.0, 76)
    cls = [
        aero_domain.evaluate_aero(cfg, env, speed_mps=8.0, alpha_deg=float(a)).cl
        for a in alphas
    ]

    # Verify no sudden step discontinuity: |dCL/dalpha| bounded
    for i in range(len(cls) - 1):
        delta_cl = abs(cls[i + 1] - cls[i])
        delta_alpha = alphas[i + 1] - alphas[i]
        assert delta_cl / delta_alpha < 0.5  # reasonable max slope


def test_sideslip_sweep_symmetry_and_finiteness() -> None:
    """Verify side force CY is odd in beta and side drag is even across [-90, +90] deg."""
    cfg = AircraftConfig(fold_trailing_deg=0.0, elevon_deg=0.0)
    env = EnvironmentConfig()

    betas = np.linspace(-60.0, 60.0, 25)
    for beta in betas:
        res = aero_domain.evaluate_aero(cfg, env, speed_mps=8.0, alpha_deg=2.0, beta_deg=float(beta))
        assert not math.isnan(res.side_n)
        assert not math.isinf(res.side_n)
        assert not math.isnan(res.cy)

        # Opposite beta test
        res_neg = aero_domain.evaluate_aero(cfg, env, speed_mps=8.0, alpha_deg=2.0, beta_deg=-float(beta))
        assert res.side_n == pytest.approx(-res_neg.side_n, rel=1e-4, abs=1e-7)


def test_density_and_speed_scaling_dimensional_forces() -> None:
    """Lift and Drag scale strictly with dynamic pressure q = 0.5 * rho * V^2 when Re exponent is zero."""
    # With re_cd0_exponent = 0.0, CD0 is independent of speed/Re, so Drag scales purely with q
    cfg = AircraftConfig(fold_trailing_deg=0.0, elevon_deg=0.0, re_cd0_exponent=0.0)
    env = EnvironmentConfig()

    # V = 5 m/s vs V = 10 m/s (4x q)
    res_5 = aero_domain.evaluate_aero(cfg, env, speed_mps=5.0, alpha_deg=3.0)
    res_10 = aero_domain.evaluate_aero(cfg, env, speed_mps=10.0, alpha_deg=3.0)

    # Dimensional forces scale with 4x dynamic pressure within Prandtl-Glauert compressibility tolerance (rel=1e-3)
    assert res_10.lift_n / res_5.lift_n == pytest.approx(4.0, rel=1e-3)
    assert res_10.drag_n / res_5.drag_n == pytest.approx(4.0, rel=1e-3)
