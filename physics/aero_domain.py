"""Aerodynamics domain: wing section, induced drag, stall, trim and forces.

Scope
-----
Every public entry point is a **pure function** of the runtime-mutable
:class:`~physics.config_schema.AircraftConfig` /
:class:`~physics.config_schema.EnvironmentConfig` pair.  Nothing here caches
state, so a UI slider that reaches the server through
``POST /api/v1/environment/atmosphere`` influences the very next call -- the
property pinned by ``tests/test_aero.py::test_drag_scale_doubles_drag_force_exactly``.

Coordinate convention (inherited from ``config_schema``)
    x forward (nose), y right wing, z **down**.  Lift acts along ``-z``;
    coefficients are referred to the mean aerodynamic chord ``c`` (pitch) and
    the span ``b`` (roll / yaw).

Model chain
-----------
1.  Lift slope from Helmbold triangular-wing theory
    ``C_Lalpha = 2 pi AR / (2 + sqrt(AR^2 (1 + lambda) + 4 AR / pi))`` unless
    ``cfg.cLalpha_per_rad > 0`` overrides it.
2.  Linear branch ``C_L = cL0 + C_Lalpha alpha`` blended into the post-stall
    branch ``C_Lmax + post_stall_slope (alpha - alpha_stall)`` by a smooth
    logistic of sharpness ``cfg.stall_sharpness`` centred on
    ``cfg.stall_alpha_deg`` (see :func:`stall_blend_weight`).
3.  Prandtl-Glauert compressibility on the slope plus a wave-drag increment
    once the flow approaches transonic.
4.  Parasite drag ``C_D0(Re) = cd0 (1e5 / Re) ** re_cd0_exponent`` plus induced
    drag ``C_L^2 / (pi e AR)``, the latter attenuated by the ground-effect
    factor ``16 (h/b)^2 / (1 + 16 (h/b)^2)``.
5.  Elevon deflection adds lift and a nose-down pitching increment, hence
    shifts the trimmed angle of attack.
6.  Static / dynamic stability derivatives assembled into ``C_m``, ``C_l`` and
    ``C_n``, non-dimensionalised with ``b / (2V)`` and ``c / (2V)``.
7.  Forces ``L = q S C_L``, ``D = q S C_D``, ``Y = q S C_y`` and moments
    ``M = q S c C_m``, ``l = q S b C_l``, ``n = q S b C_n`` with
    ``q = 0.5 rho V^2`` and reference quantities from
    :func:`~physics.config_schema.derived_properties`.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .config_schema import (
    GAMMA_AIR,
    MU0_ISA,
    MU_SUTHERLAND,
    R_AIR,
    R_VAPOR,
    T0_ISA,
    AircraftConfig,
    AeroResult,
    AeroState,
    EnvironmentConfig,
    as_dict,
    clamp,
    deg2rad,
    derived_properties,
    rad2deg,
    safe_div,
)
from .geometry_utils import cross, normalize, vec3

__all__ = [
    "helmbold_cl_alpha",
    "prandtl_glauert",
    "ground_effect_factor",
    "parasite_drag_coefficient",
    "wave_drag_coefficient",
    "lift_slope_per_rad",
    "lift_coefficient",
    "drag_coefficient",
    "stall_blend_weight",
    "elevon_effects",
    "elevon_authority",
    "trim_alpha_from_elevon",
    "elevon_for_trim",
    "drag_polar",
    "drag_polar_minima",
    "trim_solution",
    "circulation",
    "aerodynamic_force_body",
    "pitching_moment_offset_nm",
    "atmosphere_state",
    "evaluate_aero",
    "evaluate_aero_state",
    "aero_result_to_dict",
    "ELEVON_LIFT_PER_DEG",
    "ELEVON_MOMENT_PER_DEG",
    "ELEVON_MAX_DEG",
    "MACH_TRANSONIC",
    "MACH_CRITICAL",
    "RE_REFERENCE",
    "AERO_BACKEND",
]

#: Echoed in every payload so the UI never has to guess how the numbers were
#: produced.  This domain is scalar pure Python maths, hence ``numpy``.
AERO_BACKEND: str = "numpy"

#: Reference Reynolds number of the ``cd0`` correlation.
RE_REFERENCE: float = 1.0e5

#: Exponent of the logistic stall blend.  The plain logistic
#: ``1 / (1 + exp(-k d))`` is numerically indistinguishable from the linear
#: branch only about 15 deg below the stall, which is not enough to reproduce
#: the attached-flow relation to machine precision.  Raising the logistic to a
#: small integer power keeps the blend smooth (C-infinity) and centred on the
#: stall angle while making the weight decay like ``exp(-order k |d|)``: below
#: the stall the lift curve *is* the linear branch (checked to ~1e-29 relative
#: in ``tests/test_aero.py``).
STALL_BLEND_ORDER: int = 4

#: Elevon effectiveness.  ``dcl`` grows with a trailing-edge-down (positive)
#: deflection, ``dcm`` is the resulting **nose-down** pitching increment.
ELEVON_LIFT_PER_DEG: float = 0.020
ELEVON_MOMENT_PER_DEG: float = 0.030
#: Mechanical travel limit applied when a trim solution would need an
#: impossible elevon angle; a clamped solution reports ``trimmed = False``.
ELEVON_MAX_DEG: float = 180.0

#: Mach number at which the transonic wave-drag increment switches on.
MACH_TRANSONIC: float = 0.75
#: Mach number at which the Prandtl-Glauert factor stops rising.  Above it the
#: subsonic theory is meaningless, so the factor is held (never NaN).
MACH_CRITICAL: float = 0.85

_WAVE_DRAG_GAIN: float = 0.25
_SUPERSONIC_DRAG_GAIN: float = 0.15
_CD_FLOOR: float = 1.0e-12
_TOLERANCE_CM: float = 1.0e-6

try:  # the atmosphere worker owns that module; the fallback below is functional
    from . import atmosphere_domain as _ATMOSPHERE
except Exception:  # pragma: no cover - exercised only if the sibling is broken
    _ATMOSPHERE = None


# --------------------------------------------------------------------------
# Numeric guards
# --------------------------------------------------------------------------
def _finite(value: Any, default: float = 0.0) -> float:
    """``float(value)`` with a deterministic fallback for NaN / inf."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    return out if math.isfinite(out) else float(default)


