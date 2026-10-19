"""Issue #35: the CPU and GPU evaluation backends must return the same schema.

Historically the 6-DoF reference path and the CuPy batch path each emitted
their own key set.  The GPU path was missing ``stability`` entirely, which
aborted any optimisation run that selected the ``max_stability`` objective on
a CUDA host, and the specification's claim of a "completely identical"
schema was not covered by any test.

The contract itself lives in :mod:`physics.payload_contract`.
"""

from __future__ import annotations

import pytest

from physics import flight_sim, parallel_engine
from physics.optimizer_domain import OBJECTIVE_SOURCES
from physics.payload_contract import PAYLOAD_KEYS, normalize_payload


AIRCRAFT = {"mass_kg": 0.004, "wingspan_m": 0.20, "wing_area_m2": 0.02}
ENVIRONMENT = {"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0}
LAUNCH = {"launch_speed": 8.0, "launch_angle_deg": 0.0, "launch_height_m": 5.0}


def _case(duration_s: float = 3.0) -> dict:
    return {
        "aircraft": dict(AIRCRAFT),
        "environment": dict(ENVIRONMENT),
        "launch": dict(LAUNCH),
        "duration_s": duration_s,
        "dt_max": 0.02,
    }


def test_reference_backend_emits_the_full_contract():
    payload = flight_sim.evaluate_flight(AIRCRAFT, ENVIRONMENT, LAUNCH, 3.0, 0.02)
    assert set(payload) == set(PAYLOAD_KEYS)


def test_evaluate_case_emits_the_full_contract():
    payload = parallel_engine.evaluate_case(_case())
    assert set(payload) == set(PAYLOAD_KEYS)


def test_batch_fallback_emits_the_full_contract():
    payloads = parallel_engine.gpu_batch_evaluate_cases([_case(), _case()], force_cpu=True)
    assert payloads
    for payload in payloads:
        assert set(payload) == set(PAYLOAD_KEYS)


def test_cupy_backend_emits_the_full_contract():
    """The real CuPy path must emit exactly the same key set."""
    try:
        import cupy  # noqa: F401
    except Exception:
        pytest.skip("CuPy is not installed")
    info = parallel_engine.get_gpu_info()
    if not info.get("available") or info.get("backend") != "cupy":
        pytest.skip("no usable CUDA device")

    payloads = parallel_engine.gpu_batch_evaluate_cases([_case(), _case()])
    assert payloads
    for payload in payloads:
        assert set(payload) == set(PAYLOAD_KEYS)
        assert payload["model"] == "translational_ensemble"
        assert payload["approximate"] is True
        assert payload["physics_verification_eligible"] is False


def test_every_objective_source_is_part_of_the_contract():
    """A missing contract key would abort the optimiser mid-run."""
    for objective, source in OBJECTIVE_SOURCES.items():
        assert source in PAYLOAD_KEYS, f"objective '{objective}' reads '{source}'"


def test_reduced_order_backend_reports_unmeasurable_values_explicitly():
    """A reduced-order backend must say "not measurable" instead of omitting a key."""
    payload = normalize_payload(
        {"distance_m": 12.5, "status": "landed"},
        model="translational_ensemble",
        approximate=True,
        physics_verification_eligible=False,
        limitations=("reduced order",),
    )
    assert set(payload) == set(PAYLOAD_KEYS)
    # ``stability`` cannot be measured on that backend: it is reported as
    # unavailable (None), not as a fabricated zero.
    assert payload["stability"] is None
    assert payload["distance_m"] == 12.5
    assert payload["approximate"] is True
    assert payload["physics_verification_eligible"] is False
    assert payload["limitations"] == ["reduced order"]


def test_objective_value_names_the_backend_that_cannot_serve_it():
    """A reduced-order payload must produce an actionable error, not a bare KeyError."""
    from physics.optimizer_domain import objective_value

    payload = normalize_payload(
        {"status": "landed"},
        model="translational_ensemble",
        approximate=True,
        physics_verification_eligible=False,
        limitations=("reduced order",),
    )
    with pytest.raises(ValueError) as excinfo:
        objective_value("max_stability", payload)
    message = str(excinfo.value)
    assert "max_stability" in message
    assert "translational_ensemble" in message
    assert "stability" in message


def test_optimizer_rejects_reference_only_objectives_on_the_gpu_backend():
    """The optimiser must fail fast instead of crashing half way through a run."""
    from physics.optimizer_domain import unsupported_objectives

    assert unsupported_objectives(["max_distance", "max_stability"], "rigid_body_6dof") == []
    assert unsupported_objectives(["max_distance"], "translational_ensemble") == []
    assert unsupported_objectives(["max_stability"], "translational_ensemble") == ["max_stability"]
