"""6-DoF rigid-body flight dynamics for the paper-airplane simulator.

Conventions (identical to :mod:`physics.config_schema`)
-------------------------------------------------------
* Right handed frame, **+x forward**, **+y right wing**, **+z DOWN**.  Altitude is
  therefore reported as ``-z - ground_level_m``.
* Attitude is a Hamilton quaternion ``q = (w, x, y, z)`` with unit norm; the
  attitude matrix maps body vectors to world vectors
  (``v_world = R(q) @ v_body``), exactly as in :mod:`physics.geometry_utils`.
* Euler angles are aviation ZYX degrees ``(roll, pitch, yaw)``; a positive pitch
  means nose up, a positive roll means right wing down.
* Body axes carry the principal inertias from ``derived_properties``:
  ``x -> roll_inertia_kgm2``, ``y -> pitch_inertia_kgm2``,
  ``z -> yaw_inertia_kgm2``.

Integration
-----------
The integrated state is the 13-component vector

    ``[rx, ry, rz, vx, vy, vz, qw, qx, qy, qz, wx, wy, wz]``

advanced with a classical **RK4** scheme.  Every RK stage

1. normalises the quaternion,
2. subtracts the local wind vector to obtain the air-relative velocity,
3. resolves the flow angles ``alpha`` / ``beta`` in the body frame,
4. calls ``aero_domain.evaluate_aero``,
5. transforms lift / drag / side into the world frame, adds gravity, and
6. evaluates Euler's equation ``I w' = M - w x (I w)`` **including** the
   gyroscopic term through :func:`angular_acceleration_body`.

The time step is chosen adaptively (see
:meth:`FlightSimulator.derived_timestep`) and hard clamped to
``[settings.dt_min, settings.dt_max]``.  Non-finite states or speed / altitude
cap violations trigger a bounded rollback with time-step halving; a persistent
failure freezes the airframe and reports ``crashed`` with
``status == "diverged"``.

The module is import-safe: no import-time side effects, and every upstream
domain (``aero_domain``, ``atmosphere_domain``, ``wind_domain``,
``stress_domain``) is resolved lazily, with a complete local NumPy
implementation used whenever the upstream module is not importable.  The module
and its test-suite therefore run at any point of the parallel build.
"""

from __future__ import annotations

import importlib
import inspect
import math
import threading
import time
import types
import typing
from collections import OrderedDict, deque
from dataclasses import dataclass, field, fields, is_dataclass, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .config_schema import (
    AeroResult,
    AircraftConfig,
    EnvironmentConfig,
    FlightLogSample,
    LaunchConfig,
    SimulationSettings,
    StateVector,
    as_dict,
    clamp,
    deg2rad,
    derived_properties,
    from_dict,
    rad2deg,
    safe_div,
    throw_style,
    THROW_STYLE_CATALOGUE,
    FLIGHT_LOG_COLUMNS,
)
from .geometry_utils import (
    angular_acceleration_body,
    body_to_world,
    cross,
    dot,
    euler_zyx_from_quaternion,
    matrix_to_euler,
    normalize,
    quaternion_conjugate,
    quaternion_derivative,
    quaternion_from_euler,
    quaternion_multiply,
    quaternion_normalize,
    quaternion_to_body_angular_rate,
    quaternion_to_matrix,
    vec3,
    world_to_body,
)

__all__ = [
    "BACKEND",
    "EVALUATE_KEYS",
    "STATUS_VALUES",
    "FlightSimulator",
    "ThrowSolution",
    "aero_forces",
    "atmosphere_state",
    "active_backends",
    "effective_alpha_beta",
    "elevon_effects",
    "evaluate_flight",
    "flight_from_aircraft",
    "ground_effect_factor",
    "helmbold_cl_alpha",
    "initial_state",
    "log_row",
    "prandtl_glauert",
    "resolve_throw",
    "stress_metrics",
    "total_energy",
    "throw_profile_catalogue",
    "wind_vector",
]

#: Execution backend reported by every payload produced by this module.
BACKEND = "numpy"

#: Status vocabulary shared with the REST layer.
STATUS_VALUES: Tuple[str, ...] = ("flying", "landed", "crashed", "timeout", "diverged")

#: Exact key set of the :func:`evaluate_flight` payload (the optimiser relies on it).
EVALUATE_KEYS: Tuple[str, ...] = (
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
)

# --------------------------------------------------------------------------
# Numerical guard rails
# --------------------------------------------------------------------------
#: Hard speed cap (m/s).  Beyond this the airframe is considered lost.
HARD_SPEED_LIMIT_MPS = 500.0
#: Hard altitude cap (m) in either direction from ``ground_level_m``.
HARD_ALTITUDE_LIMIT_M = 1.0e4
#: Hard body-rate cap (rad/s) catching moment / inertia blow-ups.
HARD_ANGULAR_RATE_LIMIT_RPS = 5.0e3
#: Number of time-step halving retries before the integration is divergent.
DIVERGENCE_RETRIES = 6
#: Maximum attitude rotation tolerated inside a single RK4 step (rad).
MAX_ATTITUDE_STEP_RAD = 0.35
#: Maximum body-rate change tolerated inside a single RK4 step (rad/s).  This is
#: the local truncation-error estimate: a step that changes the rate by more than
#: this is rejected and halved.
MAX_RATE_CHANGE_RADPS = 2.0
#: Hard rejection thresholds applied to an integrated step.
MAX_RATE_JUMP_RADPS = 8.0
MAX_SPEED_JUMP_MPS = 25.0
#: Safety factor of the RK4 stiff-mode bound.  The explicit RK4 polynomial is
#: stable on the negative real axis only up to ``|lambda*h| = 2.785``; the
#: numerical stiffness of the airframe is estimated by finite differences and
#: divided by this factor, so the step stays inside the stable region even when
#: the finite-difference probe under-estimates the fastest eigenvalue.
RK4_STIFFNESS_SAFETY = 1.2
#: Relative change of the dynamic pressure that invalidates the stiffness cache.
STIFFNESS_CACHE_TOLERANCE = 0.05
#: Fraction of the mean aerodynamic chord the airframe may travel in one step.
TRAVEL_FRACTION_PER_STEP = 0.5
#: Airspeed below which the flow direction comes from gravity / wind tunnel.
EFFECTIVE_SPEED_FLOOR_MPS = 0.05
#: Airspeed handed to the aerodynamics solver (keeps ``q*area`` finite).
AERO_SPEED_CAP_MPS = 250.0
#: Airspeed below which L/D and range statistics are ignored.
SPEED_STAT_FLOOR_MPS = 1.0

# --------------------------------------------------------------------------
# Aeroelastic attitude stabilisation (prompt.md 2.3 restoring torque)
# --------------------------------------------------------------------------
#: Restoring stiffness as a fraction of ``m*g*MAC`` per radian.  Deliberately
#: small: the aerodynamic ``cm_alpha`` is the dominant pitch stiffness, the
#: folded keel and the dihedral only remove the slow drift and the spiral mode.
ELASTIC_RESTORE_GAIN = 0.12
#: Damping ratio of the structural attitude mode.
ELASTIC_DAMPING_RATIO = 0.7
#: Fraction of the pitch stiffness the keel may add in yaw.
ELASTIC_YAW_GAIN = 0.05
#: Nondimensional roll / yaw damping of the folded keel, ``C_lp`` and ``C_nr``
#: referred to the wing span (``moment = q*S*b*C*p_hat`` with
#: ``p_hat = omega*b/(2V)``). Paper wings have enormous relative damping against
#: roll and yaw rotations due to their high surface area and featherweight mass.
KEEL_ROLL_DAMPING = -0.20
KEEL_YAW_DAMPING = -0.26
#: Airspeed (m/s) at which the keel damping reaches half of its linear value.
KEEL_DAMPING_CUTOFF_MPS = 0.8

# --------------------------------------------------------------------------
# Structural model used when ``stress_domain`` is unavailable
# --------------------------------------------------------------------------
#: Allowable wing-root bending stress of a folded paper structure, per g/m2.
ALLOWABLE_STRESS_PA_PER_GSM = 2.0e4
#: Root section height / width as a fraction of the mean aerodynamic chord.
ROOT_SECTION_HEIGHT_FRACTION = 0.40
ROOT_SECTION_WIDTH_FRACTION = 0.30
#: Inertial (G) load lever arm ahead of the wing root, as a fraction of MAC.
G_LOAD_LEVER_FRACTION = 0.05
#: Impact speed above which a landing is treated as a structural failure.
CRASH_IMPACT_SPEED_MPS = 60.0
#: Rebound speed below which the airframe is at rest on the ground.
LANDING_SPEED_THRESHOLD_MPS = 0.30
#: Maximum number of bounces simulated before the airframe settles.
MAX_BOUNCES = 4

_EPS = 1.0e-12


# --------------------------------------------------------------------------
# Lazy upstream module / callable resolution
# --------------------------------------------------------------------------
_CACHE_LOCK = threading.Lock()
_MODULE_CACHE: Dict[str, Tuple[Any, float]] = {}
_FUNC_CACHE: Dict[Tuple[int, Tuple[str, ...]], Any] = {}
_PLAN_CACHE: Dict[int, Tuple[Any, Any]] = {}
_MISSING_RETRY_S = 0.5


def _optional_module(name: str) -> Any:
    """Import ``physics.<name>`` lazily; return ``None`` when unavailable.

    A missing module is retried at most once per :data:`_MISSING_RETRY_S` so a
    sibling domain that lands while this process is alive is still picked up,
    without paying an ``importlib`` syscall on the hot integration path.  A
    failure is never fatal: the local NumPy implementations below are
    structurally identical replacements.
    """
    now = time.monotonic()
    entry = _MODULE_CACHE.get(name)
    if entry is not None:
        module, last_attempt = entry
        if module is not None or (now - last_attempt) < _MISSING_RETRY_S:
            return module
    module: Any = None
    for candidate in (f"{__package__}.{name}" if __package__ else name, name):
        try:
            module = importlib.import_module(candidate)
        except Exception:  # ImportError, partially initialised module, ...
            module = None
        if module is not None:
            break
    with _CACHE_LOCK:
        _MODULE_CACHE[name] = (module, now)
    return module


def aero_module() -> Any:
    """Return :mod:`physics.aero_domain` when it can be imported."""
    return _optional_module("aero_domain")


def atmosphere_module() -> Any:
    """Return :mod:`physics.atmosphere_domain` when it can be imported."""
    return _optional_module("atmosphere_domain")


def wind_module() -> Any:
    """Return :mod:`physics.wind_domain` when it can be imported."""
    return _optional_module("wind_domain")


def stress_module() -> Any:
    """Return :mod:`physics.stress_domain` when it can be imported."""
    return _optional_module("stress_domain")


def active_backends() -> Dict[str, str]:
    """Report which upstream domains are live (``"numpy"`` when falling back)."""
    return {
        "aero": "numpy" if aero_module() is None else "upstream",
        "atmosphere": "numpy" if atmosphere_module() is None else "upstream",
        "wind": "numpy" if wind_module() is None else "upstream",
        "stress": "numpy" if stress_module() is None else "upstream",
        "backend": BACKEND,
    }


def _resolve_callable(module: Any, names: Sequence[str]) -> Any:
    """First callable of ``names`` present on ``module`` (memoised per module)."""
    if module is None:
        return None
    key = (id(module), tuple(names))
    if key in _FUNC_CACHE:
        return _FUNC_CACHE[key]
    found = None
    for name in names:
        candidate = getattr(module, name, None)
        if callable(candidate):
            found = candidate
            break
    _FUNC_CACHE[key] = found
    return found


def _signature_plan(func: Any) -> Any:
    """Memoised ``[(param, required), ...]`` plan for an upstream callable."""
    key = id(func)
    entry = _PLAN_CACHE.get(key)
    if entry is not None and entry[0] is func:
        return entry[1]
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        plan: Any = None
    else:
        plan = tuple(
            (name, parameter.default is parameter.empty)
            for name, parameter in signature.parameters.items()
            if parameter.kind not in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD)
        )
    _PLAN_CACHE[key] = (func, plan)
    return plan


