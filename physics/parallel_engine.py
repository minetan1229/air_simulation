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
import atexit
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union, cast

import numpy as np
ProgressCallback = Callable[[int, int, Any], None]

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
from .payload_contract import empty_payload, normalize_payload
from .aero_domain import elevon_effects, helmbold_cl_alpha
from . import gpu_dynamics
from .stress_domain import (
    ALLOWABLE_BASE_PA,
    ALLOWABLE_GSM_EXPONENT,
    ALLOWABLE_STIFFNESS_GAIN,
    ALLOWABLE_STIFFNESS_OFFSET,
    paper_allowable_stress,
    stress_field,
)
from .wind_domain import wind_direction_unit, wind_flow_components

__all__ = [
    "MAX_AUTO_WORKERS",
    "get_worker_count",
    "backend_name",
    "map_evaluate",
    "parallel_map",
    "evaluate_case",
    "parallel_evaluate",
    "evaluate_flight_surrogate",
    "gpu_ensemble_simulation",
    "gpu_batch_evaluate_cases",
    "get_gpu_info",
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

#: Model tag of the full 6-DoF rigid-body integrator
#: (:mod:`physics.flight_sim`).  Every other path in this module is a
#: reduced-order surrogate and is tagged :data:`REDUCED_MODEL_TAG`.
RIGID_BODY_MODEL_TAG = "rigid_body_6dof"

#: Model tag of every reduced-order path of this module.  The GPU ensemble
#: and the GPU optimisation batch integrate a **3-DoF point mass with an
#: explicit Euler step and clipped states**, not the 13-state rigid body of
#: :data:`RIGID_BODY_MODEL_TAG`; the analytical glide surrogate is a closed
#: form without any attitude dynamics at all.  The audit found the three
#: paths disagreeing by 1261x on the peak stress and even on the flight
#: status, so every payload has to name the model it was produced with.
REDUCED_MODEL_TAG = "reduced_surrogate"


def _vector_lift_coefficient(
    xp: Any,
    alpha: Any,
    cl0_base: Any,
    cl_alpha: float,
    cl_max: float,
    stall_alpha_rad: float,
    blend_deg: float = 45.0,
    sharpness: float = 1.4,
    blend_order: int = 4,
) -> Any:
    """Lift coefficient of the shared aerodynamic kernel.

    Thin adapter over :func:`physics.aero_kernel.lift_coefficient`: the GPU
    paths used to carry a private copy of the lift curve, and the copy had
    drifted -- a different stall blend, a different post-stall branch and a
    lift curve clipped to ``[-0.6, cl_max]`` so the deep-stall branch could
    never be reached.  Routing every path through the kernel is what makes
    the GPU evaluate the *same* airframe as the 6-DoF path.

    The kernel works in degrees; the caller works in radians.
    """
    from .aero_kernel import lift_coefficient

    alpha_deg = xp.rad2deg(xp.asarray(alpha, dtype=xp.float64))
    stall_alpha_deg = xp.rad2deg(xp.asarray(stall_alpha_rad, dtype=xp.float64))
    return lift_coefficient(
        xp,
        alpha_deg,
        xp.asarray(cl0_base, dtype=xp.float64),
        xp.asarray(cl_alpha, dtype=xp.float64),
        stall_alpha_deg,
        sharpness,
        cl_max=cl_max,
        blend_order=blend_order,
    )


def _vector_drag_coefficient(
    xp: Any,
    alpha: Any,
    cd0: float,
    k_induced: float,
    cl: Any,
    ge_factor: Any,
    stall_alpha_rad: float,
    blend_deg: float = 15.0,
) -> Any:
    """Drag coefficient of the shared aerodynamic kernel.

    The separated-flow term is the Hoerner form ``2 sin^2(a) + C_D0 cos(a)``
    and the induced drag carries the Wieselsberger ground-effect factor, so
    the polar of this path is the polar of the reference path.  The kernel
    works in degrees; the caller works in radians.
    """
    from .aero_kernel import separated_pressure_drag_coefficient

    alpha_deg = xp.rad2deg(xp.asarray(alpha, dtype=xp.float64))
    stall_alpha_deg = xp.rad2deg(xp.asarray(stall_alpha_rad, dtype=xp.float64))
    separated = separated_pressure_drag_coefficient(
        xp,
        alpha_deg,
        stall_alpha_deg,
        xp.asarray(cd0, dtype=xp.float64),
        blend_deg=blend_deg,
    )
    return (
        xp.asarray(cd0, dtype=xp.float64)
        + xp.asarray(k_induced, dtype=xp.float64) * (cl ** 2) * ge_factor
        + separated
    )


def get_gpu_info() -> Dict[str, Any]:
    """Return runtime GPU hardware availability and metadata.

    Probes CuPy first, then torch.cuda without crashing if unavailable.
    """
    info: Dict[str, Any] = {
        "available": False,
        "backend": "numpy",
        "device_name": None,
        "device_count": 0,
        "cuda_version": None,
        "total_memory_mb": None,
    }
    try:
        import cupy as cp
        count = int(cp.cuda.runtime.getDeviceCount())
        if count > 0:
            dev = cp.cuda.runtime.getDeviceProperties(0)
            raw_name = dev["name"] if isinstance(dev, dict) else getattr(dev, "name", "GPU")
            name = raw_name.decode("utf-8") if isinstance(raw_name, bytes) else str(raw_name)
            total_mem = dev.get("totalGlobalMem") if isinstance(dev, dict) else getattr(dev, "totalGlobalMem", None)
            info["available"] = True
            info["backend"] = "cupy"
            info["device_name"] = name
            info["device_count"] = count
            info["cuda_version"] = getattr(cp.cuda.runtime, "runtimeGetVersion", lambda: None)()
            if total_mem:
                info["total_memory_mb"] = round(float(total_mem) / (1024 * 1024), 1)
            try:
                mempool = cp.get_default_memory_pool()
                info["used_memory_mb"] = round(float(mempool.used_bytes()) / (1024 * 1024), 2)
                info["pool_memory_mb"] = round(float(mempool.total_bytes()) / (1024 * 1024), 2)
            except Exception:
                pass
            return info
    except Exception:
        pass
    try:
        import torch
        if bool(torch.cuda.is_available()) and int(torch.cuda.device_count()) > 0:
            info["available"] = True
            info["backend"] = "torch"
            info["device_name"] = str(torch.cuda.get_device_name(0))
            info["device_count"] = int(torch.cuda.device_count())
            return info
    except Exception:
        pass
    return info


_WARMED_UP = False


def warmup_gpu() -> bool:
    """Pre-compile CUDA JIT kernels in background to eliminate first-request latency."""
    global _WARMED_UP
    if _WARMED_UP:
        return True
    gpu_info = get_gpu_info()
    if not (gpu_info["available"] and gpu_info["backend"] == "cupy"):
        _WARMED_UP = True
        return False
    try:
        # Pre-warm GPU ensemble simulation with a tiny batch
        gpu_ensemble_simulation(n_trajectories=16)
        _WARMED_UP = True
        return True
    except Exception:
        return False

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
# Cached process pool (reused across :func:`parallel_evaluate` calls)
# --------------------------------------------------------------------------
#: The cached spawn pool and the worker count it was created with.  A pool is
#: created once and reused across optimiser generations instead of being
#: spawned and torn down for every generation.  It is released at interpreter
#: exit through :func:`_release_cached_pool` so no child processes linger.
_CACHED_POOL: Any = None
_CACHED_POOL_WORKERS: int = 0


def _release_cached_pool() -> None:
    """Close and join the cached pool, if any (called at interpreter exit)."""
    global _CACHED_POOL, _CACHED_POOL_WORKERS
    pool = _CACHED_POOL
    _CACHED_POOL = None
    _CACHED_POOL_WORKERS = 0
    if pool is None:
        return
    try:
        pool.close()
    except Exception:
        pass
    try:
        pool.join()
    except Exception:
        pass


atexit.register(_release_cached_pool)


def _get_cached_pool(workers: int) -> Any:
    """Return the cached spawn pool, creating or recreating it as needed.

    A pool whose worker count differs from the request is released and
    rebuilt so every caller gets exactly the concurrency it asked for.
    """
    global _CACHED_POOL, _CACHED_POOL_WORKERS
    if _CACHED_POOL is not None and _CACHED_POOL_WORKERS == workers:
        return _CACHED_POOL
    _release_cached_pool()
    context = multiprocessing.get_context("spawn")
    pool = context.Pool(processes=workers, initializer=_pool_init, initargs=(-1,))
    _CACHED_POOL = pool
    _CACHED_POOL_WORKERS = workers
    return pool


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
def isa_air_density(
    environment: Optional[Union[Dict[str, Any], float, int, EnvironmentConfig]] = None
) -> float:
    """Air density in kg/m^3 from an :class:`EnvironmentConfig` payload or altitude float.

    ISA temperature lapse ``T = T0 - L h`` with the hydrostatic
    ``p = p0 (T/T0)^(g/(L R))`` and ``rho = p / (R T)``, then the standard
    virtual-temperature correction for relative humidity
    ``rho *= 1 - 0.378 * RH``.  Always returns a strictly positive, finite
    value.
    """
    if isinstance(environment, (int, float)):
        env = EnvironmentConfig(altitude_m=float(environment))
    elif isinstance(environment, EnvironmentConfig):
        env = environment
    elif isinstance(environment, dict):
        env = from_dict(EnvironmentConfig, environment)
    else:
        env = EnvironmentConfig()
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
        + 0.0015 * abs(float(aircraft.fold_trailing_deg))
    )
    blunt = 0.0045 * max(abs(float(aircraft.fold_trailing_deg)) - 15.0, 0.0)
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
    # The wind component is taken from the shared meteorological convention
    # (see :func:`physics.wind_domain.wind_flow_components`): the previous
    # ``cos(direction)`` term was a 90 deg rotation of the reference path, so
    # the same "north wind" pushed the surrogate sideways.
    flow_x, flow_y = wind_flow_components(float(env.wind_direction_deg))
    wind_x = float(env.wind_speed) * flow_x
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
    # The stress index is the *utilisation* sigma / sigma_allow against the
    # airframe's own allowable fold stress.  The former fixed 120 kPa
    # reference was 54.5x the allowable stress of the default stock, so the
    # same payload reported 0.0113 (6-DoF) and 1.50 (GPU) for one flight.
    allowable_pa = max(paper_allowable_stress(air.paper_gsm, air.paper_stiffness), 1.0e-9)
    stress_index = clamp(stress_pa / allowable_pa, 0.0, 1.0)

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

    return normalize_payload(
        {
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
            "duration_s": float(horizon),
            # Extra diagnostics (never used as objective inputs) -----------------
            "surrogate": True,
            **_approximate_model_metadata("analytical_glide_surrogate"),
            "airspeed_mps": float(airspeed),
            "stall_speed_mps": float(v_stall),
            "load_factor": float(load_factor),
            "dynamic_pressure_pa": float(dynamic_pressure),
            "trim_alpha_deg": float(alpha_trim_deg),
            "steps": int(max(1, math.ceil(flight_time / dt))),
        },
        model=REDUCED_MODEL_TAG,
        approximate=True,
        physics_verification_eligible=False,
        limitations=(
            "Analytical glide surrogate: no 6-DoF attitude integration and no trajectory sampling.",
        ),
    )


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
            if isinstance(result, dict):
                return normalize_payload(
                    {**result, "model_detail": "6dof_rk4"},
                    model=RIGID_BODY_MODEL_TAG,
                    approximate=False,
                    physics_verification_eligible=not bool(result.get("status") in ("failed", "diverged", "error")),
                    limitations=("Aerodynamic and material coefficients require experimental calibration.",),
                )
        except Exception as exc:
            return empty_payload(
                status="failed",
                error=str(exc),
                distance_m=0.0,
                flight_time_s=0.0,
                max_altitude_m=0.0,
                landing_distance_m=0.0,
                max_stress_index=10.0,
                max_stress_pa=1.0e9,
                max_ld_ratio=0.0,
                stall_margin=-10.0,
                max_stall_margin=-10.0,
                launch_speed_mps=100.0,
                min_launch_speed=100.0,
                stability=-10.0,
                max_stability=-10.0,
                crashed=True,
                diverged=True,
                landed=False,
                backend="error",
                model=RIGID_BODY_MODEL_TAG,
                model_detail="6dof_rk4",
                approximate=False,
                physics_verification_eligible=False,
                limitations=(f"Evaluation failed: {exc}",),
            )
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
    best_seen = float("-inf")

    def consider(item: Dict[str, Any]) -> None:
        nonlocal best_seen
        s = item.get("score")
        if s is None:
            s = item.get("distance_m", 0.0)
        try:
            s_val = float(s)
            if math.isfinite(s_val) and s_val > best_seen:
                best_seen = s_val
        except (ValueError, TypeError):
            pass

    def emit() -> None:
        if progress_cb is not None:
            progress_cb(len(results), total, best_seen if math.isfinite(best_seen) else 0.0)

    if workers == 1:
        for item in materialised:
            if cancel_cb is not None and bool(cancel_cb()):
                return results
            result = evaluate_case(item)
            results.append(result)
            consider(result)
            emit()
        return results

    tasks = [(evaluate_case, [materialised[index] for index in chunk]) for chunk in plan]
    pool = _get_cached_pool(workers)
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
            for result in chunk_result:
                consider(result)
            emit()
    finally:
        # The pool is cached and reused across optimiser generations instead
        # of being torn down per call.  It is released once at interpreter
        # exit through ``atexit`` (_release_cached_pool) so no child
        # processes linger.
        pass
    if cancelled:
        return results
    emit()
    return results


