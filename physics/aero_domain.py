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
    branch by a smooth logistic of sharpness ``cfg.stall_sharpness`` centred on
    ``cfg.stall_alpha_deg`` (see :func:`stall_blend_weight`).  The post-stall
    branch starts at the airframe's own stall value and moves onto the
    Hoerner fully separated flat plate ``2 sin(a) cos^2(a)`` over
    ``[alpha_stall, POST_STALL_PLATE_BLEND_DEG]``; it never collapses onto an
    exact ``C_L == 0`` band.
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
from . import aero_kernel
from .geometry_utils import cross, vec3

#: Execution backend of the shared aerodynamic kernel for this scalar path.
#: The formulas live in :mod:`physics.aero_kernel` and are written once; the
#: pure-Python backend evaluates them without allocating a single array, which
#: matters because the 6-DoF integrator evaluates the aerodynamic model four
#: times per RK4 step.
_SCALAR = aero_kernel.ScalarMath

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
    "POST_STALL_PLATE_BLEND_DEG",
    "POST_STALL_DRAG_BLEND_DEG",
    "MAX_ABS_LIFT_COEFFICIENT",
    "STALL_BLEND_ORDER",
]

#: Echoed in every payload so the UI never has to guess how the numbers were
#: produced.  This domain is scalar pure Python maths, hence ``numpy``.
AERO_BACKEND: str = "numpy"

#: Reference Reynolds number of the ``cd0`` correlation.
RE_REFERENCE: float = 1.0e5

