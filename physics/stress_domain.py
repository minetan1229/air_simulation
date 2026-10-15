"""Wing cantilever loads and grid-independent bending stress.

For total normal force F, a linearly tapered half wing carries F/2.
Its root moment is F*b*(1+2*taper)/(12*(1+taper)). At station y,
M(y) is the integral of the outboard load times its lever arm. Stress is
M/Z, with Z = local_chord * thickness**2 * folded_gain / 6 [m^3].

KAPPA_SIGMA, folded_gain, allowable strength, and fold/load couplings are
empirical crease approximations, not experimentally validated paper material
properties. Reported failure status is an unvalidated indicator, not a
certified fracture prediction. NumPy is the CPU fallback; CuPy is optional.
Body axes follow config_schema: x forward, y right, z down.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np

from .config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    clamp,
    deg2rad,
    rad2deg,
    derived_properties,
    safe_div,
)
from .geometry_utils import cross, normalize, vec3

__all__ = [
    "SPAN_LOADING_FACTOR",
    "KAPPA_SIGMA",
    "INDEX_REFERENCE_PA",
    "PAPER_THICKNESS_DENOMINATOR",
    "ALLOWABLE_BASE_PA",
    "ALLOWABLE_GSM_EXPONENT",
    "ALLOWABLE_STIFFNESS_OFFSET",
    "ALLOWABLE_STIFFNESS_GAIN",
    "TE_FOLD_CHORD_REDUCTION",
    "TIP_SPAN_REDUCTION",
    "DIHEDRAL_ROLL_DERIVATIVE",
    "Z_SCALE_OFFSET",
    "Z_SCALE_GAIN",
    "STATUS_OK",
    "STATUS_HIGH_LOAD",
    "STATUS_FAILED",
    "STATUS_HIGH_LOAD_THRESHOLD",
    "STATUS_FAILED_THRESHOLD",
    "CHORDWISE_EXPONENT",
    "dynamic_pressure",
    "load_factor",
    "aircraft_load_factor",
    "aircraft_weight",
    "trim_lift_coefficient",
    "trim_speed",
    "wing_root_bending_moment",
    "effective_paper_stiffness",
    "aeroelastic_deformations",
    "paper_allowable_stress",
    "failure_index",
    "status_for_failure_index",
    "normalise_heatmap",
    "heatmap_colour",
    "active_backend",
    "critical_point_body",
    "dihedral_roll_couple",
    "stress_field",
    "analyse",
    "analyze",
]

# --------------------------------------------------------------------------
# Model constants
# --------------------------------------------------------------------------
#: Uniform half-wing root moment divided by total normal load times semispan.
SPAN_LOADING_FACTOR: float = 0.25

#: Unvalidated empirical folded-crease stress reduction; retained without retuning.
KAPPA_SIGMA: float = 9.0e-6

#: Dynamic pressure -> load factor -> root moment -> cell stress is exact;
#: this constant maps peak stress onto the heat-map saturation point.  Ten
#: times the reference allowable so that "ok" and "high_load" flights stay
#: visibly sub-saturated and a gsm / stiffness sweep is legible in the index.
INDEX_REFERENCE_PA: float = 2.5e4

#: Uncreped paper bulk density in kg/m^3, so ``t = gsm / 800`` is the sheet
#: thickness in **millimetres** for a grammage in g/m^2 (i.e. 100 g/m^2 ->
#: 0.125 mm).  :func:`_sheet_thickness` converts that to SI metres.
PAPER_THICKNESS_DENOMINATOR: float = 800.0

#: Allowable stress closed form:
#: ``A = ALLOWABLE_BASE_PA (gsm/100)^0.5 (OFFSET + GAIN * stiffness)``.
#: The sqrt(gsm) scaling follows the fold-tear force of a crease (force per
#: unit width grows with the number of stacked fibres, i.e. with thickness),
#: and the linear stiffness term represents the fold-radius rounding loss.
ALLOWABLE_BASE_PA: float = 2500.0
ALLOWABLE_GSM_EXPONENT: float = 0.5
ALLOWABLE_STIFFNESS_OFFSET: float = 0.6
ALLOWABLE_STIFFNESS_GAIN: float = 0.8

#: Status bands.  The UI colour-codes on these exact strings.
STATUS_OK: str = "ok"
STATUS_HIGH_LOAD: str = "high_load"
STATUS_FAILED: str = "failed"
STATUS_HIGH_LOAD_THRESHOLD: float = 0.6
STATUS_FAILED_THRESHOLD: float = 1.0

#: Thin-plate section-modulus shape exponent on ``(1 - x)``: 1.5 is between
#: the cantilever (1) and the simply supported free-end (2) case and peaks
#: at the leading edge / root rib.
CHORDWISE_EXPONENT: float = 1.5

# --- documented dimensionless couplings ----------------------------------
TIP_MOMENT_GAIN: float = 1.6            # winglet fold tip cantilever
SPAN_SHIFT_GAIN: float = 0.6            # dihedral -> triangular span load
DIHEDRAL_LOAD_COUPLING: float = 0.8     # dihedral -> local lift multiplier
DIHEDRAL_MOMENT_COUPLING: float = 0.45  # dihedral -> root roll couple
SIDELOAD_COUPLING: float = 0.35         # sideslip -> side load on the root
ELEVON_ARM_GAIN: float = 0.6            # elevon / TE fold moment arm
ELEVON_TE_GAIN: float = 1.5             # TE concentration from that arm
TE_FOLD_CHORD_REDUCTION: float = 0.35   # TE fold removes effective chord
TIP_SPAN_REDUCTION: float = 0.20        # span removed by a folded winglet tip
DIHEDRAL_ROLL_DERIVATIVE: float = 0.08  # rolling moment dC_l / d tan(Lambda)
Z_SCALE_OFFSET: float = 0.55            # folded section modulus gain
Z_SCALE_GAIN: float = 0.9

_TILT_LIMIT = 0.6                       # tan(31 deg) clamp on dihedral / beta
_EPS = 1.0e-12
_FAILURE_INDEX_CAP = 1.0e12


# --------------------------------------------------------------------------
# Internal coercion / geometry helpers
# --------------------------------------------------------------------------
def _finite_or(value: Any, default: float) -> float:
    """Coerce to a finite float, falling back to ``default``."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(out):
        return float(default)
    return out