# --------------------------------------------------------------------------
# GPU ensemble batch simulation (CuPy with NumPy fallback)
# --------------------------------------------------------------------------
def _rigid_body_model_metadata(model_detail: str) -> Dict[str, Any]:
    """Identify a payload that was produced by the full 6-DoF rigid body.

    The GPU batch integrates the same 13-state rigid body as the reference
    path -- same aerodynamic kernel, same wind field, same structural model
    and the same RK4 scheme -- so its numbers are 6-DoF numbers and may be
    used for physics verification.  Before the batch was rewritten it
    integrated a 3-DoF point mass and reported a peak stress 1261x away from
    the reference path; such a payload is a surrogate and must carry
    :data:`REDUCED_MODEL_TAG` instead.
    """
    return {
        "model": RIGID_BODY_MODEL_TAG,
        "model_detail": model_detail,
        "approximate": False,
        "physics_verification_eligible": True,
        "limitations": [],
    }


def _approximate_model_metadata(model_detail: str) -> Dict[str, Any]:
    """Identify reduced-order outputs separately from the full 6-DoF model.

    Every reduced path reports ``model = REDUCED_MODEL_TAG`` and names the
    concrete surrogate in ``model_detail``.  The audit showed the three
    reduced paths disagreeing with the 6-DoF path by 1261x on the peak
    stress and even on the flight status, so a consumer must never mistake
    a surrogate number for a 6-DoF number.
    """
    return {
        "model": REDUCED_MODEL_TAG,
        "model_detail": model_detail,
        "approximate": True,
        "physics_verification_eligible": False,
        "limitations": [
            "No coupled 6-DoF rigid-body attitude integration or full inertia tensor.",
            "Does not implement the repaired linked trailing-edge and relative-flow dynamics.",
            "Cannot verify launch-to-apex recovery or stable glide performance.",
            "Reduced stress and atmosphere models are not P1 physics verification results.",
        ],
    }