def _call_upstream(func: Any, values: Dict[str, Any]) -> Any:
    """Call an upstream helper, filling parameters from an alias table.

    Returns ``None`` when the signature demands a parameter absent from
    ``values`` (or the call itself fails), so the caller falls back to its own
    implementation instead of guessing an API.  The signature is inspected only
    once per callable.
    """
    plan = _signature_plan(func)
    if plan is None:
        return None
    kwargs: Dict[str, Any] = {}
    for name, required in plan:
        if name in values:
            kwargs[name] = values[name]
        elif required:
            return None
    try:
        return func(**kwargs)
    except Exception:
        return None


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _finite(value: Any, default: float = 0.0) -> float:
    """Coerce ``value`` to a finite float, falling back to ``default``."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    return out if math.isfinite(out) else float(default)


def _result_field(result: Any, *names: str, default: float = 0.0) -> float:
    """Read the first present field from a dataclass or mapping result."""
    for name in names:
        if isinstance(result, dict):
            if name in result:
                return _finite(result[name], default)
        elif hasattr(result, name):
            return _finite(getattr(result, name), default)
    return float(default)


def _lookup(mapping: Dict[str, Any], names: Sequence[str]) -> Any:
    """First present key of ``names`` inside ``mapping`` (``None`` when absent)."""
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _coerce_value(value: Any, annotation: Any) -> Any:
    """Best-effort conversion of a JSON value into ``annotation``.

    Mirrors the contract's ``_coerce`` rules (scalars, containers, nested
    dataclasses, ``Optional``) without depending on private contract helpers.
    """
    if annotation in (Any, None, inspect.Parameter.empty):
        return value
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is getattr(types, "UnionType", None):
        for option in typing.get_args(annotation):
            if option is type(None):
                continue
            if isinstance(value, (int, float, str, bool, list, tuple, dict)) and not isinstance(
                value, (list, tuple, dict)
            ):
                try:
                    return _coerce_value(value, option)
                except (TypeError, ValueError):
                    continue
            return _coerce_value(value, option)
        return None if value is None else value
    if origin in (list, List, tuple, Tuple):
        args = typing.get_args(annotation)
        inner = args[0] if args else Any
        if origin in (tuple, Tuple) and len(args) == 2 and args[1] is Ellipsis:
            return tuple(_coerce_value(item, inner) for item in value)
        if origin in (tuple, Tuple):
            return tuple(_coerce_value(item, arg) for item, arg in zip(value, args))
        return [_coerce_value(item, inner) for item in value]
    if origin is dict:
        return dict(value)
    if annotation is bool:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if annotation is int:
        return int(value)
    if annotation is float:
        return float(value)
    if annotation is str:
        return str(value)
    if is_dataclass(annotation):
        return _config_from(annotation, value)
    return value


def _config_from(cls: Any, data: Any) -> Any:
    """Build a contract dataclass from a mapping or another instance.

    ``config_schema.from_dict`` is the primary path (it is the contract's
    serialisation entry point).  If it raises -- for instance because the shared
    JSON coercion helper is temporarily unavailable -- an equivalent local
    coercion is used so this domain stays usable in isolation.  Unknown keys are
    ignored, matching the contract.
    """
    if isinstance(data, cls):
        return data
    built: Any = None
    try:
        built = from_dict(cls, data)
    except Exception:
        built = None
    if built is not None:
        return built
    if data is None:
        return cls()
    if not isinstance(data, dict):
        raise TypeError(f"{cls.__name__} expects a mapping or instance, got {type(data).__name__}")
    try:
        hints = typing.get_type_hints(cls)
    except Exception:
        hints = {spec.name: spec.type for spec in fields(cls)}
    kwargs: Dict[str, Any] = {}
    for spec in fields(cls):
        if spec.name in data:
            kwargs[spec.name] = _coerce_value(data[spec.name], hints.get(spec.name, spec.type))
    return cls(**kwargs)


# --------------------------------------------------------------------------
# Aerodynamics primitives (public; also the basis of the local fallback)
# --------------------------------------------------------------------------
def helmbold_cl_alpha(aspect_ratio: float, taper_ratio: float) -> float:
    """Helmbold lift slope ``2*pi*AR / (2 + sqrt(AR^2(1+lam) + 4*AR/pi))``."""
    ar = max(_finite(aspect_ratio), 1.0e-4)
    lam = clamp(_finite(taper_ratio), 0.0, 1.0)
    root = ar * ar * (1.0 + lam) + 4.0 * ar / math.pi
    return 2.0 * math.pi * ar / (2.0 + math.sqrt(max(root, 0.0)))


def prandtl_glauert(mach: float) -> float:
    """Prandtl-Glauert factor (1.0 at ``M = 0``, clamped for ``M >= 0.99``)."""
    m = abs(_finite(mach))
    if m >= 0.99:
        return 1.0 / math.sqrt(1.0 - 0.9801)
    return 1.0 / math.sqrt(max(1.0 - m * m, 1.0e-6))


def ground_effect_factor(height_m: float, span_m: float, enabled: bool = True) -> float:
    """Induced-drag ground-effect factor ``16(h/b)^2 / (1 + 16(h/b)^2)``.

    The value multiplies the free-air induced drag, so it is ``1.0`` when the
    model is disabled (full free-air induced drag) and ``1.0`` again far above the
    ground.  Matches :func:`physics.aero_domain.ground_effect_factor`.
    """
    if not enabled:
        return 1.0
    height = max(_finite(height_m), 0.0)
    span = max(abs(_finite(span_m)), 1.0e-6)
    ratio = 16.0 * (height / span) ** 2
    if not math.isfinite(ratio):
        return 1.0
    return ratio / (1.0 + ratio)


#: Elevon (trailing-edge fold) authority: lift and pitching-moment increments
#: per degree of deflection, scaled by the deflected chord fraction.  A positive
#: ``elevon_deg`` is a trailing-edge-down (push) deflection: it adds lift and
#: produces a nose-down pitching increment, so the trimmed angle of attack moves
#: negative.  The linear-in-degrees law matches the upstream contract solver, so
#: the local fallback and ``aero_domain.elevon_effects`` agree.
ELEVON_LIFT_PER_DEG = 0.020
ELEVON_MOMENT_PER_DEG = 0.030


def elevon_effects(elevon_deg: float, cfg: AircraftConfig) -> Dict[str, float]:
    """Elevon (trailing-edge fold) authority: ``dcl``, ``dcm``, ``dalpha_trim_deg``.

    A trailing edge folded **down** (positive ``elevon_deg``) adds lift
    (``dcl > 0``) and trims the airframe nose-down (``dcm < 0``), demanding a
    larger angle of attack to trim.  The authority is the deflected chord
    fraction ``clamp(|cfg.elevon_chord_ratio|, 0, 1)``.
    """
    deflection = _finite(elevon_deg)
    authority = clamp(abs(_finite(cfg.elevon_chord_ratio, 0.22)), 0.0, 1.0)
    dcl = authority * ELEVON_LIFT_PER_DEG * deflection
    dcm = -authority * ELEVON_MOMENT_PER_DEG * deflection
    cm_alpha = _finite(cfg.cm_alpha)
    if abs(cm_alpha) < 1.0e-12:
        return {"dcl": dcl, "dcm": dcm, "dalpha_trim_deg": 0.0}
    return {"dcl": dcl, "dcm": dcm, "dalpha_trim_deg": rad2deg(safe_div(-(_finite(cfg.cm0) + dcm), cm_alpha, 0.0))}


def _local_atmosphere(env: EnvironmentConfig, altitude_m: float, chord_m: float) -> Dict[str, float]:
    """ISA / local atmosphere with humidity correction and Sutherland viscosity.

    ``T = T0 - L h`` and ``p = p0 (T/T0)^(g/(L R))`` when ``env.isa_mode``; the
    density is corrected with the humidity mixing ratio, the dynamic viscosity
    follows Sutherland's law and the sound speed is ``sqrt(gamma R T)``.
    """
    g0 = 9.80665
    r_air = 287.05
    r_vapour = 461.5
    gamma = 1.4
    altitude = clamp(_finite(altitude_m), -5000.0, 1.0e5)
    if bool(env.isa_mode):
        t0 = _finite(env.sea_level_temperature_K, 288.15)
        p0 = _finite(env.sea_level_pressure_Pa, 101325.0)
        lapse = _finite(env.lapse_rate_K_per_m, 0.0065)
        temperature = t0 - lapse * altitude
        if abs(lapse) > 1.0e-9 and abs(t0) > 1.0e-9 and temperature > 1.0:
            pressure = p0 * (temperature / t0) ** (g0 / (lapse * r_air))
        else:
            pressure = p0
    else:
        temperature = clamp(_finite(env.temperature_K, 288.15), 150.0, 400.0)
        pressure = clamp(_finite(env.pressure_Pa, 101325.0), 1.0e3, 3.0e5)
    temperature = max(temperature, 150.0)
    pressure = max(pressure, 1.0e3)
    celsius = temperature - 273.15
    p_sat = 610.78 * math.exp(clamp(17.2694 * celsius / (celsius + 237.29), -30.0, 30.0))
    p_sat = clamp(p_sat, 0.0, 0.95 * pressure)
    mixing = 0.62198 * p_sat / max(pressure - p_sat, 1.0e-6) * clamp(_finite(env.humidity_rel), 0.0, 1.0)
    virtual = (1.0 + mixing * (r_air / r_vapour - 1.0)) / (1.0 + mixing)
    density = pressure / (r_air * temperature) * virtual
    mu0, sutherland, t_ref = 1.716e-5, 110.4, 273.15
    mu = mu0 * (temperature / t_ref) ** 1.5 * (t_ref + sutherland) / (temperature + sutherland)
    kinematic = safe_div(mu, density, 1.5e-5)
    sound = math.sqrt(gamma * r_air * temperature)
    return {
        "density_kgm3": density,
        "dynamic_viscosity_pa_s": mu,
        "kinematic_viscosity_m2s": kinematic,
        "pressure_pa": pressure,
        "temperature_K": temperature,
        "sound_speed_mps": sound,
        "reference_chord_m": max(abs(_finite(chord_m)), 1.0e-4),
        "backend": "numpy",
    }


#: Cache for :func:`atmosphere_state`.  Air density falls by ~12% per km, i.e.
#: ~1.2e-5 % per millimetre, while a 20 ms integration step moves the airframe
#: far less than a millimetre vertically.  Quantising the altitude to 1 mm
#: therefore makes the four RK4 stages of a step (and the step-rejection
#: retries) share one evaluation, removing ~75% of the atmosphere work from the
#: inner loop without any loss of accuracy.
_ATMOSPHERE_CACHE: "OrderedDict[tuple, Dict[str, float]]" = OrderedDict()
_ATMOSPHERE_CACHE_LIMIT = 256
_ATMOSPHERE_QUANTUM_M = 1.0e-3


def atmosphere_state(
    env: EnvironmentConfig, altitude_m: float = 0.0, reference_chord_m: float = 0.1
) -> Dict[str, float]:
    """Air density, kinematic viscosity and sound speed at ``altitude_m``.

    ``altitude_m`` is the height **above the ground plane** (the integrator
    measures ``-z - ground_level_m``).  The ISA barometric column is a function
    of the geometric altitude **above sea level**, so
    ``env.altitude_m + altitude_m`` is handed to the solver; otherwise a scene
    placed on a plateau (``ground_level_m = 1000``) would be flown at sea-level
    density.

    Primary path: ``atmosphere_domain.evaluate_atmosphere``; fallback: the local
    ISA / humidity / Sutherland implementation :func:`_local_atmosphere`.

    Results are memoised on a quantised (altitude, chord, environment) key --
    see :data:`_ATMOSPHERE_CACHE`.
    """
    altitude = _finite(altitude_m) + _finite(getattr(env, "altitude_m", 0.0))
    chord = max(abs(_finite(reference_chord_m)), 1.0e-4)
    key = (
        round(altitude / _ATMOSPHERE_QUANTUM_M),
        round(chord, 9),
        _finite(env.temperature_K),
        _finite(env.pressure_Pa),
        _finite(env.humidity_rel),
        bool(env.isa_mode),
        _finite(env.sea_level_temperature_K),
        _finite(env.sea_level_pressure_Pa),
        _finite(env.lapse_rate_K_per_m),
        _finite(env.gravity),
        bool(env.compressibility_enabled),
    )
    cached = _ATMOSPHERE_CACHE.get(key)
    if cached is not None:
        _ATMOSPHERE_CACHE.move_to_end(key)
        return cached

    result = _atmosphere_state_uncached(env, altitude, chord)
    _ATMOSPHERE_CACHE[key] = result
    if len(_ATMOSPHERE_CACHE) > _ATMOSPHERE_CACHE_LIMIT:
        _ATMOSPHERE_CACHE.popitem(last=False)
    return result


def _atmosphere_state_uncached(
    env: EnvironmentConfig, altitude: float, chord: float
) -> Dict[str, float]:
    module = atmosphere_module()
    func = _resolve_callable(module, ("evaluate_atmosphere", "atmosphere", "evaluate"))
    if func is not None:
        out = _call_upstream(
            func,
            {
                "env": env,
                "environment": env,
                "altitude_m": altitude,
                "height_m": altitude,
                "reference_chord_m": chord,
                "chord_m": chord,
            },
        )
        if out is not None:
            payload = out if isinstance(out, dict) else as_dict(out)
            if isinstance(payload, dict):
                density = _finite(_lookup(payload, ("density_kgm3", "density", "rho")))
                if density > 0.0:
                    return {
                        "density_kgm3": density,
                        "kinematic_viscosity_m2s": max(
                            _finite(
                                _lookup(payload, ("kinematic_viscosity_m2s", "nu_m2s", "nu", "kinematic_viscosity")),
                                1.5e-5,
                            ),
                            1.0e-12,
                        ),
                        "dynamic_viscosity_pa_s": _finite(
                            _lookup(payload, ("dynamic_viscosity_pa_s", "mu_pa_s", "mu", "dynamic_viscosity")),
                            1.7e-5,
                        ),
                        "pressure_pa": _finite(_lookup(payload, ("pressure_pa", "pressure", "p_pa"))),
                        "temperature_K": _finite(_lookup(payload, ("temperature_K", "temperature"))),
                        "sound_speed_mps": max(
                            _finite(
                                _lookup(payload, ("sound_speed_mps", "speed_of_sound_mps", "a_mps", "sound_speed")),
                                340.0,
                            ),
                            1.0,
                        ),
                        "reference_chord_m": chord,
                        "backend": "upstream",
                    }
    return _local_atmosphere(env, altitude, chord)


def _local_wind(env: EnvironmentConfig, position: Sequence[float], time_s: float) -> np.ndarray:
    """Mean wind with a logarithmic boundary layer (exact zero when calm).

    ``wind_direction_deg`` follows the same meteorological convention as
    :mod:`physics.wind_domain`: it is the bearing the wind blows **FROM**,
    clockwise from north, with ``north = +y`` and ``east = +x``, so the flow
    vector is ``-(sin theta, cos theta, 0)``.  The profile is
    ``u(z)/u(z_ref) = ln(z/z0)/ln(z_ref/z0)`` clamped to ``[0, 1]`` (the upstream
    solver does not bound it above 1 because it is the same log law; the two agree
    exactly at and below the reference height, which is the whole flight regime).
    """
    speed = _finite(env.wind_speed)
    out = np.zeros(3, dtype=np.float64)
    if speed == 0.0:
        return out
    bearing = deg2rad(clamp(_finite(env.wind_direction_deg), 0.0, 360.0))
    altitude = max(-_finite(position[2]) - _finite(env.ground_level_m), 0.0)
    factor = 1.0
    if bool(env.boundary_layer_enabled):
        z0 = max(_finite(env.surface_roughness_m, 8.0e-4), 1.0e-9)
        z_ref = max(_finite(env.reference_height_m, 1.0), z0 * 1.001)
        denominator = math.log(z_ref / z0)
        if abs(denominator) > 1.0e-9:
            factor = clamp(math.log(max(altitude, z0 * 1.001) / z0) / denominator, 0.0, 1.0)
    gust = 1.0 + _finite(env.gust_intensity) * 0.1 * math.sin(0.7 * _finite(time_s))
    magnitude = speed * factor * gust
    out[0] = -magnitude * math.sin(bearing)
    out[1] = -magnitude * math.cos(bearing)
    return out


def wind_vector(position: Sequence[float], env: EnvironmentConfig, time_s: float = 0.0) -> np.ndarray:
    """Local wind velocity vector in the **z-down world frame** (m/s).

    Primary path: ``wind_domain.wind_velocity(position, env, time_s)``.  The
    upstream solver is defined on a *z-up* height axis (its boundary layer, ground
    clamp and thermal plume all assume "height above the ground plane, positive
    up"), so the query point is converted with
    ``height = ground_level_m - z_state`` and the returned vertical component is
    negated back into the z-down world frame used by the integrator.  Fallback:
    :func:`_local_wind`, which returns the exact zero vector for a calm
    atmosphere so the coupling is perfectly inert.

    When the resulting wind field is provably the zero vector the upstream call
    is skipped outright: the mean wind and the turbulence amplitude both scale
    with ``wind_speed``, and the thermal plume with ``thermal_strength``.  Note
    ``gust_intensity`` alone does *not* create flow, so a default-calmed
    atmosphere (``wind_speed=0`` with the default ``gust_intensity=0.2``) still
    short-circuits instead of paying for a simplex evaluation whose amplitude is
    identically zero -- that is the shipped default, evaluated four times per
    RK4 stage.
    """
    if not env.wind_tunnel_enabled and _finite(env.wind_speed) == 0.0 and (
        _finite(env.thermal_strength) == 0.0
    ):
        return np.zeros(3, dtype=np.float64)
    module = wind_module()
    func = _resolve_callable(module, ("wind_velocity", "velocity_at", "sample_wind"))
    if func is not None:
        height = -_finite(position[2]) - _finite(env.ground_level_m)
        query = np.array([_finite(position[0]), _finite(position[1]), height], dtype=np.float64)
        out = _call_upstream(
            func,
            {
                "position": query,
                "position_m": query,
                "env": env,
                "environment": env,
                "time_s": _finite(time_s),
            },
        )
        if out is not None:
            try:
                vector = np.asarray(out, dtype=np.float64).reshape(-1)
            except (TypeError, ValueError):
                vector = np.zeros(0, dtype=np.float64)
            if vector.size >= 3 and bool(np.all(np.isfinite(vector[:3]))):
                return np.array([float(vector[0]), float(vector[1]), -float(vector[2])], dtype=np.float64)
    return _local_wind(env, position, time_s)


# --------------------------------------------------------------------------
# Aerodynamics bridge
# --------------------------------------------------------------------------
def _local_aero(
    aircraft: AircraftConfig,
    env: EnvironmentConfig,
    speed_mps: float,
    alpha_deg: float,
    beta_deg: float,
    mach: float,
    reynolds: float,
    ground_height_m: float,
    angular_rate: Sequence[float],
    density: float,
    kinematic_viscosity: float,
) -> AeroResult:
    """Complete local aerodynamics used when ``aero_domain`` is unavailable.

    Linear lift ``C_L = C_L0 + C_Lalpha*alpha`` blended through a logistic stall
    break into a linear post-stall branch, drag
    ``C_D = C_D0(Re) + C_L^2/(pi*e*AR)`` with the ground-effect reduction, elevon
    trim increments, Prandtl-Glaut compressibility and the full moment set.
    Structurally identical to the upstream solver so both code paths agree.
    """
    derived = derived_properties(aircraft)
    area = max(derived["ref_area_m2"], 1.0e-9)
    mac = max(derived["ref_length_m"], 1.0e-6)
    span = max(derived["ref_span_m"], 1.0e-6)
    aspect = max(derived["aspect_ratio"], 1.0e-3)
    taper = derived["taper_ratio"]
    speed = max(_finite(speed_mps), 0.0)
    rho = max(_finite(density, 1.225), 1.0e-6)
    alpha = _finite(alpha_deg)
    beta = _finite(beta_deg)
    alpha_rad = deg2rad(alpha)
    beta_rad = deg2rad(beta)
    re_eff = clamp(_finite(reynolds, 1.0e5), 1.0e2, 1.0e9)
    cl_alpha = _finite(aircraft.cLalpha_per_rad)
    if cl_alpha <= 0.0:
        cl_alpha = helmbold_cl_alpha(aspect, taper)
    stall_alpha = _finite(aircraft.stall_alpha_deg, 14.0)
    cl_max = _finite(aircraft.cl_max, 0.9)
    cl_linear = _finite(aircraft.cL0) + cl_alpha * alpha_rad
    cl_post = cl_max + _finite(aircraft.post_stall_slope) * (alpha - stall_alpha)
    sharpness = max(abs(_finite(aircraft.stall_sharpness, 1.4)), 1.0e-3)
    blend = 1.0 / (1.0 + math.exp(clamp(-sharpness * (alpha - stall_alpha), -60.0, 60.0)))
    cl = (1.0 - blend) * cl_linear + blend * cl_post
    if bool(env.compressibility_enabled):
        cl *= prandtl_glauert(mach)
    elevon = elevon_effects(aircraft.elevon_deg, aircraft)
    lift_scale = max(_finite(env.lift_scale), 0.0)
    drag_scale = max(_finite(env.drag_scale), 0.0)
    cl += elevon["dcl"] * lift_scale
    cd0 = _finite(aircraft.cd0) * (1.0e5 / re_eff) ** _finite(aircraft.re_cd0_exponent)
    induced = cl * cl / (math.pi * max(_finite(aircraft.oswald_e, 0.8), 0.05) * aspect)
    effect = ground_effect_factor(ground_height_m, span, bool(env.ground_effect_enabled))
    induced *= effect
    dynamic = 0.5 * rho * speed * speed
    lift = dynamic * area * cl * lift_scale
    drag = dynamic * area * (cd0 + induced + 0.020 * abs(elevon["dcl"])) * drag_scale
    side = dynamic * area * _finite(aircraft.cy_beta) * beta_rad
    # Nondimensional rate time scales: (reference length) / (2 V).  Every rate
    # derivative is a genuine *rate* term, so the pitching moment of a trimmed
    # airframe flying straight is exactly ``q S c (cm0 + dcm)``.
    safe_speed = max(speed, 0.5)
    rate_time_mac = safe_div(mac, 2.0 * safe_speed, 0.0)
    rate_time_span = safe_div(span, 2.0 * safe_speed, 0.0)
    rate = vec3(angular_rate)
    cm = (
        _finite(aircraft.cm0)
        + _finite(aircraft.cm_alpha) * alpha_rad
        + elevon["dcm"]
        + 0.5 * _finite(aircraft.cm_q) * float(rate[1]) * rate_time_mac
        + _finite(aircraft.cm_roll_rate) * float(rate[0]) * rate_time_mac
        + _finite(aircraft.cm_pitch_rate) * float(rate[1]) * rate_time_mac
        + _finite(aircraft.cm_yaw_rate) * float(rate[2]) * rate_time_mac
    )
    aileron = _finite(getattr(aircraft, "aileron_deg", 0.0))
    asym_cl = _finite(getattr(aircraft, "asymmetry_cl", 0.0))
    cl_control = 0.0035 * aileron + 0.25 * asym_cl
    cl_roll = (
        _finite(aircraft.cl_beta) * beta_rad
        + cl_control
        + _finite(aircraft.cl_roll_rate) * float(rate[0]) * rate_time_span
        + _finite(aircraft.cl_pitch_rate) * float(rate[1]) * rate_time_span
        + _finite(aircraft.cl_yaw_rate) * float(rate[2]) * rate_time_span
    )
    rudder = _finite(getattr(aircraft, "rudder_deg", 0.0))
    cn_control = 0.0025 * rudder
    cn = (
        _finite(aircraft.cn_beta) * beta_rad
        + cn_control
        + _finite(aircraft.cn_roll_rate) * float(rate[0]) * rate_time_span
        + _finite(aircraft.cn_pitch_rate) * float(rate[1]) * rate_time_span
        + _finite(aircraft.cn_yaw_rate) * float(rate[2]) * rate_time_span
    )
    ld_ratio = safe_div(cl, (cd0 + induced) * drag_scale, 0.0)
    return AeroResult(
        cl=cl,
        cd=(cd0 + induced) * drag_scale,
        cm=cm,
        cy=_finite(aircraft.cy_beta) * beta_rad,
        cl_roll=cl_roll,
        cm_roll=_finite(aircraft.cm_roll_rate) * float(rate[0]) * rate_time_mac,
        cn_roll=_finite(aircraft.cn_yaw_rate) * float(rate[2]) * rate_time_mac,
        lift_n=lift,
        drag_n=drag,
        side_n=side,
        pitching_moment_nm=dynamic * area * mac * cm,
        yawing_moment_nm=dynamic * area * span * cn,
        rolling_moment_nm=dynamic * area * span * cl_roll,
        ld_ratio=ld_ratio,
        dynamic_pressure_pa=dynamic,
        reynolds=re_eff,
        mach=_finite(mach),
        stall_margin=(stall_alpha - alpha) / abs(stall_alpha) if abs(stall_alpha) > 1.0e-9 else 0.0,
        stalled=alpha > stall_alpha,
        cd_min=cd0,
        alpha_ld_max_deg=rad2deg(safe_div(_finite(aircraft.cL0) + cl, cl_alpha, 0.0)),
        circulation_m2ps=safe_div(0.5 * speed * area * cl, span, 0.0),
        trim_alpha_deg=elevon["dalpha_trim_deg"],
        trim_elevon_deg=_finite(aircraft.elevon_deg),
        ground_effect_ratio=effect,
        lift_left_n=0.5 * lift,
        lift_right_n=0.5 * lift,
    )


def _normalise_aero_result(out: Any, speed_mps: float, density: float, kinematic_viscosity: float) -> AeroResult:
    """Coerce any upstream aerodynamics payload into a full :class:`AeroResult`."""
    stalled = bool(out.get("stalled", False)) if isinstance(out, dict) else bool(getattr(out, "stalled", False))
    total_lift = _result_field(out, "lift_n", "lift")
    return AeroResult(
        cl=_result_field(out, "cl"),
        cd=_result_field(out, "cd"),
        cm=_result_field(out, "cm"),
        cy=_result_field(out, "cy"),
        cl_roll=_result_field(out, "cl_roll"),
        cm_roll=_result_field(out, "cm_roll"),
        cn_roll=_result_field(out, "cn_roll"),
        lift_n=total_lift,
        drag_n=_result_field(out, "drag_n", "drag"),
        side_n=_result_field(out, "side_n", "side", "side_force_n", "y_n"),
        pitching_moment_nm=_result_field(out, "pitching_moment_nm", "pitching_moment"),
        yawing_moment_nm=_result_field(out, "yawing_moment_nm", "yawing_moment"),
        rolling_moment_nm=_result_field(out, "rolling_moment_nm", "rolling_moment"),
        ld_ratio=_result_field(out, "ld_ratio"),
        dynamic_pressure_pa=_result_field(out, "dynamic_pressure_pa", "q_pa"),
        reynolds=_result_field(out, "reynolds"),
        mach=_result_field(out, "mach"),
        stall_margin=_result_field(out, "stall_margin"),
        stalled=stalled,
        cd_min=_result_field(out, "cd_min"),
        alpha_ld_max_deg=_result_field(out, "alpha_ld_max_deg"),
        circulation_m2ps=_result_field(out, "circulation_m2ps"),
        trim_alpha_deg=_result_field(out, "trim_alpha_deg"),
        trim_elevon_deg=_result_field(out, "trim_elevon_deg"),
        ground_effect_ratio=_result_field(out, "ground_effect_ratio", default=1.0),
        lift_left_n=_result_field(out, "lift_left_n", default=0.5 * total_lift),
        lift_right_n=_result_field(out, "lift_right_n", default=0.5 * total_lift),
    )


def aero_forces(
    aircraft: AircraftConfig,
    env: EnvironmentConfig,
    speed_mps: float,
    alpha_deg: float,
    beta_deg: float = 0.0,
    mach: float = 0.0,
    reynolds: Optional[float] = None,
    ground_height_m: float = 1.0,
    angular_rate: Sequence[float] = (0.0, 0.0, 0.0),
    density: float = 1.225,
    kinematic_viscosity: float = 1.5e-5,
) -> AeroResult:
    """Evaluate the aerodynamics, preferring the upstream ``aero_domain``.

    The result always carries wind-frame forces (``lift_n`` / ``drag_n`` /
    ``side_n``) and body-frame moments (``rolling`` about +x, ``pitching`` about
    +y, ``yawing`` about +z).  Falls back to :func:`_local_aero` whenever the
    upstream solver is missing, incompatible or returns a non-finite payload.
    """
    module = aero_module()
    func = _resolve_callable(module, ("evaluate_aero", "evaluate", "solve_aero"))
    if func is not None:
        out = _call_upstream(
            func,
            {
                "cfg": aircraft,
                "config": aircraft,
                "aircraft": aircraft,
                "env": env,
                "environment": env,
                "speed_mps": max(_finite(speed_mps), 0.0),
                "alpha_deg": _finite(alpha_deg),
                "beta_deg": _finite(beta_deg),
                "mach": _finite(mach),
                "reynolds": None if reynolds is None else clamp(_finite(reynolds, 1.0e5), 1.0e2, 1.0e9),
                "ground_height_m": max(_finite(ground_height_m, 1.0), 0.0),
                "angular_rate": vec3(angular_rate),
            },
        )
        if out is not None:
            if isinstance(out, dict) and not ("lift_n" in out or "lift" in out):
                out = None
            elif out is not None:
                result = _normalise_aero_result(out, speed_mps, density, kinematic_viscosity)
                if all(
                    math.isfinite(value)
                    for value in (
                        result.lift_n,
                        result.drag_n,
                        result.side_n,
                        result.pitching_moment_nm,
                        result.dynamic_pressure_pa,
                    )
                ):
                    if result.dynamic_pressure_pa <= 0.0:
                        result.dynamic_pressure_pa = (
                            0.5 * max(_finite(density, 1.225), 1.0e-6) * max(_finite(speed_mps), 0.0) ** 2
                        )
                    if result.reynolds <= 0.0:
                        result.reynolds = safe_div(
                            max(_finite(speed_mps), 0.0) * 0.1,
                            max(_finite(kinematic_viscosity, 1.5e-5), 1.0e-12),
                            0.0,
                        )
                    return result
    return _local_aero(
        aircraft,
        env,
        speed_mps,
        alpha_deg,
        beta_deg,
        mach,
        _finite(reynolds, 1.0e5) if reynolds is not None else 1.0e5,
        ground_height_m,
        angular_rate,
        density,
        kinematic_viscosity,
    )


# --------------------------------------------------------------------------
# Structural stress bridge
# --------------------------------------------------------------------------
def _local_stress(
    aircraft: AircraftConfig,
    derived: Dict[str, float],
    env: EnvironmentConfig,
    dynamic_pressure_pa: float,
    g_load: float,
) -> Dict[str, float]:
    """Wing-root bending stress from the dynamic pressure and the G load.

    ``M_root = q*S*b/2 + m*g_load*g*(|cg_offset_x| + 0.05*MAC)`` and
    ``sigma = M_root*c_root/I_root`` for the folded root section, normalised by
    the allowable stress of :data:`ALLOWABLE_STRESS_PA_PER_GSM` per g/m2.
    """
    area = max(derived["wing_area_m2"], 1.0e-9)
    span = max(derived["wingspan_m"], 1.0e-6)
    mac = max(derived["mac_m"], 1.0e-6)
    mass = max(derived["mass_kg"], 1.0e-9)
    gsm = max(_finite(aircraft.paper_gsm, 100.0), 1.0)
    fold = 1.0 - 0.35 * abs(math.sin(deg2rad(_finite(aircraft.fold_trailing_deg))))
    height = max(ROOT_SECTION_HEIGHT_FRACTION * mac * clamp(fold, 0.15, 1.0), 1.0e-4)
    width = max(ROOT_SECTION_WIDTH_FRACTION * mac, 1.0e-4)
    inertia = max(width * height ** 3 / 12.0, 1.0e-18)
    lever = height * 0.5
    q = max(_finite(dynamic_pressure_pa), 0.0)
    load = max(_finite(g_load), 0.0)
    gravity = abs(_finite(env.gravity))
    inertial = mass * load * gravity * (abs(_finite(aircraft.cg_offset_x)) + G_LOAD_LEVER_FRACTION * mac)
    root_moment = q * area * span * 0.5 + inertial
    sigma = root_moment * lever / inertia
    allowable = ALLOWABLE_STRESS_PA_PER_GSM * gsm
    return {
        "max_stress_pa": sigma,
        "allowable_stress_pa": allowable,
        "max_stress_index": sigma / allowable,
        "root_moment_nm": root_moment,
        "aerodynamic_moment_nm": q * area * span * 0.5,
        "inertial_moment_nm": inertial,
        "backend": "numpy",
    }


def stress_metrics(
    aircraft: AircraftConfig,
    env: EnvironmentConfig,
    derived: Dict[str, float],
    dynamic_pressure_pa: float,
    g_load: float,
    speed_mps: float = 0.0,
    alpha_deg: float = 0.0,
    beta_deg: float = 0.0,
    mach: float = 0.0,
    reynolds: float = 0.0,
    altitude_m: float = 0.0,
    ground_height_m: float = 1.0,
    angular_rate: Sequence[float] = (0.0, 0.0, 0.0),
    density_kgm3: float = 1.225,
) -> Dict[str, float]:
    """Wing stress index, preferring the upstream ``stress_domain``.

    ``g_load`` is the specific-force load factor used as the structural load
    factor.  Returns ``max_stress_index`` (normalised, **not** clamped),
    ``max_stress_pa``, the allowable stress and the wing-root bending moment.
    Any signature mismatch or upstream failure falls through to the local
    closed-form model :func:`_local_stress`.
    """
    module = stress_module()
    func = _resolve_callable(
        module,
        ("analyse", "analyze", "analyse_stress", "analyze_stress", "stress_analysis", "compute_stress"),
    )
    if func is not None:
        out = _call_upstream(
            func,
            {
                "cfg": aircraft,
                "config": aircraft,
                "aircraft": aircraft,
                "env": env,
                "environment": env,
                "derived": derived,
                "density_kgm3": max(_finite(density_kgm3, 1.225), 0.0),
                "dynamic_pressure_pa": max(_finite(dynamic_pressure_pa), 0.0),
                "q_pa": max(_finite(dynamic_pressure_pa), 0.0),
                "q": max(_finite(dynamic_pressure_pa), 0.0),
                "g_load": max(_finite(g_load), 0.0),
                "load_factor": max(_finite(g_load), 0.0),
                "nz": max(_finite(g_load), 0.0),
                "speed_mps": max(_finite(speed_mps), 0.0),
                "alpha_deg": _finite(alpha_deg),
                "beta_deg": _finite(beta_deg),
                "mach": _finite(mach),
                "reynolds": max(_finite(reynolds), 0.0),
                "altitude_m": _finite(altitude_m),
                "height_m": _finite(ground_height_m),
                "ground_height_m": _finite(ground_height_m),
                "angular_rate": vec3(angular_rate),
                "time_s": 0.0,
            },
        )
        if out is not None:
            payload = out if isinstance(out, dict) else as_dict(out)
            if isinstance(payload, dict):
                index = _lookup(
                    payload, ("max_stress_index", "stress_index", "utilisation", "utilization", "peak_index")
                )
                sigma = _lookup(
                    payload,
                    ("max_stress_pa", "peak_stress_pa", "sigma_max_pa", "max_bending_stress_pa", "max_stress"),
                )
                if index is not None or sigma is not None:
                    index_value = _finite(index, -1.0)
                    sigma_value = _finite(sigma, -1.0)
                    allowable = max(
                        _finite(_lookup(payload, ("allowable_stress_pa", "allowable_pa", "sigma_allow_pa")), 0.0),
                        1.0e-9,
                    )
                    if index_value < 0.0 and sigma_value < 0.0:
                        index_value = -1.0
                    else:
                        if index_value < 0.0:
                            index_value = sigma_value / allowable
                        if sigma_value < 0.0:
                            sigma_value = index_value * allowable
                        moment = _finite(
                            _lookup(
                                payload,
                                (
                                    "root_bending_moment_nm",
                                    "wing_root_moment_nm",
                                    "root_moment_nm",
                                    "bending_moment_nm",
                                    "max_bending_moment_nm",
                                ),
                            ),
                            0.0,
                        )
                        return {
                            "max_stress_index": index_value,
                            "max_stress_pa": sigma_value,
                            "root_moment_nm": moment,
                            "allowable_stress_pa": allowable,
                            "backend": "upstream",
                        }
    return _local_stress(aircraft, derived, env, dynamic_pressure_pa, g_load)


# --------------------------------------------------------------------------
# Public free functions
# --------------------------------------------------------------------------
def effective_alpha_beta(
    velocity_body: Sequence[float], gravity: Sequence[float], env: Optional[EnvironmentConfig] = None
) -> Tuple[float, float]:
    """Effective flow angles ``(alpha_deg, beta_deg)`` from a body-frame velocity.

    ``alpha = atan2(w, u)`` and ``beta = asin(v / V)`` of the **air-relative**
    velocity expressed in the body frame (x forward, y right, z down): a nose-up
    attitude relative to the flow gives a positive ``alpha`` and a velocity
    towards the right wing gives a positive ``beta``.

    ``gravity`` is consulted only when the airspeed collapses below
    :data:`EFFECTIVE_SPEED_FLOOR_MPS` (the first RK4 stages of a hover, or a
    free-fall trajectory).  It is then interpreted as **already rotated into the
    body frame**, so the flow is taken to arrive from the direction the airframe
    falls towards (``-g``) instead of from numerical noise.  With
    ``env.wind_tunnel_enabled`` the tunnel flow along the body x axis is used
    instead, which is the physical limit for a stationary model in a tunnel.
    """
    vector = vec3(velocity_body)
    speed = float(np.linalg.norm(vector))
    if speed < EFFECTIVE_SPEED_FLOOR_MPS:
        tunnel = bool(getattr(env, "wind_tunnel_enabled", False)) if env is not None else False
        if tunnel:
            vector = np.array(
                [max(_finite(getattr(env, "wind_tunnel_speed", 0.0)), EFFECTIVE_SPEED_FLOOR_MPS), 0.0, 0.0],
                dtype=np.float64,
            )
        else:
            vector = -normalize(vec3(gravity), fallback=(0.0, 0.0, -1.0)) * EFFECTIVE_SPEED_FLOOR_MPS
        speed = float(np.linalg.norm(vector))
    if speed < _EPS:
        return 0.0, 0.0
    alpha = math.degrees(math.atan2(float(vector[2]), float(vector[0])))
    beta = math.degrees(math.asin(clamp(safe_div(float(vector[1]), speed, 0.0), -1.0, 1.0)))
    return alpha, beta


def total_energy(state: StateVector, gravity: float) -> float:
    """Specific total energy per unit mass ``0.5*|v|^2 + g*h`` (joules/kg).

    ``h`` is the altitude ``-z`` above the world origin (the reference the
    integrator uses); multiplying by the mass gives the energy in joules.  With
    drag and rotation disabled the value is invariant along a trajectory, which
    is the analytic check required by prompt.md 2.3.
    """
    velocity = vec3(state.velocity_mps)
    altitude = -float(state.position_m[2])
    return 0.5 * float(np.dot(velocity, velocity)) + _finite(gravity) * altitude


# --------------------------------------------------------------------------
# Throw model ("投げ方" / "力の入れ方")
# --------------------------------------------------------------------------
#: Physical ceilings on the body rates a hand can impart at release.  A paper
#: airframe is essentially rate-limited by the hand/wrist: measured release
#: rates for indoor record throws are of order 1-3 rad/s in pitch and up to
#: +-20 rad/s in roll, so the model clamps well below any value that would
#: make the 6-DoF integration meaningless.
MAX_RELEASE_PITCH_RATE_RADPS = 8.0
MAX_RELEASE_ROLL_RATE_RADPS = 45.0
MAX_RELEASE_YAW_RATE_RADPS = 14.0
#: Ceiling on a release speed produced by the hand model.  A hand can reach
#: roughly 25-30 m/s on a light airframe (the 98 m distance record is a
#: ~20 m/s throw); beyond that the model refuses to extrapolate.
MAX_RELEASE_SPEED_MPS = 60.0


def _velocity_from_elevation_azimuth(
    speed: float, elevation_deg: float, azimuth_deg: float
) -> np.ndarray:
    """Release velocity vector from a speed and a flight-path direction.

    ``elevation_deg`` is measured up from the horizon (positive climbs) and
    ``azimuth_deg`` from the nose axis towards the right wing, matching
    ``launch_angle_deg`` / ``launch_yaw_deg``.  ``+z`` points **down**, so the
    vertical component carries the opposite sign.
    """
    elevation = deg2rad(elevation_deg)
    azimuth = deg2rad(azimuth_deg)
    return np.array(
        [
            speed * math.cos(elevation) * math.cos(azimuth),
            speed * math.cos(elevation) * math.sin(azimuth),
            -speed * math.sin(elevation),
        ],
        dtype=np.float64,
    )


@dataclass
class ThrowSolution:
    """Resolved hand impulse and the release state it produces.

    Attributes are plain JSON-friendly values so the REST layer can hand the
    object straight to :func:`physics.config_schema.as_dict`.
    """

    style: str = "direct"
    style_label: str = ""
    impulse_active: bool = False
    speed_source: str = "direct"
    force_n: float = 0.0
    duration_s: float = 0.0
    impulse_ns: float = 0.0
    mass_kg: float = 0.0
    peak_acceleration_mps2: float = 0.0
    force_direction_body: Tuple[float, float, float] = (1.0, 0.0, 0.0)
    grip_point_m: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    moment_nm: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    torque_rate_radps: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    angular_rate_radps: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    speed_mps: float = 0.0
    velocity_mps: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    flight_path_deg: Tuple[float, float] = (0.0, 0.0)
    release_alpha_deg: float = 0.0
    release_euler_deg: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    quaternion: Tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)
    release_height_m: float = 0.0
    position_m: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    kinetic_energy_j: float = 0.0
    impulse_to_weight: float = 0.0
    runup_m: float = 0.0
    warnings: List[str] = field(default_factory=list)


def _resolve_throw_style_key(launch: LaunchConfig) -> Tuple[str, Any]:
    """Return ``(style key, ThrowStyle)`` tolerating an unknown string.

    An unknown key never raises: it degrades to the neutral ``direct`` profile
    and adds a warning, so a stale client cannot break the simulation.
    """
    key = str(getattr(launch, "throw_style", "direct") or "direct")
    try:
        return key, throw_style(key)
    except KeyError:
        return "direct", throw_style("direct")


def throw_profile_catalogue() -> List[Dict[str, Any]]:
    """JSON payload describing every selectable throwing technique."""
    out: List[Dict[str, Any]] = []
    for style in THROW_STYLE_CATALOGUE:
        out.append(
            {
                "key": style.key,
                "label": style.label,
                "label_ja": style.label_ja,
                "description": style.description,
                "notes": style.notes,
                "speed_source": "direct" if style.key == "direct" else "impulse",
                "overrides": style.launch_overrides(),
            }
        )
    return out


def resolve_throw(
    launch: Any,
    aircraft: Any = None,
    environment: Any = None,
) -> ThrowSolution:
    """Resolve a launch condition into a hand impulse and a release state.

    Model
    -----
    The hand delivers a force ``F`` for a time ``dt`` along a direction that
    is tilted away from the nose axis by ``(force_pitch, force_yaw)``:

    1. **Impulse**  ``J = F * dt``  (Ns).
    2. **Release speed**  ``|v| = |J| / m`` when ``throw_speed_source`` is
       ``"impulse"``, otherwise the explicitly requested ``launch_speed``.
    3. **Flight path vs. attitude**  the airframe leaves the hand along
       ``(launch_angle_deg + throw_force_pitch_deg, launch_yaw_deg +
       throw_force_yaw_deg)`` in impulse mode -- the force direction is
       ``throw_force_*`` above the nose axis -- and along ``(launch_angle_deg,
       launch_yaw_deg)`` in direct mode.  The **attitude** is always
       ``(launch_roll + release_roll, launch_angle + release_pitch,
       launch_yaw + release_yaw)``, so ``release_pitch_deg`` is the angle of
       attack left on the wing at the instant of release (reported as
       :attr:`ThrowSolution.release_alpha_deg`).  With every offset at zero and
       no force tilt this is exactly the historical "velocity along the nose
       axis raised by ``launch_angle_deg``" rule.
    4. **Release rotation**  the contact force acts at the grip point
       ``r = (grip_x * MAC, grip_y * semispan, 0)`` relative to the centre of
       gravity, so the impulsive moment is ``M = r x F`` and the rate it would
       produce is ``w = I^-1 M dt``.  A finite contact time plus the compliance
       of the hand and forearm absorb most of that rotation, so the surviving
       fraction is ``throw_torque_efficiency`` (default 0.15); the wrist /
       forearm rates are then added and the total clamped to the release
       ceilings above.

    Parameters
    ----------
    launch, aircraft, environment:
        Contract dataclasses or plain dictionaries; ``aircraft`` is optional
        and only needed when the impulsive rotation has to be resolved.
    """
    cfg = _config_from(LaunchConfig, launch)
    air = _config_from(AircraftConfig, aircraft if aircraft is not None else AircraftConfig())
    env = (
        _config_from(EnvironmentConfig, environment)
        if environment is not None
        else EnvironmentConfig()
    )

    key, style = _resolve_throw_style_key(cfg)
    derived = derived_properties(air)
    mass = max(_finite(derived["mass_kg"]), 1.0e-9)
    mac = max(_finite(derived["mac_m"]), 1.0e-4)
    semispan = max(_finite(derived["wingspan_m"]), 1.0e-6) * 0.5
    inertia = np.diag(
        [
            max(_finite(derived["roll_inertia_kgm2"]), 1.0e-14),
            max(_finite(derived["pitch_inertia_kgm2"]), 1.0e-14),
            max(_finite(derived["yaw_inertia_kgm2"]), 1.0e-14),
        ]
    )

    warnings: List[str] = []
    if key != str(getattr(cfg, "throw_style", "direct")):
        warnings.append(
            f"unknown throw style '{cfg.throw_style}'; falling back to 'direct'"
        )

    force = abs(_finite(cfg.throw_force_n))
    duration = abs(_finite(cfg.throw_duration_s))
    pitch_tilt = deg2rad(_finite(cfg.throw_force_pitch_deg))
    yaw_tilt = deg2rad(_finite(cfg.throw_force_yaw_deg))
    active = bool(cfg.impulse_active)

    # --- force direction and magnitude (body frame, +z is down) ----------
    direction_body = np.array(
        [
            math.cos(pitch_tilt) * math.cos(yaw_tilt),
            math.cos(pitch_tilt) * math.sin(yaw_tilt),
            -math.sin(pitch_tilt),
        ],
        dtype=np.float64,
    )
    force_body = force * direction_body
    impulse_ns = force * duration
    if force <= 0.0 and active:
        warnings.append("impulse mode is on but 'throw_force_n' is 0; release speed is 0")
    if duration <= 0.0 and active:
        warnings.append("impulse mode is on but 'throw_duration_s' is 0; release speed is 0")

    # --- release attitude vs. flight path ---------------------------------
    # ``(flight_pitch_deg, flight_yaw_deg)`` is the **flight path** the airframe
    # is thrown along.  The ``release_*`` fields then rotate the **attitude**
    # away from it, so ``release_pitch_deg`` is exactly the angle of attack the
    # hand leaves on the wing (see the :class:`LaunchConfig` contract).  Adding
    # the offsets to the velocity as well would pin alpha to zero at release and
    # leave any airframe trimmed at a positive alpha with the full residual
    # ``cm(0) = -cm_alpha * alpha_trim``; because the pitch mode is fast
    # (omega_sp ~ 200 rad/s on a 4 g paper airframe) that residual pitches the
    # nose over the top and loops the glider inside 0.2 s.
    flight_pitch_deg = _finite(cfg.launch_angle_deg)
    flight_yaw_deg = _finite(cfg.launch_yaw_deg)
    release_pitch_deg = _finite(cfg.release_pitch_deg)
    release_yaw_deg = _finite(cfg.release_yaw_deg)
    roll_total = _finite(cfg.launch_roll_deg) + _finite(cfg.release_roll_deg)
    pitch_total = flight_pitch_deg + release_pitch_deg
    yaw_total = flight_yaw_deg + release_yaw_deg
    quaternion = quaternion_from_euler(roll_total, pitch_total, yaw_total)

    # --- release speed and direction -------------------------------------
    if active:
        speed = impulse_ns / mass
        if not math.isfinite(speed) or speed > MAX_RELEASE_SPEED_MPS:
            speed = min(speed if math.isfinite(speed) else MAX_RELEASE_SPEED_MPS, MAX_RELEASE_SPEED_MPS)
            warnings.append(
                f"release speed clamped to {MAX_RELEASE_SPEED_MPS:g} m/s (hand impulse too large)"
            )
        # The impulsive hand pushes along the force direction, which sits
        # ``throw_force_pitch_deg`` / ``throw_force_yaw_deg`` above the nose
        # axis, so it is that direction -- not the nominal flight path -- that
        # the airframe leaves the hand along.
        velocity_pitch_deg = flight_pitch_deg + rad2deg(pitch_tilt)
        velocity_yaw_deg = flight_yaw_deg + rad2deg(yaw_tilt)
    else:
        speed = abs(_finite(cfg.launch_speed))
        velocity_pitch_deg = flight_pitch_deg
        velocity_yaw_deg = flight_yaw_deg
    velocity = _velocity_from_elevation_azimuth(speed, velocity_pitch_deg, velocity_yaw_deg)

    # --- release rotation ------------------------------------------------
    grip_z_val = getattr(cfg, "throw_grip_z", getattr(cfg, "grip_z", None))
    grip_z = 0.015 if grip_z_val is None else _finite(grip_z_val, 0.015)
    grip = np.array(
        [
            _finite(cfg.throw_grip_x) * mac + _finite(air.cg_offset_x),
            _finite(cfg.throw_grip_y) * semispan,
            grip_z,
        ],
        dtype=np.float64,
    )
    moment = np.cross(grip, force_body)
    efficiency = clamp(_finite(cfg.throw_torque_efficiency, 0.15), 0.0, 1.0)
    torque_rate = np.linalg.solve(inertia, moment * duration * efficiency)
    rates = np.array(
        [
            clamp(
                _finite(torque_rate[0] + _finite(cfg.throw_wrist_spin_radps)),
                -MAX_RELEASE_ROLL_RATE_RADPS,
                MAX_RELEASE_ROLL_RATE_RADPS,
            ),
            clamp(
                _finite(torque_rate[1] + _finite(cfg.throw_pitch_rate_radps)),
                -MAX_RELEASE_PITCH_RATE_RADPS,
                MAX_RELEASE_PITCH_RATE_RADPS,
            ),
            clamp(
                _finite(torque_rate[2] + _finite(cfg.throw_yaw_rate_radps)),
                -MAX_RELEASE_YAW_RATE_RADPS,
                MAX_RELEASE_YAW_RATE_RADPS,
            ),
        ],
        dtype=np.float64,
    )
    if not active:
        # The neutral entry point keeps the historical behaviour exactly:
        # no force, no grip, no wrist rotation.
        grip = np.zeros(3, dtype=np.float64)
        moment = np.zeros(3, dtype=np.float64)
        torque_rate = np.zeros(3, dtype=np.float64)
        if any(abs(_finite(v)) > 0.0 for v in (cfg.throw_wrist_spin_radps, cfg.throw_pitch_rate_radps, cfg.throw_yaw_rate_radps)):
            rates = np.array(
                [
                    clamp(_finite(cfg.throw_wrist_spin_radps), -MAX_RELEASE_ROLL_RATE_RADPS, MAX_RELEASE_ROLL_RATE_RADPS),
                    clamp(_finite(cfg.throw_pitch_rate_radps), -MAX_RELEASE_PITCH_RATE_RADPS, MAX_RELEASE_PITCH_RATE_RADPS),
                    clamp(_finite(cfg.throw_yaw_rate_radps), -MAX_RELEASE_YAW_RATE_RADPS, MAX_RELEASE_YAW_RATE_RADPS),
                ],
                dtype=np.float64,
            )
            warnings.append(
                "explicit release rates are applied even though 'throw_speed_source' is 'direct'"
            )
        else:
            rates = np.zeros(3, dtype=np.float64)

    height = abs(_finite(cfg.launch_height_m))
    position = np.array(
        [
            _finite(cfg.launch_position_m[0]),
            _finite(cfg.launch_position_m[1]),
            -height,
        ],
        dtype=np.float64,
    )
    gravity = abs(_finite(env.gravity, 9.80665))
    kinetic_energy = 0.5 * mass * speed * speed

    return ThrowSolution(
        style=key,
        style_label=style.label,
        impulse_active=active,
        speed_source=str(cfg.throw_speed_source),
        force_n=float(force),
        duration_s=float(duration),
        impulse_ns=float(impulse_ns),
        mass_kg=float(mass),
        peak_acceleration_mps2=float(safe_div(force, mass, 0.0)),
        force_direction_body=tuple(float(v) for v in direction_body),  # type: ignore[arg-type]
        grip_point_m=tuple(float(v) for v in grip),  # type: ignore[arg-type]
        moment_nm=tuple(float(v) for v in moment),  # type: ignore[arg-type]
        torque_rate_radps=tuple(float(v) for v in torque_rate),  # type: ignore[arg-type]
        angular_rate_radps=tuple(float(v) for v in rates),  # type: ignore[arg-type]
        speed_mps=float(speed),
        velocity_mps=tuple(float(v) for v in velocity),  # type: ignore[arg-type]
        flight_path_deg=(float(velocity_pitch_deg), float(velocity_yaw_deg)),
        # Nose-up attitude relative to flight path gives positive angle of attack:
        # alpha = pitch_attitude - flight_path_pitch.
        release_alpha_deg=float(pitch_total - velocity_pitch_deg),
        release_euler_deg=(float(roll_total), float(pitch_total), float(yaw_total)),
        quaternion=tuple(float(v) for v in quaternion),  # type: ignore[arg-type]
        release_height_m=float(height),
        position_m=tuple(float(v) for v in position),  # type: ignore[arg-type]
        kinetic_energy_j=float(kinetic_energy),
        impulse_to_weight=float(safe_div(impulse_ns, mass * gravity, 0.0)),
        runup_m=float(clamp(_finite(cfg.throw_runup_m), 0.0, 3.04)),
        warnings=warnings,
    )


def initial_state(
    launch: LaunchConfig,
    environment: EnvironmentConfig,
    aircraft: Optional[AircraftConfig] = None,
) -> StateVector:
    """Build the launch state from a launch condition.

    The release condition is produced by :func:`resolve_throw`, which
    reconstructs the hand impulse ("力の入れ方") that creates it.  With the
    neutral ``direct`` style the velocity points along the body x axis raised
    by ``launch_angle_deg`` above the horizon (a positive angle climbs) and
    turned by ``launch_yaw_deg``, ``launch_roll_deg`` tilts the attitude about
    the velocity vector, and the angular rate is zero -- exactly the original
    convention.  The position is ``(x, y, -launch_height_m)`` because +z points
    **down**, so the initial altitude above the environment ground level is
    exactly ``launch_height_m``.
    """
    throw = resolve_throw(launch, aircraft, environment)
    return StateVector(
        time_s=0.0,
        position_m=tuple(float(v) for v in throw.position_m),  # type: ignore[arg-type]
        velocity_mps=tuple(float(v) for v in throw.velocity_mps),  # type: ignore[arg-type]
        quaternion=tuple(float(v) for v in throw.quaternion),  # type: ignore[arg-type]
        angular_velocity_radps=tuple(float(v) for v in throw.angular_rate_radps),  # type: ignore[arg-type]
        acceleration_mps2=(0.0, 0.0, _finite(_config_from(EnvironmentConfig, environment).gravity)),
        angular_accel_radps2=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )


def log_row(sample: FlightLogSample) -> Dict[str, Any]:
    """Flatten a :class:`FlightLogSample` into a CSV/JSON ready row.

    The returned mapping has exactly ``set(FLIGHT_LOG_COLUMNS)`` as its keys.
    """
    return {name: getattr(sample, name) for name in FLIGHT_LOG_COLUMNS}


def _pack(state: StateVector) -> np.ndarray:
    """Pack a :class:`StateVector` into the 13-component integration vector."""
    position = vec3(state.position_m)
    velocity = vec3(state.velocity_mps)
    quaternion = quaternion_normalize(state.quaternion)
    omega = vec3(state.angular_velocity_radps)
    return np.concatenate([position, velocity, quaternion, omega])


def _unpack(y: np.ndarray, time_s: float) -> StateVector:
    """Inverse of :func:`_pack` (the quaternion is renormalised)."""
    quaternion = quaternion_normalize(y[6:10])
    return StateVector(
        time_s=_finite(time_s),
        position_m=(float(y[0]), float(y[1]), float(y[2])),
        velocity_mps=(float(y[3]), float(y[4]), float(y[5])),
        quaternion=(float(quaternion[0]), float(quaternion[1]), float(quaternion[2]), float(quaternion[3])),
        angular_velocity_radps=(float(y[10]), float(y[11]), float(y[12])),
        acceleration_mps2=(0.0, 0.0, 0.0),
        angular_accel_radps2=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )


def _state_is_finite(state: StateVector) -> bool:
    """True when every component of the state is a finite number."""
    for vector in (state.position_m, state.velocity_mps, state.quaternion, state.angular_velocity_radps):
        for value in vec3(vector):
            if not math.isfinite(float(value)):
                return False
    return math.isfinite(float(state.time_s))


class FlightSimulator:
    """6-DoF RK4 flight-dynamics integrator with adaptive sub-stepping.

    Parameters
    ----------
    aircraft, environment:
        Contract dataclasses (plain dictionaries are accepted too).  The
        environment stays mutable at runtime, so an API update of the atmosphere
        takes effect on the very next sub-step (prompt.md 2.1).
    launch, settings:
        Initial condition and integrator settings.
    """

    def __init__(
        self,
        aircraft: AircraftConfig,
        environment: EnvironmentConfig,
        launch: Optional[LaunchConfig] = None,
        settings: Optional[SimulationSettings] = None,
    ) -> None:
        self.aircraft: AircraftConfig = (
            _config_from(AircraftConfig, aircraft)
        )
        self.environment: EnvironmentConfig = (
            _config_from(EnvironmentConfig, environment)
        )
        self.settings: SimulationSettings = (
            _config_from(SimulationSettings, settings)
        )
        self._launch: LaunchConfig = _config_from(LaunchConfig, launch)
        self._history: deque = deque(maxlen=max(1, int(self.settings.ring_buffer_capacity)))
        self._state: StateVector = initial_state(self._launch, self.environment, self.aircraft)
        self._diverged = False
        self._rolled_back = False
        self._bounce_count = 0
        self._record_enabled = True
        self._next_record_time = 0.0
        self._diagnostics_cache: Optional[Dict[str, Any]] = None
        self._stiffness_cache: Optional[Tuple[float, float]] = None
        self._refresh_derived()
        self._record_sample()

    # -- configuration ----------------------------------------------------
    def _refresh_derived(self) -> None:
        """Recompute the cached geometry, mass, inertia and trim attitude."""
        derived = derived_properties(self.aircraft)
        self._derived: Dict[str, float] = derived
        self._mass: float = max(derived["mass_kg"], 1.0e-9)
        self._chord: float = max(derived["mac_m"], 1.0e-4)
        self._inertia: np.ndarray = np.diag(
            [
                max(derived["roll_inertia_kgm2"], 1.0e-12),
                max(derived["pitch_inertia_kgm2"], 1.0e-12),
                max(derived["yaw_inertia_kgm2"], 1.0e-12),
            ]
        )
        self._restoring_gain = (
            ELASTIC_RESTORE_GAIN
            * clamp(_finite(self.aircraft.paper_stiffness, 0.35), 0.0, 1.0)
            * self._mass
            * abs(_finite(self.environment.gravity))
            * self._chord
        )
        self._restoring_damping = 2.0 * ELASTIC_DAMPING_RATIO * math.sqrt(
            max(self._restoring_gain * self._mass * self._chord, 0.0)
            * max(derived["roll_inertia_kgm2"], 1.0e-12)
        )
        self._trim_pitch_deg = self._resolve_trim_pitch()

    def _resolve_trim_pitch(self) -> float:
        """Elevon trim angle of attack, from ``aero_domain`` when available."""
        module = aero_module()
        func = _resolve_callable(module, ("trim_solution", "trim"))
        speed = max(float(np.linalg.norm(vec3(self._state.velocity_mps))), 1.0)
        if func is not None:
            out = _call_upstream(
                func,
                {
                    "cfg": self.aircraft,
                    "config": self.aircraft,
                    "aircraft": self.aircraft,
                    "env": self.environment,
                    "environment": self.environment,
                    "speed_mps": speed,
                    "elevon_deg": _finite(self.aircraft.elevon_deg),
                    "target_alpha_deg": None,
                },
            )
            if out is not None:
                payload = out if isinstance(out, dict) else as_dict(out)
                if isinstance(payload, dict):
                    value = _finite(_lookup(payload, ("alpha_deg", "trim_alpha_deg", "alpha")), float("nan"))
                    if math.isfinite(value):
                        return value
        return elevon_effects(self.aircraft.elevon_deg, self.aircraft)["dalpha_trim_deg"]

    def _restoring_moment(
        self, quaternion: np.ndarray, omega: np.ndarray, dynamic_pressure: float, speed: float,
        effective_dihedral_deg: Optional[float] = None,
    ) -> np.ndarray:
        """Aeroelastic restoring and damping moment about the body axes.

        Two structural effects are combined, both physically real for a folded
        paper sheet and both required by prompt.md 2.3 ("attitude stabilisation
        restoring torque"):

        * a **keel damping** proportional to the roll and yaw rates, scaled by
          the dynamic pressure and the nondimensional rate ``omega*b/(2V)`` (see
          :data:`KEEL_ROLL_DAMPING`);
        * a weak **pendulum restoring** moment of the folded mass towards the
          elevon trim angle and towards wings level, with critical-ish damping.

        The keel term is small next to the aerodynamic moments in magnitude but
        it is the only convergent term for the roll and yaw modes.
        """
        roll_deg, pitch_deg, _ = euler_zyx_from_quaternion(quaternion)
        pitch_error = deg2rad(_finite(pitch_deg) - self._trim_pitch_deg)
        roll_error = deg2rad(_finite(roll_deg))
        d_deg = _finite(effective_dihedral_deg) if effective_dihedral_deg is not None else _finite(self.aircraft.dihedral_deg)
        dihedral = math.sin(deg2rad(d_deg))
        moment = np.zeros(3, dtype=np.float64)
        moment[1] = -self._restoring_gain * pitch_error - self._restoring_damping * float(omega[1])
        moment[0] = (
            -0.5 * self._restoring_gain * dihedral * roll_error - 0.5 * self._restoring_damping * float(omega[0])
        )
        moment[2] = -ELASTIC_YAW_GAIN * self._restoring_gain * float(omega[2])
        area = max(self._derived["wing_area_m2"], 1.0e-9)
        span = max(self._derived["wingspan_m"], 1.0e-6)
        airspeed = max(_finite(speed), 0.0)
        scale = airspeed / (airspeed + KEEL_DAMPING_CUTOFF_MPS)
        rate_hat = span / (2.0 * max(airspeed, 0.5))
        keel = max(_finite(dynamic_pressure), 0.0) * area * span * rate_hat * scale
        moment[0] += keel * KEEL_ROLL_DAMPING * float(omega[0])
        moment[2] += keel * KEEL_YAW_DAMPING * float(omega[2])
        # Aerodynamic dihedral restoring couple proportional to dynamic pressure
        if abs(dihedral) > 1.0e-6 and abs(roll_error) > 1.0e-6:
            aero_dihedral = -max(_finite(dynamic_pressure), 0.0) * area * span * 0.04 * dihedral * math.sin(roll_error) * scale
            moment[0] += aero_dihedral
        return moment

    # -- lifecycle --------------------------------------------------------
    @property
    def state(self) -> StateVector:
        """Current state; a copy is returned so callers cannot mutate it."""
        return replace(self._state)

    @property
    def launch(self) -> LaunchConfig:
        """The active launch condition (a copy)."""
        return replace(self._launch)

    def reset(self, launch: Optional[LaunchConfig] = None) -> StateVector:
        """Re-initialise the airframe, optionally with a new launch condition.

        The ring buffer is cleared and the ``t = 0`` sample recorded.
        """
        if launch is not None:
            self._launch = _config_from(LaunchConfig, launch)
        if bool(getattr(self.environment, "wind_tunnel_enabled", False)):
            alpha = float(getattr(self.environment, "wind_tunnel_alpha_deg", 4.0))
            beta = float(getattr(self.environment, "wind_tunnel_beta_deg", 0.0))
            pitch_rad = math.radians(alpha)
            yaw_rad = math.radians(-beta)
            q = quaternion_from_euler(0.0, pitch_rad, yaw_rad)
            self._state = StateVector(
                time_s=0.0,
                position_m=(0.0, 0.0, -1.2),
                velocity_mps=(0.0, 0.0, 0.0),
                quaternion=(float(q[0]), float(q[1]), float(q[2]), float(q[3])),
                angular_velocity_radps=(0.0, 0.0, 0.0),
                acceleration_mps2=(0.0, 0.0, 0.0),
                angular_accel_radps2=(0.0, 0.0, 0.0),
                landed=False,
                crashed=False,
            )
        else:
            self._state = initial_state(self._launch, self.environment, self.aircraft)
        self._diverged = False
        self._rolled_back = False
        self._bounce_count = 0
        self._history.clear()
        self._diagnostics_cache = None
        self._stiffness_cache = None
        self._refresh_derived()
        self._record_sample()
        return self.state

    def reset_history(self) -> None:
        """Empty the ring buffer and re-arm the recorder at the current time."""
        self._history.clear()
        self._next_record_time = float(self._state.time_s)

    def load_state(self, state: StateVector) -> StateVector:
        """Install ``state`` as the live state (timeline / session restore).

        The state is validated (finite components only) and the derived caches
        are dropped so the next sub-step is evaluated from the restored values.
        Raises ``ValueError`` for a non-finite or malformed state; the previous
        state is left untouched in that case.
        """
        candidate = _config_from(StateVector, state)
        if not _state_is_finite(candidate):
            raise ValueError("load_state requires a finite 6-DoF state")
        # Round-tripping through the 13-vector normalises the quaternion and
        # coerces every component to a proper float tuple.
        self._state = _unpack(_pack(candidate), float(candidate.time_s))
        self._state.landed = bool(candidate.landed)
        self._state.crashed = bool(candidate.crashed)
        self._diverged = False
        self._rolled_back = False
        self._bounce_count = 0
        self._diagnostics_cache = None
        self._stiffness_cache = None
        return self.state

    def frame_count(self) -> int:
        """Number of samples currently held by the ring buffer."""
        return int(len(self._history))

    def seek_frame(self, index: int) -> Optional[FlightLogSample]:
        """Return the recorded sample at ``index`` (oldest first) or ``None``.

        Negative indices resolve from the end.  The live state is never touched,
        which is what makes timeline scrubbing side-effect free.
        """
        count = len(self._history)
        if count == 0:
            return None
        position = int(index)
        if position < 0:
            position += count
        if position < 0 or position >= count:
            return None
        return self._history[position]

    # -- adaptive time step ----------------------------------------------
    def _stiffness_estimate(self, y: np.ndarray, time_s: float, core: Dict[str, Any]) -> float:
        """Fastest local eigenvalue of the coupled aero / rigid-body system.

        A finite-difference probe of the body rate and of the velocity gives
        ``max(|d(w_dot)/d(omega)|, |d(a)/d(v)|)`` in 1/s.  For the attitude this
        is the aerodynamic pitch / roll damping rate ``q S c^2 |C_mq| /(2 V I)``,
        which is exactly the time scale the explicit RK4 stability limit
        constrains.  The result is cached and only refreshed when the dynamic
        pressure moves by more than :data:`STIFFNESS_CACHE_TOLERANCE`.
        """
        q_reference = max(_finite(core["aero"].dynamic_pressure_pa), 0.0)
        cached = self._stiffness_cache
        if cached is not None and abs(q_reference - cached[0]) <= STIFFNESS_CACHE_TOLERANCE * max(abs(cached[0]), 1.0):
            return cached[1]
        velocity = vec3(y[3:6])
        speed = float(np.linalg.norm(velocity))
        probe_rate = y.copy()
        probe_rate[10:13] = vec3(y[10:13]) + np.full(3, 1.0 / math.sqrt(3.0))
        perturbed = self._compute_dynamics(probe_rate, time_s)
        rate_stiffness = float(
            np.linalg.norm(vec3(perturbed["angular_acceleration"]) - vec3(core["angular_acceleration"]))
        ) / 1.0
        speed_stiffness = 0.0
        if speed > 1.0e-6:
            delta = 0.01 * max(speed, 1.0)
            probe_velocity = y.copy()
            probe_velocity[3:6] = velocity + delta * velocity / speed
            perturbed = self._compute_dynamics(probe_velocity, time_s)
            speed_stiffness = (
                float(np.linalg.norm(vec3(perturbed["acceleration"]) - vec3(core["acceleration"]))) / delta
            )
        value = max(rate_stiffness, speed_stiffness)
        if not math.isfinite(value) or value < 0.0:
            value = 0.0
        self._stiffness_cache = (q_reference, value)
        return value

    def _timestep_from(self, core: Dict[str, Any], y: np.ndarray, time_s: float) -> float:
        """Combine the four CFL-style limits into one clamped step."""
        dt_min = max(abs(_finite(self.settings.dt_min, 1.0e-5)), 1.0e-12)
        dt_max = max(_finite(self.settings.dt_max, 2.0e-2), dt_min)
        if not bool(self.settings.adaptive):
            return clamp(dt_max, dt_min, dt_max)
        omega = vec3(y[10:13])
        velocity = vec3(y[3:6])
        if not bool(np.all(np.isfinite(omega))) or not bool(np.all(np.isfinite(velocity))):
            return dt_max
        rate = float(np.linalg.norm(omega))
        speed = float(np.linalg.norm(velocity))
        limits = [dt_max]
        if rate > 1.0e-9:
            limits.append(MAX_ATTITUDE_STEP_RAD / rate)
        if speed > 1.0e-9:
            limits.append(TRAVEL_FRACTION_PER_STEP * self._chord / speed)
        rate_change = float(np.linalg.norm(vec3(core["angular_acceleration"])))
        if rate_change > 1.0e-9:
            limits.append(MAX_RATE_CHANGE_RADPS / rate_change)
        stiffness = self._stiffness_estimate(y, time_s, core)
        if stiffness > 1.0e-9:
            limits.append(RK4_STIFFNESS_SAFETY / stiffness)
        candidate = min(limits)
        if not math.isfinite(candidate):
            return dt_max
        return clamp(candidate, dt_min, dt_max)

    def derived_timestep(self) -> float:
        """Adaptive RK4 step, always inside ``[dt_min, dt_max]`` and finite.

        The step is the smallest of

        * an **attitude limit** ``MAX_ATTITUDE_STEP_RAD / |omega|`` (the body may
          not rotate by more than 20 degrees in one step),
        * a **translational limit** ``TRAVEL_FRACTION_PER_STEP * MAC / V`` (the
          airframe may not travel more than half a mean chord),
        * a **rate-change limit** ``MAX_RATE_CHANGE_RADPS / |omega_dot|`` (the
          local truncation-error estimate on the body rate),
        * a **stiff-mode limit** ``RK4_STIFFNESS_SAFETY / lambda`` keeping the
          explicit RK4 step inside its stability region, and
        * ``settings.dt_max``.

        The value is hard clamped, so a pathological state (zero rate, zero
        airspeed, NaN) still yields a usable step instead of an infinite one.
        Returns ``dt_max`` verbatim when ``settings.adaptive`` is False.
        """
        y = _pack(self._state)
        time_s = float(self._state.time_s)
        core = self._compute_dynamics(y, time_s)
        return self._timestep_from(core, y, time_s)

    # -- aerodynamics / dynamics core ------------------------------------
    def _compute_dynamics(self, y: np.ndarray, time_s: float) -> Dict[str, Any]:
        """Aerodynamics plus rigid-body dynamics at an arbitrary 13-state.

        Returns the world-frame acceleration, the body angular acceleration
        (Euler's equation with the gyroscopic term), the flow angles, the
        specific force used for the G load and the raw :class:`AeroResult`.
        """
        position = vec3(y[0:3])
        velocity = vec3(y[3:6])
        quaternion = quaternion_normalize(y[6:10])
        omega = vec3(y[10:13])
        gravity = _finite(self.environment.gravity, 9.80665)
        gravity_world = np.array([0.0, 0.0, gravity], dtype=np.float64)
        altitude = -float(position[2]) - _finite(self.environment.ground_level_m)
        air = atmosphere_state(self.environment, altitude, self._chord)
        wind = wind_vector(position, self.environment, time_s)
        relative = velocity - wind
        speed = float(np.linalg.norm(relative))
        if speed > AERO_SPEED_CAP_MPS and speed > _EPS:
            relative = relative * (AERO_SPEED_CAP_MPS / speed)
            speed = AERO_SPEED_CAP_MPS
        gravity_body = world_to_body(quaternion, gravity_world)
        body_relative = world_to_body(quaternion, relative)
        alpha_deg, beta_deg = effective_alpha_beta(body_relative, gravity_body, self.environment)
        mach = safe_div(speed, max(air["sound_speed_mps"], 1.0), 0.0)
        reynolds = safe_div(speed * self._chord, max(air["kinematic_viscosity_m2s"], 1.0e-12), 0.0)

        # Dynamic pressure
        q_dyn = 0.5 * max(air["density_kgm3"], 1.0e-6) * speed * speed

        # 2-Way Aeroelastic Coupling:
        # Check dynamic pressure q. If aeroelastic_flex > 0 or aeroelastic_twist > 0:
        # call stress_domain.aeroelastic_deformations or lazy fallback.
        aero_flex_gain = max(_finite(getattr(self.aircraft, "aeroelastic_flex", 0.0)), 0.0)
        aero_twist_gain = max(_finite(getattr(self.aircraft, "aeroelastic_twist", 0.0)), 0.0)
        aero_dihedral_deg = 0.0
        aero_twist_deg = 0.0
        if (aero_flex_gain > 0.0 or aero_twist_gain > 0.0) and q_dyn > 0.0:
            stress_mod = stress_module()
            deform_func = _resolve_callable(stress_mod, ("aeroelastic_deformations", "compute_aeroelastic_deformations"))
            if deform_func is not None:
                try:
                    res_deform = deform_func(self.aircraft, q_dyn)
                    if isinstance(res_deform, dict):
                        aero_dihedral_deg = float(res_deform.get("delta_dihedral_deg", 0.0))
                        aero_twist_deg = float(res_deform.get("washout_twist_deg", 0.0))
                    elif isinstance(res_deform, (tuple, list)) and len(res_deform) >= 2:
                        aero_dihedral_deg = float(res_deform[0])
                        aero_twist_deg = float(res_deform[1])
                except Exception:
                    deform_func = None
            if deform_func is None:
                stiffness = max(_finite(self.aircraft.paper_stiffness, 0.35), 0.05)
                q_ref = 100.0 * stiffness
                aero_dihedral_deg = aero_flex_gain * (q_dyn / q_ref) * 5.0
                aero_twist_deg = aero_twist_gain * (q_dyn / q_ref) * 4.0

        effective_alpha_deg = alpha_deg - aero_twist_deg
        effective_dihedral_deg = float(self.aircraft.dihedral_deg) + aero_dihedral_deg

        result = aero_forces(
            self.aircraft,
            self.environment,
            speed,
            effective_alpha_deg,
            beta_deg,
            mach,
            reynolds,
            max(altitude, 0.0),
            omega,
            air["density_kgm3"],
            air["kinematic_viscosity_m2s"],
        )
        alpha_rad = deg2rad(effective_alpha_deg)
        cos_a, sin_a = math.cos(alpha_rad), math.sin(alpha_rad)
        force_body = np.array(
            [
                -result.drag_n * cos_a + result.lift_n * sin_a,
                result.side_n,
                -result.drag_n * sin_a - result.lift_n * cos_a,
            ],
            dtype=np.float64,
        )

        # Apparent / Added Mass Tensor:
        # When self.aircraft.added_mass_enabled is True:
        # thin plate added mass normal to the wing m_add_z = (pi / 4.0) * rho * span * chord^2.
        # The effective mass along normal acceleration is m_eff = m + m_add_z.
        # Adjust linear acceleration in body normal direction accordingly.
        span = max(self._derived["wingspan_m"], 1.0e-6)
        if bool(getattr(self.aircraft, "added_mass_enabled", False)):
            rho = max(air["density_kgm3"], 1.0e-6)
            m_add_z = (math.pi / 4.0) * rho * span * (self._chord ** 2)
            m_eff_z = self._mass + m_add_z
            f_z_eff = self._mass / m_eff_z
            # Accelerations in body frame before world rotation:
            gravity_body = world_to_body(quaternion, gravity_world)
            a_body = (force_body / self._mass) + gravity_body
            a_body[2] = (force_body[2] / m_eff_z) + gravity_body[2] * f_z_eff
            acceleration = body_to_world(quaternion, a_body)
        else:
            force_world = body_to_world(quaternion, force_body) + self._mass * gravity_world
            acceleration = force_world / self._mass

        moment_body = (
            np.array(
                [result.rolling_moment_nm, result.pitching_moment_nm, result.yawing_moment_nm],
                dtype=np.float64,
            )
            + self._restoring_moment(quaternion, omega, result.dynamic_pressure_pa, speed, effective_dihedral_deg=effective_dihedral_deg)
        )

        # Ground Pitch Coupling:
        # When near ground (altitude < span), if self.aircraft.ground_pitch_coupling > 0,
        # add nose-down ground pitching moment to moment_body.
        ground_pitch_gain = max(_finite(getattr(self.aircraft, "ground_pitch_coupling", 0.0)), 0.0)
        if ground_pitch_gain > 0.0 and altitude < span and altitude >= -1.0:
            h_eff = max(altitude, 0.01)
            # Factor grows as h -> 0: (1 - h/b)
            proximity = max(0.0, 1.0 - (h_eff / span))
            # Dynamic pressure * S * c * gain * proximity gives nose-down (negative) pitching moment
            area = max(self._derived["wing_area_m2"], 1.0e-9)
            m_ground_pitch = -ground_pitch_gain * proximity * q_dyn * area * self._chord
            moment_body[1] += m_ground_pitch

        angular_acceleration = angular_acceleration_body(self._inertia, omega, moment_body)
        specific_force_body = world_to_body(quaternion, acceleration - gravity_world)
        gravity_ref = abs(gravity)
        g_load = (
            abs(float(dot(specific_force_body, (0.0, 0.0, 1.0)))) / gravity_ref
            if gravity_ref > 1.0e-9
            else abs(float(specific_force_body[2]))
        )
        return {
            "position": position,
            "velocity": velocity,
            "quaternion": quaternion,
            "omega": omega,
            "acceleration": acceleration,
            "angular_acceleration": angular_acceleration,
            "force_body": force_body,
            "specific_force_body": specific_force_body,
            "moment_body": moment_body,
            "wind": wind,
            "speed": speed,
            "body_relative": body_relative,
            "alpha_deg": effective_alpha_deg,
            "beta_deg": beta_deg,
            "mach": mach,
            "reynolds": reynolds,
            "g_load": g_load,
            "altitude": altitude,
            "aero": result,
            "air": air,
            "aeroelastic_dihedral_deg": aero_dihedral_deg,
            "aeroelastic_twist_deg": aero_twist_deg,
        }

    def _derivative(self, y: np.ndarray, time_s: float, core: Optional[Dict[str, Any]] = None) -> np.ndarray:
        """Right-hand side of the 13-state ODE (position, velocity, quaternion, rate)."""
        if core is None:
            core = self._compute_dynamics(y, time_s)
        return np.concatenate(
            [
                core["velocity"],
                core["acceleration"],
                quaternion_derivative(core["quaternion"], core["omega"]),
                core["angular_acceleration"],
            ]
        )

    def _rk4(
        self, y0: np.ndarray, h: float, time_s: float, k1: Optional[np.ndarray] = None
    ) -> np.ndarray:
        """One classical RK4 sweep, renormalising the quaternion afterwards."""
        if k1 is None:
            k1 = self._derivative(y0, time_s)
        k2 = self._derivative(y0 + 0.5 * h * k1, time_s + 0.5 * h)
        k3 = self._derivative(y0 + 0.5 * h * k2, time_s + 0.5 * h)
        k4 = self._derivative(y0 + h * k3, time_s + h)
        y1 = y0 + (h / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)
        y1[6:10] = quaternion_normalize(y1[6:10])
        return y1

    # -- validation, ground contact, recording ---------------------------
    def _within_limits(self, state: StateVector) -> bool:
        """True when the state satisfies every hard physical guard rail."""
        if not _state_is_finite(state):
            return False
        if float(np.linalg.norm(vec3(state.velocity_mps))) > HARD_SPEED_LIMIT_MPS:
            return False
        if float(np.linalg.norm(vec3(state.angular_velocity_radps))) > HARD_ANGULAR_RATE_LIMIT_RPS:
            return False
        altitude = -float(state.position_m[2]) - _finite(self.environment.ground_level_m)
        return abs(altitude) <= HARD_ALTITUDE_LIMIT_M

    def _step_is_acceptable(self, previous: StateVector, candidate: StateVector) -> bool:
        """Local truncation-error guard applied to every integrated sub-step.

        A step is rejected when the body rate jumps by more than
        :data:`MAX_RATE_JUMP_RADPS` or the velocity by more than
        :data:`MAX_SPEED_JUMP_MPS`, both of which indicate that the explicit RK4
        step has left its stable region rather than a genuine fast manoeuvre.
        """
        rate_jump = float(
            np.linalg.norm(vec3(candidate.angular_velocity_radps) - vec3(previous.angular_velocity_radps))
        )
        if not math.isfinite(rate_jump) or rate_jump > MAX_RATE_JUMP_RADPS:
            return False
        speed_jump = float(np.linalg.norm(vec3(candidate.velocity_mps) - vec3(previous.velocity_mps)))
        if not math.isfinite(speed_jump) or speed_jump > MAX_SPEED_JUMP_MPS:
            return False
        norm = float(np.linalg.norm(np.asarray(candidate.quaternion, dtype=np.float64)))
        return math.isfinite(norm) and abs(norm - 1.0) <= 1.0e-6

    def _apply_ground_contact(self, state: StateVector) -> StateVector:
        """Resolve ground contact, latching the ``landed`` / ``crashed`` flags.

        The ground plane is ``z = -ground_level_m`` because +z points down.  The
        normal velocity is reversed with ``settings.restitution``, the tangential
        velocity and the body rates are damped by ``1/(1+ground_friction)`` (the
        sheet slides and crumples) and the impact is declared a structural
        failure when the impact speed exceeds :data:`CRASH_IMPACT_SPEED_MPS` or
        the impact wing stress reaches the allowable limit (index >= 1).

        Multi-point contact & crumple dynamics:
        Tracks positions of nose, left wingtip, right wingtip, and tail keel relative to ground.
        If a wingtip strikes first before the main body, introduces a ground cartwheel / roll-yaw torque damping.
        If nose strikes at high impact speed, resolves crumple deceleration.
        """
        if not bool(self.settings.collision_enabled) or state.landed or state.crashed:
            return state
        ground = _finite(self.environment.ground_level_m)
        altitude_cg = -float(state.position_m[2]) - ground

        # Multi-point contact tracking
        mac = max(self._derived.get("mac_m", 0.08), 1.0e-3)
        span = max(self._derived.get("wingspan_m", 0.20), 1.0e-3)
        semispan = span * 0.5

        # Key body stations relative to CG (x fwd, y right, z down)
        r_nose = np.array([0.5 * mac, 0.0, 0.0], dtype=np.float64)
        r_ltip = np.array([-0.3 * mac, -semispan, 0.0], dtype=np.float64)
        r_rtip = np.array([-0.3 * mac, semispan, 0.0], dtype=np.float64)
        r_tail = np.array([-0.7 * mac, 0.0, 0.015], dtype=np.float64)

        q = state.quaternion
        z_cg = float(state.position_m[2])
        z_nose = z_cg + float(body_to_world(q, r_nose)[2])
        z_ltip = z_cg + float(body_to_world(q, r_ltip)[2])
        z_rtip = z_cg + float(body_to_world(q, r_rtip)[2])
        z_tail = z_cg + float(body_to_world(q, r_tail)[2])

        # Altitudes (positive above ground, <= 0 means in contact)
        alt_nose = -z_nose - ground
        alt_ltip = -z_ltip - ground
        alt_rtip = -z_rtip - ground
        alt_tail = -z_tail - ground

        min_alt = min(altitude_cg, alt_nose, alt_ltip, alt_rtip, alt_tail)
        if min_alt > 0.0:
            return state

        x, y = float(state.position_m[0]), float(state.position_m[1])
        vx, vy, vz = (float(value) for value in state.velocity_mps)
        omega = [float(value) for value in state.angular_velocity_radps]
        damping = 1.0 / (1.0 + max(_finite(self.environment.ground_friction), 0.0))
        landed = False
        crashed = False
        new_vz = vz

        # Check wingtip strikes before main body contact (altitude_cg > 0 but wingtip below ground)
        if altitude_cg > 0.0 and (alt_ltip <= 0.0 or alt_rtip <= 0.0):
            # Asymmetric wingtip strike introduces ground cartwheel torque & damping
            omega[0] *= damping * 0.4
            omega[2] *= damping * 0.4
            if alt_ltip <= 0.0 and alt_rtip > 0.0:
                # Left tip caught ground: imparts positive roll (rightward tilt) and yaw torque
                omega[0] += 2.0 * damping
                omega[2] -= 1.5 * damping
            elif alt_rtip <= 0.0 and alt_ltip > 0.0:
                # Right tip caught ground: imparts negative roll (leftward tilt) and yaw torque
                omega[0] -= 2.0 * damping
                omega[2] += 1.5 * damping
            vx *= damping
            vy *= damping
            if vz > 0.0:
                new_vz = -clamp(_finite(self.settings.restitution, 0.0), 0.0, 0.5) * vz

        # Nose strike at high impact speed (crumple deceleration)
        if alt_nose <= 0.0 and vx > 5.0:
            # Paper nose crumple absorbs energy rapidly
            crumple_loss = 0.5 * damping
            vx *= crumple_loss
            vy *= damping
            omega[1] *= damping * 0.3

        if vz > 0.0:
            impact = vz
            air = atmosphere_state(self.environment, 0.0, self._chord)
            impact_stress = stress_metrics(
                self.aircraft,
                self.environment,
                self._derived,
                dynamic_pressure_pa=0.5 * air["density_kgm3"] * impact * impact,
                g_load=abs(impact) / max(abs(_finite(self.environment.gravity)), 1.0e-9),
                speed_mps=impact,
                density_kgm3=air["density_kgm3"],
            )
            self._bounce_count += 1
            new_vz = -clamp(_finite(self.settings.restitution, 0.0), 0.0, 1.0) * impact
            vx *= damping
            vy *= damping
            omega = [value * damping * 0.5 for value in omega]
            if impact > CRASH_IMPACT_SPEED_MPS or impact_stress["max_stress_index"] >= 1.0:
                crashed = True
            if abs(new_vz) < LANDING_SPEED_THRESHOLD_MPS or self._bounce_count >= MAX_BOUNCES:
                landed = True
        else:
            vx *= damping
            vy *= damping

        # If CG penetrates ground plane, clamp position
        new_z = float(state.position_m[2])
        if altitude_cg <= 0.0:
            new_z = -ground

        if landed or crashed:
            new_vz = 0.0
            vx *= 0.5
            vy *= 0.5
            omega = [0.0, 0.0, 0.0]

        return StateVector(
            time_s=state.time_s,
            position_m=(x, y, new_z),
            velocity_mps=(vx, vy, new_vz),
            quaternion=state.quaternion,
            angular_velocity_radps=(omega[0], omega[1], omega[2]),
            acceleration_mps2=(0.0, 0.0, 0.0),
            angular_accel_radps2=(0.0, 0.0, 0.0),
            landed=landed,
            crashed=crashed,
        )

    def _make_sample(self) -> FlightLogSample:
        """Build the log sample for the current state."""
        diag = self._diagnostics()
        state = self._state
        x, y, z = (float(value) for value in state.position_m)
        vx, vy, vz = (float(value) for value in state.velocity_mps)
        roll, pitch, yaw = euler_zyx_from_quaternion(state.quaternion)
        return FlightLogSample(
            time_s=_finite(state.time_s),
            x_m=x,
            y_m=y,
            z_m=z,
            altitude_m=max(-z - _finite(self.environment.ground_level_m), 0.0),
            speed_mps=diag["speed_mps"],
            vx_mps=vx,
            vy_mps=vy,
            vz_mps=vz,
            alpha_deg=diag["alpha_deg"],
            beta_deg=diag["beta_deg"],
            roll_deg=roll,
            pitch_deg=pitch,
            yaw_deg=yaw,
            ld_ratio=diag["ld_ratio"],
            g_load=diag["g_load"],
            max_stress_index=diag["max_stress_index"],
            max_stress_pa=diag["max_stress_pa"],
            reynolds=diag["reynolds"],
            mach=diag["mach"],
            dynamic_pressure_pa=diag["dynamic_pressure_pa"],
            stalled=bool(diag["stalled"]),
            landed=bool(state.landed),
            crashed=bool(state.crashed),
        )

    def _record_sample(self) -> None:
        """Append a sample once the record interval has elapsed."""
        if not self._record_enabled:
            return
        interval = max(_finite(self.settings.record_interval_s, 1.0e-2), 1.0e-9)
        if float(self._state.time_s) + 1.0e-12 < self._next_record_time:
            return
        self._history.append(self._make_sample())
        self._next_record_time = float(self._state.time_s) + interval

    # -- diagnostics ------------------------------------------------------
    def _diagnostics(self) -> Dict[str, Any]:
        """Full telemetry packet for the current state (memoised per step)."""
        if self._diagnostics_cache is not None:
            return self._diagnostics_cache
        state = self._state
        core = self._compute_dynamics(_pack(state), float(state.time_s))
        result = core["aero"]
        quaternion = core["quaternion"]
        roll, pitch, yaw = euler_zyx_from_quaternion(quaternion)
        matrix_angles = matrix_to_euler(quaternion_to_matrix(quaternion))
        attitude_error = max(
            abs(matrix_angles[0] - roll), abs(matrix_angles[1] - pitch), abs(matrix_angles[2] - yaw)
        )
        product = quaternion_multiply(quaternion, quaternion_conjugate(quaternion))
        quaternion_norm = math.sqrt(max(float(product[0]), 0.0))
        rate_error = float(
            np.linalg.norm(
                quaternion_to_body_angular_rate(quaternion, quaternion_derivative(quaternion, core["omega"]))
                - core["omega"]
            )
        )
        stress = stress_metrics(
            self.aircraft,
            self.environment,
            self._derived,
            dynamic_pressure_pa=result.dynamic_pressure_pa,
            g_load=core["g_load"],
            speed_mps=core["speed"],
            alpha_deg=core["alpha_deg"],
            beta_deg=core["beta_deg"],
            mach=core["mach"],
            reynolds=core["reynolds"],
            altitude_m=core["altitude"],
            ground_height_m=max(core["altitude"], 0.0),
            angular_rate=core["omega"],
            density_kgm3=core["air"]["density_kgm3"],
        )
        # Flow-path rates: the rotation of the air-relative velocity vector seen
        # from the body frame.  ``cross(v_hat, a)/V`` yields (-beta_dot, alpha_dot,
        # 0) up to the frame convention; they are reported for the UI overlays.
        alpha_rate = 0.0
        beta_rate = 0.0
        if core["speed"] > SPEED_STAT_FLOOR_MPS:
            path_rates = cross(
                normalize(core["body_relative"]), world_to_body(quaternion, core["acceleration"])
            ) / core["speed"]
            alpha_rate = rad2deg(float(path_rates[1]))
            beta_rate = rad2deg(-float(path_rates[0]))
        packet: Dict[str, Any] = {
            "time_s": _finite(state.time_s),
            "position_m": [float(value) for value in state.position_m],
            "velocity_mps": [float(value) for value in state.velocity_mps],
            "quaternion": [float(value) for value in state.quaternion],
            "angular_velocity_radps": [float(value) for value in state.angular_velocity_radps],
            "acceleration_mps2": [float(value) for value in core["acceleration"]],
            "angular_accel_radps2": [float(value) for value in core["angular_acceleration"]],
            "roll_deg": roll,
            "pitch_deg": pitch,
            "yaw_deg": yaw,
            "alpha_deg": core["alpha_deg"],
            "beta_deg": core["beta_deg"],
            "alpha_rate_degps": alpha_rate,
            "beta_rate_degps": beta_rate,
            "speed_mps": core["speed"],
            "ground_speed_mps": float(np.linalg.norm(core["velocity"])),
            "altitude_m": core["altitude"],
            "dynamic_pressure_pa": result.dynamic_pressure_pa,
            "reynolds": core["reynolds"],
            "mach": core["mach"],
            "ld_ratio": result.ld_ratio,
            "g_load": core["g_load"],
            "max_stress_index": clamp(stress["max_stress_index"], 0.0, 1.0),
            "max_stress_pa": stress["max_stress_pa"],
            "max_stress_index_raw": stress["max_stress_index"],
            "root_moment_nm": stress["root_moment_nm"],
            "stalled": bool(result.stalled),
            "cl": result.cl,
            "cd": result.cd,
            "cm": result.cm,
            "cy": result.cy,
            "lift_n": result.lift_n,
            "drag_n": result.drag_n,
            "side_n": result.side_n,
            "pitching_moment_nm": result.pitching_moment_nm,
            "wind_velocity_mps": [float(value) for value in core["wind"]],
            "stall_margin": result.stall_margin,
            "air_density_kgm3": core["air"]["density_kgm3"],
            "stress_backend": stress["backend"],
            "attitude_consistency_deg": attitude_error,
            "quaternion_norm": quaternion_norm,
            "quaternion_rate_error_radps": rate_error,
            "landed": bool(state.landed),
            "crashed": bool(state.crashed),
            "aeroelastic_dihedral_deg": core.get("aeroelastic_dihedral_deg", 0.0),
            "aeroelastic_twist_deg": core.get("aeroelastic_twist_deg", 0.0),
        }
        self._diagnostics_cache = packet
        return packet

    def _build_packet(self, diag: Dict[str, Any], dt: float, substeps: int) -> Dict[str, Any]:
        """Assemble the :meth:`step` return payload from a diagnostics packet."""
        return {
            "state": as_dict(self._state),
            "dt": float(dt),
            "substeps": int(substeps),
            "time_s": diag["time_s"],
            "position_m": diag["position_m"],
            "velocity_mps": diag["velocity_mps"],
            "quaternion": diag["quaternion"],
            "angular_velocity_radps": diag["angular_velocity_radps"],
            "acceleration_mps2": diag["acceleration_mps2"],
            "angular_accel_radps2": diag["angular_accel_radps2"],
            "roll_deg": diag["roll_deg"],
            "pitch_deg": diag["pitch_deg"],
            "yaw_deg": diag["yaw_deg"],
            "alpha_deg": diag["alpha_deg"],
            "beta_deg": diag["beta_deg"],
            "speed_mps": diag["speed_mps"],
            "ground_speed_mps": diag["ground_speed_mps"],
            "altitude_m": diag["altitude_m"],
            "dynamic_pressure_pa": diag["dynamic_pressure_pa"],
            "reynolds": diag["reynolds"],
            "mach": diag["mach"],
            "ld_ratio": diag["ld_ratio"],
            "g_load": diag["g_load"],
            "max_stress_index": diag["max_stress_index"],
            "max_stress_pa": diag["max_stress_pa"],
            "max_stress_index_raw": diag["max_stress_index_raw"],
            "stalled": diag["stalled"],
            "landed": diag["landed"],
            "crashed": diag["crashed"],
            "diverged": bool(self._diverged),
            "rolled_back": bool(self._rolled_back),
            "backend": BACKEND,
            "status": self._status(),
            "frames": int(len(self._history)),
            "derived_timestep": self.derived_timestep(),
            "cl": diag["cl"],
            "cd": diag["cd"],
            "cm": diag["cm"],
            "cy": diag["cy"],
            "lift_n": diag["lift_n"],
            "drag_n": diag["drag_n"],
            "side_n": diag["side_n"],
            "pitching_moment_nm": diag["pitching_moment_nm"],
            "wind_velocity_mps": diag["wind_velocity_mps"],
            "stall_margin": diag["stall_margin"],
            "air_density_kgm3": diag["air_density_kgm3"],
            "stress_backend": diag["stress_backend"],
            "quaternion_norm": diag["quaternion_norm"],
            "attitude_consistency_deg": diag["attitude_consistency_deg"],
            "quaternion_rate_error_radps": diag["quaternion_rate_error_radps"],
            "aeroelastic_dihedral_deg": diag.get("aeroelastic_dihedral_deg", 0.0),
            "aeroelastic_twist_deg": diag.get("aeroelastic_twist_deg", 0.0),
        }

    def _status(self) -> str:
        """Terminal status of the current state."""
        if self._diverged:
            return "diverged"
        if self._state.crashed:
            return "crashed"
        if self._state.landed:
            return "landed"
        return "flying"

    # -- integration ------------------------------------------------------
    def _advance(self, h: float, y0: np.ndarray, time_s: float, k1: Optional[np.ndarray] = None) -> bool:
        """Advance one sub-step; roll back and halve on divergence.

        Returns ``True`` when the state was advanced.  A rejected step (non-finite
        state, speed / altitude / body-rate cap violation or a rate jump beyond
        the truncation-error bound) restores the previous state, halves the step
        and retries, bounded by :data:`DIVERGENCE_RETRIES`.  When the retries are
        exhausted the previous state is restored, ``diverged`` is latched and the
        airframe is frozen as a crash.
        """
        previous = self._state
        step = h
        for attempt in range(DIVERGENCE_RETRIES + 1):
            candidate = _unpack(self._rk4(y0, step, time_s, k1), time_s + step)
            if self._within_limits(candidate) and self._step_is_acceptable(previous, candidate):
                candidate.time_s = time_s + step
                self._state = candidate
                self._diagnostics_cache = None
                if attempt > 0:
                    self._rolled_back = True
                return True
            step *= 0.5
        self._diagnostics_cache = None
        self._diverged = True
        self._rolled_back = True
        self._state = replace(previous, crashed=True, landed=False)
        return False

    def step(self, dt: Optional[float] = None, substeps: Optional[int] = None) -> Dict[str, Any]:
        """Advance the flight by ``dt`` seconds of simulated time.

        The interval is split into at most ``settings.max_substeps`` adaptive RK4
        sub-steps (``substeps`` overrides that budget) and the number actually
        used is reported.  A frozen airframe (landed / crashed) returns the
        current telemetry unchanged with ``dt = 0``.
        """
        if self._state.landed or self._state.crashed:
            return self._build_packet(self._diagnostics(), 0.0, 0)
        total = max(_finite(self.settings.dt_max, 2.0e-2), 0.0) if dt is None else max(_finite(dt), 0.0)
        if total <= 0.0:
            return self._build_packet(self._diagnostics(), 0.0, 0)
        if bool(getattr(self.environment, "wind_tunnel_enabled", False)):
            alpha = float(getattr(self.environment, "wind_tunnel_alpha_deg", 4.0))
            beta = float(getattr(self.environment, "wind_tunnel_beta_deg", 0.0))
            pitch_rad = math.radians(alpha)
            yaw_rad = math.radians(-beta)
            q = quaternion_from_euler(0.0, pitch_rad, yaw_rad)
            self._state = replace(
                self._state,
                position_m=(0.0, 0.0, -1.2),
                velocity_mps=(0.0, 0.0, 0.0),
                quaternion=(float(q[0]), float(q[1]), float(q[2]), float(q[3])),
                angular_velocity_radps=(0.0, 0.0, 0.0),
                acceleration_mps2=(0.0, 0.0, 0.0),
                angular_accel_radps2=(0.0, 0.0, 0.0),
                landed=False,
                crashed=False,
                time_s=float(self._state.time_s) + total,
            )
            self._diagnostics_cache = None
            self._record_sample()
            return self._build_packet(self._diagnostics(), total, 1)
        budget = max(1, int(self.settings.max_substeps) if substeps is None else int(substeps))
        travelled = 0.0
        used = 0
        while travelled < total - 1.0e-12 and used < budget:
            y0 = _pack(self._state)
            time_s = float(self._state.time_s)
            core = self._compute_dynamics(y0, time_s)
            h = min(self._timestep_from(core, y0, time_s), total - travelled)
            if not self._advance(h, y0, time_s, self._derivative(y0, time_s, core)):
                break
            travelled += h
            used += 1
            self._state = self._apply_ground_contact(self._state)
            self._diagnostics_cache = None
            self._record_sample()
            if self._state.landed or self._state.crashed:
                break
        return self._build_packet(self._diagnostics(), travelled, used)

    # -- history ----------------------------------------------------------
    def history(self) -> List[FlightLogSample]:
        """Recorded samples, **oldest first** (a copy of the ring buffer)."""
        return list(self._history)

    def metrics(self) -> Dict[str, Any]:
        """Summary statistics over the recorded history."""
        samples = list(self._history)
        diag = self._diagnostics()
        stall_alpha = abs(_finite(self.aircraft.stall_alpha_deg, 14.0))
        distance = 0.0
        max_altitude = 0.0
        apogee_time = 0.0
        max_ld = 0.0
        max_stress_index = 0.0
        max_stress_pa = 0.0
        max_g = 0.0
        max_speed = 0.0
        stall_margin = 0.0
        saw_margin = False
        stalled_any = False
        previous: Optional[FlightLogSample] = None
        for sample in samples:
            if previous is not None:
                distance += math.hypot(sample.x_m - previous.x_m, sample.y_m - previous.y_m)
            previous = sample
            if sample.altitude_m > max_altitude:
                max_altitude = sample.altitude_m
                apogee_time = sample.time_s
            if sample.speed_mps >= SPEED_STAT_FLOOR_MPS:
                max_ld = max(max_ld, abs(sample.ld_ratio))
            max_stress_index = max(max_stress_index, _finite(sample.max_stress_index))
            max_stress_pa = max(max_stress_pa, _finite(sample.max_stress_pa))
            max_g = max(max_g, abs(sample.g_load))
            max_speed = max(max_speed, sample.speed_mps)
            stalled_any = stalled_any or bool(sample.stalled)
            margin = (
                (stall_alpha - _finite(sample.alpha_deg)) / stall_alpha if stall_alpha > 1.0e-6 else 0.0
            )
            stall_margin = min(stall_margin, margin) if saw_margin else margin
            saw_margin = True
        final = samples[-1] if samples else None
        launch_x = float(samples[0].x_m) if samples else 0.0
        launch_y = float(samples[0].y_m) if samples else 0.0
        final_x = float(final.x_m) if final else 0.0
        final_y = float(final.y_m) if final else 0.0
        return {
            "frames": int(len(samples)),
            "points": int(len(samples)),
            "flight_time_s": max(_finite(self._state.time_s), 0.0),
            "distance_m": distance,
            "range_x_m": abs(final_x - launch_x),
            "range_y_m": abs(final_y - launch_y),
            "horizontal_range_m": math.hypot(final_x - launch_x, final_y - launch_y),
            "max_altitude_m": max_altitude,
            "apogee_time_s": apogee_time,
            "max_speed_mps": max_speed,
            "final_speed_mps": diag["speed_mps"],
            "final_altitude_m": diag["altitude_m"],
            "max_g_load": max_g,
            "max_ld_ratio": max_ld,
            "max_stress_index": max_stress_index,
            "max_stress_pa": max_stress_pa,
            "stall_margin": stall_margin,
            "stalled": bool(stalled_any),
            "landed": bool(self._state.landed),
            "crashed": bool(self._state.crashed),
            "diverged": bool(self._diverged),
            "status": self._status(),
            "launch_speed_mps": (
                math.hypot(samples[0].vx_mps, samples[0].vy_mps, samples[0].vz_mps) if samples else 0.0
            ),
            "backend": BACKEND,
            "backends": active_backends(),
            "final_state": as_dict(self._state),
        }

    # -- batch integration ------------------------------------------------
    def simulate(self, duration_s: Optional[float] = None, record: bool = True) -> Dict[str, Any]:
        """Integrate ``duration_s`` seconds and return the summary metrics.

        The loop is bounded both by the sub-step budget of :meth:`step` and by an
        overall call budget; exhausting the latter yields ``status == "timeout"``
        so a pathological configuration can never hang the optimiser.
        """
        total = max(_finite(self.settings.duration_s, 12.0), 0.0) if duration_s is None else max(_finite(duration_s), 0.0)
        previous_record = self._record_enabled
        self._record_enabled = bool(record)
        self._diverged = False
        self._rolled_back = False
        dt_max = max(_finite(self.settings.dt_max, 2.0e-2), 1.0e-6)
        chunk = dt_max * max(1, int(self.settings.max_substeps))
        call_budget = 8 * int(total / dt_max) + 128
        calls = 0
        timed_out = False
        try:
            while float(self._state.time_s) < total - 1.0e-12:
                if self._state.landed or self._state.crashed or self._diverged:
                    break
                if calls >= call_budget:
                    timed_out = True
                    break
                remaining = total - float(self._state.time_s)
                self.step(dt=min(chunk, remaining))
                calls += 1
        finally:
            self._record_enabled = previous_record
        summary = self.metrics()
        summary["steps"] = int(calls)
        summary["duration_requested_s"] = total
        if timed_out and summary["status"] == "flying":
            summary["status"] = "timeout"
        self._diagnostics_cache = None
        return summary

    def __repr__(self) -> str:
        return (
            f"FlightSimulator(aircraft={self.aircraft.name!r}, "
            f"t={float(self._state.time_s):.4f}s, frames={len(self._history)}, "
            f"status={self._status()!r})"
        )


# --------------------------------------------------------------------------
# Optimiser entry point
# --------------------------------------------------------------------------
def _trim_residual_cm(
    aircraft: AircraftConfig,
    environment: EnvironmentConfig,
    alpha_deg: float,
    speed_mps: float,
    altitude_m: float,
) -> float:
    """Pitching-moment coefficient at the best-glide point of a trajectory.

    ``|C_m|`` is the static trim residual: zero when the airframe is exactly in
    trim, growing as the unbalanced pitching moment grows.
    """
    chord = max(derived_properties(aircraft)["mac_m"], 1.0e-4)
    air = atmosphere_state(environment, altitude_m, chord)
    reynolds = safe_div(
        max(_finite(speed_mps), 0.0) * chord, max(air["kinematic_viscosity_m2s"], 1.0e-12), 0.0
    )
    result = aero_forces(
        aircraft,
        environment,
        speed_mps,
        alpha_deg,
        0.0,
        safe_div(speed_mps, max(air["sound_speed_mps"], 1.0), 0.0),
        reynolds,
        max(altitude_m, 0.0),
        (0.0, 0.0, 0.0),
        air["density_kgm3"],
        air["kinematic_viscosity_m2s"],
    )
    return _finite(result.cm)


def evaluate_flight(
    aircraft_dict: Dict[str, Any],
    environment_dict: Dict[str, Any],
    launch_dict: Dict[str, Any],
    duration_s: float = 6.0,
    dt_max: float = 2.0e-2,
) -> Dict[str, Any]:
    """Evaluate one flight and return the fixed optimiser payload.

    The configuration arguments are plain dictionaries (the payload is pickled
    into a ``multiprocessing`` pool); the dataclasses are accepted too.  Metric
    definitions:

    ``distance_m``
        Accumulated **horizontal path length** ``sum hypot(dx, dy)`` over the
        recorded samples, i.e. the 2-D ground track the airframe actually
        traced, not the straight line to the touchdown point.
    ``landing_distance_m``
        Straight-line horizontal range ``hypot(x_end - x_0, y_end - y_0)`` from
        the launch point to the final recorded position.
    ``max_altitude_m``
        Maximum of ``-z - ground_level_m`` over the recorded samples.
    ``max_ld_ratio``
        Maximum ``|L/D|`` over the samples whose airspeed is at least
        :data:`SPEED_STAT_FLOOR_MPS` (1 m/s), so the 0/0 samples of a stalled or
        hovering airframe cannot dominate the statistic.
    ``max_stress_index`` / ``max_stress_pa``
        Maximum normalised wing-root bending stress (clamped to 0..1 for the
        contract) and its dimensional peak in pascals over the samples.
    ``launch_speed_mps``
        Initial speed magnitude of the launch condition.
    ``stall_margin``
        Minimum over the samples of ``(stall_alpha_deg - alpha)/stall_alpha_deg``:
        positive before the stall, negative once the airframe is stalled.
    ``stability``
        ``1/(1+|C_m|)`` evaluated at the **trim point**, defined as the recorded
        sample with the largest L/D (the best-glide attitude): 1.0 is a perfectly
        trimmed airframe and the value decays towards 0 as the residual moment
        grows.
    ``apogee_time_s``
        Simulated time of the maximum-altitude sample.
    """
    total = max(_finite(duration_s, 6.0), 0.0)
    step_max = clamp(_finite(dt_max, 2.0e-2), 1.0e-6, 1.0e1)
    empty = {
        "distance_m": 0.0,
        "flight_time_s": 0.0,
        "max_altitude_m": 0.0,
        "max_ld_ratio": 0.0,
        "max_stress_index": 0.0,
        "max_stress_pa": 0.0,
        "launch_speed_mps": 0.0,
        "stall_margin": 0.0,
        "stability": 0.0,
        "landing_distance_m": 0.0,
        "points": 0,
        "duration_s": float(total),
        "backend": BACKEND,
        "status": "diverged",
        "apogee_time_s": 0.0,
    }
    try:
        aircraft = (
            aircraft_dict
            if isinstance(aircraft_dict, AircraftConfig)
            else _config_from(AircraftConfig, aircraft_dict)
        )
        environment = (
            environment_dict
            if isinstance(environment_dict, EnvironmentConfig)
            else _config_from(EnvironmentConfig, environment_dict)
        )
        launch = _config_from(LaunchConfig, launch_dict)
        settings = SimulationSettings(
            dt_max=step_max,
            dt_min=min(1.0e-5, step_max),
            max_substeps=64,
            duration_s=total,
            record_interval_s=step_max,
            time_scale=1.0,
            adaptive=True,
            collision_enabled=True,
            restitution=0.15,
            ring_buffer_capacity=max(1024, int(total / step_max) + 64),
        )
        simulator = FlightSimulator(aircraft, environment, launch, settings)
        summary = simulator.simulate(duration_s=total, record=True)
        samples = simulator.history()
    except Exception:
        return empty
    stall_alpha = abs(_finite(aircraft.stall_alpha_deg, 14.0))
    distance = 0.0
    max_altitude = 0.0
    apogee_time = 0.0
    max_ld = 0.0
    max_stress_index = 0.0
    max_stress_pa = 0.0
    stall_margin = 0.0
    saw_margin = False
    best: Optional[FlightLogSample] = None
    previous: Optional[FlightLogSample] = None
    for sample in samples:
        if previous is not None:
            distance += math.hypot(sample.x_m - previous.x_m, sample.y_m - previous.y_m)
        previous = sample
        if sample.altitude_m > max_altitude:
            max_altitude = sample.altitude_m
            apogee_time = sample.time_s
        if sample.speed_mps >= SPEED_STAT_FLOOR_MPS and abs(sample.ld_ratio) > max_ld:
            max_ld = abs(sample.ld_ratio)
            best = sample
        max_stress_index = max(max_stress_index, _finite(sample.max_stress_index))
        max_stress_pa = max(max_stress_pa, _finite(sample.max_stress_pa))
        if stall_alpha > 1.0e-6:
            margin = (stall_alpha - _finite(sample.alpha_deg)) / stall_alpha
            stall_margin = min(stall_margin, margin) if saw_margin else margin
            saw_margin = True
    if best is None and samples:
        best = samples[-1]
    launch_speed = math.hypot(launch.launch_speed, 0.0)
    if samples:
        launch_speed = math.hypot(samples[0].vx_mps, samples[0].vy_mps, samples[0].vz_mps)
    residual = 0.0
    if best is not None:
        residual = abs(
            _trim_residual_cm(aircraft, environment, best.alpha_deg, best.speed_mps, best.altitude_m)
        )
    status = str(summary.get("status", "flying"))
    if summary.get("diverged"):
        status = "diverged"
    elif status not in STATUS_VALUES:
        status = "flying"
    payload = {
        "distance_m": max(_finite(distance), 0.0),
        "flight_time_s": max(_finite(summary.get("flight_time_s", 0.0)), 0.0),
        "max_altitude_m": max(_finite(max_altitude), 0.0),
        "max_ld_ratio": max(_finite(max_ld), 0.0),
        "max_stress_index": clamp(_finite(max_stress_index), 0.0, 1.0),
        "max_stress_pa": max(_finite(max_stress_pa), 0.0),
        "launch_speed_mps": max(_finite(launch_speed), 0.0),
        "stall_margin": _finite(stall_margin, 0.0),
        "stability": clamp(safe_div(1.0, 1.0 + residual, 0.0), 0.0, 1.0),
        "landing_distance_m": max(_finite(summary.get("horizontal_range_m", 0.0)), 0.0),
        "points": int(summary.get("points", 0)),
        "duration_s": float(total),
        "backend": BACKEND,
        "status": status,
        "apogee_time_s": max(_finite(apogee_time), 0.0),
    }
    return {key: payload[key] for key in EVALUATE_KEYS}


def flight_from_aircraft(
    aircraft: AircraftConfig,
    environment: Optional[EnvironmentConfig] = None,
    launch: Optional[LaunchConfig] = None,
    duration_s: float = 6.0,
) -> Dict[str, Any]:
    """Convenience wrapper evaluating a flight straight from the dataclasses."""
    return evaluate_flight(
        as_dict(aircraft),
        as_dict(environment) if environment is not None else as_dict(EnvironmentConfig()),
        as_dict(launch) if launch is not None else as_dict(LaunchConfig()),
        duration_s=duration_s,
    )
