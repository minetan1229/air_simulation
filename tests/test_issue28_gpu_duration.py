"""Verification test for Issue #28:
Ensures GPU batch evaluation respects individual case duration_s limits,
and does not allow shorter cases to inherit the maximum duration of the batch.
"""

from __future__ import annotations

import pytest
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig
from physics.parallel_engine import gpu_batch_evaluate_cases


def test_gpu_batch_respects_individual_case_durations():
    """Verify that cases with shorter duration_s in a mixed batch do not exceed their own duration limit."""
    cases = [
        {
            "aircraft": AircraftConfig(),
            "environment": EnvironmentConfig(),
            "launch": LaunchConfig(launch_height_m=50.0, launch_speed=10.0, launch_angle_deg=0.0),
            "duration_s": 2.5,
        },
        {
            "aircraft": AircraftConfig(),
            "environment": EnvironmentConfig(),
            "launch": LaunchConfig(launch_height_m=50.0, launch_speed=10.0, launch_angle_deg=0.0),
            "duration_s": 6.0,
        },
        {
            "aircraft": AircraftConfig(),
            "environment": EnvironmentConfig(),
            "launch": LaunchConfig(launch_height_m=50.0, launch_speed=10.0, launch_angle_deg=0.0),
            "duration_s": 15.0,
        },
    ]

    results = gpu_batch_evaluate_cases(cases)

    assert len(results) == 3

    # Case 0: duration is 2.5s. From 50m altitude, it cannot land in 2.5s, so it must timeout at 2.5s.
    assert results[0]["flight_time_s"] <= 2.5
    assert results[0]["flight_time_s"] == pytest.approx(2.5, abs=0.02)
    assert results[0]["status"] == "timeout"
    assert results[0]["landed"] is False

    # Case 1: duration is 6.0s.
    assert results[1]["flight_time_s"] <= 6.0
    assert results[1]["flight_time_s"] == pytest.approx(6.0, abs=0.02)
    assert results[1]["status"] == "timeout"
    assert results[1]["landed"] is False

    # Case 2: duration is 15.0s.
    assert results[2]["flight_time_s"] <= 15.0


def test_gpu_batch_landing_within_duration():
    """Verify that a case that lands before its duration_s is marked as landed."""
    cases = [
        {
            # Low launch height, will land in < 1 second
            "aircraft": AircraftConfig(),
            "environment": EnvironmentConfig(),
            "launch": LaunchConfig(launch_height_m=0.2, launch_speed=2.0, launch_angle_deg=-10.0),
            "duration_s": 10.0,
        },
        {
            # High launch height, will timeout at 3.0 seconds
            "aircraft": AircraftConfig(),
            "environment": EnvironmentConfig(),
            "launch": LaunchConfig(launch_height_m=50.0, launch_speed=10.0, launch_angle_deg=0.0),
            "duration_s": 3.0,
        },
    ]

    results = gpu_batch_evaluate_cases(cases)

    assert len(results) == 2
    # Case 0 should land quickly
    assert results[0]["landed"] is True
    assert results[0]["status"] == "landed"
    assert results[0]["flight_time_s"] < 2.0

    # Case 1 should timeout at 3.0s
    assert results[1]["landed"] is False
    assert results[1]["status"] == "timeout"
    assert results[1]["flight_time_s"] <= 3.0
