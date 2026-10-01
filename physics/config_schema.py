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
    "G0", "R_AIR", "R_VAPOR", "MU0_ISA", "T0_ISA", "P0_ISA", "LAPSE_RATE_ISA",
    "GAMMA_AIR", "VON_KARMAN", "MU_SUTHERLAND", "STANDARD_GRAVITY",
    "clamp", "deg2rad", "rad2deg", "safe_div", "as_dict", "from_dict",
    "make_uuid", "utc_now_iso", "AircraftConfig", "EnvironmentConfig",
    "LaunchConfig", "StateVector", "SimulationSettings", "AeroState",
    "AeroResult", "PanelData", "MeshMetrics", "OptimizerConfig",
    "DesignVariable",     "JobRecord", "FlightLogSample", "WindSample",
    "derived_properties", "default_config_bundle",
    "FLIGHT_LOG_COLUMNS", "MESH_METRIC_KEYS", "OBJECTIVE_DIRECTIONS",
    "AIRCRAFT_SCHEMA", "ENVIRONMENT_SCHEMA", "OBJECTIVE_DIRECTIONS",
    "ALGORITHM_NAMES", "DESIGN_VARIABLE_CATALOGUE", "OptimizerDomain",
    "MeshStatus",
]

# --------------------------------------------------------------------------
# Physical constants
# --------------------------------------------------------------------------
STANDARD_GRAVITY: float = 9.80665
G0: float = STANDARD_GRAVITY
R_AIR: float = 287.05
R_VAPOR: float = 461.5
MU0_ISA: float = 1.716e-5
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
    if value < low:
        return low
    if value > high:
        return high
    return value


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
        return float(value)
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
    sheet_area_m2: float = 0.0200  # unfolded sheet area

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
    post_stall_slope: float = -0.35
    re_cd0_exponent: float = 0.15

    # --- moments & stability --------------------------------------------
    cm0: float = 0.040
    cm_alpha: float = -0.90
    cm_q: float = -12.0
    cy_beta: float = -0.25
    cl_beta: float = -0.03
    cn_beta: float = 0.08
    # Rate derivatives are *damping* terms: C_lp and C_nr must be negative or
    # the roll/yaw modes become anti-damped and the airframe tumbles.
    cl_roll_rate: float = -0.10
    cm_roll_rate: float = -0.02
    cn_roll_rate: float = -0.08
    cl_pitch_rate: float = -0.06
    cm_pitch_rate: float = 0.05
    cn_pitch_rate: float = -0.12
    cl_yaw_rate: float = -0.08
    cm_yaw_rate: float = 0.0
    cn_yaw_rate: float = -0.15
    static_margin: float = 0.12
    cg_offset_x: float = 0.0  # metres, + = aft

    # --- reference quantities -------------------------------------------
    ref_area_m2: float = 0.0  # 0 -> wing_area_m2
    ref_length_m: float = 0.0  # 0 -> mean chord
    ref_span_m: float = 0.0  # 0 -> wingspan_m
    roll_inertia_kgm2: float = 0.0  # 0 -> derived from geometry
    pitch_inertia_kgm2: float = 0.0
    yaw_inertia_kgm2: float = 0.0

    def resolved(self) -> Dict[str, float]:
        """Return the reference quantities actually used by the solvers."""
        return derived_properties(self)


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

    def clamp_ranges(self) -> "EnvironmentConfig":
        """Return a copy with every scalar forced inside its legal range."""
        return EnvironmentConfig(
            temperature_K=clamp(self.temperature_K, 180.0, 400.0),
            pressure_Pa=clamp(self.pressure_Pa, 1.0e3, 3.0e5),
            humidity_rel=clamp(self.humidity_rel, 0.0, 1.0),
            isa_mode=bool(self.isa_mode),
            altitude_m=clamp(self.altitude_m, -500.0, 20000.0),
            sea_level_temperature_K=clamp(self.sea_level_temperature_K, 180.0, 340.0),
            sea_level_pressure_Pa=clamp(self.sea_level_pressure_Pa, 1.0e3, 3.0e5),
            lapse_rate_K_per_m=clamp(self.lapse_rate_K_per_m, -0.02, 0.02),
            wind_speed=clamp(self.wind_speed, 0.0, 120.0),
            wind_direction_deg=clamp(self.wind_direction_deg, 0.0, 360.0),
            gust_intensity=clamp(self.gust_intensity, 0.0, 1.0),
            thermal_strength=clamp(self.thermal_strength, 0.0, 25.0),
            turbulence_scale=clamp(self.turbulence_scale, 0.0, 5.0),
            turbulence_seed=int(self.turbulence_seed),
            boundary_layer_enabled=bool(self.boundary_layer_enabled),
            surface_roughness_m=clamp(self.surface_roughness_m, 1.0e-6, 1.0),
            reference_height_m=clamp(self.reference_height_m, 0.01, 100.0),
            ground_level_m=float(self.ground_level_m),
            gravity=clamp(self.gravity, 0.1, 50.0),
            ground_friction=clamp(self.ground_friction, 0.0, 2.0),
            drag_scale=clamp(self.drag_scale, 0.0, 5.0),
            lift_scale=clamp(self.lift_scale, 0.0, 5.0),
            ground_effect_enabled=bool(self.ground_effect_enabled),
            compressibility_enabled=bool(self.compressibility_enabled),
            wind_tunnel_enabled=bool(self.wind_tunnel_enabled),
            wind_tunnel_speed=clamp(self.wind_tunnel_speed, 0.0, 200.0),
        )