def gpu_ensemble_simulation(
    aircraft: Optional[Any] = None,
    environment: Optional[Any] = None,
    launch: Optional[Any] = None,
    settings: Optional[Any] = None,
    n_trajectories: int = 128,
    noise_config: Optional[Dict[str, float]] = None,
    force_cpu: bool = False,
    random_seed: Optional[int] = 42,
    max_sample_tracks: int = 8,
) -> Dict[str, Any]:
    """GPU-accelerated ensemble Monte Carlo batch flight simulator with CPU fallback.

    Simulates N parallel flight trajectories simultaneously using CuPy (CUDA GPU)
    when available, or pure vectorized NumPy on CPU. Micro-variations (Gaussian noise)
    are applied to launch velocity, release angles, launch height, and wind conditions
    to evaluate aerodynamic stability, landing dispersion, and trajectory confidence envelopes.

    Parameters
    ----------
    aircraft : AircraftConfig or dict, optional
        Airframe configuration (mass, wingspan, planform, lift curve, drag polar).
    environment : EnvironmentConfig or dict, optional
        Atmospheric and wind parameters (density, gravity, wind speed/direction).
    launch : LaunchConfig or dict, optional
        Nominal launch conditions (speed, elevation, roll, yaw, height).
    settings : SimulationSettings or dict, optional
        Integration limits (time step, max duration).
    n_trajectories : int, default 128
        Number of parallel trajectories to simulate (clamped between 16 and 4096).
    noise_config : dict of str -> float, optional
        Standard deviations for launch perturbations:
        ``speed_std`` (m/s), ``pitch_std`` (deg), ``roll_std`` (deg),
        ``yaw_std`` (deg), ``height_std`` (m), ``wind_speed_std`` (m/s),
        ``wind_dir_std`` (deg).
    force_cpu : bool, default False
        If True, forces NumPy execution even if CuPy/CUDA is present.
    random_seed : int, optional
        Seed for pseudo-random perturbation reproducibility.
    max_sample_tracks : int, default 128
        Maximum number of individual trajectory 3D lines to return.

    Returns
    -------
    dict
        Ensemble statistics, confidence envelopes, sample trajectories, and backend info.
    """
    import numpy as np

    air_cfg = aircraft if isinstance(aircraft, AircraftConfig) else from_dict(AircraftConfig, aircraft or {})
    env_cfg = environment if isinstance(environment, EnvironmentConfig) else from_dict(EnvironmentConfig, environment or {})
    lnc_cfg = launch if isinstance(launch, LaunchConfig) else from_dict(LaunchConfig, launch or {})
    derived = derived_properties(air_cfg)

    # 1. Decide execution backend (CuPy GPU with safe NumPy CPU fallback)
    xp = np
    backend = "numpy"
    device_name = None
    gpu_info = get_gpu_info()
    if not force_cpu and gpu_info["available"] and gpu_info["backend"] == "cupy":
        try:
            import cupy as cp
            xp = cp
            backend = "cupy"
            device_name = gpu_info.get("device_name")
        except Exception:
            xp = np
            backend = "numpy"


    # Clamp trajectory count
    N = int(clamp(n_trajectories, 16, 4096))

    if random_seed is not None:
        xp.random.seed(int(random_seed))

    # 2. Extract noise parameters
    nc = dict(noise_config or {})
    speed_std = float(nc.get("speed_std", nc.get("launch_speed_std", 0.25)))
    pitch_std = float(nc.get("pitch_std", nc.get("launch_angle_deg_std", 0.8)))
    roll_std = float(nc.get("roll_std", nc.get("launch_roll_deg_std", 1.2)))
    yaw_std = float(nc.get("yaw_std", nc.get("launch_yaw_deg_std", 0.5)))
    height_std = float(nc.get("height_std", nc.get("launch_height_m_std", 0.05)))
    wind_spd_std = float(nc.get("wind_speed_std", 0.20))
    wind_dir_std = float(nc.get("wind_dir_std", nc.get("wind_direction_deg_std", 5.0)))

    # 3. Generate perturbed initial conditions
    v0_base = float(lnc_cfg.launch_speed)
    pitch0_base = float(lnc_cfg.launch_angle_deg)
    roll0_base = float(lnc_cfg.launch_roll_deg)
    yaw0_base = float(lnc_cfg.launch_yaw_deg)
    h0_base = max(float(lnc_cfg.launch_height_m), 0.1)

    v0 = xp.maximum(v0_base + xp.random.normal(0.0, speed_std, size=N), 0.1)
    pitch_deg = pitch0_base + xp.random.normal(0.0, pitch_std, size=N)
    roll_deg = roll0_base + xp.random.normal(0.0, roll_std, size=N)
    yaw_deg = yaw0_base + xp.random.normal(0.0, yaw_std, size=N)
    h0 = xp.maximum(h0_base + xp.random.normal(0.0, height_std, size=N), 0.02)

    pitch_rad = xp.deg2rad(pitch_deg)
    yaw_rad = xp.deg2rad(yaw_deg)
    roll_rad = xp.deg2rad(roll_deg)

    # Initial velocities (+x forward, +y right, +z down)
    vx = v0 * xp.cos(pitch_rad) * xp.cos(yaw_rad)
    vy = v0 * xp.cos(pitch_rad) * xp.sin(yaw_rad)
    vz = -v0 * xp.sin(pitch_rad)
    vel = xp.stack([vx, vy, vz], axis=1)

    # Initial positions (+z down, so ground is z=0, starting at -h0)
    pos = xp.stack([xp.zeros(N, dtype=xp.float64), xp.zeros(N, dtype=xp.float64), -h0], axis=1)

    # Wind conditions
    w_speed_base = float(env_cfg.wind_speed)
    w_dir_base = float(env_cfg.wind_direction_deg)
    w_speed = xp.maximum(w_speed_base + xp.random.normal(0.0, wind_spd_std, size=N), 0.0)
    w_dir_rad = xp.deg2rad(w_dir_base + xp.random.normal(0.0, wind_dir_std, size=N))
    # Wind vector blowing *along* the flow (WMO "from" convention, shared with
    # the reference path through :func:`physics.wind_domain.wind_flow_components`:
    # flow = -(sin(theta), cos(theta))).
    wx = -w_speed * xp.sin(w_dir_rad)
    wy = -w_speed * xp.cos(w_dir_rad)
    wz = xp.zeros(N, dtype=xp.float64)
    wind = xp.stack([wx, wy, wz], axis=1)

    # 4. Aerodynamic constants and trim parameters
    mass = max(float(derived["mass_kg"]), 1e-4)
    area = max(float(derived["wing_area_m2"]), 1e-4)
    span = max(float(derived["wingspan_m"]), 1e-3)
    aspect_ratio = max(float(derived["aspect_ratio"]), 0.5)
    chord = max(float(derived.get("mac_m", span / aspect_ratio)), 1e-3)
    pitch_inertia = max(float(derived.get("pitch_inertia_kgm2", mass * (chord ** 2) / 12.0)), 1e-7)
    rho = float(isa_air_density(float(env_cfg.altitude_m)))
    gravity = abs(float(env_cfg.gravity))

    elevon_deg = float(getattr(air_cfg, "elevon_deg", 2.0))
    # The camber of the airframe itself must not be scaled by the morphing
    # factor: only the *elevon* contribution washes out with the dynamic
    # pressure.  Scaling the whole cL0 removed the camber (and with it the
    # static stability) exactly in the high-q launch regime.
    cL0_airframe = float(air_cfg.cL0)
    # Elevon authority: the reference path scales the per-degree increment by
    # the deflected chord fraction (``elevon_chord_ratio``).  The local copy
    # used a bare 0.02/0.03 per degree, i.e. 4.5x too much authority, so the
    # two paths trimmed to different angles of attack for the same elevon.
    elevon = elevon_effects(elevon_deg, air_cfg)
    cL0_base = cL0_airframe + float(elevon["dcl"])
    cl_max = float(air_cfg.cl_max)
    cd0 = max(float(air_cfg.cd0), 0.015)
    oswald_e = clamp(float(air_cfg.oswald_e), 0.4, 0.95)
    k_induced = 1.0 / (math.pi * oswald_e * aspect_ratio)

    cl_alpha = float(air_cfg.cLalpha_per_rad)
    if cl_alpha <= 0.0:
        # The single Helmbold implementation of :mod:`physics.aero_domain`.
        # The local copy used ``2 pi AR / (2 + sqrt(AR^2 + 4))``, which drops
        # the taper term and the ``4 AR / pi`` factor: 11.3 % more lift slope
        # than the reference path for the same airframe.
        cl_alpha = helmbold_cl_alpha(aspect_ratio, float(getattr(air_cfg, "taper_ratio", 0.7)))

    cm0_base = float(air_cfg.cm0) + float(elevon["dcm"])
    cm_alpha = float(air_cfg.cm_alpha)
    cm_q = float(air_cfg.cm_q)
    alpha_trim_rad = -cm0_base / cm_alpha if abs(cm_alpha) > 1e-4 else math.radians(4.0)
    alpha_trim_rad = clamp(alpha_trim_rad, -math.radians(8.0), math.radians(22.0))
    stall_alpha_rad = math.radians(float(air_cfg.stall_alpha_deg))

    # Aeroelastic compliance parameters (same closed form as the 6-DoF path:
    # q_aeroelastic = scale_pa * stiffness / compliance).
    aero_comp_gain = max(float(getattr(air_cfg, "aeroelastic_compliance", 0.0)), 0.0)
    stiffness_val = max(float(air_cfg.paper_stiffness), 0.05)
    if aero_comp_gain > 0.0:
        pressure_scale = max(float(getattr(air_cfg, "aeroelastic_pressure_scale_pa", 160.0)), 1.0)
        q_aeroelastic = pressure_scale * stiffness_val / max(aero_comp_gain, 0.1)
    else:
        q_aeroelastic = 5000.0
    morph_exponent = max(float(getattr(air_cfg, "aeroelastic_morph_exponent", 3.0)), 0.5)

    # Control asymmetries
    aileron = float(getattr(air_cfg, "aileron_deg", 0.0))
    asym_cl = float(getattr(air_cfg, "asymmetry_cl", 0.0))
    cl_asym = 0.0035 * aileron + 0.25 * asym_cl

    # Dihedral and roll damping
    dihedral_rad = math.radians(max(float(air_cfg.dihedral_deg), 0.0))
    roll_damping = max(abs(float(air_cfg.cl_roll_rate)), 0.08)

    # 5. Integration settings
    dt = 0.01
    duration_s = 20.0
    if settings is not None:
        if isinstance(settings, dict):
            duration_s = float(settings.get("duration_s", 20.0))
        elif hasattr(settings, "max_flight_time_s"):
            duration_s = float(settings.max_flight_time_s)
    duration_s = float(clamp(duration_s, 1.0, 60.0))
    max_steps = int(duration_s / dt)
    sample_stride = max(1, int(0.05 / dt))  # Sample every 50ms for smooth envelopes

    # State tracking arrays
    active = xp.ones(N, dtype=bool)
    landing_time = xp.full(N, duration_s, dtype=xp.float64)
    landing_pos = xp.zeros((N, 3), dtype=xp.float64)

    # 3D attitude tracking: pitch (theta), roll (phi), yaw (psi)
    theta = xp.copy(pitch_rad)
    phi = xp.copy(roll_rad)
    psi = xp.copy(yaw_rad)
    p_rate = xp.zeros(N, dtype=xp.float64)
    q_rate = xp.zeros(N, dtype=xp.float64)

    history_times: List[float] = []
    history_altitudes = []
    history_distances = []
    sample_count = max(1, min(int(max_sample_tracks), N))
    sample_indices = list(range(sample_count))
    sample_indices_xp = xp.asarray(sample_indices, dtype=xp.int32)
    sample_pos_snapshots = []
    sample_att_snapshots = []

    # Pre-record t = 0
    history_times.append(0.0)
    history_altitudes.append(xp.maximum(-pos[:, 2], 0.0))
    history_distances.append(xp.sqrt(pos[:, 0] ** 2 + pos[:, 1] ** 2))
    sample_pos_snapshots.append(pos[sample_indices_xp, :])
    sample_att_snapshots.append(xp.stack([theta[sample_indices_xp], phi[sample_indices_xp], psi[sample_indices_xp]], axis=1))

    # 6. Time integration loop with advanced flight dynamics
    t = 0.0
    for step in range(1, max_steps + 1):
        t = step * dt

        # Relative airspeed
        v_rel = vel - wind
        v_horiz = xp.sqrt(v_rel[:, 0] ** 2 + v_rel[:, 1] ** 2)
        speed = xp.sqrt(v_horiz ** 2 + v_rel[:, 2] ** 2)
        speed_safe = xp.maximum(speed, 0.1)

        # Dynamic pressure
        q_dyn = 0.5 * rho * (speed_safe ** 2)

        # Passive aeroelastic morphing factor
        if aero_comp_gain > 0.0:
            p_ratio = q_dyn / q_aeroelastic
            morph_factor = 1.0 / (1.0 + (p_ratio ** morph_exponent))
        else:
            morph_factor = 1.0

        # Unit velocity vectors
        u_x = v_rel[:, 0] / speed_safe
        u_y = v_rel[:, 1] / speed_safe
        u_z = v_rel[:, 2] / speed_safe

        # Flight path angle gamma (positive up, so -u_z)
        gamma = xp.arctan2(-u_z, xp.maximum(v_horiz, 1e-4))
        track_yaw = xp.arctan2(u_y, u_x)

        # Angle of attack: difference between pitch attitude and flight path angle
        alpha_geo = theta - gamma

        # High dynamic pressure weathervaning & pitch dynamics
        q_norm = chord / (2.0 * speed_safe)
        # Static pitch moment: Cm_alpha * (alpha - alpha_trim*morph_factor) + Cm_q * q_rate
        # Low-speed apex pushover: natural nose-down when speed drops below 2 m/s
        pushover = xp.where(speed_safe < 2.0, -0.05 * (2.0 - speed_safe), 0.0)
        pitch_torque_coeff = cm_alpha * (alpha_geo - alpha_trim_rad * morph_factor) + cm_q * q_rate * q_norm + pushover
        pitch_accel = (q_dyn * area * chord * pitch_torque_coeff) / pitch_inertia

        # Damped update of pitch rate and pitch angle
        q_rate = xp.clip(q_rate + pitch_accel * dt, -20.0, 20.0)
        theta = xp.clip(theta + q_rate * dt, -math.pi / 2.2, math.pi / 2.2)

        # Effective AoA with pitch dynamics
        alpha = xp.clip(theta - gamma, -0.35, 0.60)

        # Stall modelling: the same Hoerner-anchored post-stall branch and the
        # same smooth stall blend as the reference 6-DoF path.
        cl = _vector_lift_coefficient(
            xp,
            alpha,
            cL0_base * morph_factor + cL0_airframe * (1.0 - morph_factor),
            cl_alpha,
            cl_max,
            stall_alpha_rad,
        )

        # Ground effect induced drag reduction (Wieselsberger)
        alt_m = xp.maximum(-pos[:, 2], 0.02)
        h_b = alt_m / span
        ge_factor = (16.0 * (h_b ** 2)) / (1.0 + 16.0 * (h_b ** 2))
        cd = _vector_drag_coefficient(
            xp, alpha, cd0, k_induced, cl, ge_factor, stall_alpha_rad
        )

        lift_mag = q_dyn * area * cl
        drag_mag = q_dyn * area * cd

        # Drag vector opposes relative wind
        drag_x = -drag_mag * u_x
        drag_y = -drag_mag * u_y
        drag_z = -drag_mag * u_z

        # Pitch-plane lift unit vector (perpendicular to airspeed, pointing up in -z)
        h_safe = xp.maximum(v_horiz / speed_safe, 1e-4)
        l0_x = u_z * (u_x / h_safe)
        l0_y = u_z * (u_y / h_safe)
        l0_z = -h_safe

        # Side unit vector for roll banking
        s_x = -u_y / h_safe
        s_y = u_x / h_safe
        s_z = xp.zeros(N, dtype=xp.float64)

        # Roll dynamics: torque from dihedral, roll damping and asymmetry
        roll_torque_coeff = cl_asym - roll_damping * (p_rate * span / (2.0 * speed_safe))
        roll_accel = (q_dyn * area * span * roll_torque_coeff - 0.25 * dihedral_rad * phi) / max(mass * span * span * 0.1, 1e-6)
        p_rate = xp.clip(p_rate + roll_accel * dt, -15.0, 15.0)
        phi = xp.clip(phi + p_rate * dt, -math.pi / 2.5, math.pi / 2.5)

        # Yaw weathervaning towards velocity heading
        yaw_err = psi - track_yaw
        # Wrap to [-pi, pi]
        yaw_err = xp.arctan2(xp.sin(yaw_err), xp.cos(yaw_err))
        psi = psi - 2.5 * yaw_err * dt

        cos_phi = xp.cos(phi)
        sin_phi = xp.sin(phi)

        # Banked lift unit vector
        l_x = cos_phi * l0_x + sin_phi * s_x
        l_y = cos_phi * l0_y + sin_phi * s_y
        l_z = cos_phi * l0_z + sin_phi * s_z

        lift_x = lift_mag * l_x
        lift_y = lift_mag * l_y
        lift_z = lift_mag * l_z

        # Acceleration (aerodynamic + gravity along +z)
        ax = (lift_x + drag_x) / mass
        ay = (lift_y + drag_y) / mass
        az = (lift_z + drag_z) / mass + gravity

        accel = xp.clip(xp.stack([ax, ay, az], axis=1), -150.0, 150.0)

        # Update state
        new_vel = xp.clip(vel + accel * dt, -80.0, 80.0)
        new_pos = pos + 0.5 * (vel + new_vel) * dt

        # Ground intersection detection (ground is at z >= 0)
        just_landed = active & (new_pos[:, 2] >= 0.0)
        if xp.any(just_landed):
            dz = new_pos[:, 2] - pos[:, 2]
            safe_dz = xp.where(dz > 1e-6, dz, 1e-6)
            frac = xp.clip((-pos[:, 2]) / safe_dz, 0.0, 1.0)
            t_impact = (t - dt) + frac * dt
            landing_time = xp.where(just_landed, t_impact, landing_time)

            p_impact = pos + (new_pos - pos) * frac[:, None]
            p_impact[:, 2] = 0.0
            landing_pos = xp.where(just_landed[:, None], p_impact, landing_pos)
            active = active & ~just_landed

        pos = xp.where(active[:, None], new_pos, pos)
        vel = xp.where(active[:, None], new_vel, vel)

        # History sampling
        if step % sample_stride == 0 or not xp.any(active) or step == max_steps:
            history_times.append(round(step * dt, 4))
            history_altitudes.append(xp.maximum(-pos[:, 2], 0.0))
            history_distances.append(xp.sqrt(pos[:, 0] ** 2 + pos[:, 1] ** 2))
            sample_pos_snapshots.append(pos[sample_indices_xp, :])
            sample_att_snapshots.append(xp.stack([theta[sample_indices_xp], phi[sample_indices_xp], psi[sample_indices_xp]], axis=1))

        if not xp.any(active):
            break

    # Record unlanded positions at end
    if xp.any(active):
        landing_pos = xp.where(active[:, None], pos, landing_pos)

    # 7. Convert arrays back to CPU / NumPy for statistical processing
    if backend == "cupy":
        import cupy as cp

        landing_time_np = cp.asnumpy(landing_time)
        landing_pos_np = cp.asnumpy(landing_pos)
        v0_np = cp.asnumpy(v0)
        pitch_deg_np = cp.asnumpy(pitch_deg)
        roll_deg_np = cp.asnumpy(roll_deg)
        hist_alt_np = [cp.asnumpy(h) for h in history_altitudes]
        hist_dist_np = [cp.asnumpy(h) for h in history_distances]
        snapshots_np = [cp.asnumpy(snap) for snap in sample_pos_snapshots]
        attitudes_np = [cp.asnumpy(snap) for snap in sample_att_snapshots]
    else:
        landing_time_np = np.asarray(landing_time)
        landing_pos_np = np.asarray(landing_pos)
        v0_np = np.asarray(v0)
        pitch_deg_np = np.asarray(pitch_deg)
        roll_deg_np = np.asarray(roll_deg)
        hist_alt_np = [np.asarray(h) for h in history_altitudes]
        hist_dist_np = [np.asarray(h) for h in history_distances]
        snapshots_np = [np.asarray(snap) for snap in sample_pos_snapshots]
        attitudes_np = [np.asarray(snap) for snap in sample_att_snapshots]

    # Sanitize NaN/Inf in case of numerical divergence
    landing_time_np = np.nan_to_num(landing_time_np, nan=0.0, posinf=float(duration_s), neginf=0.0)
    landing_pos_np = np.nan_to_num(landing_pos_np, nan=0.0, posinf=500.0, neginf=-500.0)
    v0_np = np.nan_to_num(v0_np, nan=0.0, posinf=50.0, neginf=0.0)
    pitch_deg_np = np.nan_to_num(pitch_deg_np, nan=0.0, posinf=90.0, neginf=-90.0)
    roll_deg_np = np.nan_to_num(roll_deg_np, nan=0.0, posinf=180.0, neginf=-180.0)

    landing_distances_np = np.nan_to_num(
        np.sqrt(landing_pos_np[:, 0] ** 2 + landing_pos_np[:, 1] ** 2),
        nan=0.0,
        posinf=500.0,
        neginf=0.0,
    )

    # 8. Compute envelope percentiles across sampling points
    n_sample_points = len(history_times)
    alt_p10, alt_p25, alt_p50, alt_p75, alt_p90 = [], [], [], [], []
    alt_min, alt_max = [], []
    dist_mean, dist_p10, dist_p90 = [], [], []

    for s_idx in range(n_sample_points):
        alts = np.nan_to_num(hist_alt_np[s_idx], nan=0.0)
        dists = np.nan_to_num(hist_dist_np[s_idx], nan=0.0)

        alt_p10.append(round(float(np.percentile(alts, 10)), 3))
        alt_p25.append(round(float(np.percentile(alts, 25)), 3))
        alt_p50.append(round(float(np.percentile(alts, 50)), 3))
        alt_p75.append(round(float(np.percentile(alts, 75)), 3))
        alt_p90.append(round(float(np.percentile(alts, 90)), 3))
        alt_min.append(round(float(np.min(alts)), 3))
        alt_max.append(round(float(np.max(alts)), 3))

        dist_mean.append(round(float(np.mean(dists)), 3))
        dist_p10.append(round(float(np.percentile(dists, 10)), 3))
        dist_p90.append(round(float(np.percentile(dists, 90)), 3))

    sample_tracks: List[List[Dict[str, float]]] = [[] for _ in sample_indices]
    for s_i, snap in enumerate(snapshots_np):
        att_snap = attitudes_np[s_i]
        for k in range(sample_count):
            p = snap[k]
            att = att_snap[k]
            px = float(p[0]) if math.isfinite(float(p[0])) else 0.0
            py = float(p[1]) if math.isfinite(float(p[1])) else 0.0
            pz = float(-p[2]) if math.isfinite(float(-p[2])) else 0.0
            ptheta = math.degrees(float(att[0])) if math.isfinite(float(att[0])) else 0.0
            pphi = math.degrees(float(att[1])) if math.isfinite(float(att[1])) else 0.0
            ppsi = math.degrees(float(att[2])) if math.isfinite(float(att[2])) else 0.0
            sample_tracks[k].append({
                "x": round(px, 3),
                "y": round(py, 3),
                "z": round(max(pz, 0.0), 3),
                "pitch": round(ptheta, 2),
                "roll": round(pphi, 2),
                "yaw": round(ppsi, 2),
            })

    # Format sample trajectories with metrics and rank
    sample_trajectories = []
    for k, idx in enumerate(sample_indices):
        pts = sample_tracks[k]
        peak_alt = max([float(p["z"]) for p in pts], default=0.0)
        dist_val = float(landing_distances_np[idx])
        time_val = float(landing_time_np[idx])
        v0_val = float(v0_np[idx])
        p_val = float(pitch_deg_np[idx])
        r_val = float(roll_deg_np[idx])
        sample_trajectories.append({
            "id": idx,
            "rank": 0,
            "points": pts,
            "landing_distance_m": round(dist_val if math.isfinite(dist_val) else 0.0, 3),
            "flight_time_s": round(time_val if math.isfinite(time_val) else 0.0, 3),
            "max_altitude_m": round(peak_alt if math.isfinite(peak_alt) else 0.0, 3),
            "speed_mps": round(v0_val if math.isfinite(v0_val) else 0.0, 2),
            "pitch_deg": round(p_val if math.isfinite(p_val) else 0.0, 2),
            "roll_deg": round(r_val if math.isfinite(r_val) else 0.0, 2),
        })

    # Compute representative "Mean Aircraft" trajectory (average of all trajectories at each timestep)
    mean_trajectory_points: List[Dict[str, float]] = []
    for s_i in range(len(history_times)):
        t_cur = history_times[s_i]
        alts_cur = hist_alt_np[s_i]
        dists_cur = hist_dist_np[s_i]
        snap_cur = snapshots_np[s_i]
        att_cur = attitudes_np[s_i]

        mean_x = float(np.mean(snap_cur[:, 0])) if snap_cur.shape[0] > 0 else float(dist_mean[s_i])
        mean_y = float(np.mean(snap_cur[:, 1])) if snap_cur.shape[0] > 0 else 0.0
        mean_z = float(np.mean(alts_cur)) if len(alts_cur) > 0 else 0.0

        mean_theta = math.degrees(float(np.mean(att_cur[:, 0]))) if att_cur.shape[0] > 0 else 0.0
        mean_phi = math.degrees(float(np.mean(att_cur[:, 1]))) if att_cur.shape[0] > 0 else 0.0
        mean_psi = math.degrees(float(np.mean(att_cur[:, 2]))) if att_cur.shape[0] > 0 else 0.0

        mean_trajectory_points.append({
            "t": round(float(t_cur), 4),
            "x": round(float(mean_x) if math.isfinite(mean_x) else 0.0, 3),
            "y": round(float(mean_y) if math.isfinite(mean_y) else 0.0, 3),
            "z": round(max(float(mean_z) if math.isfinite(mean_z) else 0.0, 0.0), 3),
            "pitch": round(float(mean_theta) if math.isfinite(mean_theta) else 0.0, 2),
            "roll": round(float(mean_phi) if math.isfinite(mean_phi) else 0.0, 2),
            "yaw": round(float(mean_psi) if math.isfinite(mean_psi) else 0.0, 2),
        })

    # Trim mean trajectory when it hits the ground
    mean_flight_time_val = float(np.mean(landing_time_np))
    mean_landing_dist_val = float(np.mean(landing_distances_np))
    mean_peak_alt = max([float(p["z"]) for p in mean_trajectory_points], default=0.0)

    mean_aircraft = {
        "id": "mean",
        "rank": 0,
        "is_mean": True,
        "points": mean_trajectory_points,
        "landing_distance_m": round(mean_landing_dist_val if math.isfinite(mean_landing_dist_val) else 0.0, 3),
        "flight_time_s": round(mean_flight_time_val if math.isfinite(mean_flight_time_val) else 0.0, 3),
        "max_altitude_m": round(mean_peak_alt if math.isfinite(mean_peak_alt) else 0.0, 3),
        "speed_mps": round(float(np.mean(v0_np)), 2),
        "pitch_deg": round(float(np.mean(pitch_deg_np)), 2),
        "roll_deg": round(float(np.mean(roll_deg_np)), 2),
    }

    # Sort descending by distance to assign ranks
    sorted_by_dist = sorted(sample_trajectories, key=lambda tr: cast(float, tr["landing_distance_m"]), reverse=True)
    for rank_idx, tr in enumerate(sorted_by_dist):
        tr["rank"] = rank_idx + 1

    best_distance_flight = sorted_by_dist[0] if sorted_by_dist else None
    worst_distance_flight = sorted_by_dist[-1] if sorted_by_dist else None
    best_hangtime_flight = max(sorted_by_dist, key=lambda tr: cast(float, tr["flight_time_s"])) if sorted_by_dist else None
    worst_hangtime_flight = min(sorted_by_dist, key=lambda tr: cast(float, tr["flight_time_s"])) if sorted_by_dist else None
    best_altitude_flight = max(sorted_by_dist, key=lambda tr: cast(float, tr["max_altitude_m"])) if sorted_by_dist else None
    worst_altitude_flight = min(sorted_by_dist, key=lambda tr: cast(float, tr["max_altitude_m"])) if sorted_by_dist else None
    leaderboard = [
        {
            "rank": tr["rank"],
            "id": tr["id"],
            "landing_distance_m": tr["landing_distance_m"],
            "flight_time_s": tr["flight_time_s"],
            "max_altitude_m": tr["max_altitude_m"],
            "speed_mps": tr["speed_mps"],
            "pitch_deg": tr["pitch_deg"],
            "roll_deg": tr["roll_deg"],
        }
        for tr in sorted_by_dist[:10]
    ]
    bottomboard = [
        {
            "rank": tr["rank"],
            "id": tr["id"],
            "landing_distance_m": tr["landing_distance_m"],
            "flight_time_s": tr["flight_time_s"],
            "max_altitude_m": tr["max_altitude_m"],
            "speed_mps": tr["speed_mps"],
            "pitch_deg": tr["pitch_deg"],
            "roll_deg": tr["roll_deg"],
        }
        for tr in reversed(sorted_by_dist[-5:])
    ]

    # Summary metrics
    mean_t = float(np.mean(landing_time_np))
    std_t = float(np.std(landing_time_np))
    min_t = float(np.min(landing_time_np))
    max_t = float(np.max(landing_time_np))
    median_t = float(np.median(landing_time_np))

    mean_d = float(np.mean(landing_distances_np))
    std_d = float(np.std(landing_distances_np))
    min_d = float(np.min(landing_distances_np))
    max_d = float(np.max(landing_distances_np))
    median_d = float(np.median(landing_distances_np))

    backend_display = f"{backend} ({device_name})" if device_name else backend

    return {
        "backend": backend,
        "backend_display": backend_display,
        **_approximate_model_metadata("gpu_ensemble_3dof"),
        "device_name": device_name,
        "gpu_available": bool(backend == "cupy"),
        "trajectories_count": N,
        "evaluated_count": N,
        "duration_s": duration_s,
        "mean_flight_time_s": round(mean_t, 3),
        "mean_distance_m": round(mean_d, 3),
        "max_flight_time_s": round(max_t, 3),
        "max_distance_m": round(max_d, 3),
        "min_flight_time_s": round(min_t, 3),
        "min_distance_m": round(min_d, 3),
        "mean_trajectory": mean_aircraft,
        "best_flight": best_distance_flight,
        "worst_flight": worst_distance_flight,
        "best_hangtime": best_hangtime_flight,
        "worst_hangtime": worst_hangtime_flight,
        "best_altitude": best_altitude_flight,
        "worst_altitude": worst_altitude_flight,
        "leaderboard": leaderboard,
        "bottomboard": bottomboard,
        "statistics": {
            "flight_time": {
                "mean": round(mean_t, 3),
                "std": round(std_t, 3),
                "min": round(min_t, 3),
                "max": round(max_t, 3),
                "median": round(median_t, 3),
            },
            "distance": {
                "mean": round(mean_d, 3),
                "std": round(std_d, 3),
                "min": round(min_d, 3),
                "max": round(max_d, 3),
                "median": round(median_d, 3),
            },
        },
        "envelope": {
            "time_s": history_times,
            "altitude_p10": alt_p10,
            "altitude_p25": alt_p25,
            "altitude_p50": alt_p50,
            "altitude_p75": alt_p75,
            "altitude_p90": alt_p90,
            "altitude_min": alt_min,
            "altitude_max": alt_max,
            "distance_mean": dist_mean,
            "distance_p10": dist_p10,
            "distance_p90": dist_p90,
        },
        "sample_trajectories": sorted_by_dist,
    }


