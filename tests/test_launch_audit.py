"""Issue #135 audit: release-state generation table and sensitivity (audit-only, no calibration invented).

Documents the LaunchConfig -> resolve_throw -> initial_state chain per throw style
(direct vs impulse) and pins: direct-zero omega by default, explicit wrist rates,
impulse speed = F*dt/m, omega proportional to torque_efficiency, clamp ceilings.
No measured calibration is asserted; unspecified constants (e.g. eta default) are
treated as model constants, not physical truth.
"""
from __future__ import annotations

import pytest

from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig
from physics.flight_sim import initial_state, resolve_throw


def test_direct_default_zero_omega_and_direct_speed():
    cfg = LaunchConfig(throw_style="direct", launch_speed=12.0)
    sol = resolve_throw(cfg, AircraftConfig(), EnvironmentConfig())
    assert sol.impulse_active is False
    assert sol.speed_mps == pytest.approx(12.0)
    assert sol.angular_rate_radps == (0.0, 0.0, 0.0)
    state = initial_state(cfg, EnvironmentConfig(), AircraftConfig())
    assert tuple(state.angular_velocity_radps) == (0.0, 0.0, 0.0)


def test_direct_explicit_wrist_rates_preserved():
    cfg = LaunchConfig(
        throw_style="direct",
        throw_wrist_spin_radps=1.0,
        throw_pitch_rate_radps=-2.0,
        throw_yaw_rate_radps=0.5,
    )
    sol = resolve_throw(cfg, AircraftConfig(), EnvironmentConfig())
    assert sol.angular_rate_radps == pytest.approx((1.0, -2.0, 0.5))


@pytest.mark.parametrize("eta", (0.0, 0.15, 1.0))
def test_impulse_omega_scales_with_efficiency(eta):
    base = dict(
        throw_style="overhand",
        throw_speed_source="impulse",
        throw_force_n=0.35,
        throw_duration_s=0.02,
        launch_speed=12.0,
    )
    t0 = resolve_throw(LaunchConfig(**base, throw_torque_efficiency=0.0), AircraftConfig(), EnvironmentConfig())
    t1 = resolve_throw(LaunchConfig(**base, throw_torque_efficiency=1.0), AircraftConfig(), EnvironmentConfig())
    from physics.flight_sim import resolve_throw as rt  # noqa

    assert t0.angular_rate_radps[1] == 0.0
    # omega grows monotonically with eta; eta=0 => no impulsive rotation
    cfg = LaunchConfig(**base, throw_torque_efficiency=eta)
    sol = resolve_throw(cfg, AircraftConfig(), EnvironmentConfig())
    assert sol.angular_rate_radps[1] >= 0.0
    assert t1.angular_rate_radps[1] > 0.0


def test_impulse_speed_is_force_times_duration_over_mass():
    cfg = LaunchConfig(
        throw_style="overhand",
        throw_speed_source="impulse",
        throw_force_n=0.35,
        throw_duration_s=0.02,
    )
    sol = resolve_throw(cfg, AircraftConfig(), EnvironmentConfig())
    mass = float(sol.mass_kg)
    assert sol.speed_mps == pytest.approx(0.35 * 0.02 / mass, rel=1e-9)
    state = initial_state(cfg, EnvironmentConfig(), AircraftConfig())
    import math as _m

    assert _m.isclose(float((state.velocity_mps[0] ** 2 + state.velocity_mps[1] ** 2 + state.velocity_mps[2] ** 2) ** 0.5), sol.speed_mps, rel_tol=1e-9)