def _sheet_thickness(paper_gsm: float) -> float:
    """Return the sheet thickness in metres from a grammage in g/m^2.

    ``t = gsm / 800`` is the thickness in **millimetres** for an uncreped
    bulk density of 800 kg/m^3; 1 g/m^2 is 1e-3 kg/m^2, so the SI value is
    ``gsm / 8.0e5`` m.  Floored at 1e-6 m so the section modulus stays finite
    for a degenerate zero-grammage configuration.
    """
    return max((max(float(paper_gsm), 0.0) * 1.0e-3) / PAPER_THICKNESS_DENOMINATOR, 1.0e-6)


def _root_chord(area: float, span: float, taper: float) -> float:
    return 2.0 * area / max(span * (1.0 + taper), _EPS)


# --------------------------------------------------------------------------
# Scalar building blocks
# --------------------------------------------------------------------------
def dynamic_pressure(density_kgm3: float, speed_mps: float) -> float:
    """Return the dynamic pressure ``q = 0.5 rho V^2`` in pascals.

    Both arguments are floored at zero: a negative density or airspeed is
    not physical and would produce a negative (attractive) load.
    """
    rho = _finite_or(float(density_kgm3), 0.0)
    speed = _finite_or(float(speed_mps), 0.0)
    if rho <= 0.0 or speed <= 0.0:
        return 0.0
    return 0.5 * rho * speed * speed


def load_factor(dynamic_pressure_pa: float, weight_n: float) -> float:
    """Normalised aerodynamic load ``q / W``.

    This is the load factor of a unit-lift-coefficient reference wing; the
    full aircraft load factor (which also carries the trimmed lift
    coefficient and the reference area) is
    :func:`aircraft_load_factor`.
    """
    q = _finite_or(float(dynamic_pressure_pa), 0.0)
    weight = _finite_or(float(weight_n), 0.0)
    if q <= 0.0 or weight <= 0.0:
        return 0.0
    return safe_div(q, weight, default=0.0)


def aircraft_weight(cfg: AircraftConfig, env: EnvironmentConfig) -> float:
    """Return the aircraft weight ``W = m g`` in newtons."""
    mass = _finite_or(float(cfg.mass_kg), 0.0)
    gravity = _finite_or(float(env.gravity), 0.0)
    if mass <= 0.0 or gravity <= 0.0:
        return 0.0
    return mass * gravity


def lift_curve_slope(cfg: AircraftConfig, derived: Optional[Dict[str, float]] = None) -> float:
    """Return ``CL_alpha`` per radian (Helmbold for a triangular wing).

    Uses ``cfg.cLalpha_per_rad`` when it is positive, otherwise the Helmbold
    estimate ``2 pi AR / (2 + sqrt(AR^2 (1 + lambda) + 4 AR / pi))``.
    """
    if derived is None:
        derived = derived_properties(cfg)
    explicit = _finite_or(float(cfg.cLalpha_per_rad), 0.0)
    if explicit > 0.0:
        return explicit
    ar = max(_finite_or(float(derived["aspect_ratio"]), 1.0e-3), 1.0e-3)
    taper = clamp(_finite_or(float(derived["taper_ratio"]), 0.7), 0.05, 1.0)
    root = ar * ar * (1.0 + taper) + 4.0 * ar / math.pi
    slope = 2.0 * math.pi * ar / (2.0 + math.sqrt(max(root, _EPS)))
    return max(slope, 1.0e-3)