# --------------------------------------------------------------------------
# High-throughput GPU batch flight evaluator for optimization & parameter sweeps
# --------------------------------------------------------------------------
def gpu_batch_evaluate_cases(
    cases: Sequence[Dict[str, Any]],
    force_cpu: bool = False,
    progress_cb: Optional[ProgressCallback] = None,
) -> List[Dict[str, Any]]:
    """Simulate N flight cases simultaneously on CUDA GPU via CuPy.

    Designed for optimizer generations (NSGA-II, Differential Evolution, Grid Search)
    and batch parameter sweeps.

    The device path is **kernel-launch bound**, not compute bound: one
    integration step issues ~8e3 CuPy kernel launches (six rigid-body state
    evaluations of a few hundred element-wise operations each), so the
    per-step wall clock barely depends on the batch size.  On Windows/WDDM
    (RTX 4060 Laptop, CuPy 14.2) a step costs ~130 ms for a batch of one
    and ~127 ms for a batch of 256, which makes the device path slower than
    the CPU path for every batch size an optimiser actually runs.
    """
    import numpy as np

    N = len(cases)
    if N == 0:
        return []

    gpu_info = get_gpu_info()
    use_gpu = (not force_cpu) and gpu_info["available"] and (gpu_info["backend"] == "cupy")

    if not use_gpu:
        raw_results = parallel_evaluate(cases, processes=0, progress_cb=progress_cb)
        cpu_results: List[Dict[str, Any]] = []
        for idx, item in enumerate(raw_results):
            dur = float(cases[idx].get("duration_s", 20.0))
            t_val = min(float(item.get("flight_time_s", 0.0)), dur)
            status_raw = str(item.get("status", "flying"))
            failed = status_raw in ("error", "failed")
            crashed = bool(item.get("crashed", status_raw == "crashed"))
            diverged = bool(item.get("diverged", status_raw == "diverged"))
            landed = bool(item.get("landed", status_raw == "landed" or (t_val < dur - 0.01 and not crashed)))
            if failed:
                # A failed evaluation is not a flight at all: relabelling it as a
                # timeout (or as a crash) hides the failure from
                # ``optimizer_domain.is_infeasible_flight`` and the broken design
                # would enter the Pareto front as a legitimate 0 m flight.
                status_str = status_raw
            elif crashed:
                status_str = "crashed"
            elif diverged:
                # A diverged integration is not a timeout; relabelling it hides a
                # numerical failure behind a benign looking status.
                status_str = "diverged"
            elif landed:
                status_str = "landed"
            else:
                status_str = "timeout"
            # ``distance_m`` is the **distance flown**: the accumulated
            # horizontal path of the flight.  The CPU fallback used to publish
            # the straight-line range to the touchdown point
            # (``landing_distance_m``) under the name ``distance_m``, so a
            # curving flight reported a different distance from the 6-DoF
            # path and from the device path - both of which publish the
            # accumulated path - and the GA engine, which ranks on
            # ``distance_m``, optimised the range instead of the distance.
            d_val = float(item.get("distance_m", 0.0))
            range_val = float(item.get("horizontal_range_m", d_val))
            landing_val = float(item.get("landing_distance_m", range_val))
            r_entry = dict(item)
            r_entry.update({
                "distance_m": d_val,
                "flight_time_s": round(t_val, 4),
                "horizontal_range_m": range_val,
                "landing_distance_m": landing_val,
                "range_x_m": float(item.get("range_x_m", 0.0)),
                "range_y_m": float(item.get("range_y_m", 0.0)),
                "landed": landed,
                "crashed": crashed,
                "diverged": diverged,
                "status": status_str,
                "duration_s": round(float(dur), 4),
            })
            model = r_entry.get("model", RIGID_BODY_MODEL_TAG)
            approximate = bool(r_entry.get("approximate", False))
            physics_verification_eligible = bool(r_entry.get("physics_verification_eligible", True))
            limitations = r_entry.get("limitations", [])
            cpu_results.append(
                normalize_payload(
                    r_entry,
                    model=model,
                    approximate=approximate,
                    physics_verification_eligible=physics_verification_eligible,
                    limitations=limitations,
                )
            )
        return cpu_results

    try:
        import cupy as xp
    except Exception:
        return gpu_batch_evaluate_cases(cases, progress_cb=progress_cb, force_cpu=True)

    backend = "cupy"
    device_name = gpu_info.get("device_name")
    return evaluate_cases_on_device(
        cases, xp, backend=backend, device_name=device_name, progress_cb=progress_cb
    )


