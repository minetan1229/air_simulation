"""Test parity of duration_s between CPU fallback and GPU/device path in gpu_batch_evaluate_cases.

Verifies Issue #56:
- Unspecified duration_s should default to the same value in both paths.
- duration_s outside normal bounds (e.g. < 0.1s or > 120s) must be handled identically in both paths.
"""

from __future__ import annotations

import pytest
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig
from physics.parallel_engine import gpu_batch_evaluate_cases


def test_duration_default_parity():
    """Verify that omitting duration_s uses the same default and produces consistent duration_s."""
    case = {
        "aircraft": AircraftConfig(),
        "environment": EnvironmentConfig(),
        "launch": LaunchConfig(launch_height_m=50.0, launch_speed=10.0, launch_angle_deg=0.0),
    }

    # Evaluate with CPU fallback
    cpu_res = gpu_batch_evaluate_cases([dict(case)], force_cpu=True)[0]

    # Evaluate with GPU/device path (using NumPy/CuPy)
    device_res = gpu_batch_evaluate_cases([dict(case)], force_cpu=False)[0]

    # Both must report the same duration_s
    assert cpu_res["duration_s"] == device_res["duration_s"], (
        f"Default duration_s mismatch: CPU={cpu_res['duration_s']} vs Device={device_res['duration_s']}"
    )


def test_duration_clamping_parity_short():
    """Verify that a very short duration (e.g. 0.05s) behaves identically in both paths."""
    case = {
        "aircraft": AircraftConfig(),
        "environment": EnvironmentConfig(),
        "launch": LaunchConfig(launch_height_m=50.0, launch_speed=10.0, launch_angle_deg=0.0),
        "duration_s": 0.05,
    }

    cpu_res = gpu_batch_evaluate_cases([dict(case)], force_cpu=True)[0]
    device_res = gpu_batch_evaluate_cases([dict(case)], force_cpu=False)[0]

    assert cpu_res["duration_s"] == device_res["duration_s"], (
        f"Short duration_s mismatch: CPU={cpu_res['duration_s']} vs Device={device_res['duration_s']}"
    )
    assert cpu_res["flight_time_s"] == pytest.approx(device_res["flight_time_s"], abs=0.02)
    assert cpu_res["status"] == device_res["status"]


def test_duration_clamping_parity_long():
    """Verify that a very long duration (e.g. 200.0s) behaves identically in both paths."""
    case = {
        "aircraft": AircraftConfig(),
        "environment": EnvironmentConfig(),
        "launch": LaunchConfig(launch_height_m=50.0, launch_speed=10.0, launch_angle_deg=0.0),
        "duration_s": 200.0,
    }

    cpu_res = gpu_batch_evaluate_cases([dict(case)], force_cpu=True)[0]
    device_res = gpu_batch_evaluate_cases([dict(case)], force_cpu=False)[0]

    assert cpu_res["duration_s"] == device_res["duration_s"], (
        f"Long duration_s mismatch: CPU={cpu_res['duration_s']} vs Device={device_res['duration_s']}"
    )
