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

import contextlib
import inspect
import math
from dataclasses import dataclass
from dataclasses import fields as _dataclass_fields
from dataclasses import MISSING as _MISSING
from typing import Any, Dict, Tuple

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
    "MODEL_MACH_LIMIT",
    "MODEL_REYNOLDS_MIN",
    "MODEL_REYNOLDS_MAX",
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
    "polhamus_attached_branch",
    "lift_coefficient",
    "separated_pressure_drag_coefficient",
    "drag_coefficient",
    "elevon_effects",
    "trailing_edge_fold_effects",
    "center_of_pressure_fraction",
    "evaluate_coefficients",
    "coefficient_buildup_audit",
    "effective_stall_alpha_deg",
    "fused_gpu_state",
    "params_from_config",
    "estimate_polhamus_kv",
    "first_order_lag",
    "compute_mass_ratio",
    "compute_added_mass",
    "compute_apparent_forces",
    "evaluate_model_validity",
    "unsteady_circulation_lag",
    "lsb_dynamic_stall_shift",
    "lsb_dynamic_stall_hysteresis",
    "nonlinear_dynamic_damping",
    "low_reynolds_aerodynamics_audit",
]

#: Reference Reynolds number of the ``cd0`` correlation.
RE_REFERENCE: float = 1.0e5

#: Exponent of the logistic stall blend.  The plain logistic
#: ``1 / (1 + exp(-k d))`` is numerically indistinguishable from the linear
#: branch only about 15 deg below the stall; raising it to a small integer
#: power keeps the blend smooth and centred on the stall angle while making
#: the weight decay like ``exp(-order k |d|)``.
STALL_BLEND_ORDER: int = 4

#: Aerodynamic model validity envelope constants.
MODEL_MACH_LIMIT: float = 0.3
MODEL_REYNOLDS_MIN: float = 1.0e3
MODEL_REYNOLDS_MAX: float = 1.0e5

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

    @staticmethod
    def square(value: float) -> float:
        """The square of a value.

        NumPy and CuPy evaluate ``value ** 2`` as a plain multiplication
        (their "fast scalar power"), while the scalar backend evaluates it
        with :func:`math.pow`.  The shared kernel states the square
        explicitly, because the fused GPU kernel has no fast-power path and
        would call the libdevice ``pow`` -- which differs from the eager
        CuPy path in the last bit.  The scalar backend keeps
        :func:`math.pow`, so the 6-DoF reference path is unchanged.
        """
        return math.pow(value, 2)



# --------------------------------------------------------------------------
# Array-module agnostic primitives
# --------------------------------------------------------------------------
def _array_module(value: Any) -> Any:
    """The array module of ``value`` (``numpy`` / ``cupy``), or ``None``.

    The kernel is written against an ``xp`` argument so that it never has to
    import an array library itself.  The bundle sanitiser is the one place
    that has to work on a value whose backend it does not know, so it asks the
    value which module it belongs to.
    """
    root = type(value).__module__.split(".")[0]
    if root == "numpy":
        import numpy

        return numpy
    if root == "cupy":
        import cupy

        return cupy
    return None


def _sanitize_field(value: Any, default: Any) -> Any:
    """Replace a non-finite bundle field by its documented default."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else float(default)
    module = _array_module(value)
    if module is None:
        return value
    try:
        finite = module.isfinite(value)
    except TypeError:
        return value
    return module.where(finite, value, module.asarray(default, dtype=value.dtype))


def _clamp(xp: Any, value: Any, low: Any, high: Any) -> Any:
    """``clip`` that also accepts plain Python floats.

    The value is broadcast by the array module itself: ``clip`` accepts a
    scalar argument, and the fused GPU kernel traces the very same call.
    (An explicit ``asarray`` would break the fusion, because a traced
    value cannot be converted on the host.)
    """
    return xp.clip(value, low, high)


def _safe_div(xp: Any, numerator: Any, denominator: Any, default: Any = 0.0) -> Any:
    """``safe_div`` for arrays: zero denominators yield ``default``."""
    good = xp.abs(denominator) > _EPS
    safe_denominator = xp.where(good, denominator, 1.0)
    return xp.where(good, numerator / safe_denominator, default)


def _rad(xp: Any, value: Any) -> Any:
    """Degrees to radians, array aware (``config_schema.deg2rad`` is scalar).

    The ``xp`` argument is kept for the uniform signature of the kernel
    primitives; the conversion itself is a plain multiplication, which the
    fused GPU kernel can trace and the scalar backend evaluates with the
    very same double-precision constant.
    """
    return value * (math.pi / 180.0)


def _logistic(xp: Any, value: Any) -> Any:
    """Overflow-free logistic ``1 / (1 + exp(-x))``.

    ``xp.where`` evaluates *both* branches, so every exponential has to be
    safe on its own: the positive branch only ever sees a non-positive
    argument (``exp(-x)``) and the negative branch only ever sees ``exp(x)``
    with ``x <= 0``.  Clamping the arguments of both exponentials keeps the
    evaluation finite for every input, including the large positive offsets
    the stall blend produces.
    """
    x = value
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

    The array branch uses ``where(isfinite(...))`` instead of
    ``nan_to_num``.  The two agree exactly (every non-finite value becomes
    ``default`` either way), and the ``where`` form is the one the fused
    GPU kernel can express: ``nan_to_num`` is not an elementwise operation
    and cannot be traced into a single kernel.
    """
    if isinstance(value, float):
        if value == value and -1.0e308 < value < 1.0e308:
            return value
    return xp.where(xp.isfinite(value), value, default)


def _flag_array(xp: Any, value: Any) -> Any:
    """Turn a plain Python flag into an array the array module understands.

    ``cupy.where`` rejects a Python ``bool`` condition, so a flag has to be
    an array on the GPU path.  Values that already are arrays are passed
    through unchanged, which keeps the fused kernel free of host-side
    conversions (``asarray`` of a traced value is not possible).
    """
    if isinstance(value, (bool, int, float)):
        return xp.asarray(value)
    return value


def _f64(xp: Any, value: Any) -> Any:
    """A constant in double precision.

    The CuPy fusion tracer types a bare Python float as ``float16``: an
    expression whose dtype is decided by a Python constant alone (the
    ``where`` branches of the post-stall sign, the stall blend angle, ...)
    would be evaluated in half precision inside the fused kernel and would
    no longer reproduce the eager evaluation.  Wrapping the constants in the
    array module's own ``float64`` keeps the fused kernel in double
    precision; the values are unchanged.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return xp.float64(value)
    return value


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
    """Induced-drag retention factor ``sigma = 16 (h/b)^2 / (1 + 16 (h/b)^2)`` (Wieselsberger 1922).

    The multiplier applies directly to the free-air induced drag coefficient:
    ``C_Di(h) = sigma * C_Di_free_air = sigma * (C_L^2 / (pi * e * AR))``.

    Boundary and Asymptotic Characteristics:
    - At high clearance (free air, ``h / b >> 1``): ``sigma -> 1.0`` (no ground-effect reduction).
    - As ground clearance decreases (``h / b -> 0``): ``sigma`` monotonically decreases towards 0.0.
    - At ground contact / zero clearance (``h = 0``): ``sigma = 0.0`` cleanly without singularity.
    - When disabled (``enabled = False``): returns ``1.0`` (retaining full free-air polar).
    - Non-negative inputs: safe guarding against negative heights (clamped to 0) and small spans.
    """
    enabled = _flag_array(xp, enabled)
    height = xp.maximum(_finite(xp, height_m, 0.0), 0.0)
    span = xp.maximum(_finite(xp, span_m, 0.2), 1.0e-9)
    ratio = 16.0 * xp.square(height / span)
    ratio = xp.where(xp.isfinite(ratio), ratio, 1.0)
    factor = ratio / (1.0 + ratio)
    return xp.where(enabled, factor, 1.0)


def parasite_drag_coefficient(xp: Any, cd0: Any, reynolds: Any, exponent: Any) -> Any:
    """Reynolds-corrected parasite drag ``C_D0 = cd0 (1e5 / Re) ** exponent``."""
    value = xp.maximum(xp.abs(_finite(xp, reynolds, RE_REFERENCE)), 1.0)
    safe_cd0 = xp.maximum(_finite(xp, cd0, 0.02), 0.0)
    return safe_cd0 * (RE_REFERENCE / value) ** _finite(xp, exponent, 0.0)


def wave_drag_coefficient(xp: Any, mach: Any, compressible: Any = True) -> Any:
    """Transonic wave-drag increment (exactly ``0.0`` below transonic)."""
    compressible = _flag_array(xp, compressible)
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
    residual_cl: Any = 0.0,
) -> Any:
    """Post-stall lift anchored on the Hoerner flat-plate limit.

    The branch starts at the airframe's own stall value ``ref_cl`` and moves
    onto the fully separated plate curve ``2 sin(a) cos^2(a)`` over
    ``[alpha_stall, blend_deg]``.

    When ``residual_cl > 0.0``, a smooth residual lift increment representing
    camber and residual separated-vortex suction is retained past stall:
    ``C_L,res = residual_cl * cos(alpha)``, ensuring C0/C1 continuity with the
    attached branch at ``alpha_stall`` and transitioning smoothly to zero at
    90 deg. When ``residual_cl == 0.0`` (the default), the pure Hoerner
    flat-plate limit is recovered with bit-level fidelity.

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
    sign = xp.where(alpha >= 0.0, _f64(xp, 1.0), _f64(xp, -1.0))
    abs_alpha = xp.abs(alpha)
    stall_alpha = xp.abs(_finite(xp, stall_alpha_deg, 14.0))
    reference = xp.abs(_finite(xp, ref_cl, 0.0))
    blend_angle = _f64(xp, _finite(xp, blend_deg, POST_STALL_PLATE_BLEND_DEG))
    res_param = xp.maximum(_finite(xp, residual_cl, 0.0), 0.0)

    # Base Hoerner plate
    plate_bare = flat_plate_lift_coefficient(xp, alpha)
    # Cosine roll-off for residual lift
    cos_a = xp.maximum(xp.cos(_rad(xp, abs_alpha)), 0.0)
    plate = plate_bare + sign * res_param * cos_a

    # The blend target is the magnitude of the plate value at the blend angle
    target_bare = xp.abs(flat_plate_lift_coefficient(xp, blend_angle))
    cos_blend = xp.maximum(xp.cos(_rad(xp, blend_angle)), 0.0)
    target = target_bare + res_param * cos_blend

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


