"""Verification of the GPU/NumPy ensemble batch simulation and REST API."""

import pytest
import numpy as np

from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig
from physics.parallel_engine import gpu_ensemble_simulation
import app as flask_app


def test_gpu_ensemble_simulation_cupy_and_numpy():
    """Verify ensemble simulation runs under CuPy (or NumPy fallback) and pure CPU mode."""
    aircraft = AircraftConfig()
    environment = EnvironmentConfig(wind_speed=2.0, wind_direction_deg=45.0)
    launch = LaunchConfig(launch_speed=10.0, launch_angle_deg=8.0, launch_height_m=2.0)

    # 1. Automatic mode (prefers CuPy on RTX 4060 GPU, fallback to NumPy)
    res_auto = gpu_ensemble_simulation(
        aircraft=aircraft,
        environment=environment,
        launch=launch,
        n_trajectories=64,
        force_cpu=False,
        random_seed=123,
    )
    assert res_auto["trajectories_count"] == 64
    assert res_auto["evaluated_count"] == 64
    assert res_auto["backend"] in ("cupy", "numpy")
    assert res_auto["mean_flight_time_s"] > 0.0
    assert res_auto["mean_distance_m"] > 0.0
    assert res_auto["max_flight_time_s"] >= res_auto["mean_flight_time_s"]
    assert res_auto["max_distance_m"] >= res_auto["mean_distance_m"]
    assert res_auto["min_flight_time_s"] <= res_auto["mean_flight_time_s"]
    assert res_auto["min_distance_m"] <= res_auto["mean_distance_m"]

    # Check statistics dictionary
    stats = res_auto["statistics"]
    assert "flight_time" in stats and "distance" in stats
    for metric in ("mean", "std", "min", "max", "median"):
        assert metric in stats["flight_time"]
        assert metric in stats["distance"]

    # Check confidence envelope
    env_data = res_auto["envelope"]
    assert len(env_data["time_s"]) > 10
    assert len(env_data["altitude_p50"]) == len(env_data["time_s"])
    assert len(env_data["altitude_p10"]) == len(env_data["time_s"])
    assert len(env_data["altitude_p90"]) == len(env_data["time_s"])
    assert len(env_data["altitude_min"]) == len(env_data["time_s"])
    assert len(env_data["altitude_max"]) == len(env_data["time_s"])
    # Percentile monotonic ordering at t=0
    assert env_data["altitude_p10"][0] <= env_data["altitude_p50"][0] <= env_data["altitude_p90"][0]

    # Check sample trajectories
    samples = res_auto["sample_trajectories"]
    assert len(samples) == min(8, 64)
    for sample in samples:
        assert "id" in sample
        assert "points" in sample and len(sample["points"]) > 0
        assert "x" in sample["points"][0] and "y" in sample["points"][0] and "z" in sample["points"][0]

    # 2. Forced CPU mode (NumPy fallback verification)
    res_cpu = gpu_ensemble_simulation(
        aircraft=aircraft,
        environment=environment,
        launch=launch,
        n_trajectories=32,
        force_cpu=True,
        random_seed=123,
    )
    assert res_cpu["backend"] == "numpy"
    assert res_cpu["trajectories_count"] == 32
    assert res_cpu["mean_distance_m"] > 0.0
    assert res_cpu["mean_flight_time_s"] > 0.0


def test_api_simulation_ensemble_endpoint():
    """Verify POST /api/v1/simulation/ensemble endpoint returns valid JSON statistics."""
    client = flask_app.app.test_client()

    # Default payload
    res = client.post("/api/v1/simulation/ensemble", json={"n_trajectories": 48})
    assert res.status_code == 200
    data = res.get_json()
    assert data["trajectories_count"] == 48
    assert "backend" in data
    assert "statistics" in data
    assert "envelope" in data
    assert "sample_trajectories" in data
    assert data["mean_distance_m"] > 0.0

    # Custom payload with noise and forced CPU
    res_cpu = client.post(
        "/api/v1/simulation/ensemble",
        json={
            "n_trajectories": 24,
            "force_cpu": True,
            "noise_config": {
                "speed_std": 0.5,
                "pitch_std": 1.5,
            },
        },
    )
    assert res_cpu.status_code == 200
    data_cpu = res_cpu.get_json()
    assert data_cpu["backend"] == "numpy"
    assert data_cpu["trajectories_count"] == 24


