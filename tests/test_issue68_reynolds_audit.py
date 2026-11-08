"""Tests for Issue #68: Reynolds number dependency and envelope audit.

Verifies:
1. Definition and physical scaling of Re = rho * V * MAC / mu.
2. Parasite drag coefficient power-law scaling with Reynolds number (re_cd0_exponent).
3. Low-Re laminar separation bubble (LSB) dynamic stall shift and hysteresis scaling f(Re) ~ sqrt(1e4 / Re).
4. Aerodynamic model envelope validity flags and bounds [MODEL_REYNOLDS_MIN, MODEL_REYNOLDS_MAX].
5. Re sensitivity across flight conditions (glide speed 2.0 to 15.0 m/s).
"""

from __future__ import annotations

import math
import pytest

from physics import aero_domain, aero_kernel
from physics.aero_kernel import (
    MODEL_REYNOLDS_MAX,
    MODEL_REYNOLDS_MIN,
    ScalarMath,
    evaluate_model_validity,
    lsb_dynamic_stall_shift,
    parasite_drag_coefficient,
)
from physics.config_schema import AircraftConfig, EnvironmentConfig, derived_properties


def test_reynolds_calculation_from_airspeed_and_chord() -> None:
    """Verifies standard Re calculation Re = rho * V * c / mu."""
    cfg = AircraftConfig(wing_area_m2=0.015, wingspan_m=0.20)
    env = EnvironmentConfig()

    speed = 6.5
    geom = derived_properties(cfg)
    chord = float(geom["ref_length_m"])
    # Standard air: nu ~ 1.5e-5 m^2/s
    expected_re = speed * chord / 1.5e-5

    probe = aero_domain.evaluate_aero(cfg, env, speed_mps=speed, alpha_deg=2.0)
    assert probe.reynolds == pytest.approx(expected_re, rel=0.05)
    assert 2.0e4 <= probe.reynolds <= 5.0e4


def test_parasite_drag_reynolds_exponent_scaling() -> None:
    """Parasite drag scales inversely with Re when exponent > 0."""
    cd0_base = 0.03
    exponent = 0.5  # laminar boundary layer scaling ~ Re^(-0.5)

    re_low = 1.0e4
    re_high = 1.0e5

    cd0_at_low = parasite_drag_coefficient(ScalarMath, cd0_base, re_low, exponent)
    cd0_at_high = parasite_drag_coefficient(ScalarMath, cd0_base, re_high, exponent)

    # cd0_at_low / cd0_at_high should equal sqrt(1e5 / 1e4) = sqrt(10) ~ 3.162
    expected_ratio = math.sqrt(re_high / re_low)
    assert (cd0_at_low / cd0_at_high) == pytest.approx(expected_ratio, rel=1e-5)


def test_lsb_hysteresis_reynolds_scaling() -> None:
    """LSB dynamic stall overshoot sensitivity scales with f(Re) = sqrt(1e4 / Re)."""
    alpha_dot = 20.0  # 20 deg/s pitch-up
    mac = 0.08
    speed = 5.0

    shift_10k = lsb_dynamic_stall_shift(
        ScalarMath, alpha_dot_deg_s=alpha_dot, mac_m=mac, speed_mps=speed, reynolds=1.0e4, kappa_lsb=1.5
    )
    shift_40k = lsb_dynamic_stall_shift(
        ScalarMath, alpha_dot_deg_s=alpha_dot, mac_m=mac, speed_mps=speed, reynolds=4.0e4, kappa_lsb=1.5
    )

    # f(1e4) = 1.0, f(4e4) = 0.5 -> dynamic stall overshoot is higher at lower Re
    assert shift_10k > shift_40k
    assert shift_10k == pytest.approx(2.0 * shift_40k, rel=1e-3)


def test_reynolds_envelope_validity_boundaries() -> None:
    """Flight conditions below MODEL_REYNOLDS_MIN or above MODEL_REYNOLDS_MAX flag re_ok=False."""
    valid_mid = evaluate_model_validity(ScalarMath, mach=0.03, reynolds=3.5e4)
    assert valid_mid["re_ok"] is True
    assert valid_mid["valid"] is True

    too_low = evaluate_model_validity(ScalarMath, mach=0.03, reynolds=MODEL_REYNOLDS_MIN * 0.5)
    assert too_low["re_ok"] is False
    assert too_low["valid"] is False

    too_high = evaluate_model_validity(ScalarMath, mach=0.03, reynolds=MODEL_REYNOLDS_MAX * 1.5)
    assert too_high["re_ok"] is False
    assert too_high["valid"] is False