def estimate_polhamus_kv(
    xp: Any,
    aspect_ratio: Any,
    sweep_le_deg: Any,
    taper_ratio: Any = 1.0,
) -> Any:
    """Estimate Polhamus suction analogy vortex lift factor K_v from planform geometry.

    According to Polhamus (1966 NASA TN D-3767, 1971 NASA TN D-6482):
    For sharp-edge delta and swept wings, leading-edge suction translates into
    normal vortex lift proportional to K_v. On a slender delta wing, K_v approaches
    pi (~3.14159). For general swept wings, the vortex suction analogy scales with
    the leading-edge sweep angle and aspect ratio:
        K_v = pi * sin(Lambda_LE) * (AR / (AR + 2.0 * cos(Lambda_LE)))
    bounded within [0.0, pi].
    When leading-edge sweep is zero, K_v is identically 0.0.
    """
    ar = xp.maximum(_finite(xp, aspect_ratio, 2.0), 0.1)
    sweep_rad = _rad(xp, _finite(xp, sweep_le_deg, 0.0))
    sin_sw = xp.abs(xp.sin(sweep_rad))
    cos_sw = xp.abs(xp.cos(sweep_rad))
    kv_raw = math.pi * sin_sw * (ar / xp.maximum(ar + 2.0 * cos_sw, 0.1))
    return _clamp(xp, kv_raw, 0.0, math.pi)


def first_order_lag(
    xp: Any,
    current: Any,
    target: Any,
    dt: Any,
    tau: Any,
) -> Any:
    """First-order lag filter: tau * dx/dt + x = target.

    Discretised step:
        x_next = current + (dt / (tau + dt)) * (target - current)
    When tau <= 0.0 or dt <= 0.0, the response is instantaneous (x_next = target).
    """
    dt_val = xp.maximum(_finite(xp, dt, 0.0), 0.0)
    tau_val = xp.maximum(_finite(xp, tau, 0.0), 0.0)
    cur = _finite(xp, current, 0.0)
    tgt = _finite(xp, target, 0.0)
    active = (tau_val > 1.0e-9) & (dt_val > 1.0e-9)
    alpha = _safe_div(xp, dt_val, tau_val + dt_val)
    lagged = cur + alpha * (tgt - cur)
    return xp.where(active, lagged, tgt)


def compute_mass_ratio(
    xp: Any,
    mass_kg: Any,
    density_kgm3: Any,
    wing_area_m2: Any,
    mac_m: Any,
) -> Any:
    """Non-dimensional mass ratio mu = mass / (rho * S * mac).

    For ultra-light paper gliders, mu is typically on the order of 1 - 5,
    meaning fluid added mass is not negligible compared to structural mass.
    """
    m = xp.maximum(_finite(xp, mass_kg, 0.003), 1.0e-9)
    rho = xp.maximum(_finite(xp, density_kgm3, 1.225), 1.0e-6)
    s = xp.maximum(_finite(xp, wing_area_m2, 0.02), 1.0e-9)
    c = xp.maximum(_finite(xp, mac_m, 0.1), 1.0e-9)
    return m / (rho * s * c)


def compute_added_mass(
    xp: Any,
    density_kgm3: Any,
    wing_area_m2: Any,
    mac_m: Any,
    wingspan_m: Any = None,
) -> Dict[str, Any]:
    """Apparent (added) fluid mass for a thin planar wing.

    Theoretical flat plate in normal motion (Blevins / Newman):
        m_added_z = (pi / 4) * rho * S * mac
        I_added_yy = (pi / 128) * rho * S * mac^3
        I_added_xx = (pi / 48) * rho * b^3 * mac^2
        I_added_zz = (pi / 128) * rho * h_keel * mac^4  # [kg m^2]
    """
    rho = xp.maximum(_finite(xp, density_kgm3, 1.225), 0.0)
    s = xp.maximum(_finite(xp, wing_area_m2, 0.02), 0.0)
    c = xp.maximum(_finite(xp, mac_m, 0.1), 0.0)
    if wingspan_m is not None:
        b = xp.maximum(_finite(xp, wingspan_m, 0.20), 0.0)
    else:
        # Default aspect ratio ~ 3 estimate if span not provided
        b = xp.sqrt(xp.maximum(s * 3.0, 1.0e-6))
    m_z = (math.pi / 4.0) * rho * s * c
    i_yy = (math.pi / 128.0) * rho * s * (c * c * c)
    i_xx = (math.pi / 48.0) * rho * (b * b * b) * (c * c)
    # Yaw apparent inertia (vertical keel/fin plate rotation about z axis: Blevins / Newman)
    # Keel / vertical fin projected area ~ S_v = keel_h * c, where keel_h ~ 0.08 * b
    # Dimension: [rho * b * c^4] or [rho * S_v * c^3] = [kg/m^3 * m * m^3 = kg * m] was incorrect.
    # The moment of inertia of added mass about yaw z-axis for a vertical plate of depth h and chord c:
    # I_zz = (pi / 128) * rho * h * (c^4) -> [kg/m^3 * m * m^4] = [kg m^2]
    keel_h = xp.maximum(b * 0.08, 0.01)
    i_zz = (math.pi / 128.0) * rho * keel_h * (c * c * c * c)
    return {
        "added_mass_z_kg": m_z,
        "added_inertia_yy_kgm2": i_yy,
        "added_inertia_xx_kgm2": i_xx,
        "added_inertia_zz_kgm2": i_zz,
    }


def compute_apparent_forces(
    xp: Any,
    body_velocity: Any,
    omega: Any,
    added_mass_z_kg: Any,
) -> Tuple[Any, Any]:
    """Apparent-mass Coriolis and Munk terms for a body moving through fluid.

    The fluid momentum carried by the body is ``P_A = [0, 0, m_z * w]`` (added
    mass acts only in the z direction).  In body axes with z down:

        Fluid Coriolis force:   F_app = omega x P_A
        Munk apparent moment:   M_app = -v_rel x P_A

    Expanding:

        F_app_x = + q * m_z * w
        F_app_y = - p * m_z * w
        F_app_z = 0

        M_app_x = - v * m_z * w   (roll apparent moment)
        M_app_y = + u * m_z * w   (pitch apparent moment)
        M_app_z = 0

    The inertial (added-mass) term ``-dP_A/dt`` is handled separately by the
    caller through the effective mass ``m_eff_z = m + m_add_z``; this function
    returns only the rotational coupling terms.  These terms transfer energy
    between body motion and fluid kinetic energy; they do not conserve power
    by themselves.
    """
    v_arr = xp.asarray(body_velocity, dtype=xp.float64)
    w_arr = xp.asarray(omega, dtype=xp.float64)
    m_z = xp.asarray(added_mass_z_kg, dtype=xp.float64)

    u = v_arr[..., 0]
    v = v_arr[..., 1]
    w = v_arr[..., 2]
    p = w_arr[..., 0]
    q = w_arr[..., 1]

    fx = q * m_z * w
    fy = -p * m_z * w
    fz = xp.zeros_like(fx)
    force_apparent = xp.stack((fx, fy, fz), axis=-1)

    mx = -v * m_z * w
    my = u * m_z * w
    mz = xp.zeros_like(mx)
    moment_apparent = xp.stack((mx, my, mz), axis=-1)

    return force_apparent, moment_apparent


def unsteady_circulation_lag(
    xp: Any,
    cl_current: Any,
    cl_target: Any,
    dt: Any,
    mac_m: Any,
    speed_mps: Any,
    k_circ: Any = 2.0,
) -> Any:
    """Circulation development lag for unsteady thin airfoil / wing flow.

    Based on the Wagner function indicial response / Beddoes-Leishman formulation.
    The convective circulation lag time constant is:
        tau_circ = k_circ * c / (2 * V_rel)
    When k_circ <= 0.0 or speed <= 0.0, the response is instantaneous (cl_target).
    """
    k = xp.maximum(_finite(xp, k_circ, 2.0), 0.0)
    c = xp.maximum(_finite(xp, mac_m, 0.1), 1.0e-6)
    v = xp.maximum(_finite(xp, speed_mps, 0.0), 0.0)
    # Avoid zero division when stationary
    tau = xp.where(
        (k > 1.0e-9) & (v > 0.1),
        (k * c) / (2.0 * xp.maximum(v, 0.1)),
        0.0,
    )
    return first_order_lag(xp, cl_current, cl_target, dt, tau)


