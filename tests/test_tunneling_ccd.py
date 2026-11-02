"""Test suite for Continuous Collision Detection (CCD) and anti-tunneling physics.

Verifies that high-velocity descent / steep entry does not tunnel through the ground
plane or bury the aircraft underground, but correctly interpolates the impact point,
clamps to the ground boundary (min_alt >= -1e-4), and resolves collision dynamics.
"""

from __future__ import annotations

import pytest

from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    SimulationSettings,
    StateVector,
)
from physics.flight_sim import FlightSimulator, compute_ground_stations


def test_high_speed_penetration_ccd_clamps_to_ground_and_interpolates_time():
    """Verify that a high-speed penetration across the ground plane in a single step

    (prev min_alt > 0, curr min_alt < -0.05) is detected by CCD, clamping the contact
    state to the ground surface (min_alt ~ 0.0) and interpolating the collision time.
    """
    aircraft = AircraftConfig(name="test_plane")
    env = EnvironmentConfig(ground_level_m=0.0)
    settings = SimulationSettings(collision_enabled=True)
    sim = FlightSimulator(aircraft, env, settings=settings)

    # Airframe starts slightly above ground: z = -0.10 m (min_alt ~ 0.085 m)
    prev_state = StateVector(
        time_s=0.0,
        position_m=(0.0, 0.0, -0.10),
        velocity_mps=(0.0, 0.0, 65.0),
        quaternion=(1.0, 0.0, 0.0, 0.0),
        angular_velocity_radps=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )
    # 0.01s later, at 65 m/s without CCD, it would tunnel to z = +0.55 m (min_alt ~ -0.565 m)
    curr_state = StateVector(
        time_s=0.01,
        position_m=(0.0, 0.0, 0.55),
        velocity_mps=(0.0, 0.0, 65.0),
        quaternion=(1.0, 0.0, 0.0, 0.0),
        angular_velocity_radps=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )

    st_prev = compute_ground_stations(prev_state.position_m, prev_state.quaternion, env, sim._derived)
    st_curr = compute_ground_stations(curr_state.position_m, curr_state.quaternion, env, sim._derived)
    assert st_prev["min_alt"] > 0.0, "Previous state must be above ground"
    assert st_curr["min_alt"] < -0.05, "Current state must have penetrated deep underground"

    # Apply ground contact with CCD enabled via previous state
    contact_state = sim._apply_ground_contact(curr_state, prev_state)
    st_contact = compute_ground_stations(contact_state.position_m, contact_state.quaternion, env, sim._derived)

    # 1. CCD must clamp the airframe to the ground surface rather than leaving it buried underground
    assert abs(st_contact["min_alt"]) <= 0.005, (
        f"Expected airframe clamped to ground surface (min_alt ~ 0.0), got {st_contact['min_alt']} m"
    )
    # 2. Extreme impact speed (50 m/s) must declare crashed
    assert contact_state.crashed is True
    assert contact_state.velocity_mps == (0.0, 0.0, 0.0)

    # 3. Impact time must be interpolated between t_prev and t_curr
    expected_tau = st_prev["min_alt"] / (st_prev["min_alt"] - st_curr["min_alt"])
    expected_t = prev_state.time_s + expected_tau * (curr_state.time_s - prev_state.time_s)
    assert abs(contact_state.time_s - expected_t) < 1e-4, (
        f"Expected interpolated impact time {expected_t:.6f} s, got {contact_state.time_s:.6f} s"
    )


def test_ccd_prevents_false_penetration_crash_and_preserves_rebound():
    """Verify that a survivable descent that penetrates past the tolerance

    (min_alt < -0.05) in a single large step is resolved at the ground surface by
    CCD, preserving the restitution bounce instead of falsely triggering the
    deep penetration immediate crash.

    The impact speed is 3 m/s: the structural criterion of the repaired ground
    contact (wing-root stress against the airframe's own allowable stress) is
    evaluated with the *same* scale as the stress telemetry, and an 8 m/s
    vertical impact exceeds that limit.  The structural failure of a hard
    impact is covered by
    :func:`test_hard_vertical_impact_is_a_structural_failure_not_a_penetration_crash`.
    """
    aircraft = AircraftConfig(name="bounce_plane")
    env = EnvironmentConfig(ground_level_m=0.0)
    settings = SimulationSettings(collision_enabled=True, restitution=0.5)
    sim = FlightSimulator(aircraft, env, settings=settings)

    # Starts close to ground: min_alt ~ 0.02 m
    prev_state = StateVector(
        time_s=0.0,
        position_m=(0.0, 0.0, -0.035),
        velocity_mps=(0.0, 0.0, 3.0),
        quaternion=(1.0, 0.0, 0.0, 0.0),
        angular_velocity_radps=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )
    # Crosses ground into min_alt ~ -0.06 m (> 0.05m tolerance)
    curr_state = StateVector(
        time_s=0.01,
        position_m=(0.0, 0.0, 0.045),
        velocity_mps=(0.0, 0.0, 3.0),
        quaternion=(1.0, 0.0, 0.0, 0.0),
        angular_velocity_radps=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )

    st_prev = compute_ground_stations(prev_state.position_m, prev_state.quaternion, env, sim._derived)
    st_curr = compute_ground_stations(curr_state.position_m, curr_state.quaternion, env, sim._derived)
    assert st_prev["min_alt"] > 0.0
    assert st_curr["min_alt"] < -0.05

    # Ground contact with CCD
    contact_state = sim._apply_ground_contact(curr_state, prev_state)
    st_contact = compute_ground_stations(contact_state.position_m, contact_state.quaternion, env, sim._derived)

    # Must clamp to ground surface
    assert abs(st_contact["min_alt"]) <= 0.005, f"Expected min_alt ~ 0, got {st_contact['min_alt']}"
    # Must NOT be crashed because the impact is within structural survivability
    assert contact_state.crashed is False, "Survivable impact must rebound, not crash"
    # vz must rebound upward (negative in NED world frame)
    assert contact_state.velocity_mps[2] < 0.0, (
        f"Expected rebound vz < 0, got {contact_state.velocity_mps[2]}"
    )


