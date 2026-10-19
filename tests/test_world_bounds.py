"""Test suite verifying world bounds enforcement and horizontal anomaly prevention."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from physics import flight_sim
from physics.flight_sim import FlightSimulator, HARD_HORIZONTAL_LIMIT_M
from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    SimulationSettings,
    StateVector,
)


def test_within_limits_rejects_extreme_horizontal_position():
    """Verify that _within_limits rejects states beyond HARD_HORIZONTAL_LIMIT_M."""
    sim = FlightSimulator(
        AircraftConfig(name="test_plane"),
        EnvironmentConfig(),
        LaunchConfig(launch_speed=10.0, launch_height_m=1.5),
        SimulationSettings(dt_max=0.01),
    )

    base_state = StateVector(
        time_s=0.0,
        position_m=(0.0, 0.0, -1.5),
        velocity_mps=(10.0, 0.0, 0.0),
        quaternion=(1.0, 0.0, 0.0, 0.0),
        angular_velocity_radps=(0.0, 0.0, 0.0),
    )

    # Within bounds (10km) -> should pass
    valid_state = replace(base_state, position_m=(1.0e4, 0.0, -1.5))
    assert sim._within_limits(valid_state) is True

    # At boundary (HARD_HORIZONTAL_LIMIT_M = 100km) -> should pass
    boundary_state = replace(base_state, position_m=(HARD_HORIZONTAL_LIMIT_M, 0.0, -1.5))
    assert sim._within_limits(boundary_state) is True

    # Exceeding along X axis -> should fail
    out_x_state = replace(base_state, position_m=(HARD_HORIZONTAL_LIMIT_M + 1.0, 0.0, -1.5))
    assert sim._within_limits(out_x_state) is False

    # Exceeding along Y axis -> should fail
    out_y_state = replace(base_state, position_m=(0.0, HARD_HORIZONTAL_LIMIT_M + 1.0, -1.5))
    assert sim._within_limits(out_y_state) is False

    # Exceeding diagonally (hypot > HARD_HORIZONTAL_LIMIT_M) -> should fail
    diag_dist = HARD_HORIZONTAL_LIMIT_M * 0.75  # sqrt(2)*0.75 ≈ 1.0606 > 1.0
    out_diag_state = replace(base_state, position_m=(diag_dist, diag_dist, -1.5))
    assert math.hypot(diag_dist, diag_dist) > HARD_HORIZONTAL_LIMIT_M
    assert sim._within_limits(out_diag_state) is False


def test_simulation_crashes_and_freezes_when_crossing_horizontal_boundary():
    """Verify that a simulation step crossing the horizontal world bounds triggers a crash/freeze."""
    settings = SimulationSettings(
        dt_max=0.01,
        dt_min=1.0e-5,
        max_substeps=16,
        record_interval_s=0.01,
        duration_s=1.0,
    )
    sim = FlightSimulator(
        AircraftConfig(name="test_plane"),
        EnvironmentConfig(),
        LaunchConfig(launch_speed=10.0, launch_height_m=100.0),
        settings,
    )

    # Force position to just inside the horizontal boundary
    just_inside_x = HARD_HORIZONTAL_LIMIT_M - 0.05
    sim._state = replace(
        sim._state,
        position_m=(just_inside_x, 0.0, -100.0),
        velocity_mps=(50.0, 0.0, 0.0),  # moving outwards at 50 m/s
    )

    # Step the simulation forward - candidate step will cross the 100km boundary
    packet = sim.step(dt=0.01)

    assert sim._state.crashed is True
    assert packet["crashed"] is True
    assert packet["diverged"] is True
    assert packet["status"] == "diverged"