def lsb_dynamic_stall_shift(
    xp: Any,
    alpha_dot_deg_s: Any,
    mac_m: Any,
    speed_mps: Any,
    reynolds: Any,
    kappa_lsb: Any = 1.5,
) -> Any:
    """Dynamic stall angle shift due to low-Reynolds laminar separation bubble (LSB).

    At low Reynolds numbers (1e3 <= Re <= 5e4), laminar separation bubbles delay
    boundary layer separation during dynamic pitch-up (alpha_dot > 0), raising
    effective stall angle:
        alpha_stall_eff = alpha_stall_static + delta_alpha_hyst
    During pitch-down (alpha_dot < 0), reattachment is delayed, lowering effective
    stall angle:
        delta_alpha_hyst = sgn(alpha_dot) * kappa_lsb * sqrt(k_reduced) * f(Re)
    where:
        k_reduced = |alpha_dot_rad| * c / (2 * V_rel)
        f(Re) = sqrt(1e4 / max(Re, 1000))
    At steady state (alpha_dot = 0), delta_alpha_hyst is identically 0.0.
    """
    adot = _finite(xp, alpha_dot_deg_s, 0.0)
    adot_rad = _rad(xp, adot)
    abs_adot_rad = xp.abs(adot_rad)
    c = xp.maximum(_finite(xp, mac_m, 0.1), 1.0e-6)
    v = xp.maximum(_finite(xp, speed_mps, 0.0), 0.1)
    re = xp.maximum(_finite(xp, reynolds, 1.0e4), 1.0e3)
    k_lsb = xp.maximum(_finite(xp, kappa_lsb, 1.5), 0.0)

    # Reduced pitch rate
    k_red = (abs_adot_rad * c) / (2.0 * v)
    # Reynolds scaling factor (bounded)
    f_re = _clamp(xp, xp.sqrt(1.0e4 / re), 0.2, 2.0)

    # Regularized smooth direction to prevent chattering and numerical energy injection
    smooth_sign = xp.tanh(adot / 5.0)
    # Scale from nondimensional delay to degrees using (180 / pi)
    delta_deg = smooth_sign * k_lsb * (180.0 / math.pi) * xp.sqrt(xp.maximum(k_red, 0.0)) * f_re
    # Physical clamp on maximum dynamic stall overshoot (+/- 10 degrees)
    return _clamp(xp, delta_deg, -10.0, 10.0)


def lsb_dynamic_stall_hysteresis(
    xp: Any,
    alpha_deg: Any,
    alpha_dot_deg_s: Any,
    stall_alpha_deg: Any,
    mac_m: Any,
    speed_mps: Any,
    reynolds: Any,
    is_stalled_prev: Any = False,
    kappa_lsb: Any = 1.5,
    kappa_reatt: Any = 1.0,
    delta_alpha0_deg: Any = 2.5,
) -> Tuple[Any, Any]:
    """Low-Re Laminar Separation Bubble (LSB) dynamic hysteresis model.

    Distinguishes pitch-up stall delay (convective lag) from pitch-down
    reattachment delay (turbulent bubble reform lag), producing a physical
    counter-clockwise hysteresis loop in CL(alpha).

    Returns:
        (effective_stall_alpha_deg, is_stalled_current)
    """
    alpha = xp.abs(_finite(xp, alpha_deg, 0.0))
    adot = _finite(xp, alpha_dot_deg_s, 0.0)
    adot_rad = _rad(xp, adot)
    c = xp.maximum(_finite(xp, mac_m, 0.1), 1.0e-6)
    v = xp.maximum(_finite(xp, speed_mps, 0.0), 0.1)
    re = xp.maximum(_finite(xp, reynolds, 1.0e4), 1.0e3)
    k_lsb = xp.maximum(_finite(xp, kappa_lsb, 1.5), 0.0)
    k_reatt = xp.maximum(_finite(xp, kappa_reatt, 1.0), 0.0)
    a_stall_0 = xp.abs(_finite(xp, stall_alpha_deg, 14.0))

    k_red = xp.sqrt(xp.maximum((xp.abs(adot_rad) * c) / (2.0 * v), 0.0))
    f_re = _clamp(xp, xp.sqrt(1.0e4 / re), 0.2, 2.0)
    delta_reatt = _clamp(xp, delta_alpha0_deg * xp.sqrt(2.0e4 / re), 0.5, 6.0)

    shift_up = k_lsb * k_red * f_re * (180.0 / math.pi)
    shift_down = -(delta_reatt + k_reatt * k_red * f_re * (180.0 / math.pi))
    delta_shift = xp.where(adot >= 0.0, shift_up, shift_down)

    is_prev = _flag_array(xp, is_stalled_prev)
    eff_stall = xp.where(
        is_prev,
        a_stall_0 + xp.minimum(delta_shift, -0.5),
        a_stall_0 + xp.maximum(delta_shift, 0.0),
    )
    eff_stall = _clamp(xp, eff_stall, a_stall_0 - 8.0, a_stall_0 + 10.0)
    is_stalled = alpha > eff_stall
    return eff_stall, is_stalled


def nonlinear_dynamic_damping(
    xp: Any,
    alpha_deg: Any,
    stall_alpha_deg: Any,
    p_hat: Any,
    q_hat: Any,
    r_hat: Any,
    cl_p0: Any = -0.35,
    cm_q0: Any = -1.80,
    cn_r0: Any = -0.25,
) -> Tuple[Any, Any, Any]:
    """Separation-aware non-linear dynamic damping derivatives.

    At attached flow, preserves potential linear damping (cl_p0, cm_q0, cn_r0).
    As stall progresses, smoothly transitions to cross-flow bluff-body drag
    damping (|p|, |q|, |r| dependence).
    Strictly negative everywhere, enforcing monotonic energy dissipation.
    """
    abs_alpha = xp.abs(_finite(xp, alpha_deg, 0.0))
    a_stall = xp.abs(_finite(xp, stall_alpha_deg, 14.0))
    prog = _clamp(xp, (abs_alpha - a_stall) / 15.0, 0.0, 1.0)
    w_sep = prog * prog * (3.0 - 2.0 * prog)

    lp_att = xp.minimum(_finite(xp, cl_p0, -0.35), -1.0e-4)
    mq_att = xp.minimum(_finite(xp, cm_q0, -1.80), -1.0e-4)
    nr_att = xp.minimum(_finite(xp, cn_r0, -0.25), -1.0e-4)

    lp_sep = -(0.15 + 1.2 * xp.abs(_finite(xp, p_hat, 0.0)))
    mq_sep = -(0.40 + 2.5 * xp.abs(_finite(xp, q_hat, 0.0)))
    nr_sep = -(0.10 + 1.0 * xp.abs(_finite(xp, r_hat, 0.0)))

    cl_p = (1.0 - w_sep) * lp_att + w_sep * lp_sep
    cm_q = (1.0 - w_sep) * mq_att + w_sep * mq_sep
    cn_r = (1.0 - w_sep) * nr_att + w_sep * nr_sep
    return cl_p, cm_q, cn_r


def evaluate_model_validity(
    xp: Any,
    mach: Any,
    reynolds: Any,
    alpha_deg: Any = 0.0,
    mass_ratio: Any = 10.0,
    mach_limit: float = MODEL_MACH_LIMIT,
    re_min: float = MODEL_REYNOLDS_MIN,
    re_max: float = MODEL_REYNOLDS_MAX,
    stall_alpha_margin_deg: float = 45.0,
    min_mass_ratio: float = 0.5,
    strict_checks: bool = False,
) -> Dict[str, Any]:
    """Evaluate whether flight conditions stay within aerodynamic envelope limits.

    Baseline validity checks:
    - mach <= mach_limit (incompressible low-speed assumption)
    - re_min <= reynolds <= re_max (calibrated Reynolds range)

    Extended checks (active when strict_checks=True or accessed via telemetry):
    - abs(alpha_deg) <= stall_alpha_margin_deg (post-stall applicability limits)
    - mass_ratio >= min_mass_ratio (added-mass quasi-steady assumption)
    """
    m = _finite(xp, mach, 0.0)
    re = _finite(xp, reynolds, 1.0e4)
    a = xp.abs(_finite(xp, alpha_deg, 0.0))
    mu = _finite(xp, mass_ratio, 10.0)

    mach_ok = m <= mach_limit
    re_ok = (re >= re_min) & (re <= re_max)
    alpha_ok = a <= stall_alpha_margin_deg
    mu_ok = mu >= min_mass_ratio

    base_valid = mach_ok & re_ok
    full_valid = base_valid & alpha_ok & mu_ok
    valid = xp.where(_flag_array(xp, strict_checks), full_valid, base_valid)
    mass_ratio_warning = mu < 5.0

    return {
        "valid": valid,
        "base_valid": base_valid,
        "mach_ok": mach_ok,
        "re_ok": re_ok,
        "alpha_ok": alpha_ok,
        "mass_ratio_ok": mu_ok,
        "mass_ratio_warning": mass_ratio_warning,
    }


