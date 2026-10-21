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

Grid axis contract: every 2-D array published by :func:`stress_field`
(``stress_index``, ``stress_shape``, ``stress_pa``, ``colours``) is indexed
``grid[chordwise][spanwise]`` -- the **row** walks the chord from the leading
edge (row 0) to the trailing edge, the **column** walks the full span from
the port tip (column 0) through the wing root (the centre column for an odd
``spanwise``, the two columns straddling it for an even one) to the
starboard tip.  The cantilever peak therefore belongs to the wing-root
leading edge, i.e. ``grid[0][(spanwise - 1) // 2]``.  The contract is
spelled out in :data:`STRESS_GRID_AXES` and checked at every call, so a
silent transpose cannot reach the payload: the published shape is verified
against the requested resolution, and the peak is additionally required to
sit on the wing-root column of the span axis -- a check that also catches a
transpose of a *square* grid, where the shape alone is ambiguous.  The
reported peak station is read from the true stress field, so a saturated
(failed) airframe still reports the root and not a wing tip.
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
    STANDARD_GRAVITY,
)
from .aero_domain import elevon_effects, helmbold_cl_alpha
from .geometry_utils import cross, normalize, vec3

__all__ = [
    "SPAN_LOADING_FACTOR",
    "KAPPA_SIGMA",
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
    "STRESS_GRID_AXES",
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
    "root_bending_moment_batch",
    "section_modulus_batch",
    "peak_root_stress_pa",
    "root_chord_batch",
    "te_fold_chord_scale",
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
#:
#: **Retired (2026-10-19).**  The heat map used to be normalised against this
#: fixed 25 kPa reference while the failure criterion used the airframe's own
#: :func:`paper_allowable_stress` (2.2 kPa for the default stock) and the GPU
#: batch path used yet a third value (120 kPa).  The three scales made
#: ``max_stress_index`` mean three different things in one payload -- a
#: 1261x spread between the 6-DoF and the GPU path.  The single reference is
#: now :func:`paper_allowable_stress`; ``stress_index`` is the utilisation
#: ``sigma / sigma_allow`` (0 = safe, 1 = failure limit) and the min-max
#: pattern lives in the separate ``stress_shape`` channel.
INDEX_REFERENCE_PA: float = 2.5e4  # noqa: F401 - retained for import compatibility only

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

#: Axis order of every 2-D stress array published by :func:`stress_field`:
#: ``("chordwise", "spanwise")``, i.e. ``grid[row][column]``.
#:
#: The **row** walks the chord: row 0 is the leading edge, row
#: ``chordwise - 1`` the trailing edge.  The **column** walks the full span:
#: column 0 is the port tip, the centre column is the wing root (an exact
#: column for an odd ``spanwise``, the two columns straddling it for an
#: even one) and column ``spanwise - 1`` the starboard tip.  The cantilever
#: peak -- the wing-root leading edge -- is ``grid[0][(spanwise - 1) // 2]``.
#:
#: A client resolves the layout from the ``chordwise`` and ``spanwise``
#: resolution keys published next to the grid, so no axis has to be guessed.
STRESS_GRID_AXES: Tuple[str, str] = ("chordwise", "spanwise")

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

#: Aeroelastic compliance: bend/twist angle (deg) per unit bending stress (Pa).
#: Basis: a thin sheet cantilever of semispan ``b/2`` under end moment ``M``
#: rotates by ``theta = M * (b/2) / (E I)``; approximating flexural rigidity
#: as ``eff_stiff * t^3`` makes ``moment / denominator`` a stress in Pa, so
#: the gain converts pascals to degrees.  Units: deg / Pa.  The value is an
#: unvalidated empirical calibration, retained without retuning.
AEROELASTIC_COMPLIANCE_DEG_PER_PA: float = 1.0e-10

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
# Vectorised structural model (shared with the GPU batch path)
# --------------------------------------------------------------------------
def root_bending_moment_batch(
    xp: Any,
    load_factor: Any,
    mass_kg: Any,
    gravity: float,
    span_m: Any,
    taper_ratio: Any,
) -> Any:
    """Half-wing root bending moment of a batch of airframes.

    Vectorised twin of :func:`wing_root_bending_moment`: the root carries the
    largest moment of the half wing, ``M = F b (1 + 2 lambda) / (12 (1 +
    lambda))`` with ``F = m g |nz|`` the aircraft weight times the load
    factor.
    """
    nz = xp.abs(load_factor)
    force = mass_kg * gravity * nz
    taper = xp.clip(taper_ratio, 0.05, 1.0)
    return force * span_m * (1.0 + 2.0 * taper) / (12.0 * (1.0 + taper))


def section_modulus_batch(
    xp: Any, chord_m: Any, thickness_m: Any, z_gain: Any
) -> Any:
    """Section modulus ``Z = b t^2 / 6`` of the folded root section."""
    return xp.maximum(chord_m * thickness_m * thickness_m * z_gain / 6.0, 1.0e-30)


def peak_root_stress_pa(
    xp: Any,
    load_factor: Any,
    mass_kg: Any,
    gravity: float,
    span_m: Any,
    taper_ratio: Any,
    root_chord_m: Any,
    thickness_m: Any,
    z_gain: Any,
    chord_scale: Any = 1.0,
    tip_factor: Any = 0.0,
) -> Any:
    """Peak wing-root bending stress of a batch of airframes, in pascals.

    This is the *scalar reduction* of :func:`stress_field`: the root section
    carries the largest bending moment and the leading edge the largest
    chordwise shape factor, so the peak of the stress field is the root
    section evaluated at the leading edge.

    The GPU batch path evaluates this function instead of an inline formula
    of its own.  That inline formula used a *span* based section modulus and
    a completely different load path, which is why the GPU path reported
    355 388 Pa where the 6-DoF path reported 281.8 Pa for the same flight --
    a factor of 1261 in the very quantity that decides whether the airframe
    is considered to have failed.
    """
    moment = root_bending_moment_batch(
        xp, load_factor, mass_kg, gravity, span_m, taper_ratio
    )
    # A folded winglet raises the moment carried by the root section.
    moment = moment * (1.0 + TIP_MOMENT_GAIN * xp.clip(tip_factor, 0.0, 1.0))
    modulus = section_modulus_batch(xp, root_chord_m * chord_scale, thickness_m, z_gain)
    return KAPPA_SIGMA * moment / modulus


def root_chord_batch(xp: Any, area_m2: Any, span_m: Any, taper_ratio: Any) -> Any:
    """Root chord ``c_root = 2 S / (b (1 + lambda))`` of a batch of airframes."""
    taper = xp.clip(taper_ratio, 0.05, 1.0)
    return 2.0 * area_m2 / xp.maximum(span_m * (1.0 + taper), _EPS)


def te_fold_chord_scale(xp: Any, fold_trailing_deg: Any) -> Any:
    """Effective-chord reduction caused by the trailing-edge fold."""
    fold = xp.abs(xp.sin(_rad_batch(xp, fold_trailing_deg)))
    return 1.0 - TE_FOLD_CHORD_REDUCTION * xp.clip(fold, 0.0, 1.0)


def _rad_batch(xp: Any, value: Any) -> Any:
    """Degrees to radians for a batch (NumPy or CuPy)."""
    return xp.asarray(value, dtype=xp.float64) * (math.pi / 180.0)


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
    estimate ``2 pi AR / (2 + sqrt(AR^2 (1 + lambda) + 4 AR / pi))`` taken from
    the single implementation of :func:`physics.aero_domain.helmbold_cl_alpha`.
    The audit found four different copies of this expression in the project
    (reference path, two GPU paths and the specification text), which made the
    structural and the aerodynamic lift slope differ by up to 11 %.
    """
    if derived is None:
        derived = derived_properties(cfg)
    explicit = _finite_or(float(cfg.cLalpha_per_rad), 0.0)
    if explicit > 0.0:
        return explicit
    ar = max(_finite_or(float(derived["aspect_ratio"]), 1.0e-3), 1.0e-3)
    taper = _finite_or(float(derived["taper_ratio"]), 0.7)
    return max(helmbold_cl_alpha(ar, taper), 1.0e-3)


def _elevon_pitching_increment(cfg: AircraftConfig) -> float:
    """``dC_m`` contributed by the elevon.

    ``aero_domain`` is imported at module load, so it is always available here;
    a pitch moment increment is never silently dropped behind a swallowed
    exception.
    """
    effects = elevon_effects(_finite_or(float(cfg.elevon_deg), 0.0), cfg)
    return _finite_or(float(effects.get("dcm", 0.0)), 0.0)


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

    # Empirical compliance [deg/Pa]; paper_stiffness is dimensionless.
    # nz already carries dynamic pressure through L/W, so use force only once.
    normal_force = max(cfg.mass_kg, 0.0) * STANDARD_GRAVITY * load
    delta_dihedral = clamp(
        (normal_force * (span * 0.5) * flex_gain * AEROELASTIC_COMPLIANCE_DEG_PER_PA) / denom,
        -30.0,
        30.0,
    )
    washout_twist = clamp(
        (q * area * c_mac * twist_gain * AEROELASTIC_COMPLIANCE_DEG_PER_PA) / denom,
        -20.0,
        20.0,
    )

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
    sign convention of the internal ``spanwise`` axis of :func:`stress_field`.
    The published ``critical_spanwise_frac`` is the *unsigned* distance from
    the wing root (0 = root, 1 = tip), so a caller that needs the side of
    the root takes the sign from the peak station itself.  Used by the UI to
    place the "peak stress" marker on the 3D model.
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

    The published grid is indexed ``grid[chordwise][spanwise]``: the row
    walks the chord from the leading edge (row 0) to the trailing edge, the
    column walks the full span from the port tip (column 0) through the wing
    root (the centre column for an odd ``spanwise``, the two columns
    straddling it for an even one) to the starboard tip.  The cantilever
    peak is the wing-root leading edge, i.e. ``grid[0][(spanwise - 1) // 2]``,
    and ``critical_chordwise_frac`` / ``critical_spanwise_frac`` report its
    station (0 = leading edge / wing root).  The ``chordwise`` and
    ``spanwise`` resolution keys published beside the grid let a client
    resolve the layout without guessing; the contract itself is
    :data:`STRESS_GRID_AXES`.

    The peak station is read from the true stress field, not from the
    clipped ``stress_index`` grid, so an airframe whose utilisation
    saturates (index >= 1, ``status`` ``"failed"``) still reports the wing
    root; and an unloaded wing, whose field is identically zero and has no
    peak at all, reports the nominal wing-root leading edge.

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
    if backend == "cupy":
        if _CUPY_MODULE is None:
            raise RuntimeError(
                "CuPy GPU backend requested for stress field calculation, but CUDA accelerator is unavailable. "
                "Hardware acceleration (CUDA GPU) is required and CPU fallback is strictly prohibited."
            )
        xp = _CUPY_MODULE
        actual_backend = "cupy"
    elif backend == "auto":
        if _CUPY_MODULE is not None:
            xp = _CUPY_MODULE
            actual_backend = "cupy"
        else:
            actual_backend = "numpy"
    elif backend == "numpy":
        xp = np
        actual_backend = "numpy"
    else:
        raise ValueError(f"unknown stress backend '{backend}'; expected 'auto', 'cupy' or 'numpy'")


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
    # A folded winglet raises the moment carried by the root section; the
    # reported root moment uses the same tip-gain moment as the stress field
    # so that stress-derived moment and reported moment stay consistent.
    root_moment_with_tip = root_moment * (1.0 + TIP_MOMENT_GAIN * tip_factor)

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
    # Dihedral tilts the wing, so the lift normal to the surface projects onto
    # the body vertical by cos(delta) -- identical on both wings.  The spanwise
    # load is therefore symmetric in symmetric flight: dihedral bends both
    # wingtips equally instead of splitting load port/starboard.  The former
    # ``tilt * y_signed`` terms broke symmetry and are removed; the dihedral
    # roll moment is reported separately by dihedral_roll_couple().
    load_mult = 1.0 / math.sqrt(1.0 + tilt * tilt)
    couple_mult = 1.0
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
    # The broadcast below is (spanwise, chordwise): one row per span
    # station, one column per chord station.  The published grid is the
    # other way round -- (chordwise, spanwise), see ``STRESS_GRID_AXES`` --
    # so the transpose is part of the payload contract, not a formatting
    # detail.
    section_modulus = xp.maximum(local_chord * thickness * thickness * z_gain / 6.0, 1.0e-30)
    stress_span_chord = (KAPPA_SIGMA * moment / section_modulus)[:, xp.newaxis] * shape[
        xp.newaxis, :
    ]
    stress = xp.nan_to_num(stress_span_chord.T, nan=0.0, posinf=0.0, neginf=0.0)
    stress = xp.maximum(stress, 0.0)

    # Convert to NumPy for host formatting if computed on GPU
    stress_np = stress.get() if hasattr(stress, "get") else np.asarray(stress)

    # The published contract is (chordwise, spanwise).  A transpose that
    # slipped through here would move the wing-root peak into the middle of
    # the grid, so the shape is verified on every call.
    if stress_np.shape != (nc, ns):
        raise RuntimeError(
            "stress grid axis contract violated: expected (chordwise, spanwise) "
            f"= {(nc, ns)}, got {tuple(stress_np.shape)}"
        )

    max_stress = float(stress_np.max())
    mean_stress = float(stress_np.mean())

    # --- heat-map index ----------------------------------------------------
    # Single reference scale: the airframe's own allowable fold stress.  The
    # index is the *utilisation* ``sigma / sigma_allow`` (0 = safe, 1 = the
    # failure limit), so "1.0" finally means the same thing in the heat map,
    # in ``failure_index`` and in the GPU batch path.  The min-max pattern of
    # the load path is published separately as ``stress_shape`` so the shape
    # stays visible without redefining what 1.0 means.
    allowable = paper_allowable_stress(gsm, stiffness)
    fi = failure_index(max_stress, allowable)
    shape_index = normalise_heatmap(stress_np)
    index_grid = np.clip(np.nan_to_num(stress_np / max(allowable, _EPS), nan=0.0), 0.0, 1.0)

    # Peak station.  The published grid is (chordwise, spanwise): reducing
    # over the chordwise axis (axis 0) leaves the spanwise profile, whose
    # largest entry is the spanwise station of the peak; reducing that
    # column over the spanwise axis (axis 1) gives its chordwise station.
    #
    # The station is read from the *true* stress field, never from the
    # clipped utilisation grid.  A saturated (failed) airframe has a whole
    # band of cells at index 1.0, and the first of them in row-major order
    # lies outboard of the wing root, so reading the peak from the clipped
    # grid would report the peak stress at a wing tip instead of at the root.
    if max_stress > 0.0:
        span_index = int(np.argmax(stress_np.max(axis=0)))
        chord_index = int(np.argmax(stress_np[:, span_index]))
        # Orientation contract: the cantilever peak belongs to the wing-root
        # column of the span axis.  The shape check above cannot see a
        # transpose when the grid is square (``spanwise == chordwise``), but
        # the peak station can: a transposed grid puts the peak at a chord
        # station, which is off the root line.
        if abs(span_index - centre) > 0.5:
            raise RuntimeError(
                "stress grid axis contract violated: the peak stress must sit on the "
                f"wing-root column of the span axis (within half a station of "
                f"{centre}), got column {span_index} of {ns}"
            )
        # ``critical_spanwise_frac`` is the unsigned distance from the wing root
        # (0 = root, 1 = tip); ``critical_chordwise_frac`` runs from the leading
        # edge (0) to the trailing edge (1).
        critical_span = clamp(abs(span_index - centre) / half_index, 0.0, 1.0)
        critical_chord = clamp(chord_index / max(nc - 1, 1), 0.0, 1.0)
    else:
        # An unloaded wing carries no stress at all, so the field has no
        # peak.  The nominal cantilever station -- the wing-root leading
        # edge -- is published instead of the first cell of the array, which
        # sits on a wing tip.
        critical_span = 0.0
        critical_chord = 0.0

    status = status_for_failure_index(fi)

    colours = [
        [list(heatmap_colour(float(value))) for value in row] for row in index_grid
    ]

    return {
        "backend": actual_backend,
        "material_model_validated": False,
        "stress_index": [[float(v) for v in row] for row in index_grid],
        "stress_shape": [[float(v) for v in row] for row in shape_index],
        "stress_pa": [[float(v) for v in row] for row in stress_np],
        "spanwise": ns,
        "chordwise": nc,
        "max_stress_index": float(fi),
        "min_stress_index": float(index_grid.min()),
        "max_stress_pa": max_stress,
        "mean_stress_pa": mean_stress,
        "wing_root_moment_nm": float(root_moment_with_tip),
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