def _oswald(cfg: AircraftConfig) -> float:
    return clamp(_finite(cfg.oswald_e, 0.75), 0.05, 1.0)


def _geom(cfg: AircraftConfig) -> Dict[str, float]:
    return derived_properties(cfg)


def _span(cfg: AircraftConfig) -> float:
    return max(_finite(_geom(cfg)["ref_span_m"], 0.2), 1.0e-6)


def _chord(cfg: AircraftConfig) -> float:
    return max(_finite(_geom(cfg)["ref_length_m"], 0.075), 1.0e-9)


def _area(cfg: AircraftConfig) -> float:
    return max(_finite(_geom(cfg)["ref_area_m2"], 0.015), 1.0e-9)


def _polar_constant(cfg: AircraftConfig) -> float:
    """``k = pi e AR`` -- the divisor of the induced term ``C_L^2 / k``."""
    aspect = max(_finite(_geom(cfg)["aspect_ratio"], 1.0), 1.0e-3)
    return math.pi * _oswald(cfg) * aspect


def _logistic(value: float) -> float:
    """Overflow-free logistic function ``1 / (1 + exp(-value))``."""
    x = _finite(value)
    if x >= 0.0:
        return 1.0 / (1.0 + math.exp(-min(x, 700.0)))
    exponent = math.exp(max(x, -700.0))
    return exponent / (1.0 + exponent)


# --------------------------------------------------------------------------
# 1. Lift-curve slope
# --------------------------------------------------------------------------
def helmbold_cl_alpha(aspect_ratio: float, taper_ratio: float) -> float:
    """Helmbold triangular-wing lift-curve slope ``dC_L / dalpha`` per radian.

    ``C_Lalpha = 2 pi AR / (2 + sqrt(AR^2 (1 + lambda) + 4 AR / pi))``
    """
    aspect = max(_finite(aspect_ratio, 1.0), 1.0e-3)
    taper = clamp(_finite(taper_ratio, 0.7), 0.01, 1.0)
    denominator = 2.0 + math.sqrt(aspect * aspect * (1.0 + taper) + 4.0 * aspect / math.pi)
    return 2.0 * math.pi * aspect / denominator


def prandtl_glauert(mach: float) -> float:
    """Prandtl-Glauert compressibility factor ``1 / sqrt(1 - M^2)``.

    Exactly ``1.0`` at ``M = 0``, monotonically increasing up to
    :data:`MACH_CRITICAL` and **held** at the critical value above it (the
    subsonic formula would be NaN/inf for ``M >= 1``).  A non-finite Mach
    number is treated as ``M = 0``; a negative one uses its magnitude.
    """
    try:
        value = float(mach)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(value):
        return prandtl_glauert(MACH_CRITICAL if value > 0.0 else 0.0)
    value = abs(value)
    if value <= 0.0:
        return 1.0
    effective = min(value, MACH_CRITICAL)
    return 1.0 / math.sqrt(max(1.0 - effective * effective, 1.0e-12))


