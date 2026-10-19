"""Canonical flight-payload contract shared by every evaluation backend.

The optimiser, the REST layer and the tests all consume the payload produced
by a flight evaluation.  Historically the 6-DoF reference path
(:func:`physics.flight_sim.evaluate_flight`) and the CuPy batch path
(:func:`physics.parallel_engine.gpu_batch_evaluate_cases`) each emitted their
own key set, so a client written against one of them crashed on the other --
and the GPU path was missing ``stability`` entirely, which aborted any
optimisation run that selected the ``max_stability`` objective on a CUDA host.

This module is the single source of truth for the contract:

``CORE_KEYS``
    The metrics the optimiser reads.  Their names and meanings are fixed.
``DIAGNOSTIC_KEYS``
    Additional observables that every backend reports.
``METADATA_KEYS``
    Provenance flags that tell a client how much to trust the numbers.

:func:`normalize_payload` guarantees that *every* backend returns exactly the
same key set, filling in a documented default for anything a reduced-order
backend cannot reproduce.  Backends must then advertise the reduced fidelity
through ``approximate``, ``physics_verification_eligible`` and ``limitations``
rather than by silently omitting keys.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

__all__ = [
    "CORE_KEYS",
    "DIAGNOSTIC_KEYS",
    "METADATA_KEYS",
    "PAYLOAD_KEYS",
    "DEFAULT_VALUES",
    "empty_payload",
    "normalize_payload",
]


#: Metrics consumed by the optimiser.  Names and meanings are contractual.
CORE_KEYS: Tuple[str, ...] = (
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
)

#: Further observables reported by every backend.
DIAGNOSTIC_KEYS: Tuple[str, ...] = (
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
)

#: Provenance flags.  ``approximate`` marks a reduced-order backend,
#: ``physics_verification_eligible`` marks a result that may be compared with
#: wind-tunnel or P1 verification data, and ``limitations`` enumerates what the
#: backend could not reproduce.
METADATA_KEYS: Tuple[str, ...] = (
    "model",
    "approximate",
    "physics_verification_eligible",
    "limitations",
)

#: The complete contract: every backend returns exactly these keys.
PAYLOAD_KEYS: Tuple[str, ...] = CORE_KEYS + DIAGNOSTIC_KEYS + METADATA_KEYS


#: Value used when a backend cannot produce a key.  ``None`` marks a value that
#: is genuinely unavailable (as opposed to a measured zero), so that a
#: consumer can distinguish "not measurable on this backend" from "measured
#: as zero".
DEFAULT_VALUES: Dict[str, Any] = {
    "distance_m": 0.0,
    "flight_time_s": 0.0,
    "max_altitude_m": 0.0,
    "max_ld_ratio": 0.0,
    "max_stress_index": 0.0,
    "max_stress_pa": 0.0,
    "launch_speed_mps": 0.0,
    "stall_margin": 0.0,
    "stability": None,
    "landing_distance_m": 0.0,
    "points": 0,
    "duration_s": 0.0,
    "backend": "numpy",
    "status": "diverged",
    "apogee_time_s": 0.0,
    "post_apogee_time_s": 0.0,
    "glide_entry_time_s": None,
    "stable_glide_duration_s": 0.0,
    "apex_recovery_status": "not_observed",
    "range_x_m": 0.0,
    "range_y_m": 0.0,
    "horizontal_range_m": 0.0,
    "max_speed_mps": 0.0,
    "max_g_load": 0.0,
    "stalled": False,
    "landed": False,
    "crashed": False,
    "diverged": False,
    "device_name": None,
    "model": "unknown",
    "approximate": True,
    "physics_verification_eligible": False,
    "limitations": [],
}


def empty_payload(**overrides: Any) -> Dict[str, Any]:
    """Return a payload with every contractual key present, defaults applied."""
    payload: Dict[str, Any] = {key: DEFAULT_VALUES[key] for key in PAYLOAD_KEYS}
    payload.update(overrides)
    return payload


def normalize_payload(
    payload: Dict[str, Any],
    *,
    model: str,
    approximate: bool,
    limitations: Any = None,
    physics_verification_eligible: bool = False,
) -> Dict[str, Any]:
    """Return ``payload`` with exactly the contractual key set.

    Missing keys are filled from :data:`DEFAULT_VALUES`, so a reduced-order
    backend reports "not measurable" (``None``) instead of omitting the key.
    The provenance flags are always overwritten with the caller's values.

    Keys that are *not* part of the contract are preserved, so a backend may
    publish additional diagnostics without breaking the contract.  The
    contract keys themselves are always present, which is what the CPU/GPU
    parity test asserts.
    """
    if not isinstance(payload, dict):
        payload = {}
    normalized = empty_payload()
    for key in PAYLOAD_KEYS:
        if key in payload and payload[key] is not None:
            normalized[key] = payload[key]
        elif key in payload and key in ("glide_entry_time_s", "stability", "device_name"):
            # ``None`` is a meaningful value for these keys.
            normalized[key] = payload[key]
    for key, value in payload.items():
        if key not in PAYLOAD_KEYS:
            normalized[key] = value
    normalized["model"] = model
    normalized["approximate"] = bool(approximate)
    normalized["physics_verification_eligible"] = bool(physics_verification_eligible)
    normalized["limitations"] = list(limitations) if limitations else []
    return normalized