def _elevon_pitching_increment(cfg: AircraftConfig) -> float:
    """``dC_m`` contributed by the elevon, or 0.0 when aero is unavailable."""
    try:
        from physics import aero_domain

        effects = aero_domain.elevon_effects(_finite_or(float(cfg.elevon_deg), 0.0), cfg)
        return _finite_or(float(effects.get("dcm", 0.0)), 0.0)
    except Exception:  # pragma: no cover - aero_domain is optional here
        return 0.0


def trim_alpha_deg(cfg: AircraftConfig, derived: Optional[Dict[str, float]] = None) -> float:
    """Angle of attack that nulls the pitching moment, in degrees.

    ``C_m = C_m0 + C_m_alpha * alpha + dC_m(elevon) = 0`` so, with the stable
    ``C_m_alpha < 0`` convention, ``alpha_trim = -(C_m0 + dC_m) / C_m_alpha``.
    This is the attitude a trimmed airframe actually flies at; assuming half
    the stall angle instead overstates the load factor by more than 2x.
    """
    if derived is None:
        derived = derived_properties(cfg)
    cm_alpha = _finite_or(float(cfg.cm_alpha), -1.0)
    if abs(cm_alpha) < 1.0e-6:
        return clamp(0.5 * _finite_or(float(cfg.stall_alpha_deg), 10.0), 0.25, 15.0)
    cm_total = _finite_or(float(cfg.cm0), 0.0) + _elevon_pitching_increment(cfg)
    stall = _finite_or(float(cfg.stall_alpha_deg), 14.0)
    return clamp(rad2deg(-cm_total / cm_alpha), -stall, stall)


def trim_lift_coefficient(
    cfg: AircraftConfig, derived: Optional[Dict[str, float]] = None
) -> float:
    """Return the trimmed lift coefficient used for the load factor.

    Evaluated on the *same* lift curve the flight integrator uses, at the
    moment-balanced trim AoA (:func:`trim_alpha_deg`), so the structural load
    factor can never disagree with the aerodynamics that produced it::

        alpha_trim = -(C_m0 + dC_m(elevon)) / C_m_alpha
        C_L_trim   = lift_coefficient(alpha_trim, cfg)

    Falls back to the local linear model ``cL0 + CL_alpha * alpha_trim`` when
    ``aero_domain`` cannot be imported.
    """
    if derived is None:
        derived = derived_properties(cfg)
    alpha_ref_deg = trim_alpha_deg(cfg, derived)
    cl_max = max(_finite_or(float(cfg.cl_max), 0.9), 0.05)
    try:
        from physics import aero_domain

        cl = aero_domain.lift_coefficient(alpha_ref_deg, cfg, compressible=False)
        if math.isfinite(float(cl)):
            return clamp(float(cl), 0.02, cl_max)
    except Exception:  # pragma: no cover - aero_domain is optional here
        pass
    slope = lift_curve_slope(cfg, derived)
    cl_ref = _finite_or(float(cfg.cL0), 0.0) + slope * deg2rad(alpha_ref_deg)
    return clamp(cl_ref, 0.02, cl_max)


def aircraft_load_factor(
    cfg: AircraftConfig,
    dynamic_pressure_pa: float,
    weight_n: float,
    derived: Optional[Dict[str, float]] = None,
) -> float:
    """Return the true load factor ``nz = L / W`` with ``L = q S CL_trim``.

    Exactly 1.0 at the trim speed returned by :func:`trim_speed`, and scales
    with ``q`` (hence with ``V^2``).
    """
    if derived is None:
        derived = derived_properties(cfg)
    q = _finite_or(float(dynamic_pressure_pa), 0.0)
    weight = _finite_or(float(weight_n), 0.0)
    if q <= 0.0 or weight <= 0.0:
        return 0.0
    area = max(_finite_or(float(derived["ref_area_m2"]), 0.0), _EPS)
    lift = q * area * trim_lift_coefficient(cfg, derived)
    return max(safe_div(lift, weight, default=0.0), 0.0)