#: Versioned default Reynolds number for the published drag polar.
#: This is the order-of-magnitude of the best-glide (vertex) condition
#: (``V approx 2 m/s``), not the cruise operating point (``V approx 8-12 m/s``).
#: The default is intentionally kept at ``1.0e4`` so the published polar's
#: numeric contract is stable across versions; callers operating off the vertex
#: should pass ``reynolds`` explicitly.  Changing this default is a versioned,
#: next-phase decision (see ``no-finish.md`` section A-6).
DRAG_POLAR_DEFAULT_REYNOLDS: float = 1.0e4

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
#: Matches the physical +/-45 deg elevon travel limit (the guard is the
#: constraint, not the configured trim authority).
ELEVON_MAX_DEG: float = 45.0

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

    Thin wrapper around :func:`physics.aero_kernel.helmbold_cl_alpha`, which is
    the single implementation of this correlation in the project.  The GPU
    paths used to carry a truncated variant of this formula; routing every
    caller through one function is what removed that 11.3 % slope error.
    """
    return aero_kernel.helmbold_cl_alpha(aspect_ratio, taper_ratio)


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
        if math.isnan(value):
            return 1.0
        return float(aero_kernel.prandtl_glauert(_SCALAR, MACH_CRITICAL)) if value > 0.0 else 1.0
    return float(aero_kernel.prandtl_glauert(_SCALAR, value))


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
    return float(aero_kernel.ground_effect_factor(_SCALAR, height_m, span_m, True))


def parasite_drag_coefficient(cfg: AircraftConfig, reynolds: float) -> float:
    """Reynolds-corrected parasite drag ``C_D0 = cd0 (1e5 / Re) ** exponent``."""
    return float(
        aero_kernel.parasite_drag_coefficient(
            _SCALAR, _finite(cfg.cd0, 0.02), reynolds, _finite(cfg.re_cd0_exponent, 0.0)
        )
    )


def wave_drag_coefficient(mach: float, compressible: bool = True) -> float:
    """Transonic wave-drag increment (exactly ``0.0`` below :data:`MACH_TRANSONIC`).

    Monotone and finite at any Mach number; the supersonic term keeps the
    empirical drag-divergence trend growing instead of collapsing like
    ``1 - M^2`` would.
    """
    return float(aero_kernel.wave_drag_coefficient(_SCALAR, mach, bool(compressible)))


# --------------------------------------------------------------------------
# 3. Lift coefficient and the stall blend
# --------------------------------------------------------------------------
def effective_stall_alpha_deg(cfg: AircraftConfig) -> float:
    """Stall angle of attack modified by fold step turbulator trip wire.

    When ``cfg.step_trip_ratio > 0``, the fold step acts as a boundary layer trip wire,
    preventing laminar bubble burst and delaying full stall separation by up to several degrees.

    The airframe is symmetric, so the same angle applies in both directions.
    The previous implementation used a wider *negative* threshold
    (``max(stall + 15, 30)``) - an empirical recovery assumption with no
    source in any paper - which made the model stall at 14 deg nose-up but
    30 deg nose-down and systematically over-predicted inverted recovery.
    """
    return float(aero_kernel.effective_stall_alpha_deg(cfg))


def polhamus_lift_coefficient(alpha_deg: float, cfg: AircraftConfig, kp: Optional[float] = None) -> float:
    """Polhamus suction analogy leading-edge vortex (LEV) lift coefficient.

    ``C_L_polhamus = K_p * sin(alpha) * cos(alpha)^2 + K_v * sin(alpha)^2 * cos(alpha)``
    where ``K_p`` is the potential lift factor (proportional to ``lift_slope_per_rad``) and
    ``K_v`` is ``cfg.polhamus_kv`` representing vortex suction force on slender/delta wings.
    """
    alpha_rad = deg2rad(_finite(alpha_deg))
    cos_a = math.cos(alpha_rad)
    sin_a = math.sin(alpha_rad)
    if kp is None:
        explicit = max(_finite(getattr(cfg, "polhamus_kp", 0.0), 0.0), 0.0)
        k_p = explicit if explicit > 0.0 else lift_slope_per_rad(cfg, compressible=False)
    else:
        k_p = float(kp)
    k_v = max(_finite(cfg.polhamus_kv, 0.0), 0.0)
    return k_p * sin_a * (cos_a ** 2) + k_v * (sin_a ** 2) * cos_a


def stall_blend_weight(alpha_deg: float, cfg: AircraftConfig) -> float:
    """Smooth logistic blend weight between the linear and post-stall branch.

    ``1`` deep post-stall, ``0`` in attached flow.  The weight is exactly
    zero at and below the stall angle, so the attached-flow branch reproduces
    ``cL0 + cLalpha alpha`` to machine precision there (the plain logistic is
    only numerically indistinguishable from zero about 15 deg below the
    stall, which is not enough).  Above the stall the logistic^4 shape is
    retained - it is 1.65x sharper than a plain sigmoid - and is renormalised
    so that it starts from exactly zero at the break.

    Thin wrapper around :func:`physics.aero_kernel.stall_blend_weight`, which
    is the single implementation of the blend: the GPU paths use the same
    kernel, so the stall break has the same shape in every path.
    """
    return float(
        aero_kernel.stall_blend_weight(
            _SCALAR,
            alpha_deg,
            effective_stall_alpha_deg(cfg),
            max(abs(_finite(cfg.stall_sharpness, 1.4)), 1.0e-6),
            STALL_BLEND_ORDER,
        )
    )


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


#: Angle (deg) at which the post-stall branch has fully moved onto the
#: Hoerner fully separated flat-plate limit ``2 sin(a) cos^2(a)``.  The
#: transition *starts* at the stall angle, so the lift curve can never
#: collapse onto an exact ``C_L == 0`` band between the stall break and the
#: plate limit.
POST_STALL_PLATE_BLEND_DEG = 45.0

#: Width (deg) of the smoothstep that moves the separated pressure drag from
#: zero at the stall angle to the full Hoerner value.
POST_STALL_DRAG_BLEND_DEG = 15.0

#: Hard ceiling on |C_L|.  A thin aerofoil section cannot exceed roughly
#: 2*sin(a)*cos^2(a) + a modest attached-flow contribution, so anything beyond
#: this is numerical, not physical, and is clipped.
MAX_ABS_LIFT_COEFFICIENT = 4.0


def _smoothstep01(value: float) -> float:
    """Hermite smoothstep clamped to ``[0, 1]`` (C1 continuous)."""
    t = clamp(_finite(value), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _post_stall_lift_coefficient(alpha: float, cfg: AircraftConfig, linear_at_stall: float) -> float:
    """Post-stall lift anchored on the Hoerner flat-plate limit.

    The branch starts at the airframe's own stall value ``linear_at_stall``
    and moves onto the fully separated plate curve ``2 sin(a) cos^2(a)``
    (Hoerner 1965) over ``[alpha_stall, POST_STALL_PLATE_BLEND_DEG]``.

    The transition target is the plate value *at the blend angle*, not the
    moving plate curve itself.  Blending towards the moving curve produced a
    spurious dip near 30 deg followed by a secondary peak near 41 deg, because
    the plate curve itself rises to its 35.26 deg maximum while the attached
    branch falls.  With a fixed target the composite is strictly decreasing
    from the stall to the blend angle and joins the plate curve exactly, so
    the lift can never collapse onto an exact ``C_L == 0`` band (the defect
    that produced a lift-free, drag-only deep stall: a separated plate at
    41 deg still carries the Hoerner normal force 0.75, not nothing).

    An airframe with no attached-flow lift at all (``cL0 = 0`` and no lift
    slope) has no post-stall lift either: the flat-plate term is a property
    of the airframe's own lift capability.

    Thin wrapper around :func:`physics.aero_kernel.post_stall_lift_coefficient`.
    """
    return float(
        aero_kernel.post_stall_lift_coefficient(
            _SCALAR, alpha, linear_at_stall, effective_stall_alpha_deg(cfg)
        )
    )


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

    Thin wrapper around :func:`physics.aero_kernel.lift_coefficient`, the
    single implementation of the lift curve.  The GPU paths evaluate the very
    same kernel, so the lift curve of an airframe no longer depends on
    whether a GPU was present.
    """
    params = replace(
        aero_kernel.params_from_aircraft(cfg), compressible=bool(compressible)
    )
    slope = params.lift_slope(_SCALAR, mach)
    return float(
        aero_kernel.lift_coefficient(
            _SCALAR,
            alpha_deg,
            params.cL0,
            slope,
            params.stall_alpha_deg,
            params.stall_sharpness,
            cl_max=params.cl_max,
            polhamus_kp=params.polhamus_kp,
            polhamus_kv=params.polhamus_kv,
            cl_offset=cl_offset,
        )
    )


