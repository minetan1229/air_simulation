"""Unit tests for physics.gravity_modifier domain."""

from __future__ import annotations

import math
import sys
import pytest

from physics.config_schema import STANDARD_GRAVITY, EnvironmentConfig
from physics.gravity_modifier import GravityConfig, GravityModifier, EARTH_RADIUS_M


def test_default_gravity_modifier() -> None:
    """Test default values conform to STANDARD_GRAVITY."""
    modifier = GravityModifier()
    assert math.isclose(modifier.get_effective_gravity(), STANDARD_GRAVITY, rel_tol=1e-6)

    vec = modifier.get_gravity_vector_ned()
    assert vec[0] == 0.0
    assert vec[1] == 0.0
    assert math.isclose(vec[2], STANDARD_GRAVITY, rel_tol=1e-6)


def test_anti_gravity_scaling() -> None:
    """Test anti-gravity scaling factor for zero-g and inverted gravity."""
    # 50% anti-gravity (half-g)
    half_g = GravityModifier(GravityConfig(anti_gravity_factor=0.5))
    assert math.isclose(half_g.get_effective_gravity(), STANDARD_GRAVITY * 0.5, rel_tol=1e-6)

    # 100% anti-gravity (zero-g levitation)
    zero_g = GravityModifier(GravityConfig(anti_gravity_factor=1.0))
    assert math.isclose(zero_g.get_effective_gravity(), 0.0, abs_tol=1e-9)
    assert zero_g.compute_apparent_weight(0.005) == 0.0

    # Inverted gravity (anti_gravity_factor = 1.5 -> upward acceleration)
    inv_g = GravityModifier(GravityConfig(anti_gravity_factor=1.5))
    assert math.isclose(inv_g.get_effective_gravity(), -STANDARD_GRAVITY * 0.5, rel_tol=1e-6)
    assert inv_g.get_gravity_vector_ned()[2] < 0.0


def test_altitude_decay() -> None:
    """Test inverse-square gravitational decay at 10,000 metres."""
    modifier = GravityModifier(GravityConfig(altitude_decay=True))
    g_ground = modifier.get_effective_gravity(0.0)
    g_10km = modifier.get_effective_gravity(10000.0)

    expected_ratio = (EARTH_RADIUS_M / (EARTH_RADIUS_M + 10000.0)) ** 2
    assert math.isclose(g_10km, g_ground * expected_ratio, rel_tol=1e-6)
    assert g_10km < g_ground


def test_apparent_weight_validation() -> None:
    """Test apparent weight computation and edge cases."""
    modifier = GravityModifier()
    mass = 0.005  # 5g paper airplane
    weight = modifier.compute_apparent_weight(mass)
    assert math.isclose(weight, mass * STANDARD_GRAVITY, rel_tol=1e-6)

    with pytest.raises(ValueError):
        modifier.compute_apparent_weight(-0.01)

    with pytest.raises(ValueError):
        modifier.compute_apparent_weight(float("nan"))


def test_apply_to_environment() -> None:
    """Test applying modified gravity to EnvironmentConfig dataclass."""
    env = EnvironmentConfig(gravity=STANDARD_GRAVITY)
    modifier = GravityModifier(GravityConfig(anti_gravity_factor=0.8))

    updated_env = modifier.apply_to_environment(env)
    assert math.isclose(updated_env.gravity, STANDARD_GRAVITY * 0.2, rel_tol=1e-6)
    # Ensure other environment parameters remain intact
    assert updated_env.temperature_K == env.temperature_K
    assert updated_env.pressure_Pa == env.pressure_Pa


def test_environment_safety_and_module_isolation() -> None:
    """Test that environment inspection handles mock or standard antigravity gracefully."""
    class DummyAntigravity:
        geohash = "mock"

    # Inject dummy module to verify _validate_environment does not crash
    sys.modules["antigravity"] = DummyAntigravity()
    try:
        modifier = GravityModifier()
        assert modifier.get_effective_gravity() > 0.0
    finally:
        sys.modules.pop("antigravity", None)