def test_hard_vertical_impact_is_a_structural_failure_not_a_penetration_crash():
    """A hard 8 m/s vertical impact must be reported as a structural failure.

    The failure has to come from the *structural* criterion (the wing-root
    stress against the airframe's own allowable stress, the same scale the
    telemetry uses), and not from the deep-penetration guardrail: the contact
    is still resolved at the ground surface by the CCD.
    """
    aircraft = AircraftConfig(name="bounce_plane")
    env = EnvironmentConfig(ground_level_m=0.0)
    settings = SimulationSettings(collision_enabled=True, restitution=0.5)
    sim = FlightSimulator(aircraft, env, settings=settings)

    prev_state = StateVector(
        time_s=0.0,
        position_m=(0.0, 0.0, -0.035),
        velocity_mps=(0.0, 0.0, 8.0),
        quaternion=(1.0, 0.0, 0.0, 0.0),
        angular_velocity_radps=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )
    curr_state = StateVector(
        time_s=0.01,
        position_m=(0.0, 0.0, 0.045),
        velocity_mps=(0.0, 0.0, 8.0),
        quaternion=(1.0, 0.0, 0.0, 0.0),
        angular_velocity_radps=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )

    contact_state = sim._apply_ground_contact(curr_state, prev_state)
    st_contact = compute_ground_stations(contact_state.position_m, contact_state.quaternion, env, sim._derived)

    assert contact_state.crashed is True, "An 8 m/s vertical impact must fail structurally"
    # The contact was resolved on the surface: the deep-penetration guardrail
    # would have left the airframe 6 cm below the ground.
    assert abs(st_contact["min_alt"]) <= 0.005, f"Expected min_alt ~ 0, got {st_contact['min_alt']}"
    # The impact instant is interpolated, not the end of the step.
    assert 0.0 < contact_state.time_s < curr_state.time_s


def test_ccd_does_not_affect_genuine_underground_spawn():
    """Verify that genuine underground spawning (prev_state is None or already underground)

    still immediately latches crashed=True and does not teleport upward.
    """
    aircraft = AircraftConfig(name="underground_plane")
    env = EnvironmentConfig(ground_level_m=0.0)
    settings = SimulationSettings(collision_enabled=True)
    sim = FlightSimulator(aircraft, env, settings=settings)

    underground_state = StateVector(
        time_s=0.0,
        position_m=(0.0, 0.0, 2.0),  # 2m underground
        velocity_mps=(0.0, 0.0, 0.0),
        quaternion=(1.0, 0.0, 0.0, 0.0),
        angular_velocity_radps=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )
    # No prev_state: genuine underground
    result = sim._apply_ground_contact(underground_state, prev_state=None)
    assert result.crashed is True
    assert result.position_m[2] == 2.0  # Does NOT teleport upward

    # Both prev and curr underground: no crossing
    prev_underground = StateVector(
        time_s=0.0,
        position_m=(0.0, 0.0, 1.0),
        velocity_mps=(0.0, 0.0, 5.0),
        quaternion=(1.0, 0.0, 0.0, 0.0),
        angular_velocity_radps=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )
    result2 = sim._apply_ground_contact(underground_state, prev_state=prev_underground)
    assert result2.crashed is True
    assert result2.position_m[2] == 2.0


def test_sim_step_with_ccd_prevents_tunneling():
    """Verify that advancing a step in FlightSimulator with steep descent

    activates CCD, clamping contact at the ground surface and correctly
    transitioning state without deep subterranean tunneling.
    """
    aircraft = AircraftConfig(name="step_plane", cm0=0.0, cL0=0.0, elevon_deg=0.0)
    env = EnvironmentConfig(ground_level_m=0.0)
    settings = SimulationSettings(collision_enabled=True, dt_min=1e-4, dt_max=0.01)
    sim = FlightSimulator(aircraft, env, settings=settings)

    # Set state right above ground moving fast downwards
    state_above = StateVector(
        time_s=0.0,
        position_m=(0.0, 0.0, -0.05),
        velocity_mps=(0.0, 0.0, 25.0),
        quaternion=(1.0, 0.0, 0.0, 0.0),
        angular_velocity_radps=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )
    sim.load_state(state_above)
    st_init = compute_ground_stations(sim.state.position_m, sim.state.quaternion, env, sim._derived)
    assert st_init["min_alt"] > 0.0

    packet = sim.step(dt=0.005)
    st_final = compute_ground_stations(sim.state.position_m, sim.state.quaternion, env, sim._derived)

    # Min alt must not tunnel underground beyond margin
    assert st_final["min_alt"] >= -0.01, (
        f"Sim step tunneled underground: min_alt = {st_final['min_alt']} m"
    )
    assert sim.state.crashed is True or sim.state.landed is True or sim.state.velocity_mps[2] < 0.0
