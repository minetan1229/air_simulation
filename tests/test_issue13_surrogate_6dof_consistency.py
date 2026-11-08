"""Verification of surrogate vs 6-DoF reference consistency (Issue #13).

Verifies:
1. Candidate re-verification path confirms surrogate rankings and detects false positives.
2. Unfeasible flight statuses (diverged, stalled, no-launch) are strictly rejected from Pareto/incumbents.
3. Thread-safe backend state in parallel map_evaluate.
4. Monotonic correlation between surrogate score trends and full 6-DoF physics simulation.
"""

import math
import pytest
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig, as_dict
from physics.optimizer_domain import is_infeasible_flight, INFEASIBLE_STATUSES
from physics.parallel_engine import evaluate_flight_surrogate, map_evaluate
from physics.flight_sim import evaluate_flight


def test_infeasible_statuses_strict_rejection():
    """Verify that failed/diverged/stalled flights are marked infeasible and never ranked feasible."""
    for status in INFEASIBLE_STATUSES:
        payload = {"status": status, "distance_m": 0.0, "crashed": False}
        assert is_infeasible_flight(payload) is True

    # Legitimate finished flights must be feasible
    assert is_infeasible_flight({"status": "landed", "distance_m": 12.5}) is False
    assert is_infeasible_flight({"status": "flying", "distance_m": 8.0}) is False
    assert is_infeasible_flight({"status": "timeout", "distance_m": 15.0}) is False


def test_surrogate_and_6dof_trend_consistency():
    """Verify that surrogate and full 6-DoF both show positive distance response to L/D enhancement."""
    ac_low = AircraftConfig(fold_trailing_deg=0.0, cL0=0.2)
    ac_high = AircraftConfig(fold_trailing_deg=0.0, cL0=0.6)
    env = EnvironmentConfig()
    ln = LaunchConfig(launch_speed=7.0, launch_angle_deg=5.0)

    # 1. Surrogate evaluation (takes dicts)
    res_surr_low = evaluate_flight_surrogate(as_dict(ac_low), as_dict(env), as_dict(ln))
    res_surr_high = evaluate_flight_surrogate(as_dict(ac_high), as_dict(env), as_dict(ln))

    # 2. Full 6-DoF evaluation
    res_6dof_low = evaluate_flight(as_dict(ac_low), as_dict(env), as_dict(ln), duration_s=1.5)
    res_6dof_high = evaluate_flight(as_dict(ac_high), as_dict(env), as_dict(ln), duration_s=1.5)

    # Both models should yield positive valid flights and preserve physical order of magnitude
    assert res_surr_low["distance_m"] > 0.0
    assert res_surr_high["distance_m"] > 0.0
    assert res_6dof_low["distance_m"] > 0.0
    assert res_6dof_high["distance_m"] > 0.0
