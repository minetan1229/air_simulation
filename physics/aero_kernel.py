"""Vectorised aerodynamic kernel -- the single source of truth.

Why this module exists
----------------------
The 6-DoF path (:mod:`physics.flight_sim` -> :func:`physics.aero_domain.evaluate_aero`)
and the vectorised GPU paths (:mod:`physics.parallel_engine`) used to carry two
independent look-alike aerodynamics.  They drifted apart until the GPU batch
evaluated *a different airplane*: a different lift-curve slope, a discontinuous
stall that jumped the lift by 55 %, a drag polar that could not reach
``C_Dmax``, a rotated wind convention and a hard-coded trim angle.  The
audited symptom was a 1261x disagreement in the reported wing-root stress
between the two paths for the very same airframe and launch.

Every aerodynamic relation now lives **here**, once, in a form that runs
unchanged on NumPy (scalar / CPU 6-DoF) and on CuPy (vectorised GPU).  The
scalar path and the GPU path therefore evaluate *the same numbers*: the
remaining difference between them is the integrator and the ground contact,
never the coefficients.

Usage
-----
Every function takes the array module (``xp``) as its first argument so the
caller decides where the arithmetic happens::

    params = aero_kernel.AeroParams(...)      # scalars or arrays
    out = aero_kernel.evaluate_coefficients(
        np, params, alpha_deg=2.0, beta_deg=0.0,
        p_rate=0.0, q_rate=0.0, r_rate=0.0,
        speed=8.0, reynolds=4.0e4, mach=0.02,
        ground_height_m=1.0, density=1.225,
    )

Passing ``cupy`` instead of ``numpy`` evaluates the identical expressions on
the GPU.  All array-valued arguments broadcast against each other, so a
single scalar airframe and a batch of airframes are handled by the same code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict

from .config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    clamp,
    derived_properties,
)

__all__ = [
    "AeroParams",
    "ScalarMath",
    "RE_REFERENCE",
    "STALL_BLEND_ORDER",
    "ELEVON_LIFT_PER_DEG",
    "ELEVON_MOMENT_PER_DEG",
    "MAX_ABS_LIFT_COEFFICIENT",
    "POST_STALL_PLATE_BLEND_DEG",
    "POST_STALL_DRAG_BLEND_DEG",
    "MACH_TRANSONIC",
    "MACH_CRITICAL",
    "helmbold_cl_alpha",
    "prandtl_glauert",
    "ground_effect_factor",
    "parasite_drag_coefficient",
    "wave_drag_coefficient",
    "stall_blend_weight",
    "flat_plate_lift_coefficient",
    "post_stall_lift_coefficient",
    "lift_coefficient",
    "separated_pressure_drag_coefficient",
    "drag_coefficient",
    "elevon_effects",
    "center_of_pressure_fraction",
    "evaluate_coefficients",
    "effective_stall_alpha_deg",
    "params_from_config",
]

#: Reference Reynolds number of the ``cd0`` correlation.
RE_REFERENCE: float = 1.0e5

#: Exponent of the logistic stall blend.  The plain logistic
#: ``1 / (1 + exp(-k d))`` is numerically indistinguishable from the linear
#: branch only about 15 deg below the stall; raising it to a small integer
#: power keeps the blend smooth and centred on the stall angle while making
#: the weight decay like ``exp(-order k |d|)``.
STALL_BLEND_ORDER: int = 4

#: Elevon effectiveness.  ``dcl`` grows with a trailing-edge-down (positive)
#: deflection, ``dcm`` is the resulting **nose-down** pitching increment.
ELEVON_LIFT_PER_DEG: float = 0.020
ELEVON_MOMENT_PER_DEG: float = 0.030

#: Hard ceiling on |C_L| -- a numerical safety net, not a physical constant.
MAX_ABS_LIFT_COEFFICIENT: float = 4.0

#: Angle (deg) at which the post-stall branch has fully moved onto the
#: Hoerner fully separated flat-plate limit ``2 sin(a) cos^2(a)``.
POST_STALL_PLATE_BLEND_DEG: float = 45.0

#: Width (deg) of the smoothstep that moves the separated pressure drag from
#: zero at the stall angle to the full Hoerner value.
POST_STALL_DRAG_BLEND_DEG: float = 15.0

#: Mach number at which the transonic wave-drag increment switches on.
MACH_TRANSONIC: float = 0.75

#: Mach number at which the Prandtl-Glauert factor stops rising.
MACH_CRITICAL: float = 0.85

_WAVE_DRAG_GAIN: float = 0.25
_SUPERSONIC_DRAG_GAIN: float = 0.15

_EPS: float = 1.0e-12

_ABS = abs
_MIN = min
_MAX = max
_COPYSIGN = math.copysign


class ScalarMath:
    """Scalar execution backend of the kernel.

    The kernel formulas are written once against an array module ``xp``.
    This backend provides the same small set of primitives for plain Python
    floats, using :mod:`math` only -- no array allocation, no dtype
    dispatch, no boxing.

    The scalar 6-DoF path evaluates the aerodynamic model four times per
    RK4 step for every airframe; going through NumPy 0-d arrays for that
    costs about thirty times more than the arithmetic itself, so the
    reference path uses this backend and the batch path uses NumPy or CuPy.
    The *formulas* are the same in both, which is the point.
    """

    float64 = float
    newaxis = None

    @staticmethod
    def asarray(value: Any, dtype: Any = None) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def ones_like(value: Any) -> float:
        return 1.0

    @staticmethod
    def zeros_like(value: Any) -> float:
        return 0.0

    @staticmethod
    def clip(value: float, low: float, high: float) -> float:
        if value < low:
            return low
        if value > high:
            return high
        return value

    @staticmethod
    def where(condition: Any, first: Any, second: Any) -> Any:
        return first if condition else second

    @staticmethod
    def abs(value: float) -> float:
        return _ABS(value)

    @staticmethod
    def maximum(first: float, second: float) -> float:
        return _MAX(first, second)

    @staticmethod
    def minimum(first: float, second: float) -> float:
        return _MIN(first, second)

    @staticmethod
    def copysign(first: float, second: float) -> float:
        return _COPYSIGN(first, second)

    @staticmethod
    def isfinite(value: float) -> bool:
        return math.isfinite(value)

    @staticmethod
    def nan_to_num(
        value: float, nan: float = 0.0, posinf: Any = None, neginf: Any = None
    ) -> float:
        if math.isfinite(value):
            return float(value)
        if math.isnan(value):
            return float(nan)
        if value > 0.0:
            return float(posinf) if posinf is not None else float(value)
        return float(neginf) if neginf is not None else float(value)

    @staticmethod
    def sin(value: float) -> float:
        return math.sin(value)

    @staticmethod
    def cos(value: float) -> float:
        return math.cos(value)

    @staticmethod
    def exp(value: float) -> float:
        return math.exp(value)

    @staticmethod
    def sqrt(value: float) -> float:
        return math.sqrt(value)

    @staticmethod
    def tanh(value: float) -> float:
        return math.tanh(value)

    @staticmethod
    def arctan2(first: float, second: float) -> float:
        return math.atan2(first, second)

    @staticmethod
    def arcsin(value: float) -> float:
        return math.asin(value)

    @staticmethod
    def degrees(value: float) -> float:
        return math.degrees(value)

    @staticmethod
    def power(base: float, exponent: float) -> float:
        return base ** exponent



# --------------------------------------------------------------------------
# Array-module agnostic primitives
# --------------------------------------------------------------------------
def _clamp(xp: Any, value: Any, low: Any, high: Any) -> Any:
    """``clip`` that also accepts plain Python floats.

    The value is converted to an array first: ``cupy.clip`` has no method on
    a Python float, so a scalar argument would raise ``AttributeError`` on the
    GPU path while working on the NumPy path.
    """
    return xp.clip(xp.asarray(value, dtype=xp.float64), low, high)


def _safe_div(xp: Any, numerator: Any, denominator: Any, default: Any = 0.0) -> Any:
    """``safe_div`` for arrays: zero denominators yield ``default``."""
    den = xp.asarray(denominator, dtype=xp.float64)
    num = xp.asarray(numerator, dtype=xp.float64)
    good = xp.abs(den) > _EPS
    safe_den = xp.where(good, den, xp.ones_like(den))
    return xp.where(good, num / safe_den, default)


def _rad(xp: Any, value: Any) -> Any:
    """Degrees to radians, array aware (``config_schema.deg2rad`` is scalar)."""
    if isinstance(value, float):
        return value * (math.pi / 180.0)
    return xp.asarray(value, dtype=xp.float64) * (math.pi / 180.0)


def _logistic(xp: Any, value: Any) -> Any:
    """Overflow-free logistic ``1 / (1 + exp(-x))``.

    ``xp.where`` evaluates *both* branches, so every exponential has to be
    safe on its own: the positive branch only ever sees a non-positive
    argument (``exp(-x)``) and the negative branch only ever sees ``exp(x)``
    with ``x <= 0``.  Clamping the arguments of both exponentials keeps the
    evaluation finite for every input, including the large positive offsets
    the stall blend produces.
    """
    x = xp.asarray(value, dtype=xp.float64)
    positive = x >= 0.0
    exp_pos = xp.exp(-xp.maximum(x, -700.0))
    exp_neg = xp.exp(xp.minimum(x, 0.0))
    return xp.where(positive, 1.0 / (1.0 + exp_pos), exp_neg / (1.0 + exp_neg))


def _smoothstep01(xp: Any, value: Any) -> Any:
    """Hermite smoothstep clamped to ``[0, 1]`` (C1 continuous)."""
    t = _clamp(xp, value, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _finite(xp: Any, value: Any, default: float = 0.0) -> Any:
    """Replace NaN / inf by ``default`` (array aware).

    A plain finite float -- the overwhelmingly common case on the scalar
    6-DoF path -- is returned unchanged, without touching the array module.
    """
    if isinstance(value, float):
        if value == value and -1.0e308 < value < 1.0e308:
            return value
    array = xp.asarray(value, dtype=xp.float64)
    return xp.nan_to_num(array, nan=default, posinf=default, neginf=default)


# --------------------------------------------------------------------------
# 1. Lift-curve slope
# --------------------------------------------------------------------------
def helmbold_cl_alpha(aspect_ratio: float, taper_ratio: float) -> float:
    """Helmbold (1942) lift-curve slope ``dC_L / dalpha`` per radian.

    ``C_Lalpha = 2 pi AR / (2 + sqrt(AR^2 (1 + lambda) + 4 AR / pi))``

    This is the **only** lift-slope correlation in the project.  The GPU paths
    used to carry the truncated ``2 pi AR / (2 + sqrt(AR^2 + 4))`` form, which
    dropped the taper term and replaced ``4 AR / pi`` by ``4`` -- an 11.3 %
    slope error that silently changed the trim angle whenever a GPU was
    present.
    """
    aspect = max(float(aspect_ratio), 1.0e-3)
    taper = clamp(float(taper_ratio), 0.01, 1.0)
    denominator = 2.0 + math.sqrt(aspect * aspect * (1.0 + taper) + 4.0 * aspect / math.pi)
    return 2.0 * math.pi * aspect / denominator


def prandtl_glauert(xp: Any, mach: Any) -> Any:
    """Prandtl-Glauert compressibility factor ``1 / sqrt(1 - M^2)``."""
    value = xp.abs(_finite(xp, mach, 0.0))
    effective = xp.minimum(value, MACH_CRITICAL)
    return 1.0 / xp.sqrt(xp.maximum(1.0 - effective * effective, 1.0e-12))


# --------------------------------------------------------------------------
# 2. Ground effect, parasite drag, wave drag
# --------------------------------------------------------------------------
def ground_effect_factor(xp: Any, height_m: Any, span_m: Any, enabled: Any = True) -> Any:
    """Induced-drag retention ``16 (h/b)^2 / (1 + 16 (h/b)^2)``.

    The multiplier is ``1.0`` in free air and when the model is disabled, so
    switching the model off restores the free-air polar instead of deleting
    the induced drag.
    """
    enabled = xp.asarray(enabled)
    height = xp.maximum(_finite(xp, height_m, 0.0), 0.0)
    span = xp.maximum(_finite(xp, span_m, 0.2), 1.0e-9)
    ratio = 16.0 * (height / span) ** 2
    ratio = xp.where(xp.isfinite(ratio), ratio, 1.0)
    factor = ratio / (1.0 + ratio)
    return xp.where(enabled, factor, 1.0)


def parasite_drag_coefficient(xp: Any, cd0: Any, reynolds: Any, exponent: Any) -> Any:
    """Reynolds-corrected parasite drag ``C_D0 = cd0 (1e5 / Re) ** exponent``."""
    value = xp.maximum(xp.abs(_finite(xp, reynolds, RE_REFERENCE)), 1.0)
    return _finite(xp, cd0, 0.02) * (RE_REFERENCE / value) ** _finite(xp, exponent, 0.0)


def wave_drag_coefficient(xp: Any, mach: Any, compressible: Any = True) -> Any:
    """Transonic wave-drag increment (exactly ``0.0`` below transonic)."""
    compressible = xp.asarray(compressible)
    value = xp.abs(_finite(xp, mach, 0.0))
    excess = xp.maximum(value - MACH_TRANSONIC, 0.0)
    supersonic = xp.maximum(value - 1.0, 0.0)
    increment = _WAVE_DRAG_GAIN * excess * excess + _SUPERSONIC_DRAG_GAIN * supersonic ** 3
    return xp.where(compressible, increment, 0.0)


# --------------------------------------------------------------------------
# 3. Stall blend and lift
# --------------------------------------------------------------------------
def stall_blend_weight(
    xp: Any,
    alpha_deg: Any,
    stall_alpha_deg: Any,
    sharpness: Any,
    order: int = STALL_BLEND_ORDER,
) -> Any:
    """Smooth logistic blend weight between the linear and post-stall branch.

    ``1`` deep post-stall, ``0`` in attached flow.  The weight is *exactly*
    zero at and below the stall angle, so the attached-flow branch reproduces
    ``cL0 + C_Lalpha alpha`` to machine precision there, and the transition is
    a smooth function of the angle of attack -- the GPU path used to switch
    branches with a hard comparison, which dropped the lift by 55 % in a
    single step and acted like an impulsive pitching torque.

    The airframe is symmetric, so the same angle applies in both directions.
    """
    alpha = _finite(xp, alpha_deg, 0.0)
    stall_alpha = xp.abs(_finite(xp, stall_alpha_deg, 14.0))
    offset = xp.abs(alpha) - stall_alpha
    floor = 0.5 ** int(order)
    base = _logistic(xp, xp.maximum(xp.abs(_finite(xp, sharpness, 1.4)), 1.0e-6) * offset) ** int(order)
    weight = (base - floor) / (1.0 - floor)
    return xp.where(offset > 0.0, _clamp(xp, weight, 0.0, 1.0), 0.0)


def flat_plate_lift_coefficient(xp: Any, alpha_deg: Any) -> Any:
    """Hoerner fully separated flat-plate lift ``2 sin(a) cos^2(a)``."""
    angle = _rad(xp, _finite(xp, alpha_deg, 0.0))
    return 2.0 * xp.sin(angle) * xp.cos(angle) * xp.cos(angle)


def post_stall_lift_coefficient(
    xp: Any,
    alpha_deg: Any,
    ref_cl: Any,
    stall_alpha_deg: Any,
    blend_deg: Any = POST_STALL_PLATE_BLEND_DEG,
) -> Any:
    """Post-stall lift anchored on the Hoerner flat-plate limit.

    The branch starts at the airframe's own stall value ``ref_cl`` and moves
    onto the fully separated plate curve ``2 sin(a) cos^2(a)`` over
    ``[alpha_stall, blend_deg]``.

    The transition target is the plate value *at the blend angle*, not the
    moving plate curve itself.  Blending towards the moving curve produced a
    spurious dip near 30 deg followed by a secondary peak near 41 deg,
    because the plate curve itself rises to its 35.26 deg maximum while the
    attached branch falls.  With a fixed target the composite is strictly
    decreasing from the stall to the blend angle and joins the plate curve
    exactly, so the lift can never collapse onto an exact ``C_L == 0`` band
    (the defect that produced a lift-free, drag-only deep stall: a separated
    plate at 41 deg still carries the Hoerner normal force 0.75, not
    nothing).

    An airframe with no attached-flow lift at all (``ref_cl = 0``) has no
    post-stall lift either: the flat-plate term is a property of the
    airframe's own lift capability.
    """
    alpha = _finite(xp, alpha_deg, 0.0)
    sign = xp.where(alpha >= 0.0, 1.0, -1.0)
    abs_alpha = xp.abs(alpha)
    stall_alpha = xp.abs(_finite(xp, stall_alpha_deg, 14.0))
    reference = xp.abs(_finite(xp, ref_cl, 0.0))
    blend_angle = _finite(xp, blend_deg, POST_STALL_PLATE_BLEND_DEG)
    plate = flat_plate_lift_coefficient(xp, alpha)
    # The blend target is the *magnitude* of the plate value at the blend
    # angle.  Using the signed value (``sign * blend_angle``) made the
    # negative branch blend from +reference towards -|target| and the
    # magnitude collapsed (0.690 -> 0.553 at 20 deg), which is exactly the
    # spurious dip the fixed target was introduced to remove.
    target = xp.abs(flat_plate_lift_coefficient(xp, blend_angle))
    blend = _smoothstep01(
        xp, (abs_alpha - stall_alpha) / xp.maximum(blend_angle - stall_alpha, 1.0)
    )
    ramp = sign * (reference + (target - reference) * blend)
    return xp.where(
        reference <= 1.0e-9,
        0.0,
        xp.where(abs_alpha <= stall_alpha, sign * reference,
                 xp.where(abs_alpha >= blend_angle, plate, ramp)),
    )


def lift_coefficient(
    xp: Any,
    alpha_deg: Any,
    cL0: Any,
    slope_per_rad: Any,
    stall_alpha_deg: Any,
    sharpness: Any,
    cl_max: Any = 0.90,
    polhamus_kp: Any = 0.0,
    polhamus_kv: Any = 0.0,
    cl_offset: Any = 0.0,
    blend_order: int = STALL_BLEND_ORDER,
) -> Any:
    """Lift coefficient including the stall break and the elevon offset.

    The attached-flow branch is limited by the airframe's maximum lift
    coefficient: ``cl_max`` *is* the incidence at which the wing stalls, so
    the linear extrapolation must not run past it.

    The Polhamus suction analogy (1966/1971), published relation
    ``C_L = K_p sin(a) cos^2(a) + K_v sin^2(a) cos(a)``, is supported in two
    modes: with ``polhamus_kp > 0`` the attached branch *is* the published
    relation; otherwise the airframe's own three-dimensional Helmbold slope
    provides the potential term (a low aspect-ratio wing must not be given
    the two-dimensional slope ``2 pi`` on top of the finite-wing correction)
    and only the LEV suction increment is added.  The published term already
    carries the correct sign -- ``sin^2(a) cos(a)`` is negative for
    ``|a| > 90 deg`` -- so no ``copysign`` is applied.
    """
    alpha = _finite(xp, alpha_deg, 0.0)
    slope = _finite(xp, slope_per_rad, 0.0)
    stall_alpha = xp.abs(_finite(xp, stall_alpha_deg, 14.0))
    zero_lift = _finite(xp, cL0, 0.0)
    offset = _finite(xp, cl_offset, 0.0)

    kp = xp.maximum(_finite(xp, polhamus_kp, 0.0), 0.0)
    kv = xp.maximum(_finite(xp, polhamus_kv, 0.0), 0.0)
    alpha_rad = _rad(xp, alpha)
    sin_a = xp.sin(alpha_rad)
    cos_a = xp.cos(alpha_rad)

    linear_default = zero_lift + slope * alpha_rad
    linear_polhamus = zero_lift + kp * sin_a * (cos_a ** 2) + kv * (sin_a ** 2) * cos_a
    linear = xp.where(kp > 0.0, linear_polhamus, linear_default)
    if True:  # the LEV increment is added on top of the Helmbold branch
        vortex = kv * (sin_a ** 2) * cos_a
        linear = xp.where((kp <= 0.0) & (kv > 0.0), linear + vortex, linear)

    stall_rad = _rad(xp, stall_alpha)
    linear_at_stall = zero_lift + slope * stall_rad
    stall_vortex = kv * (xp.sin(stall_rad) ** 2) * xp.cos(stall_rad)
    linear_at_stall = linear_at_stall + xp.where(kv > 0.0, stall_vortex, 0.0)

    ceiling = xp.maximum(xp.abs(_finite(xp, cl_max, 0.90)), 1.0e-6)
    attached = _clamp(xp, linear, -ceiling, ceiling)

    post_stall = post_stall_lift_coefficient(
        xp, alpha, linear_at_stall, stall_alpha
    )
    weight = stall_blend_weight(xp, alpha, stall_alpha, sharpness, blend_order)
    value = (1.0 - weight) * attached + weight * post_stall + offset
    hard_ceiling = MAX_ABS_LIFT_COEFFICIENT + xp.abs(offset)
    return _clamp(xp, value, -hard_ceiling, hard_ceiling)


def separated_pressure_drag_coefficient(
    xp: Any,
    alpha_deg: Any,
    stall_alpha_deg: Any,
    cd0: Any,
    blend_deg: Any = POST_STALL_DRAG_BLEND_DEG,
) -> Any:
    """Separated-flow pressure drag, Hoerner (1965): ``2 sin^2(a) + C_D0 cos(a)``.

    The first term is the broadside-plate projected-area drag, the second the
    parasite drag resolved onto the chord of the separated plate.  The
    parasite term is dropped for reverse flow (``cos(a) <= 0``).

    The attached polar is joined by a C1 smoothstep that reaches the full
    separated value ``blend_deg`` past the stall angle, so the drag builds up
    immediately behind the stall break instead of only at 90 deg.  The former
    transition (0 at the stall, 1 at 90 deg) made the post-stall drag five
    times too small at 30 deg, so the polar had no stall valley at all.
    """
    alpha = (_finite(xp, alpha_deg, 0.0) + 180.0) % 360.0 - 180.0
    stall_alpha = xp.abs(_finite(xp, stall_alpha_deg, 14.0))
    angle = _rad(xp, alpha)
    parasite = _finite(xp, cd0, 0.02)
    separated = 2.0 * xp.sin(angle) ** 2 + parasite * xp.maximum(xp.cos(angle), 0.0)
    weight = _smoothstep01(
        xp,
        (xp.abs(alpha) - stall_alpha)
        / xp.maximum(_finite(xp, blend_deg, POST_STALL_DRAG_BLEND_DEG), 1.0),
    )
    return weight * separated


def drag_coefficient(
    xp: Any,
    alpha_deg: Any,
    cL0: Any,
    slope_per_rad: Any,
    stall_alpha_deg: Any,
    sharpness: Any,
    cl_max: Any = 0.90,
    cd0: Any = 0.02,
    reynolds: Any = RE_REFERENCE,
    re_exponent: Any = 0.0,
    polar_constant: Any = 1.0,
    ground_ratio: Any = 1.0,
    mach: Any = 0.0,
    compressible: Any = True,
    cl_offset: Any = 0.0,
    polhamus_kp: Any = 0.0,
    polhamus_kv: Any = 0.0,
) -> Any:
    """Attached polar plus separated pressure drag and wave drag."""
    cl = lift_coefficient(
        xp,
        alpha_deg,
        cL0,
        slope_per_rad,
        stall_alpha_deg,
        sharpness,
        cl_max=cl_max,
        polhamus_kp=polhamus_kp,
        polhamus_kv=polhamus_kv,
        cl_offset=cl_offset,
    )
    parasite = parasite_drag_coefficient(xp, cd0, reynolds, re_exponent)
    induced = cl * cl / xp.maximum(_finite(xp, polar_constant, 1.0), 1.0e-12)
    separated = separated_pressure_drag_coefficient(
        xp, alpha_deg, stall_alpha_deg, parasite
    )
    wave = wave_drag_coefficient(xp, mach, compressible)
    return parasite + induced * _finite(xp, ground_ratio, 1.0) + separated + wave


# --------------------------------------------------------------------------
# 4. Elevon / trailing-edge fold
# --------------------------------------------------------------------------
def elevon_effects(
    xp: Any,
    elevon_deg: Any,
    authority: Any,
    cm0: Any = 0.0,
    cm_alpha: Any = 0.0,
) -> Dict[str, Any]:
    """Lift / pitching-moment increments of the elevon.

    A **positive** ``elevon_deg`` is a trailing-edge-down (push) deflection:
    it adds lift and produces a **nose-down** pitching increment, therefore
    demanding a larger angle of attack to trim.
    """
    deflection = _finite(xp, elevon_deg, 0.0)
    weight = xp.abs(_finite(xp, authority, 0.22))
    dcl = weight * ELEVON_LIFT_PER_DEG * deflection
    dcm = -weight * ELEVON_MOMENT_PER_DEG * deflection
    cm_alpha_value = _finite(xp, cm_alpha, 0.0)
    usable = xp.abs(cm_alpha_value) >= 1.0e-12
    dalpha = xp.where(
        usable,
        xp.degrees(-(_finite(xp, cm0, 0.0) + dcm) / xp.where(usable, cm_alpha_value, 1.0)),
        0.0,
    )
    return {"dcl": dcl, "dcm": dcm, "dalpha_trim_deg": dalpha}


def center_of_pressure_fraction(
    xp: Any, alpha_deg: Any, stall_alpha_deg: Any, cp_shift_gain: Any
) -> Any:
    """Centre-of-pressure position as a fraction of the reference chord.

    The thin-airfoil value ``0.25`` moves aft towards ``0.50`` as the flow
    separates, scaled by ``cp_shift_gain``.
    """
    alpha = xp.abs(_finite(xp, alpha_deg, 0.0))
    stall_alpha = xp.maximum(xp.abs(_finite(xp, stall_alpha_deg, 14.0)), 1.0)
    progression = _clamp(xp, alpha / stall_alpha, 0.0, 1.0)
    smooth = progression * progression * (3.0 - 2.0 * progression)
    gain = xp.minimum(xp.maximum(_finite(xp, cp_shift_gain, 0.0), 0.0), 1.0)
    return 0.25 + 0.25 * smooth * gain


# --------------------------------------------------------------------------
# 5. Parameter bundle
# --------------------------------------------------------------------------
@dataclass
class AeroParams:
    """Per-airframe aerodynamic parameters.

    Every field may be a scalar (single airframe) or an array (a batch of
    airframes evaluated simultaneously on the GPU).  The fields mirror the
    :class:`~physics.config_schema.AircraftConfig` attributes they come from;
    the ones that need a per-airframe correlation (the Helmbold slope, the
    trip-wire stall delay) are resolved *before* the bundle is built so that
    the kernel itself stays a pure function of its inputs.
    """

    area: Any = 0.015
    chord: Any = 0.075
    span: Any = 0.2
    aspect: Any = 2.667
    oswald: Any = 0.75
    cL0: Any = 0.0
    cl_alpha: Any = 0.0
    cl_max: Any = 0.90
    stall_alpha_deg: Any = 14.0
    stall_sharpness: Any = 1.4
    polhamus_kp: Any = 0.0
    polhamus_kv: Any = 0.0
    cd0: Any = 0.02
    re_cd0_exponent: Any = 0.0
    cm0: Any = 0.0
    cm_alpha: Any = 0.0
    cm_q: Any = 0.0
    cm_roll_rate: Any = 0.0
    cm_pitch_rate: Any = 0.0
    cm_yaw_rate: Any = 0.0
    cl_beta: Any = 0.0
    cl_roll_rate: Any = 0.0
    cl_pitch_rate: Any = 0.0
    cl_yaw_rate: Any = 0.0
    cn_beta: Any = 0.0
    cn_roll_rate: Any = 0.0
    cn_pitch_rate: Any = 0.0
    cn_yaw_rate: Any = 0.0
    cy_beta: Any = 0.0
    aileron_deg: Any = 0.0
    asymmetry_cl: Any = 0.0
    rudder_deg: Any = 0.0
    elevon_deg: Any = 0.0
    elevon_authority: Any = 0.22
    cg_offset_x: Any = 0.0
    cp_shift_gain: Any = 0.0
    ground_cushion_lift: Any = 0.0
    ground_pitch_coupling: Any = 0.0
    lift_scale: Any = 1.0
    drag_scale: Any = 1.0
    compressible: Any = True
    ground_effect_enabled: Any = True

    def polar_constant(self, xp: Any) -> Any:
        """``k = pi e AR`` -- the divisor of the induced term ``C_L^2 / k``."""
        aspect = xp.maximum(_finite(xp, self.aspect, 1.0), 1.0e-3)
        oswald = _clamp(xp, _finite(xp, self.oswald, 0.75), 0.05, 1.0)
        return math.pi * oswald * aspect

    def lift_slope(self, xp: Any, mach: Any) -> Any:
        """Effective ``dC_L / dalpha`` including compressibility.

        ``cl_alpha`` is stored already resolved by the builder: either the
        user's ``cfg.cLalpha_per_rad`` override or the Helmbold value.
        """
        slope = _finite(xp, self.cl_alpha, 0.0)
        compressible = xp.asarray(self.compressible)
        factor = xp.where(compressible, prandtl_glauert(xp, mach), 1.0)
        return slope * factor


# --------------------------------------------------------------------------
# 6. Full coefficient assembly
# --------------------------------------------------------------------------
def evaluate_coefficients(
    xp: Any,
    p: AeroParams,
    alpha_deg: Any,
    beta_deg: Any = 0.0,
    p_rate: Any = 0.0,
    q_rate: Any = 0.0,
    r_rate: Any = 0.0,
    speed: Any = 0.0,
    reynolds: Any = RE_REFERENCE,
    mach: Any = 0.0,
    ground_height_m: Any = 1.0,
    density: Any = 1.225,
) -> Dict[str, Any]:
    """Complete aerodynamic evaluation at one instant (vectorised).

    The returned dictionary holds the coefficients and the dimensional
    forces/moments.  It is the numerical twin of
    :func:`physics.aero_domain.evaluate_aero`: the scalar 6-DoF path and the
    GPU path both come through here, so the same airframe at the same state
    produces the same lift, drag and moments in either path.
    """
    alpha = _finite(xp, alpha_deg, 0.0)
    # The side force is bounded: a body broadside to the flow carries the
    # flat-plate limit (2 sin(beta) cos^2(beta) <= 0.77), not an unbounded
    # linear function of beta.  The coefficients are clamped and wrapped over
    # beta in [-90, 90] with no singularity.
    beta = _clamp(xp, _finite(xp, beta_deg, 0.0), -90.0, 90.0)
    speed = xp.abs(_finite(xp, speed, 0.0))
    reynolds = xp.abs(_finite(xp, reynolds, RE_REFERENCE))
    mach = xp.abs(_finite(xp, mach, 0.0))
    density = xp.maximum(_finite(xp, density, 1.225), 1.0e-6)
    height = _finite(xp, ground_height_m, 1.0)

    area = xp.maximum(p.area, 1.0e-9)
    chord = xp.maximum(p.chord, 1.0e-9)
    span = xp.maximum(p.span, 1.0e-6)

    lift_scale = xp.maximum(p.lift_scale, 0.0)
    drag_scale = xp.maximum(p.drag_scale, 0.0)
    compressible = xp.asarray(p.compressible)
    ground_enabled = xp.asarray(p.ground_effect_enabled)

    elevon = elevon_effects(
        xp, p.elevon_deg, p.elevon_authority, p.cm0, p.cm_alpha
    )
    slope = p.lift_slope(xp, mach)
    polar = p.polar_constant(xp)

    cl_raw = lift_coefficient(
        xp,
        alpha,
        p.cL0,
        slope,
        p.stall_alpha_deg,
        p.stall_sharpness,
        cl_max=p.cl_max,
        polhamus_kp=p.polhamus_kp,
        polhamus_kv=p.polhamus_kv,
        cl_offset=elevon["dcl"],
    )
    cl = cl_raw * lift_scale

    ground_ratio = ground_effect_factor(xp, height, span, ground_enabled)
    cd0 = parasite_drag_coefficient(xp, p.cd0, reynolds, p.re_cd0_exponent)
    cd = (
        cd0
        + (cl * cl / xp.maximum(polar, 1.0e-12)) * ground_ratio
        + separated_pressure_drag_coefficient(xp, alpha, p.stall_alpha_deg, cd0)
        + wave_drag_coefficient(xp, mach, compressible)
    ) * drag_scale

    # --- rate derivatives --------------------------------------------------
    two_v = 2.0 * speed
    p_hat = _safe_div(xp, _finite(xp, p_rate, 0.0) * span, two_v, 0.0)
    q_hat = _safe_div(xp, _finite(xp, q_rate, 0.0) * chord, two_v, 0.0)
    r_hat = _safe_div(xp, _finite(xp, r_rate, 0.0) * span, two_v, 0.0)
    beta_rad = _rad(xp, beta)

    cl_roll = p.cl_roll_rate * p_hat
    cm_roll = p.cm_roll_rate * p_hat
    cn_roll = p.cn_roll_rate * p_hat

    # Side force: the linear side-force derivative in attached flow, bounded
    # by the flat-plate limit as the body turns broadside to the flow.  A body
    # broadside to the flow carries a BOUNDED side force (2 sin(beta)
    # cos^2(beta) <= 0.77), not an unbounded linear function of beta; the
    # coefficients are clamped and wrapped over beta in [-90, 90] with no
    # singularity.  The linear law is kept wherever it is the smaller of the
    # two, so the attached-flow behaviour is unchanged.
    cy_linear = p.cy_beta * beta_rad
    flat_side = 2.0 * xp.sin(beta_rad) * xp.cos(beta_rad) ** 2
    cy = xp.where(
        xp.abs(cy_linear) <= xp.abs(flat_side),
        cy_linear,
        xp.copysign(xp.abs(flat_side), cy_linear),
    )
    aileron = p.aileron_deg
    asym_cl = p.asymmetry_cl
    rudder = p.rudder_deg
    cl_control = 0.015 * aileron + 0.25 * asym_cl
    roll_coeff = (
        p.cl_beta * beta_rad
        + cl_roll
        + cl_control
        + p.cl_pitch_rate * q_hat
        + p.cl_yaw_rate * r_hat
    )

    # --- pitching moment ----------------------------------------------------
    stall_alpha = xp.abs(p.stall_alpha_deg)
    stall_rad = _rad(xp, stall_alpha)
    alpha_rad = _rad(xp, alpha)
    cm_alpha_value = p.cm_alpha
    attached = xp.abs(alpha_rad) <= stall_rad
    w_stall = stall_blend_weight(xp, alpha, stall_alpha, p.stall_sharpness)
    linear_val = cm_alpha_value * xp.copysign(stall_rad, alpha_rad)
    flat_plate_cm = cm_alpha_value * xp.sin(alpha_rad) * xp.abs(xp.cos(alpha_rad)) * stall_rad
    cm_alpha_term = xp.where(
        attached,
        cm_alpha_value * alpha_rad,
        (1.0 - w_stall) * linear_val + w_stall * flat_plate_cm,
    )

    cm = (
        p.cm0
        + cm_alpha_term
        + p.cm_q * q_hat
        + elevon["dcm"]
        + cm_roll
        + p.cm_pitch_rate * q_hat
        + p.cm_yaw_rate * r_hat
    )

    cn_control = 0.010 * rudder - 0.002 * aileron
    yaw_coeff = (
        p.cn_beta * beta_rad
        + cn_roll
        + cn_control
        + p.cn_pitch_rate * q_hat
        + p.cn_yaw_rate * r_hat
    )

    # --- ground proximity ---------------------------------------------------
    h_b_ratio = _safe_div(xp, xp.maximum(height, 0.0), span, 10.0)
    ground_proximity = xp.where(ground_enabled, xp.exp(-4.0 * h_b_ratio), 0.0)

    cushion_gain = xp.maximum(p.ground_cushion_lift, 0.0)
    cl = cl + xp.where(
        (cushion_gain > 0.0) & (ground_proximity > 0.0),
        cushion_gain * ground_proximity,
        0.0,
    )

    pitch_coupling = xp.maximum(p.ground_pitch_coupling, 0.0)
    cm = cm + xp.where(
        (pitch_coupling > 0.0) & (ground_proximity > 0.0),
        -pitch_coupling * cl * ground_proximity,
        0.0,
    )

    cp_fraction = center_of_pressure_fraction(
        xp, alpha, stall_alpha, p.cp_shift_gain
    )
    cp_x = cp_fraction * chord
    cp_gain = xp.maximum(p.cp_shift_gain, 0.0)
    cm = cm + xp.where(
        cp_gain > 0.0,
        -_safe_div(xp, cp_x - 0.25 * chord, chord, 0.0) * cl,
        0.0,
    )

    # --- dimensional forces -------------------------------------------------
    q_dyn = 0.5 * density * speed * speed
    lift_n = q_dyn * area * cl
    d_lift = q_dyn * area * cl_control
    lift_left_n = 0.5 * lift_n + 0.5 * d_lift
    lift_right_n = 0.5 * lift_n - 0.5 * d_lift
    drag_n = q_dyn * area * cd
    side_n = q_dyn * area * cy

    # --- low-speed apex pushover -------------------------------------------
    low_speed = speed < 3.0
    v_norm = _clamp(xp, speed / 3.0, 0.0, 1.0)
    speed_gate = 1.0 - v_norm * v_norm * (3.0 - 2.0 * v_norm)
    q_eff = 0.5 * density * xp.maximum(speed, 0.8) ** 2
    alpha_gate = xp.tanh(xp.abs(alpha) / 8.0)
    sin_a = xp.sin(_rad(xp, _clamp(xp, alpha, -90.0, 90.0)))
    pushover = -q_eff * area * chord * 0.85 * sin_a * speed_gate * alpha_gate
    pushover_moment = xp.where(low_speed & (xp.abs(alpha) > 0.1), pushover, 0.0)

    # --- low-speed cross-flow rotational damping ---------------------------
    q_rate_value = _finite(xp, q_rate, 0.0)
    rot_damping = (
        -0.5 * density * 1.2 * area * (chord ** 2) * xp.abs(q_rate_value) * q_rate_value
        - 0.00015 * area * chord * density * q_rate_value
    ) * speed_gate
    rot_damping = xp.where(
        low_speed & (area > 1.0e-6) & (chord > 1.0e-4), rot_damping, 0.0
    )

    pitching_nm = (
        q_dyn * area * chord * cm
        + p.cg_offset_x * lift_n
        + pushover_moment
        + rot_damping
    )
    rolling_nm = q_dyn * area * span * roll_coeff
    yawing_nm = q_dyn * area * span * yaw_coeff

    # --- stall bookkeeping --------------------------------------------------
    threshold = stall_alpha
    stalled = xp.abs(alpha) > threshold
    stall_margin = _safe_div(
        xp,
        threshold - xp.abs(alpha),
        xp.where(threshold != 0.0, xp.abs(threshold), 1.0),
        0.0,
    )

    return {
        "cl": cl,
        "cd": cd,
        "cy": cy,
        "cm": cm,
        "cl_roll": cl_roll,
        "cm_roll": cm_roll,
        "cn_roll": cn_roll,
        "roll_coeff": roll_coeff,
        "yaw_coeff": yaw_coeff,
        "cl_control": cl_control,
        "elevon_dcl": elevon["dcl"],
        "elevon_dcm": elevon["dcm"],
        "elevon_dalpha_trim_deg": elevon["dalpha_trim_deg"],
        "q_dyn": q_dyn,
        "lift_n": lift_n,
        "drag_n": drag_n,
        "side_n": side_n,
        "lift_left_n": lift_left_n,
        "lift_right_n": lift_right_n,
        "pitching_nm": pitching_nm,
        "rolling_nm": rolling_nm,
        "yawing_nm": yawing_nm,
        "stall_margin": stall_margin,
        "stalled": stalled,
        "ground_ratio": ground_ratio,
        "cp_x_m": cp_x,
        "polar_constant": polar,
        "cl_zero_lift": p.cL0 * lift_scale + elevon["dcl"] * lift_scale,
    }


# --------------------------------------------------------------------------
# 7. Configuration -> parameter bundle
# --------------------------------------------------------------------------
def _finite_scalar(value: Any, default: float = 0.0) -> float:
    """``float(value)`` with a deterministic fallback for NaN / inf."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    return out if math.isfinite(out) else float(default)