def polhamus_attached_branch(
    xp: Any,
    alpha_deg: Any,
    zero_lift: Any,
    slope_per_rad: Any,
    polhamus_kp: Any = 0.0,
    polhamus_kv: Any = 0.0,
) -> Any:
    """The attached-flow lift branch, including the Polhamus LEV terms.

    The Polhamus suction analogy (1966/1971) publishes

    ``C_L = K_p sin(a) cos^2(a) + K_v sin^2(a) cos(a)``

    with two terms that are **not** interchangeable:

    * the *potential* term ``K_p sin(a) cos^2(a)`` is the **two-dimensional**
      lift slope of a sharp-edge delta wing.  When ``K_p > 0`` it *replaces*
      the linear branch, because the published relation is the whole
      attached-flow law of that wing, not an increment on top of it.
    * the *vortex* term ``K_v sin^2(a) cos(a)`` is the leading-edge-suction
      increment and is added **on top of** whichever attached branch is used.

    With the default ``K_p = 0`` the airframe keeps its own three-dimensional
    Helmbold slope ``cL0 + C_Lalpha a``: a low aspect-ratio wing must not be
    given the two-dimensional slope on top of the finite-wing correction.
    Only the vortex increment is then added.

    The published term already carries the correct sign -- ``sin^2(a) cos(a)``
    is negative for ``|a| > 90 deg`` -- so no ``copysign`` is applied.

    This function is the **canonical** implementation of the relation inside
    the flight path.  :func:`physics.aero_domain.polhamus_lift_coefficient`
    exposes the published relation *standalone* (without the ``cL0`` offset
    and with the airframe's own slope substituted for ``K_p``), which is a
    different quantity; the two are kept apart by name instead of being two
    accidental copies of each other.
    """
    kp = xp.maximum(_finite(xp, polhamus_kp, 0.0), 0.0)
    kv = xp.maximum(_finite(xp, polhamus_kv, 0.0), 0.0)
    alpha_rad = _rad(xp, alpha_deg)
    sin_a = xp.sin(alpha_rad)
    cos_a = xp.cos(alpha_rad)

    linear_default = zero_lift + slope_per_rad * alpha_rad
    vortex_sign = xp.where(sin_a >= 0.0, _f64(xp, 1.0), _f64(xp, -1.0))
    linear_polhamus = (
        zero_lift
        + kp * sin_a * xp.square(cos_a)
        + kv * xp.square(sin_a) * cos_a * vortex_sign
    )
    attached = xp.where(kp > 0.0, linear_polhamus, linear_default)
    vortex = kv * xp.square(sin_a) * cos_a * vortex_sign
    return xp.where((kp <= 0.0) & (kv > 0.0), attached + vortex, attached)


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
    residual_cl: Any = 0.0,
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

    linear = polhamus_attached_branch(
        xp, alpha, zero_lift, slope, polhamus_kp, polhamus_kv
    )

    stall_rad = _rad(xp, stall_alpha)
    kv = xp.maximum(_finite(xp, polhamus_kv, 0.0), 0.0)
    stall_vortex = kv * xp.square(xp.sin(stall_rad)) * xp.cos(stall_rad)
    vortex_inc = xp.where(kv > 0.0, stall_vortex, 0.0)
    linear_at_stall_pos = zero_lift + slope * stall_rad + vortex_inc
    linear_at_stall_neg = zero_lift - slope * stall_rad - vortex_inc

    ceiling = xp.maximum(xp.abs(_finite(xp, cl_max, 0.90)), 1.0e-6)
    attached = _clamp(xp, linear, -ceiling, ceiling)
    capped_linear_at_stall_pos = _clamp(xp, linear_at_stall_pos, -ceiling, ceiling)
    capped_linear_at_stall_neg = _clamp(xp, linear_at_stall_neg, -ceiling, ceiling)
    ref_at_stall = xp.where(
        alpha >= 0.0, capped_linear_at_stall_pos, xp.abs(capped_linear_at_stall_neg)
    )

    post_stall = post_stall_lift_coefficient(
        xp, alpha, ref_at_stall, stall_alpha, residual_cl=residual_cl
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
    separated = 2.0 * xp.square(xp.sin(angle)) + parasite * xp.maximum(xp.cos(angle), 0.0)
    weight = _smoothstep01(
        xp,
        (xp.abs(alpha) - stall_alpha)
        / xp.maximum(
            _f64(xp, _finite(xp, blend_deg, POST_STALL_DRAG_BLEND_DEG)), 1.0
        ),
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
    residual_cl: Any = 0.0,
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
        residual_cl=residual_cl,
    )
    parasite = parasite_drag_coefficient(xp, cd0, reynolds, re_exponent)
    induced = cl * cl / xp.maximum(_finite(xp, polar_constant, 1.0), 1.0e-12)
    attached_drag = parasite + induced * _finite(xp, ground_ratio, 1.0)

    # Fully separated plate drag (Hoerner 1965): 2 sin^2(a) + C_D0 cos(a)
    alpha = (_finite(xp, alpha_deg, 0.0) + 180.0) % 360.0 - 180.0
    stall_alpha = xp.abs(_finite(xp, stall_alpha_deg, 14.0))
    angle = _rad(xp, alpha)
    separated_drag = 2.0 * xp.square(xp.sin(angle)) + parasite * xp.maximum(xp.cos(angle), 0.0)

    weight = _smoothstep01(
        xp,
        (xp.abs(alpha) - stall_alpha)
        / xp.maximum(
            _f64(xp, _finite(xp, POST_STALL_DRAG_BLEND_DEG, 15.0)), 1.0
        ),
    )
    # Blend attached polar and separated polar smoothly to eliminate double counting
    cd_polar = (1.0 - weight) * attached_drag + weight * separated_drag
    wave = wave_drag_coefficient(xp, mach, compressible)
    return cd_polar + wave



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


def trailing_edge_fold_effects(xp: Any, fold_trailing_deg: Any) -> Dict[str, Any]:
    """Aerodynamic increments from full-span trailing-edge fold angle (vectorised).

    A positive fold angle is trailing-edge-down (camber increase): adds lift (dcl > 0)
    and introduces a nose-down moment (dcm < 0).
    A negative fold angle is trailing-edge-up (reflex): reduces lift (dcl < 0)
    and introduces a nose-up pitch trim moment (dcm > 0), essential for tailless paper gliders.
    Also produces a profile drag increment dcd scaling with sin^2(fold).
    """
    fold = _finite(xp, fold_trailing_deg, 0.0)
    rad = _rad(xp, fold)
    dcl = 0.015 * fold
    dcm = -0.012 * fold
    sin_val = xp.sin(rad)
    dcd = 0.035 * xp.square(sin_val)
    return {"dcl": dcl, "dcm": dcm, "dcd": dcd}


def _guard_fold_increment(xp: Any, te_fold: Dict[str, Any], cl_max: Any) -> Dict[str, Any]:
    """Stability guard of the trailing-edge fold connection (``|dC_L| <= cl_max``).

    The conformance audit (H-19) prescribes this guard for the fold: the airframe
    cannot produce more lift than its own maximum, so a fold increment larger
    than ``cl_max`` is not a physical state.  The lift increment and the moment
    increment are scaled **together**, so the ratio ``dcm / dcl`` -- and with it
    the position of the fold's incremental centre of pressure (the fold's lift
    acting at the trailing edge) -- is preserved.  The profile-drag increment is
    a separate effect of the folded panel and is not bounded by the lift limit.
    """
    limit = xp.maximum(xp.abs(_finite(xp, cl_max, 0.90)), 0.0)
    dcl = te_fold["dcl"]
    magnitude = xp.abs(dcl)
    oversized = magnitude > limit
    scale = xp.where(
        oversized,
        limit / xp.where(magnitude > 0.0, magnitude, 1.0),
        1.0,
    )
    return {"dcl": dcl * scale, "dcm": te_fold["dcm"] * scale, "dcd": te_fold["dcd"]}


def center_of_pressure_fraction(
    xp: Any, alpha_deg: Any, stall_alpha_deg: Any, cp_shift_gain: Any
) -> Any:
    r"""Centre-of-pressure position as a fraction of the reference chord :math:`x_{cp} / c`.

    Theoretical & Experimental Basis (Issue #109):
    ----------------------------------------------
    1. Linear Attached Flow (Thin Airfoil Theory):
       At low angles of attack (:math:`|\alpha| \ll \alpha_{\mathrm{stall}}`), attached flow
       around a thin/flat airfoil places the aerodynamic center at approximately 25% of the
       chord (:math:`x_{ac} / c \approx 0.25`). For symmetric flat plates with zero camber,
       the pitching moment about the aerodynamic center :math:`C_{m,ac} = 0`, so the center
       of pressure coincides with the aerodynamic center: :math:`x_{cp} / c = 0.25`.

    2. Fully Separated Bluff Flat-Plate Flow (Kirchhoff-Rayleigh / Hoerner limit):
       Beyond stall (:math:`|\alpha| \ge \alpha_{\mathrm{stall}}`), leading-edge flow separation
       causes massive bluff-body detachment. For a stalled flat plate, the pressure distribution
       becomes symmetric across the chord, shifting the center of pressure to the geometric
       area centroid :math:`x_{cp} / c = 0.50` (50% chord), as verified experimentally by
       Hoerner (Fluid-Dynamic Lift, 1975) and low-Re flat plate experiments (e.g. Pelletier &
       Mueller 2000, Sunada et al. 2002).

    3. Transition & Continuity (:math:`C^1` smoothstep):
       The transition between attached flow (:math:`0.25`) and separated flow (:math:`0.50`)
       is governed by a cubic Hermite smoothstep polynomial:
       .. math::
           S(u) = 3 u^2 - 2 u^3, \quad u = \mathrm{clamp}(|\alpha| / \alpha_{\mathrm{stall}}, 0, 1)

       - Monotonicity: :math:`S'(u) = 6 u (1 - u) \ge 0` for all :math:`u \in [0, 1]`.
       - Boundary conditions & :math:`C^1` continuity:
         :math:`S(0) = 0, \quad S'(0) = 0`, ensuring :math:`\left.\frac{d(x_{cp}/c)}{d\alpha}\right|_{\alpha=0} = 0`.
         :math:`S(1) = 1, \quad S'(1) = 0`, ensuring :math:`\left.\frac{d(x_{cp}/c)}{d\alpha}\right|_{\alpha=\alpha_{\mathrm{stall}}} = 0`.
       - Symmetry: :math:`x_{cp}(-\alpha) = x_{cp}(\alpha)`.
       - Pitch stability coupling:
         The aft shift produces a stabilizing pitch increment :math:`\Delta C_m = -(x_{cp} - 0.25 c)/c \cdot C_L`,
         reinforcing static pitch stability margin :math:`\Delta h = (x_{cg} - x_{cp})/c`
         without introducing speculative empirical offsets.
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
    fold_trailing_deg: Any = 0.0
    cg_offset_x: Any = 0.0
    cp_shift_gain: Any = 0.0
    ground_cushion_lift: Any = 0.0
    ground_pitch_coupling: Any = 0.0
    lift_scale: Any = 1.0
    drag_scale: Any = 1.0
    compressible: Any = True
    ground_effect_enabled: Any = True

    def __post_init__(self) -> None:
        """Guarantee that no field of the bundle is NaN or infinite.

        The kernel reads the bundle fields directly: re-checking them on every
        evaluation would cost one array pass per field on the batch path, which
        is exactly the overhead the batch integrator must not pay.  The
        guarantee therefore lives in the bundle itself -- the single place every
        construction path goes through -- so a non-finite value can no longer
        reach the coefficients, whichever path built the bundle.
        """
        for field in _dataclass_fields(self):
            value = getattr(self, field.name)
            default = field.default
            if default is _MISSING:
                default = 0.0
            setattr(self, field.name, _sanitize_field(value, default))

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
#: The :class:`AeroParams` fields the coefficient evaluation reads, in the
#: order of the core signature.
_CORE_BUNDLE_FIELDS: Tuple[str, ...] = (
    "area",
    "chord",
    "span",
    "aspect",
    "oswald",
    "cL0",
    "cl_alpha",
    "cl_max",
    "stall_alpha_deg",
    "stall_sharpness",
    "polhamus_kp",
    "polhamus_kv",
    "cd0",
    "re_cd0_exponent",
    "cm0",
    "cm_alpha",
    "cm_q",
    "cm_roll_rate",
    "cm_yaw_rate",
    "cl_beta",
    "cl_roll_rate",
    "cl_pitch_rate",
    "cl_yaw_rate",
    "cn_beta",
    "cn_roll_rate",
    "cn_pitch_rate",
    "cn_yaw_rate",
    "cy_beta",
    "aileron_deg",
    "asymmetry_cl",
    "rudder_deg",
    "elevon_deg",
    "elevon_authority",
    "fold_trailing_deg",
    "cg_offset_x",
    "cp_shift_gain",
    "ground_cushion_lift",
    "ground_pitch_coupling",
    "lift_scale",
    "drag_scale",
    "compressible",
    "ground_effect_enabled",
)

#: The state arguments of the core, in the order of the core signature.
_CORE_STATE_FIELDS: Tuple[str, ...] = (
    "alpha_deg",
    "beta_deg",
    "p_rate",
    "q_rate",
    "r_rate",
    "speed",
    "reynolds",
    "mach",
    "ground_height_m",
    "density",
)

#: The coefficients the evaluation returns, in the order of the core's
#: return tuple.
_COEFFICIENT_KEYS: Tuple[str, ...] = (
    "cl",
    "cd",
    "cy",
    "cm",
    "cl_roll",
    "cm_roll",
    "cn_roll",
    "roll_coeff",
    "yaw_coeff",
    "cl_control",
    "elevon_dcl",
    "elevon_dcm",
    "elevon_dalpha_trim_deg",
    "te_fold_dcl",
    "te_fold_dcm",
    "te_fold_dcd",
    "q_dyn",
    "lift_n",
    "drag_n",
    "side_n",
    "lift_left_n",
    "lift_right_n",
    "pitching_nm",
    "rolling_nm",
    "yawing_nm",
    "stall_margin",
    "stalled",
    "ground_ratio",
    "cp_x_m",
    "polar_constant",
    "cl_zero_lift",
)


def _coefficient_core(
    xp: Any,
    area: Any,
    chord: Any,
    span: Any,
    aspect: Any,
    oswald: Any,
    cL0: Any,
    cl_alpha: Any,
    cl_max: Any,
    stall_alpha_deg: Any,
    stall_sharpness: Any,
    polhamus_kp: Any,
    polhamus_kv: Any,
    cd0: Any,
    re_cd0_exponent: Any,
    cm0: Any,
    cm_alpha: Any,
    cm_q: Any,
    cm_roll_rate: Any,
    cm_yaw_rate: Any,
    cl_beta: Any,
    cl_roll_rate: Any,
    cl_pitch_rate: Any,
    cl_yaw_rate: Any,
    cn_beta: Any,
    cn_roll_rate: Any,
    cn_pitch_rate: Any,
    cn_yaw_rate: Any,
    cy_beta: Any,
    aileron_deg: Any,
    asymmetry_cl: Any,
    rudder_deg: Any,
    elevon_deg: Any,
    elevon_authority: Any,
    fold_trailing_deg: Any,
    cg_offset_x: Any,
    cp_shift_gain: Any,
    ground_cushion_lift: Any,
    ground_pitch_coupling: Any,
    lift_scale: Any,
    drag_scale: Any,
    compressible: Any,
    ground_effect_enabled: Any,
    alpha_deg: Any,
    beta_deg: Any,
    p_rate: Any,
    q_rate: Any,
    r_rate: Any,
    speed: Any,
    reynolds: Any,
    mach: Any,
    ground_height_m: Any,
    density: Any,
) -> Tuple[Any, ...]:
    """The aerodynamic evaluation as one elementwise expression tree.

    This is the single source of the coefficients.  The body is written
    strictly elementwise -- no reduction, no data-dependent branching and no
    host-side conversion of a value -- so that the **same source** runs

    * on :class:`ScalarMath` (the scalar 6-DoF reference path),
    * on NumPy (the vectorised reference path),
    * on CuPy eagerly, and
    * on CuPy **fused into a single CUDA kernel** (see
      :func:`_fused_coefficients`).

    The parameter list is flat on purpose: a fused kernel receives its
    inputs as kernel arguments, so the airframe parameters cannot travel
    as an attribute of a bundle object.  The order is fixed by
    ``_CORE_BUNDLE_FIELDS + _CORE_STATE_FIELDS`` and checked against the
    signature at import time.

    The formulas, their coefficients, their units and the order of the
    arithmetic are the ones of the pre-fusion kernel -- the fusion changes
    only *how* the expression tree is executed, never *what* it computes.
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

    area = xp.maximum(area, 1.0e-9)
    chord = xp.maximum(chord, 1.0e-9)
    span = xp.maximum(span, 1.0e-6)

    lift_scale = xp.maximum(lift_scale, 0.0)
    drag_scale = xp.maximum(drag_scale, 0.0)

    elevon = elevon_effects(
        xp, elevon_deg, elevon_authority, cm0, cm_alpha
    )
    te_fold = _guard_fold_increment(
        xp, trailing_edge_fold_effects(xp, fold_trailing_deg), cl_max
    )
    # The two bundle methods of the pre-fusion kernel are inlined here:
    # ``lift_slope`` (the Helmbold slope times the Prandtl-Glauert factor)
    # and ``polar_constant`` (``k = pi e AR``).  Their expressions and their
    # order are unchanged.
    slope = _finite(xp, cl_alpha, 0.0) * xp.where(
        compressible, prandtl_glauert(xp, mach), 1.0
    )
    polar = math.pi * _clamp(xp, _finite(xp, oswald, 0.75), 0.05, 1.0) * xp.maximum(
        _finite(xp, aspect, 1.0), 1.0e-3
    )

    cl_raw = lift_coefficient(
        xp,
        alpha,
        cL0,
        slope,
        stall_alpha_deg,
        stall_sharpness,
        cl_max=cl_max,
        polhamus_kp=polhamus_kp,
        polhamus_kv=polhamus_kv,
        cl_offset=elevon["dcl"] + te_fold["dcl"],
    )
    cl = cl_raw * lift_scale

    ground_ratio = ground_effect_factor(xp, height, span, ground_effect_enabled)
    cd0 = parasite_drag_coefficient(xp, cd0, reynolds, re_cd0_exponent) + te_fold["dcd"]
    induced = (cl * cl / xp.maximum(polar, 1.0e-12)) * ground_ratio
    attached_drag = cd0 + induced

    # Fully separated plate drag (Hoerner 1965): 2 sin^2(a) + C_D0 cos(a)
    alpha_norm = (alpha + 180.0) % 360.0 - 180.0
    stall_alpha_val = xp.abs(_finite(xp, stall_alpha_deg, 14.0))
    angle = _rad(xp, alpha_norm)
    separated_drag = 2.0 * xp.square(xp.sin(angle)) + cd0 * xp.maximum(xp.cos(angle), 0.0)
    w_drag = _smoothstep01(
        xp,
        (xp.abs(alpha_norm) - stall_alpha_val)
        / xp.maximum(_f64(xp, POST_STALL_DRAG_BLEND_DEG), 1.0),
    )
    cd_polar = (1.0 - w_drag) * attached_drag + w_drag * separated_drag
    cd = (
        cd_polar
        + wave_drag_coefficient(xp, mach, compressible)
    ) * drag_scale


    # --- rate derivatives --------------------------------------------------
    two_v = 2.0 * speed
    p_hat = _safe_div(xp, _finite(xp, p_rate, 0.0) * span, two_v, 0.0)
    q_hat = _safe_div(xp, _finite(xp, q_rate, 0.0) * chord, two_v, 0.0)
    r_hat = _safe_div(xp, _finite(xp, r_rate, 0.0) * span, two_v, 0.0)
    beta_rad = _rad(xp, beta)

    cl_roll = cl_roll_rate * p_hat
    cm_roll = cm_roll_rate * p_hat
    cn_roll = cn_roll_rate * p_hat

    # Side force: the linear side-force derivative in attached flow, bounded
    # by the flat-plate limit as the body turns broadside to the flow.  A body
    # broadside to the flow carries a BOUNDED side force (2 sin(beta)
    # cos^2(beta) <= 0.77), not an unbounded linear function of beta; the
    # coefficients are clamped and wrapped over beta in [-90, 90] with no
    # singularity.  The linear law is kept wherever it is the smaller of the
    # two, so the attached-flow behaviour is unchanged.
    cy_linear = cy_beta * beta_rad
    flat_side = 2.0 * xp.sin(beta_rad) * xp.square(xp.cos(beta_rad))
    cy = xp.where(
        xp.abs(cy_linear) <= xp.abs(flat_side),
        cy_linear,
        xp.copysign(xp.abs(flat_side), cy_linear),
    )
    aileron = aileron_deg
    asym_cl = asymmetry_cl
    rudder = rudder_deg
    cl_control = 0.015 * aileron + 0.25 * asym_cl
    roll_coeff = (
        cl_beta * beta_rad
        + cl_roll
        + cl_control
        + cl_pitch_rate * q_hat
        + cl_yaw_rate * r_hat
    )

    # --- pitching moment ----------------------------------------------------
    stall_alpha = xp.abs(stall_alpha_deg)
    stall_rad = _rad(xp, stall_alpha)
    alpha_rad = _rad(xp, alpha)
    cm_alpha_value = cm_alpha
    attached = xp.abs(alpha_rad) <= stall_rad
    w_stall = stall_blend_weight(xp, alpha, stall_alpha, stall_sharpness)
    linear_val = cm_alpha_value * xp.copysign(stall_rad, alpha_rad)
    flat_plate_cm = cm_alpha_value * xp.sin(alpha_rad) * xp.abs(xp.cos(alpha_rad)) * stall_rad
    cm_alpha_term = xp.where(
        attached,
        cm_alpha_value * alpha_rad,
        (1.0 - w_stall) * linear_val + w_stall * flat_plate_cm,
    )

    cm = (
        cm0
        + cm_alpha_term
        + cm_q * q_hat
        + elevon["dcm"]
        + te_fold["dcm"]
        + cm_roll
        + cm_yaw_rate * r_hat
    )

    cn_control = 0.010 * rudder - 0.002 * aileron
    yaw_coeff = (
        cn_beta * beta_rad
        + cn_roll
        + cn_control
        + cn_pitch_rate * q_hat
        + cn_yaw_rate * r_hat
    )

    # --- ground proximity ---------------------------------------------------
    # Low-aspect-ratio paper airplane ground proximity modeling:
    # 1. Induced drag reduction (Wieselsberger sigma factor applied above to C_Di).
    # 2. Ram-pressure cushion lift increment: delta_CL = cushion_gain * exp(-4 * h/b).
    #    Near the ground (h/b << 1), ram pressure beneath low-aspect flat/cambered wings
    #    develops positive lift cushion. When ground_cushion_lift == 0 (default),
    #    pure classical Wieselsberger behavior is retained without arbitrary offsets.
    # 3. Ground-effect pitching moment coupling: delta_Cm = -pitch_coupling * CL * exp(-4 * h/b).
    #    For typical configurations in ground effect, downwash reduction and center-of-pressure
    #    dynamics introduce a stabilizing nose-down pitch coupling proportional to circulation/CL.
    #    When ground_pitch_coupling == 0 (default), Cm remains strictly free-air.
    h_b_ratio = _safe_div(xp, xp.maximum(height, 0.0), span, 10.0)
    ground_proximity = xp.where(ground_effect_enabled, xp.exp(-4.0 * h_b_ratio), 0.0)

    cushion_gain = xp.maximum(ground_cushion_lift, 0.0)
    cl = cl + xp.where(
        (cushion_gain > 0.0) & (ground_proximity > 0.0),
        cushion_gain * ground_proximity,
        0.0,
    )

    pitch_coupling = xp.maximum(ground_pitch_coupling, 0.0)
    cm = cm + xp.where(
        (pitch_coupling > 0.0) & (ground_proximity > 0.0),
        -pitch_coupling * cl * ground_proximity,
        0.0,
    )

    cp_fraction = center_of_pressure_fraction(
        xp, alpha, stall_alpha, cp_shift_gain
    )
    cp_x = cp_fraction * chord
    cp_gain = xp.maximum(cp_shift_gain, 0.0)
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
    q_eff = 0.5 * density * xp.square(xp.maximum(speed, 0.8))
    alpha_gate = xp.tanh(xp.abs(alpha) / 8.0)
    sin_a = xp.sin(_rad(xp, _clamp(xp, alpha, -90.0, 90.0)))
    pushover = -q_eff * area * chord * 0.85 * sin_a * speed_gate * alpha_gate
    pushover_moment = xp.where(low_speed & (xp.abs(alpha) > 0.1), pushover, 0.0)

    # --- low-speed cross-flow rotational damping ---------------------------
    q_rate_value = _finite(xp, q_rate, 0.0)
    rot_damping = (
        -0.5 * density * 1.2 * area * xp.square(chord) * xp.abs(q_rate_value) * q_rate_value
        - 0.00015 * area * chord * density * q_rate_value
    ) * speed_gate
    rot_damping = xp.where(
        low_speed & (area > 1.0e-6) & (chord > 1.0e-4), rot_damping, 0.0
    )

    pitching_nm = (
        q_dyn * area * chord * cm
        + cg_offset_x * lift_n
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

    return (
        cl,
        cd,
        cy,
        cm,
        cl_roll,
        cm_roll,
        cn_roll,
        roll_coeff,
        yaw_coeff,
        cl_control,
        elevon["dcl"],
        elevon["dcm"],
        elevon["dalpha_trim_deg"],
        te_fold["dcl"],
        te_fold["dcm"],
        te_fold["dcd"],
        q_dyn,
        lift_n,
        drag_n,
        side_n,
        lift_left_n,
        lift_right_n,
        pitching_nm,
        rolling_nm,
        yawing_nm,
        stall_margin,
        stalled,
        ground_ratio,
        cp_x,
        polar,
        cL0 * lift_scale + (elevon["dcl"] + te_fold["dcl"]) * lift_scale,
    )


# --------------------------------------------------------------------------
# 6a. Fused GPU execution
# --------------------------------------------------------------------------
#: ``"unavailable"`` -- no CuPy, or the fused kernel could not be built;
#: ``"pending"`` -- the fused kernel exists but has not been verified yet;
#: ``"verified"`` -- the fused kernel reproduced the eager evaluation bit
#: for bit; ``"rejected"`` -- the verification failed, so the eager path is
#: used from now on.
_FUSED_STATE: str = "unavailable"

#: The compiled fused kernel.  It is created on first use, because CuPy is
#: an optional dependency of this module.
_FUSED_KERNEL: Any = None

#: The order of the core's arguments.
_CORE_PARAM_ORDER: Tuple[str, ...] = _CORE_BUNDLE_FIELDS + _CORE_STATE_FIELDS

#: The core's signature without the array-module argument.  It has to agree
#: with :data:`_CORE_PARAM_ORDER`, because the arguments travel positionally
#: to the core and to the fused kernel.
_CORE_SIGNATURE: Tuple[str, ...] = tuple(
    inspect.signature(_coefficient_core).parameters
)[1:]

if _CORE_SIGNATURE != _CORE_PARAM_ORDER:
    raise RuntimeError(
        "aero_kernel: the coefficient core signature and the field order disagree"
    )

#: Rounding-sensitive states used by the bit-exactness verification of the
#: fused kernel.  The values are deliberately awkward (irrational-looking
#: angles, non-dyadic speeds) so that a contracted multiply-add differs
#: from the eager multiplication and addition.
_FUSED_PROBE_STATES: Tuple[Tuple[float, ...], ...] = (
    (3.7, -11.3, 0.37, -0.29, 0.19, 7.3, 3.1e4, 0.021, 1.37, 1.213),
    (-27.4, 23.9, -0.83, 1.19, -0.61, 12.7, 7.9e4, 0.037, 0.83, 1.241),
    (18.3, -4.7, 0.53, 0.41, -0.37, 3.1, 1.7e4, 0.009, 2.71, 1.198),
)


def _is_cupy(xp: Any) -> bool:
    """``True`` when ``xp`` is the CuPy array module."""
    return getattr(xp, "__name__", "") == "cupy"


@contextlib.contextmanager
def _fma_contraction_disabled() -> Any:
    """Compile the fused kernel without FMA contraction.

    A fused kernel evaluates a whole expression tree inside one thread, so
    the compiler may contract a multiplication and the addition that
    consumes it into a single fused multiply-add.  The eager path cannot do
    that -- it runs one operation per kernel -- so the contraction would
    make the fused kernel differ from the eager one in the last bit.  The
    option is therefore removed from the NVRTC command line of the fused
    kernel alone, which keeps the two paths bit-identical.

    The patch is scoped to the compilation of the fused kernel; every other
    CuPy kernel of the process keeps the default (contracting) behaviour.
    The patch is held for the whole fused call, not only for the first one,
    because a new dtype combination would be compiled during that call and
    would have to be compiled without the contraction as well.  The window
    is short and the batch path is single threaded, so a kernel compiled
    concurrently by another thread would at worst lose its contraction --
    a last-bit difference in an unrelated kernel.
    """
    try:
        import cupy.cuda.compiler as compiler
    except Exception:  # pragma: no cover - a CuPy without the compiler module
        yield
        return
    original = getattr(compiler, "_compile_module_with_cache", None)
    if original is None:  # pragma: no cover - an unknown CuPy layout
        yield
        return

    def without_fma(source, options=(), *args, **kwargs):
        return original(source, tuple(options) + ("--fmad=false",), *args, **kwargs)

    compiler._compile_module_with_cache = without_fma
    try:
        yield
    finally:
        compiler._compile_module_with_cache = original


def _device_array(xp: Any, value: Any) -> Any:
    """The value as a device array the fused kernel can consume."""
    if hasattr(value, "get"):  # already a CuPy array
        return value
    return xp.asarray(value)


def _run_fused(xp: Any, device_arguments: Tuple[Any, ...]) -> Tuple[Any, ...]:
    """Run the fused kernel (compiling it on first use)."""
    global _FUSED_KERNEL
    if _FUSED_KERNEL is None:
        import cupy

        @cupy.fuse(kernel_name="aero_coefficients_fused")
        def _fused(*kernel_arguments: Any) -> Tuple[Any, ...]:
            return _coefficient_core(cupy, *kernel_arguments)

        _FUSED_KERNEL = _fused
    with _fma_contraction_disabled():
        return _FUSED_KERNEL(*device_arguments)


def _values_match(xp: Any, fused: Any, eager: Any) -> bool:
    """``True`` when the fused and the eager value are the same number.

    The two may differ in shape -- the fused kernel returns the broadcast
    shape of all its inputs, while the eager path keeps the shape of the
    expression that produced the value -- so the comparison is made on the
    broadcast of the two.
    """
    fused = xp.asarray(fused)
    eager = xp.asarray(eager)
    if fused.dtype != eager.dtype:
        return False
    if fused.dtype == xp.bool_:
        return bool(xp.all(fused == eager))
    fused, eager = xp.broadcast_arrays(fused, eager)
    equal = (fused == eager) | (xp.isnan(fused) & xp.isnan(eager))
    return bool(xp.all(equal))


def _fused_matches_eager(
    xp: Any, arguments: Tuple[Any, ...], values: Tuple[Any, ...]
) -> bool:
    """``True`` when the fused kernel reproduces the eager evaluation exactly.

    The comparison covers the state of the call itself and a set of
    rounding-sensitive probe states, evaluated with the same airframe.
    """
    eager = _coefficient_core(xp, *arguments)
    if len(eager) != len(values):
        return False
    for fused_value, eager_value in zip(values, eager):
        if not _values_match(xp, fused_value, eager_value):
            return False

    shape = xp.asarray(arguments[_CORE_BUNDLE_FIELDS.index("area")]).shape
    for probe in _FUSED_PROBE_STATES:
        probe_arguments = list(arguments)
        for offset, value in enumerate(probe):
            index = len(_CORE_BUNDLE_FIELDS) + offset
            probe_arguments[index] = xp.full(shape, value, dtype=xp.float64)
        probe_values = _run_fused(xp, tuple(probe_arguments))
        probe_eager = _coefficient_core(xp, *probe_arguments)
        for fused_value, eager_value in zip(probe_values, probe_eager):
            if not _values_match(xp, fused_value, eager_value):
                return False
    return True


def _fused_coefficients(xp: Any, arguments: Tuple[Any, ...]) -> Any:
    """Evaluate the coefficients in a single fused CUDA kernel.

    Returns ``None`` when the fused path is not usable (no CuPy, no usable
    ``cupy.fuse``, or the bit-exactness verification failed); the caller then
    uses the eager path, which computes the same numbers.
    """
    global _FUSED_STATE, _FUSED_KERNEL
    if _FUSED_STATE == "rejected":
        return None
    try:
        device_arguments = tuple(_device_array(xp, value) for value in arguments)
        values = _run_fused(xp, device_arguments)
    except Exception:
        # A CuPy build without a working fusion must not break the batch
        # path: the eager evaluation computes exactly the same numbers.
        _FUSED_STATE = "rejected"
        _FUSED_KERNEL = None
        return None
    if _FUSED_STATE != "verified":
        if not _fused_matches_eager(xp, device_arguments, values):
            _FUSED_STATE = "rejected"
            _FUSED_KERNEL = None
            return None
        _FUSED_STATE = "verified"
    return values


def fused_gpu_state() -> str:
    """The state of the fused GPU path.

    ``"unavailable"`` (no CuPy or no fused kernel), ``"verified"`` (the
    fused kernel is in use and reproduces the eager evaluation bit for bit)
    or ``"rejected"`` (the verification failed and the eager path is used).
    """
    return _FUSED_STATE


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

    On the GPU the evaluation runs as **one fused kernel** (see
    :func:`_fused_coefficients`): the 13-state RK4 integrator of the batch
    path is dominated by the kernel launch overhead, and the fused kernel
    replaces the several hundred launches of one state evaluation with a
    single one.  The fused kernel is verified against the eager evaluation
    bit for bit before it is used; if the verification fails -- or if CuPy
    is not available at all -- the eager path computes the very same
    numbers.
    """
    values = {name: getattr(p, name) for name in _CORE_BUNDLE_FIELDS}
    values["compressible"] = xp.asarray(p.compressible)
    values["ground_effect_enabled"] = xp.asarray(p.ground_effect_enabled)
    values["alpha_deg"] = alpha_deg
    values["beta_deg"] = beta_deg
    values["p_rate"] = p_rate
    values["q_rate"] = q_rate
    values["r_rate"] = r_rate
    values["speed"] = speed
    values["reynolds"] = reynolds
    values["mach"] = mach
    values["ground_height_m"] = ground_height_m
    values["density"] = density
    arguments = tuple(values[name] for name in _CORE_PARAM_ORDER)

    if _is_cupy(xp):
        fused = _fused_coefficients(xp, arguments)
        if fused is not None:
            return dict(zip(_COEFFICIENT_KEYS, fused))
    return dict(zip(_COEFFICIENT_KEYS, _coefficient_core(xp, *arguments)))




def low_reynolds_aerodynamics_audit(
    xp: Any,
    p: AeroParams,
    reynolds_range: Tuple[float, float] = (1.0e4, 1.0e5),
    num_re_points: int = 5,
    alpha_sweep_deg: Tuple[float, float] = (-5.0, 25.0),
    num_alpha_points: int = 61,
) -> Dict[str, Any]:
    """Quantify low-Re aerodynamic characteristics across the flight envelope (Issue #111).

    Evaluates Reynolds number dependence of:
    - Lift-curve slope (dCL/dalpha per radian and per deg) in attached flow
    - Maximum lift coefficient (CLmax)
    - Stall angle (alpha_stall_deg)
    - Parasite drag (CD0) scaling and total drag polar sensitivity
    - Attached flow vs flat-plate stall transition bounds
    - Envelope diagnostics and model validity

    Paper aircraft flight envelope spans Re in [1e4, 1e5] (U approx 3-15 m/s, c approx 0.05-0.15 m).
    """
    import numpy as _np

    re_vals = _np.geomspace(reynolds_range[0], reynolds_range[1], num_re_points)
    alpha_vals = _np.linspace(alpha_sweep_deg[0], alpha_sweep_deg[1], num_alpha_points)

    re_sweep_data = []
    for re_val in re_vals:
        coeffs = evaluate_coefficients(
            xp,
            p,
            alpha_deg=alpha_vals,
            beta_deg=0.0,
            speed=8.0,
            reynolds=float(re_val),
            mach=0.02,
        )
        cl = _np.asarray(coeffs["cl"], dtype=float).reshape(-1)
        cd = _np.asarray(coeffs["cd"], dtype=float).reshape(-1)
        stalled = _np.asarray(coeffs["stalled"], dtype=bool).reshape(-1)
        stall_margin = _np.asarray(coeffs["stall_margin"], dtype=float).reshape(-1)

        # Attached linear region around alpha in [-2, 6] deg for slope estimation
        att_mask = (alpha_vals >= -2.0) & (alpha_vals <= 6.0)
        if _np.sum(att_mask) >= 2:
            poly = _np.polyfit(_np.radians(alpha_vals[att_mask]), cl[att_mask], deg=1)
            cl_alpha_per_rad = float(poly[0])
            cl_alpha_per_deg = cl_alpha_per_rad * (_np.pi / 180.0)
        else:
            cl_alpha_per_rad = float(p.cl_alpha)
            cl_alpha_per_deg = cl_alpha_per_rad * (_np.pi / 180.0)

        cl_max_observed = float(_np.max(cl))
        idx_max = int(_np.argmax(cl))
        alpha_at_cl_max = float(alpha_vals[idx_max])

        # Parasite drag cd0 at alpha=0
        idx_zero = int(_np.argmin(_np.abs(alpha_vals)))
        cd0_val = float(cd[idx_zero])

        re_sweep_data.append({
            "reynolds": float(re_val),
            "cl_alpha_per_rad": cl_alpha_per_rad,
            "cl_alpha_per_deg": cl_alpha_per_deg,
            "cl_max": cl_max_observed,
            "alpha_at_cl_max_deg": alpha_at_cl_max,
            "cd0": cd0_val,
            "nominal_stall_alpha_deg": float(p.stall_alpha_deg),
            "stalled_fraction": float(_np.mean(stalled)),
        })

    # Evaluate constancy / variation across Re
    slopes = [d["cl_alpha_per_rad"] for d in re_sweep_data]
    cl_maxs = [d["cl_max"] for d in re_sweep_data]
    cd0s = [d["cd0"] for d in re_sweep_data]

    slope_variation = float(_np.ptp(slopes))
    cl_max_variation = float(_np.ptp(cl_maxs))
    cd0_variation = float(_np.ptp(cd0s))

    # Envelope check
    val_low = evaluate_model_validity(xp, 0.02, reynolds_range[0])
    val_high = evaluate_model_validity(xp, 0.02, reynolds_range[1])
    envelope_valid = bool(_np.asarray(val_low["valid"]).reshape(-1)[0] and _np.asarray(val_high["valid"]).reshape(-1)[0])

    return {
        "re_sweep_data": re_sweep_data,
        "slope_variation": slope_variation,
        "cl_max_variation": cl_max_variation,
        "cd0_variation": cd0_variation,
        "envelope_valid": envelope_valid,
        "envelope_re_min": float(MODEL_REYNOLDS_MIN),
        "envelope_re_max": float(MODEL_REYNOLDS_MAX),
        "helmbold_attached_cl_alpha": float(p.cl_alpha),
        "configured_cl_max": float(p.cl_max),
        "configured_stall_alpha_deg": float(p.stall_alpha_deg),
        "notes": (
            "In the calibrated baseline model, potential lift slope (Helmbold) and static stall "
            "ceiling (cl_max, stall_alpha_deg) are invariant with steady Re, while parasite drag cd0 "
            "scales via re_cd0_exponent. Unsteady LSB dynamics (lsb_dynamic_stall_shift/hysteresis) "
            "provide rate-dependent pitch delays scaling with Re^-0.5."
        ),
    }

def coefficient_buildup_audit(
    xp: Any,
    p: AeroParams,
    speed: Any = 8.0,
    reynolds: Any = RE_REFERENCE,
    mach: Any = 0.0,
    step_deg: float = 2.0,
    span_deg: float = 20.0,
) -> Dict[str, Any]:
    """Audit of the coefficient buildup (Issue #71).

    Single-domain buildup, not wing/fuselage/tail summation: CL/CD/CM/CY
    come from one fused core, so downwash shielding and interference are
    deliberately NOT modelled.  This report makes that limit explicit and
    checks the properties the summation would have endangered: reference
    area/chord/span, odd/even symmetry, bounded slopes across stall,
    envelope validity.
    """
    import numpy as _np

    alphas_list = []
    value = -span_deg
    while value <= span_deg + 1e-9:
        alphas_list.append(value)
        value += step_deg
    alphas = _np.asarray(alphas_list, dtype=float)
    coeffs = evaluate_coefficients(
        xp, p, alpha_deg=alphas, beta_deg=0.0, speed=speed, reynolds=reynolds, mach=mach
    )
    cl = _np.asarray(coeffs["cl"], dtype=float).reshape(-1)
    cd = _np.asarray(coeffs["cd"], dtype=float).reshape(-1)
    cm = _np.asarray(coeffs["cm"], dtype=float).reshape(-1)
    finite = bool(
        _np.all(_np.isfinite(cl)) and _np.all(_np.isfinite(cd)) and _np.all(_np.isfinite(cm))
    )
    count = len(alphas)
    mid = count // 2
    half = min(mid, count - mid - 1)
    if half > 0:
        cl_plus = cl[mid + 1 : mid + 1 + half]
        cl_minus = cl[mid - 1 :: -1][:half]
        cd_plus = cd[mid + 1 : mid + 1 + half]
        cd_minus = cd[mid - 1 :: -1][:half]
        cl_odd_err = float(_np.max(_np.abs(cl_plus + cl_minus - 2.0 * cl[mid])))
        cd_even_err = float(_np.max(_np.abs(cd_plus - cd_minus)))
    else:
        cl_odd_err = 0.0
        cd_even_err = 0.0
    cl_slope = float(_np.max(_np.abs(_np.diff(cl)))) if len(cl) > 1 else 0.0
    cd_slope = float(_np.max(_np.abs(_np.diff(cd)))) if len(cd) > 1 else 0.0
    cm_slope = float(_np.max(_np.abs(_np.diff(cm)))) if len(cm) > 1 else 0.0
    plus = evaluate_coefficients(xp, p, alpha_deg=0.0, beta_deg=10.0, speed=speed, reynolds=reynolds, mach=mach)
    minus = evaluate_coefficients(xp, p, alpha_deg=0.0, beta_deg=-10.0, speed=speed, reynolds=reynolds, mach=mach)
    cy_odd_err = abs(float(_np.asarray(plus["cy"]).reshape(-1)[0]) + float(_np.asarray(minus["cy"]).reshape(-1)[0]))
    clamped = bool(cl_slope < 2.0 and cd_slope < 2.0 and cm_slope < 2.0)
    validity = evaluate_model_validity(xp, mach, reynolds, 0.0)
    return {
        "finite": finite,
        "cl_odd_err": cl_odd_err,
        "cd_even_err": cd_even_err,
        "cl_slope_max": cl_slope,
        "cd_slope_max": cd_slope,
        "cm_slope_max": cm_slope,
        "cy_odd_err": float(cy_odd_err),
        "clamped": clamped,
        "envelope_valid": bool(_np.asarray(validity["valid"]).reshape(-1)[0]),
        "interference_modelled": False,
        "interference_note": "single-domain buildup; downwash/shielding between wing/fuselage/tail NOT modelled",
        "reference_area_m2": float(_np.asarray(p.area).reshape(-1)[0]),
        "reference_chord_m": float(_np.asarray(p.chord).reshape(-1)[0]),
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
        fold_trailing_deg=_finite_scalar(getattr(cfg, "fold_trailing_deg", 0.0), 0.0),
        cg_offset_x=_finite_scalar(cfg.cg_offset_x, 0.0),
        cp_shift_gain=_finite_scalar(cfg.cp_shift_gain, 0.0),
        ground_cushion_lift=_finite_scalar(cfg.ground_cushion_lift, 0.0),
        ground_pitch_coupling=_finite_scalar(cfg.ground_pitch_coupling, 0.0),
    )


def environment_scales(env: EnvironmentConfig) -> Tuple[float, float]:
    """Sanitised ``(lift_scale, drag_scale)`` of the environment.

    The 6-DoF reference path (:func:`params_from_config`) and the batch paths
    read the environment factors through this single helper, so a non-finite
    value is replaced by the same default in every path instead of only in the
    reference path.  A NaN reaching the batch path used to poison every
    coefficient of every case of the batch.
    """
    return (
        max(_finite_scalar(env.lift_scale, 1.0), 0.0),
        max(_finite_scalar(env.drag_scale, 1.0), 0.0),
    )


def params_from_config(cfg: AircraftConfig, env: EnvironmentConfig) -> AeroParams:
    """Build the kernel parameter bundle from the runtime configuration.

    This is the bridge between the mutable configuration objects and the
    pure-function kernel.  The per-airframe correlations that need a scalar
    (the Helmbold slope, the trip-wire stall delay) are resolved *here*, so the
    kernel stays a pure function of its inputs and can evaluate a whole
    batch of airframes at once.
    """
    params = params_from_aircraft(cfg)
    lift_scale, drag_scale = environment_scales(env)
    params.lift_scale = lift_scale
    params.drag_scale = drag_scale
    params.compressible = bool(env.compressibility_enabled)
    params.ground_effect_enabled = bool(env.ground_effect_enabled)
    return params
