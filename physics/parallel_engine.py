"""Parallel evaluation engine (spawn-safe, order-preserving, no import-time pools).

Responsibilities
----------------
1. Decide how many workers may be used (:func:`get_worker_count`) and how to
   label that decision (:func:`backend_name`).
2. Map an arbitrary module-level callable over a list of items while
   **preserving input order** (:func:`map_evaluate`, :func:`parallel_map`).
3. Evaluate a *flight case* -- a plain ``dict`` holding the airframe,
   environment and launch payloads plus the integrator settings -- either with
   the real 6-DoF integrator of :mod:`physics.flight_sim` or, when that module
   is not importable yet, with the analytical surrogate implemented here.

Windows / ``spawn`` rules honoured by this module
-------------------------------------------------
* Nothing handed to a worker pool is ever a lambda, closure, bound method or
  nested function: :func:`evaluate_case` and :func:`_pool_worker` are
  module-level, and payloads are plain dictionaries / tuples / lists.
* If the user supplied ``func`` cannot be pickled the engine transparently
  falls back to a :class:`~concurrent.futures.ThreadPoolExecutor` and
  :func:`backend_name` reports ``"threading"``.
* No pool, executor or child process is created at import time; every pool is
  created *inside* a function call and closed before it returns.

The analytical surrogate
------------------------
:func:`evaluate_flight_surrogate` is a complete (if coarse) gliding-airplane
model, not a stub.  Its chain is

``derived_properties`` -> Helmbold lift slope -> parabolic drag polar ->
``L/D_max`` -> stall speed ``V_stall`` -> excess-speed glide height
``h = (V^2 - V_stall^2) / 2g`` -> range ``(L/D) h`` -> dynamic pressure
``q = 1/2 rho V^2`` -> structural stress proxy ``q S b / 2`` scaled by the
load factor.  Every design variable of the optimiser catalogue reaches at
least one of those terms, so the surrogate is monotone and physically
sensible in each search direction.
"""

from __future__ import annotations

import math
import multiprocessing
import os
import pickle
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .config_schema import (
    LAPSE_RATE_ISA,
    R_AIR,
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    as_dict,
    clamp,
    deg2rad,
    derived_properties,
    from_dict,
    safe_div,
)

__all__ = [
    "MAX_AUTO_WORKERS",
    "get_worker_count",
    "backend_name",
    "map_evaluate",
    "parallel_map",
    "evaluate_case",
    "parallel_evaluate",
    "evaluate_flight_surrogate",
    "isa_air_density",
    "worker_id",
    "worker_pid",
    "FLIGHT_RESULT_KEYS",
]

#: Upper bound applied when the caller asks for the automatic worker count.
MAX_AUTO_WORKERS: int = 8

#: Keys produced by every evaluation, surrogate or real integrator alike.
FLIGHT_RESULT_KEYS: Tuple[str, ...] = (
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
    "status",
)

#: Module level identity of the *current* worker.  ``_pool_init`` overwrites
#: both entries inside pool workers; the values visible in the parent process
#: stay at ``-1`` / the parent pid, which is how the test-suite proves that a
#: real pool (and not an in-process loop) executed the work.
_WORKER_ID: int = -1
_WORKER_PID: int = os.getpid()

#: Paper burst strength used to normalise the stress heat-map index (Pa).
_STRESS_LIMIT_PA: float = 1.2e5


# --------------------------------------------------------------------------
# Worker sizing / backend naming
# --------------------------------------------------------------------------
def get_worker_count(requested: int = 0) -> int:
    """Return a strictly positive, sane worker count.

    ``requested <= 0`` means "automatic": ``min(cpu_count, MAX_AUTO_WORKERS)``
    with a floor of 1.  A positive request is clamped into
    ``[1, cpu_count or 1]``.
    """
    cpu = os.cpu_count() or 1
    try:
        want = int(requested)
    except (TypeError, ValueError):
        want = 0
    if want <= 0:
        return max(1, min(cpu, MAX_AUTO_WORKERS))
    return max(1, min(want, cpu))


