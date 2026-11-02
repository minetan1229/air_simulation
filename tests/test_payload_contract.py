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
from physics.payload_contract import CORE_KEYS, PAYLOAD_KEYS, normalize_payload


AIRCRAFT = {"mass_kg": 0.004, "wingspan_m": 0.20, "wing_area_m2": 0.02}
ENVIRONMENT = {"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0}
LAUNCH = {"launch_speed": 8.0, "launch_angle_deg": 0.0, "launch_height_m": 5.0}

#: The contractual key set, written out **literally**.
#:
#: Comparing a payload against :data:`physics.payload_contract.PAYLOAD_KEYS`
#: alone cannot detect a key that was *removed* from the contract, because
#: both sides of the comparison would move together.  That is exactly the
#: 2026-10-20 regression: ``error`` / ``error_id`` disappeared from
#: ``CORE_KEYS`` while ``physics.flight_sim.summarize_flight`` kept emitting
#: them, and every self-referential contract test stayed green.  The expected
#: set is therefore pinned here, and the contract module is checked against it.
EXPECTED_CONTRACT_KEYS = frozenset(
    {
        # core metrics
        "distance_m",
        "flight_time_s",
        "max_altitude_m",
        "max_ld_ratio",
        "max_stress_index",
        "max_stress_pa",
        "launch_speed_mps",
        "stall_margin",
        "stability",
        "landing_distance_m",
        "points",
        "duration_s",
        "backend",
        "status",
        "apogee_time_s",
        "post_apogee_time_s",
        "glide_entry_time_s",
        "stable_glide_duration_s",
        "apex_recovery_status",
        # failure reporting
        "error",
        "error_id",
        # diagnostics
        "range_x_m",
        "range_y_m",
        "horizontal_range_m",
        "max_speed_mps",
        "max_g_load",
        "stalled",
        "landed",
        "crashed",
        "diverged",
        "device_name",
        # metadata
        "model",
        "model_detail",
        "approximate",
        "physics_verification_eligible",
        "limitations",
    }
)


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
        # Compared with the **pinned literal** set, not only with the
        # contract module: a key that left the contract together with the
        # backend would still satisfy a self-referential comparison.
        assert set(payload) == EXPECTED_CONTRACT_KEYS
        assert set(payload) == set(PAYLOAD_KEYS)
        assert payload["model"] in ("translational_ensemble", parallel_engine.RIGID_BODY_MODEL_TAG)
        # The failure-reporting keys are present on the GPU path as well.
        assert "error" in payload and "error_id" in payload


def test_the_contract_key_set_is_pinned():
    """No key may leave the contract without a deliberate change here."""
    assert set(PAYLOAD_KEYS) == EXPECTED_CONTRACT_KEYS
    assert set(CORE_KEYS) <= EXPECTED_CONTRACT_KEYS
    # The reference path publishes exactly the contract keys.
    assert set(flight_sim.EVALUATE_KEYS) == EXPECTED_CONTRACT_KEYS


def test_every_backend_reports_the_same_key_set():
    """The 6-DoF reference, the CPU batch and the CuPy batch agree.

    The three backends are compared **with each other** as well as with the
    pinned set, so a key that is missing from one of them cannot hide behind
    the contract module.  The CuPy backend is checked against the same pinned
    set in ``test_cupy_backend_emits_the_full_contract`` (it is a separate
    evaluation because a CUDA batch costs minutes on a WDDM host).
    """
    reference = flight_sim.evaluate_flight(AIRCRAFT, ENVIRONMENT, LAUNCH, 3.0, 0.02)
    cpu_case = parallel_engine.evaluate_case(_case())
    cpu_batch = parallel_engine.gpu_batch_evaluate_cases([_case(), _case()], force_cpu=True)[0]
    for payload in (reference, cpu_case, cpu_batch):
        assert set(payload) == EXPECTED_CONTRACT_KEYS


def test_failure_reporting_keys_are_present_and_none_on_success():
    """A successful evaluation reports ``error`` / ``error_id`` as ``None``."""
    payload = flight_sim.evaluate_flight(AIRCRAFT, ENVIRONMENT, LAUNCH, 3.0, 0.02)
    assert payload["status"] != "error"
    assert payload["error"] is None
    assert payload["error_id"] is None


def test_normalize_payload_keeps_a_meaningful_none_for_the_error_keys():
    """``None`` means "no failure" and must survive the normalisation."""
    payload = normalize_payload(
        {"status": "landed"},
        model="rigid_body_6dof",
        approximate=False,
        physics_verification_eligible=True,
    )
    assert payload["error"] is None
    assert payload["error_id"] is None


def test_normalize_payload_keeps_a_reported_failure():
    """A reported failure survives the normalisation with its correlation id."""
    payload = normalize_payload(
        {"status": "error", "error": "ValueError", "error_id": "abc123"},
        model="rigid_body_6dof",
        approximate=False,
        physics_verification_eligible=False,
    )
    assert payload["error"] == "ValueError"
    assert payload["error_id"] == "abc123"


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