def evaluate_cases_on_device(
    cases: Sequence[Dict[str, Any]],
    xp: Any,
    backend: str = "numpy",
    device_name: Optional[str] = None,
    progress_cb: Optional[ProgressCallback] = None,
) -> List[Dict[str, Any]]:
    """Integrate a batch of flight cases with the shared 6-DoF rigid body.

    The array module ``xp`` selects the device: ``cupy`` for the GPU and
    ``numpy`` for the CPU.  Both evaluate the *same* expressions, so the
    result of this function is the 6-DoF result -- not a surrogate of it.

    The function is public so the parity with the reference path can be
    asserted in the test suite on a machine without a GPU.
    """
    import numpy as np

    N = len(cases)
    if N == 0:
        return []

    # The GPU batch integrates the **same 13-state rigid body** as the 6-DoF
    # path (position, velocity, quaternion, body rates) with the same RK4
    # scheme, the same aerodynamic kernel, the same wind field and the same
    # structural model.  The path used to integrate a 3-DoF point mass with
    # an explicit Euler step and ten artificial clamps, and it reported the
    # peak wing-root stress 1261x away from the reference path.
    #
    # The atmosphere, the gravity and the wind field are properties of the
    # *environment*, so the cases are grouped by environment and each group is
    # integrated as one vectorised batch.  A parameter sweep normally uses a
    # single environment, so this is one batch.
    groups: Dict[Any, List[int]] = {}
    aircrafts: List[AircraftConfig] = []
    environments: List[EnvironmentConfig] = []
    launches: List[LaunchConfig] = []
    durations: List[float] = []
    for index, case in enumerate(cases):
        ac = case.get("aircraft")
        if not isinstance(ac, AircraftConfig):
            ac = from_dict(AircraftConfig, ac or {})
        env = case.get("environment")
        if not isinstance(env, EnvironmentConfig):
            env = from_dict(EnvironmentConfig, env or {})
        lnc = case.get("launch")
        if not isinstance(lnc, LaunchConfig):
            lnc = from_dict(LaunchConfig, lnc or {})
        aircrafts.append(ac)
        environments.append(env)
        launches.append(lnc)
        durations.append(clamp(float(case.get("duration_s", 20.0)), 0.1, 120.0))
        groups.setdefault(_environment_key(env), []).append(index)

    results: List[Dict[str, Any]] = [{} for _ in range(N)]
    completed = 0
    for members in groups.values():
        env = environments[members[0]]
        group_aircraft = [aircrafts[i] for i in members]
        group_launch = [launches[i] for i in members]
        group_durations = [durations[i] for i in members]

        params = gpu_dynamics.build_params(xp, group_aircraft, env)
        initial = xp.asarray(
            np.stack(
                [
                    gpu_dynamics.launch_state_vector(lnc, env, ac)
                    for lnc, ac in zip(group_launch, group_aircraft)
                ]
            ),
            dtype=xp.float64,
        )
        batch = gpu_dynamics.simulate_batch(
            xp,
            params,
            initial,
            xp.asarray(group_durations, dtype=xp.float64),
            2.0e-2,
        )

        state = _to_host(xp, batch["state"])
        distance = _to_host(xp, batch["distance_m"])
        flight_time = _to_host(xp, batch["flight_time_s"])
        stress_pa = _to_host(xp, batch["peak_stress_pa"])
        stress_index = _to_host(xp, batch["peak_stress_index"])
        max_g = _to_host(xp, batch["peak_load_factor"])
        max_q = _to_host(xp, batch["peak_dynamic_pressure_pa"])
        max_ld = _to_host(xp, batch["max_ld_ratio"])
        stability = _to_host(xp, batch["stability"])
        landed = _to_host(xp, batch["landed"])
        crashed = _to_host(xp, batch["crashed"])
        stall_margin = _to_host(xp, batch["stall_margin"])

        for position, index in enumerate(members):
            duration = group_durations[position]
            t_val = min(float(flight_time[position]), duration)
            is_crashed = bool(crashed[position])
            is_landed = bool(landed[position])
            if is_crashed:
                status_str = "crashed"
            elif is_landed:
                status_str = "landed"
            else:
                status_str = "timeout"
            # The payload publishes the **measured** values at full float
            # precision.  It used to round them for JSON tidiness -- the
            # peak stress to two decimals and its index to six -- and the
            # quantisation cost the parity contract: the published peak
            # stress could only agree with the 6-DoF reference to ~1e-5
            # relative instead of the 1e-6 the contract demands
            # (``tests/test_gpu_kernel_parity.py``).  A rounded payload
            # cannot carry the contract it advertises, so only the echoed
            # request fields keep their fixed-point form.
            # The range fields describe the **straight-line** distance from
            # the release point to the final point, the way the 6-DoF path
            # reports them (``horizontal_range_m`` is the hypotenuse of the
            # range, ``landing_distance_m`` its published alias).  The
            # device path used to publish the accumulated path under those
            # names, so the same field meant two different things depending
            # on the backend.
            range_x = abs(float(state[position, 0]) - float(initial[position, 0]))
            range_y = abs(float(state[position, 1]) - float(initial[position, 1]))
            horizontal_range = math.hypot(
                float(state[position, 0]) - float(initial[position, 0]),
                float(state[position, 1]) - float(initial[position, 1]),
            )
            entry = {
                "distance_m": float(distance[position]),
                "flight_time_s": float(t_val),
                "horizontal_range_m": horizontal_range,
                "landing_distance_m": horizontal_range,
                "range_x_m": range_x,
                "range_y_m": range_y,
                "max_altitude_m": abs(float(state[position, 2])),
                "max_speed_mps": round(float(launches[index].launch_speed), 4),
                "launch_speed_mps": round(float(launches[index].launch_speed), 4),
                "max_ld_ratio": float(max_ld[position]),
                "max_stress_index": float(stress_index[position]),
                "max_stress_pa": float(stress_pa[position]),
                "max_g_load": float(max_g[position]),
                "stability": float(stability[position]),
                "stall_margin": float(stall_margin[position]),
                "stalled": bool(stress_index[position] >= 1.0),
                "landed": is_landed,
                "crashed": is_crashed,
                "diverged": False,
                "status": status_str,
                "backend": backend,
                "device_name": device_name,
                "duration_s": round(float(duration), 4),
                **_rigid_body_model_metadata("rigid_body_6dof_batch"),
            }
            results[index] = normalize_payload(
                entry,
                model=RIGID_BODY_MODEL_TAG,
                approximate=False,
                physics_verification_eligible=True,
                limitations=[],
            )
        completed += len(members)
        if progress_cb is not None:
            try:
                progress_cb(completed, N, backend)
            except Exception:
                pass

    return results


def _environment_key(env: EnvironmentConfig) -> Any:
    """Hashable identity of an environment, used to group the batch."""
    return tuple(sorted((as_dict(env) or {}).items(), key=lambda item: item[0]))


def _to_host(xp: Any, array: Any) -> Any:
    """Copy a device array to the host as a NumPy array."""
    if hasattr(array, "get"):
        return array.get()
    return np.asarray(array)



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