def lift_slope_per_rad(cfg: AircraftConfig, compressible: bool = True, mach: float = 0.0) -> float:
    """Effective ``dC_L / dalpha`` in ``1/rad``, compressibility included."""
    geom = _geom(cfg)
    if _finite(cfg.cLalpha_per_rad) > 0.0:
        slope = float(cfg.cLalpha_per_rad)
    else:
        slope = helmbold_cl_alpha(geom["aspect_ratio"], geom["taper_ratio"])
    return slope * prandtl_glauert(mach) if compressible else slope


# --------------------------------------------------------------------------
# 2. Ground effect, parasite drag, wave drag
# --------------------------------------------------------------------------
def ground_effect_factor(height_m: float, span_m: float, enabled: bool = True) -> float:
    """Induced-drag retention ``16 (h/b)^2 / (1 + 16 (h/b)^2)``.

    ``0.0`` when ``enabled`` is false (the caller then applies no ground-effect
    model at all), strictly inside ``(0, 1)`` for finite positive height and
    span, and exactly ``1.0`` once ``h / b`` exceeds ~2.4e7.
    """
    if not bool(enabled):
        return 0.0
    height = max(_finite(height_m), 0.0)
    span = max(_finite(span_m, 0.2), 1.0e-9)
    ratio = 16.0 * (height / span) ** 2
    if not math.isfinite(ratio):
        return 1.0
    return ratio / (1.0 + ratio)


def parasite_drag_coefficient(cfg: AircraftConfig, reynolds: float) -> float:
    """Reynolds-corrected parasite drag ``C_D0 = cd0 (1e5 / Re) ** exponent``."""
    value = max(abs(_finite(reynolds, RE_REFERENCE)), 1.0)
    return _finite(cfg.cd0, 0.02) * (RE_REFERENCE / value) ** _finite(cfg.re_cd0_exponent, 0.0)


def wave_drag_coefficient(mach: float, compressible: bool = True) -> float:
    """Transonic wave-drag increment (exactly ``0.0`` below :data:`MACH_TRANSONIC`).

    Monotone and finite at any Mach number; the supersonic term keeps the
    empirical drag-divergence trend growing instead of collapsing like
    ``1 - M^2`` would.
    """
    if not bool(compressible):
        return 0.0
    value = abs(_finite(mach))
    if value <= MACH_TRANSONIC:
        return 0.0
    excess = value - MACH_TRANSONIC
    supersonic = max(value - 1.0, 0.0)
    return _WAVE_DRAG_GAIN * excess * excess + _SUPERSONIC_DRAG_GAIN * supersonic ** 3


# --------------------------------------------------------------------------
# 3. Lift coefficient and the stall blend
# --------------------------------------------------------------------------
def stall_blend_weight(alpha_deg: float, cfg: AircraftConfig) -> float:
    """Smooth logistic blend weight between the linear and post-stall branch.

    ``1`` deep post-stall, ``0`` in attached flow, exactly
    ``0.5 ** STALL_BLEND_ORDER`` at ``cfg.stall_alpha_deg``.
    """
    offset = _finite(alpha_deg) - _finite(cfg.stall_alpha_deg, 14.0)
    sharpness = max(abs(_finite(cfg.stall_sharpness, 1.4)), 1.0e-6)
    return _logistic(sharpness * offset) ** STALL_BLEND_ORDER


def lift_coefficient(
    alpha_deg: float,
    cfg: AircraftConfig,
    compressible: bool = True,
    mach: float = 0.0,
    cl_offset: float = 0.0,
) -> float:
    """Lift coefficient including the stall break, elevon offset and gust-free flow.

    Below the stall this is exactly ``cL0 + cLalpha alpha`` (the blend weight
    is ~1e-29 there); through the stall it transitions smoothly onto
    ``cl_max + post_stall_slope (alpha - stall_alpha_deg)`` whose negative slope
    guarantees the post-stall branch lies below the linear extrapolation.
    ``cl_offset`` carries the elevon lift increment so callers keep one coherent
    ``C_L``; the default zero reproduces the pure airfoil relation.
    """
    alpha = _finite(alpha_deg)
    slope = lift_slope_per_rad(cfg, compressible=compressible, mach=mach)
    linear = _finite(cfg.cL0, 0.0) + slope * deg2rad(alpha)
    post_stall = _finite(cfg.cl_max, 0.9) + _finite(cfg.post_stall_slope, -0.35) * (
        alpha - _finite(cfg.stall_alpha_deg, 14.0)
    )
    weight = stall_blend_weight(alpha, cfg)
    return (1.0 - weight) * linear + weight * post_stall + _finite(cl_offset)