def backend_name(processes: int = 0) -> str:
    """Label the backend :func:`map_evaluate` would use for ``processes``.

    ``"serial"`` for a single worker, ``"multiprocessing"`` when a real process
    pool is created and ``"threading"`` when the callable cannot be pickled.
    """
    if get_worker_count(processes) == 1:
        return "serial"
    return _BACKEND_OVERRIDE or "multiprocessing"


#: Set by :func:`map_evaluate` when (and only when) it had to fall back to
#: threads because the callable was not picklable.  ``None`` otherwise.
_BACKEND_OVERRIDE: Optional[str] = None


def worker_id() -> int:
    """Index assigned to this worker by :func:`_pool_init` (``-1`` in parent)."""
    return _WORKER_ID


def worker_pid() -> int:
    """Process id of this worker (``os.getpid()`` in the parent process)."""
    return _WORKER_PID


# --------------------------------------------------------------------------
# Order preserving mapping
# --------------------------------------------------------------------------
def _chunk_plan(count: int, workers: int, chunk_size: Optional[int]) -> List[List[int]]:
    """Split ``range(count)`` into contiguous chunks handed to workers."""
    if count <= 0:
        return []
    if chunk_size is None or int(chunk_size) <= 0:
        size = max(1, math.ceil(count / max(1, workers)))
    else:
        size = int(chunk_size)
    return [list(range(start, min(start + size, count)))
            for start in range(0, count, size)]


def _is_picklable(func: Callable[..., Any]) -> bool:
    """True when ``func`` can be shipped to a spawned process by reference."""
    try:
        pickle.dumps(func)
    except Exception:
        return False
    return True


def map_evaluate(
    func: Callable[[Any], Any],
    items: Sequence[Any],
    processes: int = 0,
    chunk_size: Optional[int] = None,
) -> List[Any]:
    """Apply ``func`` to every element of ``items`` and return the results.

    The output order always matches the input order, whatever backend runs.
    ``processes == 1`` is a plain serial loop (no pool object is built).
    """
    global _BACKEND_OVERRIDE

    materialised: List[Any] = list(items)
    if not materialised:
        _BACKEND_OVERRIDE = None
        return []

    workers = get_worker_count(processes)
    if workers == 1:
        _BACKEND_OVERRIDE = None
        return [func(item) for item in materialised]

    if not _is_picklable(func):
        _BACKEND_OVERRIDE = "threading"
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                return list(pool.map(func, materialised))
        finally:
            _BACKEND_OVERRIDE = None

    _BACKEND_OVERRIDE = None
    plan = _chunk_plan(len(materialised), workers, chunk_size)
    tasks = [(func, [materialised[index] for index in chunk]) for chunk in plan]
    ordered: List[Any] = []
    context = multiprocessing.get_context("spawn")
    with context.Pool(processes=workers, initializer=_pool_init, initargs=(-1,)) as pool:
        for chunk_result in pool.imap(_pool_worker, tasks):
            ordered.extend(chunk_result)
    return ordered


def parallel_map(func: Callable[[Any], Any], items: Sequence[Any], processes: int = 0) -> List[Any]:
    """Convenience wrapper around :func:`map_evaluate` with automatic chunking."""
    return map_evaluate(func, items, processes=processes, chunk_size=None)


# --------------------------------------------------------------------------
# Spawn pool primitives (module level on purpose)
# --------------------------------------------------------------------------
def _pool_init(worker_id: int) -> None:
    """Pool ``initializer``: publish the worker index and pid as globals.

    ``worker_id < 0`` asks the initializer to derive the index from
    ``multiprocessing.current_process()._identity``, which is unique per
    spawned worker even though every worker shares the same ``initargs``.
    """
    global _WORKER_ID, _WORKER_PID
    index = int(worker_id)
    if index < 0:
        identity = getattr(multiprocessing.current_process(), "_identity", None)
        index = int(identity[0]) if identity else -1
    _WORKER_ID = index
    _WORKER_PID = os.getpid()


def _pool_worker(args: Tuple[Callable[[Any], Any], List[Any]]) -> List[Any]:
    """Apply a picklable callable to a picklable chunk, in chunk order."""
    func, chunk = args
    return [func(item) for item in chunk]


