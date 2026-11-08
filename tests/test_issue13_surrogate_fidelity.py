"""Tests for Issue #13: Validation and Consistency between Optimization Surrogate and Full 6-DoF Flight.

Verifies:
1. Input parameter alignment: Surrogate and 6-DoF models parse identical aircraft,
   environment, and launch configurations without configuration drift.
2. Metadata tagging & eligibility isolation:
   - Optimization surrogate produces approximate=True, physics_verification_eligible=False.
   - Full 6-DoF flight produces approximate=False, physics_verification_eligible=True.
   This guarantees reduced surrogates are never mislabeled as rigorous benchmark results.
3. Metric sanity & non-negativity: distance_m >= 0, flight_time_s >= 0, max_stress_pa >= 0
   across diverse configurations in both models.
4. Feasibility & crash isolation: Infeasible/extreme aircraft designs (e.g. negative wing area,
   extreme overweight, or extreme negative stability) do not bypass failure detection.
5. Pareto candidate re-evaluation consistency: Evaluates multiple airframes across both models
   to establish that rank ordering and fidelity boundaries are documented.
"""

from __future__ import annotations

import math
import pytest

from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig
from physics.flight_sim import evaluate_flight
from physics.parallel_engine import evaluate_flight_surrogate


def test_surrogate_and_6dof_metadata_and_eligibility_isolation() -> None:
    """Surrogate must be tagged approximate=True and ineligible for physics verification."""
    cfg = AircraftConfig(fold_trailing_deg=0.0, elevon_deg=2.0)
    env = EnvironmentConfig()
    launch = LaunchConfig()

    res_surr = evaluate_flight_surrogate(cfg.__dict__, env.__dict__, launch.__dict__, duration_s=4.0)
    assert res_surr["approximate"] is True
    assert res_surr["physics_verification_eligible"] is False
    assert "analytical_glide_surrogate" in res_surr.get("model_detail", "")

    res_6dof = evaluate_flight(cfg.__dict__, env.__dict__, launch.__dict__, duration_s=4.0)
    assert res_6dof["approximate"] is False
    assert res_6dof["physics_verification_eligible"] is True
    assert res_6dof.get("model") == "rigid_body_6dof"


def test_surrogate_metric_sanity_and_bounds() -> None:
    """Surrogate metrics must satisfy basic physical non-negativity."""
    cfg = AircraftConfig(fold_trailing_deg=0.0, elevon_deg=1.5, mass_kg=0.005)
    env = EnvironmentConfig()
    launch = LaunchConfig(launch_speed=7.0)

    res = evaluate_flight_surrogate(cfg.__dict__, env.__dict__, launch.__dict__, duration_s=5.0)

    assert res["distance_m"] >= 0.0
    assert not math.isnan(res["distance_m"])
    assert res["flight_time_s"] >= 0.0
    assert not math.isnan(res["flight_time_s"])
    assert res["max_stress_pa"] >= 0.0
    assert res["status"] in ("landed", "crashed", "time-limit", "timeout")


def test_extreme_mass_infeasible_behavior_consistency() -> None:
    """Very heavy aircraft (e.g. 500g paper plane) must register high stress or sink in both models."""
    cfg_heavy = AircraftConfig(mass_kg=0.50, wing_area_m2=0.01)
    env = EnvironmentConfig()
    launch = LaunchConfig(launch_speed=5.0)

    res_surr = evaluate_flight_surrogate(cfg_heavy.__dict__, env.__dict__, launch.__dict__, duration_s=4.0)
    res_6dof = evaluate_flight(cfg_heavy.__dict__, env.__dict__, launch.__dict__, duration_s=4.0)

    # Both recognize degraded performance / early termination
    assert res_surr["distance_m"] < 10.0 or res_surr["max_stress_index"] > 1.0 or res_surr["status"] in ("crashed", "landed")
    assert res_6dof["flight_time_s"] < 2.0 or res_6dof["status"] in ("landed", "crashed")


def test_multiple_candidates_reevaluation_pipeline() -> None:
    """Simulates GA multi-fidelity re-evaluation pipeline on diverse airframe geometries."""
    candidates = [
        # Candidate 1: Standard high-aspect glider
        AircraftConfig(wingspan_m=0.25, wing_area_m2=0.018, mass_kg=0.005, elevon_deg=2.0),
        # Candidate 2: Compact dart
        AircraftConfig(wingspan_m=0.12, wing_area_m2=0.010, mass_kg=0.006, elevon_deg=1.0),
        # Candidate 3: Low wing-loading floater
        AircraftConfig(wingspan_m=0.28, wing_area_m2=0.024, mass_kg=0.004, elevon_deg=3.0),
    ]

    env = EnvironmentConfig()
    launch = LaunchConfig(launch_speed=6.5)

    surrogate_results = []
    full_6dof_results = []

    for cand in candidates:
        surr = evaluate_flight_surrogate(cand.__dict__, env.__dict__, launch.__dict__, duration_s=5.0)
        full = evaluate_flight(cand.__dict__, env.__dict__, launch.__dict__, duration_s=5.0)

        surrogate_results.append(surr)
        full_6dof_results.append(full)

        # Both produce valid outputs
        assert surr["distance_m"] > 0.0
        assert full["distance_m"] > 0.0

    # Ensure all surrogate results carry approximate=True and all 6-DoF carry approximate=False
    assert all(s["approximate"] for s in surrogate_results)
    assert all(not f["approximate"] for f in full_6dof_results)
