"""Core interfaces, data contracts, and protocols for the v3 architecture.

Zero-placeholder, fully typed, headless CUI-only data definitions.
Strictly decoupled from legacy GA and GUI frameworks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple, runtime_checkable

# ============================================================================
# 1. FIXED HORIZONTAL LAUNCH CONSTANTS (Immutable Benchmark Specification)
# ============================================================================
# Launch conditions are locked constants to prevent zoom-climb/pitch hacks.
LAUNCH_PITCH_DEG: float = 0.0
LAUNCH_ROLL_DEG: float = 0.0
LAUNCH_YAW_DEG: float = 0.0
LAUNCH_PITCH_RATE_DEG_S: float = 0.0
LAUNCH_ROLL_RATE_DEG_S: float = 0.0
LAUNCH_YAW_RATE_DEG_S: float = 0.0
LAUNCH_HEIGHT_M: float = 1.5
WIND_SPEED_MPS: float = 0.0

# 2-point fixed horizontal evaluation speeds
SPEED_LOW_MPS: float = 6.0
SPEED_MID_MPS: float = 10.0
BENCHMARK_SPEEDS: Tuple[float, float] = (SPEED_LOW_MPS, SPEED_MID_MPS)

# Minimum & Maximum step requirements for parametric origami (physical fold steps)
MIN_FOLD_STEPS: int = 6
MAX_FOLD_STEPS: int = 16


# ============================================================================
# 2. EVALUATION & BENCHMARK METRICS
# ============================================================================
@dataclass(frozen=True)
class EvaluationMetric:
    """Metrics extracted from a single deterministic flight simulation run."""

    speed_mps: float
    flight_time_s: float
    max_ld_ratio: float
    steady_glide_fraction: float
    stability: float
    distance_m: float
    max_altitude_m: float
    landed: bool
    crashed: bool
    diverged: bool
    status: str

    @property
    def deterministic_score(self) -> float:
        """Deterministic hang-time & glide efficiency score.

        Score = T_air * sqrt(max_ld_ratio) * steady_glide_fraction * stability
        Guarded against zoom-climb stalls and ground-bounce exploits.
        """
        if self.crashed or self.diverged or self.status in ("failed", "diverged", "error"):
            return 0.0
        if self.flight_time_s <= 0.0 or self.max_ld_ratio <= 0.0:
            return 0.0

        ld_clamped = max(0.0, min(self.max_ld_ratio, 40.0))
        # Distance-grounded effective L/D to prevent transient numerical spikes
        eff_ld = max(0.1, min(ld_clamped, max(0.5, self.distance_m / 1.5), 25.0))
        glide_clamped = max(0.0, min(self.steady_glide_fraction, 1.0))
        stability_clamped = max(0.0, min(self.stability, 1.0))
        # Forward speed check: penalize near-vertical parachuting (mean speed < 1.0 m/s)
        mean_speed = self.distance_m / max(0.1, self.flight_time_s)
        speed_factor = max(0.1, min(1.0, mean_speed / 2.0))

        return float(self.flight_time_s * math.sqrt(eff_ld) * glide_clamped * stability_clamped * speed_factor)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "speed_mps": self.speed_mps,
            "flight_time_s": self.flight_time_s,
            "max_ld_ratio": self.max_ld_ratio,
            "steady_glide_fraction": self.steady_glide_fraction,
            "stability": self.stability,
            "distance_m": self.distance_m,
            "max_altitude_m": self.max_altitude_m,
            "landed": self.landed,
            "crashed": self.crashed,
            "diverged": self.diverged,
            "status": self.status,
            "deterministic_score": self.deterministic_score,
        }


@dataclass(frozen=True)
class FlightBenchmarkResult:
    """Aggregate benchmark result across fixed low and mid horizontal speeds."""

    low_speed: EvaluationMetric
    mid_speed: EvaluationMetric
    composite_score: float
    valid: bool
    rejection_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "low_speed": self.low_speed.to_dict(),
            "mid_speed": self.mid_speed.to_dict(),
            "composite_score": self.composite_score,
            "valid": self.valid,
            "rejection_reason": self.rejection_reason,
        }


# ============================================================================
# 3. BEHAVIOR DESCRIPTOR & PHYSICAL PROPERTIES (MAP-Elites 3D Grid)
# ============================================================================
@dataclass(frozen=True)
class BehaviorDescriptor:
    """3D Behavior Descriptor for MAP-Elites niche mapping.

    Dimensions:
      1. Wing loading (N/m^2 or kg/m^2)
      2. Static stability margin or pitch restoring derivative metric
      3. Aspect ratio (b^2 / S)
    """

    wing_loading_kg_m2: float
    static_stability: float
    aspect_ratio: float

    def as_tuple(self) -> Tuple[float, float, float]:
        return (self.wing_loading_kg_m2, self.static_stability, self.aspect_ratio)

    def to_dict(self) -> Dict[str, float]:
        return {
            "wing_loading_kg_m2": self.wing_loading_kg_m2,
            "static_stability": self.static_stability,
            "aspect_ratio": self.aspect_ratio,
        }


@dataclass(frozen=True)
class AirframeGeometry:
    """Physical & aerodynamic properties extracted from folded geometry."""

    mass_kg: float
    wing_area_m2: float
    wingspan_m: float
    chord_m: float
    aspect_ratio: float
    cg_m: Tuple[float, float, float]
    keel_height_m: float
    keel_length_m: float
    keel_layers: int
    step_count: int
    planform_area_m2: float = 0.0
    planform_aspect_ratio: float = 0.0
    penetrations: int = 0

    def compute_behavior_descriptor(self, stability: Optional[float] = None) -> BehaviorDescriptor:
        loading = self.mass_kg / max(1.0e-5, self.wing_area_m2)
        if stability is None:
            chord = max(0.01, self.chord_m)
            # Standard quarter-chord aerodynamic center approximation
            x_ac = 0.25 * chord
            x_cg = self.cg_m[0]
            # Static margin: positive = stable (CG ahead of AC)
            sm = (x_ac - x_cg) / chord
            stability = max(0.0, min(1.0, 0.5 + sm * 2.0))
        ar = self.planform_aspect_ratio if self.planform_aspect_ratio > 0.0 else self.aspect_ratio
        return BehaviorDescriptor(
            wing_loading_kg_m2=loading,
            static_stability=float(stability),
            aspect_ratio=ar,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mass_kg": self.mass_kg,
            "wing_area_m2": self.wing_area_m2,
            "wingspan_m": self.wingspan_m,
            "chord_m": self.chord_m,
            "aspect_ratio": self.aspect_ratio,
            "planform_area_m2": self.planform_area_m2,
            "planform_aspect_ratio": self.planform_aspect_ratio,
            "penetrations": self.penetrations,
            "cg_m": list(self.cg_m),
            "keel_height_m": self.keel_height_m,
            "keel_length_m": self.keel_length_m,
            "keel_layers": self.keel_layers,
            "step_count": self.step_count,
        }


# ============================================================================
# 4. PARAMETRIC 20-FOLD ORIGAMI DATA STRUCTURES
# ============================================================================
@dataclass(frozen=True)
class ParametricNoseSpec:
    """Phase 1: Nose multi-layer fold sequence parameters.

    Folds nose layers 3 to 6 times to shift CG forward and ensure tip rigidity.
    """

    fold_count: int = 4  # 3..6 folds
    taper_ratio: float = 0.65  # 0.4..0.85
    nose_length_ratio: float = 0.28  # fraction of sheet length
    fold_angles_deg: Tuple[float, ...] = (180.0, -180.0, 180.0, -180.0)


@dataclass(frozen=True)
class ParametricKeelSpec:
    """Phase 2: Keel depth & lock fold parameters.

    Guarantees grip_thresholds (height >= 8mm, >= 2 layers) structurally.
    """

    keel_depth_ratio: float = 0.08  # relative to length (~16-24mm)
    has_lock_fold: bool = True
    lock_fold_angle_deg: float = 180.0
    keel_layers: int = 4
    crease_offset_u: float = 0.5


@dataclass(frozen=True)
class ParametricWingSpec:
    """Phase 3: Wing, elevon, winglet & dihedral parameters."""

    wingspan_ratio: float = 0.75
    dihedral_deg: float = 3.5  # positive = V-shape stability
    elevon_left_deg: float = 2.0  # trailing edge upward trim
    elevon_right_deg: float = 2.0
    winglet_angle_deg: float = 45.0
    has_winglet: bool = True


@dataclass(frozen=True)
class ParametricOrigamiConfig:
    """High-level parametric blueprint covering Phase 1, Phase 2, and Phase 3."""

    nose: ParametricNoseSpec = field(default_factory=ParametricNoseSpec)
    keel: ParametricKeelSpec = field(default_factory=ParametricKeelSpec)
    wing: ParametricWingSpec = field(default_factory=ParametricWingSpec)
    paper_gsm: float = 100.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nose": {
                "fold_count": self.nose.fold_count,
                "taper_ratio": self.nose.taper_ratio,
                "nose_length_ratio": self.nose.nose_length_ratio,
                "fold_angles_deg": list(self.nose.fold_angles_deg),
            },
            "keel": {
                "keel_depth_ratio": self.keel.keel_depth_ratio,
                "has_lock_fold": self.keel.has_lock_fold,
                "lock_fold_angle_deg": self.keel.lock_fold_angle_deg,
                "keel_layers": self.keel.keel_layers,
                "crease_offset_u": self.keel.crease_offset_u,
            },
            "wing": {
                "wingspan_ratio": self.wing.wingspan_ratio,
                "dihedral_deg": self.wing.dihedral_deg,
                "elevon_left_deg": self.wing.elevon_left_deg,
                "elevon_right_deg": self.wing.elevon_right_deg,
                "winglet_angle_deg": self.wing.winglet_angle_deg,
                "has_winglet": self.wing.has_winglet,
            },
            "paper_gsm": self.paper_gsm,
        }


# ============================================================================
# 5. MCTS & SEARCH CANDIDATE
# ============================================================================
@dataclass(frozen=True)
class GeometricStaticScore:
    """Fast non-6DoF heuristic score for MCTS leaf evaluation."""

    cg_margin_score: float  # forward CG within stable margin
    symmetry_score: float  # left-right balance
    wing_loading_score: float  # reasonable wing area vs total mass
    tier0_passed: bool
    rejection_reasons: Tuple[str, ...] = ()

    @property
    def total_value(self) -> float:
        if not self.tier0_passed:
            return 0.0
        return float(
            0.4 * max(0.0, self.cg_margin_score)
            + 0.3 * max(0.0, self.symmetry_score)
            + 0.3 * max(0.0, self.wing_loading_score)
        )


@dataclass
class AircraftCandidate:
    """Candidate aircraft design holding both parametric and folded forms."""

    candidate_id: str
    config: ParametricOrigamiConfig
    step_count: int
    raw_genome: Optional[Any] = None  # Compatible OrigamiGenome object
    geometry: Optional[AirframeGeometry] = None
    descriptor: Optional[BehaviorDescriptor] = None
    benchmark: Optional[FlightBenchmarkResult] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "step_count": self.step_count,
            "config": self.config.to_dict(),
            "geometry": self.geometry.to_dict() if self.geometry else None,
            "descriptor": self.descriptor.to_dict() if self.descriptor else None,
            "benchmark": self.benchmark.to_dict() if self.benchmark else None,
            "metadata": dict(self.metadata),
        }


# ============================================================================
# 6. STAGE 3 (TRIM) & STAGE 4 (EVAL) DATA STRUCTURES
# ============================================================================
@dataclass(frozen=True)
class TrimResult:
    """Result of Nelder-Mead local continuous parameter optimization."""

    candidate_id: str
    optimal_elevon_deg: float
    optimal_dihedral_deg: float
    initial_score: float
    optimized_score: float
    iterations: int
    converged: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "optimal_elevon_deg": self.optimal_elevon_deg,
            "optimal_dihedral_deg": self.optimal_dihedral_deg,
            "initial_score": self.initial_score,
            "optimized_score": self.optimized_score,
            "iterations": self.iterations,
            "converged": self.converged,
        }


@dataclass(frozen=True)
class RobustnessReport:
    """Stage 4 real-world test results: turbulence, throw jitter, high-angle."""

    candidate_id: str
    dryden_survival_rate: float
    dryden_mean_hangtime_s: float
    jitter_robustness_score: float
    high_pitch_recovery_rate: float
    overall_robustness_score: float
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "dryden_survival_rate": self.dryden_survival_rate,
            "dryden_mean_hangtime_s": self.dryden_mean_hangtime_s,
            "jitter_robustness_score": self.jitter_robustness_score,
            "high_pitch_recovery_rate": self.high_pitch_recovery_rate,
            "overall_robustness_score": self.overall_robustness_score,
            "details": dict(self.details),
        }


# ============================================================================
# 7. SUBSYSTEM PROTOCOLS (Interfaces for Module Decoupling)
# ============================================================================
@runtime_checkable
class OrigamiCompilerProtocol(Protocol):
    """Compiles parametric configs into 16-24 fold sequences and OrigamiGenome."""

    def compile(self, config: ParametricOrigamiConfig) -> AircraftCandidate: ...

    def validate_tier0(self, candidate: AircraftCandidate) -> Tuple[bool, List[str]]: ...


@runtime_checkable
class FlightBenchmarkProtocol(Protocol):
    """Executes deterministic 2-point horizontal flight benchmark."""

    def evaluate_candidate(self, candidate: AircraftCandidate) -> FlightBenchmarkResult: ...


@runtime_checkable
class MAPElitesArchiveProtocol(Protocol):
    """Maintains the 3D behavior descriptor grid archive."""

    def add_candidate(self, candidate: AircraftCandidate) -> bool: ...

    def get_elites(self) -> List[AircraftCandidate]: ...

    def coverage(self) -> float: ...


@runtime_checkable
class TrimOptimizerProtocol(Protocol):
    """Local Nelder-Mead trim optimizer for elevon/dihedral angles."""

    def optimize_trim(self, candidate: AircraftCandidate) -> TrimResult: ...


@runtime_checkable
class RobustnessEvaluatorProtocol(Protocol):
    """Evaluates top candidates under turbulence and throw perturbations."""

    def evaluate_robustness(self, candidate: AircraftCandidate) -> RobustnessReport: ...


# ============================================================================
# 8. MILCY ALGORITHM (FORMERLY V3) COMPREHENSIVE PIPELINE PROTOCOL
# ============================================================================
@runtime_checkable
class MilcyPipelineProtocol(Protocol):
    """Protocol for the milcy algorithm pipeline (MAP-Elites + MCTS + Nelder-Mead)."""

    def run(
        self,
        iterations: int = ...,
        seed: int = ...,
        output_dir: str = ...,
    ) -> Sequence[AircraftCandidate]: ...


# Type Aliases for milcy algorithm
MilcyAlgorithmProtocol = MilcyPipelineProtocol
V3PipelineProtocol = MilcyPipelineProtocol