def trim_speed(
    cfg: AircraftConfig,
    env: EnvironmentConfig,
    density_kgm3: float = 1.225,
    derived: Optional[Dict[str, float]] = None,
) -> float:
    """Return the steady-glide trim speed in m/s (``q S CL_trim = W``)."""
    if derived is None:
        derived = derived_properties(cfg)
    weight = aircraft_weight(cfg, env)
    if weight <= 0.0:
        return 0.0
    area = max(_finite_or(float(derived["ref_area_m2"]), 0.0), _EPS)
    cl = trim_lift_coefficient(cfg, derived)
    rho = _finite_or(float(density_kgm3), 0.0)
    if rho <= 0.0 or area <= 0.0 or cl <= 0.0:
        return 0.0
    return math.sqrt(2.0 * weight / (rho * area * cl))


def wing_root_bending_moment(
    dynamic_pressure_pa: float,
    wing_area_m2: float,
    wingspan_m: float,
    load_factor: float = 1.0,
    cfg: Optional[AircraftConfig] = None,
    gravity: float = 9.80665,
) -> float:
    """Half-wing root moment from a single normal load.

    With cfg, load_factor is aircraft nz and F = m*g*abs(nz); q is not
    multiplied again. Without cfg, the fourth argument is a normal-force
    coefficient and F=q*S*abs(coefficient), with uniform span loading.
    """
    q = max(_finite_or(dynamic_pressure_pa, 0.0), 0.0)
    area = max(_finite_or(wing_area_m2, 0.0), 0.0)
    span = max(_finite_or(wingspan_m, 0.0), 0.0)
    nz = abs(_finite_or(load_factor, 0.0))
    if cfg is None:
        force = q * area * nz
    elif gravity > 0.05:
        force = max(cfg.mass_kg, 0.0) * gravity * nz
    else:
        force = q * area * nz
    taper = 1.0 if cfg is None else clamp(cfg.taper_ratio, 0.05, 1.0)
    return force * span * (1.0 + 2.0 * taper) / (12.0 * (1.0 + taper))


def effective_paper_stiffness(cfg: AircraftConfig) -> float:
    """Return effective bending stiffness of the paper taking into account fiber grain anisotropy.

    Real paper has Machine Direction (MD) and Cross Direction (CD) with Young's
    modulus ratio E_MD / E_CD ~ 1.5..2.2 (``cfg.paper_anisotropy_ratio``, default 1.8).
    Using ellipse interpolation:
        E(theta) = E_MD / sqrt(cos(theta)^2 + (E_MD/E_CD)^2 * sin(theta)^2)
    where theta = cfg.paper_grain_angle_deg (0 deg = MD longitudinal, 90 deg = CD transverse).

    Returns an effective stiffness value scaled relative to isotropic stiffness:
        effective_stiffness = cfg.paper_stiffness * (E(theta) / E_MD)
    clamped to >= 0.0.
    """
    base_stiffness = max(_finite_or(float(cfg.paper_stiffness), 0.35), 0.0)
    theta_rad = deg2rad(_finite_or(float(cfg.paper_grain_angle_deg), 0.0))
    ratio = max(_finite_or(float(cfg.paper_anisotropy_ratio), 1.8), 1.0)

    cos_t = math.cos(theta_rad)
    sin_t = math.sin(theta_rad)
    denom = math.sqrt(cos_t * cos_t + (ratio * ratio) * (sin_t * sin_t))
    anisotropy_factor = 1.0 / max(denom, _EPS)
    return max(base_stiffness * anisotropy_factor, 0.0)


def aeroelastic_deformations(
    cfg: AircraftConfig,
    dynamic_pressure: float,
    nz: float = 1.0,
) -> Dict[str, float]:
    """Compute dynamic aeroelastic deformations due to dynamic pressure and g-load.

    Returns:
        {
            "delta_dihedral_deg": float,  # wing bending flex / dynamic dihedral increment
            "washout_twist_deg": float,   # wingtip washout twist
            "effective_stiffness": float, # effective paper stiffness considering grain
        }

    When ``cfg.aeroelastic_flex == 0.0`` and ``cfg.aeroelastic_twist == 0.0``,
    both deformations return 0.0 exactly.
    """
    eff_stiff = max(effective_paper_stiffness(cfg), 0.01)
    q = max(_finite_or(float(dynamic_pressure), 0.0), 0.0)
    load = max(_finite_or(float(nz), 0.0), 0.0)

    flex_gain = _finite_or(float(cfg.aeroelastic_flex), 0.0)
    twist_gain = _finite_or(float(cfg.aeroelastic_twist), 0.0)

    if (flex_gain == 0.0 and twist_gain == 0.0) or q == 0.0:
        return {
            "delta_dihedral_deg": 0.0,
            "washout_twist_deg": 0.0,
            "effective_stiffness": float(eff_stiff),
        }

    derived = derived_properties(cfg)
    area = max(_finite_or(float(derived.get("ref_area_m2") or derived["wing_area_m2"]), 0.0), _EPS)
    span = max(_finite_or(float(derived.get("ref_span_m") or derived["wingspan_m"]), 0.0), _EPS)
    c_mac = max(_finite_or(float(derived.get("mac_m") or derived["ref_length_m"]), 0.0), _EPS)

    gsm = max(_finite_or(float(cfg.paper_gsm), 0.0), 0.0)
    thickness = _sheet_thickness(gsm)
    t3 = max(thickness ** 3, 1.0e-18)
    denom = max(eff_stiff * t3, 1.0e-18)

    # Empirical compliance [deg*m^2/N]; paper_stiffness is dimensionless.
    # nz already carries dynamic pressure through L/W, so use force only once.
    norm_scale = 1.0e-10
    normal_force = max(cfg.mass_kg, 0.0) * 9.80665 * load
    delta_dihedral = clamp((normal_force * (span * 0.5) * flex_gain * norm_scale) / denom, -30.0, 30.0)
    washout_twist = clamp((q * area * c_mac * twist_gain * norm_scale) / denom, -20.0, 20.0)

    return {
        "delta_dihedral_deg": float(delta_dihedral),
        "washout_twist_deg": float(washout_twist),
        "effective_stiffness": float(eff_stiff),
    }


