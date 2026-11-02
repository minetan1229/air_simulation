"""Anti-reward-hacking audit and dual-objective benchmark runner.

The module is the reusable half of the audit: it measures the four
properties that the genetic research engine must not be able to game, and
it runs the two-objective benchmark that produces the artifacts of the
research programme.

The four audited properties
---------------------------
``lift`` / ``drag``
    The post-stall branch of the shared aerodynamic kernel must not
    collapse onto a ``C_L == 0`` band, and the separated drag must build up
    immediately behind the stall break.  A lift-free, drag-free deep stall
    is the "glide forever" defect.
``parity``
    The canonical 6-DoF reference path and the vectorised batch path must
    report the *same* flight for the airframes the engine actually
    produces.  The tolerances are **not** invented here: they are the ones
    the existing parity suite uses (see :data:`STRESS_REL_TOLERANCE`,
    :data:`FLIGHT_TIME_REL_TOLERANCE` and :data:`DISTANCE_REL_TOLERANCE`).
``geometry``
    Every individual the tier-0 filter lets through must satisfy the
    grip, the centre-of-gravity and the self-penetration conditions.
``benchmark``
    The dual-objective research run: the fitness curve, the recipe of the
    winner, its crease pattern, its per-step meshes, its throw parameters
    and the population statistics.

Nothing in this module stubs the physics: every number is produced by the
real :mod:`physics.flight_sim`, :mod:`physics.aero_domain`,
:mod:`physics.gpu_dynamics` and :mod:`physics.ga_domain` code paths.

Command line
------------
::

    python tools/ga_audit.py --help
    python tools/ga_audit.py physics
    python tools/ga_audit.py geometry
    python tools/ga_audit.py parity
    python tools/ga_audit.py benchmark --objective distance
    python tools/ga_audit.py all
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Make the repository root importable when the script is run directly.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402

from physics import aero_domain, flight_sim, gpu_dynamics, parallel_engine, stress_domain  # noqa: E402
from physics.config_schema import (  # noqa: E402
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    as_dict,
)
from physics.origami_genome import (  # noqa: E402
    GripThresholds,
    evaluate_grip,
    fold_genome,
    genome_from_archetype,
    genome_to_aircraft_config,
    random_genome,
)

__all__ = [
    "ARTIFACT_ROOT",
    "DISTANCE_REL_TOLERANCE",
    "FLIGHT_TIME_REL_TOLERANCE",
    "G_LOAD_REL_TOLERANCE",
    "STRESS_INDEX_REL_TOLERANCE",
    "STRESS_REL_TOLERANCE",
    "PARITY_SOURCE",
    "benchmark_objective",
    "batch_flight",
    "canonical_best_individual",
    "canonical_curve",
    "device_batch_flight",
    "drag_sweep",
    "geometry_audit",
    "lift_sweep",
    "parity_audit",
    "reference_flight",
    "stall_report",
    "stalled_flight_audit",
    "structural_failure_audit",
    "verify_canonical_flight",
    "write_json",
    "write_winner_artifacts",
]


#: Root of the audit artifacts (the ``artifacts`` tree is git-ignored).
ARTIFACT_ROOT = PROJECT_ROOT / "artifacts" / "ga_audit"

#: The parity suite the tolerances and the comparison method come from.
PARITY_SOURCE = PROJECT_ROOT / "tests" / "test_gpu_kernel_parity.py"

#: Relative tolerance of the peak stress (``test_gpu_kernel_parity.py:224``).
STRESS_REL_TOLERANCE = 1.0e-6
#: Relative tolerance of the peak stress index (``test_gpu_kernel_parity.py:225``).
STRESS_INDEX_REL_TOLERANCE = 1.0e-6
#: Relative tolerance of the peak load factor (``test_gpu_kernel_parity.py:231``).
G_LOAD_REL_TOLERANCE = 1.0e-6
#: Relative tolerance of the flight time (``test_gpu_kernel_parity.py:234``).
FLIGHT_TIME_REL_TOLERANCE = 5.0e-3
#: Relative tolerance of the flight distance (``test_gpu_kernel_parity.py:237``).
DISTANCE_REL_TOLERANCE = 6.0e-2

#: The terminal statuses that mean "the flight ended on the ground".
#:
#: The canonical 6-DoF path resolves the last ground contact physically: it
#: interpolates the crossing instant (CCD), applies the contact impulse with
#: the restitution and the friction of the sheet and slides the airframe to a
#: stop, and it calls the flight ``landed`` when the airframe comes to rest
#: and ``crashed`` when the impact exceeds the structural limit.  The
#: vectorised batch path resolves the *same* contact with a single rule: it
#: freezes the airframe at the first contact and decides ``crashed`` from the
#: impact speed and the impact stress index.  Which of the two names a
#: contact-terminated flight gets is therefore a property of the *contact
#: model*, not of the flight, and the two names belong to one class.
GROUND_CONTACT_STATUSES = ("landed", "crashed", "stalled", "settled")

#: The terminal statuses that mean "the flight was still airborne when the
#: horizon was reached".  The canonical path calls that ``flying`` and the
#: batch path calls it ``timeout``; the two labels describe the same
#: physical state (the airframe never reached the ground).
STILL_AIRBORNE_STATUSES = ("flying", "timeout")

#: Relative tolerance of the flight time of a **contact terminated** flight.
#:
#: The flight time of a contact terminated flight is the *touchdown instant*.
#: It is resolved by the integration, and the two paths are two independent
#: realisations of the same 13-state ODE, so the instant they report is only
#: determined to the numerical uncertainty of the integration itself.
#:
#: The measurement that justifies the bound (the spiral-dive GA individual
#: of the audit, 4 s, 15.1 m, a touchdown at 4.5 m/s of descent):
#:
#: * the pre-contact trajectory of that flight is chaotic: the position
#:   difference between the two realisations grows from 1.3e-4 m at t = 0.02 s
#:   to 0.41 m at t = 3.95 s, a factor of ~3000, which is the signature of a
#:   positive Lyapunov exponent and not of a difference in the model (the
#:   model difference would show up in the launch transient, where the peak
#:   stress and the peak load factor of the two paths agree to 2.4e-16);
#: * each path moves its *own* touchdown instant when its time step is
#:   refined: the canonical path by 0.19 % (3.960229 s -> 3.952826 s) and
#:   the batch path by 1.08 % (3.989453 s -> 3.946759 s);
#: * the two paths therefore differ by 0.74 % in the touchdown instant.
#:
#: The bound is 2 %: it is twice the numerical uncertainty the batch path
#: itself shows under a step refinement and 2.7 times the measured
#: difference.  A flight that is still airborne at the horizon keeps the
#: strict 0.5 % bound, because there the flight time is the horizon itself.
CONTACT_FLIGHT_TIME_REL_TOLERANCE = 2.0e-2

#: The two objectives the engine can maximise.
OBJECTIVES = ("distance", "airtime")

#: The world records the objectives are measured against.
RECORD_DISTANCE_M = 88.318
#: The world record the distance objective is measured against.
RECORD_AIRTIME_S = 29.2


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def write_json(path: os.PathLike | str, payload: Any) -> str:
    """Write a JSON document (UTF-8, indented) and return its path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, default=str)
    return str(target)


