"""Canonical data contract for the 3D Paper Airplane CFD simulator.

Every physics module, the Flask layer and the browser UI must exchange data
through the dataclasses declared here.  The module is intentionally free of
any simulation logic: it only holds constants, pure helpers, dataclasses and
the JSON serialisation contract.

Coordinates (right handed, NED free convention used consistently):
    * x : forward (nose direction)
    * y : right wing
    * z : down  (so "altitude" is reported as -z)

Angles are stored in **degrees** in every dataclass / JSON payload and are
converted to radians at the point of use by the physics modules.
Lengths are metres, times seconds, masses kilograms, temperatures kelvin.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

__all__ = [
    "G0", "R_AIR", "R_VAPOR", "MU0_ISA", "SUTHERLAND_T0", "T0_ISA", "P0_ISA", "LAPSE_RATE_ISA",
    "GAMMA_AIR", "VON_KARMAN", "MU_SUTHERLAND", "STANDARD_GRAVITY",
    "clamp", "deg2rad", "rad2deg", "safe_div", "as_dict", "from_dict",
    "make_uuid", "utc_now_iso", "AircraftConfig", "EnvironmentConfig",
    "LaunchConfig", "StateVector", "SimulationSettings", "AeroState",
    "AeroResult", "PanelData", "MeshMetrics", "OptimizerConfig",
    "DesignVariable",     "JobRecord", "FlightLogSample", "WindSample",
    "derived_properties", "default_config_bundle",
    "FLIGHT_LOG_COLUMNS", "MESH_METRIC_KEYS", "OBJECTIVE_DIRECTIONS",
    "AIRCRAFT_SCHEMA", "ENVIRONMENT_SCHEMA", "LAUNCH_SCHEMA",
    "SIMULATION_SCHEMA",
    "ALGORITHM_NAMES", "DESIGN_VARIABLE_CATALOGUE", "OptimizerDomain",
    "MeshStatus", "ThrowStyle", "THROW_STYLE_CATALOGUE", "throw_style",
    "THROW_SPEED_SOURCES",
]

# --------------------------------------------------------------------------
# Physical constants
# --------------------------------------------------------------------------
STANDARD_GRAVITY: float = 9.80665
G0: float = STANDARD_GRAVITY
R_AIR: float = 287.05
R_VAPOR: float = 461.5
#: Sutherland's reference dynamic viscosity of dry air, ``1.716e-5`` Pa s.  It is
#: tabulated at **0 degC**, i.e. at :data:`SUTHERLAND_T0`, *not* at
#: :data:`T0_ISA` (15 degC, where the real value is 1.789e-5 Pa s).  Pairing the
#: constant with the ISA sea-level temperature makes Sutherland's law understate
#: the viscosity by ~4 % at every altitude -- 3.8 % low at 0 degC -- which flows
#: straight into nu, Re and the Reynolds-corrected C_D0.
MU0_ISA: float = 1.716e-5
#: Reference temperature of the Sutherland law, 273.15 K = 0 degC.
SUTHERLAND_T0: float = 273.15
MU_SUTHERLAND: float = 110.4
T0_ISA: float = 288.15
P0_ISA: float = 101325.0
LAPSE_RATE_ISA: float = 0.0065
GAMMA_AIR: float = 1.4
VON_KARMAN: float = 0.41
PAPER_DENSITY_BASE: float = 800.0  # kg/m^3 for 100 g/m^2 uncreped stock


# --------------------------------------------------------------------------
# Small pure helpers
# --------------------------------------------------------------------------
def clamp(value: float, low: float, high: float) -> float:
    """Clamp ``value`` into ``[low, high]`` (order independent)."""
    val = float(value)
    if not math.isfinite(val):
        raise ValueError(f"cannot clamp non-finite value {value!r}")
    lo = min(float(low), float(high))
    hi = max(float(low), float(high))
    if val < lo:
        return lo
    if val > hi:
        return hi
    return val


def deg2rad(value: float) -> float:
    return float(value) * math.pi / 180.0


def rad2deg(value: float) -> float:
    return float(value) * 180.0 / math.pi


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Division that never raises; returns ``default`` on zero denominator."""
    denom = float(denominator)
    if denom == 0.0 or not math.isfinite(denom):
        return default
    out = float(numerator) / denom
    if not math.isfinite(out):
        return default
    return out


def make_uuid() -> str:
    return str(uuid.uuid4())


def utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with second resolution."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------
# (De)serialisation contract
# --------------------------------------------------------------------------
_HINT_CACHE: Dict[type, Dict[str, Any]] = {}


def _resolved_hints(cls: type) -> Dict[str, Any]:
    """Resolve a dataclass's annotations to real types.

    ``from __future__ import annotations`` turns every ``__annotations__``
    entry into a string, so ``Field.type`` cannot be compared against the
    builtin types directly.  ``typing.get_type_hints`` evaluates them once and
    the result is cached, which is what makes the coercion in
    :func:`_coerce` (and therefore the type safety of the whole API) work.
    """
    cached = _HINT_CACHE.get(cls)
    if cached is None:
        import typing

        try:
            cached = typing.get_type_hints(cls)
        except Exception:  # pragma: no cover - defensive
            cached = {}
        _HINT_CACHE[cls] = cached
    return cached


def _coerce(value: Any, annotation: Any, name: str) -> Any:
    """Best effort conversion of a JSON value into the annotated type."""
    if annotation in (Any, None, "", "Any"):
        return value

    origin = getattr(annotation, "__origin__", None)
    args = tuple(getattr(annotation, "__args__", ()))

    # Optional[X] / Union[X, None] -> unwrap to X
    if origin is Union or str(origin) == "typing.Union" or origin is getattr(__import__("types"), "UnionType", None):
        candidates = [a for a in args if a is not type(None)]  # noqa: E721
        if len(candidates) == 1:
            return _coerce(value, candidates[0], name)
        return value

    if origin is not None:
        if origin is tuple:
            if not args:
                return tuple(value)
            if len(args) == 2 and args[1] is Ellipsis:
                return tuple(_coerce(v, args[0], name) for v in value)
            return tuple(_coerce(v, a, name) for v, a in zip(value, args))
        if origin is list:
            inner = args[0] if args else Any
            if not isinstance(value, (list, tuple)):
                return value
            return [_coerce(v, inner, name) for v in value]
        if origin is dict:
            key_type = args[0] if len(args) == 2 else Any
            value_type = args[1] if len(args) == 2 else Any
            if not isinstance(value, dict):
                return value
            return {
                _coerce(k, key_type, name): _coerce(v, value_type, name)
                for k, v in value.items()
            }
        if origin is set:
            inner = args[0] if args else Any
            if not isinstance(value, (list, tuple, set)):
                return value
            return {_coerce(v, inner, name) for v in value}
        # Unknown generic container: leave the payload untouched.
        return value

    if annotation is float:
        val = float(value)
        if not math.isfinite(val):
            raise ValueError(f"field '{name}' must be finite, got {value!r}")
        return val
    if annotation is int:
        return int(value)
    if annotation is bool:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if annotation is str:
        return str(value)
    if is_dataclass(annotation):
        return from_dict(annotation, value or {})
    return value