def drag_coefficient(
    alpha_deg: float,
    cfg: AircraftConfig,
    reynolds: float = 1.0e4,
    ground_height_m: Optional[float] = None,
    mach: float = 0.0,
    compressible: bool = True,
    cl_offset: float = 0.0,
) -> float:
    """Drag polar ``C_D = C_D0(Re) + C_L^2 / (pi e AR)`` plus wave drag.

    ``ground_height_m=None`` evaluates the free-air polar; a height attenuates
    the **induced** part only, because a ground effect does not change the
    profile drag.
    """
    cl = lift_coefficient(alpha_deg, cfg, compressible=compressible, mach=mach, cl_offset=cl_offset)
    induced = cl * cl / _polar_constant(cfg)
    factor = 1.0 if ground_height_m is None else ground_effect_factor(ground_height_m, _span(cfg), True)
    return parasite_drag_coefficient(cfg, reynolds) + induced * factor + wave_drag_coefficient(
        mach, compressible
    )


# --------------------------------------------------------------------------
# 4. Elevon / trim surface
# --------------------------------------------------------------------------
def elevon_authority(cfg: AircraftConfig) -> float:
    """Fraction of the chord actually deflected by the elevon."""
    return clamp(abs(_finite(cfg.elevon_chord_ratio, 0.22)), 0.0, 1.0)


def elevon_effects(elevon_deg: float, cfg: AircraftConfig) -> Dict[str, float]:
    """Lift / pitching-moment increments and the resulting trim angle of attack.

    A **positive** ``elevon_deg`` is a trailing-edge-down (push) deflection: it
    adds lift and produces a **nose-down** pitching increment, therefore
    demanding a larger angle of attack to trim.  Both increments scale linearly
    with :func:`elevon_authority`.

    Returns
    -------
    dict
        ``dcl``              lift-coefficient increment (dimensionless)
        ``dcm``              pitching-moment increment (negative = nose down)
        ``dalpha_trim_deg``  angle of attack nulling ``cm0 + cm_alpha alpha + dcm``
    """
    deflection = _finite(elevon_deg)
    authority = elevon_authority(cfg)
    dcl = authority * ELEVON_LIFT_PER_DEG * deflection
    dcm = -authority * ELEVON_MOMENT_PER_DEG * deflection
    cm_alpha = _finite(cfg.cm_alpha, 0.0)
    if abs(cm_alpha) < 1.0e-12:
        return {"dcl": dcl, "dcm": dcm, "dalpha_trim_deg": 0.0}
    return {"dcl": dcl, "dcm": dcm, "dalpha_trim_deg": rad2deg(-(_finite(cfg.cm0) + dcm) / cm_alpha)}


def trim_alpha_from_elevon(cfg: AircraftConfig, elevon_deg: float) -> float:
    """Closed-form angle of attack that nulls the static pitching moment."""
    return float(elevon_effects(elevon_deg, cfg)["dalpha_trim_deg"])


def elevon_for_trim(cfg: AircraftConfig, alpha_deg: float) -> float:
    """Elevon deflection that nulls the static moment at ``alpha_deg``."""
    gain = elevon_authority(cfg) * ELEVON_MOMENT_PER_DEG
    if gain <= 1.0e-12:
        return 0.0
    required = -(_finite(cfg.cm0) + _finite(cfg.cm_alpha) * deg2rad(alpha_deg))
    return clamp(-required / gain, -ELEVON_MAX_DEG, ELEVON_MAX_DEG)


# --------------------------------------------------------------------------
# 5. Drag polar
# --------------------------------------------------------------------------
def drag_polar_minima(
    cfg: AircraftConfig,
    reynolds: float = 1.0e4,
    cl_zero_lift: Optional[float] = None,
    slope_per_rad: Optional[float] = None,
) -> Dict[str, float]:
    """Closed-form ``C_Dmin`` and the maximum-``L/D`` angle of attack.

    For the parabolic polar ``C_D = C_D0 + C_L^2 / k`` with ``k = pi e AR``:

    * ``C_Dmin = C_D0`` is reached at **zero lift** (``C_L = 0``);
    * maximum ``L/D`` is reached where parasite drag equals induced drag,
      ``C_L = sqrt(C_D0 k)``, giving ``(L/D)_max = 0.5 sqrt(k / C_D0)``.

    ``cl_zero_lift`` (the ``C_L`` at ``alpha = 0`` including the elevon
    increment, defaulting to ``cfg.cL0``) only maps those coefficients back onto
    an angle of attack.
    """
    constant = _polar_constant(cfg)
    cd0 = parasite_drag_coefficient(cfg, reynolds)
    zero_lift = _finite(cfg.cL0) if cl_zero_lift is None else _finite(cl_zero_lift)
    slope = lift_slope_per_rad(cfg) if slope_per_rad is None else _finite(slope_per_rad)
    cl_at_ld_max = math.sqrt(max(cd0, 0.0) * constant)
    return {
        "cd_min": cd0,
        "cl_at_cd_min": 0.0,
        "cd_at_cd_min": cd0,
        "alpha_cd_min_deg": 0.0 if abs(slope) < 1.0e-12 else rad2deg(-zero_lift / slope),
        "cl_at_ld_max": cl_at_ld_max,
        "alpha_ld_max_deg": 0.0 if abs(slope) < 1.0e-12 else rad2deg((cl_at_ld_max - zero_lift) / slope),
        "ld_max_theory": 0.5 * math.sqrt(constant / cd0) if cd0 > 0.0 else 0.0,
        "polar_constant": constant,
        "reynolds": abs(_finite(reynolds, RE_REFERENCE)),
    }