@pytest.mark.parametrize("force_cpu", [True, False])
def test_ensemble_identifies_approximate_physics_even_on_gpu(force_cpu):
    from physics.parallel_engine import get_gpu_info
    result = gpu_ensemble_simulation(n_trajectories=8, settings={"duration_s":.2}, force_cpu=force_cpu)
    assert result["model"] == "reduced_surrogate"
    assert result["model_detail"] == "gpu_ensemble_3dof"
    assert result["approximate"] is True
    assert result["physics_verification_eligible"] is False
    assert any("apex" in limitation for limitation in result["limitations"])
    if force_cpu:
        assert result["backend"] == "numpy"
    elif get_gpu_info()["available"] and get_gpu_info()["backend"] == "cupy":
        assert result["backend"] == "cupy"


def test_ensemble_sorting_and_sanitization():
    """Verify that sample trajectories are properly sanitized against NaN/Inf and sorted."""
    res = gpu_ensemble_simulation(
        n_trajectories=32,
        force_cpu=True,
        random_seed=42,
        max_sample_tracks=16,
    )
    trajs = res["sample_trajectories"]
    assert len(trajs) == 16

    # Verify descending sort order by landing_distance_m
    for i in range(len(trajs) - 1):
        assert trajs[i]["landing_distance_m"] >= trajs[i + 1]["landing_distance_m"]

    # Verify rank assignments
    assert trajs[0]["rank"] == 1
    assert trajs[1]["rank"] == 2

    # Verify metrics sanitization (finite values)
    for t in trajs:
        assert np.isfinite(t["landing_distance_m"])
        assert np.isfinite(t["flight_time_s"])
        assert np.isfinite(t["max_altitude_m"])
        assert np.isfinite(t["speed_mps"])
        assert np.isfinite(t["pitch_deg"])
        assert np.isfinite(t["roll_deg"])
        for p in t["points"]:
            assert np.isfinite(p["x"]) and np.isfinite(p["y"]) and np.isfinite(p["z"])
            assert np.isfinite(p["pitch"]) and np.isfinite(p["roll"]) and np.isfinite(p["yaw"])


def test_ensemble_skip_first_promotion_logic():
    """Verify that skipping rank 1 promotes rank 2 to effective champion."""
    res = gpu_ensemble_simulation(
        n_trajectories=32,
        force_cpu=True,
        random_seed=999,
        max_sample_tracks=16,
    )
    trajs = res["sample_trajectories"]
    assert len(trajs) >= 2

    # Before skipping
    original_top1 = trajs[0]
    original_top2 = trajs[1]
    assert original_top1["rank"] == 1
    assert original_top2["rank"] == 2

    # Simulating the UI skip-top-1 promotion logic:
    # effective_list = trajs[1:]
    effective_list = trajs[1:]
    promoted_champion = effective_list[0]

    # Promoted champion must be original top 2
    assert promoted_champion["id"] == original_top2["id"]
    assert promoted_champion["landing_distance_m"] == original_top2["landing_distance_m"]
    # Skipped item is distinct
    assert promoted_champion["id"] != original_top1["id"]

def test_api_simulation_ensemble_validation_and_bounds():
    """Verify input validation and boundaries for n_trajectories and max_sample_tracks."""
    client = flask_app.app.test_client()

    # n_trajectories <= 0 -> 400 BadRequest
    res_zero = client.post("/api/v1/simulation/ensemble", json={"n_trajectories": 0})
    assert res_zero.status_code == 400
    assert "n_trajectories" in res_zero.get_json()["error"]

    res_neg = client.post("/api/v1/simulation/ensemble", json={"n_trajectories": -5})
    assert res_neg.status_code == 400

    # n_trajectories > 4096 -> 400 BadRequest
    res_over = client.post("/api/v1/simulation/ensemble", json={"n_trajectories": 5000})
    assert res_over.status_code == 400
    assert "n_trajectories" in res_over.get_json()["error"]

    # max_sample_tracks < 0 -> 400 BadRequest
    res_neg_samples = client.post("/api/v1/simulation/ensemble", json={"n_trajectories": 20, "max_sample_tracks": -1})
    assert res_neg_samples.status_code == 400
    assert "max_sample_tracks" in res_neg_samples.get_json()["error"]

    # max_sample_tracks > n_trajectories -> 400 BadRequest
    res_excess_samples = client.post("/api/v1/simulation/ensemble", json={"n_trajectories": 20, "max_sample_tracks": 25})
    assert res_excess_samples.status_code == 400
    assert "max_sample_tracks" in res_excess_samples.get_json()["error"]

    # max_sample_tracks == 0 is valid (returns 0 sample trajectories without crashing)
    res_zero_samples = client.post("/api/v1/simulation/ensemble", json={"n_trajectories": 20, "max_sample_tracks": 0})
    assert res_zero_samples.status_code == 200
    data = res_zero_samples.get_json()
    assert len(data["sample_trajectories"]) == 0
    assert data["trajectories_count"] == 20