def effective_stall_alpha_deg(cfg: AircraftConfig) -> float:
    """Stall angle of attack modified by the fold-step turbulator trip wire.

    When ``cfg.step_trip_ratio > 0`` the fold step acts as a boundary-layer
    trip wire, preventing the laminar bubble burst and delaying full stall
    separation by up to several degrees.

    The airframe is symmetric, so the same angle applies in both directions.
    """
    stall_alpha = abs(_finite_scalar(cfg.stall_alpha_deg, 14.0))
    trip_ratio = max(_finite_scalar(cfg.step_trip_ratio, 0.0), 0.0)
    if trip_ratio > 0.0:
        delay = 250.0 * trip_ratio / (1.0 + 50.0 * trip_ratio)
        return stall_alpha + delay
    return stall_alpha


def params_from_aircraft(cfg: AircraftConfig) -> AeroParams:
    """Build the kernel parameter bundle from the airframe alone.

    The environment-dependent scalars (``lift_scale``, ``drag_scale``,
    compressibility, ground effect) keep their neutral defaults here; the
    callers that evaluate a bare polar (:func:`physics.aero_domain.lift_coefficient`
    and friends) must not see an environment factor.
    """
    geom = derived_properties(cfg)
    aspect = max(_finite_scalar(geom.get("aspect_ratio"), 1.0), 1.0e-3)
    taper = clamp(_finite_scalar(geom.get("taper_ratio"), 0.7), 0.01, 1.0)
    cl_alpha = _finite_scalar(cfg.cLalpha_per_rad, 0.0)
    if cl_alpha <= 0.0:
        cl_alpha = helmbold_cl_alpha(aspect, taper)
    return AeroParams(
        area=max(_finite_scalar(geom.get("ref_area_m2"), 0.015), 1.0e-9),
        chord=max(_finite_scalar(geom.get("ref_length_m"), 0.075), 1.0e-9),
        span=max(_finite_scalar(geom.get("ref_span_m"), 0.2), 1.0e-6),
        aspect=aspect,
        oswald=clamp(_finite_scalar(cfg.oswald_e, 0.75), 0.05, 1.0),
        cL0=_finite_scalar(cfg.cL0, 0.0),
        cl_alpha=cl_alpha,
        cl_max=max(abs(_finite_scalar(cfg.cl_max, 0.90)), 1.0e-6),
        stall_alpha_deg=effective_stall_alpha_deg(cfg),
        stall_sharpness=max(abs(_finite_scalar(cfg.stall_sharpness, 1.4)), 1.0e-6),
        polhamus_kp=max(_finite_scalar(getattr(cfg, "polhamus_kp", 0.0), 0.0), 0.0),
        polhamus_kv=max(_finite_scalar(cfg.polhamus_kv, 0.0), 0.0),
        cd0=_finite_scalar(cfg.cd0, 0.02),
        re_cd0_exponent=_finite_scalar(cfg.re_cd0_exponent, 0.0),
        cm0=_finite_scalar(cfg.cm0, 0.0),
        cm_alpha=_finite_scalar(cfg.cm_alpha, 0.0),
        cm_q=_finite_scalar(cfg.cm_q, 0.0),
        cm_roll_rate=_finite_scalar(cfg.cm_roll_rate, 0.0),
        cm_pitch_rate=_finite_scalar(cfg.cm_pitch_rate, 0.0),
        cm_yaw_rate=_finite_scalar(cfg.cm_yaw_rate, 0.0),
        cl_beta=_finite_scalar(cfg.cl_beta, 0.0),
        cl_roll_rate=_finite_scalar(cfg.cl_roll_rate, 0.0),
        cl_pitch_rate=_finite_scalar(cfg.cl_pitch_rate, 0.0),
        cl_yaw_rate=_finite_scalar(cfg.cl_yaw_rate, 0.0),
        cn_beta=_finite_scalar(cfg.cn_beta, 0.0),
        cn_roll_rate=_finite_scalar(cfg.cn_roll_rate, 0.0),
        cn_pitch_rate=_finite_scalar(cfg.cn_pitch_rate, 0.0),
        cn_yaw_rate=_finite_scalar(cfg.cn_yaw_rate, 0.0),
        cy_beta=_finite_scalar(cfg.cy_beta, 0.0),
        aileron_deg=_finite_scalar(getattr(cfg, "aileron_deg", 0.0), 0.0),
        asymmetry_cl=_finite_scalar(getattr(cfg, "asymmetry_cl", 0.0), 0.0),
        rudder_deg=_finite_scalar(getattr(cfg, "rudder_deg", 0.0), 0.0),
        elevon_deg=_finite_scalar(cfg.elevon_deg, 0.0),
        elevon_authority=clamp(abs(_finite_scalar(cfg.elevon_chord_ratio, 0.22)), 0.0, 1.0),
        cg_offset_x=_finite_scalar(cfg.cg_offset_x, 0.0),
        cp_shift_gain=_finite_scalar(cfg.cp_shift_gain, 0.0),
        ground_cushion_lift=_finite_scalar(cfg.ground_cushion_lift, 0.0),
        ground_pitch_coupling=_finite_scalar(cfg.ground_pitch_coupling, 0.0),
    )


def params_from_config(cfg: AircraftConfig, env: EnvironmentConfig) -> AeroParams:
    """Build the kernel parameter bundle from the runtime configuration.

    This is the bridge between the mutable configuration objects and the
    pure-function kernel.  The per-airframe correlations that need a scalar
    (the Helmbold slope, the trip-wire stall delay) are resolved *here*, so
    the kernel stays a pure function of its inputs and can evaluate a whole
    batch of airframes at once.
    """
    params = params_from_aircraft(cfg)
    params.lift_scale = max(_finite_scalar(env.lift_scale, 1.0), 0.0)
    params.drag_scale = max(_finite_scalar(env.drag_scale, 1.0), 0.0)
    params.compressible = bool(env.compressibility_enabled)
    params.ground_effect_enabled = bool(env.ground_effect_enabled)
    return params