def drag_polar(
    cfg: AircraftConfig,
    alpha_min_deg: float = -8.0,
    alpha_max_deg: float = 20.0,
    points: int = 57,
    reynolds: float = 1.0e4,
) -> Dict[str, Any]:
    """Sweep the free-air drag polar over ``[alpha_min_deg, alpha_max_deg]``.

    Returns the sampled ``alpha_deg`` / ``cl`` / ``cd`` / ``ld_ratio`` columns
    together with ``cd_min``, ``alpha_ld_max_deg``, ``cl_at_cd_min``,
    ``cd_at_cd_min``, ``ld_max``, ``reynolds``, ``points`` and the backend tag.
    """
    first = _finite(alpha_min_deg, -8.0)
    second = _finite(alpha_max_deg, 20.0)
    low, high = min(first, second), max(first, second)
    count = max(int(points), 2)
    step = (high - low) / (count - 1)
    alphas: List[float] = [high if index == count - 1 else low + step * index for index in range(count)]

    slope = lift_slope_per_rad(cfg)
    cols_cl: List[float] = []
    cols_cd: List[float] = []
    cols_ld: List[float] = []
    for alpha in alphas:
        cl = lift_coefficient(alpha, cfg, compressible=False, mach=0.0)
        cd = drag_coefficient(alpha, cfg, reynolds=reynolds, ground_height_m=None)
        cols_cl.append(cl)
        cols_cd.append(cd)
        cols_ld.append(safe_div(cl, cd, 0.0) if cd > _CD_FLOOR else 0.0)

    best_cd = min(range(count), key=lambda i: cols_cd[i])
    best_ld = max(range(count), key=lambda i: cols_ld[i])
    zero_lift = _finite(cfg.cL0) + elevon_effects(cfg.elevon_deg, cfg)["dcl"]
    minima = drag_polar_minima(cfg, reynolds=reynolds, cl_zero_lift=zero_lift, slope_per_rad=slope)

    return {
        "alpha_deg": alphas,
        "cl": cols_cl,
        "cd": cols_cd,
        "ld_ratio": cols_ld,
        "cd_min": float(cols_cd[best_cd]),
        "alpha_cd_min_deg": float(alphas[best_cd]),
        "cl_at_cd_min": float(cols_cl[best_cd]),
        "cd_at_cd_min": float(cols_cd[best_cd]),
        "ld_max": float(cols_ld[best_ld]),
        "alpha_ld_max_deg": float(alphas[best_ld]),
        "cl_at_ld_max": float(cols_cl[best_ld]),
        "cd_at_ld_max": float(cols_cd[best_ld]),
        "reynolds": abs(_finite(reynolds, RE_REFERENCE)),
        "points": count,
        "alpha_min_deg": low,
        "alpha_max_deg": high,
        "analytic": minima,
        "backend": AERO_BACKEND,
    }


# --------------------------------------------------------------------------
# 6. Circulation
# --------------------------------------------------------------------------
def circulation(cfg: AircraftConfig, speed_mps: float, alpha_deg: float, density: float = 1.225) -> float:
    """Circulation around the wing ``Gamma = L / (rho V b)``.

    Obtained from ``L = rho V Gamma b`` with ``L = q S C_L`` and
    ``q = 0.5 rho V^2``, i.e. ``Gamma = 0.5 V S C_L / b`` (the only form that
    is both dimensionally consistent and satisfies the identity).  Returns
    ``0.0`` for zero speed or a zero / non-finite density.
    """
    try:
        rho = float(density)
    except (TypeError, ValueError):
        return 0.0
    speed = abs(_finite(speed_mps))
    if speed <= 0.0 or not math.isfinite(rho) or rho <= 0.0:
        return 0.0
    cl = lift_coefficient(alpha_deg, cfg, compressible=False, mach=0.0)
    return 0.5 * speed * _area(cfg) * cl / _span(cfg)


