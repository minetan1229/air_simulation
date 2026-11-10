"""
Unit and physical verification tests for:
1. Low-Aspect-Ratio (Low-AR) tip stall delay scaling (Torres & Mueller 2004, DeVoria & Mohseni 2017).
2. Mainstream turbulence intensity suppression of LSB aerodynamic hysteresis (Hoffmann 1991, Marchman et al. 1988).
"""
import math
import pytest
from physics import aero_kernel
from physics.aero_kernel import ScalarMath
from physics.config_schema import AircraftConfig, EnvironmentConfig


def test_low_ar_stall_delay_scaling():
    """Verify low_ar_stall_delay mathematical behaviour across aspect ratios."""
    # High aspect ratio (e.g. glider AR=10): minimal stall delay (< 2 deg)
    delay_ar10 = aero_kernel.low_ar_stall_delay(10.0)
    assert 0.0 <= delay_ar10 <= 2.0

    # Low aspect ratio (e.g. dart paper plane AR=1.0): significant stall delay (~15 deg)
    delay_ar1 = aero_kernel.low_ar_stall_delay(1.0)
    assert 14.0 <= delay_ar1 <= 16.0

    # Extremely low aspect ratio (AR=0.5): capped at maximum delay (18 deg)
    delay_ar05 = aero_kernel.low_ar_stall_delay(0.5)
    assert math.isclose(delay_ar05, 18.0, abs_tol=1e-3)

    # Monotonicity: lower AR must produce greater or equal stall delay
    assert aero_kernel.low_ar_stall_delay(0.8) >= aero_kernel.low_ar_stall_delay(1.5)
    assert aero_kernel.low_ar_stall_delay(1.5) >= aero_kernel.low_ar_stall_delay(4.0)


def test_effective_stall_alpha_with_low_ar():
    """Verify effective_stall_alpha_deg integrates low-AR stall delay."""
    # When disabled (default False), stall angle is unmodified
    cfg_disabled = AircraftConfig(
        wingspan_m=0.1,
        aspect_ratio=0.5,
        stall_alpha_deg=14.0,
        step_trip_ratio=0.0,
        low_ar_stall_delay_enabled=False,
    )
    assert aero_kernel.effective_stall_alpha_deg(cfg_disabled) == 14.0

    # Low AR configuration (AR ~ 0.5) with low_ar_stall_delay_enabled=True
    cfg_low_ar = AircraftConfig(
        wingspan_m=0.1,
        aspect_ratio=0.5,
        stall_alpha_deg=14.0,
        step_trip_ratio=0.0,
        low_ar_stall_delay_enabled=True,
    )
    stall_eff_low_ar = aero_kernel.effective_stall_alpha_deg(cfg_low_ar)
    # Base 14 + delay (~18) => >= 25 deg
    assert stall_eff_low_ar >= 25.0

    # Glider configuration with high AR (AR = 8.0)
    cfg_high_ar = AircraftConfig(
        wingspan_m=0.4,
        aspect_ratio=8.0,
        stall_alpha_deg=14.0,
        step_trip_ratio=0.0,
        low_ar_stall_delay_enabled=True,
    )
    stall_eff_high_ar = aero_kernel.effective_stall_alpha_deg(cfg_high_ar)
    # Base 14 + small delay (< 3 deg)
    assert 14.0 <= stall_eff_high_ar <= 17.5


def test_turbulence_intensity_suppresses_lsb_hysteresis():
    """Verify mainstream turbulence suppresses LSB hysteresis loop according to Hoffmann (1991)."""
    alpha = 15.0
    alpha_dot_down = -10.0
    base_stall = 14.0
    mac = 0.08
    speed = 6.0
    reynolds = 12000.0

    # Calm air (Tu = 0.0): full hysteresis shift
    eff_stall_calm, _ = aero_kernel.lsb_dynamic_stall_hysteresis(
        ScalarMath,
        alpha_deg=alpha,
        alpha_dot_deg_s=alpha_dot_down,
        stall_alpha_deg=base_stall,
        mac_m=mac,
        speed_mps=speed,
        reynolds=reynolds,
        is_stalled_prev=True,
        turbulence_intensity=0.0,
    )
    shift_calm = abs(eff_stall_calm - base_stall)
    assert shift_calm >= 1.0

    # Mild turbulence (Tu = 0.015): hysteresis attenuated by ~ 1/e (~ 37%)
    eff_stall_mild, _ = aero_kernel.lsb_dynamic_stall_hysteresis(
        ScalarMath,
        alpha_deg=alpha,
        alpha_dot_deg_s=alpha_dot_down,
        stall_alpha_deg=base_stall,
        mac_m=mac,
        speed_mps=speed,
        reynolds=reynolds,
        is_stalled_prev=True,
        turbulence_intensity=0.015,
    )
    shift_mild = abs(eff_stall_mild - base_stall)
    assert shift_mild < shift_calm
    assert math.isclose(shift_mild, shift_calm * math.exp(-1.0), rel_tol=0.25)

    # Strong outdoor turbulence (Tu = 0.06): hysteresis almost completely suppressed (< 10%)
    eff_stall_strong, _ = aero_kernel.lsb_dynamic_stall_hysteresis(
        ScalarMath,
        alpha_deg=alpha,
        alpha_dot_deg_s=alpha_dot_down,
        stall_alpha_deg=base_stall,
        mac_m=mac,
        speed_mps=speed,
        reynolds=reynolds,
        is_stalled_prev=True,
        turbulence_intensity=0.06,
    )
    shift_strong = abs(eff_stall_strong - base_stall)
    assert shift_strong <= 0.15 * shift_calm