def separated_pressure_drag_coefficient(
    alpha_deg: float, cfg: AircraftConfig, reynolds: float = RE_REFERENCE
) -> float:
    """Separated-flow pressure drag, Hoerner (1965): ``2 sin^2(a) + C_D0 cos(a)``.

    The first term is the broadside-plate projected-area drag, the second the
    parasite drag resolved onto the chord of the separated plate.  The
    parasite term is dropped for reverse flow (``cos(a) <= 0``): a separated
    plate beyond 90 deg sees no chord-aligned flow and a negative drag term
    would make the body gain energy.

    The attached polar is joined by a C1 smoothstep that reaches the full
    separated value ``POST_STALL_DRAG_BLEND_DEG`` past the stall angle, so the
    drag builds up immediately behind the stall break instead of only at
    90 deg.  The former transition (0 at the stall, 1 at 90 deg) made the
    post-stall drag five times too small at 30 deg, so the polar had no
    stall     valley at all.  ``reynolds`` selects the Reynolds-corrected parasite
    drag, which keeps the separated branch consistent with the attached polar.

    Thin wrapper around :func:`physics.aero_kernel.separated_pressure_drag_coefficient`.
    """
    return float(
        aero_kernel.separated_pressure_drag_coefficient(
            _SCALAR,
            alpha_deg,
            effective_stall_alpha_deg(cfg),
            parasite_drag_coefficient(cfg, reynolds),
        )
    )