def dihedral_roll_couple(
    cfg: AircraftConfig, dynamic_pressure_pa: float, load_factor_value: float = 1.0
) -> float:
    """Return the dihedral rolling moment about the roll axis in N m.

    ``C_l = DIHEDRAL_ROLL_DERIVATIVE * tan(Lambda)``, ``M = q S b C_l nz``.
    Positive dihedral rolls to port, so the returned value is positive.
    """
    q = _finite_or(float(dynamic_pressure_pa), 0.0)
    nz = _finite_or(float(load_factor_value), 0.0)
    if q <= 0.0 or nz <= 0.0:
        return 0.0
    derived = derived_properties(cfg)
    tilt = clamp(
        math.tan(deg2rad(_finite_or(float(cfg.dihedral_deg), 0.0))),
        -_TILT_LIMIT,
        _TILT_LIMIT,
    )
    area = max(_finite_or(float(derived["ref_area_m2"]), 0.0), _EPS)
    span = max(_finite_or(float(derived["ref_span_m"]), 0.0), _EPS)
    return q * area * span * DIHEDRAL_ROLL_DERIVATIVE * tilt * nz


def paper_allowable_stress(paper_gsm: float, paper_stiffness: float) -> float:
    """Return the allowable fold stress in pascals.

    ``A = 2500 (gsm/100)^0.5 (0.6 + 0.8 stiffness)``

    The base value 2500 Pa is an unvalidated empirical calibration for
    100 g/m^2 stock, not a measured material strength.  The square-root grammage scaling follows the tear force of a
    crease, which grows with the number of stacked fibres; the linear
    stiffness term models the loss of fold sharpness (and therefore of
    usable tear strength) as the paper is made stiffer.  Monotonically
    increasing in both arguments.
    """
    gsm = max(_finite_or(float(paper_gsm), 0.0), 0.0)
    stiffness = max(_finite_or(float(paper_stiffness), 0.0), 0.0)
    return (
        ALLOWABLE_BASE_PA
        * (gsm / 100.0) ** ALLOWABLE_GSM_EXPONENT
        * (ALLOWABLE_STIFFNESS_OFFSET + ALLOWABLE_STIFFNESS_GAIN * stiffness)
    )


def failure_index(max_stress_pa: float, allowable_pa: float) -> float:
    """Return the failure index ``max_stress / allowable`` (clamped >= 0).

    A zero or non-finite allowable cannot be divided by; a zero allowable
    with a positive stress is treated as an unbounded utilisation, capped at
    :data:`_FAILURE_INDEX_CAP` so the payload stays finite.
    """
    stress = _finite_or(float(max_stress_pa), 0.0)
    allowable = _finite_or(float(allowable_pa), 0.0)
    if stress <= 0.0:
        return 0.0
    if allowable <= 0.0:
        return _FAILURE_INDEX_CAP
    return clamp(safe_div(stress, allowable, default=_FAILURE_INDEX_CAP), 0.0, _FAILURE_INDEX_CAP)


def status_for_failure_index(index: float) -> str:
    """Map a failure index onto ``"ok"`` / ``"high_load"`` / ``"failed"``."""
    value = _finite_or(float(index), 0.0)
    if value >= STATUS_FAILED_THRESHOLD:
        return STATUS_FAILED
    if value >= STATUS_HIGH_LOAD_THRESHOLD:
        return STATUS_HIGH_LOAD
    return STATUS_OK