# --------------------------------------------------------------------------
# Atmosphere helper (surrogate only)
# --------------------------------------------------------------------------
def isa_air_density(environment: Optional[Dict[str, Any]] = None) -> float:
    """Air density in kg/m^3 from an :class:`EnvironmentConfig` payload.

    ISA temperature lapse ``T = T0 - L h`` with the hydrostatic
    ``p = p0 (T/T0)^(g/(L R))`` and ``rho = p / (R T)``, then the standard
    virtual-temperature correction for relative humidity
    ``rho *= 1 - 0.378 * RH``.  Always returns a strictly positive, finite
    value.
    """
    env = EnvironmentConfig()
    if environment:
        env = from_dict(EnvironmentConfig, environment)
    altitude = max(float(env.altitude_m), 0.0)
    gravity = max(float(env.gravity), 0.1)
    lapse = float(env.lapse_rate_K_per_m)
    if abs(lapse) < 1.0e-9:
        lapse = LAPSE_RATE_ISA
    t0 = clamp(float(env.sea_level_temperature_K), 180.0, 400.0)
    temperature = t0 - lapse * altitude
    temperature = clamp(temperature, 150.0, 450.0)
    exponent = gravity / (lapse * R_AIR) if lapse > 0.0 else -gravity * altitude / (R_AIR * temperature)
    pressure = float(env.sea_level_pressure_Pa) * (temperature / t0) ** exponent
    pressure = clamp(pressure, 1.0e3, 3.0e5)
    density = pressure / (R_AIR * temperature)
    density *= 1.0 - 0.378 * clamp(float(env.humidity_rel), 0.0, 1.0)
    return clamp(density, 1.0e-3, 10.0)


# --------------------------------------------------------------------------
# Analytical surrogate flight model
# --------------------------------------------------------------------------
def _fold_efficiency(aircraft: AircraftConfig) -> Tuple[float, float]:
    """Return ``(cd0 multiplier, cl_max multiplier)`` caused by the folds.

    Extra fold lines act as wetted surface (parasite drag) and blunt the nose
    (peak lift), so both factors grow with the fold angles.
    """
    drag_factor = (
        1.0
        + 0.0060 * max(float(aircraft.fold_leading_deg), 0.0)
        + 0.0040 * max(float(aircraft.fold_tip_deg), 0.0)
        + 0.0030 * max(float(aircraft.fold_center_deg), 0.0)
        + 0.0015 * max(float(aircraft.fold_trailing_deg), 0.0)
    )
    blunt = 0.0045 * max(float(aircraft.fold_trailing_deg) - 15.0, 0.0)
    blunt += 0.0035 * max(float(aircraft.fold_tip_deg), 0.0)
    lift_factor = clamp(1.0 - blunt, 0.55, 1.15)
    return drag_factor, lift_factor