# --------------------------------------------------------------------------
# 5b. Trim solution
# --------------------------------------------------------------------------
def trim_solution(
    cfg: AircraftConfig,
    env: EnvironmentConfig,
    speed_mps: float,
    target_alpha_deg: Optional[float] = None,
    elevon_deg: Optional[float] = None,
) -> Dict[str, Any]:
    """Closed-form trim of the static pitching moment.

    * ``target_alpha_deg is None`` -> the **angle of attack** is solved for the
      supplied (or configured) elevon deflection, ``method = "alpha_trim"``.
    * ``target_alpha_deg`` given -> the **elevon** is solved for that angle,
      ``method = "elevon_trim"``.  A travel-limited deflection yields a non-zero
      ``residual_cm`` and ``trimmed = False``.

    ``cl`` / ``cd`` / ``ld_ratio`` are reported through :func:`evaluate_aero`,
    i.e. they already include ``env.lift_scale`` / ``env.drag_scale``.
    """
    if target_alpha_deg is None:
        deflection = _finite(cfg.elevon_deg) if elevon_deg is None else _finite(elevon_deg)
        alpha = trim_alpha_from_elevon(cfg, deflection)
        method = "alpha_trim"
    else:
        alpha = _finite(target_alpha_deg)
        deflection = elevon_for_trim(cfg, alpha)
        method = "elevon_trim"
    trimmed_cfg = replace(cfg, elevon_deg=float(deflection))
    result = evaluate_aero(trimmed_cfg, env, speed_mps, alpha, beta_deg=0.0, angular_rate=(0.0, 0.0, 0.0))
    residual = float(result.cm)
    return {
        "alpha_deg": float(alpha),
        "elevon_deg": float(deflection),
        "cm": residual,
        "cl": float(result.cl),
        "cd": float(result.cd),
        "ld_ratio": float(result.ld_ratio),
        "trimmed": bool(abs(residual) <= _TOLERANCE_CM),
        "residual_cm": residual,
        "method": method,
        "lift_n": float(result.lift_n),
        "drag_n": float(result.drag_n),
        "pitching_moment_nm": float(result.pitching_moment_nm),
        "ground_effect_ratio": float(result.ground_effect_ratio),
        "speed_mps": abs(_finite(speed_mps)),
    }


# --------------------------------------------------------------------------
# 7. Force / moment assembly
# --------------------------------------------------------------------------
def aerodynamic_force_body(drag_n: float, side_n: float, lift_n: float) -> Any:
    """Aerodynamic force vector in body axes (``x`` fwd, ``y`` right, ``z`` down)."""
    return vec3((-_finite(drag_n), _finite(side_n), -_finite(lift_n)))


def pitching_moment_offset_nm(
    cfg: AircraftConfig, lift_n: float, drag_n: float = 0.0, side_n: float = 0.0
) -> float:
    """Nose-up moment from the CG offset of the aerodynamic centre.

    ``M_offset = -(r x F)_y`` with ``r = (cg_offset_x, 0, 0)``.  Exactly zero for
    the default ``cg_offset_x = 0`` (after which the pitching moment is
    precisely ``q S c C_m``) and a stabilising nose-down term for an aft CG.
    """
    offset = _finite(cfg.cg_offset_x)
    if offset == 0.0:
        return 0.0
    arm = normalize(vec3((offset, 0.0, 0.0)))
    moment = cross(arm * offset, aerodynamic_force_body(drag_n, side_n, lift_n))
    return -float(moment[1])


# --------------------------------------------------------------------------
# 8. Atmosphere bridge
# --------------------------------------------------------------------------
def _fallback_atmosphere(env: EnvironmentConfig) -> Tuple[float, float, float, float]:
    """Self-contained ``(rho, nu, a, T)`` when the atmosphere module is absent."""
    gravity = abs(_finite(env.gravity, 9.80665))
    height = _finite(env.altitude_m)
    t0 = max(_finite(env.sea_level_temperature_K, T0_ISA), 1.0)
    p0 = max(_finite(env.sea_level_pressure_Pa, 101325.0), 1.0)
    lapse = _finite(env.lapse_rate_K_per_m, 0.0065)
    if bool(env.isa_mode):
        temperature = t0 - lapse * height
        ratio = temperature / t0
        if lapse == 0.0 or ratio <= 0.0:
            pressure = p0 * math.exp(clamp(-gravity * height / (R_AIR * t0), -700.0, 700.0))
        else:
            pressure = p0 * ratio ** (gravity / (lapse * R_AIR))
    else:
        temperature = _finite(env.temperature_K, T0_ISA)
        pressure = _finite(env.pressure_Pa, 101325.0)
    temperature = max(temperature, 100.0)
    pressure = max(pressure, 1.0)
    humidity = clamp(_finite(env.humidity_rel), 0.0, 1.0)
    celsius = temperature - 273.15
    p_sat = 610.94 * math.exp(clamp(17.625 * celsius / (celsius + 243.04), -60.0, 60.0))
    p_vapour = clamp(humidity * p_sat, 0.0, 0.999999 * pressure)
    r_moist = ((pressure - p_vapour) * R_AIR + p_vapour * R_VAPOR) / pressure
    density = pressure / (r_moist * temperature)
    ratio_t = temperature / T0_ISA
    viscosity = MU0_ISA * (ratio_t ** 1.5) * ((T0_ISA + MU_SUTHERLAND) / (temperature + MU_SUTHERLAND))
    return density, viscosity / density, math.sqrt(GAMMA_AIR * R_AIR * temperature), temperature


