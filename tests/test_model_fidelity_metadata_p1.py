"""Regression tests for Phase 3: P1-4 (Integrity metadata) and P1-5 (Model verification eligibility).

Fixates:
- P1-4: Telemetry and optimization runs publish `approximate`, `model_detail`,
  and `physics_verification_eligible` to distinguish rigid 6DoF from reduced surrogates.
- P1-5: Verification eligibility is strictly determined by whether the evaluation
  was performed using the full 6DoF RK4 flight model, regardless of host environment.
"""

from __future__ import annotations

import pytest

from physics import flight_sim, parallel_engine
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig, as_dict
from physics.optimizer_domain import OptimizerConfig, run_optimizer
from physics.payload_contract import PAYLOAD_KEYS


def test_p1_4_rigid_body_6dof_metadata_is_verification_eligible() -> None:
    """The canonical 6DoF flight evaluation must report rigid_body_6dof and be verification eligible."""
    payload = flight_sim.evaluate_flight(
        as_dict(AircraftConfig()),
        as_dict(EnvironmentConfig()),
        as_dict(LaunchConfig(launch_speed=12.0)),
        duration_s=1.0,
    )

    # Required integrity metadata keys (P1-4 & payload contract)
    assert set(PAYLOAD_KEYS) <= set(payload.keys())
    assert payload["approximate"] is False
    assert payload["model"] == "rigid_body_6dof"
    assert payload["model_detail"] == "6dof_rk4"
    # Must be eligible for physical record verification when evaluation succeeds (P1-5)
    assert payload["physics_verification_eligible"] is True


def test_p1_4_and_p1_5_reduced_model_disables_verification_eligibility() -> None:
    """Any approximate/reduced evaluation must explicitly mark physics_verification_eligible as False."""
    surrogate = parallel_engine.evaluate_flight_surrogate(
        as_dict(AircraftConfig()),
        as_dict(EnvironmentConfig()),
        as_dict(LaunchConfig(launch_speed=12.0)),
        duration_s=1.0,
    )

    assert surrogate["approximate"] is True
    assert surrogate["model"] == parallel_engine.REDUCED_MODEL_TAG
    assert surrogate["model_detail"] == "analytical_glide_surrogate"
    assert surrogate["physics_verification_eligible"] is False

    ensemble = parallel_engine.gpu_ensemble_simulation(
        n_trajectories=4, settings={"duration_s": 0.1}, force_cpu=True
    )
    assert ensemble["approximate"] is True
    assert ensemble["model"] == parallel_engine.REDUCED_MODEL_TAG
    assert ensemble["model_detail"] == "gpu_ensemble_3dof"
    assert ensemble["physics_verification_eligible"] is False


def test_p1_5_optimizer_result_tracks_model_verification_eligibility() -> None:
    """The optimizer result payload exposes physics_models, approximate, and physics_verification_eligible."""
    opt_cfg = OptimizerConfig(
        algorithm="grid",
        design_variables=["wingspan_m"],
        max_evaluations=2,
        processes=1,
        seed=42,
        sim_duration_s=0.2,
    )
    result = run_optimizer(opt_cfg)

    assert "physics_models" in result
    assert "approximate" in result
    assert "physics_verification_eligible" in result
    # For single process reference 6DoF runs, it should be eligible and not approximate
    assert result["approximate"] is False
    assert result["physics_verification_eligible"] is True
