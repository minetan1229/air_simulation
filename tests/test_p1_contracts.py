"""Regression tests for Phase 2 contracts: P1-1 (Environment ISA & clamp) and P1-2 (Config clamping).

Fixates:
- P1-1: Environment updates under isa_mode=True derive temperature/pressure from ISA and clamp ranges.
- P1-2: Strict validation and clamping for physical bounds and throw_style.
"""

from __future__ import annotations

import pytest

from api.core import merge_environment
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig


def test_p1_1_isa_mode_derives_temperature_and_pressure() -> None:
    """When isa_mode=True, temperature and pressure are derived from altitude, not manual values."""
    base = EnvironmentConfig(altitude_m=0.0, isa_mode=True)
    update = {
        "isa_mode": True,
        "altitude_m": 1000.0,
        "temperature_K": 350.0,
        "pressure_Pa": 50000.0,
    }
    result = merge_environment(update, base)

    assert result.isa_mode is True
    assert result.altitude_m == 1000.0
    # Standard ISA at 1000m is ~281.65 K, NOT 350.0 K
    assert 280.0 < result.temperature_K < 283.0
    # Standard ISA at 1000m is ~89874 Pa, NOT 50000.0 Pa
    assert 89000.0 < result.pressure_Pa < 91000.0


def test_p1_1_non_isa_allows_manual_temperature_and_pressure() -> None:
    """Explicitly passing isa_mode=False allows custom temperature and pressure."""
    base = EnvironmentConfig(altitude_m=0.0, isa_mode=True)
    update = {
        "isa_mode": False,
        "temperature_K": 310.0,
        "pressure_Pa": 95000.0,
    }
    result = merge_environment(update, base)

    assert result.isa_mode is False
    assert result.temperature_K == pytest.approx(310.0)
    assert result.pressure_Pa == pytest.approx(95000.0)


def test_p1_2_aircraft_config_clamp_ranges_validates_physical_bounds() -> None:
    """AircraftConfig clamp_ranges ensures bounds on geometry and aerodynamic coefficients."""
    extreme_cfg = AircraftConfig(
        wingspan_m=10.0,        # exceeds max 5.0m
        wing_area_m2=5.0,       # exceeds max 1.0m^2
        mass_kg=10.0,           # exceeds max 1.0kg
        cLalpha_per_rad=100.0,  # exceeds max 12.0
    )
    clamped = extreme_cfg.clamp_ranges()

    assert clamped.wingspan_m <= 5.0
    assert clamped.wing_area_m2 <= 1.0
    assert clamped.mass_kg <= 1.0
    assert clamped.cLalpha_per_rad <= 12.0


def test_p1_2_launch_config_clamp_ranges_validates_throw_style() -> None:
    """LaunchConfig clamp_ranges validates launch scalars and rejects unknown throw styles."""
    cfg = LaunchConfig(
        launch_speed=200.0,       # exceeds max 150.0 m/s
        launch_angle_deg=120.0,   # exceeds max 89.0 deg
        throw_style="direct",
    )
    clamped = cfg.clamp_ranges()

    assert clamped.launch_speed <= 150.0
    assert clamped.launch_angle_deg <= 89.0

    # Rejection of unknown throw style
    with pytest.raises(ValueError, match="unknown throw_style 'invalid_style'"):
        bad_cfg = LaunchConfig(throw_style="invalid_style")
        bad_cfg.clamp_ranges()