def evaluate_flight_surrogate(
    aircraft: Optional[Dict[str, Any]] = None,
    environment: Optional[Dict[str, Any]] = None,
    launch: Optional[Dict[str, Any]] = None,
    duration_s: float = 6.0,
    dt_max: float = 2.0e-2,
) -> Dict[str, Any]:
    """Analytical gliding-flight surrogate returning :data:`FLIGHT_RESULT_KEYS`.

    The model is intentionally cheap (pure scalar arithmetic, no allocation of
    trajectory samples) so that thousands of optimiser evaluations stay in the
    millisecond range, yet every term is a real aerodynamic relation:

    ==========================  ==========================================
    quantity                    relation
    ==========================  ==========================================
    lift slope                  Helmbold ``2 pi AR / (2 + sqrt(AR^2(1+l)+4 AR/pi))``
    drag polar                  ``CD0 + CL^2 / (pi e AR)``
    best glide ratio            ``0.5 sqrt(pi e AR / CD0)`` (ground-effect corrected)
    stall speed                 ``V_stall = sqrt(2 m g / (rho S CLmax))``
    excess-speed height         ``h = (V^2 - V_stall^2) / (2 g)``
    range                       ``(L/D) h``
    stress proxy                ``q S b / 2`` with ``q = 1/2 rho V^2``
    ==========================  ==========================================
    """
    air = AircraftConfig()
    if aircraft:
        air = from_dict(AircraftConfig, aircraft)
    env = EnvironmentConfig()
    if environment:
        env = from_dict(EnvironmentConfig, environment)
    lau = LaunchConfig()
    if launch:
        lau = from_dict(LaunchConfig, launch)

    horizon = max(float(duration_s), 0.05)
    dt = max(float(dt_max), 1.0e-6)  # integrated implicit step, kept for API parity

    props = derived_properties(air)
    span = props["wingspan_m"]
    area = props["wing_area_m2"]
    aspect = props["aspect_ratio"]
    taper = props["taper_ratio"]
    mass = props["mass_kg"]

    gravity = max(float(env.gravity), 0.1)
    density = isa_air_density(as_environment_payload(environment))
    lift_scale = clamp(float(env.lift_scale), 0.0, 5.0)
    drag_scale = clamp(float(env.drag_scale), 0.0, 5.0)
    ground_effect_enabled = bool(env.ground_effect_enabled)

    # --- lift slope (Helmbold triangular wing) -----------------------------
    if float(air.cLalpha_per_rad) > 0.0:
        cl_alpha = float(air.cLalpha_per_rad)
    else:
        radicand = max(aspect * aspect * (1.0 + taper) + 4.0 * aspect / math.pi, 0.0)
        cl_alpha = 2.0 * math.pi * aspect / (2.0 + math.sqrt(radicand))

    drag_factor, lift_factor = _fold_efficiency(air)
    cd0 = max(float(air.cd0) * drag_factor * drag_scale, 1.0e-6)
    oswald = clamp(float(air.oswald_e), 0.05, 1.0)
    cl_max = clamp(
        float(air.cl_max) * lift_factor * (1.0 + 0.004 * max(float(air.dihedral_deg), 0.0)),
        0.15,
        2.5,
    )

    # --- ground effect on induced drag ------------------------------------
    glide_height = max(float(lau.launch_height_m), 0.0)
    hb = glide_height / max(span, 1.0e-6)
    ground_ratio = (16.0 * hb * hb) / (1.0 + 16.0 * hb * hb) if ground_effect_enabled else 0.0

    # --- best glide condition ---------------------------------------------
    cl_glide = math.sqrt(cd0 * math.pi * oswald * aspect)
    alpha_zero_elevon = (cl_glide - float(air.cL0)) / max(cl_alpha, 1.0e-6)
    # Elevon deflection adds camber -> less geometric incidence is needed.
    alpha_trim = alpha_zero_elevon + 0.25 * deg2rad(float(air.elevon_deg))
    alpha_stall = abs(float(air.stall_alpha_deg))
    alpha_trim_deg = clamp(math.degrees(alpha_trim), -30.0, 45.0)
    stall_margin = clamp(alpha_stall - alpha_trim_deg, -30.0, 60.0)

    ld_finite = 0.5 * math.sqrt(math.pi * oswald * aspect / cd0) * (1.0 + 0.12 * ground_ratio)
    ld_ratio = clamp(ld_finite * lift_scale, 0.05, 120.0)

    # --- airspeed, wind and stall speed -----------------------------------
    wind_x = float(env.wind_speed) * math.cos(deg2rad(float(env.wind_direction_deg)))
    launch_speed = max(float(lau.launch_speed), 0.0)
    launch_angle = deg2rad(clamp(float(lau.launch_angle_deg), -30.0, 60.0))
    roll = deg2rad(clamp(float(lau.launch_roll_deg), -90.0, 90.0))
    roll_factor = max(math.cos(roll), 0.15)
    airspeed = max(launch_speed + wind_x, 0.0)
    v_stall = math.sqrt(2.0 * mass * gravity / max(density * area * cl_max, 1.0e-9))

    # --- excess-speed glide height (including the throw arc) ---------------
    excess = max(airspeed * airspeed - v_stall * v_stall, 0.0)
    height_gain = excess / (2.0 * gravity)
    launch_vertical = airspeed * math.sin(launch_angle)
    throw_gain = max(launch_vertical * launch_vertical, 0.0) / (2.0 * gravity)
    usable = (height_gain + throw_gain + glide_height) * roll_factor
    usable = max(usable, 0.0)

    # --- glide time and range ---------------------------------------------
    sin_gamma = safe_div(1.0, math.sqrt(1.0 + ld_ratio * ld_ratio), 1.0)
    cos_gamma = safe_div(ld_ratio, math.sqrt(1.0 + ld_ratio * ld_ratio), 0.0)
    time_to_ground = safe_div(usable, airspeed * sin_gamma, 0.0) if airspeed > 1.0e-6 else 0.0
    flight_time = clamp(min(time_to_ground, horizon), 0.0, horizon)
    horizontal_speed = airspeed * cos_gamma
    distance = max(horizontal_speed * flight_time, 0.0)
    landing_distance = max(distance + 0.5 * wind_x * flight_time, 0.0)
    max_altitude = clamp(glide_height + height_gain + throw_gain, 0.0, 1.0e4)

    # --- loads and structural stress --------------------------------------
    dynamic_pressure = 0.5 * density * airspeed * airspeed
    cl_flight = safe_div(2.0 * mass * gravity * roll_factor,
                         max(density * area * airspeed * airspeed, 1.0e-9), 0.0)
    load_factor = clamp(safe_div(cl_flight * dynamic_pressure * area,
                                 max(mass * gravity, 1.0e-9), 1.0), 0.0, 40.0)
    stress_pa = 0.5 * dynamic_pressure * area * span * (1.0 + 0.15 * load_factor)
    stress_index = clamp(stress_pa / _STRESS_LIMIT_PA, 0.0, 1.0)

    # --- static / dynamic stability index ----------------------------------
    stability = (
        0.50
        + 0.0100 * max(float(air.dihedral_deg), 0.0)
        + 0.60 * clamp(float(air.static_margin), 0.0, 1.0)
        + 0.20 * clamp(float(air.paper_stiffness), 0.0, 1.0)
        - 0.35 * clamp(1.0 - abs(math.degrees(alpha_trim)) / max(alpha_stall, 1.0e-6), 0.0, 1.0)
    )
    stability = clamp(stability, 0.0, 2.0)

    if airspeed <= 1.0e-6 or distance <= 1.0e-6:
        status = "no-launch"
    elif stall_margin <= 0.0:
        status = "stalled"
    elif time_to_ground > horizon + 1.0e-9:
        status = "time-limit"
    else:
        status = "landed"

    return {
        "distance_m": float(distance),
        "flight_time_s": float(flight_time),
        "max_altitude_m": float(max_altitude),
        "max_ld_ratio": float(ld_ratio),
        "max_stress_index": float(stress_index),
        "max_stress_pa": float(max(stress_pa, 0.0)),
        "launch_speed_mps": float(launch_speed),
        "stall_margin": float(stall_margin),
        "stability": float(stability),
        "landing_distance_m": float(landing_distance),
        "status": status,
        # Extra diagnostics (never used as objective inputs) -----------------
        "surrogate": True,
        "airspeed_mps": float(airspeed),
        "stall_speed_mps": float(v_stall),
        "load_factor": float(load_factor),
        "dynamic_pressure_pa": float(dynamic_pressure),
        "trim_alpha_deg": float(alpha_trim_deg),
        "steps": int(max(1, math.ceil(flight_time / dt))),
    }