# --------------------------------------------------------------------------
# Heat-map helpers
# --------------------------------------------------------------------------
def normalise_heatmap(values: Any) -> np.ndarray:
    """Min-max normalise ``values`` onto ``[0, 1]``.

    A constant (or zero-range) input returns an array of ``0.5`` so a dead
    field renders as neutral instead of as a false gradient.  Non-finite
    entries are replaced by the mean of the finite entries before the
    min-max, which keeps the output finite for any input.
    """
    array = np.asarray(values, dtype=float)
    if array.size == 0:
        return array.copy()
    finite = np.isfinite(array)
    if not bool(finite.all()):
        good = array[finite]
        fill = float(good.mean()) if good.size else 0.0
        array = np.where(finite, array, fill)
    low = float(array.min())
    high = float(array.max())
    span = high - low
    if not math.isfinite(span) or span <= _EPS:
        return np.full(array.shape, 0.5, dtype=float)
    return (array - low) / span


def heatmap_colour(index: float) -> Tuple[int, int, int]:
    """Return an ``(r, g, b)`` int triple for a heat-map index in ``[0, 1]``.

    Triphasic ramp: blue ``(0, 0, 255)`` at 0.0, green at 0.5, red
    ``(255, 0, 0)`` at 1.0.  The red channel is monotonically increasing and
    the blue channel monotonically decreasing across the ramp.
    """
    t = clamp(_finite_or(float(index), 0.0), 0.0, 1.0)
    red = int(round(255.0 * t))
    blue = int(round(255.0 * (1.0 - t)))
    green = int(round(255.0 * min(1.0, 4.0 * t * (1.0 - t))))
    return (red, green, blue)


def _probe_cupy():
    """Probe whether CuPy is usable on an available CUDA GPU."""
    try:
        import cupy as cp
        if int(cp.cuda.runtime.getDeviceCount()) > 0:
            return cp
    except Exception:
        pass
    return None


_CUPY_MODULE = _probe_cupy()


def active_backend(force_cpu: bool = False) -> str:
    """Return the array backend actually used for the stress grid.

    Returns ``"cupy"`` when CUDA GPU hardware is available, else ``"numpy"``.
    """
    if force_cpu:
        return "numpy"
    return "cupy" if _CUPY_MODULE is not None else "numpy"



def critical_point_body(
    cfg: AircraftConfig,
    chordwise_frac: float,
    spanwise_frac: float,
    derived: Optional[Dict[str, float]] = None,
) -> np.ndarray:
    """Return the body-frame ``(x, y, z)`` position of a heat-map station.

    ``chordwise_frac`` runs 0 (leading edge) to 1 (trailing edge) and
    ``spanwise_frac`` runs -1 (port tip) to +1 (starboard tip), matching the
    sign convention of the ``spanwise`` axis of :func:`stress_field`.  Used
    by the UI to place the "peak stress" marker on the 3D model.
    """
    if derived is None:
        derived = derived_properties(cfg)
    area = max(_finite_or(float(derived["wing_area_m2"]), 0.0), _EPS)
    span = max(_finite_or(float(derived["wingspan_m"]), 0.0), _EPS)
    taper = clamp(_finite_or(float(derived["taper_ratio"]), 0.7), 0.05, 1.0)
    xi = clamp(_finite_or(float(chordwise_frac), 0.0), 0.0, 1.0)
    yi = clamp(_finite_or(float(spanwise_frac), 0.0), -1.0, 1.0)
    a = abs(yi)
    c_root = 2.0 * area / (span * (1.0 + taper))
    chord = c_root * (1.0 - (1.0 - taper) * a)
    half_chord = chord * 0.5
    # Orthonormal wing frame from the shared rotation helpers: the drag axis
    # points forward (+x), the span axis to starboard (+y), the lift axis down
    # (+z).  The station is measured from the aerodynamic centre, so the chord
    # runs nose (+x) to tail (-x).
    drag_axis = normalize(vec3((1.0, 0.0, 0.0)))
    lift_axis = normalize(vec3((0.0, 0.0, 1.0)))
    span_axis = cross(lift_axis, drag_axis)
    if span_axis[1] < 0.0:
        span_axis = -span_axis
    span_axis = normalize(span_axis)
    x = half_chord * (1.0 - 2.0 * xi)
    y = yi * span * 0.5
    return drag_axis * x + span_axis * y