# --------------------------------------------------------------------------
# Launch condition & integrator settings
# --------------------------------------------------------------------------
@dataclass
class LaunchConfig:
    launch_speed: float = 12.0
    launch_angle_deg: float = 12.0
    launch_roll_deg: float = 0.0
    launch_yaw_deg: float = 0.0
    launch_height_m: float = 1.20
    launch_position_m: Tuple[float, float, float] = (0.0, 0.0, 0.0)


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
    DesignVariable("fold_trailing_deg", 0.0, 90.0, 10.0, "fold", "Trailing fold", "deg"),
    DesignVariable("fold_tip_deg", 0.0, 90.0, 10.0, "fold", "Tip fold", "deg"),
    DesignVariable("fold_center_deg", 0.0, 90.0, 10.0, "fold", "Centre fold", "deg"),
    DesignVariable("elevon_deg", 0.0, 35.0, 5.0, "fold", "Elevon angle", "deg"),
    DesignVariable("paper_gsm", 60.0, 220.0, 20.0, "material", "Paper grammage", "g/m2"),
    DesignVariable("paper_stiffness", 0.0, 1.0, 0.1, "material", "Paper stiffness", "-"),
    DesignVariable("mass_kg", 0.003, 0.020, 0.001, "material", "Mass", "kg"),
    DesignVariable("launch_angle_deg", -10.0, 45.0, 5.0, "launch", "Launch angle", "deg"),
    DesignVariable("launch_roll_deg", -45.0, 45.0, 5.0, "launch", "Launch roll", "deg"),
    DesignVariable("launch_speed", 4.0, 30.0, 1.0, "launch", "Launch speed", "m/s"),
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
    # Mean aerodynamic chord of a linearly tapered wing:
    #   MAC = (2/3) * b * c_root * (1 + L + L^2) / (1 + L)
    # The root chord is not a config field, so it is recovered from the
    # trapezoid area S = b * c_root * (1 + L) / 2.
    root_chord = 2.0 * area / (span * (1.0 + taper))
    mac = (2.0 * span / 3.0) * root_chord * (1.0 + taper + taper * taper) / (1.0 + taper)
    sheet_area = max(float(cfg.sheet_area_m2), 1.0e-9)
    mass = max(float(cfg.mass_kg), 1.0e-9)
    inertia_roll = mass * (span * span) / 12.0
    inertia_pitch = mass * (mac * mac) / 12.0 + mass * 0.05 * area
    inertia_yaw = inertia_pitch
    return {
        "wingspan_m": span,
        "wing_area_m2": area,
        "aspect_ratio": aspect,
        "mean_chord_m": mean_chord,
        "mac_m": mac,
        "taper_ratio": taper,
        "sheet_area_m2": sheet_area,
        "mass_kg": mass,
        "ref_area_m2": float(cfg.ref_area_m2) if cfg.ref_area_m2 > 0.0 else area,
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
        else inertia_yaw,
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


AIRCRAFT_SCHEMA: Dict[str, Any] = {
    "group": "aircraft",
    "fields": {
        f.name: _field_entry(f, AircraftConfig.__dataclass_fields__[f.name].default)
        for f in fields(AircraftConfig)
    },
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
    "ranges": {
        "temperature_K": [180.0, 400.0],
        "pressure_Pa": [1000.0, 300000.0],
        "humidity_rel": [0.0, 1.0],
        "wind_speed": [0.0, 120.0],
        "wind_direction_deg": [0.0, 360.0],
        "gust_intensity": [0.0, 1.0],
        "thermal_strength": [0.0, 25.0],
        "gravity": [0.1, 50.0],
        "ground_friction": [0.0, 2.0],
        "drag_scale": [0.0, 5.0],
        "lift_scale": [0.0, 5.0],
    },
}


def default_config_bundle() -> Dict[str, Any]:
    """Default payload used by ``POST /api/v1/data/import`` and the UI boot."""
    return {
        "aircraft": as_dict(AircraftConfig()),
        "environment": as_dict(EnvironmentConfig()),
        "launch": as_dict(LaunchConfig()),
        "simulation": as_dict(SimulationSettings()),
    }