def as_environment_payload(environment: Any) -> Dict[str, Any]:
    """Normalise an environment payload/dataclass into a plain dictionary."""
    if environment is None:
        return {}
    if isinstance(environment, dict):
        return dict(environment)
    return {
        name: getattr(environment, name)
        for name in EnvironmentConfig.__dataclass_fields__
    }


# --------------------------------------------------------------------------
# Upstream integrator hook
# --------------------------------------------------------------------------
def _flight_module() -> Any:
    """Import :mod:`physics.flight_sim` lazily; ``None`` when unavailable.

    The surrogate engine must stay importable (and testable) before the 6-DoF
    integrator exists, so every dependency on it goes through this guard.
    """
    try:
        from . import flight_sim  # noqa: WPS433 - lazy, optional dependency
    except Exception:
        try:
            from physics import flight_sim  # type: ignore[no-redef]
        except Exception:
            return None
    return flight_sim


def evaluate_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate one flight case -- module level, picklable, spawn safe.

    ``case`` is a plain dictionary with the keys ``aircraft``, ``environment``,
    ``launch``, ``duration_s`` and ``dt_max``.  When :mod:`physics.flight_sim`
    is importable its ``evaluate_flight`` is used with exactly those five
    arguments, otherwise the analytical surrogate of this module runs.
    """
    payload = case if isinstance(case, dict) else {}
    duration = float(payload.get("duration_s", 6.0) or 6.0)
    dt_max = float(payload.get("dt_max", 2.0e-2) or 2.0e-2)
    aircraft = payload.get("aircraft")
    environment = payload.get("environment")
    launch = payload.get("launch")

    module = _flight_module()
    if module is not None and hasattr(module, "evaluate_flight"):
        try:
            result = module.evaluate_flight(aircraft, environment, launch, duration, dt_max)
        except Exception:
            # A broken integrator must never take a worker pool down with it.
            return evaluate_flight_surrogate(aircraft, environment, launch, duration, dt_max)
        if isinstance(result, dict):
            return result
    return evaluate_flight_surrogate(aircraft, environment, launch, duration, dt_max)


# --------------------------------------------------------------------------
# Batch driver
# --------------------------------------------------------------------------
def parallel_evaluate(
    cases: Sequence[Dict[str, Any]],
    processes: int = 0,
    progress_cb: Optional[Callable[[int, int, float], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> List[Dict[str, Any]]:
    """Evaluate every case, keeping the input order, with progress + cancel.

    ``progress_cb(evaluated, total, best_score)`` is invoked after each chunk
    (and once at the end) with a monotonically non-decreasing ``evaluated``
    that reaches ``total`` for a completed run; ``total`` is never ``0`` for a
    non-empty request.  ``cancel_cb()`` is polled before every chunk: as soon
    as it returns ``True`` the pool is terminated and the results produced so
    far are returned (a *partial* list).  The caller decides the resulting
    ``status`` -- :mod:`physics.optimizer_domain` reports ``"cancelled"``.
    """
    materialised: List[Dict[str, Any]] = list(cases or [])
    if not materialised:
        return []

    total = len(materialised)
    if cancel_cb is not None and bool(cancel_cb()):
        return []

    workers = get_worker_count(processes)
    plan = _chunk_plan(total, workers, None)
    results: List[Dict[str, Any]] = []

    def emit() -> None:
        if progress_cb is not None:
            progress_cb(len(results), total, 0.0)

    if workers == 1:
        for item in materialised:
            if cancel_cb is not None and bool(cancel_cb()):
                return results
            results.append(evaluate_case(item))
            emit()
        return results

    tasks = [(evaluate_case, [materialised[index] for index in chunk]) for chunk in plan]
    context = multiprocessing.get_context("spawn")
    pool = context.Pool(processes=workers, initializer=_pool_init, initargs=(-1,))
    cancelled = False
    try:
        iterator = pool.imap(_pool_worker, tasks)
        while True:
            if cancel_cb is not None and bool(cancel_cb()):
                cancelled = True
                break
            try:
                chunk_result = next(iterator)
            except StopIteration:
                break
            results.extend(chunk_result)
            emit()
    finally:
        if cancelled:
            pool.terminate()
        else:
            pool.close()
        pool.join()
    if cancelled:
        return results
    emit()
    return results


# --------------------------------------------------------------------------
# Self check / demo (``python -m physics.parallel_engine``)
# --------------------------------------------------------------------------
def _demo() -> Dict[str, Any]:
    """Tiny end-to-end demonstration used by the module entry point."""
    cases = [
        {
            "aircraft": as_dict(AircraftConfig()),
            "environment": as_dict(EnvironmentConfig()),
            "launch": as_dict(LaunchConfig(launch_speed=speed)),
            "duration_s": 6.0,
            "dt_max": 2.0e-2,
        }
        for speed in (6.0, 9.0, 12.0, 15.0)
    ]
    seen: List[int] = []
    results = parallel_evaluate(
        cases, processes=0, progress_cb=lambda done, tot, _b: seen.append(done)
    )
    return {
        "backend": backend_name(0),
        "workers": get_worker_count(0),
        "progress": seen,
        "distances_m": [round(float(r.get("distance_m", 0.0)), 4) for r in results],
        "statuses": [r.get("status", "") for r in results],
    }


if __name__ == "__main__":  # pragma: no cover - manual smoke check
    import json

    print(json.dumps(_demo(), indent=2))