# --------------------------------------------------------------------------
# Field construction
# --------------------------------------------------------------------------
def stress_field(
    cfg: AircraftConfig,
    dynamic_pressure_pa: float,
    load_factor: float = 1.0,
    spanwise: int = 24,
    chordwise: int = 16,
    beta_deg: float = 0.0,
    backend: str = "auto",
    gravity: float = 9.80665,
) -> Dict[str, Any]:
    """Build the ``chordwise x spanwise`` wing stress heat map.

    load_factor is the prescribed aircraft normal load divided by m*g.
    Dynamic pressure is metadata here; it is not multiplied into nz again.
    Accelerated on CUDA GPU via CuPy when available.
    """
    derived = derived_properties(cfg)
    ns = int(clamp(int(spanwise), 2, 256))
    nc = int(clamp(int(chordwise), 2, 256))

    # Decide backend (cupy GPU vs numpy CPU)
    xp = np
    actual_backend = "numpy"
    if backend in ("auto", "cupy") and _CUPY_MODULE is not None:
        xp = _CUPY_MODULE
        actual_backend = "cupy"

    q = max(_finite_or(dynamic_pressure_pa, 0.0), 0.0)
    nz = max(_finite_or(load_factor, 0.0), 0.0)
    beta = _finite_or(beta_deg, 0.0)
    dihedral_rad = deg2rad(_finite_or(float(cfg.dihedral_deg), 0.0))
    tilt = clamp(math.tan(dihedral_rad), -_TILT_LIMIT, _TILT_LIMIT)
    side = clamp(math.tan(deg2rad(beta)), -_TILT_LIMIT, _TILT_LIMIT)

    area = max(_finite_or(float(derived.get("ref_area_m2") or derived["wing_area_m2"]), 0.0), _EPS)
    span = max(_finite_or(float(derived.get("ref_span_m") or derived["wingspan_m"]), 0.0), _EPS)
    taper = clamp(_finite_or(float(derived["taper_ratio"]), 0.7), 0.05, 1.0)

    # --- reported semispan: a folded winglet removes loaded span ----------
    tip_factor = abs(math.sin(deg2rad(_finite_or(float(cfg.fold_tip_deg), 0.0))))
    tip_factor = clamp(tip_factor, 0.0, 1.0)
    semispan = (span * 0.5) * (1.0 - TIP_SPAN_REDUCTION * tip_factor)

    # --- root moment -------------------------------------------------------
    root_moment = wing_root_bending_moment(q, area, span, nz, cfg, gravity)

    # --- local chord -------------------------------------------------------
    chord_root = _root_chord(area, span, taper)
    te_fold = abs(math.sin(deg2rad(_finite_or(float(cfg.fold_trailing_deg), 0.0))))
    chord_scale = 1.0 - TE_FOLD_CHORD_REDUCTION * clamp(te_fold, 0.0, 1.0)

    # --- section properties ------------------------------------------------
    gsm = max(_finite_or(float(cfg.paper_gsm), 0.0), 0.0)
    thickness = _sheet_thickness(gsm)
    eff_stiffness = effective_paper_stiffness(cfg)
    stiffness = max(eff_stiffness, 0.0)
    z_gain = max(Z_SCALE_OFFSET + Z_SCALE_GAIN * stiffness, _EPS)

    # --- spanwise axis (full span, centre-line at i_c) ---------------------
    centre = (ns - 1) * 0.5
    half_index = max(centre, _EPS)
    i_axis = xp.arange(ns, dtype=float)
    y_signed = (i_axis - centre) / half_index
    y_abs = xp.abs(y_signed)

    # Exact cantilever integral for w(u) proportional to 1-(1-taper)*u.
    # Integral_a^1 [1-(1-taper)*u]*(u-a) du.
    remaining = xp.clip(1.0 - y_abs, 0.0, 1.0)
    integral = taper * remaining**2 / 2.0 + (1.0 - taper) * remaining**3 / 6.0
    root_integral = (1.0 + 2.0 * taper) / 6.0
    moment = root_moment * integral / root_integral
    # A tip fold modifies the section capacity, not a fictitious free-tip moment.
    moment = moment * (1.0 + TIP_MOMENT_GAIN * tip_factor)

    # dihedral roll couple + span load transfer + sideslip side load
    load_mult = 1.0 - DIHEDRAL_LOAD_COUPLING * tilt * y_signed
    couple_mult = 1.0 + DIHEDRAL_MOMENT_COUPLING * tilt * y_signed
    side_mult = 1.0 + SIDELOAD_COUPLING * side * y_signed
    moment = moment * xp.maximum(load_mult, 0.05) * xp.maximum(couple_mult, 0.05) * xp.maximum(
        side_mult, 0.05
    )

    local_chord = chord_root * (1.0 - (1.0 - taper) * y_abs) * chord_scale

    # --- chordwise axis ----------------------------------------------------
    j_axis = xp.arange(nc, dtype=float)
    x_axis = j_axis / max(nc - 1, 1)
    arm = ELEVON_ARM_GAIN * (
        abs(deg2rad(_finite_or(float(cfg.elevon_deg), 0.0)))
        + abs(deg2rad(_finite_or(float(cfg.fold_trailing_deg), 0.0)))
    )
    shape = xp.power(xp.clip(1.0 - x_axis, 0.0, 1.0), CHORDWISE_EXPONENT)
    shape = shape + ELEVON_TE_GAIN * arm * xp.power(x_axis, 4.0)

    # --- cell bending stress [Pa] -----------------------------------------
    section_modulus = xp.maximum(local_chord * thickness * thickness * z_gain / 6.0, 1.0e-30)
    stress = (KAPPA_SIGMA * moment / section_modulus)[:, xp.newaxis] * shape[xp.newaxis, :]
    stress = xp.nan_to_num(stress.T, nan=0.0, posinf=0.0, neginf=0.0)
    stress = xp.maximum(stress, 0.0)

    # Convert to NumPy for host formatting if computed on GPU
    stress_np = stress.get() if hasattr(stress, "get") else np.asarray(stress)

    max_stress = float(stress_np.max())
    mean_stress = float(stress_np.mean())

    # --- heat-map index ----------------------------------------------------
    shape_index = normalise_heatmap(stress_np)
    head = clamp(max_stress / INDEX_REFERENCE_PA, 0.0, 1.0)
    index_grid = shape_index * head
    index_grid = np.clip(np.nan_to_num(index_grid, nan=0.0), 0.0, 1.0)

    # argmax: axis 0 walks the span, axis 1 the chord
    i_arg = int(np.argmax(index_grid.max(axis=0)))
    j_arg = int(np.argmax(index_grid[:, i_arg]))
    critical_span = clamp(abs(i_arg - centre) / half_index, 0.0, 1.0)
    critical_chord = clamp(j_arg / max(nc - 1, 1), 0.0, 1.0)


    allowable = paper_allowable_stress(gsm, stiffness)
    fi = failure_index(max_stress, allowable)
    status = status_for_failure_index(fi)

    colours = [
        [list(heatmap_colour(float(value))) for value in row] for row in index_grid
    ]

    return {
        "backend": actual_backend,
        "material_model_validated": False,
        "stress_index": [[float(v) for v in row] for row in index_grid],
        "stress_pa": [[float(v) for v in row] for row in stress_np],
        "spanwise": ns,
        "chordwise": nc,
        "max_stress_index": float(index_grid.max()),
        "min_stress_index": float(index_grid.min()),
        "max_stress_pa": max_stress,
        "mean_stress_pa": mean_stress,
        "wing_root_moment_nm": float(root_moment),
        "load_factor": float(nz),
        "dynamic_pressure_pa": float(q),
        "allowable_stress_pa": float(allowable),
        "failure_index": float(fi),
        "status": status,
        "dihedral_rad": float(dihedral_rad),
        "semispan_m": float(semispan),
        "colours": colours,
        "critical_spanwise_frac": float(critical_span),
        "critical_chordwise_frac": float(critical_chord),
    }