def as_dict(obj: Any) -> Any:
    """Recursively convert dataclasses (and containers of them) to plain JSON."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: as_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, dict):
        return {str(k): as_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [as_dict(v) for v in obj]
    return obj


def from_dict(cls: Any, data: Optional[Dict[str, Any]], strict: bool = False) -> Any:
    """Build a dataclass instance from a (possibly partial) JSON mapping.

    Unknown keys are ignored unless ``strict`` is set, which raises
    ``ValueError`` describing the offending key -- this is the mechanism used
    by the test-suite to prove that no module drifts away from the contract.
    """
    payload = dict(data or {})
    known = {f.name: f for f in fields(cls)}
    if strict:
        unknown = sorted(set(payload) - set(known))
        if unknown:
            raise ValueError(
                f"{cls.__name__} received unknown field(s): {', '.join(unknown)}; "
                f"allowed: {', '.join(sorted(known))}"
            )
    kwargs: Dict[str, Any] = {}
    hints = _resolved_hints(cls)
    for name, spec in known.items():
        if name in payload:
            kwargs[name] = _coerce(payload[name], hints.get(name, spec.type), name)
    return cls(**kwargs)


# --------------------------------------------------------------------------
# Airframe configuration
# --------------------------------------------------------------------------
@dataclass
class AircraftConfig:
    """Airframe geometry, material, trim and stability derivatives."""

    name: str = "Paper Glider"
    shape_flag: int = 1  # 0 = dart, 1 = glider, 2 = wide-body glider

    # --- mass & material -------------------------------------------------
    mass_kg: float = 0.0050
    paper_gsm: float = 100.0
    paper_stiffness: float = 0.35  # 0..1 normalised bending stiffness
    sheet_area_m2: float = 0.0500  # unfolded sheet area (0.05 m2 x 100 g/m2 = 5 g == mass_kg)

    # --- planform --------------------------------------------------------
    wing_area_m2: float = 0.0150
    wingspan_m: float = 0.2000
    taper_ratio: float = 0.70
    dihedral_deg: float = 5.0
    aspect_ratio: float = 0.0  # 0 -> derived as b^2 / S

    # --- folds & trim ----------------------------------------------------
    fold_leading_deg: float = 0.0
    fold_trailing_deg: float = 15.0
    fold_tip_deg: float = 0.0
    fold_center_deg: float = 0.0
    elevon_deg: float = 2.0
    elevon_chord_ratio: float = 0.22
    aileron_deg: float = 0.0  # differential elevon / aileron angle in degrees (+ = roll right)
    asymmetry_cl: float = 0.0  # intrinsic aerodynamic lift asymmetry between left and right wings
    rudder_deg: float = 0.0  # rudder / vertical crease angle in degrees (+ = yaw right)


    # --- section / lift curve -------------------------------------------
    # cL0 / cm0 are calibrated together so the default airframe trims at a
    # *positive* angle of attack with positive lift (cm0 > 0 and cm_alpha < 0
    # give alpha_trim = -cm0/cm_alpha > 0).  With cL0 = 0.10 / cm0 = -0.06 the
    # trim sat at -3.8 deg, where the lift coefficient is negative and the
    # glider cannot recover -- it dived into the ground in ~0.7 s.
    cL0: float = 0.12
    cLalpha_per_rad: float = 0.0  # 0 -> Helmbold triangular-wing estimate
    cd0: float = 0.0200
    oswald_e: float = 0.75
    cl_max: float = 0.90
    stall_alpha_deg: float = 14.0
    stall_sharpness: float = 1.40
    # Post-stall lift falls from ~cl_max at the stall angle towards zero over
    # the next ~30 deg, i.e. a slope of order -0.03 /deg.  A much steeper value
    # (e.g. -0.35) drives |C_L| past 7 by 40 deg past stall, which is not
    # physical and lets a tumbling airframe generate multi-weight forces.
    # NOTE: the post-stall lift branch is now anchored on the published
    # Hoerner flat-plate limit (2 sin(a) cos^2(a)) through a monotone
    # transition, so this slope no longer sets the shape of the deep-stall
    # curve (the linear ramp was the source of the lift-free notch between
    # 41 and 45 deg).  The field is kept for API compatibility.
    post_stall_slope: float = -0.03
    re_cd0_exponent: float = 0.15

    # --- moments & stability --------------------------------------------
    cm0: float = 0.040
    cm_alpha: float = -0.90
    cm_q: float = -12.0
    cy_beta: float = -0.25
    cl_beta: float = -0.03
    cn_beta: float = 0.08
    # Rate derivatives are damping terms: paper airplanes have very small inertia
    # and large surface area, so aerodynamic roll/yaw damping must be strong.
    # Cross-coupling terms (cl_pitch_rate, cn_pitch_rate, cm_roll_rate) are zero
    # for symmetric airframes so pitching does not cause unnatural rolling or spinning.
    cl_roll_rate: float = -0.40
    cm_roll_rate: float = 0.0
    cn_roll_rate: float = 0.0
    cl_pitch_rate: float = 0.0
    cm_pitch_rate: float = 0.0
    cn_pitch_rate: float = 0.0
    cl_yaw_rate: float = 0.0
    cm_yaw_rate: float = 0.0
    cn_yaw_rate: float = -0.30
    static_margin: float = 0.12
    cg_offset_x: float = 0.0  # metres, + = aft

    # --- advanced physics: aeroelasticity & low-Re thin plate -------------
    polhamus_kv: float = 0.0  # leading-edge vortex suction gain (0 -> standard thin wing)
    # Potential (two-dimensional) lift slope K_p of the Polhamus suction
    # analogy.  The published relation is
    #   C_L = K_p sin(a) cos^2(a) + K_v sin^2(a) cos(a).
    # 0 keeps the airframe's own three-dimensional Helmbold slope (the default,
    # and the physically correct slope for a low aspect ratio wing); a
    # positive value switches the attached-flow branch onto the published
    # Polhamus form with that explicit K_p.
    polhamus_kp: float = 0.0
    step_trip_ratio: float = 0.0  # fold step height to chord ratio (turbulator effect, 0..0.02)
    cp_shift_gain: float = 0.0  # dynamic center of pressure shift with alpha (0..1.0)
    aeroelastic_flex: float = 0.0  # dynamic dihedral flex gain with dynamic pressure (0..1.0)
    aeroelastic_twist: float = 0.0  # wingtip washout twist gain with dynamic pressure (0..1.0)
    aeroelastic_compliance: float = 0.0  # dynamic camber compliance gain under launch pressure (0..5.0)
    # Passive aeroelastic morphing (SPEC 1.1): the trailing-edge trim deflection
    # washes out with dynamic pressure,
    #   delta_eff(q) = delta_trim / (1 + (q / q_aeroelastic) ** n_morph)
    # with q_aeroelastic = scale_pa * paper_stiffness / aeroelastic_compliance.
    # Both parameters were previously hard-coded literals (160.0 and 3.0) and
    # could not be calibrated; they are configuration now.
    aeroelastic_morph_exponent: float = 3.0  # n_morph (dimensionless)
    aeroelastic_pressure_scale_pa: float = 160.0  # q_aeroelastic scale (Pa)
    paper_grain_angle_deg: float = 0.0  # paper fiber angle: 0 = MD (longitudinal), 90 = CD (transverse)
    paper_anisotropy_ratio: float = 1.8  # E_MD / E_CD ratio (typical paper 1.5..2.2)
    added_mass_enabled: bool = False  # apparent mass tensor for lightweight bodies
    ground_pitch_coupling: float = 0.0  # nose-down pitching moment coupling in ground effect
    ground_cushion_lift: float = 0.0  # ram pressure cushion lift gain in ground effect

    # --- reference quantities -------------------------------------------
    ref_area_m2: float = 0.0  # 0 -> wing_area_m2
    ref_length_m: float = 0.0  # 0 -> mean aerodynamic chord
    ref_span_m: float = 0.0  # 0 -> wingspan_m
    roll_inertia_kgm2: float = 0.0  # 0 -> derived from geometry
    pitch_inertia_kgm2: float = 0.0
    yaw_inertia_kgm2: float = 0.0
    inertia_xy_kgm2: float = 0.0
    inertia_xz_kgm2: float = 0.0
    inertia_yz_kgm2: float = 0.0

    def resolved(self) -> Dict[str, float]:
        """Return the reference quantities actually used by the solvers."""
        return derived_properties(self)

    def clamp_ranges(self) -> "AircraftConfig":
        """Return a copy with physical scalars forced into plausible bounds."""
        from dataclasses import replace as _replace

        r = float(self.roll_inertia_kgm2)
        p = float(self.pitch_inertia_kgm2)
        y = float(self.yaw_inertia_kgm2)
        ixy = float(self.inertia_xy_kgm2)
        ixz = float(self.inertia_xz_kgm2)
        iyz = float(self.inertia_yz_kgm2)

        if r > 0.0 or p > 0.0 or y > 0.0:
            r = max(r, 0.0)
            p = max(p, 0.0)
            y = max(y, 0.0)
            if r > 0.0 and p > 0.0 and y > 0.0:
                if r > p + y:
                    r = p + y
                elif p > r + y:
                    p = r + y
                elif y > r + p:
                    y = r + p

                max_xy = 0.95 * math.sqrt(r * p)
                max_xz = 0.95 * math.sqrt(r * y)
                max_yz = 0.95 * math.sqrt(p * y)
                ixy = clamp(ixy, -max_xy, max_xy)
                ixz = clamp(ixz, -max_xz, max_xz)
                iyz = clamp(iyz, -max_yz, max_yz)

        mean_chord = float(self.wing_area_m2) / max(float(self.wingspan_m), 1.0e-6)

        return _replace(
            self,
            mass_kg=clamp(self.mass_kg, *AIRCRAFT_RANGES["mass_kg"]),
            paper_gsm=clamp(self.paper_gsm, *AIRCRAFT_RANGES["paper_gsm"]),
            paper_stiffness=clamp(self.paper_stiffness, *AIRCRAFT_RANGES["paper_stiffness"]),
            sheet_area_m2=clamp(self.sheet_area_m2, *AIRCRAFT_RANGES["sheet_area_m2"]),
            wing_area_m2=clamp(self.wing_area_m2, *AIRCRAFT_RANGES["wing_area_m2"]),
            wingspan_m=clamp(self.wingspan_m, *AIRCRAFT_RANGES["wingspan_m"]),
            taper_ratio=clamp(self.taper_ratio, *AIRCRAFT_RANGES["taper_ratio"]),
            dihedral_deg=clamp(self.dihedral_deg, *AIRCRAFT_RANGES["dihedral_deg"]),
            aspect_ratio=clamp(self.aspect_ratio, *AIRCRAFT_RANGES["aspect_ratio"]),
            fold_leading_deg=clamp(self.fold_leading_deg, *AIRCRAFT_RANGES["fold_leading_deg"]),
            fold_trailing_deg=clamp(self.fold_trailing_deg, *AIRCRAFT_RANGES["fold_trailing_deg"]),
            fold_tip_deg=clamp(self.fold_tip_deg, *AIRCRAFT_RANGES["fold_tip_deg"]),
            fold_center_deg=clamp(self.fold_center_deg, *AIRCRAFT_RANGES["fold_center_deg"]),
            elevon_deg=clamp(self.elevon_deg, *AIRCRAFT_RANGES["elevon_deg"]),
            elevon_chord_ratio=clamp(self.elevon_chord_ratio, *AIRCRAFT_RANGES["elevon_chord_ratio"]),
            aileron_deg=clamp(self.aileron_deg, *AIRCRAFT_RANGES["aileron_deg"]),
            asymmetry_cl=clamp(self.asymmetry_cl, *AIRCRAFT_RANGES["asymmetry_cl"]),
            rudder_deg=clamp(self.rudder_deg, *AIRCRAFT_RANGES["rudder_deg"]),
            cL0=clamp(self.cL0, *AIRCRAFT_RANGES["cL0"]),
            cd0=clamp(self.cd0, *AIRCRAFT_RANGES["cd0"]),
            oswald_e=clamp(self.oswald_e, *AIRCRAFT_RANGES["oswald_e"]),
            cl_max=clamp(self.cl_max, *AIRCRAFT_RANGES["cl_max"]),
            stall_alpha_deg=clamp(self.stall_alpha_deg, *AIRCRAFT_RANGES["stall_alpha_deg"]),
            stall_sharpness=clamp(self.stall_sharpness, *AIRCRAFT_RANGES["stall_sharpness"]),
            re_cd0_exponent=clamp(self.re_cd0_exponent, *AIRCRAFT_RANGES["re_cd0_exponent"]),
            static_margin=clamp(self.static_margin, *AIRCRAFT_RANGES["static_margin"]),
            cg_offset_x=clamp(self.cg_offset_x, *AIRCRAFT_RANGES["cg_offset_x"]),
            polhamus_kv=clamp(self.polhamus_kv, *AIRCRAFT_RANGES["polhamus_kv"]),
            polhamus_kp=clamp(self.polhamus_kp, *AIRCRAFT_RANGES["polhamus_kp"]),
            step_trip_ratio=clamp(self.step_trip_ratio, *AIRCRAFT_RANGES["step_trip_ratio"]),
            cp_shift_gain=clamp(self.cp_shift_gain, *AIRCRAFT_RANGES["cp_shift_gain"]),
            aeroelastic_flex=clamp(self.aeroelastic_flex, *AIRCRAFT_RANGES["aeroelastic_flex"]),
            aeroelastic_twist=clamp(self.aeroelastic_twist, *AIRCRAFT_RANGES["aeroelastic_twist"]),
            aeroelastic_compliance=clamp(self.aeroelastic_compliance, *AIRCRAFT_RANGES["aeroelastic_compliance"]),
            aeroelastic_morph_exponent=clamp(self.aeroelastic_morph_exponent, *AIRCRAFT_RANGES["aeroelastic_morph_exponent"]),
            aeroelastic_pressure_scale_pa=clamp(self.aeroelastic_pressure_scale_pa, *AIRCRAFT_RANGES["aeroelastic_pressure_scale_pa"]),
            paper_grain_angle_deg=clamp(self.paper_grain_angle_deg, *AIRCRAFT_RANGES["paper_grain_angle_deg"]),
            paper_anisotropy_ratio=clamp(self.paper_anisotropy_ratio, *AIRCRAFT_RANGES["paper_anisotropy_ratio"]),
            added_mass_enabled=bool(self.added_mass_enabled),
            ground_pitch_coupling=clamp(self.ground_pitch_coupling, *AIRCRAFT_RANGES["ground_pitch_coupling"]),
            ground_cushion_lift=clamp(self.ground_cushion_lift, *AIRCRAFT_RANGES["ground_cushion_lift"]),
            cm0=clamp(self.cm0, *AIRCRAFT_RANGES["cm0"]),
            cm_alpha=clamp(self.cm_alpha, *AIRCRAFT_RANGES["cm_alpha"]),
            cm_q=clamp(self.cm_q, *AIRCRAFT_RANGES["cm_q"]),
            cy_beta=clamp(self.cy_beta, *AIRCRAFT_RANGES["cy_beta"]),
            cl_beta=clamp(self.cl_beta, *AIRCRAFT_RANGES["cl_beta"]),
            cn_beta=clamp(self.cn_beta, *AIRCRAFT_RANGES["cn_beta"]),
            cl_roll_rate=clamp(self.cl_roll_rate, *AIRCRAFT_RANGES["cl_roll_rate"]),
            cm_roll_rate=clamp(self.cm_roll_rate, *AIRCRAFT_RANGES["cm_roll_rate"]),
            cn_roll_rate=clamp(self.cn_roll_rate, *AIRCRAFT_RANGES["cn_roll_rate"]),
            cl_pitch_rate=clamp(self.cl_pitch_rate, *AIRCRAFT_RANGES["cl_pitch_rate"]),
            cm_pitch_rate=clamp(self.cm_pitch_rate, *AIRCRAFT_RANGES["cm_pitch_rate"]),
            cn_pitch_rate=clamp(self.cn_pitch_rate, *AIRCRAFT_RANGES["cn_pitch_rate"]),
            cl_yaw_rate=clamp(self.cl_yaw_rate, *AIRCRAFT_RANGES["cl_yaw_rate"]),
            cm_yaw_rate=clamp(self.cm_yaw_rate, *AIRCRAFT_RANGES["cm_yaw_rate"]),
            cn_yaw_rate=clamp(self.cn_yaw_rate, *AIRCRAFT_RANGES["cn_yaw_rate"]),
            shape_flag=int(clamp(float(self.shape_flag), *AIRCRAFT_RANGES["shape_flag"])),
            post_stall_slope=clamp(self.post_stall_slope, *AIRCRAFT_RANGES["post_stall_slope"]),
            cLalpha_per_rad=clamp(self.cLalpha_per_rad, *AIRCRAFT_RANGES["cLalpha_per_rad"]),
            ref_area_m2=(
                0.0
                if float(self.ref_area_m2) <= 0.0
                else clamp(
                    float(self.ref_area_m2),
                    0.2 * float(self.wing_area_m2),
                    3.0 * float(self.wing_area_m2),
                )
            ),
            ref_length_m=(
                0.0
                if float(self.ref_length_m) <= 0.0
                else clamp(float(self.ref_length_m), 0.1 * mean_chord, 5.0 * mean_chord)
            ),
            ref_span_m=(
                0.0
                if float(self.ref_span_m) <= 0.0
                else clamp(
                    float(self.ref_span_m),
                    0.5 * float(self.wingspan_m),
                    2.0 * float(self.wingspan_m),
                )
            ),
            roll_inertia_kgm2=r,
            pitch_inertia_kgm2=p,
            yaw_inertia_kgm2=y,
            inertia_xy_kgm2=ixy,
            inertia_xz_kgm2=ixz,
            inertia_yz_kgm2=iyz,
        )


# --------------------------------------------------------------------------
# Environment configuration
# --------------------------------------------------------------------------
@dataclass
class EnvironmentConfig:
    """Fully runtime-mutable atmosphere, wind and scaling parameters."""

    # --- local atmosphere override --------------------------------------
    temperature_K: float = 288.15
    pressure_Pa: float = 101325.0
    humidity_rel: float = 0.50
    isa_mode: bool = True  # True -> T/p follow ISA from altitude_m
    altitude_m: float = 0.0

    # --- reference sea level for ISA / Sutherland -----------------------
    sea_level_temperature_K: float = 288.15
    sea_level_pressure_Pa: float = 101325.0
    lapse_rate_K_per_m: float = 0.0065

    # --- wind ------------------------------------------------------------
    wind_speed: float = 0.0
    wind_direction_deg: float = 0.0
    gust_intensity: float = 0.20
    thermal_strength: float = 0.50
    turbulence_scale: float = 0.35
    turbulence_seed: int = 12345
    boundary_layer_enabled: bool = True
    surface_roughness_m: float = 0.0008
    reference_height_m: float = 1.0
    ground_level_m: float = 0.0

    # --- scaling ---------------------------------------------------------
    gravity: float = STANDARD_GRAVITY
    ground_friction: float = 0.40
    drag_scale: float = 1.0
    lift_scale: float = 1.0
    ground_effect_enabled: bool = True
    compressibility_enabled: bool = True

    # --- wind tunnel -----------------------------------------------------
    wind_tunnel_enabled: bool = False
    wind_tunnel_speed: float = 12.0
    wind_tunnel_alpha_deg: float = 4.0  # Angle of attack on test bench (degrees)
    wind_tunnel_beta_deg: float = 0.0   # Sideslip angle on test bench (degrees)
    # Tunnel geometry and flow quality.  These were previously hard-coded
    # literals inside wind_domain; they are configuration now so that the
    # tunnel boundary correction can actually be reached from the API.
    wind_tunnel_blockage: float = 1.035  # velocity ratio in the contracted section
    wind_tunnel_turbulence_intensity: float = 0.005  # free-stream TI
    wind_tunnel_radius_m: float = 2.5  # test-section radius
    wind_tunnel_wall_thickness_m: float = 0.15  # wall boundary-layer thickness
    wind_tunnel_boundary_correction: bool = False  # apply the wall boundary layer

    def clamp_ranges(self) -> "EnvironmentConfig":
        """Return a copy with every scalar forced inside its legal range."""
        return EnvironmentConfig(
            temperature_K=clamp(self.temperature_K, *ENVIRONMENT_RANGES["temperature_K"]),
            pressure_Pa=clamp(self.pressure_Pa, *ENVIRONMENT_RANGES["pressure_Pa"]),
            humidity_rel=clamp(self.humidity_rel, *ENVIRONMENT_RANGES["humidity_rel"]),
            isa_mode=bool(self.isa_mode),
            altitude_m=clamp(self.altitude_m, *ENVIRONMENT_RANGES["altitude_m"]),
            sea_level_temperature_K=clamp(
                self.sea_level_temperature_K, *ENVIRONMENT_RANGES["sea_level_temperature_K"]
            ),
            sea_level_pressure_Pa=clamp(
                self.sea_level_pressure_Pa, *ENVIRONMENT_RANGES["sea_level_pressure_Pa"]
            ),
            lapse_rate_K_per_m=clamp(self.lapse_rate_K_per_m, *ENVIRONMENT_RANGES["lapse_rate_K_per_m"]),
            wind_speed=clamp(self.wind_speed, *ENVIRONMENT_RANGES["wind_speed"]),
            wind_direction_deg=clamp(self.wind_direction_deg, *ENVIRONMENT_RANGES["wind_direction_deg"]),
            gust_intensity=clamp(self.gust_intensity, *ENVIRONMENT_RANGES["gust_intensity"]),
            thermal_strength=clamp(self.thermal_strength, *ENVIRONMENT_RANGES["thermal_strength"]),
            turbulence_scale=clamp(self.turbulence_scale, *ENVIRONMENT_RANGES["turbulence_scale"]),
            turbulence_seed=int(self.turbulence_seed),
            boundary_layer_enabled=bool(self.boundary_layer_enabled),
            surface_roughness_m=clamp(self.surface_roughness_m, *ENVIRONMENT_RANGES["surface_roughness_m"]),
            reference_height_m=clamp(self.reference_height_m, *ENVIRONMENT_RANGES["reference_height_m"]),
            ground_level_m=float(self.ground_level_m),
            gravity=clamp(self.gravity, *ENVIRONMENT_RANGES["gravity"]),
            ground_friction=clamp(self.ground_friction, *ENVIRONMENT_RANGES["ground_friction"]),
            drag_scale=clamp(self.drag_scale, *ENVIRONMENT_RANGES["drag_scale"]),
            lift_scale=clamp(self.lift_scale, *ENVIRONMENT_RANGES["lift_scale"]),
            ground_effect_enabled=bool(self.ground_effect_enabled),
            compressibility_enabled=bool(self.compressibility_enabled),
            wind_tunnel_enabled=bool(self.wind_tunnel_enabled),
            wind_tunnel_speed=clamp(self.wind_tunnel_speed, *ENVIRONMENT_RANGES["wind_tunnel_speed"]),
            wind_tunnel_alpha_deg=clamp(self.wind_tunnel_alpha_deg, *ENVIRONMENT_RANGES["wind_tunnel_alpha_deg"]),
            wind_tunnel_blockage=clamp(self.wind_tunnel_blockage, *ENVIRONMENT_RANGES["wind_tunnel_blockage"]),
            wind_tunnel_turbulence_intensity=clamp(
                self.wind_tunnel_turbulence_intensity,
                *ENVIRONMENT_RANGES["wind_tunnel_turbulence_intensity"],
            ),
            wind_tunnel_radius_m=clamp(self.wind_tunnel_radius_m, *ENVIRONMENT_RANGES["wind_tunnel_radius_m"]),
            wind_tunnel_wall_thickness_m=clamp(
                self.wind_tunnel_wall_thickness_m,
                *ENVIRONMENT_RANGES["wind_tunnel_wall_thickness_m"],
            ),
            wind_tunnel_boundary_correction=bool(self.wind_tunnel_boundary_correction),
            wind_tunnel_beta_deg=clamp(self.wind_tunnel_beta_deg, *ENVIRONMENT_RANGES["wind_tunnel_beta_deg"]),
        )


# --------------------------------------------------------------------------
# Launch condition & integrator settings
# --------------------------------------------------------------------------
THROW_SPEED_SOURCES: Tuple[str, ...] = ("direct", "impulse")


@dataclass
class ThrowStyle:
    """A named throwing technique ("投げ方") with its hand-load parameters.

    Every field is a *hand/arm* quantity, never an airframe quantity, so a
    style can be applied to any airframe: the release speed follows from
    ``impulse = force * duration`` divided by the airframe mass.
    """

    key: str
    label: str
    label_ja: str
    description: str
    force_n: float
    duration_s: float
    force_pitch_deg: float
    force_yaw_deg: float
    grip_x: float
    grip_y: float
    wrist_spin_radps: float
    pitch_rate_radps: float
    yaw_rate_radps: float
    release_pitch_deg: float
    release_yaw_deg: float
    release_roll_deg: float
    runup_m: float
    grip_z: float = 0.015
    notes: str = ""

    def launch_overrides(self) -> Dict[str, Any]:
        """The :class:`LaunchConfig` fields this style writes.

        Applying a style always re-states ``throw_speed_source`` so that
        selecting a technique cannot leave the previous "direct" source in
        place (and vice versa).
        """
        return {
            "throw_style": self.key,
            "throw_speed_source": "direct" if self.key == "direct" else "impulse",
            "throw_force_n": self.force_n,
            "throw_duration_s": self.duration_s,
            "throw_force_pitch_deg": self.force_pitch_deg,
            "throw_force_yaw_deg": self.force_yaw_deg,
            "throw_grip_x": self.grip_x,
            "throw_grip_y": self.grip_y,
            "throw_grip_z": self.grip_z,
            "throw_wrist_spin_radps": self.wrist_spin_radps,
            "throw_pitch_rate_radps": self.pitch_rate_radps,
            "throw_yaw_rate_radps": self.yaw_rate_radps,
            "release_pitch_deg": self.release_pitch_deg,
            "release_yaw_deg": self.release_yaw_deg,
            "release_roll_deg": self.release_roll_deg,
            "throw_runup_m": self.runup_m,
        }


#: Throwing techniques offered by the UI and the optimiser.  ``direct`` is the
#: neutral entry: it leaves ``launch_speed``/``launch_angle_deg`` untouched and
#: switches the impulse model off, so the pre-existing behaviour of the
#: simulator is bit-for-bit preserved.  Every other entry activates the
#: impulsive model implemented by :func:`physics.flight_sim.resolve_throw`.
THROW_STYLE_CATALOGUE: Tuple[ThrowStyle, ...] = (
    ThrowStyle(
        key="direct",
        label="Direct (explicit speed/attitude)",
        label_ja="直接指定（速度・姿勢を数値で指定）",
        description=(
            "The impulsive hand model is bypassed: the release speed is "
            "launch_speed and the release attitude is (launch_roll_deg, "
            "launch_angle_deg, launch_yaw_deg)."
        ),
        force_n=0.0,
        duration_s=0.12,
        force_pitch_deg=0.0,
        force_yaw_deg=0.0,
        grip_x=0.0,
        grip_y=0.0,
        wrist_spin_radps=0.0,
        pitch_rate_radps=0.0,
        yaw_rate_radps=0.0,
        release_pitch_deg=0.0,
        release_yaw_deg=0.0,
        release_roll_deg=0.0,
        runup_m=0.0,
        notes="Neutral entry point; identical to the pre-v5 behaviour.",
    ),
    ThrowStyle(
        key="overhand",
        label="Overhand (javelin)",
        label_ja="オーバー投（ジャベリン投）",
        description=(
            "Forward-upward push from behind the CG, grip slightly ahead of "
            "the CG; the classic indoor glide throw."
        ),
        force_n=0.35,
        duration_s=0.12,
        force_pitch_deg=10.0,
        force_yaw_deg=0.0,
        grip_x=0.10,
        grip_y=0.0,
        wrist_spin_radps=0.0,
        pitch_rate_radps=0.0,
        yaw_rate_radps=0.0,
        release_pitch_deg=0.0,
        release_yaw_deg=0.0,
        release_roll_deg=0.0,
        runup_m=0.0,
    ),
    ThrowStyle(
        key="underhand",
        label="Underhand (push off)",
        label_ja="アンダーハンド（下方から押す）",
        description=(
            "Push delivered from below with the nose slightly down; keeps the "
            "airframe flat and produces a shallow, fast glide."
        ),
        force_n=0.30,
        duration_s=0.12,
        force_pitch_deg=-8.0,
        force_yaw_deg=0.0,
        grip_x=0.05,
        grip_y=0.0,
        wrist_spin_radps=0.0,
        pitch_rate_radps=0.0,
        yaw_rate_radps=0.0,
        release_pitch_deg=0.0,
        release_yaw_deg=0.0,
        release_roll_deg=0.0,
        runup_m=0.0,
    ),
    ThrowStyle(
        key="sidearm",
        label="Sidearm (lateral sweep)",
        label_ja="サイドアーム（横から振り抜く）",
        description=(
            "Lateral sweep with the grip outboard of the CG; imparts a yaw "
            "rate that curves the track -- how record gliders are aimed around "
            "an arena."
        ),
        force_n=0.40,
        duration_s=0.10,
        force_pitch_deg=8.0,
        force_yaw_deg=-40.0,
        grip_x=0.0,
        grip_y=0.25,
        wrist_spin_radps=0.0,
        pitch_rate_radps=0.0,
        yaw_rate_radps=2.5,
        release_pitch_deg=0.0,
        release_yaw_deg=0.0,
        release_roll_deg=0.0,
        runup_m=0.0,
    ),
    ThrowStyle(
        key="level_push",
        label="Level push (long, low)",
        label_ja="水平押し（低く長く）",
        description=(
            "Straight, level push with a wide grip; maximises the release "
            "speed for a given hand force, which is what the 98 m distance "
            "record throws need."
        ),
        force_n=0.45,
        duration_s=0.15,
        force_pitch_deg=0.0,
        force_yaw_deg=0.0,
        grip_x=0.20,
        grip_y=0.0,
        wrist_spin_radps=0.0,
        pitch_rate_radps=0.0,
        yaw_rate_radps=0.0,
        release_pitch_deg=0.0,
        release_yaw_deg=0.0,
        release_roll_deg=0.0,
        runup_m=3.0,
        notes="3.0 m run-up, inside the 10 ft (3.04 m) Guinness allowance.",
    ),
    ThrowStyle(
        key="reverse_flick",
        label="Reverse flick (wrist snap)",
        label_ja="リバースフリック（手首を返す）",
        description=(
            "Forward push with a pronating wrist snap; puts a strong roll rate "
            "into the airframe so it settles wings-level after release."
        ),
        force_n=0.30,
        duration_s=0.10,
        force_pitch_deg=6.0,
        force_yaw_deg=0.0,
        grip_x=0.10,
        grip_y=0.0,
        wrist_spin_radps=18.0,
        pitch_rate_radps=0.0,
        yaw_rate_radps=0.0,
        release_pitch_deg=0.0,
        release_yaw_deg=0.0,
        release_roll_deg=0.0,
        runup_m=0.0,
    ),
    ThrowStyle(
        key="boomerang_curve",
        label="Boomerang curve",
        label_ja="ブーメラン投じ（曲線飛行）",
        description=(
            "Deliberately asymmetric: roll plus yaw spin it back towards the "
            "thrower.  Maximum endurance, minimum straight-line distance."
        ),
        force_n=0.35,
        duration_s=0.11,
        force_pitch_deg=12.0,
        force_yaw_deg=18.0,
        grip_x=0.05,
        grip_y=-0.30,
        wrist_spin_radps=12.0,
        pitch_rate_radps=0.0,
        yaw_rate_radps=3.0,
        release_pitch_deg=0.0,
        release_yaw_deg=0.0,
        release_roll_deg=0.0,
        runup_m=0.0,
    ),
    ThrowStyle(
        key="high_release",
        label="Gentle loft (high release)",
        label_ja="ソフトなロフト（高い位置から静かに放つ）",
        description=(
            "A long, low-force push: the airframe leaves the hand slowly but "
            "high, trading speed for the altitude that dominates indoor "
            "duration flights."
        ),
        force_n=0.18,
        duration_s=0.30,
        force_pitch_deg=5.0,
        force_yaw_deg=0.0,
        grip_x=0.10,
        grip_y=0.0,
        wrist_spin_radps=0.0,
        pitch_rate_radps=0.0,
        yaw_rate_radps=0.0,
        release_pitch_deg=0.0,
        release_yaw_deg=0.0,
        release_roll_deg=0.0,
        runup_m=0.0,
    ),
    ThrowStyle(
        key="record_overhand",
        label="Record overhand (indoor duration)",
        label_ja="記録更新オーバ投（室内滞空）",
        description=(
            "The technique behind the indoor duration records: hard, steep, "
            "flat-spun loft with a legal 3.0 m run-up."
        ),
        force_n=0.55,
        duration_s=0.14,
        force_pitch_deg=14.0,
        force_yaw_deg=0.0,
        grip_x=0.12,
        grip_y=0.0,
        wrist_spin_radps=6.0,
        pitch_rate_radps=0.0,
        yaw_rate_radps=0.0,
        release_pitch_deg=2.0,
        release_yaw_deg=0.0,
        release_roll_deg=0.0,
        runup_m=3.0,
        notes="Run-up 3.0 m <= the 10 ft (3.04 m) permitted by the rules.",
    ),
    ThrowStyle(
        key="record_loft",
        label="Record loft (steep, endurance)",
        label_ja="記録更新ロフト（急角度・長滞空）",
        description=(
            "Maximum loft angle with the largest legal run-up: trades the "
            "initial climb rate for the apex height a 30 s flight needs."
        ),
        force_n=0.60,
        duration_s=0.16,
        force_pitch_deg=24.0,
        force_yaw_deg=0.0,
        grip_x=0.12,
        grip_y=0.0,
        wrist_spin_radps=4.0,
        pitch_rate_radps=0.0,
        yaw_rate_radps=0.0,
        release_pitch_deg=3.0,
        release_yaw_deg=0.0,
        release_roll_deg=0.0,
        runup_m=3.0,
    ),
)


def throw_style(key: str) -> ThrowStyle:
    """Look a :class:`ThrowStyle` up by key (raises ``KeyError`` if unknown)."""
    for style in THROW_STYLE_CATALOGUE:
        if style.key == key:
            return style
    raise KeyError(
        f"unknown throw style '{key}'; expected one of "
        f"{', '.join(s.key for s in THROW_STYLE_CATALOGUE)}"
    )


@dataclass
class LaunchConfig:
    """Release condition plus the full hand/arm model of the throw.

    ``launch_speed``/``launch_angle_deg``/``launch_roll_deg``/``launch_yaw_deg``
    describe the *resolved* release condition in the world frame and remain the
    authoritative values whenever the impulsive model is inactive
    (``throw_style == "direct"`` or ``throw_speed_source == "direct"``).

    The throw-model fields describe *how* the hand delivers that state:

    * ``throw_force_n`` x ``throw_duration_s`` is the hand impulse, so the
      release speed of an impulsive throw is ``F*dt/m``;
    * ``throw_force_pitch_deg`` / ``throw_force_yaw_deg`` tilt that impulse away
      from the nose axis ("力の入れ方向");
    * ``throw_grip_x`` / ``throw_grip_y`` place the contact point relative to
      the centre of gravity in mean-chord / semi-span fractions, which is what
      converts the off-axis force into a rotation rate (``r x F``);
    * ``throw_wrist_spin_radps`` / ``throw_pitch_rate_radps`` /
      ``throw_yaw_rate_radps`` are the residual rates the forearm and wrist add
      at release;
    * ``release_pitch_deg`` / ``release_yaw_deg`` / ``release_roll_deg`` are the
      attitude offsets from the flight path at the instant the hand lets go.
    """

    launch_speed: float = 12.0
    launch_angle_deg: float = 12.0
    launch_roll_deg: float = 0.0
    launch_yaw_deg: float = 0.0
    launch_height_m: float = 1.20
    launch_position_m: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    flight_mode: str = "legacy"  # legacy | full_flight | apex_glide; persisted with initial conditions

    # --- throwing technique ("投げ方") -----------------------------------
    throw_style: str = "direct"
    throw_speed_source: str = "direct"  # "direct" -> launch_speed, "impulse" -> F*dt/m

    # --- hand load ("力の入れ方") ----------------------------------------
    throw_force_n: float = 0.35
    throw_duration_s: float = 0.12
    throw_force_pitch_deg: float = 10.0
    throw_force_yaw_deg: float = 0.0
    throw_grip_x: float = 0.10
    throw_grip_y: float = 0.0
    throw_grip_z: float = 0.015  # metres below CG (+z is downward along keel)
    throw_torque_efficiency: float = 0.15
    throw_runup_m: float = 0.0

    # --- wrist / forearm rotation at release ----------------------------
    throw_wrist_spin_radps: float = 0.0
    throw_pitch_rate_radps: float = 0.0
    throw_yaw_rate_radps: float = 0.0

    # --- attitude offsets from the flight path ---------------------------
    release_pitch_deg: float = 0.0
    release_yaw_deg: float = 0.0
    release_roll_deg: float = 0.0

    def clamp_ranges(self) -> "LaunchConfig":
        """Return a copy with every throw/launch scalar forced into range."""
        from dataclasses import replace as _replace

        allowed_throw_styles = {style.key for style in THROW_STYLE_CATALOGUE}
        if str(self.throw_style) not in allowed_throw_styles:
            raise ValueError(
                f"unknown throw_style '{self.throw_style}'; "
                f"expected one of {', '.join(sorted(allowed_throw_styles))}"
            )

        return _replace(
            self,
            launch_speed=clamp(self.launch_speed, *LAUNCH_RANGES["launch_speed"]),
            launch_angle_deg=clamp(self.launch_angle_deg, *LAUNCH_RANGES["launch_angle_deg"]),
            launch_roll_deg=clamp(self.launch_roll_deg, *LAUNCH_RANGES["launch_roll_deg"]),
            launch_yaw_deg=clamp(self.launch_yaw_deg, *LAUNCH_RANGES["launch_yaw_deg"]),
            launch_height_m=clamp(self.launch_height_m, *LAUNCH_RANGES["launch_height_m"]),
            throw_speed_source=(
                self.throw_speed_source
                if self.throw_speed_source in THROW_SPEED_SOURCES
                else "direct"
            ),
            throw_force_n=clamp(self.throw_force_n, *LAUNCH_RANGES["throw_force_n"]),
            throw_duration_s=clamp(self.throw_duration_s, *LAUNCH_RANGES["throw_duration_s"]),
            throw_force_pitch_deg=clamp(self.throw_force_pitch_deg, *LAUNCH_RANGES["throw_force_pitch_deg"]),
            throw_force_yaw_deg=clamp(self.throw_force_yaw_deg, *LAUNCH_RANGES["throw_force_yaw_deg"]),
            throw_grip_x=clamp(self.throw_grip_x, *LAUNCH_RANGES["throw_grip_x"]),
            throw_grip_y=clamp(self.throw_grip_y, *LAUNCH_RANGES["throw_grip_y"]),
            throw_grip_z=clamp(self.throw_grip_z, *LAUNCH_RANGES["throw_grip_z"]),
            throw_torque_efficiency=clamp(self.throw_torque_efficiency, *LAUNCH_RANGES["throw_torque_efficiency"]),
            throw_runup_m=clamp(self.throw_runup_m, *LAUNCH_RANGES["throw_runup_m"]),
            throw_wrist_spin_radps=clamp(self.throw_wrist_spin_radps, *LAUNCH_RANGES["throw_wrist_spin_radps"]),
            throw_pitch_rate_radps=clamp(self.throw_pitch_rate_radps, *LAUNCH_RANGES["throw_pitch_rate_radps"]),
            throw_yaw_rate_radps=clamp(self.throw_yaw_rate_radps, *LAUNCH_RANGES["throw_yaw_rate_radps"]),
            release_pitch_deg=clamp(self.release_pitch_deg, *LAUNCH_RANGES["release_pitch_deg"]),
            release_yaw_deg=clamp(self.release_yaw_deg, *LAUNCH_RANGES["release_yaw_deg"]),
            release_roll_deg=clamp(self.release_roll_deg, *LAUNCH_RANGES["release_roll_deg"]),
        )

    @property
    def impulse_active(self) -> bool:
        """True when the impulsive hand model drives the release speed."""
        return (
            self.throw_speed_source == "impulse"
            and str(self.throw_style) not in ("", "direct")
        )


@dataclass
class SimulationSettings:
    dt_max: float = 2.0e-2
    dt_min: float = 1.0e-5
    max_substeps: int = 64
    duration_s: float = 12.0
    ring_buffer_capacity: int = 20000
    record_interval_s: float = 1.0e-2
    time_scale: float = 1.0
    adaptive: bool = True
    collision_enabled: bool = True
    restitution: float = 0.15
    # Coefficient of restitution for a wingtip strike.  Negative means "use
    # the body value"; a positive value overrides it (a wingtip fold is
    # usually less elastic than the fuselage).
    tip_restitution: float = -1.0
    # Structural stop distance used to convert an impact speed into a load
    # factor: n = 1 + v^2 / (2 g d).  31 mm is the crumple stroke of a
    # folded paper keel; it replaces the previous hard-coded 0.08 s time.
    ground_crush_stroke_m: float = 0.031

    def clamp_ranges(self) -> "SimulationSettings":
        """Return a copy with simulation parameters constrained to safe numerical limits."""
        from dataclasses import replace as _replace

        dt_min = clamp(self.dt_min, *SIMULATION_RANGES["dt_min"])
        dt_max = clamp(self.dt_max, dt_min, SIMULATION_RANGES["dt_max"][1])
        return _replace(
            self,
            dt_min=dt_min,
            dt_max=dt_max,
            max_substeps=int(clamp(self.max_substeps, *SIMULATION_RANGES["max_substeps"])),
            duration_s=clamp(self.duration_s, *SIMULATION_RANGES["duration_s"]),
            ring_buffer_capacity=int(clamp(self.ring_buffer_capacity, *SIMULATION_RANGES["ring_buffer_capacity"])),
            record_interval_s=clamp(self.record_interval_s, *SIMULATION_RANGES["record_interval_s"]),
            time_scale=clamp(self.time_scale, *SIMULATION_RANGES["time_scale"]),
            adaptive=bool(self.adaptive),
            collision_enabled=bool(self.collision_enabled),
            restitution=clamp(self.restitution, *SIMULATION_RANGES["restitution"]),
            tip_restitution=clamp(self.tip_restitution, *SIMULATION_RANGES["tip_restitution"]),
            ground_crush_stroke_m=clamp(self.ground_crush_stroke_m, *SIMULATION_RANGES["ground_crush_stroke_m"]),
        )


@dataclass
class StateVector:
    """Full 6-DoF state in the inertial (world) frame."""

    time_s: float = 0.0
    position_m: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    velocity_mps: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    quaternion: Tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)
    angular_velocity_radps: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    acceleration_mps2: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    angular_accel_radps2: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    landed: bool = False
    crashed: bool = False


# --------------------------------------------------------------------------
# Aerodynamics payloads
# --------------------------------------------------------------------------
@dataclass
class AeroState:
    """Flow condition handed to the aerodynamics solver."""

    alpha_deg: float = 0.0
    beta_deg: float = 0.0
    mach: float = 0.0
    reynolds: float = 1.0e4
    altitude_m: float = 0.0
    ground_height_m: float = 1.0
    angular_rate: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    elevon_deg: Optional[float] = None
    compressible: bool = True


@dataclass
class AeroResult:
    cl: float = 0.0
    cd: float = 0.0
    cm: float = 0.0
    cy: float = 0.0
    cl_roll: float = 0.0
    cm_roll: float = 0.0
    cn_roll: float = 0.0
    lift_n: float = 0.0
    drag_n: float = 0.0
    side_n: float = 0.0
    pitching_moment_nm: float = 0.0
    yawing_moment_nm: float = 0.0
    rolling_moment_nm: float = 0.0
    ld_ratio: float = 0.0
    dynamic_pressure_pa: float = 0.0
    reynolds: float = 0.0
    mach: float = 0.0
    stall_margin: float = 0.0
    stalled: bool = False
    cd_min: float = 0.0
    alpha_ld_max_deg: float = 0.0
    circulation_m2ps: float = 0.0
    trim_alpha_deg: float = 0.0
    trim_elevon_deg: float = 0.0
    ground_effect_ratio: float = 1.0
    lift_left_n: float = 0.0
    lift_right_n: float = 0.0
    cp_x_m: float = 0.0
    aeroelastic_dihedral_deg: float = 0.0
    aeroelastic_twist_deg: float = 0.0
    calibration_active: bool = False


# --------------------------------------------------------------------------
# Mesh payloads
# --------------------------------------------------------------------------
@dataclass
class PanelData:
    index: int = 0
    centroid_m: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    normal: Tuple[float, float, float] = (0.0, 0.0, 1.0)
    area_m2: float = 0.0
    chordwise_frac: float = 0.0
    spanwise_frac: float = 0.0
    arc_frac: float = 0.0
    aero_lambda: float = 0.0
    local_alpha_deg: float = 0.0
    leading_edge: bool = False


@dataclass
class MeshMetrics:
    model_id: str = ""
    name: str = ""
    source_format: str = ""
    source_path: str = ""
    units: str = "m"
    scale_factor: float = 1.0
    vertex_count: int = 0
    face_count: int = 0
    surface_area_m2: float = 0.0
    planform_area_m2: float = 0.0
    wingspan_m: float = 0.0
    mean_chord_m: float = 0.0
    taper_ratio: float = 0.0
    aspect_ratio: float = 0.0
    cg_m: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    cg_mode: str = "volume"
    volume_m3: float = 0.0
    mass_kg: float = 0.0
    inertia_tensor: Tuple[
        float, float, float, float, float, float, float, float, float
    ] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    paper_gsm: float = 100.0
    panels: List[PanelData] = field(default_factory=list)
    panel_count: int = 0
    overrides: Dict[str, float] = field(default_factory=dict)
    created_at: str = ""
    backend: str = "numpy"
    mesh_hash: str = ""


MESH_METRIC_KEYS: Tuple[str, ...] = (
    "surface_area_m2",
    "planform_area_m2",
    "wingspan_m",
    "mean_chord_m",
    "taper_ratio",
    "aspect_ratio",
    "mass_kg",
    "cg_x_m",
    "cg_y_m",
    "cg_z_m",
)


# --------------------------------------------------------------------------
# Optimizer payloads
# --------------------------------------------------------------------------
class OptimizerDomain:
    """Objective domains.  ``MAXIMISE`` -> value larger is better."""

    MAXIMISE = 1
    MINIMISE = -1


OBJECTIVE_DIRECTIONS: Dict[str, int] = {
    "max_distance": OptimizerDomain.MAXIMISE,
    "max_flight_time": OptimizerDomain.MAXIMISE,
    "max_ld_ratio": OptimizerDomain.MAXIMISE,
    "min_max_stress": OptimizerDomain.MINIMISE,
    "max_stall_margin": OptimizerDomain.MAXIMISE,
    "min_launch_speed": OptimizerDomain.MINIMISE,
    "max_stability": OptimizerDomain.MAXIMISE,
}

ALGORITHM_NAMES: Tuple[str, ...] = (
    "grid",
    "nelder-mead",
    "differential-evolution",
    "nsga2",
    "surrogate",
)


@dataclass
class DesignVariable:
    """One optimiser search dimension (flat, JSON friendly)."""

    name: str
    low: float
    high: float
    step: float = 0.0  # 0 -> continuous
    category: str = "shape"
    label: str = ""
    unit: str = ""

    def clip(self, value: float) -> float:
        return clamp(float(value), float(self.low), float(self.high))


#: Canonical design-variable catalogue (prompt.md 3.3).  ``step`` is the grid
#: resolution used by the coarse sweep; ``0`` means continuous.
DESIGN_VARIABLE_CATALOGUE: Tuple[DesignVariable, ...] = (
    DesignVariable("wingspan_m", 0.12, 0.36, 0.02, "shape", "Wing span", "m"),
    DesignVariable("wing_area_m2", 0.008, 0.030, 0.002, "shape", "Wing area", "m2"),
    DesignVariable("taper_ratio", 0.30, 1.00, 0.10, "shape", "Taper ratio", "-"),
    DesignVariable("aspect_ratio", 0.80, 4.00, 0.20, "shape", "Aspect ratio", "-"),
    DesignVariable("dihedral_deg", 0.0, 20.0, 2.5, "shape", "Dihedral", "deg"),
    DesignVariable("fold_leading_deg", 0.0, 90.0, 10.0, "fold", "Leading fold", "deg"),
    DesignVariable("fold_trailing_deg", -45.0, 45.0, 5.0, "fold", "Trailing fold", "deg"),
    DesignVariable("fold_tip_deg", 0.0, 90.0, 10.0, "fold", "Tip fold", "deg"),
    DesignVariable("fold_center_deg", 0.0, 90.0, 10.0, "fold", "Centre fold", "deg"),
    DesignVariable("elevon_deg", 0.0, 35.0, 5.0, "fold", "Elevon angle", "deg"),
    DesignVariable("paper_gsm", 60.0, 220.0, 20.0, "material", "Paper grammage", "g/m2"),
    DesignVariable("paper_stiffness", 0.0, 1.0, 0.1, "material", "Paper stiffness", "-"),
    DesignVariable("mass_kg", 0.003, 0.020, 0.001, "material", "Mass", "kg"),
    DesignVariable("launch_angle_deg", -10.0, 45.0, 5.0, "launch", "Launch angle", "deg"),
    DesignVariable("launch_roll_deg", -45.0, 45.0, 5.0, "launch", "Launch roll", "deg"),
    DesignVariable("launch_speed", 4.0, 30.0, 1.0, "launch", "Launch speed", "m/s"),
    DesignVariable("throw_force_n", 0.05, 1.20, 0.05, "throw", "Hand force", "N"),
    DesignVariable("throw_duration_s", 0.05, 0.30, 0.01, "throw", "Push duration", "s"),
    DesignVariable("throw_force_pitch_deg", -15.0, 45.0, 5.0, "throw", "Force tilt (pitch)", "deg"),
    DesignVariable("throw_grip_x", -0.30, 0.60, 0.05, "throw", "Grip station / MAC", "-"),
    DesignVariable("throw_grip_y", -0.60, 0.60, 0.10, "throw", "Grip offset / semispan", "-"),
    DesignVariable("throw_wrist_spin_radps", -30.0, 30.0, 5.0, "throw", "Wrist roll rate", "rad/s"),
)


@dataclass
class OptimizerConfig:
    objectives: List[str] = field(
        default_factory=lambda: ["max_distance", "min_max_stress"]
    )
    weights: Dict[str, float] = field(
        default_factory=lambda: {"max_distance": 1.0, "min_max_stress": 0.5}
    )
    algorithm: str = "differential-evolution"
    design_variables: List[str] = field(
        default_factory=lambda: [v.name for v in DESIGN_VARIABLE_CATALOGUE]
    )
    variable_bounds: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    max_evaluations: int = 200
    population_size: int = 40
    seed: int = 7
    processes: int = 0  # 0 -> os.cpu_count() based, clamped by parallel_engine
    sim_duration_s: float = 6.0
    dt_max: float = 2.0e-2
    aircraft: AircraftConfig = field(default_factory=AircraftConfig)
    environment: EnvironmentConfig = field(default_factory=EnvironmentConfig)
    launch: LaunchConfig = field(default_factory=LaunchConfig)
    k_nn: int = 4
    rbf_gamma: float = 0.5

    def variable(self, name: str) -> DesignVariable:
        for candidate in DESIGN_VARIABLE_CATALOGUE:
            if candidate.name == name:
                bounds = self.variable_bounds.get(name)
                if bounds:
                    return DesignVariable(
                        name=candidate.name,
                        low=float(bounds[0]),
                        high=float(bounds[1]),
                        step=candidate.step,
                        category=candidate.category,
                        label=candidate.label,
                        unit=candidate.unit,
                    )
                return candidate
        raise KeyError(f"unknown design variable '{name}'")

    def resolved_variables(self) -> List[DesignVariable]:
        return [self.variable(name) for name in self.design_variables]

    def active_objectives(self) -> List[str]:
        picked = [name for name in self.objectives if name in OBJECTIVE_DIRECTIONS]
        return picked


# --------------------------------------------------------------------------
# Job records (ETA management)
# --------------------------------------------------------------------------
@dataclass
class JobRecord:
    job_id: str = field(default_factory=make_uuid)
    name: str = "optimizer"
    algorithm: str = ""
    status: str = "queued"  # queued|running|completed|failed|cancelled
    progress: float = 0.0
    evaluated_count: int = 0
    total_evaluations: int = 0
    started_at: str = ""
    updated_at: str = ""
    finished_at: str = ""
    eta_seconds: float = 0.0
    estimated_completion: str = ""
    backend: str = "serial"  # multiprocessing|threading|serial
    elapsed_seconds: float = 0.0
    evals_per_second: float = 0.0
    message: str = ""
    objectives: List[str] = field(default_factory=list)
    model_id: str = ""
    result_path: str = ""


class MeshStatus:
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


# --------------------------------------------------------------------------
# Logging / export column contracts
# --------------------------------------------------------------------------
@dataclass
class FlightLogSample:
    time_s: float = 0.0
    x_m: float = 0.0
    y_m: float = 0.0
    z_m: float = 0.0
    altitude_m: float = 0.0
    speed_mps: float = 0.0
    vx_mps: float = 0.0
    vy_mps: float = 0.0
    vz_mps: float = 0.0
    alpha_deg: float = 0.0
    beta_deg: float = 0.0
    roll_deg: float = 0.0
    pitch_deg: float = 0.0
    yaw_deg: float = 0.0
    qw: float = 1.0
    qx: float = 0.0
    qy: float = 0.0
    qz: float = 0.0
    ld_ratio: float = 0.0
    g_load: float = 1.0
    max_stress_index: float = 0.0
    max_stress_pa: float = 0.0
    reynolds: float = 0.0
    mach: float = 0.0
    dynamic_pressure_pa: float = 0.0
    stalled: bool = False
    landed: bool = False
    crashed: bool = False
    flight_mode: str = "legacy"
    physics_model_version: str = "unknown"


FLIGHT_LOG_COLUMNS: Tuple[str, ...] = tuple(
    f.name for f in fields(FlightLogSample)
)


@dataclass
class WindSample:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    u: float = 0.0
    v: float = 0.0
    w: float = 0.0
    speed: float = 0.0


# --------------------------------------------------------------------------
# Derived geometry (shared by aero / stress / optimizer / mesh)
# --------------------------------------------------------------------------
def derived_properties(cfg: AircraftConfig) -> Dict[str, float]:
    """Resolve aspect ratio, chords, sheet area and inertia scale.

    Pure function of :class:`AircraftConfig`; every module that needs a
    geometric quantity must call this so the whole simulator stays
    consistent when the user drags a slider.
    """
    span = max(float(cfg.wingspan_m), 1.0e-6)
    area = max(float(cfg.wing_area_m2), 1.0e-9)
    aspect = float(cfg.aspect_ratio)
    if aspect <= 0.0:
        aspect = span * span / area
    aspect = max(aspect, 1.0e-3)
    mean_chord = area / span
    taper = clamp(float(cfg.taper_ratio), 0.05, 1.0)
    # Mean aerodynamic chord of a linearly tapered wing (Raymer, "Aircraft
    # Design: A Conceptual Approach", eq. 12.10; Shevell, eq. 6.1-6.2):
    #   MAC = (2/3) * c_root * (1 + L + L^2) / (1 + L)
    # It is a **length**: from MAC = (2/S) * integral c(y)^2 dy with
    # c(y) = c_root (1 - (1 - L) y / (b/2)) the b cancels against the 1/b in
    # dA = c dy.  The root chord is not a config field, so it is recovered from
    # the trapezoid area S = b * c_root * (1 + L) / 2.  Consistency check: a
    # rectangular wing (L = 1) must give MAC = c_root = S / b.
    root_chord = 2.0 * area / (span * (1.0 + taper))
    mac = (2.0 / 3.0) * root_chord * (1.0 + taper + taper * taper) / (1.0 + taper)
    sheet_area = max(float(cfg.sheet_area_m2), 1.0e-9)
    mass = max(float(cfg.mass_kg), 1.0e-9)
    # Uniform, chord-centred trapezoidal sheet.  The second moments below are
    # taken about the **sheet's own centre of mass**, i.e. the parallel-axis
    # term is included, because the roll/pitch inertia enters Euler's equation
    # about the CG.  For a linearly tapered sheet with root chord ``c_root``,
    # span ``b`` and taper ``lambda`` (c(y) = c_root (1 - (1-L) 2y/b)):
    #
    #   y_cg = b (1 + 2L) / (3 (1 + L))            spanwise, measured from the root
    #   x_cg = c_root (1 + L + L^2) / (3 (1 + L))  chordwise, measured from the LE
    #   E[(y - y_cg)^2] = b^2 [ (1+3L)/(6(1+L)) - (1+2L)^2/(9(1+L)^2) ]
    #
    # The bracket reduces to 1/12 for a rectangular sheet (b^2/12) and to 1/18
    # for a triangular one (b^2/18) - the textbook values.  The previous
    # expression b^2 (1+3L)/(24(1+L)) was exact only for a rectangle (it
    # omitted the parallel-axis shift), understating the triangular-wing
    # inertia by 25 %.
    second_y = span * span * (
        (1.0 + 3.0 * taper) / (6.0 * (1.0 + taper))
        - (1.0 + 2.0 * taper) ** 2 / (9.0 * (1.0 + taper) ** 2)
    )
    second_x = root_chord * root_chord * (
        (1.0 + taper + taper ** 2 + taper ** 3) / (6.0 * (1.0 + taper))
        - (1.0 + taper + taper ** 2) ** 2 / (9.0 * (1.0 + taper) ** 2)
    )
    second_y = max(second_y, 1.0e-12)
    second_x = max(second_x, 1.0e-12)
    thickness = max(float(cfg.paper_gsm), 0.0) * 1.0e-3 / 800.0
    second_z = thickness * thickness / 12.0
    inertia_roll = mass * (second_y + second_z)
    inertia_pitch = mass * (second_x + second_z)
    inertia_yaw = mass * (second_x + second_y)
    return {
        "wingspan_m": span,
        "wing_area_m2": area,
        "aspect_ratio": aspect,
        "mean_chord_m": mean_chord,
        "mac_m": mac,
        "taper_ratio": taper,
        "sheet_area_m2": sheet_area,
        "mass_kg": mass,
        # Mass implied by the sheet material (m = gsm * A / 1000).  It is
        # reported so that a mismatch against ``mass_kg`` (extra nose weight,
        # a thicker stock, ...) is visible instead of silent.
        "implied_mass_kg": max(float(cfg.paper_gsm), 0.0) * sheet_area / 1000.0,
        "ref_area_m2": float(cfg.ref_area_m2) if cfg.ref_area_m2 > 0.0 else area,
        # The aerodynamic reference length is the mean aerodynamic chord, the
        # length the moment / rate derivatives are referred to.  For the
        # trapezoids a paper airframe is folded into it agrees with the geometric
        # mean chord S / b to better than 1 %.
        "ref_length_m": float(cfg.ref_length_m) if cfg.ref_length_m > 0.0 else mac,
        "ref_span_m": float(cfg.ref_span_m) if cfg.ref_span_m > 0.0 else span,
        "roll_inertia_kgm2": float(cfg.roll_inertia_kgm2)
        if cfg.roll_inertia_kgm2 > 0.0
        else inertia_roll,
        "pitch_inertia_kgm2": float(cfg.pitch_inertia_kgm2)
        if cfg.pitch_inertia_kgm2 > 0.0
        else inertia_pitch,
        "yaw_inertia_kgm2": float(cfg.yaw_inertia_kgm2)
        if cfg.yaw_inertia_kgm2 > 0.0
        else (
            max(
                inertia_yaw,
                (float(cfg.roll_inertia_kgm2) if cfg.roll_inertia_kgm2 > 0.0 else inertia_roll)
                + (float(cfg.pitch_inertia_kgm2) if cfg.pitch_inertia_kgm2 > 0.0 else inertia_pitch)
                - 2.0 * mass * second_z,
            )
            if (cfg.roll_inertia_kgm2 > 0.0 or cfg.pitch_inertia_kgm2 > 0.0)
            else inertia_yaw
        ),
        "inertia_xy_kgm2": float(cfg.inertia_xy_kgm2),
        "inertia_xz_kgm2": float(cfg.inertia_xz_kgm2),
        "inertia_yz_kgm2": float(cfg.inertia_yz_kgm2),
    }


# --------------------------------------------------------------------------
# JSON schema fragments served to the UI (single source of truth for the
# sliders, so the front end never hard-codes a range)
# --------------------------------------------------------------------------
def _field_entry(spec, default) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"default": default, "type": type(default).__name__}
    if isinstance(default, bool):
        entry["type"] = "bool"
    return entry


#: Canonical clamp bounds shared by :meth:`AircraftConfig.clamp_ranges` and the
#: ``ranges`` served by ``GET /api/v1/meta/schemas``.  Keeping them in one place
#: guarantees the server contract and the UI sliders can never diverge.
AIRCRAFT_RANGES: Dict[str, Tuple[float, float]] = {
    "mass_kg": (1.0e-4, 1.0),
    "paper_gsm": (10.0, 600.0),
    "paper_stiffness": (0.0, 1.0),
    "sheet_area_m2": (1.0e-4, 2.0),
    "wing_area_m2": (1.0e-4, 1.0),
    "wingspan_m": (0.01, 5.0),
    "taper_ratio": (0.01, 2.0),
    "dihedral_deg": (-45.0, 45.0),
    "aspect_ratio": (0.0, 30.0),
    "fold_leading_deg": (-90.0, 90.0),
    "fold_trailing_deg": (-90.0, 90.0),
    "fold_tip_deg": (-90.0, 90.0),
    "fold_center_deg": (-90.0, 90.0),
    "elevon_deg": (-45.0, 45.0),
    "elevon_chord_ratio": (0.01, 0.9),
    "aileron_deg": (-45.0, 45.0),
    "asymmetry_cl": (-0.5, 0.5),
    "rudder_deg": (-45.0, 45.0),
    "cL0": (-1.0, 2.0),
    "cd0": (0.001, 1.0),
    "oswald_e": (0.1, 1.0),
    "cl_max": (0.1, 3.0),
    "stall_alpha_deg": (2.0, 45.0),
    "stall_sharpness": (0.1, 10.0),
    "re_cd0_exponent": (0.0, 1.0),
    "static_margin": (-1.0, 1.0),
    "cg_offset_x": (-1.0, 1.0),
    "polhamus_kv": (0.0, 5.0),
    "polhamus_kp": (0.0, 12.0),
    "step_trip_ratio": (0.0, 0.05),
    "cp_shift_gain": (0.0, 2.0),
    "aeroelastic_flex": (0.0, 5.0),
    "aeroelastic_twist": (0.0, 5.0),
    "aeroelastic_compliance": (0.0, 5.0),
    "aeroelastic_morph_exponent": (0.5, 12.0),
    "aeroelastic_pressure_scale_pa": (1.0, 1.0e4),
    "paper_grain_angle_deg": (-90.0, 90.0),
    "paper_anisotropy_ratio": (1.0, 5.0),
    "ground_pitch_coupling": (0.0, 5.0),
    "ground_cushion_lift": (0.0, 5.0),
    "cm0": (-1.0, 1.0),
    "cm_alpha": (-10.0, 5.0),
    "cm_q": (-50.0, 5.0),
    "cy_beta": (-5.0, 5.0),
    "cl_beta": (-5.0, 5.0),
    "cn_beta": (-5.0, 5.0),
    "cl_roll_rate": (-10.0, 0.0),
    "cm_roll_rate": (-5.0, 5.0),
    "cn_roll_rate": (-5.0, 5.0),
    "cl_pitch_rate": (-5.0, 5.0),
    "cm_pitch_rate": (-5.0, 5.0),
    "cn_pitch_rate": (-5.0, 5.0),
    "cl_yaw_rate": (-5.0, 5.0),
    "cm_yaw_rate": (-5.0, 5.0),
    "cn_yaw_rate": (-10.0, 0.0),
    "shape_flag": (0.0, 2.0),
    "post_stall_slope": (-0.5, 0.0),
    "cLalpha_per_rad": (0.0, 12.0),
}

ENVIRONMENT_RANGES: Dict[str, Tuple[float, float]] = {
    "temperature_K": (180.0, 400.0),
    "pressure_Pa": (1.0e3, 3.0e5),
    "humidity_rel": (0.0, 1.0),
    "altitude_m": (-500.0, 20000.0),
    "sea_level_temperature_K": (180.0, 340.0),
    "sea_level_pressure_Pa": (1.0e3, 3.0e5),
    "lapse_rate_K_per_m": (-0.02, 0.02),
    "wind_speed": (0.0, 120.0),
    "wind_direction_deg": (0.0, 360.0),
    "gust_intensity": (0.0, 1.0),
    "thermal_strength": (0.0, 25.0),
    "turbulence_scale": (0.0, 5.0),
    "surface_roughness_m": (1.0e-6, 1.0),
    "reference_height_m": (0.01, 100.0),
    "gravity": (0.1, 50.0),
    "ground_friction": (0.0, 2.0),
    "drag_scale": (0.0, 5.0),
    "lift_scale": (0.0, 5.0),
    "wind_tunnel_speed": (0.0, 200.0),
    "wind_tunnel_alpha_deg": (-90.0, 90.0),
    "wind_tunnel_blockage": (0.5, 2.0),
    "wind_tunnel_turbulence_intensity": (0.0, 0.5),
    "wind_tunnel_radius_m": (0.1, 50.0),
    "wind_tunnel_wall_thickness_m": (1.0e-3, 5.0),
    "wind_tunnel_beta_deg": (-90.0, 90.0),
}

SIMULATION_RANGES: Dict[str, Tuple[float, float]] = {
    "dt_max": (1.0e-7, 1.0),
    "dt_min": (1.0e-7, 0.1),
    "max_substeps": (1.0, 1024.0),
    "duration_s": (0.1, 600.0),
    "ring_buffer_capacity": (10.0, 100000.0),
    "record_interval_s": (1.0e-4, 1.0),
    "time_scale": (0.01, 100.0),
    "restitution": (0.0, 1.0),
    "tip_restitution": (-1.0, 1.0),
    "ground_crush_stroke_m": (1.0e-4, 1.0),
}

LAUNCH_RANGES: Dict[str, Tuple[float, float]] = {
    "launch_speed": (0.0, 150.0),
    "launch_angle_deg": (-89.0, 89.0),
    "launch_roll_deg": (-180.0, 180.0),
    "launch_yaw_deg": (-360.0, 360.0),
    "launch_height_m": (0.0, 200.0),
    "throw_force_n": (0.0, 5.0),
    "throw_duration_s": (0.005, 1.0),
    "throw_force_pitch_deg": (-90.0, 90.0),
    "throw_force_yaw_deg": (-90.0, 90.0),
    "throw_grip_x": (-1.0, 1.0),
    "throw_grip_y": (-1.0, 1.0),
    "throw_grip_z": (-0.5, 0.5),
    "throw_torque_efficiency": (0.0, 1.0),
    "throw_runup_m": (0.0, 3.04),
    "throw_wrist_spin_radps": (-60.0, 60.0),
    "throw_pitch_rate_radps": (-12.0, 12.0),
    "throw_yaw_rate_radps": (-20.0, 20.0),
    "release_pitch_deg": (-90.0, 90.0),
    "release_yaw_deg": (-90.0, 90.0),
    "release_roll_deg": (-180.0, 180.0),
}


AIRCRAFT_SCHEMA: Dict[str, Any] = {
    "group": "aircraft",
    "fields": {
        f.name: _field_entry(f, AircraftConfig.__dataclass_fields__[f.name].default)
        for f in fields(AircraftConfig)
    },
    "ranges": {name: list(bounds) for name, bounds in AIRCRAFT_RANGES.items()},
    "design_variables": [
        as_dict(v) for v in DESIGN_VARIABLE_CATALOGUE
    ],
}

ENVIRONMENT_SCHEMA: Dict[str, Any] = {
    "group": "environment",
    "fields": {
        f.name: _field_entry(f, EnvironmentConfig.__dataclass_fields__[f.name].default)
        for f in fields(EnvironmentConfig)
    },
    "ranges": {name: list(bounds) for name, bounds in ENVIRONMENT_RANGES.items()},
}

LAUNCH_SCHEMA: Dict[str, Any] = {
    "group": "launch",
    "fields": {
        f.name: _field_entry(f, LaunchConfig.__dataclass_fields__[f.name].default)
        for f in fields(LaunchConfig)
    },
    "ranges": {name: list(bounds) for name, bounds in LAUNCH_RANGES.items()},
    "throw_styles": [
        {
            "key": style.key,
            "label": style.label,
            "label_ja": style.label_ja,
            "description": style.description,
            "notes": style.notes,
            "overrides": style.launch_overrides(),
        }
        for style in THROW_STYLE_CATALOGUE
    ],
    "speed_sources": list(THROW_SPEED_SOURCES),
}

SIMULATION_SCHEMA: Dict[str, Any] = {
    "group": "simulation",
    "fields": {
        f.name: _field_entry(f, SimulationSettings.__dataclass_fields__[f.name].default)
        for f in fields(SimulationSettings)
    },
    "ranges": {name: list(bounds) for name, bounds in SIMULATION_RANGES.items()},
}


def default_config_bundle() -> Dict[str, Any]:
    """Default payload used by ``POST /api/v1/data/import`` and the UI boot."""
    return {
        "aircraft": as_dict(AircraftConfig()),
        "environment": as_dict(EnvironmentConfig()),
        "launch": as_dict(LaunchConfig()),
        "simulation": as_dict(SimulationSettings()),
    }