def atmosphere_state(env: EnvironmentConfig, speed_mps: float = 0.0) -> Dict[str, float]:
    """``(rho, nu, a, T, Re, M)`` resolved from the runtime environment.

    Delegates to :mod:`physics.atmosphere_domain` when importable (that worker
    owns ISA / Sutherland / moist-air) and otherwise evaluates the identical
    self-contained formulation, so this domain never hard-depends on it.
    """
    speed = abs(_finite(speed_mps))
    if _ATMOSPHERE is not None:
        try:
            payload = _ATMOSPHERE.evaluate_atmosphere(env, reference_chord_m=0.075)
            density = float(payload["density_kgm3"])
            kinematic = float(payload["kinematic_viscosity_m2s"])
            sound = float(payload["sound_speed_mps"])
            temperature = float(payload["temperature_K"])
        except Exception:
            density, kinematic, sound, temperature = _fallback_atmosphere(env)
    else:
        density, kinematic, sound, temperature = _fallback_atmosphere(env)
    return {
        "density_kgm3": density,
        "kinematic_viscosity_m2s": kinematic,
        "sound_speed_mps": sound,
        "temperature_K": temperature,
        "reynolds": safe_div(speed * 0.075, kinematic, 0.0) if kinematic > 0.0 else 0.0,
        "mach": safe_div(speed, sound, 0.0) if sound > 0.0 else 0.0,
    }