def analyse(
    cfg: AircraftConfig,
    env: EnvironmentConfig,
    speed_mps: float,
    density_kgm3: float,
    load_factor: Optional[float] = None,
    spanwise: int = 24,
    chordwise: int = 16,
    beta_deg: float = 0.0,
    backend: str = "auto",
) -> Dict[str, Any]:
    """End-to-end structural analysis for the REST layer.

    Computes the dynamic pressure from ``density_kgm3`` and ``speed_mps``;
    when ``load_factor`` is ``None`` it is derived from the trimmed lift
    coefficient and the aircraft weight, otherwise the supplied value is
    used verbatim (floored at zero).  Returns every key of
    :func:`stress_field` plus ``speed_mps``, ``density_kgm3`` and
    ``backend``.
    """
    speed = max(_finite_or(speed_mps, 0.0), 0.0)
    density = max(_finite_or(density_kgm3, 0.0), 0.0)
    q = dynamic_pressure(density, speed)

    if load_factor is None:
        derived = derived_properties(cfg)
        nz = aircraft_load_factor(cfg, q, aircraft_weight(cfg, env), derived)
    else:
        nz = max(_finite_or(load_factor, 0.0), 0.0)

    out = stress_field(
        cfg=cfg,
        dynamic_pressure_pa=q,
        load_factor=nz,
        spanwise=spanwise,
        chordwise=chordwise,
        beta_deg=beta_deg,
        backend=backend,
        gravity=env.gravity,
    )
    out["speed_mps"] = float(speed)
    out["density_kgm3"] = float(density)
    out["backend"] = out.get("backend", active_backend())
    return out


#: US spelling alias.
analyze = analyse

