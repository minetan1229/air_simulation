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
    drag ``C_L^2 / (pi e AR)``, the latter multiplied by the ground-effect
    factor ``16 (h/b)^2 / (1 + 16 (h/b)^2)`` (which is ``1.0`` in free air and
    ``1.0`` again when the model is switched off, so disabling it restores the
    free-air polar instead of deleting the induced drag).
5.  Elevon deflection adds lift and a nose-down pitching increment, hence
    shifts the trimmed angle of attack.
6.  Static / dynamic stability derivatives assembled into ``C_m``, ``C_l`` and
    ``C_n``, non-dimensionalised with ``b / (2V)`` and ``c / (2V)``.
7.  Forces ``L = q S C_L``, ``D = q S C_D``, ``Y = q S C_y`` and moments
    ``M = q S c C_m``, ``l = q S b C_l``, ``n = q S b C_n`` with
    ``q = 0.5 rho V^2`` and reference quantities from
    :func:`~physics.config_schema.derived_properties`.

``c`` in the moment and rate derivatives and in ``Re = V c / nu`` is the **mean
aerodynamic chord** ``derived_properties(cfg)["ref_length_m"]``; the whole
airframe is referred to it, so a slider that moves span or area moves ``Re``,
the parasite drag, the pitch inertia and the reference moment arm together.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from .config_schema import (
    GAMMA_AIR,
    MU0_ISA,
    MU_SUTHERLAND,
    R_AIR,
    R_VAPOR,
    SUTHERLAND_T0,
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
from .geometry_utils import cross, vec3

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
    "trailing_edge_fold_effects",
    "elevon_authority",
    "trim_alpha_from_elevon",
    "elevon_for_trim",
    "drag_polar",
    "drag_polar_minima",
    "trim_solution",
    "circulation",
    "aerodynamic_force_body",
    "pitching_moment_offset_nm",
    "center_of_pressure_x",
    "polhamus_lift_coefficient",
    "effective_stall_alpha_deg",
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
    "DEFAULT_REFERENCE_CHORD_M",
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

#: Fallback Reynolds reference chord (m) for the geometry-free callers of
#: :func:`atmosphere_state`.  Every caller that owns an :class:`AircraftConfig`
#: must override it with the airframe's mean aerodynamic chord.
DEFAULT_REFERENCE_CHORD_M: float = 0.1

_ATMOSPHERE: Any = None
try:  # the atmosphere worker owns that module; the fallback below is functional
    from . import atmosphere_domain as _ATMOSPHERE  # type: ignore[no-redef]
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

    The returned value is the **multiplier applied to the free-air induced
    drag**, so it is ``1.0`` in free air, ``0.0`` at the surface, strictly
    inside ``(0, 1]`` for finite positive height and span, and exactly ``1.0``
    once ``h / b`` exceeds ~2.4e7.

    Disabling the model returns ``1.0`` -- *no* ground-effect reduction, i.e. the
    full free-air induced drag -- not ``0.0``.  Returning zero would silently
    delete the induced-drag term (the caller multiplies, it does not skip), which
    would inflate ``L/D`` by ~30 % on the default airframe the moment a user
    unticked ``ground_effect_enabled``.
    """
    if not bool(enabled):
        return 1.0
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
def effective_stall_alpha_deg(cfg: AircraftConfig) -> float:
    """Stall angle of attack modified by fold step turbulator trip wire.

    When ``cfg.step_trip_ratio > 0``, the fold step acts as a boundary layer trip wire,
    preventing laminar bubble burst and delaying full stall separation by up to several degrees.
    """
    stall_alpha = _finite(cfg.stall_alpha_deg, 14.0)
    trip_ratio = max(_finite(cfg.step_trip_ratio, 0.0), 0.0)
    if trip_ratio > 0.0:
        # Step trip wire delays stall separation: each 0.01 step/c adds ~2.5 deg stall delay (saturating)
        delay = 250.0 * trip_ratio / (1.0 + 50.0 * trip_ratio)
        return stall_alpha + delay
    return stall_alpha


def polhamus_lift_coefficient(alpha_deg: float, cfg: AircraftConfig, kp: Optional[float] = None) -> float:
    """Polhamus suction analogy leading-edge vortex (LEV) lift coefficient.

    ``C_L_polhamus = K_p * sin(alpha) * cos(alpha)^2 + K_v * sin(alpha)^2 * cos(alpha)``
    where ``K_p`` is the potential lift factor (proportional to ``lift_slope_per_rad``) and
    ``K_v`` is ``cfg.polhamus_kv`` representing vortex suction force on slender/delta wings.
    """
    alpha_rad = deg2rad(_finite(alpha_deg))
    cos_a = math.cos(alpha_rad)
    sin_a = math.sin(alpha_rad)
    k_p = lift_slope_per_rad(cfg, compressible=False) if kp is None else float(kp)
    k_v = max(_finite(cfg.polhamus_kv, 0.0), 0.0)
    return k_p * sin_a * (cos_a ** 2) + k_v * (sin_a ** 2) * cos_a


def stall_blend_weight(alpha_deg: float, cfg: AircraftConfig) -> float:
    """Smooth logistic blend weight between the linear and post-stall branch.

    ``1`` deep post-stall, ``0`` in attached flow, exactly
    ``0.5 ** STALL_BLEND_ORDER`` at effective stall angle.
    """
    alpha = _finite(alpha_deg)
    stall_alpha = effective_stall_alpha_deg(cfg)
    if alpha >= 0.0:
        offset = alpha - stall_alpha
    else:
        # Negative alpha remains linear in typical inverted/recovery angles,
        # transitioning to the flat plate curve at deep negative alpha
        neg_stall = max(stall_alpha + 15.0, 30.0)
        offset = -alpha - neg_stall
    sharpness = max(abs(_finite(cfg.stall_sharpness, 1.4)), 1.0e-6)
    return _logistic(sharpness * offset) ** STALL_BLEND_ORDER


def flat_plate_lift_coefficient(alpha_deg: float) -> float:
    """Hoerner fully-separated flat-plate lift coefficient ``2 sin a cos^2 a``.

    This is the correct limit once the flow is completely separated: the lift
    peaks at ``2*sin(35.26 deg)*cos^2(35.26 deg) = 0.7698`` and returns to
    **zero** at 90 deg, because a plate broadside to the flow carries no normal
    force.  Unlike a linear post-stall ramp it is bounded, which is what keeps
    the force model finite when the airframe tumbles (a body in vertical free
    fall sits at alpha=90).
    """
    a = deg2rad(_finite(alpha_deg))
    return 2.0 * math.sin(a) * math.cos(a) * math.cos(a)


#: Beyond this |alpha| the unbounded linear post-stall ramp is progressively
#: replaced by the flat-plate curve; by 85 deg only the flat plate remains.
POST_STALL_BLEND_START_DEG = 45.0
POST_STALL_BLEND_END_DEG = 85.0

#: Hard ceiling on |C_L|.  A thin aerofoil section cannot exceed roughly
#: 2*sin(a)*cos^2(a) + a modest attached-flow contribution, so anything beyond
#: this is numerical, not physical, and is clipped.
MAX_ABS_LIFT_COEFFICIENT = 4.0


def _post_stall_lift_coefficient(alpha: float, cfg: AircraftConfig, linear_at_stall: float) -> float:
    """Bounded post-stall lift, anchored to the airframe's own lift curve."""
    sign = 1.0 if alpha >= 0.0 else -1.0
    abs_alpha = abs(alpha)
    stall_alpha = effective_stall_alpha_deg(cfg)
    ref_cl = abs(linear_at_stall)
    raw = ref_cl + _finite(cfg.post_stall_slope, -0.03) * (abs_alpha - stall_alpha)
    ceiling = max(abs(_finite(cfg.cl_max, 0.90)), ref_cl, 0.1)
    ramp = sign * ceiling * math.tanh(max(raw, 0.0) / ceiling)
    if abs_alpha <= POST_STALL_BLEND_START_DEG:
        return ramp
    plate_reference = flat_plate_lift_coefficient(stall_alpha)
    if abs(plate_reference) < 1.0e-9:
        plate = 0.0
    else:
        plate = flat_plate_lift_coefficient(alpha) * (ref_cl / plate_reference)
    if abs_alpha >= POST_STALL_BLEND_END_DEG:
        return plate
    span = POST_STALL_BLEND_END_DEG - POST_STALL_BLEND_START_DEG
    t = (abs_alpha - POST_STALL_BLEND_START_DEG) / span
    weight = t * t * (3.0 - 2.0 * t)  # smoothstep, C1 continuous
    return (1.0 - weight) * ramp + weight * plate


def lift_coefficient(
    alpha_deg: float,
    cfg: AircraftConfig,
    compressible: bool = True,
    mach: float = 0.0,
    cl_offset: float = 0.0,
) -> float:
    """Lift coefficient including the stall break, elevon offset and gust-free flow.

    Below the stall this is exactly ``cL0 + cLalpha alpha`` (or augmented with Polhamus LEV suction
    when ``cfg.polhamus_kv > 0``); through the stall it transitions smoothly onto the
    post-stall branch ``cl_max + post_stall_slope (alpha - stall_alpha_deg)``,
    whose negative slope keeps it below the linear extrapolation.  Deep in
    stall that unbounded ramp is blended into :func:`flat_plate_lift_coefficient`
    and the result is clipped to :data:`MAX_ABS_LIFT_COEFFICIENT`, so no
    attitude can produce an infinite lift (or drag) force.
    ``cl_offset`` carries the elevon lift increment so callers keep one
    coherent ``C_L``; the default zero reproduces the pure airfoil relation.
    """
    alpha = _finite(alpha_deg)
    slope = lift_slope_per_rad(cfg, compressible=compressible, mach=mach)
    stall_alpha = effective_stall_alpha_deg(cfg)
    linear = _finite(cfg.cL0, 0.0) + slope * deg2rad(alpha)

    # Polhamus LEV suction analogy: when cfg.polhamus_kv > 0, compute vortex lift increment
    kv = max(_finite(cfg.polhamus_kv, 0.0), 0.0)
    if kv > 0.0:
        alpha_rad = deg2rad(alpha)
        sin_a = math.sin(alpha_rad)
        cos_a = math.cos(alpha_rad)
        # Delta vortex lift: K_v * sin^2(alpha) * cos(alpha)
        # Sign preservation for negative alpha:
        vortex_cl = math.copysign(kv * (sin_a ** 2) * cos_a, alpha_rad)
        linear = linear + vortex_cl

    linear_at_stall = _finite(cfg.cL0, 0.0) + slope * deg2rad(stall_alpha)
    if kv > 0.0:
        stall_rad = deg2rad(stall_alpha)
        linear_at_stall += math.copysign(kv * (math.sin(stall_rad) ** 2) * math.cos(stall_rad), stall_rad)

    post_stall = _post_stall_lift_coefficient(alpha, cfg, linear_at_stall)
    weight = stall_blend_weight(alpha, cfg)
    value = (1.0 - weight) * linear + weight * post_stall + _finite(cl_offset)
    ceiling = MAX_ABS_LIFT_COEFFICIENT + abs(_finite(cl_offset))
    return clamp(value, -ceiling, ceiling)


def separated_pressure_drag_coefficient(alpha_deg: float, cfg: AircraftConfig) -> float:
    """Empirical separated-plate pressure drag, C_D = 2 sin²(alpha).

    A C1 smooth transition starts at the lift model's corresponding stall
    threshold and reaches the plate limit at broadside incidence. This is
    an uncalibrated thin-plate approximation, not a measured airframe polar.
    Attached-flow analytic polars remain unchanged. Reverse flow is handled
    by the same projected-area law; edge-on flow retains parasite drag.
    """
    alpha = (_finite(alpha_deg) + 180.0) % 360.0 - 180.0
    threshold = effective_stall_alpha_deg(cfg)
    if alpha < 0.0:
        threshold = max(threshold + 15.0, 30.0)
    transition = clamp((abs(alpha) - threshold) / max(90.0 - threshold, 1.0), 0.0, 1.0)
    weight = transition * transition * (3.0 - 2.0 * transition)
    return weight * 2.0 * math.sin(deg2rad(alpha)) ** 2


def drag_coefficient(
    alpha_deg: float,
    cfg: AircraftConfig,
    reynolds: float = 1.0e4,
    ground_height_m: Optional[float] = None,
    mach: float = 0.0,
    compressible: bool = True,
    cl_offset: float = 0.0,
) -> float:
    """Attached polar plus separated pressure drag and wave drag.

    ``ground_height_m=None`` evaluates the free-air polar; a height attenuates
    the **induced** part only, because a ground effect does not change the
    profile drag.
    """
    cl = lift_coefficient(alpha_deg, cfg, compressible=compressible, mach=mach, cl_offset=cl_offset)
    induced = cl * cl / _polar_constant(cfg)
    factor = 1.0 if ground_height_m is None else ground_effect_factor(ground_height_m, _span(cfg), True)
    return parasite_drag_coefficient(cfg, reynolds) + induced * factor + separated_pressure_drag_coefficient(alpha_deg, cfg) + wave_drag_coefficient(
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


def trailing_edge_fold_effects(cfg: AircraftConfig) -> Dict[str, float]:
    """Aerodynamic increments from full-span trailing-edge fold angle.

    A positive fold angle is trailing-edge-down (camber increase): adds lift (dcl > 0)
    and introduces a nose-down moment (dcm < 0).
    A negative fold angle is trailing-edge-up (reflex): reduces lift (dcl < 0)
    and introduces a nose-up pitch trim moment (dcm > 0), essential for tailless paper gliders.
    Also produces a profile drag increment dcd scaling with sin^2(fold).
    """
    fold_deg = _finite(getattr(cfg, "fold_trailing_deg", 0.0), 0.0)
    if fold_deg == 0.0:
        return {"dcl": 0.0, "dcm": 0.0, "dcd": 0.0}
    rad = deg2rad(fold_deg)
    dcl = 0.015 * fold_deg
    dcm = -0.012 * fold_deg
    dcd = 0.035 * (math.sin(rad) ** 2)
    return {"dcl": dcl, "dcm": dcm, "dcd": dcd}


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

    ``M_offset = (r x F)_y`` with the arm ``r = (offset, 0, 0)`` representing
    the vector from CG to AC. With ``cg_offset_x`` (+ = aft), an aft CG places
    the CG behind the AC, so the AC is ahead of the CG (r_x = +offset). Lift
    acts in the -z direction (-L), giving a pitching moment of:
        (r x F)_y = -r_x * F_z = -offset * (-L) = +offset * L
    This represents a destabilising nose-up pitching moment (+offset * L) for an
    aft CG and a stabilising nose-down pitching moment (-|offset| * L) for a
    forward CG. Exactly zero for the default ``cg_offset_x = 0``.
    """
    offset = _finite(cfg.cg_offset_x)
    if offset == 0.0:
        return 0.0
    moment = cross(vec3((offset, 0.0, 0.0)), aerodynamic_force_body(drag_n, side_n, lift_n))
    return float(moment[1])


def center_of_pressure_x(alpha_deg: float, cfg: AircraftConfig) -> float:
    """Dynamic center of pressure position along MAC from the leading edge (in metres).

    For thin plates/airfoils, in linear attached flow at small alpha, the center of pressure
    lies around 0.25 MAC (aerodynamic center). As angle of attack increases towards stall
    and separation, the center of pressure shifts aft towards 0.45..0.50 MAC.
    When ``cfg.cp_shift_gain > 0``, the shift is scaled by ``cp_shift_gain``.
    """
    chord = max(_finite(_geom(cfg)["ref_length_m"], 0.075), 1.0e-9)
    alpha = abs(_finite(alpha_deg))
    stall_alpha = effective_stall_alpha_deg(cfg)

    # Shift fraction from 0.25 at alpha=0 towards 0.50 at and beyond stall
    # Normalized progression with alpha / stall_alpha
    progression = clamp(alpha / max(stall_alpha, 1.0), 0.0, 1.0)
    # Smooth progression (Hermite smoothstep)
    progression_smooth = progression * progression * (3.0 - 2.0 * progression)

    # Baseline thin airfoil CP is at 0.25 c; max aft shift at full separation is up to 0.50 c
    gain = max(_finite(cfg.cp_shift_gain, 0.0), 0.0)
    cp_frac = 0.25 + 0.25 * progression_smooth * min(gain, 1.0)
    return float(cp_frac * chord)


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
    r_moist = pressure / (((pressure - p_vapour) / R_AIR) + (p_vapour / R_VAPOR))
    density = pressure / (r_moist * temperature)
    ratio_t = temperature / SUTHERLAND_T0
    viscosity = MU0_ISA * (ratio_t ** 1.5) * ((SUTHERLAND_T0 + MU_SUTHERLAND) / (temperature + MU_SUTHERLAND))
    return density, viscosity / density, math.sqrt(GAMMA_AIR * R_AIR * temperature), temperature


def atmosphere_state(
    env: EnvironmentConfig, speed_mps: float = 0.0, reference_chord_m: float = DEFAULT_REFERENCE_CHORD_M
) -> Dict[str, float]:
    """``(rho, nu, a, T, Re, M)`` resolved from the runtime environment.

    ``Re = V c / nu`` is referred to ``reference_chord_m``.  That must be the
    airframe's **mean aerodynamic chord** -- callers holding an
    :class:`AircraftConfig` should pass ``derived_properties(cfg)["ref_length_m"]``
    (which is exactly what :func:`evaluate_aero` does), otherwise the parasite
    drag of a slider-driven airframe is evaluated at a chord that never moves.
    ``DEFAULT_REFERENCE_CHORD_M`` is only the stand-in for the geometry-free
    callers.

    Delegates to :mod:`physics.atmosphere_domain` when importable (that worker
    owns ISA / Sutherland / moist air) and otherwise evaluates the identical
    self-contained formulation, so this domain never hard-depends on it.
    """
    speed = abs(_finite(speed_mps))
    chord = max(_finite(reference_chord_m, DEFAULT_REFERENCE_CHORD_M), 1.0e-9)
    if _ATMOSPHERE is not None:
        try:
            payload = _ATMOSPHERE.evaluate_atmosphere(env, reference_chord_m=chord)
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
        "reynolds": safe_div(speed * chord, kinematic, 0.0) if kinematic > 0.0 else 0.0,
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
    angular_rate: Union[Sequence[float], np.ndarray] = (0.0, 0.0, 0.0),
    density_kgm3: Optional[float] = None,
    kinematic_viscosity_m2s: Optional[float] = None,
) -> AeroResult:
    """Complete 6-DoF aerodynamic evaluation at one instant.

    Honours ``env.drag_scale``, ``env.lift_scale``, ``env.ground_effect_enabled``
    and ``env.compressibility_enabled`` directly, so mutating the environment
    changes the returned forces with no hidden caching.  ``reynolds`` / ``mach``
    default to the values derived from ``speed_mps`` and the environment.
    Optional local density and kinematic viscosity override the environment
    atmosphere for dynamic pressure and Reynolds number, respectively. An
    explicit Reynolds number still takes precedence. ``cm_q`` multiplies the
    standard nondimensional pitch rate ``q * chord / (2 * speed)`` once.
    """
    geom = _geom(cfg)
    area = max(_finite(geom["ref_area_m2"], 0.015), 1.0e-9)
    chord = max(_finite(geom["ref_length_m"], 0.075), 1.0e-9)
    span = max(_finite(geom["ref_span_m"], 0.2), 1.0e-6)

    speed = abs(_finite(speed_mps))
    alpha = _finite(alpha_deg)
    beta = _finite(beta_deg)
    state = atmosphere_state(env, speed, reference_chord_m=chord)
    density = max(state["density_kgm3"], 1.0e-6)
    kinematic = state["kinematic_viscosity_m2s"]
    if density_kgm3 is not None:
        density = float(density_kgm3)
        if not math.isfinite(density) or density <= 0.0:
            raise ValueError("density_kgm3 must be finite and positive")
    if kinematic_viscosity_m2s is not None:
        kinematic = float(kinematic_viscosity_m2s)
        if not math.isfinite(kinematic) or kinematic <= 0.0:
            raise ValueError("kinematic_viscosity_m2s must be finite and positive")
    local_reynolds = speed * chord / kinematic

    re_value = abs(_finite(reynolds, 0.0)) if reynolds is not None else local_reynolds
    if re_value <= 0.0:
        re_value = local_reynolds
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
        + separated_pressure_drag_coefficient(alpha, cfg)
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
    aileron = _finite(getattr(cfg, "aileron_deg", 0.0))
    asym_cl = _finite(getattr(cfg, "asymmetry_cl", 0.0))
    rudder = _finite(getattr(cfg, "rudder_deg", 0.0))
    cl_control = 0.015 * aileron + 0.25 * asym_cl
    roll_coeff = (
        _finite(cfg.cl_beta) * beta_rad
        + cl_roll
        + cl_control
        + _finite(cfg.cl_pitch_rate) * q_hat
        + _finite(cfg.cl_yaw_rate) * r_hat
    )
    # Nonlinear pitching moment with post-stall saturation (Viterna / flat plate theory):
    # Attached flow: Cm_alpha * alpha. Post-stall / high-alpha separation:
    # Pitching moment saturates smoothly and approaches flat-plate cross-flow behavior ~ -sin(2*alpha),
    # preventing non-physical massive torques (e.g. Cm < -1.0) during apex flow reversal (alpha > 90 deg).
    stall_rad = deg2rad(effective_stall_alpha_deg(cfg))
    alpha_rad = deg2rad(alpha)
    if abs(alpha_rad) <= stall_rad:
        cm_alpha_term = _finite(cfg.cm_alpha) * alpha_rad
    else:
        w_stall = stall_blend_weight(alpha, cfg)
        linear_val = _finite(cfg.cm_alpha) * math.copysign(stall_rad, alpha_rad)
        # Flat-plate cross-flow restoring moment:
        # Physically, normal flow resistance on the aft planform acts to restore
        # the body into alignment with the relative wind. The restoring moment
        # scales as sin(alpha) * |cos(alpha)|, ensuring correct sign preservation
        # for both positive and negative angles of attack up to +/-180 degrees.
        sin_a = math.sin(alpha_rad)
        cos_a = math.cos(alpha_rad)
        flat_plate_cm = _finite(cfg.cm_alpha) * sin_a * abs(cos_a) * stall_rad
        cm_alpha_term = (1.0 - w_stall) * linear_val + w_stall * flat_plate_cm

    cm = (
        _finite(cfg.cm0)
        + cm_alpha_term
        + _finite(cfg.cm_q) * q_hat
        + elevon["dcm"]
        + cm_roll
        + _finite(cfg.cm_pitch_rate) * q_hat
        + _finite(cfg.cm_yaw_rate) * r_hat
    )
    # Adverse yaw from aileron + rudder effect
    cn_control = 0.010 * rudder - 0.002 * aileron
    yaw_coeff = (
        _finite(cfg.cn_beta) * beta_rad
        + cn_roll
        + cn_control
        + _finite(cfg.cn_pitch_rate) * q_hat
        + _finite(cfg.cn_yaw_rate) * r_hat
    )

    # Ground cushion lift increment: scaling with cfg.ground_cushion_lift * exp(-4.0 * h / b)
    cushion_gain = max(_finite(cfg.ground_cushion_lift, 0.0), 0.0)
    h_b_ratio = safe_div(max(_finite(ground_height_m, 1.0), 0.0), span, 10.0)
    ground_proximity = math.exp(-4.0 * h_b_ratio) if bool(env.ground_effect_enabled) else 0.0
    if cushion_gain > 0.0 and ground_proximity > 0.0:
        delta_cl_cushion = cushion_gain * ground_proximity
        cl = cl + delta_cl_cushion

    # Nose-down ground pitching moment increment: scaling with cfg.ground_pitch_coupling * C_L * exp(-4.0 * h / b)
    pitch_coupling = max(_finite(cfg.ground_pitch_coupling, 0.0), 0.0)
    if pitch_coupling > 0.0 and ground_proximity > 0.0:
        # Negative increment produces nose-down pitching moment
        delta_cm_ground = -pitch_coupling * cl * ground_proximity
        cm = cm + delta_cm_ground

    # Dynamic center of pressure shift: cp_x_m from leading edge
    # Baseline AC is at 0.25 chord (or including cfg.cg_offset_x)
    cp_x = center_of_pressure_x(alpha, cfg)
    cp_gain = max(_finite(cfg.cp_shift_gain, 0.0), 0.0)
    if cp_gain > 0.0:
        # Baseline reference aerodynamic center is at 0.25 MAC
        # When CP shifts aft, delta_cm = - (x_cp - x_ac) / c * C_L
        delta_cm_cp = - safe_div(cp_x - 0.25 * chord, chord, 0.0) * cl
        cm = cm + delta_cm_cp

    dynamic_pressure = 0.5 * density * speed * speed
    lift_n = dynamic_pressure * area * cl
    # Left and right wing lift partitioning based on total lift and roll moment
    # rolling_nm = (lift_left_n - lift_right_n) * (span / 4) approx, or partition from roll_coeff / cl_control
    d_lift = dynamic_pressure * area * cl_control
    lift_left_n = 0.5 * lift_n + 0.5 * d_lift
    lift_right_n = 0.5 * lift_n - 0.5 * d_lift
    drag_n = dynamic_pressure * area * cd
    side_n = dynamic_pressure * area * cy

    # Apex low-speed pushover dynamics (AGENTS.md Section 8.2):
    # At the climb apex (speed < 3.0 m/s) where dynamic pressure approaches zero,
    # the aft center-of-pressure shift in separated flow and forward-shifted CG eccentricity
    # produce a physical aerodynamic restoring pitching moment to align the nose with flow.
    # Note: Bounded and smoothly decayed as the aircraft reaches horizontal glide attitude.
    if speed < 3.0 and abs(alpha) > 0.1:
        v_norm = clamp(speed / 3.0, 0.0, 1.0)
        speed_gate = 1.0 - v_norm * v_norm * (3.0 - 2.0 * v_norm)  # Hermite smoothstep C1 continuous
        # Effective dynamic pressure floor ensuring restoring authority even as V -> 0:
        # q_eff = 0.5 * rho * max(V, 0.8)^2 (~0.39 Pa at rho=1.225)
        q_eff = 0.5 * density * (max(speed, 0.8) ** 2)
        alpha_gate = math.tanh(abs(alpha) / 8.0)
        sin_a = math.sin(deg2rad(clamp(alpha, -90.0, 90.0)))
        pushover_moment = -q_eff * area * chord * 0.85 * sin_a * speed_gate * alpha_gate
    else:
        pushover_moment = 0.0

    # Low-speed rotational apparent/viscous damping (Cross-flow resistance):
    # In low airspeed regime where dynamic pressure q_dyn -> 0, classic q*c/(2V) vanishes.
    # Fluid cross-flow drag on the rotating planform exerts physical angular damping:
    if speed < 3.0 and area > 1e-6 and chord > 1e-4:
        v_norm = clamp(speed / 3.0, 0.0, 1.0)
        damp_gate = 1.0 - v_norm * v_norm * (3.0 - 2.0 * v_norm)
        q_rate_val = float(angular_rate[1]) if len(angular_rate) > 1 else 0.0
        rot_damping = (-0.5 * density * 1.2 * area * (chord ** 2) * abs(q_rate_val) * q_rate_val - 0.00015 * area * chord * density * q_rate_val) * damp_gate
    else:
        rot_damping = 0.0

    pitching_nm = (
        dynamic_pressure * area * chord * cm
        + pitching_moment_offset_nm(cfg, lift_n, drag_n, side_n)
        + pushover_moment
        + rot_damping
    )
    rolling_nm = dynamic_pressure * area * span * roll_coeff
    yawing_nm = dynamic_pressure * area * span * yaw_coeff

    stall_angle = effective_stall_alpha_deg(cfg)
    # Match both sign-dependent thresholds in stall_blend_weight. The wider
    # negative threshold is an existing empirical recovery assumption, not
    # evidence of a measured negative-angle polar for every airframe.
    stall_threshold = stall_angle if alpha >= 0.0 else max(stall_angle + 15.0, 30.0)
    stalled = bool(abs(alpha) > stall_threshold)
    stall_margin = safe_div(stall_threshold - abs(alpha), abs(stall_threshold) if stall_threshold != 0.0 else 1.0, 0.0)

    # Aeroelastic deformation estimates from dynamic pressure and stiffness
    stiffness = max(_finite(cfg.paper_stiffness, 0.35), 0.01)
    # Reference aeroelastic dynamic pressure scale: q_ref ~ 50 Pa for typical paper glider
    q_scale = safe_div(dynamic_pressure, 50.0 * stiffness, 0.0)
    aeroelastic_dihedral = _finite(cfg.aeroelastic_flex, 0.0) * q_scale * 2.5
    aeroelastic_twist = -_finite(cfg.aeroelastic_twist, 0.0) * q_scale * 1.5

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
        lift_left_n=float(lift_left_n),
        lift_right_n=float(lift_right_n),
        cp_x_m=float(cp_x),
        aeroelastic_dihedral_deg=float(aeroelastic_dihedral),
        aeroelastic_twist_deg=float(aeroelastic_twist),
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