# --------------------------------------------------------------------------
# 9. Full evaluation
# --------------------------------------------------------------------------
def evaluate_aero(
    cfg: AircraftConfig,
    env: EnvironmentConfig,
    speed_mps: float,
    alpha_deg: float,
    beta_deg: float = 0.0,
    mach: float = 0.0,
    reynolds: Optional[float] = None,
    ground_height_m: float = 1.0,
    angular_rate: Sequence[float] = (0.0, 0.0, 0.0),
) -> AeroResult:
    """Complete 6-DoF aerodynamic evaluation at one instant.

    Honours ``env.drag_scale``, ``env.lift_scale``, ``env.ground_effect_enabled``
    and ``env.compressibility_enabled`` directly, so mutating the environment
    changes the returned forces with no hidden caching.  ``reynolds`` / ``mach``
    default to the values derived from ``speed_mps`` and the environment.
    """
    geom = _geom(cfg)
    area = max(_finite(geom["ref_area_m2"], 0.015), 1.0e-9)
    chord = max(_finite(geom["ref_length_m"], 0.075), 1.0e-9)
    span = max(_finite(geom["ref_span_m"], 0.2), 1.0e-6)

    speed = abs(_finite(speed_mps))
    alpha = _finite(alpha_deg)
    beta = _finite(beta_deg)
    state = atmosphere_state(env, speed)
    density = max(state["density_kgm3"], 1.0e-6)

    re_value = abs(_finite(reynolds, 0.0)) if reynolds is not None else float(state["reynolds"])
    if re_value <= 0.0:
        re_value = float(state["reynolds"])
    mach_value = abs(_finite(mach, 0.0))
    if mach_value <= 0.0:
        mach_value = float(state["mach"])

    compressible = bool(env.compressibility_enabled)
    lift_scale = max(_finite(env.lift_scale, 1.0), 0.0)
    drag_scale = max(_finite(env.drag_scale, 1.0), 0.0)

    elevon = elevon_effects(_finite(cfg.elevon_deg), cfg)
    slope = lift_slope_per_rad(cfg, compressible=compressible, mach=mach_value)

    cl_raw = lift_coefficient(alpha, cfg, compressible=compressible, mach=mach_value, cl_offset=elevon["dcl"])
    cl = cl_raw * lift_scale

    ground_ratio = ground_effect_factor(ground_height_m, span, bool(env.ground_effect_enabled))
    cd0 = parasite_drag_coefficient(cfg, re_value)
    cd = (
        cd0
        + (cl * cl / _polar_constant(cfg)) * ground_ratio
        + wave_drag_coefficient(mach_value, compressible)
    ) * drag_scale

    # --- derivatives -----------------------------------------------------
    p_rate, q_rate, r_rate = (float(component) for component in vec3(angular_rate))
    two_v = 2.0 * speed
    p_hat = safe_div(p_rate * span, two_v)
    q_hat = safe_div(q_rate * chord, two_v)
    r_hat = safe_div(r_rate * span, two_v)
    beta_rad = deg2rad(beta)

    cl_roll = _finite(cfg.cl_roll_rate) * p_hat
    cm_roll = _finite(cfg.cm_roll_rate) * p_hat
    cn_roll = _finite(cfg.cn_roll_rate) * p_hat

    cy = _finite(cfg.cy_beta) * beta_rad
    roll_coeff = _finite(cfg.cl_beta) * beta_rad + cl_roll + _finite(cfg.cl_pitch_rate) * q_hat + _finite(cfg.cl_yaw_rate) * r_hat
    cm = (
        _finite(cfg.cm0)
        + _finite(cfg.cm_alpha) * deg2rad(alpha)
        + 0.5 * _finite(cfg.cm_q) * q_hat
        + elevon["dcm"]
        + cm_roll
        + _finite(cfg.cm_pitch_rate) * q_hat
        + _finite(cfg.cm_yaw_rate) * r_hat
    )
    yaw_coeff = _finite(cfg.cn_beta) * beta_rad + cn_roll + _finite(cfg.cn_pitch_rate) * q_hat + _finite(cfg.cn_yaw_rate) * r_hat

    dynamic_pressure = 0.5 * density * speed * speed
    lift_n = dynamic_pressure * area * cl
    drag_n = dynamic_pressure * area * cd
    side_n = dynamic_pressure * area * cy
    pitching_nm = dynamic_pressure * area * chord * cm + pitching_moment_offset_nm(cfg, lift_n, drag_n, side_n)
    rolling_nm = dynamic_pressure * area * span * roll_coeff
    yawing_nm = dynamic_pressure * area * span * yaw_coeff

    stall_angle = _finite(cfg.stall_alpha_deg, 14.0)
    stalled = bool(alpha > stall_angle + 1.0)
    stall_margin = safe_div(stall_angle - alpha, abs(stall_angle) if stall_angle != 0.0 else 1.0, 0.0)

    zero_lift = _finite(cfg.cL0) * lift_scale + elevon["dcl"] * lift_scale
    # The reported polar minima are free-air and incompressible (no ground
    # effect, no wave drag, no Prandtl-Glauert), matching the free-air sweep
    # that drag_polar() performs by default.
    minima = drag_polar_minima(
        cfg,
        reynolds=re_value,
        cl_zero_lift=zero_lift,
        slope_per_rad=lift_slope_per_rad(cfg, compressible=False),
    )

    ld_ratio = safe_div(cl, cd, 0.0) if cd > _CD_FLOOR else 0.0
    gamma = 0.0 if (speed <= 0.0 or density <= 0.0) else 0.5 * speed * area * cl / span

    return AeroResult(
        cl=float(cl),
        cd=float(cd),
        cm=float(cm),
        cy=float(cy),
        cl_roll=float(cl_roll),
        cm_roll=float(cm_roll),
        cn_roll=float(cn_roll),
        lift_n=float(lift_n),
        drag_n=float(drag_n),
        side_n=float(side_n),
        pitching_moment_nm=float(pitching_nm),
        yawing_moment_nm=float(yawing_nm),
        rolling_moment_nm=float(rolling_nm),
        ld_ratio=float(ld_ratio),
        dynamic_pressure_pa=float(dynamic_pressure),
        reynolds=float(re_value),
        mach=float(mach_value),
        stall_margin=float(stall_margin),
        stalled=stalled,
        cd_min=float(minima["cd_min"]),
        alpha_ld_max_deg=float(minima["alpha_ld_max_deg"]),
        circulation_m2ps=float(gamma),
        trim_alpha_deg=trim_alpha_from_elevon(cfg, _finite(cfg.elevon_deg)),
        trim_elevon_deg=elevon_for_trim(cfg, alpha),
        ground_effect_ratio=float(ground_ratio),
    )


def evaluate_aero_state(
    cfg: AircraftConfig, env: EnvironmentConfig, speed_mps: float, state: AeroState
) -> AeroResult:
    """:func:`evaluate_aero` driven by an :class:`AeroState` payload."""
    return evaluate_aero(
        cfg,
        env,
        speed_mps,
        _finite(state.alpha_deg),
        beta_deg=_finite(state.beta_deg),
        mach=abs(_finite(state.mach)),
        reynolds=None if state.reynolds is None else abs(_finite(state.reynolds)),
        ground_height_m=_finite(state.ground_height_m, 1.0),
        angular_rate=vec3(state.angular_rate),
    )


def aero_result_to_dict(result: AeroResult) -> Dict[str, Any]:
    """JSON payload for one :class:`AeroResult`."""
    payload = as_dict(result)
    payload["backend"] = AERO_BACKEND
    return payload