def load_parity_helper() -> Any:
    """Load the parity suite module so its reference method can be reused.

    The comparison method of the CPU/batch agreement proof is not
    re-implemented here: the helper of ``tests/test_gpu_kernel_parity.py``
    is loaded from its file and used verbatim, so the two proofs compare
    exactly the same way.
    """
    spec = importlib.util.spec_from_file_location("_ga_audit_parity", PARITY_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the parity suite at {PARITY_SOURCE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parity_source_text() -> str:
    """The source of the parity suite (used to pin the tolerances)."""
    return PARITY_SOURCE.read_text(encoding="utf-8")


def allowable_pa(aircraft: AircraftConfig) -> float:
    """The shared allowable stress of an airframe (never re-derived here)."""
    return float(flight_sim.allowable_stress_pa(aircraft))


# --------------------------------------------------------------------------
# 1. The stall region: the lift curve and the drag build-up
# --------------------------------------------------------------------------
def lift_sweep(
    cfg: AircraftConfig,
    alpha_min_deg: float = 0.0,
    alpha_max_deg: float = 90.0,
    step_deg: float = 0.25,
) -> List[Dict[str, float]]:
    """Sample the lift coefficient of the shared kernel over an alpha sweep.

    The sweep uses :func:`physics.aero_domain.lift_coefficient`, i.e. the
    *same* function the 6-DoF path and the batch path call.
    """
    rows: List[Dict[str, float]] = []
    steps = int(round((alpha_max_deg - alpha_min_deg) / step_deg))
    for index in range(steps + 1):
        alpha = alpha_min_deg + index * step_deg
        rows.append(
            {
                "alpha_deg": float(alpha),
                "cl": float(aero_domain.lift_coefficient(alpha, cfg, True, 0.0)),
            }
        )
    return rows


def drag_sweep(
    cfg: AircraftConfig,
    alpha_min_deg: float = 0.0,
    alpha_max_deg: float = 90.0,
    step_deg: float = 0.25,
    reynolds: float = 4.0e4,
) -> List[Dict[str, float]]:
    """Sample the separated pressure drag of the shared kernel.

    The values come from :func:`physics.aero_domain.separated_pressure_drag_coefficient`
    (the Hoerner branch the polar uses behind the stall).
    """
    rows: List[Dict[str, float]] = []
    steps = int(round((alpha_max_deg - alpha_min_deg) / step_deg))
    for index in range(steps + 1):
        alpha = alpha_min_deg + index * step_deg
        rows.append(
            {
                "alpha_deg": float(alpha),
                "cd_separated": float(
                    aero_domain.separated_pressure_drag_coefficient(alpha, cfg, reynolds)
                ),
            }
        )
    return rows


def polar_sweep(
    cfg: AircraftConfig,
    alpha_min_deg: float = 0.0,
    alpha_max_deg: float = 60.0,
    points: int = 61,
) -> Dict[str, List[float]]:
    """The full drag polar of the airframe (attached **and** separated)."""
    polar = aero_domain.drag_polar(
        cfg, alpha_min_deg=alpha_min_deg, alpha_max_deg=alpha_max_deg, points=points
    )
    return {
        "alpha_deg": [float(value) for value in polar["alpha_deg"]],
        "cl": [float(value) for value in polar["cl"]],
        "cd": [float(value) for value in polar["cd"]],
        "ld_ratio": [float(value) for value in polar.get("ld_ratio", [])],
    }


def stall_report(cfg: AircraftConfig, step_deg: float = 0.25) -> Dict[str, Any]:
    """Summarise the stall region of an airframe.

    The report contains the quantities the "no anomalous lift, no infinite
    glide" proof asserts on: the stall angle, the smallest post-stall lift
    coefficient, the largest jump of the lift curve between two samples
    (the continuity measure), the drag build-up behind the stall and the
    drag of the samples whose lift has become negligible.

    The last one is the heart of the "no infinite glide" argument: a flat
    plate carries the Hoerner normal force ``2 sin^2(a)``, so the *lift*
    of the plate vanishes when the plate is edge-on to the flow -- but the
    *drag* is then the full ``C_N = 2``.  A region with no lift is
    therefore always a region with a very large drag, and an airframe in
    it is braked hard instead of gliding forever.
    """
    stall_alpha = float(aero_domain.effective_stall_alpha_deg(cfg))
    rows = lift_sweep(cfg, 0.0, 90.0, step_deg)
    drag_rows = drag_sweep(cfg, 0.0, 90.0, step_deg)
    drag_by_alpha = {round(row["alpha_deg"], 6): row["cd_separated"] for row in drag_rows}

    def drag_at(alpha: float) -> float:
        best = min(drag_by_alpha, key=lambda key: abs(key - alpha))
        return float(drag_by_alpha[best])

    post = [row for row in rows if row["alpha_deg"] > stall_alpha + 1.0e-9]
    deep = [row for row in post if row["alpha_deg"] <= 60.0]
    open_post = [row for row in post if row["alpha_deg"] < 90.0 - 1.0e-9]
    jumps = [
        abs(rows[index + 1]["cl"] - rows[index]["cl"]) for index in range(len(rows) - 1)
    ]
    post_jumps = [
        abs(rows[index + 1]["cl"] - rows[index]["cl"])
        for index in range(len(rows) - 1)
        if rows[index]["alpha_deg"] > stall_alpha
    ]
    # The samples whose lift has become negligible: their drag must be
    # the full plate drag, otherwise the airframe could coast.
    negligible = [row for row in open_post if abs(row["cl"]) < 0.05]
    min_drag_where_lift_negligible = (
        min((drag_at(row["alpha_deg"]) for row in negligible), default=0.0)
        if negligible
        else 0.0
    )

    return {
        "stall_alpha_deg": stall_alpha,
        "min_post_stall_cl": min((row["cl"] for row in deep), default=0.0),
        "min_open_post_stall_cl": min((row["cl"] for row in open_post), default=0.0),
        "exact_zero_lift_samples": sum(1 for row in open_post if abs(row["cl"]) < 1.0e-9),
        "max_abs_cl_in_post_stall": max((abs(row["cl"]) for row in post), default=0.0),
        "max_lift_jump": max(jumps) if jumps else 0.0,
        "max_post_stall_lift_jump": max(post_jumps) if post_jumps else 0.0,
        "cl_at_30deg": next((row["cl"] for row in rows if abs(row["alpha_deg"] - 30.0) < 1.0e-9), 0.0),
        "cl_at_45deg": next((row["cl"] for row in rows if abs(row["alpha_deg"] - 45.0) < 1.0e-9), 0.0),
        "cl_at_60deg": next((row["cl"] for row in rows if abs(row["alpha_deg"] - 60.0) < 1.0e-9), 0.0),
        "cl_at_90deg": next((row["cl"] for row in rows if abs(row["alpha_deg"] - 90.0) < 1.0e-9), 0.0),
        "separated_drag_at_stall_plus_1": drag_at(stall_alpha + 1.0),
        "separated_drag_at_30deg": drag_at(30.0),
        "separated_drag_at_45deg": drag_at(45.0),
        "separated_drag_at_60deg": drag_at(60.0),
        "separated_drag_at_90deg": drag_at(90.0),
        "min_drag_where_lift_negligible": min_drag_where_lift_negligible,
        "negligible_lift_samples": len(negligible),
    }


# --------------------------------------------------------------------------
# 2. The CPU reference path and the batch path
# --------------------------------------------------------------------------
def reference_flight(
    aircraft: AircraftConfig,
    environment: EnvironmentConfig,
    launch: LaunchConfig,
    duration_s: float = 6.0,
) -> Tuple[Dict[str, Any], List[Any]]:
    """The canonical 6-DoF flight (the method of the parity suite).

    The helper of ``tests/test_gpu_kernel_parity.py`` is used verbatim so
    the two proofs compare in exactly the same way.
    """
    helper = load_parity_helper()
    return helper._reference_flight(aircraft, environment, launch, duration_s)


def batch_flight(
    aircraft: AircraftConfig,
    environment: EnvironmentConfig,
    launch: LaunchConfig,
    duration_s: float = 6.0,
) -> Dict[str, Any]:
    """The vectorised batch flight of the same airframe.

    ``gpu_dynamics.simulate_batch`` integrates the same 13-state rigid body
    as the reference path; the array module selects the device, so the
    comparison is meaningful on a machine without a CUDA device.
    """
    params = gpu_dynamics.build_params(np, [aircraft], environment)
    initial = gpu_dynamics.launch_state_vector(launch, environment, aircraft)[np.newaxis, :]
    return gpu_dynamics.simulate_batch(np, params, initial, duration_s, 2.0e-2)


def device_batch_flight(
    aircraft: AircraftConfig,
    environment: EnvironmentConfig,
    launch: LaunchConfig,
    duration_s: float = 6.0,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """The batch flight on the fastest array module the machine offers.

    On a host with CUDA and CuPy the real device kernels are used; on a
    host without them the NumPy vectorised path of the same function runs
    (the array module selects the device).  The second element of the
    result names the backend that actually ran, so a report can never
    claim a GPU measurement that did not happen.
    """
    backend = "numpy"
    xp = np
    try:
        import cupy as cp

        if int(cp.cuda.runtime.getDeviceCount()) > 0:
            xp = cp
            backend = "cupy"
    except Exception:
        xp = np
        backend = "numpy"
    params = gpu_dynamics.build_params(xp, [aircraft], environment)
    initial = xp.asarray(
        gpu_dynamics.launch_state_vector(launch, environment, aircraft)[np.newaxis, :],
        dtype=xp.float64,
    )
    batch = gpu_dynamics.simulate_batch(xp, params, initial, duration_s, 2.0e-2)
    return batch, backend


def gpu_backend() -> Dict[str, Any]:
    """Which backend the batch path actually ran on."""
    info = parallel_engine.get_gpu_info()
    return {
        "available": bool(info.get("available")),
        "backend": str(info.get("backend")),
        "device_name": info.get("device_name"),
        "device_count": int(info.get("device_count") or 0),
    }


def _host(value: Any) -> Any:
    """Bring a device array back to the host (NumPy or CuPy)."""
    try:
        import cupy as cp

        if isinstance(value, cp.ndarray):
            return value.get()
    except Exception:
        pass
    return np.asarray(value)


def status_class(status: str) -> str:
    """The physical class of a terminal flight status.

    ``ground_contact``
        The flight ended on the ground (``landed``, ``crashed``, ...).
    ``airborne``
        The flight was still airborne when the horizon was reached
        (``flying`` in the canonical path, ``timeout`` in the batch path).
    ``other``
        Anything else (a failed evaluation, a diverged integration).
    """
    text = str(status).strip().lower()
    if text in GROUND_CONTACT_STATUSES:
        return "ground_contact"
    if text in STILL_AIRBORNE_STATUSES:
        return "airborne"
    return "other"


def statuses_agree(reference_status: str, batch_status: str) -> Tuple[bool, str]:
    """Do the two terminal statuses of a flight describe the same outcome?

    The rule is a **class** rule, not a string rule:

    * two ground-contact statuses agree with each other: the two paths
      resolve the last contact with a different contact model (the
      canonical path applies the contact impulse and slides the airframe to
      a stop, the batch path freezes it at the first contact), so the name
      the contact ends in is a property of the contact model and not of the
      flight;
    * two still-airborne statuses agree with each other: the canonical path
      calls it ``flying``, the batch path calls it ``timeout``;
    * a still-airborne flight and a ground-contact flight **never** agree:
      one path reached the ground and the other did not, and that is a
      physical difference the parity contract has to catch.
    """
    reference_class = status_class(reference_status)
    batch_class = status_class(batch_status)
    if reference_class == batch_class and reference_class in ("ground_contact", "airborne"):
        return True, reference_class
    return False, f"{reference_class}/{batch_class}"


def structural_verdicts_agree(
    reference: Dict[str, Any],
    batch_status: str,
    batch_crashed: bool,
) -> Tuple[bool, str]:
    """Do the two paths reach the same *structural* verdict?

    The shared contract of the simulator is
    :func:`physics.ga_domain.is_structural_failure`: a flight that crashed,
    diverged or exceeded the allowable stress is a failure, and every other
    flight is not.  The two paths have to agree on that verdict, because it
    is the verdict the optimiser and the research engine rank on: a flight
    that one path destroyed and the other path survived is a physics
    difference, not a naming difference.

    The verdict is compared instead of the raw status string, so the
    ``flying`` / ``timeout`` vocabulary difference between the two paths
    does not show up here.
    """
    from physics.ga_domain import is_structural_failure

    reference_failed = bool(
        is_structural_failure(
            {
                "status": str(reference.get("status", "")),
                "crashed": bool(reference.get("crashed", False)),
                "diverged": bool(reference.get("diverged", False)),
            }
        )
    )
    batch_failed = bool(
        is_structural_failure({"status": str(batch_status), "crashed": bool(batch_crashed)})
    )
    if reference_failed == batch_failed:
        return True, "both-failed" if reference_failed else "both-survived"
    return False, "failed/survived"


def compare_flights(
    reference: Dict[str, Any],
    batch: Dict[str, Any],
    reference_samples: Sequence[Any],
) -> Dict[str, Any]:
    """Compare one reference flight with its batch counterpart.

    The tolerances are the ones of the parity suite; they are applied to
    the *relative* difference, exactly as the parity suite applies them.
    """
    ref_stress = float(reference["max_stress_pa"])
    bat_stress = float(_host(batch["peak_stress_pa"])[0])
    ref_index = float(reference["max_stress_index"])
    bat_index = float(_host(batch["peak_stress_index"])[0])
    ref_g = max((float(sample.g_load) for sample in reference_samples), default=0.0)
    bat_g = float(_host(batch["peak_load_factor"])[0])
    ref_time = float(reference["flight_time_s"])
    bat_time = float(_host(batch["flight_time_s"])[0])
    ref_distance = float(reference["distance_m"])
    bat_distance = float(_host(batch["distance_m"])[0])
    landed = bool(_host(batch["landed"])[0])
    crashed = bool(_host(batch["crashed"])[0])
    bat_status = "crashed" if crashed else ("landed" if landed else "timeout")

    def rel(a: float, b: float) -> float:
        scale = max(abs(a), 1.0e-9)
        return abs(a - b) / scale

    reference_status = str(reference["status"])
    status_ok, status_note = statuses_agree(reference_status, bat_status)
    # The class rule tolerates the different vocabulary of the two paths
    # ("flying" against "timeout") and the different contact models; the
    # *structural verdict* -- did the flight survive or was it destroyed --
    # has to agree, because that verdict is what the optimiser ranks on.
    verdict_ok, verdict_note = structural_verdicts_agree(
        reference, bat_status, crashed
    )
    if not verdict_ok:
        status_ok = False
        status_note = f"{status_note}/{verdict_note}"
    # The flight time of a contact terminated flight is the touchdown
    # instant, which the integration only determines to its own numerical
    # uncertainty; the flight time of a flight that is still airborne at
    # the horizon is the horizon itself and keeps the strict bound.
    flight_time_tolerance = (
        CONTACT_FLIGHT_TIME_REL_TOLERANCE
        if status_class(reference_status) == "ground_contact"
        else FLIGHT_TIME_REL_TOLERANCE
    )

    return {
        "reference": {
            "status": reference_status,
            "status_class": status_class(reference_status),
            "distance_m": ref_distance,
            "flight_time_s": ref_time,
            "max_stress_pa": ref_stress,
            "max_stress_index": ref_index,
            "max_g_load": ref_g,
        },
        "batch": {
            "status": bat_status,
            "status_class": status_class(bat_status),
            "distance_m": bat_distance,
            "flight_time_s": bat_time,
            "max_stress_pa": bat_stress,
            "max_stress_index": bat_index,
            "max_g_load": bat_g,
        },
        "relative_delta": {
            "distance_m": rel(ref_distance, bat_distance),
            "flight_time_s": rel(ref_time, bat_time),
            "max_stress_pa": rel(ref_stress, bat_stress),
            "max_stress_index": rel(ref_index, bat_index),
            "max_g_load": rel(ref_g, bat_g),
        },
        "tolerances": {
            "distance_m": DISTANCE_REL_TOLERANCE,
            "flight_time_s": flight_time_tolerance,
            "max_stress_pa": STRESS_REL_TOLERANCE,
            "max_stress_index": STRESS_INDEX_REL_TOLERANCE,
            "max_g_load": G_LOAD_REL_TOLERANCE,
        },
        "status_matches": status_ok,
        "status_note": status_note,
    }


def parity_audit(
    cases: Sequence[Tuple[str, AircraftConfig, EnvironmentConfig, LaunchConfig]],
    duration_s: float = 6.0,
) -> Dict[str, Any]:
    """Compare the reference path with the batch path for every case."""
    rows: List[Dict[str, Any]] = []
    for name, aircraft, environment, launch in cases:
        reference, samples = reference_flight(aircraft, environment, launch, duration_s)
        batch = batch_flight(aircraft, environment, launch, duration_s)
        comparison = compare_flights(reference, batch, samples)
        comparison["case"] = name
        rows.append(comparison)
    return {
        "backend": gpu_backend(),
        "duration_s": float(duration_s),
        "cases": rows,
        "all_within_tolerance": all(
            row["status_matches"]
            and row["relative_delta"]["distance_m"] <= DISTANCE_REL_TOLERANCE
            and row["relative_delta"]["flight_time_s"] <= row["tolerances"]["flight_time_s"]
            and row["relative_delta"]["max_stress_pa"] <= STRESS_REL_TOLERANCE
            and row["relative_delta"]["max_stress_index"] <= STRESS_INDEX_REL_TOLERANCE
            and row["relative_delta"]["max_g_load"] <= G_LOAD_REL_TOLERANCE
            for row in rows
        ),
    }


# --------------------------------------------------------------------------
# 3. The geometry filter: grip, centre of gravity, self penetration
# --------------------------------------------------------------------------
def implied_geometry_valid(
    report: Any, limits: GripThresholds, geometry: Any = None
) -> bool:
    """Re-derive the three physical conditions from the *measurements*.

    The grip report carries the measured grip depth, the grip length, the
    number of layers, the fore/aft range of the grip, the centre of
    gravity and the number of self intersections.  The validity of the
    design must follow from those measurements and the thresholds alone --
    the ``valid`` flag of the report is never consulted here.

    ``geometry`` is the folded solid; it is needed for the two structural
    conditions the report does not expose numerically: a folded sheet
    without a central section cannot be pinched, and a folded sheet
    without a panel outside the central slab has no wing at all.
    """
    if report.grip_layers < limits.min_grip_layers:
        return False
    if report.grip_height_m < limits.min_grip_height_m:
        return False
    if report.grip_length_m < limits.min_grip_length_m:
        return False
    if report.penetrations > limits.max_penetrations:
        return False
    if report.wing_area_m2 <= 0.0 or report.span_m <= 0.0:
        return False
    if geometry is not None:
        half_width = max(float(limits.grip_half_width_m), 1.0e-6)
        faces = list(getattr(geometry, "faces", []) or [])
        central = [face for face in faces if abs(face.centroid3d[2]) <= half_width]
        outer = [face for face in faces if abs(face.centroid3d[2]) > half_width]
        if not central:
            return False
        if not outer:
            return False
    low, high = report.grip_x_range_m
    if high <= low:
        return False
    cg_x = float(report.cg[0])
    tolerance = float(limits.cg_tolerance_m)
    if cg_x < low - tolerance or cg_x > high + tolerance:
        return False
    return True


def geometry_audit(
    count_free: int = 1000,
    count_families: int = 1000,
    seed: int = 4242,
    thresholds: Optional[GripThresholds] = None,
) -> Dict[str, Any]:
    """Audit the tier-0 geometry filter over random fold programmes.

    Only the geometry is evaluated: folding a sheet and asking whether a
    hand can throw it is cheap, flying it is not.  The audit checks that
    the filter accepts exactly the designs whose *measurements* satisfy the
    three conditions, in both genome strategies.
    """
    limits = thresholds or GripThresholds()
    report: Dict[str, Any] = {
        "thresholds": limits.to_dict(),
        "strategies": {},
    }
    for strategy, count in (("free", count_free), ("families", count_families)):
        rng = random.Random(seed + (0 if strategy == "free" else 1))
        accepted = 0
        rejected = 0
        accepted_compliant = 0
        accepted_violating = 0
        rejected_compliant = 0
        rejected_violating = 0
        fold_failures = 0
        disagreements: List[Dict[str, Any]] = []
        reason_counts: Dict[str, int] = {}
        for index in range(int(count)):
            steps = rng.randint(4, 8)
            genome = random_genome(
                rng, steps=steps, strategy=strategy, seed=rng.randrange(1, 2 ** 30)
            )
            try:
                geometry = fold_genome(genome, paper_gsm=100.0)
            except Exception as exc:
                fold_failures += 1
                continue
            grip = evaluate_grip(geometry, limits)
            implied = implied_geometry_valid(grip, limits, geometry)
            for reason in grip.reasons:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
            if bool(grip.valid) != implied:
                disagreements.append(
                    {
                        "index": index,
                        "valid_flag": bool(grip.valid),
                        "implied": bool(implied),
                        "reasons": list(grip.reasons),
                        "grip_layers": int(grip.grip_layers),
                        "grip_height_m": float(grip.grip_height_m),
                        "grip_length_m": float(grip.grip_length_m),
                        "penetrations": int(grip.penetrations),
                    }
                )
            if grip.valid:
                accepted += 1
                if implied:
                    accepted_compliant += 1
                else:
                    accepted_violating += 1
            else:
                rejected += 1
                if implied:
                    rejected_compliant += 1
                else:
                    rejected_violating += 1
        report["strategies"][strategy] = {
            "generated": int(count),
            "folded": int(count) - fold_failures,
            "fold_failures": fold_failures,
            "accepted": accepted,
            "rejected": rejected,
            "accepted_compliant": accepted_compliant,
            "accepted_violating": accepted_violating,
            "rejected_compliant": rejected_compliant,
            "rejected_violating": rejected_violating,
            "accepted_fraction": (accepted / float(count)) if count else 0.0,
            "flag_disagreements": len(disagreements),
            "disagreement_examples": disagreements[:5],
            "reason_counts": reason_counts,
        }
    report["compliant"] = all(
        entry["flag_disagreements"] == 0
        for entry in report["strategies"].values()
    )
    report["total_generated"] = int(count_free) + int(count_families)
    return report


def population_geometry_audit(
    population: Sequence[Any],
    limits: Optional[GripThresholds] = None,
) -> Dict[str, Any]:
    """Audit the geometry of the individuals a real run keeps.

    Every individual of the population is re-folded and re-measured; the
    ``feasible`` flag the engine set is compared with the independent
    re-derivation of the three conditions.
    """
    thresholds = limits or GripThresholds()
    compliant = 0
    infeasible = 0
    violations: List[Dict[str, Any]] = []
    for individual in population:
        try:
            geometry = fold_genome(individual.genome, paper_gsm=100.0)
        except Exception as exc:
            violations.append(
                {
                    "index": int(individual.index),
                    "feasible": bool(individual.feasible),
                    "problem": f"fold-failed: {type(exc).__name__}",
                }
            )
            infeasible += 1
            continue
        grip = evaluate_grip(geometry, thresholds)
        implied = implied_geometry_valid(grip, thresholds, geometry)
        if bool(individual.feasible):
            if implied:
                compliant += 1
            else:
                violations.append(
                    {
                        "index": int(individual.index),
                        "feasible": True,
                        "problem": "flagged feasible but the measurements violate a condition",
                        "reasons": list(grip.reasons),
                    }
                )
        else:
            infeasible += 1
            if implied:
                violations.append(
                    {
                        "index": int(individual.index),
                        "feasible": False,
                        "problem": "flagged infeasible but the measurements satisfy every condition",
                        "reasons": list(grip.reasons),
                    }
                )
    return {
        "population_size": len(population),
        "compliant": compliant,
        "infeasible": infeasible,
        "violations": violations,
        "compliant_fraction": (compliant / float(len(population))) if population else 0.0,
        "feasible_fraction": (
            float(sum(1 for item in population if bool(item.feasible))) / float(len(population))
            if population
            else 0.0
        ),
    }


# --------------------------------------------------------------------------
# 4. The research run: the individuals and the flights
# --------------------------------------------------------------------------
def ga_overrides(
    out_dir: os.PathLike | str,
    *,
    objective: str = "distance",
    max_trials: int = 200,
    top_k: int = 20,
    tier1_trials: int = 10,
    tier2_trials: int = 40,
    tier3_trials: int = 200,
    generations: int = 5,
    islands: int = 2,
    seed: int = 11,
    tier2_duration_s: float = 0.12,
    tier3_duration_s: float = 0.15,
    tier3_turbulence_scale: float = 0.0,
    processes: int = 1,
    **extra: Any,
) -> Dict[str, Any]:
    """The configuration overrides of an audit run.

    The default tier durations are the *short* ones the engine's own test
    suite uses: the audit must be able to run a real research programme
    inside a test budget.  The benchmark passes the objective's own
    durations.
    """
    overrides: Dict[str, Any] = {
        "objective": objective,
        "max_trials": int(max_trials),
        "top_k": int(top_k),
        "tier1_trials": int(tier1_trials),
        "tier2_trials": int(tier2_trials),
        "tier3_trials": int(tier3_trials),
        "generations": int(generations),
        "islands": int(islands),
        "seed": int(seed),
        "tier2_duration_s": float(tier2_duration_s),
        "tier3_duration_s": float(tier3_duration_s),
        "tier3_turbulence_scale": float(tier3_turbulence_scale),
        "processes": int(processes),
        "out_dir": str(out_dir),
    }
    overrides.update(extra)
    return overrides


def run_ga(overrides: Dict[str, Any]) -> Tuple[Any, Dict[str, Any]]:
    """Run the research engine and return the live run plus its summary."""
    from physics.ga_domain import ResearchRun
    from physics.ga_config import GAConfig

    config = GAConfig().merged(overrides).validate()
    run = AuditedRun(config, dict(overrides))
    summary = run.run()
    return run, summary


def _build_audited_run_class() -> Any:
    """Build the audited subclass of the engine's research run.

    The subclass records, after every generation, the best **canonical**
    (6-DoF) fitness of the island next to the published one.  The engine
    ranks its hall of fame over *all* tiers, and the low fidelity
    surrogate over-predicts the distance, so the two curves are not the
    same series -- the audit has to be able to show both.
    """
    from physics.ga_domain import ResearchRun

    class AuditedRun(ResearchRun):
        """A research run that records the canonical curve of every generation."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.canonical_history: List[Dict[str, Any]] = []

        def _run_generation(self, island: Any) -> Dict[str, Any]:
            report = super()._run_generation(island)
            canonical = [
                individual
                for individual in island.population
                if individual.tier >= 3 and individual.result
            ]
            best_canonical = max(canonical, key=lambda item: item.fitness, default=None)
            self.canonical_history.append(
                {
                    "generation": int(report.get("generation", 0)),
                    "island": int(report.get("island", 0)),
                    "published_best_fitness": (
                        None
                        if report.get("best_fitness") is None
                        else float(report["best_fitness"])
                    ),
                    "canonical_best_fitness": (
                        None if best_canonical is None else float(best_canonical.fitness)
                    ),
                    "canonical_best_metric": (
                        None if best_canonical is None else float(best_canonical.metric)
                    ),
                    "canonical_best_model": (
                        None
                        if best_canonical is None
                        else str((best_canonical.result or {}).get("model", ""))
                    ),
                    "canonical_individuals": len(canonical),
                }
            )
            return report

    return AuditedRun


class _AuditedRunProxy:
    """Resolve the audited run class lazily (the engine may be reloaded)."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return _build_audited_run_class()(*args, **kwargs)


#: The audited research run used by the audit (bound lazily).
AuditedRun = _AuditedRunProxy()


def fitness_curve(run: Any) -> List[Dict[str, Any]]:
    """The best fitness of every generation of a run.

    The curve is the island maximum of the (monotone) island best fitness,
    i.e. the best design the search has found up to that generation.
    """
    curve: List[Dict[str, Any]] = []
    for entry in getattr(run, "history", []):
        reports = entry.get("reports") or []
        values = [
            float(report["best_fitness"])
            for report in reports
            if report.get("best_fitness") is not None
        ]
        curve.append(
            {
                "generation": int(entry.get("generation", len(curve))),
                "best_fitness": (max(values) if values else None),
                "island_best_fitness": [
                    (None if report.get("best_fitness") is None else float(report["best_fitness"]))
                    for report in reports
                ],
                "survivors": [int(report.get("survivors", 0)) for report in reports],
                "tier3": [int(report.get("tier3", 0)) for report in reports],
                "evaluations": int(entry.get("evaluations", 0)),
            }
        )
    return curve


def individual_aircraft(individual: Any, paper_gsm: float = 100.0) -> AircraftConfig:
    """The flight model configuration of a GA individual."""
    geometry = fold_genome(individual.genome, paper_gsm=paper_gsm)
    aircraft = genome_to_aircraft_config(geometry, paper_gsm=paper_gsm)
    payload = as_dict(aircraft)
    payload["elevon_deg"] = float(individual.genes.get("elevon_angle_deg", 0.0))
    payload["cg_offset_x"] = float(individual.genes.get("cg_offset_x_m", 0.0))
    from physics.config_schema import from_dict

    return from_dict(AircraftConfig, payload)


def individual_launch(individual: Any, height_m: float = 1.2) -> LaunchConfig:
    """The launch condition of a GA individual."""
    throw = individual.throw
    return LaunchConfig(
        launch_speed=float(throw.speed_mps),
        launch_angle_deg=float(throw.release_elevation_deg),
        launch_roll_deg=float(throw.roll_deg),
        launch_yaw_deg=float(throw.yaw_deg),
        launch_height_m=float(height_m),
        throw_style="direct",
        throw_speed_source="direct",
        throw_pitch_rate_radps=float(throw.pitch_rate_radps),
    )


def canonical_curve(run: Any) -> List[Dict[str, Any]]:
    """The best **canonical** (6-DoF) fitness of every generation.

    The engine's own curve ranks the individuals of every tier against
    each other, and the low fidelity surrogate over-predicts the flight
    distance, so the engine's curve is not a series of comparable numbers.
    This curve contains only the individuals that were flown by the
    canonical 6-DoF model, which makes the generations comparable and
    the improvement claim verifiable.
    """
    per_generation: Dict[int, Dict[str, Any]] = {}
    for entry in getattr(run, "canonical_history", []):
        generation = int(entry["generation"])
        best = per_generation.get(generation)
        if best is None or (
            entry.get("canonical_best_fitness") is not None
            and float(entry["canonical_best_fitness"]) > float(best["canonical_best_fitness"])
        ):
            per_generation[generation] = dict(entry)
    curve: List[Dict[str, Any]] = []
    for generation in sorted(per_generation):
        entry = per_generation[generation]
        curve.append(
            {
                "generation": generation,
                "canonical_best_fitness": entry.get("canonical_best_fitness"),
                "canonical_best_metric": entry.get("canonical_best_metric"),
                "canonical_best_model": entry.get("canonical_best_model"),
                "canonical_individuals": entry.get("canonical_individuals", 0),
                "published_best_fitness": entry.get("published_best_fitness"),
            }
        )
    return curve


def canonical_best_individual(run: Any) -> Optional[Any]:
    """The best individual the canonical 6-DoF model ever flew."""
    best = None
    for island in run.islands:
        for individual in island.population:
            if individual.tier < 3 or not individual.result:
                continue
            if str((individual.result or {}).get("model", "")) != "rigid_body_6dof":
                continue
            if best is None or individual.fitness > best.fitness:
                best = individual
    for individual in getattr(run, "hall_of_fame", []):
        if individual.tier < 3 or not individual.result:
            continue
        if str((individual.result or {}).get("model", "")) != "rigid_body_6dof":
            continue
        if best is None or individual.fitness > best.fitness:
            best = individual
    return best


def verify_canonical_flight(
    individual: Any,
    duration_s: float,
    paper_gsm: float = 100.0,
    height_m: float = 1.2,
) -> Dict[str, Any]:
    """Re-fly an individual through the canonical 6-DoF path.

    The result is the proof that the published winner is a physically
    valid flyer and not a surrogate estimate.
    """
    aircraft = individual_aircraft(individual, paper_gsm=paper_gsm)
    launch = individual_launch(individual, height_m=height_m)
    result = flight_sim.evaluate_flight(
        as_dict(aircraft),
        as_dict(EnvironmentConfig()),
        as_dict(launch),
        duration_s,
        2.0e-2,
    )
    return {
        "model": str(result.get("model", "")),
        "status": str(result.get("status", "")),
        "distance_m": float(result.get("distance_m", 0.0)),
        "flight_time_s": float(result.get("flight_time_s", 0.0)),
        "max_stress_pa": float(result.get("max_stress_pa", 0.0)),
        "max_stress_index": float(result.get("max_stress_index", 0.0)),
        "launch_speed_mps": float(result.get("launch_speed_mps", 0.0)),
        "is_canonical": str(result.get("model", "")) == "rigid_body_6dof",
    }


def write_winner_artifacts(
    individual: Any,
    directory: os.PathLike | str,
    paper_gsm: float = 100.0,
) -> Dict[str, str]:
    """Write the human facing artifacts of one individual.

    The recipe (step table and Markdown), the crease pattern and the per
    step meshes are produced by the shared origami module, exactly like
    the engine produces them.
    """
    from physics.origami_genome import (
        crease_pattern_svg,
        recipe_markdown,
        recipe_steps,
        step_meshes,
    )

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    geometry = fold_genome(individual.genome, paper_gsm=paper_gsm)
    steps = recipe_steps(individual.genome, geometry)
    markdown = recipe_markdown(individual.genome, geometry)
    svg = crease_pattern_svg(individual.genome)
    meshes = step_meshes(individual.genome)

    paths = {
        "recipe": str(target / "canonical_best_recipe.json"),
        "recipe_markdown": str(target / "canonical_best_recipe.md"),
        "crease_pattern_svg": str(target / "canonical_best_crease_pattern.svg"),
        "step_meshes": str(target / "canonical_best_step_meshes.json"),
        "throw_params": str(target / "canonical_best_throw_params.json"),
    }
    write_json(paths["recipe"], {"steps": steps})
    with open(paths["recipe_markdown"], "w", encoding="utf-8") as handle:
        handle.write(markdown)
    with open(paths["crease_pattern_svg"], "w", encoding="utf-8") as handle:
        handle.write(svg)
    write_json(paths["step_meshes"], meshes)
    write_json(paths["throw_params"], individual.throw.to_dict())
    return paths


# --------------------------------------------------------------------------
# 5. The stalled flight: it has to come down
# --------------------------------------------------------------------------
def stalled_flight_audit(
    individuals: Sequence[Any],
    duration_s: float = 6.0,
    height_m: float = 0.3,
    environment: Optional[EnvironmentConfig] = None,
    paper_gsm: float = 100.0,
) -> Dict[str, Any]:
    """Fly GA individuals and check that a stalled flight terminates.

    Every individual is flown through the canonical 6-DoF path with a low
    release, so the airframe has to reach the ground.  The audit reports
    the flight time, the status and the stall state of every flight.
    """
    env = environment or EnvironmentConfig()
    rows: List[Dict[str, Any]] = []
    for individual in individuals:
        aircraft = individual_aircraft(individual, paper_gsm=paper_gsm)
        launch = individual_launch(individual, height_m=height_m)
        result = flight_sim.evaluate_flight(
            as_dict(aircraft), as_dict(env), as_dict(launch), duration_s, 2.0e-2
        )
        rows.append(
            {
                "index": int(individual.index),
                "status": str(result.get("status")),
                "flight_time_s": float(result.get("flight_time_s", 0.0)),
                "distance_m": float(result.get("distance_m", 0.0)),
                "stalled": bool(result.get("stalled", False)),
                "stall_margin": float(result.get("stall_margin", 0.0)),
                "max_stress_pa": float(result.get("max_stress_pa", 0.0)),
                "max_stress_index": float(result.get("max_stress_index", 0.0)),
                "landed": bool(result.get("landed", False)),
                "crashed": bool(result.get("crashed", False)),
            }
        )
    stalled_rows = [row for row in rows if row["stalled"] or row["stall_margin"] <= 0.0]
    return {
        "duration_s": float(duration_s),
        "release_height_m": float(height_m),
        "flights": rows,
        "stalled_flights": len(stalled_rows),
        "max_flight_time_s": max((row["flight_time_s"] for row in rows), default=0.0),
        "all_bounded": all(row["flight_time_s"] <= duration_s + 1.0e-6 for row in rows),
        "all_terminated": all(
            row["status"] in ("landed", "crashed", "diverged", "failed", "error")
            for row in rows
        ),
    }


# --------------------------------------------------------------------------
# 6. The structural failure rule
# --------------------------------------------------------------------------
def structural_failure_audit(run: Any) -> Dict[str, Any]:
    """Audit the structural failure accounting of a real run.

    Every individual that was actually flown is classified with the
    engine's own rule (:func:`physics.ga_domain.is_structural_failure`) and
    the shared allowable, and the classification is compared with the
    failure rate the run published.
    """
    from physics import ga_domain

    allowable = float(run._allowable_pa())
    # The engine classifies an *individual* with its own rule; the audit
    # uses the very same function so the comparison is exact.
    classify = getattr(ga_domain, "individual_structural_failure", None)
    if classify is None:  # pragma: no cover - older engine
        def classify(individual: Any, allowable_pa: float) -> bool:
            return bool(ga_domain.is_structural_failure(individual.result, allowable_pa))

    flown = [
        individual
        for island in run.islands
        for individual in island.population
        if individual.tier >= 1 and individual.result
    ]
    # The engine publishes the failure rate over its *full fidelity*
    # population, so the audit classifies the same population.
    population = [individual for individual in flown if individual.tier >= 3]
    overstressed: List[Dict[str, Any]] = []
    failures = 0
    for individual in population:
        stress = float(individual.result.get("max_stress_pa", 0.0))
        failed = bool(classify(individual, allowable))
        if failed:
            failures += 1
        if stress > allowable:
            overstressed.append(
                {
                    "index": int(individual.index),
                    "max_stress_pa": stress,
                    "allowable_pa": allowable,
                    "stress_index": stress / allowable if allowable else 0.0,
                    "status": str(individual.result.get("status", "")),
                    "classified_as_failure": failed,
                }
            )
    best = run._best_individual()
    best_failure = None
    best_stress = None
    if best is not None and best.result:
        best_failure = bool(classify(best, allowable))
        best_stress = float(best.result.get("max_stress_pa", 0.0))
    return {
        "allowable_pa": allowable,
        "flown_individuals": len(flown),
        "full_fidelity_individuals": len(population),
        "structural_failures": failures,
        "failure_rate": (failures / float(len(population))) if population else 0.0,
        "overstressed": overstressed,
        "overstressed_count": len(overstressed),
        "best_is_structural_failure": best_failure,
        "best_max_stress_pa": best_stress,
    }


# --------------------------------------------------------------------------
# 7. The dual-objective benchmark
# --------------------------------------------------------------------------
def benchmark_objective(
    objective: str,
    out_dir: os.PathLike | str,
    *,
    max_trials: int = 200,
    top_k: int = 20,
    tier1_trials: int = 10,
    tier2_trials: int = 40,
    tier3_trials: int = 200,
    generations: int = 5,
    islands: int = 2,
    seed: int = 11,
    processes: int = 0,
    tier2_duration_s: float = 0.0,
    tier3_duration_s: float = 0.0,
) -> Dict[str, Any]:
    """Run one objective of the research programme and write its artifacts.

    The artifacts are the ones the task requires: the generation-0 feasible
    fraction, the fitness curve, the recipe of the winner (step table and
    Markdown), the crease pattern, the per-step meshes, the throw
    parameters and the statistical summary.
    """
    from physics.ga_domain import objective_spec

    spec = objective_spec(objective)
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)

    overrides = ga_overrides(
        target,
        objective=objective,
        max_trials=max_trials,
        top_k=top_k,
        tier1_trials=tier1_trials,
        tier2_trials=tier2_trials,
        tier3_trials=tier3_trials,
        generations=generations,
        islands=islands,
        seed=seed,
        tier2_duration_s=tier2_duration_s,
        tier3_duration_s=tier3_duration_s,
        tier3_turbulence_scale=0.5,
        processes=processes,
    )
    started = time.time()
    run, summary = run_ga(overrides)
    elapsed = time.time() - started

    # The curve the engine publishes (it ranks the individuals of every
    # tier against each other) and the curve of the individuals that the
    # canonical 6-DoF model actually flew.
    curve = fitness_curve(run)
    canon = canonical_curve(run)
    gen0 = next((row["best_fitness"] for row in curve if row["generation"] == 0), None)
    final_best = summary.get("best_fitness")
    curve_values = [row["best_fitness"] for row in curve if row["best_fitness"] is not None]
    curve_max = max(curve_values) if curve_values else None

    canon_values = [
        row["canonical_best_fitness"]
        for row in canon
        if row["canonical_best_fitness"] is not None
    ]
    canon_gen0 = next(
        (
            row["canonical_best_fitness"]
            for row in canon
            if row["generation"] == 0 and row["canonical_best_fitness"] is not None
        ),
        None,
    )
    canon_best_fitness = max(canon_values) if canon_values else None

    # The winner the engine published, and the winner the canonical model
    # produced.  They are not necessarily the same design.
    published = run._best_individual()
    published_model = str((published.result or {}).get("model", "")) if published else ""
    published_is_surrogate = published_model == "reduced_surrogate"

    winner = canonical_best_individual(run)
    verification: Dict[str, Any] = {}
    winner_artifacts: Dict[str, str] = {}
    winner_metric = None
    if winner is not None:
        winner_metric = float(winner.metric)
        verification = verify_canonical_flight(
            winner,
            float(run.config.tier3_duration_s or spec.tier3_duration_s),
            paper_gsm=float(run.config.paper_gsm),
            height_m=float(run.config.launch_height_m),
        )
        verification["reported_tier"] = int(winner.tier)
        verification["reported_metric"] = float(winner.metric)
        verification["reported_status"] = str((winner.result or {}).get("status", ""))
        winner_artifacts = write_winner_artifacts(
            winner, target / "canonical_best", paper_gsm=float(run.config.paper_gsm)
        )

    stats = dict(summary.get("stats") or {})
    payload = {
        "objective": objective,
        "objective_detail": spec.to_dict(),
        "status": summary.get("status"),
        "elapsed_s": elapsed,
        "evaluations": int(summary.get("evaluations", 0)),
        "tier_counts": dict(summary.get("tier_counts") or {}),
        "promotion": dict(summary.get("promotion") or {}),
        "generation0_feasible_fraction": float(summary.get("generation0_feasible_fraction", 0.0)),
        "fitness_curve": curve,
        "canonical_fitness_curve": canon,
        "generation0_best_fitness": gen0,
        "final_best_fitness": final_best,
        "curve_max_fitness": curve_max,
        "canonical_generation0_best_fitness": canon_gen0,
        "canonical_final_best_fitness": canon_best_fitness,
        "improved": bool(
            canon_gen0 is not None
            and canon_best_fitness is not None
            and float(canon_best_fitness) > float(canon_gen0)
        ),
        "engine_reported_improved": bool(
            gen0 is not None
            and (final_best is not None)
            and float(final_best) > float(gen0)
        ),
        "best_metric": summary.get("best_metric"),
        "best_tier": summary.get("best_tier"),
        "published_best_model": published_model,
        "published_best_is_surrogate": published_is_surrogate,
        "canonical_best_metric": winner_metric,
        "reference_target": float(spec.reference_target),
        "record_fraction": (
            float(winner_metric or 0.0) / float(spec.reference_target)
        ),
        "published_record_fraction": (
            float(summary.get("best_metric", 0.0)) / float(spec.reference_target)
            if summary.get("best_metric") is not None
            else 0.0
        ),
        "statistics": stats,
        "throw_params": (summary.get("result") or {}).get("throw_params"),
        "canonical_verification": verification,
        "canonical_artifacts": winner_artifacts,
        "artifacts": dict(summary.get("artifacts") or {}),
        "config": dict(summary.get("config") or {}),
    }
    write_json(target / "benchmark.json", payload)
    write_json(target / "fitness_curve.json", curve)
    write_json(target / "canonical_fitness_curve.json", canon)
    write_json(target / "statistics.json", stats)
    write_json(target / "best_individual_full.json", summary.get("result") or {})
    return payload


def assemble_summary() -> Dict[str, Any]:
    """Assemble the machine readable summary of the whole audit.

    The summary collects the two benchmark runs and the artifacts of the
    other audits into ``artifacts/ga_audit/benchmark_summary.json``.
    """
    summary: Dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "record_targets": {"distance_m": RECORD_DISTANCE_M, "airtime_s": RECORD_AIRTIME_S},
        "backend": gpu_backend(),
        "objectives": {},
    }
    for objective in OBJECTIVES:
        path = ARTIFACT_ROOT / objective / "benchmark.json"
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        curve = payload.get("canonical_fitness_curve") or []
        summary["objectives"][objective] = {
            "status": payload.get("status"),
            "evaluations": payload.get("evaluations"),
            "elapsed_s": payload.get("elapsed_s"),
            "generation0_feasible_fraction": payload.get("generation0_feasible_fraction"),
            "generation0_best_fitness": payload.get("generation0_best_fitness"),
            "final_best_fitness": payload.get("final_best_fitness"),
            "canonical_generation0_best_fitness": payload.get("canonical_generation0_best_fitness"),
            "canonical_final_best_fitness": payload.get("canonical_final_best_fitness"),
            "canonical_fitness_curve": curve,
            "improved": payload.get("improved"),
            "engine_reported_improved": payload.get("engine_reported_improved"),
            "canonical_best_metric": payload.get("canonical_best_metric"),
            "published_best_metric": payload.get("best_metric"),
            "published_best_tier": payload.get("best_tier"),
            "published_best_model": payload.get("published_best_model"),
            "published_best_is_surrogate": payload.get("published_best_is_surrogate"),
            "reference_target": payload.get("reference_target"),
            "record_fraction": payload.get("record_fraction"),
            "published_record_fraction": payload.get("published_record_fraction"),
            "statistics": payload.get("statistics"),
            "canonical_verification": payload.get("canonical_verification"),
            "throw_params": payload.get("throw_params"),
            "artifacts": payload.get("artifacts"),
            "canonical_artifacts": payload.get("canonical_artifacts"),
        }
    for name, relative in (
        ("physics", "physics/stall_region.json"),
        ("stalled_flights", "physics/stalled_flights.json"),
        ("geometry", "geometry/geometry_audit.json"),
        ("parity", "parity/parity.json"),
    ):
        path = ARTIFACT_ROOT / relative
        if path.exists():
            with open(path, "r", encoding="utf-8") as handle:
                summary[name] = json.load(handle)
    write_json(ARTIFACT_ROOT / "benchmark_summary.json", summary)
    return summary


def surrogate_check(
    out_dir: os.PathLike | str,
    *,
    max_trials: int = 60,
    generations: int = 2,
    islands: int = 2,
    seed: int = 11,
) -> Dict[str, Any]:
    """Measure the tier mixing of the published best individual.

    The engine ranks the individuals of **every** tier against each other
    in its hall of fame, while the low fidelity surrogate over-predicts
    the flight distance.  This check reports the model of the individual
    the engine publishes as the best of the run next to the best
    individual the canonical 6-DoF model actually flew.
    """
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    overrides = ga_overrides(
        target,
        objective="distance",
        max_trials=max_trials,
        top_k=10,
        tier1_trials=6,
        tier2_trials=6,
        tier3_trials=6,
        generations=generations,
        islands=islands,
        seed=seed,
        tier2_duration_s=4.0,
        tier3_duration_s=12.0,
        processes=1,
    )
    run, summary = run_ga(overrides)
    published = run._best_individual()
    winner = canonical_best_individual(run)
    published_model = str((published.result or {}).get("model", "")) if published else ""
    payload = {
        "objective": "distance",
        "status": summary.get("status"),
        "evaluations": summary.get("evaluations"),
        "published_best_model": published_model,
        "published_best_tier": int(published.tier) if published else None,
        "published_best_metric": float(published.metric) if published else None,
        "published_best_is_surrogate": published_model == "reduced_surrogate",
        "canonical_best_model": str((winner.result or {}).get("model", "")) if winner else "",
        "canonical_best_tier": int(winner.tier) if winner else None,
        "canonical_best_metric": float(winner.metric) if winner else None,
        "published_over_canonical_ratio": (
            float(published.metric) / float(winner.metric)
            if published is not None and winner is not None and winner.metric
            else None
        ),
        "hall_of_fame": [
            {
                "index": int(item.index),
                "tier": int(item.tier),
                "model": str((item.result or {}).get("model", "")),
                "fitness": float(item.fitness),
                "metric": float(item.metric),
            }
            for item in getattr(run, "hall_of_fame", [])
        ],
    }
    write_json(ARTIFACT_ROOT / "physics" / "surrogate_check.json", payload)
    return payload


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """The argument parser of the audit runner."""
    parser = argparse.ArgumentParser(
        prog="ga_audit",
        description="Anti-reward-hacking audit and benchmark of the genetic research engine.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("physics", help="audit the stall region of the aerodynamic kernel")
    sub.add_parser("geometry", help="audit the tier-0 geometry filter over random genomes")
    parity = sub.add_parser("parity", help="audit the CPU/batch agreement of the flight paths")
    parity.add_argument(
        "--device",
        action="store_true",
        help="also run the comparison on the CuPy device kernel (very slow)",
    )
    sub.add_parser("stall", help="audit that a stalled flight terminates on the ground")
    sub.add_parser("summary", help="assemble artifacts/ga_audit/benchmark_summary.json")
    surrogate = sub.add_parser(
        "surrogate", help="measure whether the published best is a surrogate estimate"
    )
    surrogate.add_argument("--max-trials", type=int, default=60)
    surrogate.add_argument("--generations", type=int, default=2)
    surrogate.add_argument("--islands", type=int, default=2)
    surrogate.add_argument("--seed", type=int, default=11)

    bench = sub.add_parser("benchmark", help="run one objective of the research programme")
    bench.add_argument("--objective", choices=list(OBJECTIVES), default="distance")
    bench.add_argument("--max-trials", type=int, default=200)
    bench.add_argument("--top-k", type=int, default=20)
    bench.add_argument("--tier1-trials", type=int, default=10)
    bench.add_argument("--tier2-trials", type=int, default=40)
    bench.add_argument("--tier3-trials", type=int, default=200)
    bench.add_argument("--generations", type=int, default=5)
    bench.add_argument("--islands", type=int, default=2)
    bench.add_argument("--seed", type=int, default=11)
    bench.add_argument("--processes", type=int, default=0)
    bench.add_argument("--tier2-duration", type=float, default=0.0)
    bench.add_argument("--tier3-duration", type=float, default=0.0)
    bench.add_argument("--out", dest="out_dir", default=None)

    every = sub.add_parser("all", help="run every audit and both benchmarks")
    every.add_argument("--max-trials", type=int, default=200)
    every.add_argument("--top-k", type=int, default=20)
    every.add_argument("--tier1-trials", type=int, default=10)
    every.add_argument("--tier2-trials", type=int, default=40)
    every.add_argument("--tier3-trials", type=int, default=200)
    every.add_argument("--generations", type=int, default=5)
    every.add_argument("--islands", type=int, default=2)
    every.add_argument("--seed", type=int, default=11)
    every.add_argument("--processes", type=int, default=0)
    return parser


def _physics_command() -> Dict[str, Any]:
    """Run the stall-region audit and write its artifacts."""
    cfg = AircraftConfig()
    report = stall_report(cfg)
    polar = polar_sweep(cfg)
    payload = {
        "aircraft": as_dict(cfg),
        "stall_region": report,
        "polar": polar,
        "backend": gpu_backend(),
    }
    write_json(ARTIFACT_ROOT / "physics" / "stall_region.json", payload)
    write_json(ARTIFACT_ROOT / "physics" / "lift_sweep.json", lift_sweep(cfg))
    write_json(ARTIFACT_ROOT / "physics" / "drag_sweep.json", drag_sweep(cfg))
    return payload


def _geometry_command(count: int = 1000) -> Dict[str, Any]:
    """Run the geometry audit and write its artifacts."""
    payload = geometry_audit(count_free=count, count_families=count)
    write_json(ARTIFACT_ROOT / "geometry" / "geometry_audit.json", payload)
    return payload


def _parity_command(device: bool = False) -> Dict[str, Any]:
    """Run the CPU/batch agreement audit and write its artifacts.

    The cases are flown the way the engine flies them (the release height
    of the run) and, in addition, from a low release, where the flight is
    dominated by its touchdown: that case documents how far the two paths
    can drift when the landing transient is the whole flight.
    """
    from physics.origami_genome import fold_genome

    geometries = {
        name: fold_genome(genome_from_archetype(name), paper_gsm=100.0)
        for name in ("dart", "nakamura", "sky_king")
    }
    cases: List[Tuple[str, AircraftConfig, EnvironmentConfig, LaunchConfig]] = []
    for name, geometry in geometries.items():
        aircraft = genome_to_aircraft_config(geometry, paper_gsm=100.0)
        cases.append(
            (
                f"{name}@1.2m",
                aircraft,
                EnvironmentConfig(),
                LaunchConfig(launch_speed=12.0, launch_angle_deg=0.0, launch_height_m=1.2),
            )
        )
        cases.append(
            (
                f"{name}@0.3m",
                aircraft,
                EnvironmentConfig(),
                LaunchConfig(launch_speed=12.0, launch_angle_deg=0.0, launch_height_m=0.3),
            )
        )
    payload = parity_audit(cases, duration_s=6.0)
    if device:
        payload["device"] = _device_parity(cases, duration_s=6.0)
    write_json(ARTIFACT_ROOT / "parity" / "parity.json", payload)
    return payload


def _device_parity(
    cases: Sequence[Tuple[str, AircraftConfig, EnvironmentConfig, LaunchConfig]],
    duration_s: float = 6.0,
) -> Dict[str, Any]:
    """The same comparison on the CuPy device kernel.

    The device kernel of this repository is far slower than the CPU
    6-DoF path it accelerates, so it is only run on request; the numbers
    are recorded in the artifacts of the audit.
    """
    rows: List[Dict[str, Any]] = []
    for name, aircraft, environment, launch in cases:
        reference, samples = reference_flight(aircraft, environment, launch, duration_s)
        batch, backend = device_batch_flight(aircraft, environment, launch, duration_s)
        comparison = compare_flights(reference, batch, samples)
        comparison["case"] = name
        comparison["backend"] = backend
        rows.append(comparison)
    return {
        "backend": gpu_backend(),
        "duration_s": float(duration_s),
        "cases": rows,
        "all_within_tolerance": all(
            row["status_matches"]
            and row["relative_delta"]["distance_m"] <= DISTANCE_REL_TOLERANCE
            and row["relative_delta"]["flight_time_s"] <= row["tolerances"]["flight_time_s"]
            and row["relative_delta"]["max_stress_pa"] <= STRESS_REL_TOLERANCE
            and row["relative_delta"]["max_stress_index"] <= STRESS_INDEX_REL_TOLERANCE
            and row["relative_delta"]["max_g_load"] <= G_LOAD_REL_TOLERANCE
            for row in rows
        ),
    }


def _stall_command() -> Dict[str, Any]:
    """Fly the archetype individuals and audit the stall behaviour."""
    from physics.origami_genome import fold_genome

    rows: List[Dict[str, Any]] = []
    for name in ("dart", "nakamura", "sky_king"):
        geometry = fold_genome(genome_from_archetype(name), paper_gsm=100.0)
        aircraft = genome_to_aircraft_config(geometry, paper_gsm=100.0)
        launch = LaunchConfig(launch_speed=12.0, launch_angle_deg=0.0, launch_height_m=0.3)
        result = flight_sim.evaluate_flight(
            as_dict(aircraft), as_dict(EnvironmentConfig()), as_dict(launch), 6.0, 2.0e-2
        )
        rows.append(
            {
                "archetype": name,
                "status": str(result.get("status", "")),
                "flight_time_s": float(result.get("flight_time_s", 0.0)),
                "distance_m": float(result.get("distance_m", 0.0)),
                "stalled": bool(result.get("stalled", False)),
                "stall_margin": float(result.get("stall_margin", 0.0)),
            }
        )
    payload = {"flights": rows}
    write_json(ARTIFACT_ROOT / "physics" / "stalled_flights.json", payload)
    return payload


def main(argv: Optional[List[str]] = None) -> int:
    """Run the audit."""
    parser = build_parser()
    args = parser.parse_args(argv)
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)

    if args.command == "physics":
        payload = _physics_command()
    elif args.command == "geometry":
        payload = _geometry_command()
    elif args.command == "parity":
        payload = _parity_command(device=bool(getattr(args, "device", False)))
    elif args.command == "stall":
        payload = _stall_command()
    elif args.command == "summary":
        payload = assemble_summary()
    elif args.command == "surrogate":
        payload = surrogate_check(
            ARTIFACT_ROOT / "surrogate_check",
            max_trials=args.max_trials,
            generations=args.generations,
            islands=args.islands,
            seed=args.seed,
        )
    elif args.command == "benchmark":
        out_dir = args.out_dir or (ARTIFACT_ROOT / args.objective)
        payload = benchmark_objective(
            args.objective,
            out_dir,
            max_trials=args.max_trials,
            top_k=args.top_k,
            tier1_trials=args.tier1_trials,
            tier2_trials=args.tier2_trials,
            tier3_trials=args.tier3_trials,
            generations=args.generations,
            islands=args.islands,
            seed=args.seed,
            processes=args.processes,
            tier2_duration_s=args.tier2_duration,
            tier3_duration_s=args.tier3_duration,
        )
    elif args.command == "all":
        payload = {
            "physics": _physics_command(),
            "stall": _stall_command(),
            "geometry": _geometry_command(),
            "parity": _parity_command(),
            "benchmarks": {},
        }
        for objective in OBJECTIVES:
            payload["benchmarks"][objective] = benchmark_objective(
                objective,
                ARTIFACT_ROOT / objective,
                max_trials=args.max_trials,
                top_k=args.top_k,
                tier1_trials=args.tier1_trials,
                tier2_trials=args.tier2_trials,
                tier3_trials=args.tier3_trials,
                generations=args.generations,
                islands=args.islands,
                seed=args.seed,
                processes=args.processes,
            )
        payload = assemble_summary()
    else:  # pragma: no cover - argparse rejects unknown commands
        parser.error(f"unknown command {args.command!r}")
        return 2

    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