def drag_coefficient(
    alpha_deg: float,
    cfg: AircraftConfig,
    reynolds: float = DRAG_POLAR_DEFAULT_REYNOLDS,
    ground_height_m: Optional[float] = None,
    mach: float = 0.0,
    compressible: bool = True,
    cl_offset: float = 0.0,
) -> float:
    """Attached polar plus separated pressure drag and wave drag.

    ``ground_height_m=None`` evaluates the free-air polar; a height attenuates
    the **induced** part only, because a ground effect does not change the
    profile drag.

    ``reynolds`` defaults to the versioned :data:`DRAG_POLAR_DEFAULT_REYNOLDS`
    (the vertex/best-glide condition).  Callers operating off that point should
    pass ``reynolds`` explicitly; the default is intentionally fixed so the
    published polar's numeric contract is stable across versions.
    """
    te_fold = trailing_edge_fold_effects(cfg)
    cl = lift_coefficient(alpha_deg, cfg, compressible=compressible, mach=mach, cl_offset=cl_offset)
    induced = cl * cl / _polar_constant(cfg)
    factor = 1.0 if ground_height_m is None else ground_effect_factor(ground_height_m, _span(cfg), True)
    return (
        parasite_drag_coefficient(cfg, reynolds)
        + te_fold["dcd"]
        + induced * factor
        + separated_pressure_drag_coefficient(alpha_deg, cfg, reynolds)
        + wave_drag_coefficient(mach, compressible)
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
    effects = aero_kernel.elevon_effects(
        _SCALAR,
        elevon_deg,
        elevon_authority(cfg),
        _finite(cfg.cm0, 0.0),
        _finite(cfg.cm_alpha, 0.0),
    )
    return {key: float(value) for key, value in effects.items()}


def trim_alpha_from_elevon(cfg: AircraftConfig, elevon_deg: float) -> float:
    """Closed-form angle of attack that nulls the static pitching moment."""
    elevon = elevon_effects(elevon_deg, cfg)
    te_fold = trailing_edge_fold_effects(cfg)
    cm_alpha = _finite(cfg.cm_alpha)
    if abs(cm_alpha) < 1.0e-12:
        return 0.0
    cm_static = _finite(cfg.cm0) + elevon["dcm"] + te_fold["dcm"]
    return float(rad2deg(-cm_static / cm_alpha))


def elevon_for_trim(cfg: AircraftConfig, alpha_deg: float) -> float:
    """Elevon deflection that nulls the static moment at ``alpha_deg``."""
    gain = elevon_authority(cfg) * ELEVON_MOMENT_PER_DEG
    if gain <= 1.0e-12:
        return 0.0
    te_fold = trailing_edge_fold_effects(cfg)
    required = -(_finite(cfg.cm0) + _finite(cfg.cm_alpha) * deg2rad(alpha_deg) + te_fold["dcm"])
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
    effects = aero_kernel.trailing_edge_fold_effects(_SCALAR, fold_deg)
    return {key: float(value) for key, value in effects.items()}


# --------------------------------------------------------------------------
# 5. Drag polar
# --------------------------------------------------------------------------
def drag_polar_minima(
    cfg: AircraftConfig,
    reynolds: float = DRAG_POLAR_DEFAULT_REYNOLDS,
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
    te_fold = trailing_edge_fold_effects(cfg)
    cd0 = parasite_drag_coefficient(cfg, reynolds) + te_fold["dcd"]
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
    reynolds: float = DRAG_POLAR_DEFAULT_REYNOLDS,
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
    te_fold = trailing_edge_fold_effects(cfg)
    elevon = elevon_effects(cfg.elevon_deg, cfg)
    total_cl_offset = elevon["dcl"] + te_fold["dcl"]
    cols_cl: List[float] = []
    cols_cd: List[float] = []
    cols_ld: List[float] = []
    for alpha in alphas:
        cl = lift_coefficient(alpha, cfg, compressible=False, mach=0.0, cl_offset=total_cl_offset)
        cd = drag_coefficient(alpha, cfg, reynolds=reynolds, ground_height_m=None, cl_offset=total_cl_offset)
        cols_cl.append(cl)
        cols_cd.append(cd)
        cols_ld.append(safe_div(cl, cd, 0.0) if cd > _CD_FLOOR else 0.0)

    best_cd = min(range(count), key=lambda i: cols_cd[i])
    best_ld = max(range(count), key=lambda i: cols_ld[i])
    zero_lift = _finite(cfg.cL0) + total_cl_offset
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

    Thin wrapper around :func:`physics.aero_kernel.center_of_pressure_fraction`.
    """
    chord = max(_finite(_geom(cfg)["ref_length_m"], 0.075), 1.0e-9)
    fraction = float(
        aero_kernel.center_of_pressure_fraction(
            _SCALAR, alpha_deg, effective_stall_alpha_deg(cfg), _finite(cfg.cp_shift_gain, 0.0)
        )
    )
    return float(fraction * chord)


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
    # The side force is bounded: a body broadside to the flow carries the
    # flat-plate limit (2 sin(beta) cos^2(beta) <= 0.77), not an unbounded
    # linear function of beta.  The audit requires the coefficients to be
    # clamped and wrapped over beta in [-90, 90] with no singularity.
    beta = clamp(_finite(beta_deg), -90.0, 90.0)
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

    # The coefficient evaluation itself lives in :mod:`physics.aero_kernel`,
    # the vectorised kernel that is shared with the GPU paths.  Evaluating it
    # here through the same code is what makes the 6-DoF path and the GPU
    # batch agree on the lift, the drag and the moments of the *same*
    # airframe instead of evaluating two look-alike models.
    params = aero_kernel.params_from_config(cfg, env)
    # §A-4: expose whether lift_scale / drag_scale are actively calibrating the
    # forces (non-neutral values must be visible in the response payload).
    calibration_active = bool(params.lift_scale != 1.0 or params.drag_scale != 1.0)
    p_rate, q_rate, r_rate = (float(component) for component in vec3(angular_rate))
    coefficients = aero_kernel.evaluate_coefficients(
        _SCALAR,
        params,
        alpha_deg=alpha,
        beta_deg=beta,
        p_rate=p_rate,
        q_rate=q_rate,
        r_rate=r_rate,
        speed=speed,
        reynolds=re_value,
        mach=mach_value,
        ground_height_m=ground_height_m,
        density=density,
    )
    cl = float(coefficients["cl"])
    cd = float(coefficients["cd"])
    cy = float(coefficients["cy"])
    cm = float(coefficients["cm"])
    cl_roll = float(coefficients["cl_roll"])
    cm_roll = float(coefficients["cm_roll"])
    cn_roll = float(coefficients["cn_roll"])
    ground_ratio = float(coefficients["ground_ratio"])
    lift_n = float(coefficients["lift_n"])
    drag_n = float(coefficients["drag_n"])
    side_n = float(coefficients["side_n"])
    lift_left_n = float(coefficients["lift_left_n"])
    lift_right_n = float(coefficients["lift_right_n"])
    dynamic_pressure = float(coefficients["q_dyn"])
    pitching_nm = float(coefficients["pitching_nm"])
    rolling_nm = float(coefficients["rolling_nm"])
    yawing_nm = float(coefficients["yawing_nm"])
    cp_x = float(coefficients["cp_x_m"])
    stall_margin = float(coefficients["stall_margin"])
    stalled = bool(coefficients["stalled"])
    zero_lift = float(coefficients["cl_zero_lift"])

    # Aeroelastic deformation estimates from dynamic pressure and stiffness
    stiffness = max(_finite(cfg.paper_stiffness, 0.35), 0.01)
    # Reference aeroelastic dynamic pressure scale: q_ref ~ 50 Pa for typical paper glider
    q_scale = safe_div(dynamic_pressure, 50.0 * stiffness, 0.0)
    aeroelastic_dihedral = _finite(cfg.aeroelastic_flex, 0.0) * q_scale * 2.5
    aeroelastic_twist = -_finite(cfg.aeroelastic_twist, 0.0) * q_scale * 1.5

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
        calibration_active=calibration_active,
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
