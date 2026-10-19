"""Test suite for ground penetration prevention and underground spawn anomaly detection."""

from __future__ import annotations

import pytest

from physics import flight_sim
from physics.flight_sim import FlightSimulator, initial_state
from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    SimulationSettings,
    StateVector,
)


def test_negative_launch_height_triggers_immediate_crash():
    """Verify that launching underground (launch_height_m < 0) immediately latches crashed=True."""
    launch = LaunchConfig(launch_height_m=-5.0, launch_speed=10.0)
    env = EnvironmentConfig(ground_level_m=0.0)
    aircraft = AircraftConfig(name="test_plane")

    # 1. initial_state must recognize underground spawn as crashed
    state = initial_state(launch, env, aircraft)
    assert state.crashed is True, f"Expected state.crashed=True, got {state.crashed}"
    assert state.landed is False
    assert state.position_m[2] > 0.0, f"Expected z > 0 (downwards/underground), got {state.position_m[2]}"

    # 2. FlightSimulator initialized underground must latch crashed=True immediately
    settings = SimulationSettings(collision_enabled=True)
    sim = FlightSimulator(aircraft, env, launch, settings)
    assert sim.state.crashed is True, f"Expected sim.state.crashed=True, got {sim.state.crashed}"
    assert sim.state.landed is False
    assert sim._status() == "crashed"

    # 3. Initial recorded sample at t=0 must reflect crashed=True
    samples = sim.history()
    assert len(samples) > 0
    assert samples[0].crashed is True
    assert samples[0].landed is False

    # 4. Stepping an already crashed simulator should not fly or teleport
    packet = sim.step(dt=0.02)
    assert packet["crashed"] is True
    assert packet["status"] == "crashed"
    assert sim.state.velocity_mps == (0.0, 0.0, 0.0)


def test_extreme_attitude_ground_penetration_triggers_immediate_crash():
    """Verify that extreme attitude pitching into ground (min_alt < -0.05) latches crashed=True."""
    # Low launch height with steep 90 deg roll: right wingtip penetrates 9.25cm (> 5cm tolerance)
    launch = LaunchConfig(
        launch_height_m=0.01,
        launch_roll_deg=90.0,
        launch_speed=5.0,
    )
    env = EnvironmentConfig(ground_level_m=0.0)
    aircraft = AircraftConfig(name="test_plane")

    state = initial_state(launch, env, aircraft)
    assert state.crashed is True, "Expected deep station penetration to latch crashed=True in initial_state"
    assert state.landed is False

    sim = FlightSimulator(aircraft, env, launch)
    assert sim.state.crashed is True, "Expected sim.state.crashed=True on deep station penetration"
    assert sim._status() == "crashed"
    assert sim.history()[0].crashed is True


def test_reset_with_underground_launch_triggers_crash():
    """Verify that calling reset() with underground launch configuration latches crashed=True."""
    aircraft = AircraftConfig(name="test_plane")
    env = EnvironmentConfig(ground_level_m=0.0)
    normal_launch = LaunchConfig(launch_height_m=2.0, launch_speed=10.0)

    sim = FlightSimulator(aircraft, env, normal_launch)
    assert sim.state.crashed is False
    assert sim._status() == "flying"

    # Reset with underground launch
    underground_launch = LaunchConfig(launch_height_m=-3.0, launch_speed=10.0)
    new_state = sim.reset(underground_launch)

    assert new_state.crashed is True
    assert new_state.landed is False
    assert sim.state.crashed is True
    assert sim._status() == "crashed"
    assert sim.history()[0].crashed is True


def test_load_state_with_underground_position_triggers_crash():
    """Verify that load_state rejects / latches crashed=True for underground states."""
    aircraft = AircraftConfig(name="test_plane")
    env = EnvironmentConfig(ground_level_m=0.0)
    launch = LaunchConfig(launch_height_m=2.0, launch_speed=10.0)

    sim = FlightSimulator(aircraft, env, launch)
    assert sim.state.crashed is False

    # State located 10m underground (z = +10.0, altitude = -10.0)
    underground_state = StateVector(
        time_s=1.0,
        position_m=(5.0, 0.0, 10.0),
        velocity_mps=(10.0, 0.0, 0.0),
        quaternion=(1.0, 0.0, 0.0, 0.0),
        angular_velocity_radps=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )
    loaded = sim.load_state(underground_state)
    assert loaded.crashed is True
    assert loaded.landed is False
    assert sim.state.crashed is True
    assert sim._status() == "crashed"


def test_valid_launch_does_not_falsely_crash():
    """Verify that standard above-ground launch does not trigger false crash."""
    aircraft = AircraftConfig(name="test_plane")
    env = EnvironmentConfig(ground_level_m=0.0)
    launch = LaunchConfig(launch_height_m=1.5, launch_speed=8.0)

    state = initial_state(launch, env, aircraft)
    assert state.crashed is False
    assert state.landed is False

    sim = FlightSimulator(aircraft, env, launch)
    assert sim.state.crashed is False
    assert sim.state.landed is False
    assert sim._status() == "flying"
    assert sim.history()[0].crashed is False
