"""The autonomous genetic research engine of the paper aeroplane simulator.

The engine searches for a paper aeroplane that flies *far* or that stays in
the air *long*, and it searches for it the way a research programme would:
with a pipeline of increasingly expensive evaluations, with an island model
that keeps several independent populations alive, with a covariance adapting
strategy on the continuous part of the design space and with a meta
controller that notices when the search stagnates and changes the search
itself.

The five ingredients
--------------------
1. **Objectives.**  ``distance`` (maximise the horizontal flight distance,
   reference target 88.31 m -- the Dillon Ruble indoor record) and
   ``airtime`` (maximise the hang time, reference target 29.2 s -- the Takuo
   Toda indoor record).  The two are *mutually exclusive* and live in
   separate registry entries: the fitness code never knows which one it is
   computing, it asks the registry.
2. **A multi-fidelity racing pipeline.**  ``tier0`` is the cheap geometric
   filter (grip, centre of gravity, self intersection), ``tier1`` the
   analytical surrogate, ``tier2`` a short reference 6-DoF flight and
   ``tier3`` the full reference 6-DoF flight with 3-D turbulence and a
   Monte-Carlo throw jitter.  The size of every tier is configuration, never
   a number written into the logic.
3. **A real CMA-ES** (:mod:`physics.ga_cma`) on the continuous genes -- the
   fold angles of the genome (main wing fold angle, elevon angle), the
   centre-of-gravity offset and the throw parameters -- so that the wing fold
   angle and the elevon angle become *correlated* instead of being searched
   independently.
4. **A linkage block crossover** that inherits whole blocks of correlated
   parameters from one parent, plus a genome level crossover and a repair
   mutation that perturbs a *feasible* genome instead of throwing it away.
5. **An autonomous meta controller**: an island model with elite migration,
   stagnation detection that triggers a *structure search shift* (more fold
   steps, micro adjustment flaps, an objective re-weighting derived from a
   sensitivity analysis) and a decision journal that records every
   autonomous decision as one JSON object per line.

Everything the run does is persisted: the island populations, the CMA
covariance matrices and evolution paths, the random seeds, the hall of fame
and the generation history.  A run can therefore be interrupted and resumed,
and the resumed run honours overridden trial counts.

The module never re-derives a physical constant: the stress limit comes from
the shared :func:`physics.flight_sim.allowable_stress_pa`, the aerodynamic
coefficients from the shared kernel, and the flight itself from
:func:`physics.parallel_engine.evaluate_case`.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    as_dict,
    utc_now_iso,
)
from .ga_cma import CMAES
from .ga_config import (
    DEFAULT_BROKEN_GENOME_PENALTY,
    DEFAULT_CMA_MUTATION_RATE,
    DEFAULT_FOLD_FLIP_RATE,
    DEFAULT_GA_OFFSPRING_FRACTION,
    DEFAULT_GRIP_PENALTY_WEIGHT,
    DEFAULT_MUTATION_ANGLE_SIGMA_DEG,
    DEFAULT_MUTATION_CREASE_SHIFT,
    DEFAULT_PROGRESS_HISTORY_LIMIT,
    DEFAULT_STAT_ELITE_MAX_FRACTION,
    DEFAULT_SYMMETRY_CHANGE_RATE,
    GAConfig,
    OBJECTIVE_AIRTIME,
    OBJECTIVE_DISTANCE,
    OBJECTIVES,
    STAT_MODE_BEST,
    STAT_MODE_HYBRID,
    STAT_MODE_MEAN,
    STAT_MODE_MEDIAN,
    STAT_MODES,
)
from .origami_genome import (
    FoldStep,
    GripThresholds,
    OrigamiGenome,
    evaluate_grip,
    fold_genome,
    genome_from_archetype,
    random_genome,
)
from .optimizer_domain import INFEASIBLE_SCORE, is_infeasible_flight
from .parallel_engine import evaluate_case, evaluate_flight_surrogate, parallel_evaluate

__all__ = [
    "ObjectiveSpec",
    "OBJECTIVE_REGISTRY",
    "objective_spec",
    "GeneSpec",
    "GENE_SPECS",
    "GENE_NAMES",
    "GENE_INDEX",
    "LINKAGE_BLOCKS",
    "ThrowParams",
    "Individual",
    "Island",
    "ResearchRun",
    "run_research",
    "block_crossover",
    "compute_population_statistics",
    "select_statistic",
    "is_structural_failure",
    "is_failed_evaluation",
    "individual_structural_failure",
    "THROW_SPEED_MIN_MPS",
    "THROW_SPEED_MAX_MPS",
    "RELEASE_ELEVATION_MIN_DEG",
    "RELEASE_ELEVATION_MAX_DEG",
    "PITCH_RATE_MIN_RADPS",
    "PITCH_RATE_MAX_RADPS",
    "ROLL_MIN_DEG",
    "ROLL_MAX_DEG",
    "YAW_MIN_DEG",
    "YAW_MAX_DEG",
    "REFERENCE_DISTANCE_M",
    "REFERENCE_AIRTIME_S",
    "PROGRESS_HISTORY_LIMIT",
]


#: The number of generations the live progress snapshot keeps in its history.
#:
#: The snapshot is polled by the monitor over and over, so the curve it
#: carries must stay small: the *tail* of the last ``PROGRESS_HISTORY_LIMIT``
#: generations is published, every entry being a compact
#: ``{"generation": int, "best_fitness": float | None}`` pair.  The value is
#: the default of :attr:`physics.ga_config.GAConfig.progress_history_limit`
#: and every run may override it.
PROGRESS_HISTORY_LIMIT: int = DEFAULT_PROGRESS_HISTORY_LIMIT


# --------------------------------------------------------------------------
# Human limits of a throw
# --------------------------------------------------------------------------
# The throw parameters of an individual are not free numbers: they describe
# what a pair of human hands can actually do with a 5 g sheet of paper.  The
# bounds below are the *only* place where these limits are written down, and
# every one of them has a physical reason.
#
#: Release speed.  A paper aeroplane is thrown with a push of the forearm, not
#: with a javelin throw: 5 m/s is the speed of a gentle indoor lob and 20 m/s
#: is the fastest hand throw that still leaves the paper intact (the 88.318 m
#: record throw was measured at roughly 20 m/s).  The simulator refuses to
#: extrapolate above 60 m/s (``MAX_RELEASE_SPEED_MPS``), so the search stays
#: far below the model's own validity limit.
THROW_SPEED_MIN_MPS: float = 5.0
THROW_SPEED_MAX_MPS: float = 20.0

#: Release elevation above the horizon.  A thrown paper aeroplane leaves the
#: hand between flat (0 deg, a long low glide) and 45 deg (a steep loft).
#: Beyond 45 deg the throw is a lob rather than a throw and the horizontal
#: component of the release velocity becomes negligible.
RELEASE_ELEVATION_MIN_DEG: float = 0.0
RELEASE_ELEVATION_MAX_DEG: float = 45.0

#: Release pitch rate.  The wrist snap of a hard indoor throw peaks at about
#: 8 rad/s (460 deg/s); that is also the ceiling the flight model itself
#: applies to the release pitch rate (``MAX_RELEASE_PITCH_RATE_RADPS``), so a
#: search above it would be silently clipped by the model instead of being
#: searched.
PITCH_RATE_MIN_RADPS: float = 0.0
PITCH_RATE_MAX_RADPS: float = 8.0

#: Release roll.  The hand can roll the paper by a quarter turn in either
#: direction while releasing it; +-45 deg covers every deliberate bank and
#: stays inside the +-180 deg the launch contract allows.
ROLL_MIN_DEG: float = -45.0
ROLL_MAX_DEG: float = 45.0

#: Release yaw.  The same argument as for the roll: +-45 deg is the aiming
#: error a wrist can introduce, and the launch contract allows +-360 deg.
YAW_MIN_DEG: float = -45.0
YAW_MAX_DEG: float = 45.0

#: Reference target of the distance objective -- the Dillon Ruble indoor
#: record of 88.318 m (see ``physics/records_domain.py``), rounded to 88.31 m.
REFERENCE_DISTANCE_M: float = 88.31

#: Reference target of the airtime objective -- the Takuo Toda indoor record
#: of 29.2 s (see ``physics/records_domain.py``).
REFERENCE_AIRTIME_S: float = 29.2


# --------------------------------------------------------------------------
# The objective registry -- the two modes are strictly separated
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ObjectiveSpec:
    """Everything that belongs to one objective.

    The fitness code of the engine never mentions "distance" or "hang time":
    it asks the registry for the metric of the active objective.  Two runs
    with two different objectives therefore execute two different code paths,
    not one code path with a switched constant.
    """

    key: str
    label: str
    label_ja: str
    metric_key: str
    unit: str
    reference_target: float
    tier2_duration_s: float
    tier3_duration_s: float
    secondary_metric_keys: Tuple[str, ...]
    secondary_references: Dict[str, float]

    def extract(self, result: Dict[str, Any]) -> float:
        """The objective value of one flight result."""
        try:
            value = float(result.get(self.metric_key, 0.0))
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(value):
            return 0.0
        return value

    def secondary_value(self, result: Dict[str, Any], key: str) -> float:
        """The value of one secondary metric of a flight result."""
        try:
            value = float(result.get(key, 0.0))
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(value):
            return 0.0
        return value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "label_ja": self.label_ja,
            "metric_key": self.metric_key,
            "unit": self.unit,
            "reference_target": float(self.reference_target),
            "tier2_duration_s": float(self.tier2_duration_s),
            "tier3_duration_s": float(self.tier3_duration_s),
            "secondary_metric_keys": list(self.secondary_metric_keys),
            "secondary_references": {
                key: float(value) for key, value in self.secondary_references.items()
            },
        }


#: The two mutually exclusive objectives of the engine.
OBJECTIVE_REGISTRY: Dict[str, ObjectiveSpec] = {
    OBJECTIVE_DISTANCE: ObjectiveSpec(
        key=OBJECTIVE_DISTANCE,
        label="Horizontal flight distance",
        label_ja="水平飛行距離",
        metric_key="distance_m",
        unit="m",
        reference_target=REFERENCE_DISTANCE_M,
        tier2_duration_s=4.0,
        tier3_duration_s=12.0,
        secondary_metric_keys=("max_ld_ratio", "stability"),
        secondary_references={"max_ld_ratio": 8.0, "stability": 1.0},
    ),
    OBJECTIVE_AIRTIME: ObjectiveSpec(
        key=OBJECTIVE_AIRTIME,
        label="Hang time",
        label_ja="滞空時間",
        metric_key="flight_time_s",
        unit="s",
        reference_target=REFERENCE_AIRTIME_S,
        tier2_duration_s=8.0,
        tier3_duration_s=32.0,
        secondary_metric_keys=("max_altitude_m", "max_ld_ratio"),
        secondary_references={"max_altitude_m": 10.0, "max_ld_ratio": 8.0},
    ),
}


def objective_spec(key: str) -> ObjectiveSpec:
    """The registry entry of one objective (raises ``KeyError`` if unknown)."""
    if key not in OBJECTIVE_REGISTRY:
        raise KeyError(
            f"unknown objective '{key}'; expected one of {', '.join(OBJECTIVES)}"
        )
    return OBJECTIVE_REGISTRY[key]


# --------------------------------------------------------------------------
# The continuous gene space
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class GeneSpec:
    """One continuous design variable with its physical bounds."""

    name: str
    low: float
    high: float
    unit: str
    default: float
    description: str

    def clip(self, value: float) -> float:
        return min(max(float(value), self.low), self.high)

    @property
    def range(self) -> float:
        return float(self.high - self.low)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "low": float(self.low),
            "high": float(self.high),
            "unit": self.unit,
            "default": float(self.default),
            "description": self.description,
        }


#: The continuous part of the search space.  ``main_wing_fold_angle_deg`` and
#: ``elevon_angle_deg`` are *fold angles of the genome*: they are written
#: into the crease programme of the individual, so the covariance adaptation
#: links the wing fold to the elevon the way the two are linked on a real
#: sheet of paper.
GENE_SPECS: Tuple[GeneSpec, ...] = (
    GeneSpec(
        name="main_wing_fold_angle_deg",
        low=-180.0,
        high=180.0,
        unit="deg",
        default=30.0,
        description="fold angle of the main wing crease of the genome",
    ),
    GeneSpec(
        name="elevon_angle_deg",
        low=-45.0,
        high=45.0,
        unit="deg",
        default=5.0,
        description="trailing edge trim fold, also written into the flight model",
    ),
    GeneSpec(
        name="cg_offset_x_m",
        low=-0.05,
        high=0.05,
        unit="m",
        default=0.0,
        description="fore/aft offset of the centre of gravity (+ = aft)",
    ),
    GeneSpec(
        name="throw_speed_mps",
        low=THROW_SPEED_MIN_MPS,
        high=THROW_SPEED_MAX_MPS,
        unit="m/s",
        default=12.0,
        description="release speed of the throw",
    ),
    GeneSpec(
        name="release_elevation_deg",
        low=RELEASE_ELEVATION_MIN_DEG,
        high=RELEASE_ELEVATION_MAX_DEG,
        unit="deg",
        default=10.0,
        description="elevation of the release above the horizon",
    ),
    GeneSpec(
        name="pitch_rate_radps",
        low=PITCH_RATE_MIN_RADPS,
        high=PITCH_RATE_MAX_RADPS,
        unit="rad/s",
        default=0.0,
        description="pitch rate the wrist leaves on the airframe at release",
    ),
    GeneSpec(
        name="roll_deg",
        low=ROLL_MIN_DEG,
        high=ROLL_MAX_DEG,
        unit="deg",
        default=0.0,
        description="bank angle of the release",
    ),
    GeneSpec(
        name="yaw_deg",
        low=YAW_MIN_DEG,
        high=YAW_MAX_DEG,
        unit="deg",
        default=0.0,
        description="yaw offset of the release",
    ),
)

#: The names of the continuous genes, in the canonical order.
GENE_NAMES: Tuple[str, ...] = tuple(spec.name for spec in GENE_SPECS)

#: Index of every gene inside the continuous vector.
GENE_INDEX: Dict[str, int] = {name: index for index, name in enumerate(GENE_NAMES)}

#: The linkage blocks: groups of genes that are inherited as a unit by the
#: block crossover.  The wing block couples the fold geometry with the
#: balance of the airframe, the throw block couples the speed with the
#: elevation of the release and the attitude block couples the three rates
#: and angles the hand leaves on the paper.
LINKAGE_BLOCKS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("main_wing", ("main_wing_fold_angle_deg", "elevon_angle_deg", "cg_offset_x_m")),
    ("throw", ("throw_speed_mps", "release_elevation_deg")),
    ("attitude", ("pitch_rate_radps", "roll_deg", "yaw_deg")),
)


def default_genes() -> Dict[str, float]:
    """The default value of every continuous gene."""
    return {spec.name: float(spec.default) for spec in GENE_SPECS}


def gene_bounds() -> List[Tuple[float, float]]:
    """The bounds of the continuous vector, in the canonical order."""
    return [(spec.low, spec.high) for spec in GENE_SPECS]


def normalise_genes(genes: Dict[str, float]) -> List[float]:
    """Map a gene vector into ``[0, 1]`` (used for the diversity measure)."""
    out: List[float] = []
    for spec in GENE_SPECS:
        span = spec.range or 1.0
        out.append((float(genes.get(spec.name, spec.default)) - spec.low) / span)
    return out


# --------------------------------------------------------------------------
# The individual
# --------------------------------------------------------------------------
@dataclass
class ThrowParams:
    """The parameters of the throw that releases the airframe."""

    speed_mps: float = 12.0
    release_elevation_deg: float = 10.0
    pitch_rate_radps: float = 0.0
    roll_deg: float = 0.0
    yaw_deg: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "speed_mps": float(self.speed_mps),
            "release_elevation_deg": float(self.release_elevation_deg),
            "pitch_rate_radps": float(self.pitch_rate_radps),
            "roll_deg": float(self.roll_deg),
            "yaw_deg": float(self.yaw_deg),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ThrowParams":
        return cls(
            speed_mps=float(payload.get("speed_mps", 12.0)),
            release_elevation_deg=float(payload.get("release_elevation_deg", 10.0)),
            pitch_rate_radps=float(payload.get("pitch_rate_radps", 0.0)),
            roll_deg=float(payload.get("roll_deg", 0.0)),
            yaw_deg=float(payload.get("yaw_deg", 0.0)),
        )

    @classmethod
    def from_genes(cls, genes: Dict[str, float]) -> "ThrowParams":
        return cls(
            speed_mps=float(genes.get("throw_speed_mps", 12.0)),
            release_elevation_deg=float(genes.get("release_elevation_deg", 10.0)),
            pitch_rate_radps=float(genes.get("pitch_rate_radps", 0.0)),
            roll_deg=float(genes.get("roll_deg", 0.0)),
            yaw_deg=float(genes.get("yaw_deg", 0.0)),
        )


@dataclass(eq=False)
class Individual:
    """One candidate: a fold genome *and* the throw that releases it.

    The dataclass compares by *identity* (``eq=False``): two candidates that
    happen to carry the same numbers are still two different candidates, and
    the migration must be able to tell them apart.
    """

    index: int
    genome: OrigamiGenome
    genes: Dict[str, float]
    throw: ThrowParams
    tier: int = 0
    metric: float = 0.0
    fitness: float = float("-inf")
    feasible: bool = False
    grip_penalty: float = 0.0
    #: How comfortably the design passes the grip filter (the smallest
    #: ratio of a grip condition to its threshold).  It is the *quality*
    #: signal of the cheap tier and it is what the promotion to the
    #: surrogate tier is ranked by.
    grip_quality: float = 0.0
    grip_reasons: List[str] = field(default_factory=list)
    result: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, float] = field(default_factory=dict)
    #: The structural failure flag of every Monte-Carlo sample of the full
    #: fidelity tier (empty when the individual was not evaluated there).
    sample_failures: List[bool] = field(default_factory=list)
    #: The folded solid of the genome (cached, never serialised).
    geometry: Any = None

    @property
    def key(self) -> str:
        return f"{self.index:06d}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": int(self.index),
            "genome": self.genome.to_dict(),
            "genes": {str(k): float(v) for k, v in self.genes.items()},
            "throw": self.throw.to_dict(),
            "tier": int(self.tier),
            "metric": float(self.metric),
            "fitness": float(self.fitness) if math.isfinite(self.fitness) else None,
            "feasible": bool(self.feasible),
            "grip_penalty": float(self.grip_penalty),
            "grip_quality": float(self.grip_quality),
            "grip_reasons": list(self.grip_reasons),
            "metrics": {str(k): float(v) for k, v in self.metrics.items()},
            "sample_failures": [bool(flag) for flag in self.sample_failures],
            "result": {
                key: value
                for key, value in self.result.items()
                if isinstance(value, (int, float, str, bool, type(None)))
            },
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Individual":
        from .origami_genome import genome_from_dict

        genome = genome_from_dict(dict(payload.get("genome") or {}))
        fitness = payload.get("fitness")
        return cls(
            index=int(payload.get("index", 0)),
            genome=genome,
            genes={str(k): float(v) for k, v in dict(payload.get("genes") or {}).items()},
            throw=ThrowParams.from_dict(dict(payload.get("throw") or {})),
            tier=int(payload.get("tier", 0)),
            metric=float(payload.get("metric", 0.0)),
            fitness=float(fitness) if fitness is not None else float("-inf"),
            feasible=bool(payload.get("feasible", False)),
            grip_penalty=float(payload.get("grip_penalty", 0.0)),
            grip_quality=float(payload.get("grip_quality", 0.0)),
            grip_reasons=[str(value) for value in payload.get("grip_reasons") or []],
            metrics={str(k): float(v) for k, v in dict(payload.get("metrics") or {}).items()},
            sample_failures=[bool(flag) for flag in payload.get("sample_failures") or []],
            result=dict(payload.get("result") or {}),
        )


# --------------------------------------------------------------------------
# Genome operators
# --------------------------------------------------------------------------
#: The role a fold step plays in the programme.  The role is decided by the
#: position of the step, so it survives every mutation and crossover.
STEP_ROLES: Tuple[str, ...] = ("keel", "nose", "main_wing", "elevon", "trim")


def step_role(step_index: int) -> str:
    """The role of the fold step with the (1 based) index ``step_index``."""
    position = max(int(step_index), 1) - 1
    if position < len(STEP_ROLES):
        return STEP_ROLES[position]
    return STEP_ROLES[-1]


def _role_index(genome: OrigamiGenome, role: str) -> Optional[int]:
    """The index of the step that carries ``role`` (``None`` when absent)."""
    if not genome.steps:
        return None
    wanted = STEP_ROLES.index(role) if role in STEP_ROLES else len(STEP_ROLES) - 1
    index = min(wanted, len(genome.steps) - 1)
    return index


def apply_genes_to_genome(
    genome: OrigamiGenome,
    genes: Dict[str, float],
) -> OrigamiGenome:
    """Write the genome-bound genes back into the crease programme.

    ``main_wing_fold_angle_deg`` sets the fold angle of the main wing crease
    and ``elevon_angle_deg`` the fold angle of the trailing edge trim.  The
    two are the continuous fold angles of the genome, so the covariance
    adaptation of the CMA-ES can learn that they belong together.
    """
    steps = list(genome.steps)
    if not steps:
        return replace(genome, steps=steps)

    main_index = _role_index(genome, "main_wing")
    elevon_index = _role_index(genome, "elevon")
    main_angle = float(genes.get("main_wing_fold_angle_deg", 0.0))
    elevon_angle = float(genes.get("elevon_angle_deg", 0.0))

    if main_index is not None:
        step = steps[main_index]
        steps[main_index] = replace(
            step,
            fold_angle_deg=main_angle if step.mountain else -abs(main_angle),
            mountain=main_angle >= 0.0,
        )
    if elevon_index is not None and elevon_index != main_index:
        step = steps[elevon_index]
        steps[elevon_index] = replace(
            step,
            fold_angle_deg=elevon_angle if step.mountain else -abs(elevon_angle),
            mountain=elevon_angle >= 0.0,
        )
    return replace(genome, steps=steps)


def mutate_genome(
    genome: OrigamiGenome,
    rng: random.Random,
    max_steps: int,
    angle_sigma_deg: float = DEFAULT_MUTATION_ANGLE_SIGMA_DEG,
    crease_shift: float = DEFAULT_MUTATION_CREASE_SHIFT,
    micro_flaps: bool = False,
    fold_flip_rate: float = DEFAULT_FOLD_FLIP_RATE,
    symmetry_change_rate: float = DEFAULT_SYMMETRY_CHANGE_RATE,
) -> OrigamiGenome:
    """Repair mutation: perturb an *existing* genome instead of replacing it.

    A fresh random genome is grip feasible only about a quarter of the time,
    so the population would never climb if every offspring were sampled from
    scratch.  The repair mutation keeps the crease programme of a feasible
    parent and only jitters it: the fold angles are perturbed, the crease
    lines are translated a little and the sense of a fold is flipped now and
    then.  The grip filter therefore keeps rejecting the *bad* designs, but
    the good ones stay in the pool.

    The rates of the operator are configuration
    (:attr:`physics.ga_config.GAConfig.mutation_angle_sigma_deg`,
    ``mutation_crease_shift``, ``fold_flip_rate`` and
    ``symmetry_change_rate``); the defaults below are the documented
    defaults of :mod:`physics.ga_config`.
    """
    steps: List[FoldStep] = []
    for step in genome.steps:
        angle = step.fold_angle_deg + rng.gauss(0.0, angle_sigma_deg)
        angle = min(max(angle, -180.0), 180.0)
        mountain = step.mountain
        if rng.random() < fold_flip_rate:
            mountain = not mountain
            angle = abs(angle) if mountain else -abs(angle)
        symmetry = step.symmetry
        if rng.random() < symmetry_change_rate:
            symmetry = rng.choice(("symmetric", "left", "right"))
        (u0, v0), (u1, v1) = step.crease_line
        du = rng.gauss(0.0, crease_shift)
        dv = rng.gauss(0.0, crease_shift)
        line = (
            (min(max(u0 + du, 0.0), 1.0), min(max(v0 + dv, 0.0), 1.0)),
            (min(max(u1 + du, 0.0), 1.0), min(max(v1 + dv, 0.0), 1.0)),
        )
        steps.append(replace(step, fold_angle_deg=angle, mountain=mountain, symmetry=symmetry, crease_line=line))

    if micro_flaps:
        steps = _add_micro_flaps(steps, rng)
    steps = steps[: max(int(max_steps), 1)]
    return replace(genome, steps=steps)


def _add_micro_flaps(steps: List[FoldStep], rng: random.Random) -> List[FoldStep]:
    """Append the micro adjustment flaps (winglets) of the structure shift."""
    if not steps:
        return steps
    count = 1 + (1 if rng.random() < 0.4 else 0)
    base = len(steps)
    for offset in range(count):
        angle = rng.uniform(4.0, 16.0) * (1.0 if rng.random() < 0.5 else -1.0)
        side = rng.uniform(0.30, 0.46)
        position = rng.uniform(0.62, 0.92)
        steps.append(
            FoldStep(
                step_index=base + offset + 1,
                crease_line=((side, position), (side + 0.10, position - 0.04)),
                fold_angle_deg=angle,
                symmetry="right" if offset == 0 else "left",
                layer_order="auto",
                target_face_ids=(),
                group=(),
                set_group=f"flap{base + offset + 1}",
                mountain=angle >= 0.0,
                label="micro flap",
            )
        )
    return steps


def crossover_genomes(
    parent_a: OrigamiGenome,
    parent_b: OrigamiGenome,
    rng: random.Random,
) -> OrigamiGenome:
    """Genome level crossover: every fold step is taken from one parent."""
    children: List[FoldStep] = []
    count = max(len(parent_a.steps), len(parent_b.steps))
    for index in range(count):
        from_a = rng.random() < 0.5
        source = parent_a if (from_a or index >= len(parent_a.steps)) else parent_b
        fallback = parent_b if source is parent_a else parent_a
        if index >= len(source.steps):
            source = fallback
        if index >= len(source.steps):
            break
        step = source.steps[index]
        children.append(replace(step, step_index=index + 1))
    if not children:
        children = list(parent_a.steps) or list(parent_b.steps)
    return replace(parent_a, steps=children, label="crossover fold programme")


def block_crossover(
    parent_a: Dict[str, float],
    parent_b: Dict[str, float],
    rng: random.Random,
) -> Dict[str, float]:
    """Linkage block crossover of the continuous genes.

    Every block of :data:`LINKAGE_BLOCKS` is inherited **as a unit** from one
    of the two parents, so the linkage the CMA-ES discovered inside a block
    (for example between the main wing fold angle and the elevon angle) is
    never broken by the crossover.  Genes that do not belong to a block are
    inherited one by one.
    """
    child: Dict[str, float] = {}
    assigned: set = set()
    for _name, members in LINKAGE_BLOCKS:
        source = parent_a if rng.random() < 0.5 else parent_b
        for member in members:
            child[member] = float(source.get(member, 0.0))
            assigned.add(member)
    for spec in GENE_SPECS:
        if spec.name in assigned:
            continue
        source = parent_a if rng.random() < 0.5 else parent_b
        child[spec.name] = float(source.get(spec.name, spec.default))
    return child


def genes_from_genome(genome: OrigamiGenome, throw: ThrowParams) -> Dict[str, float]:
    """Read the continuous genes back out of a genome and a throw."""
    genes = default_genes()
    main_index = _role_index(genome, "main_wing")
    elevon_index = _role_index(genome, "elevon")
    if main_index is not None:
        genes["main_wing_fold_angle_deg"] = float(genome.steps[main_index].fold_angle_deg)
    if elevon_index is not None and elevon_index != main_index:
        genes["elevon_angle_deg"] = float(genome.steps[elevon_index].fold_angle_deg)
    genes["throw_speed_mps"] = float(throw.speed_mps)
    genes["release_elevation_deg"] = float(throw.release_elevation_deg)
    genes["pitch_rate_radps"] = float(throw.pitch_rate_radps)
    genes["roll_deg"] = float(throw.roll_deg)
    genes["yaw_deg"] = float(throw.yaw_deg)
    return genes


# --------------------------------------------------------------------------
# Statistics over a population
# --------------------------------------------------------------------------
def _median(values: Sequence[float]) -> float:
    """The median of a sequence (``0.0`` for an empty sequence)."""
    data = sorted(float(value) for value in values)
    count = len(data)
    if count == 0:
        return 0.0
    middle = count // 2
    if count % 2 == 1:
        return data[middle]
    return 0.5 * (data[middle - 1] + data[middle])


def compute_population_statistics(
    tier3_values: Sequence[float],
    overall_values: Sequence[float],
    failures: Sequence[bool],
) -> Dict[str, Any]:
    """The four statistics the research report is built on.

    ``mean``
        The arithmetic mean of the objective metric over the full fidelity
        (tier 3) population.
    ``median``
        The median of the same population -- the robust central value, which
        is not dragged away by a single spectacular throw.
    ``overall_median``
        The median over *every* successful flight evaluation of the whole run
        (all tiers), i.e. the central value of the complete search history.
    ``failure_rate``
        The fraction of the full fidelity population that failed
        structurally (diverged, crashed or exceeded the allowable stress).
    """
    values = [float(value) for value in tier3_values]
    overall = [float(value) for value in overall_values]
    flags = [bool(flag) for flag in failures]
    mean = sum(values) / len(values) if values else 0.0
    return {
        "mean": mean,
        "median": _median(values),
        "overall_median": _median(overall),
        "failure_rate": (sum(1 for flag in flags if flag) / len(flags)) if flags else 0.0,
        "best": max(values) if values else 0.0,
        "count": float(len(values)),
    }


def select_statistic(
    statistics: Dict[str, float],
    stat_mode: str,
    weights: Optional[Dict[str, float]] = None,
) -> float:
    """The population statistic selected by ``stat_mode``.

    ``mean``
        The arithmetic mean of the objective metric.
    ``median``
        The median of the objective metric.
    ``best``
        The best individual of the population.
    ``hybrid_median_mean`` (the default)
        The weighted blend

            ``hybrid = (w_mean * mean + w_median * median) / (w_mean + w_median)``

        with the default weights ``w_mean = w_median = 0.5``, i.e.
        ``hybrid = 0.5 * mean + 0.5 * median``.  The blend is *normalised*
        by the reference target of the active objective when it is used as a
        fitness, so a fitness of ``1.0`` means "as good as the world record
        the objective is measured against".
    """
    if stat_mode == STAT_MODE_MEAN:
        return float(statistics.get("mean", 0.0))
    if stat_mode == STAT_MODE_MEDIAN:
        return float(statistics.get("median", 0.0))
    if stat_mode == STAT_MODE_BEST:
        return float(statistics.get("best", 0.0))
    if stat_mode != STAT_MODE_HYBRID:
        raise ValueError(
            f"unknown stat mode '{stat_mode}'; expected one of {', '.join(STAT_MODES)}"
        )
    blend = dict(weights or {})
    w_mean = float(blend.get("mean", 0.5))
    w_median = float(blend.get("median", 0.5))
    total = w_mean + w_median
    if total <= 0.0:
        return float(statistics.get("median", 0.0))
    mean = float(statistics.get("mean", 0.0))
    median = float(statistics.get("median", 0.0))
    return (w_mean * mean + w_median * median) / total


def is_structural_failure(result: Dict[str, Any], allowable_pa: Optional[float] = None) -> bool:
    """Did the flight fail structurally (diverge, crash or overstress)?

    The stress limit is the **shared** allowable of the flight model
    (:func:`physics.flight_sim.allowable_stress_pa`); the engine never
    re-derives a second one.
    """
    status = str(result.get("status", "")).lower()
    if status in ("diverged", "crashed", "failed", "error", "no-launch"):
        return True
    if bool(result.get("crashed")) or bool(result.get("diverged")):
        return True
    if result.get("error"):
        return True
    if allowable_pa is not None:
        try:
            stress = float(result.get("max_stress_pa", 0.0))
        except (TypeError, ValueError):
            stress = 0.0
        if stress > float(allowable_pa):
            return True
    return False


def is_failed_evaluation(result: Dict[str, Any], allowable_pa: Optional[float] = None) -> bool:
    """Did the evaluation of this flight fail?

    Two rules of the simulator are combined here and **no third rule is
    invented**:

    * :func:`physics.optimizer_domain.is_infeasible_flight` -- the shared
      contract of the whole simulator: a flight whose ``status`` is
      ``error`` or ``failed`` could not be evaluated at all.
    * :func:`physics.ga_domain.is_structural_failure` -- the rule of the
      research engine: the flight diverged, crashed, never launched or
      exceeded the **shared** allowable stress of the paper stock.

    A failed evaluation must never be scored like a legitimate flight: its
    metric is ``0.0``, which is exactly the metric of an aeroplane that
    flew nowhere, and an optimiser that cannot tell the two apart will
    happily publish a broken design as the best one it ever found.
    """
    if is_infeasible_flight(result):
        return True
    return is_structural_failure(result, allowable_pa)


def individual_structural_failure(
    individual: "Individual",
    allowable_pa: Optional[float] = None,
) -> bool:
    """Did the individual fail structurally?

    An individual of the full fidelity tier is evaluated several times
    (the Monte-Carlo ensemble of the tier); the individual counts as a
    structural failure when **any** of its samples failed, so a design that
    only survives one of its three throws is not published as a robust one.
    """
    if individual.sample_failures:
        return any(bool(flag) for flag in individual.sample_failures)
    return is_failed_evaluation(individual.result, allowable_pa)


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    """The Pearson correlation of two equal length sequences."""
    count = len(xs)
    if count < 3 or count != len(ys):
        return 0.0
    mean_x = sum(xs) / count
    mean_y = sum(ys) / count
    sxx = sum((value - mean_x) ** 2 for value in xs)
    syy = sum((value - mean_y) ** 2 for value in ys)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    if sxx <= 0.0 or syy <= 0.0:
        return 0.0
    value = sxy / math.sqrt(sxx * syy)
    if not math.isfinite(value):
        return 0.0
    return max(-1.0, min(1.0, value))


# --------------------------------------------------------------------------
# The decision journal
# --------------------------------------------------------------------------
class JournalWriter:
    """The append only decision journal of a research run.

    Every autonomous decision of the meta controller is appended as exactly
    one JSON object per line, with the timestamp, the generation, the island
    the decision was taken on, the decision itself, the trigger that caused
    it and the parameters that were changed.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.count = 0
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)

    def record(
        self,
        generation: int,
        island: Optional[int],
        decision: str,
        trigger: str,
        changes: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Append one decision and return the record that was written."""
        entry: Dict[str, Any] = {
            "timestamp": utc_now_iso(),
            "generation": int(generation),
            "island": None if island is None else int(island),
            "decision": str(decision),
            "trigger": str(trigger),
            "changes": dict(changes or {}),
        }
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        self.count += 1
        return entry

    def read(self) -> List[Dict[str, Any]]:
        """Every entry of the journal, oldest first."""
        if not os.path.exists(self.path):
            return []
        entries: List[Dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return entries


# --------------------------------------------------------------------------
# The island
# --------------------------------------------------------------------------
@dataclass
class Island:
    """One independent population with its own CMA-ES and random stream.

    The island also owns the **meta state** of the autonomous meta
    controller: the number of fold steps of its genomes and the micro
    adjustment flaps are the *structure* the island searches in.  A
    structure search shift therefore changes the search of **this island
    only** -- it never leaks into the other islands of the run.
    """

    id: int
    seed: int
    rng: random.Random
    cma: CMAES
    population: List[Individual] = field(default_factory=list)
    best_fitness: float = float("-inf")
    best_metric: float = 0.0
    stagnation_counter: int = 0
    diversity: float = 0.0
    improved: bool = True
    history: List[Dict[str, Any]] = field(default_factory=list)
    #: The number of fold steps the genomes of this island may have.
    max_fold_steps: int = 0
    #: Whether the micro adjustment flaps are part of the representation.
    micro_flaps: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": int(self.id),
            "seed": int(self.seed),
            "population": [individual.to_dict() for individual in self.population],
            "cma": self.cma.to_dict(),
            "best_fitness": float(self.best_fitness) if math.isfinite(self.best_fitness) else None,
            "best_metric": float(self.best_metric),
            "stagnation_counter": int(self.stagnation_counter),
            "diversity": float(self.diversity),
            "history": list(self.history),
            "max_fold_steps": int(self.max_fold_steps),
            "micro_flaps": bool(self.micro_flaps),
            "rng_state": _encode_pickle(self.rng.getstate()),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Island":
        seed = int(payload.get("seed", 0))
        rng = random.Random(seed)
        state = payload.get("rng_state")
        if state:
            rng.setstate(_decode_pickle(state))
        cma = CMAES.from_dict(dict(payload["cma"]))
        fitness = payload.get("best_fitness")
        island = cls(
            id=int(payload.get("id", 0)),
            seed=seed,
            rng=rng,
            cma=cma,
            population=[Individual.from_dict(item) for item in payload.get("population") or []],
            best_fitness=float(fitness) if fitness is not None else float("-inf"),
            best_metric=float(payload.get("best_metric", 0.0)),
            stagnation_counter=int(payload.get("stagnation_counter", 0)),
            diversity=float(payload.get("diversity", 0.0)),
            history=list(payload.get("history") or []),
            max_fold_steps=int(payload.get("max_fold_steps", 0)),
            micro_flaps=bool(payload.get("micro_flaps", False)),
        )
        return island


def _encode_pickle(value: Any) -> str:
    """Encode a python object as a base64 string (for the JSON checkpoint)."""
    import base64
    import pickle

    return base64.b64encode(pickle.dumps(value)).decode("ascii")


def _decode_pickle(value: str) -> Any:
    """The inverse of :func:`_encode_pickle`."""
    import base64
    import pickle

    return pickle.loads(base64.b64decode(value.encode("ascii")))


# --------------------------------------------------------------------------
# The research run
# --------------------------------------------------------------------------
class ResearchRun:
    """One autonomous research run.

    The object owns the islands, the budget, the journal and the
    checkpoints.  :func:`run_research` is the public entry point; the API
    and the command line both go through it.
    """

    def __init__(
        self,
        config: GAConfig,
        overrides: Optional[Dict[str, Any]] = None,
        *,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.config = config.validate()
        self.overrides = {str(key): value for key, value in dict(overrides or {}).items()}
        self.objective = objective_spec(self.config.objective)
        self.run_id = uuid.uuid4().hex[:16]
        self.fingerprint = hashlib.sha256(
            json.dumps(self.config.to_dict(), sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]
        self.started_at = time.time()
        self.rng = random.Random(self.config.seed)
        self.journal = JournalWriter(self.config.resolved_journal_path())
        self.islands: List[Island] = []
        self.hall_of_fame: List[Individual] = []
        self.history: List[Dict[str, Any]] = []
        self.evaluation_count = 0
        self.tier_counts: Dict[str, int] = {"tier0": 0, "tier1": 0, "tier2": 0, "tier3": 0}
        self.overall_values: List[float] = []
        self.generation0_feasible_fraction = 0.0
        self.secondary_weights: Dict[str, float] = {}
        self.stat_weights: Dict[str, float] = dict(self.config.stat_weights)
        self.generation = 0
        self._individual_counter = 0
        #: The cached shared allowable stress of the paper stock.
        self._allowable_cache: Optional[float] = None
        #: The listener that receives the live progress of the run.
        self.progress_callback = progress_callback
        #: The compact per generation curve of the live progress.
        self._progress_history: List[Dict[str, Any]] = []
        #: The best fitness found so far (monotone over the generations).
        self._best_fitness_so_far: float = float("-inf")

    # ------------------------------------------------------------------
    # Budget
    # ------------------------------------------------------------------
    @property
    def remaining_trials(self) -> int:
        """How many flight evaluations are still allowed."""
        return max(int(self.config.max_trials) - int(self.evaluation_count), 0)

    @property
    def budget_exhausted(self) -> bool:
        return self.remaining_trials <= 0

    # ------------------------------------------------------------------
    # Preparation
    # ------------------------------------------------------------------
    def _prepare_output(self) -> None:
        os.makedirs(self.config.out_dir, exist_ok=True)
        os.makedirs(self.config.resolved_checkpoint_dir(), exist_ok=True)
        journal_directory = os.path.dirname(os.path.abspath(self.config.resolved_journal_path()))
        if journal_directory:
            os.makedirs(journal_directory, exist_ok=True)

    def _grip_thresholds(self) -> GripThresholds:
        """The grip thresholds of the run (configurable end to end)."""
        limits = GripThresholds()
        for name, value in (self.config.grip_thresholds or {}).items():
            if not hasattr(limits, name):
                continue
            current = getattr(limits, name)
            if isinstance(current, bool):
                setattr(limits, name, bool(value))
            elif isinstance(current, int):
                setattr(limits, name, int(value))
            else:
                setattr(limits, name, float(value))
        return limits

    # ------------------------------------------------------------------
    # Bootstrapping
    # ------------------------------------------------------------------
    def _seed_genomes(self, island_index: int, count: int, rng: random.Random) -> List[OrigamiGenome]:
        """The grip feasible seed genomes of an island.

        A purely random generation 0 is almost entirely infeasible (only
        about a quarter of the "families" genomes pass the grip filter and
        the "free" ones almost never do), so the population is seeded with
        the classic archetypes -- which are known to be throwable -- mixed
        with random programmes.  The mix is configurable.
        """
        archetypes = ("dart", "nakamura", "sky_king")
        genomes: List[OrigamiGenome] = []
        for index in range(count):
            archetype = archetypes[(island_index + index) % len(archetypes)]
            genome = genome_from_archetype(archetype)
            genomes.append(
                replace(
                    genome,
                    key=f"{archetype}-g{island_index}-{index}",
                    label=f"{archetype} seed",
                    seed=rng.randrange(1, 2 ** 30),
                    meta={"step_count": len(genome.steps), "origin": "archetype"},
                )
            )
        return genomes

    def _random_genomes(self, island_index: int, count: int, rng: random.Random) -> List[OrigamiGenome]:
        """The random seed genomes of an island."""
        genomes: List[OrigamiGenome] = []
        for index in range(count):
            steps = rng.randint(max(3, self.config.max_fold_steps - 3), self.config.max_fold_steps)
            strategy = self.config.genome_strategy if rng.random() < 0.8 else "free"
            genomes.append(
                random_genome(
                    rng,
                    steps=steps,
                    strategy=strategy,
                    seed=rng.randrange(1, 2 ** 30),
                    key=f"random-g{island_index}-{index}",
                )
            )
        return genomes

    def _bootstrap_island(self, island: Island) -> None:
        """Fill the initial population of an island."""
        rng = island.rng
        archetype_count = max(int(self.config.archetype_seed_count), 0)
        random_count = max(int(self.config.random_seed_count), 0)
        seeds = self._seed_genomes(island.id, archetype_count, rng)
        seeds.extend(self._random_genomes(island.id, random_count, rng))

        population: List[Individual] = []
        for genome in seeds[: self.config.population_size]:
            genes = default_genes()
            spread = float(self.config.bootstrap_gene_spread)
            for spec in GENE_SPECS:
                if spec.range > 0.0:
                    genes[spec.name] = spec.clip(spec.default + rng.gauss(0.0, spread * spec.range))
            genome = apply_genes_to_genome(genome, genes)
            throw = ThrowParams.from_genes(genes)
            population.append(
                Individual(
                    index=self._next_index(),
                    genome=genome,
                    genes=genes,
                    throw=throw,
                )
            )
        island.population = population
        if population:
            mean = [
                sum(float(ind.genes.get(name, 0.0)) for ind in population) / len(population)
                for name in GENE_NAMES
            ]
            island.cma.set_mean(mean)

    def _build_islands(self) -> None:
        """Create the islands with their own seeds, streams and CMA-ES."""
        self.islands = []
        for index in range(self.config.islands):
            seed = int(self.config.seed) * 1_009 + index * 97 + 13
            rng = random.Random(seed)
            cma = CMAES(
                x0=[spec.default for spec in GENE_SPECS],
                sigma0=float(self.config.cma_sigma0),
                bounds=gene_bounds(),
                seed=seed,
                lam=int(self.config.cma_lambda),
                mu=int(self.config.cma_mu),
            )
            island = Island(id=index, seed=seed, rng=rng, cma=cma)
            # The structure the island searches in is its own: a structure
            # search shift of one island never touches another one.
            island.max_fold_steps = int(self.config.max_fold_steps)
            island.micro_flaps = bool(self.config.micro_flaps)
            self._bootstrap_island(island)
            self.islands.append(island)

    def _next_index(self) -> int:
        self._individual_counter += 1
        return self._individual_counter

    # ------------------------------------------------------------------
    # Offspring
    # ------------------------------------------------------------------
    def _select_parents(self, island: Island, rng: random.Random) -> Tuple[Individual, Individual]:
        """Tournament selection of two parents out of the island population."""
        population = island.population
        if not population:
            raise RuntimeError("the island has no population")
        if len(population) == 1:
            return population[0], population[0]

        def tournament() -> Individual:
            best = population[rng.randrange(len(population))]
            for _ in range(2):
                challenger = population[rng.randrange(len(population))]
                if challenger.fitness > best.fitness:
                    best = challenger
            return best

        return tournament(), tournament()

    def _make_offspring(self, island: Island) -> Tuple[List[Individual], List[int]]:
        """Create the offspring of one generation.

        Two kinds of offspring are produced:

        * the CMA-ES offspring, whose continuous gene vector comes from the
          covariance adapted distribution and whose genome comes from the
          crossover of two elites of the island,
        * the block crossover offspring, whose whole linkage blocks are
          inherited from one parent.

        Both are repaired by the mutation operator, so a feasible parent
        stays feasible.
        """
        rng = island.rng
        samples = island.cma.ask()
        offspring: List[Individual] = []
        cma_indices: List[int] = []

        ga_count = max(1, int(round(self.config.population_size * self._ga_fraction())))
        for index, sample in enumerate(samples):
            parent_a, parent_b = self._select_parents(island, rng)
            genome = crossover_genomes(parent_a.genome, parent_b.genome, rng)
            if rng.random() < float(self.config.cma_mutation_rate):
                genome = mutate_genome(
                    genome,
                    rng,
                    island.max_fold_steps,
                    micro_flaps=island.micro_flaps,
                    fold_flip_rate=float(self.config.fold_flip_rate),
                    symmetry_change_rate=float(self.config.symmetry_change_rate),
                    angle_sigma_deg=float(self.config.mutation_angle_sigma_deg),
                    crease_shift=float(self.config.mutation_crease_shift),
                )
            genes = {
                name: float(value)
                for name, value in zip(GENE_NAMES, sample)
            }
            genome = apply_genes_to_genome(genome, genes)
            throw = ThrowParams.from_genes(genes)
            offspring.append(
                Individual(
                    index=self._next_index(),
                    genome=genome,
                    genes=genes,
                    throw=throw,
                )
            )
            cma_indices.append(len(offspring) - 1)

        for _ in range(ga_count):
            parent_a, parent_b = self._select_parents(island, rng)
            genes = block_crossover(parent_a.genes, parent_b.genes, rng)
            genome = crossover_genomes(parent_a.genome, parent_b.genome, rng)
            genome = mutate_genome(
                genome,
                rng,
                island.max_fold_steps,
                micro_flaps=island.micro_flaps,
                fold_flip_rate=float(self.config.fold_flip_rate),
                symmetry_change_rate=float(self.config.symmetry_change_rate),
                angle_sigma_deg=float(self.config.mutation_angle_sigma_deg),
                crease_shift=float(self.config.mutation_crease_shift),
            )
            genome = apply_genes_to_genome(genome, genes)
            throw = ThrowParams.from_genes(genes)
            offspring.append(
                Individual(
                    index=self._next_index(),
                    genome=genome,
                    genes=genes,
                    throw=throw,
                )
            )

        return offspring, cma_indices

    def _ga_fraction(self) -> float:
        """The share of the offspring produced by the block crossover."""
        return float(self.config.ga_offspring_fraction)

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------
    def _tier2_duration(self) -> float:
        if self.config.tier2_duration_s > 0.0:
            return float(self.config.tier2_duration_s)
        return float(self.objective.tier2_duration_s)

    def _tier3_duration(self) -> float:
        if self.config.tier3_duration_s > 0.0:
            return float(self.config.tier3_duration_s)
        return float(self.objective.tier3_duration_s)

    def _environment(self, tier: int, seed: int) -> Dict[str, Any]:
        """The environment of one evaluation.

        The intermediate tier flies in still air (a clean reference), the
        full fidelity tier flies in the turbulent air of the hall: a mean
        wind, gusts and thermals.
        """
        if tier >= 3:
            return as_dict(
                EnvironmentConfig(
                    wind_speed=float(self.config.tier3_wind_speed),
                    gust_intensity=float(self.config.tier3_gust_intensity),
                    thermal_strength=float(self.config.tier3_thermal_strength),
                    turbulence_scale=float(self.config.tier3_turbulence_scale),
                    turbulence_seed=int(seed),
                )
            )
        return as_dict(
            EnvironmentConfig(
                wind_speed=0.0,
                gust_intensity=0.0,
                thermal_strength=0.0,
                turbulence_scale=0.0,
                turbulence_seed=int(seed),
            )
        )

    def _launch_payload(self, throw: ThrowParams, height_m: float) -> Dict[str, Any]:
        """The launch condition of a throw."""
        return as_dict(
            LaunchConfig(
                launch_speed=float(throw.speed_mps),
                launch_angle_deg=float(throw.release_elevation_deg),
                launch_roll_deg=float(throw.roll_deg),
                launch_yaw_deg=float(throw.yaw_deg),
                launch_height_m=float(height_m),
                throw_style="direct",
                throw_speed_source="direct",
                throw_pitch_rate_radps=float(throw.pitch_rate_radps),
            )
        )

    def _geometry_of(self, individual: Individual) -> Any:
        """The folded solid of an individual (folded once and cached)."""
        if individual.geometry is None:
            individual.geometry = fold_genome(
                individual.genome, paper_gsm=float(self.config.paper_gsm)
            )
        return individual.geometry

    def _aircraft_payload(self, individual: Individual) -> Dict[str, Any]:
        """The flight model configuration of an individual.

        The geometry comes from the folded genome, so the planform, the span
        and the inertia of the airframe are the ones of the *actual* folded
        paper and not of a nominal template.
        """
        geometry = self._geometry_of(individual)
        aircraft = _aircraft_from_geometry(geometry, float(self.config.paper_gsm))
        payload = as_dict(aircraft)
        payload["elevon_deg"] = float(individual.genes.get("elevon_angle_deg", 0.0))
        payload["cg_offset_x"] = float(individual.genes.get("cg_offset_x_m", 0.0))
        return payload

    def _jittered_throw(self, throw: ThrowParams, rng: random.Random) -> ThrowParams:
        """The Monte-Carlo jitter of one throw."""
        return ThrowParams(
            speed_mps=min(
                max(
                    throw.speed_mps + rng.gauss(0.0, float(self.config.jitter_speed_mps)),
                    THROW_SPEED_MIN_MPS,
                ),
                THROW_SPEED_MAX_MPS,
            ),
            release_elevation_deg=min(
                max(
                    throw.release_elevation_deg
                    + rng.gauss(0.0, float(self.config.jitter_elevation_deg)),
                    RELEASE_ELEVATION_MIN_DEG,
                ),
                RELEASE_ELEVATION_MAX_DEG,
            ),
            pitch_rate_radps=min(
                max(
                    throw.pitch_rate_radps
                    + rng.gauss(0.0, float(self.config.jitter_pitch_rate_radps)),
                    PITCH_RATE_MIN_RADPS,
                ),
                PITCH_RATE_MAX_RADPS,
            ),
            roll_deg=min(
                max(throw.roll_deg + rng.gauss(0.0, float(self.config.jitter_roll_deg)), ROLL_MIN_DEG),
                ROLL_MAX_DEG,
            ),
            yaw_deg=min(
                max(throw.yaw_deg + rng.gauss(0.0, float(self.config.jitter_yaw_deg)), YAW_MIN_DEG),
                YAW_MAX_DEG,
            ),
        )

    def _evaluate_batch(self, cases: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Run a batch of flight cases through the shared engine.

        Small batches are evaluated in process: the worker pool of the batch
        evaluator costs more than the evaluation itself for a handful of
        cases.
        """
        if not cases:
            return []
        if len(cases) < 4 or self.config.processes == 1:
            return [evaluate_case(case) for case in cases]
        return parallel_evaluate(cases, processes=int(self.config.processes))

    # ------------------------------------------------------------------
    # The tiers
    # ------------------------------------------------------------------
    def _tier0_geometry(self, individuals: Sequence[Individual]) -> List[Individual]:
        """The cheap geometric filter: grip, centre of gravity, self intersection.

        Besides the pass/fail verdict the filter computes the **quality** of
        a surviving design: the smallest ratio of a grip condition to its
        threshold.  It is the merit signal of the cheap tier and it is what
        the promotion to the surrogate tier is ranked by, so the cheap tier
        screens by merit and not by birth order.
        """
        thresholds = self._grip_thresholds()
        survivors: List[Individual] = []
        for individual in individuals:
            self.tier_counts["tier0"] += 1
            try:
                geometry = self._geometry_of(individual)
                report = evaluate_grip(geometry, thresholds)
            except Exception as exc:  # a broken programme is simply not feasible
                individual.feasible = False
                individual.grip_penalty = float(self.config.broken_genome_penalty)
                individual.grip_quality = 0.0
                individual.grip_reasons = [f"fold-failed: {type(exc).__name__}"]
                continue
            individual.feasible = bool(report.valid)
            individual.grip_penalty = float(report.penalty)
            individual.grip_quality = _grip_quality(report, thresholds)
            individual.grip_reasons = list(report.reasons)
            if report.valid:
                survivors.append(individual)
        return survivors

    def _tier1_surrogate(self, individuals: Sequence[Individual]) -> List[Individual]:
        """The low fidelity tier: the analytical gliding-flight surrogate.

        The promotion into this tier is decided by the **merit** of the
        tier-0 survivors, not by the order in which they were born: the
        survivors are ranked by their grip penalty (ascending) and, for the
        designs that pass the filter equally well, by their grip quality
        (descending) before the configured number of them is sliced off.
        """
        limit = min(int(self.config.tier1_trials), len(individuals), self.remaining_trials)
        if limit <= 0:
            return []
        ranked = sorted(
            individuals,
            key=lambda item: (float(item.grip_penalty), -float(item.grip_quality), int(item.index)),
        )
        chosen = ranked[:limit]
        for individual in chosen:
            aircraft = self._aircraft_payload(individual)
            environment = self._environment(1, seed=individual.index)
            launch = self._launch_payload(individual.throw, self.config.launch_height_m)
            result = evaluate_flight_surrogate(
                aircraft,
                environment,
                launch,
                duration_s=self._tier2_duration(),
                dt_max=float(self.config.dt_max),
            )
            individual.result = result
            individual.tier = max(individual.tier, 1)
            individual.metric = self.objective.extract(result)
            individual.metrics = _metric_snapshot(result)
            self.evaluation_count += 1
            self.tier_counts["tier1"] += 1
            self.overall_values.append(individual.metric)
        return chosen

    def _tier2_reference(self, individuals: Sequence[Individual]) -> List[Individual]:
        """The intermediate tier: a short flight of the reference 6-DoF model."""
        limit = min(int(self.config.tier2_trials), len(individuals), self.remaining_trials)
        if limit <= 0:
            return []
        ranked = sorted(individuals, key=lambda item: item.metric, reverse=True)
        chosen = ranked[:limit]
        cases: List[Dict[str, Any]] = []
        for individual in chosen:
            aircraft = self._aircraft_payload(individual)
            environment = self._environment(2, seed=individual.index)
            launch = self._launch_payload(individual.throw, self.config.launch_height_m)
            cases.append(
                {
                    "aircraft": aircraft,
                    "environment": environment,
                    "launch": launch,
                    "duration_s": self._tier2_duration(),
                    "dt_max": float(self.config.dt_max),
                }
            )
        results = self._evaluate_batch(cases)
        for individual, result in zip(chosen, results):
            individual.result = result
            individual.tier = max(individual.tier, 2)
            individual.metric = self.objective.extract(result)
            individual.metrics = _metric_snapshot(result)
            self.evaluation_count += 1
            self.tier_counts["tier2"] += 1
            self.overall_values.append(individual.metric)
        return chosen

    def _tier3_full(self, individuals: Sequence[Individual]) -> List[Individual]:
        """The full fidelity tier: the Monte-Carlo ensemble of the 6-DoF model.

        Every individual that is promoted into this tier is evaluated
        ``tier3_monte_carlo_samples`` times.  The samples differ in their
        throw jitter and in their turbulence seed, and both are derived
        deterministically from the individual and the index of the sample,
        so a run is reproducible.

        The metric of the individual is the **mean of the per sample
        metrics**: the tier reports how well a design flies on average, not
        how well its luckiest throw flew.  The per sample structural
        failure flags are kept on the individual, so a design that only
        survives one of its three throws is counted as a failure and never
        enters the hall of fame.

        The budget is honest: every flight of the ensemble is counted in
        ``evaluation_count`` and in the tier-3 count, and an individual is
        only promoted when its **whole** ensemble fits into the remaining
        budget, so a run never silently exceeds ``max_trials``.
        """
        samples = max(int(self.config.tier3_monte_carlo_samples), 1)
        affordable = self.remaining_trials // samples
        limit = min(int(self.config.tier3_trials), len(individuals), affordable)
        if limit <= 0:
            return []
        ranked = sorted(individuals, key=lambda item: item.metric, reverse=True)
        chosen = ranked[:limit]
        cases: List[Dict[str, Any]] = []
        for individual in chosen:
            aircraft = self._aircraft_payload(individual)
            for sample in range(samples):
                rng = random.Random(self._jitter_seed(individual, sample))
                throw = self._jittered_throw(individual.throw, rng)
                environment = self._environment(3, seed=self._jitter_seed(individual, sample))
                launch = self._launch_payload(throw, self.config.launch_height_m)
                cases.append(
                    {
                        "aircraft": aircraft,
                        "environment": environment,
                        "launch": launch,
                        "duration_s": self._tier3_duration(),
                        "dt_max": float(self.config.dt_max),
                    }
                )
        results = self._evaluate_batch(cases)
        allowable = self._allowable_pa()
        for position, individual in enumerate(chosen):
            sample_results = results[position * samples : (position + 1) * samples]
            if not sample_results:
                continue
            metrics = [self.objective.extract(result) for result in sample_results]
            individual.result = sample_results[0]
            individual.tier = max(individual.tier, 3)
            individual.metric = sum(metrics) / len(metrics)
            individual.metrics = _metric_average(sample_results)
            individual.sample_failures = [
                is_failed_evaluation(result, allowable) for result in sample_results
            ]
            self.evaluation_count += len(sample_results)
            self.tier_counts["tier3"] += len(sample_results)
            self.overall_values.extend(metrics)
        return chosen

    def _jitter_seed(self, individual: Individual, sample: int = 0) -> int:
        """The deterministic seed of the Monte-Carlo jitter of one sample.

        The seed depends on the individual, on the generation and on the
        index of the sample, so every sample of every individual has its own
        reproducible turbulence and its own reproducible throw jitter.
        """
        return int(
            (
                self.config.seed * 999_983
                + individual.index * 7919
                + self.generation * 104_729
                + int(sample) * 15_485_863
            )
            % (2 ** 31 - 1)
        )

    # ------------------------------------------------------------------
    # Fitness
    # ------------------------------------------------------------------
    def _individual_fitness(self, individual: Individual) -> float:
        """The fitness of one individual.

        The primary term is the objective metric normalised by the reference
        target of the active objective.  The meta controller may add
        secondary terms (derived from the sensitivity analysis); they are
        weighted explicitly and never replace the primary term.

        A flight that **failed** is infeasible.  Its metric is ``0.0`` --
        exactly the metric of an aeroplane that flew nowhere -- so scoring it
        like a legitimate flight would let a broken evaluation win every
        comparison and end up as the published best individual.  An
        infeasible individual therefore receives the shared
        ``INFEASIBLE_SCORE`` of the simulator (:mod:`physics.optimizer_domain`),
        loses every comparison, never enters the hall of fame and is never
        selected.
        """
        if self._is_infeasible(individual):
            return float(INFEASIBLE_SCORE)
        reference = float(self.objective.reference_target) or 1.0
        fitness = individual.metric / reference
        for key, weight in self.secondary_weights.items():
            if weight <= 0.0:
                continue
            secondary_reference = float(self.objective.secondary_references.get(key, 1.0)) or 1.0
            value = individual.metrics.get(key)
            if value is None:
                continue
            fitness += weight * (float(value) / secondary_reference)
        if not individual.feasible:
            fitness -= 1.0 + float(self.config.grip_penalty_weight) * float(
                individual.grip_penalty
            )
        if not math.isfinite(fitness):
            return float("-inf")
        return fitness

    def _refresh_fitness(self, individuals: Sequence[Individual]) -> None:
        for individual in individuals:
            individual.fitness = self._individual_fitness(individual)

    # ------------------------------------------------------------------
    # One generation
    # ------------------------------------------------------------------
    def _elite_count(self, candidates: Sequence[Individual], aggregate_fitness: float) -> int:
        """The number of elites the island keeps in its next population.

        The size of the elite block is a **population level** decision, and
        it is the decision the selected statistic makes: the block is the
        group of candidates that are at least as good as the selected
        aggregate, i.e. the group that carries the statistic.

        * ``best`` -- the aggregate is the peak of the full fidelity
          population, so only the peak is an elite: the island rebuilds its
          population around the best individual it has found (exploitation).
        * ``mean`` -- the aggregate is the mean, so every candidate that is
          at least as good as the mean survives: the island keeps a broadly
          good population.
        * ``median`` -- the aggregate is the median, so every candidate that
          is at least as good as the median survives: the island keeps a
          robust population.

        The block never fills the whole population (at least one slot is
        always left for new blood), so the search can never freeze.
        """
        size = max(int(self.config.population_size), 2)
        ceiling = max(1, int(round(float(self.config.stat_elite_max_fraction) * size)))
        ceiling = min(ceiling, size - 1)
        survivors = 0
        for individual in candidates:
            if individual.fitness >= aggregate_fitness:
                survivors += 1
        return max(1, min(survivors, ceiling))

    def _update_hall_of_fame(self, candidates: Sequence[Individual]) -> None:
        """Merge the candidates into the hall of fame of the whole run.

        The hall of fame holds the best ``top_k`` individuals of the **whole
        run**: the new candidates are merged with the archive and the best
        are kept, with a deterministic tie break (the fitness first, the
        index second).  An earlier version of the engine stopped filling
        the archive as soon as it held ``top_k`` entries, so the archive
        stayed frozen at the first generation and the run published a much
        worse individual than the one it had actually found.

        A failed evaluation never enters the archive: an individual whose
        flight could not be evaluated is infeasible and must never be
        published as the best aeroplane of the research programme.
        """
        merged: Dict[int, Individual] = {
            item.index: item for item in self.hall_of_fame
        }
        for item in candidates:
            if self._is_infeasible(item):
                continue
            merged[item.index] = item
        self.hall_of_fame = sorted(
            merged.values(),
            key=lambda item: (item.fitness, -int(item.index)),
            reverse=True,
        )[: self.config.top_k]

    def _run_generation(self, island: Island) -> Dict[str, Any]:
        """Run one generation of one island.

        The **population level selection** is driven by the selected
        population statistic (see :meth:`_elite_count`): the statistic
        decides how many parents survive into the next population, so
        ``mean`` keeps a broadly good population, ``median`` keeps a robust
        one and ``best`` concentrates on the peak.  The per individual
        fitness is still what the CMA-ES ranks its samples with, because
        the strategy needs one score per sample.
        """
        previous_best = island.best_fitness
        offspring, cma_indices = self._make_offspring(island)
        candidates = list(island.population) + list(offspring)
        survivors = self._tier0_geometry(offspring)
        tier1 = self._tier1_surrogate(survivors)
        tier2 = self._tier2_reference(tier1)
        tier3 = self._tier3_full(tier2)

        self._refresh_fitness(candidates)

        # The CMA-ES learns from the individuals it sampled.
        scores: List[float] = []
        for position in range(len(offspring)):
            individual = offspring[position]
            score = individual.fitness
            if not math.isfinite(score):
                score = -1.0e6
            scores.append(score)
        if cma_indices:
            island.cma.tell(scores[: max(cma_indices) + 1])

        # The statistics of the full fidelity population of this island and
        # the aggregate the selected statistic mode picks out of them.
        tier3_values = [item.metric for item in tier3]
        allowable = self._allowable_pa()
        failures = [individual_structural_failure(item, allowable) for item in tier3]
        statistics = compute_population_statistics(
            tier3_values, self.overall_values, failures
        )
        aggregate = select_statistic(statistics, self.config.stat_mode, self.stat_weights)
        aggregate_fitness = aggregate / (float(self.objective.reference_target) or 1.0)

        # Selection: the island keeps the elite block the statistic selected
        # and fills the rest of the population with the best of the new
        # offspring.
        ranked = sorted(candidates, key=lambda item: (item.fitness, item.index), reverse=True)
        elite_count = self._elite_count(ranked, aggregate_fitness)
        elites = ranked[:elite_count]
        elite_ids = {id(item) for item in elites}
        newcomers = [item for item in offspring if id(item) not in elite_ids]
        island.population = (elites + newcomers)[: self.config.population_size]
        island.diversity = _population_diversity(island.population)

        # The hall of fame keeps the best of the whole run.
        self._update_hall_of_fame(ranked)

        if island.population:
            best = island.population[0]
            if best.fitness > island.best_fitness:
                island.best_fitness = best.fitness
                island.best_metric = best.metric
        island.improved = island.best_fitness > previous_best + 1.0e-9

        report = {
            "generation": int(self.generation),
            "island": int(island.id),
            "candidates": len(candidates),
            "survivors": len(survivors),
            "tier1": len(tier1),
            "tier2": len(tier2),
            "tier3": len(tier3),
            "best_fitness": float(island.best_fitness) if math.isfinite(island.best_fitness) else None,
            "aggregate": float(aggregate),
            "aggregate_fitness": float(aggregate_fitness),
            "elite_count": int(elite_count),
            "diversity": float(island.diversity),
            "statistics": statistics,
            "evaluations": int(self.evaluation_count),
            "max_fold_steps": int(island.max_fold_steps),
            "micro_flaps": bool(island.micro_flaps),
        }
        island.history.append(report)
        return report

    def _allowable_pa(self) -> float:
        """The shared allowable stress of the paper stock of the run.

        The value comes from :func:`physics.flight_sim.allowable_stress_pa`
        (which reads :func:`physics.stress_domain.paper_allowable_stress`),
        i.e. from the single shared standard of the whole simulator; the
        engine never re-derives a second allowable.  The bending stiffness
        of the stock is the default of the flight model
        (:class:`physics.config_schema.AircraftConfig`), so the airframe
        used here is the one the simulator itself uses.
        """
        cached = getattr(self, "_allowable_cache", None)
        if cached is not None:
            return float(cached)
        from .flight_sim import allowable_stress_pa

        aircraft = AircraftConfig(paper_gsm=float(self.config.paper_gsm))
        value = float(allowable_stress_pa(aircraft))
        self._allowable_cache = value
        return value

    def _is_infeasible(self, individual: Individual) -> bool:
        """Did the evaluation of this individual fail?

        The rule is the shared contract of the simulator
        (:func:`physics.optimizer_domain.is_infeasible_flight`) together with
        the rule of the engine
        (:func:`physics.ga_domain.is_structural_failure`); no third rule is
        invented and no second allowable stress is derived.
        """
        return individual_structural_failure(individual, self._allowable_pa())

    # ------------------------------------------------------------------
    # Meta control
    # ------------------------------------------------------------------
    def _check_stagnation(self, island: Island) -> Optional[Dict[str, Any]]:
        """Detect the stagnation of an island and trigger the structure shift.

        An island stagnates when a generation did not improve its best
        fitness.  The counter is reset by every improvement, so the shift
        fires after ``stagnation_generations`` generations in a row without
        progress.
        """
        if island.improved:
            island.stagnation_counter = 0
        else:
            island.stagnation_counter += 1
        if island.stagnation_counter < int(self.config.stagnation_generations):
            return None
        island.stagnation_counter = 0
        return self._structure_search_shift(island)

    def _structure_search_shift(self, island: Island) -> Dict[str, Any]:
        """Change the *search* of one island when it stopped improving.

        Three levers are available and every one of them is recorded in the
        decision journal:

        * more fold steps in the genome,
        * micro adjustment flaps (winglets) in the genome representation,
        * a re-weighting of the objective derived from a sensitivity analysis
          over the full fidelity population.

        The first two levers belong to the **island** that stagnated: they
        are stored on the island and read from there when the next
        generation of that island is built.  They never touch the shared
        configuration, so a shift that the journal attributes to island 1
        does not silently change the search of island 0.
        """
        trigger = (
            f"stagnation: no fitness improvement for "
            f"{int(self.config.stagnation_generations)} generations"
        )
        changes: Dict[str, Any] = {}

        if int(island.max_fold_steps) < int(self.config.max_fold_steps_limit):
            island.max_fold_steps += 1
            changes["max_fold_steps"] = int(island.max_fold_steps)
            self.journal.record(
                self.generation,
                island.id,
                "structure_search_shift",
                trigger,
                changes,
            )

        if not island.micro_flaps:
            island.micro_flaps = True
            self.journal.record(
                self.generation,
                island.id,
                "structure_search_shift",
                trigger,
                {"micro_flaps": True},
            )

        weights = self._sensitivity_weights(island)
        if weights:
            previous = dict(self.secondary_weights)
            self.secondary_weights = weights
            self.journal.record(
                self.generation,
                island.id,
                "objective_reweight",
                trigger,
                {"secondary_weights": dict(weights), "previous": previous},
            )
            self._refresh_fitness(self.hall_of_fame)
            for other in self.islands:
                self._refresh_fitness(other.population)

        return {
            "generation": int(self.generation),
            "island": int(island.id),
            "trigger": trigger,
            "changes": changes,
            "max_fold_steps": int(island.max_fold_steps),
            "micro_flaps": bool(island.micro_flaps),
            "secondary_weights": dict(self.secondary_weights),
        }

    def _sensitivity_weights(self, island: Island) -> Dict[str, float]:
        """The objective re-weighting derived from a sensitivity analysis.

        The correlation between the objective metric and every secondary
        metric of the objective is measured over the full fidelity population
        of the island.  A secondary metric that correlates with the objective
        carries information the objective alone does not, so it is given a
        weight proportional to its correlation.  The total secondary weight
        is capped by ``max_secondary_weight`` so the primary objective always
        dominates.
        """
        population = [item for item in island.population if item.tier >= 3 and item.metrics]
        if len(population) < 3:
            return {}
        primary = [float(item.metric) for item in population]
        weights: Dict[str, float] = {}
        best = 0.0
        for key in self.objective.secondary_metric_keys:
            values = []
            for item in population:
                value = item.metrics.get(key)
                if value is None:
                    break
                values.append(float(value))
            if len(values) != len(population):
                continue
            correlation = abs(_pearson(primary, values))
            if correlation < float(self.config.sensitivity_threshold):
                continue
            weights[key] = correlation
            best = max(best, correlation)
        if not weights or best <= 0.0:
            return {}
        cap = float(self.config.max_secondary_weight)
        return {key: cap * value / best for key, value in weights.items()}

    # ------------------------------------------------------------------
    # Migration
    # ------------------------------------------------------------------
    def _migrate(self) -> Dict[str, Any]:
        """Migrate the elites between the islands."""
        if len(self.islands) < 2:
            return {"migrated": 0, "edges": []}
        interval = int(self.config.migration_interval)
        if self.generation % interval != 0:
            return {"migrated": 0, "edges": []}
        count = int(self.config.migration_elites)
        if count <= 0:
            return {"migrated": 0, "edges": []}

        edges: List[Dict[str, Any]] = []
        migrated = 0
        for index, island in enumerate(self.islands):
            receiver = self.islands[(index + 1) % len(self.islands)]
            ranked = sorted(island.population, key=lambda item: item.fitness, reverse=True)
            migrants = ranked[:count]
            if not migrants:
                continue
            receiver_ranked = sorted(
                receiver.population, key=lambda item: item.fitness, reverse=True
            )
            known = {id(item) for item in receiver_ranked}
            fresh = [migrant for migrant in migrants if id(migrant) not in known]
            kept = receiver_ranked[: max(len(receiver_ranked) - len(fresh), 0)]
            merged = kept + fresh
            merged.sort(key=lambda item: (item.fitness, item.index), reverse=True)
            receiver.population = merged[: self.config.population_size]
            receiver.diversity = _population_diversity(receiver.population)
            migrated += len(migrants)
            edges.append(
                {
                    "from": int(island.id),
                    "to": int(receiver.id),
                    "elites": len(migrants),
                    "best_fitness": float(migrants[0].fitness)
                    if math.isfinite(migrants[0].fitness)
                    else None,
                }
            )
        if edges:
            self.journal.record(
                self.generation,
                None,
                "migration",
                f"scheduled island migration every {interval} generations",
                {"edges": edges, "migrated": migrated},
            )
        return {"migrated": migrated, "edges": edges}

    # ------------------------------------------------------------------
    # Checkpoints
    # ------------------------------------------------------------------
    def _checkpoint_payload(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "run_id": self.run_id,
            "fingerprint": self.fingerprint,
            "generation": int(self.generation),
            "created_at": utc_now_iso(),
            "config": self.config.to_dict(),
            "overrides": dict(self.overrides),
            "evaluation_count": int(self.evaluation_count),
            "tier_counts": dict(self.tier_counts),
            "rng_seed": int(self.config.seed),
            "objective_weights": dict(self.secondary_weights),
            "stat_weights": dict(self.stat_weights),
            "generation0_feasible_fraction": float(self.generation0_feasible_fraction),
            "overall_values": [float(value) for value in self.overall_values],
            "islands": [island.to_dict() for island in self.islands],
            "hall_of_fame": [individual.to_dict() for individual in self.hall_of_fame],
            "history": list(self.history),
            "journal_entries": int(self.journal.count),
        }

    def _save_checkpoint(self) -> str:
        """Write the checkpoint of the current state and return its path."""
        directory = self.config.resolved_checkpoint_dir()
        os.makedirs(directory, exist_ok=True)
        payload = self._checkpoint_payload()
        name = f"checkpoint_{int(self.generation):05d}.json"
        path = os.path.join(directory, name)
        _write_json(path, payload)
        _write_json(os.path.join(directory, "latest.json"), payload)
        _write_json(
            os.path.join(directory, "hall_of_fame.json"),
            [individual.to_dict() for individual in self.hall_of_fame],
        )
        _write_json(os.path.join(directory, "history.json"), list(self.history))
        return path

    def _load_checkpoint(self, path: str) -> Dict[str, Any]:
        """Restore the state of a checkpoint."""
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.generation = int(payload.get("generation", 0))
        self.evaluation_count = int(payload.get("evaluation_count", 0))
        self.tier_counts.update(payload.get("tier_counts") or {})
        self.overall_values = [float(value) for value in payload.get("overall_values") or []]
        self.secondary_weights = {
            str(key): float(value)
            for key, value in (payload.get("objective_weights") or {}).items()
        }
        self.stat_weights = {
            str(key): float(value)
            for key, value in (payload.get("stat_weights") or {}).items()
        }
        self.generation0_feasible_fraction = float(
            payload.get("generation0_feasible_fraction", 0.0)
        )
        self.history = list(payload.get("history") or [])
        self.hall_of_fame = [
            Individual.from_dict(item) for item in payload.get("hall_of_fame") or []
        ]
        self.islands = [Island.from_dict(item) for item in payload.get("islands") or []]
        # The meta state of an island (the structure it searches in) is part
        # of the checkpoint.  A checkpoint written before the meta state was
        # per island falls back to the configuration of the run.
        for island in self.islands:
            if int(island.max_fold_steps) <= 0:
                island.max_fold_steps = int(self.config.max_fold_steps)
        self._individual_counter = max(
            [individual.index for individual in self.hall_of_fame]
            + [individual.index for island in self.islands for individual in island.population]
            + [0]
        )
        return payload

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------
    def _best_individual(self) -> Optional[Individual]:
        """The best individual of the run.

        The hall of fame is the archive of the best individuals of the whole
        run, so its first entry is the best one.  A failed evaluation is
        infeasible and never enters the archive, so an infeasible individual
        can never be published as the best aeroplane of the run.
        """
        if self.hall_of_fame:
            return self.hall_of_fame[0]
        best: Optional[Individual] = None
        for island in self.islands:
            for individual in island.population:
                if self._is_infeasible(individual):
                    continue
                if best is None or individual.fitness > best.fitness:
                    best = individual
        return best

    def _write_artifacts(self, best: Optional[Individual]) -> Dict[str, Any]:
        """Write the artifacts of the best individual and return their paths."""
        directory = self.config.out_dir
        os.makedirs(directory, exist_ok=True)
        paths: Dict[str, Any] = {}
        if best is None:
            return paths

        from .origami_genome import (
            crease_pattern_svg,
            recipe_markdown,
            recipe_steps,
            step_meshes,
        )

        geometry = self._geometry_of(best) if best.geometry is None else best.geometry
        steps = recipe_steps(best.genome, geometry)
        markdown = recipe_markdown(best.genome, geometry)
        svg = crease_pattern_svg(best.genome)
        meshes = step_meshes(best.genome)

        recipe_path = os.path.join(directory, "best_recipe.json")
        _write_json(recipe_path, {"steps": steps})
        paths["recipe"] = recipe_path

        markdown_path = os.path.join(directory, "best_recipe.md")
        with open(markdown_path, "w", encoding="utf-8") as handle:
            handle.write(markdown)
        paths["recipe_markdown"] = markdown_path

        svg_path = os.path.join(directory, "best_crease_pattern.svg")
        with open(svg_path, "w", encoding="utf-8") as handle:
            handle.write(svg)
        paths["crease_pattern_svg"] = svg_path

        meshes_path = os.path.join(directory, "best_step_meshes.json")
        _write_json(meshes_path, {"states": meshes})
        paths["step_meshes"] = meshes_path

        throw_path = os.path.join(directory, "best_throw_params.json")
        _write_json(throw_path, best.throw.to_dict())
        paths["throw_params"] = throw_path

        statistics = self._final_statistics()
        statistics_path = os.path.join(directory, "best_statistics.json")
        _write_json(statistics_path, statistics)
        paths["statistics"] = statistics_path

        payload = self._result_payload(best)
        payload_path = os.path.join(directory, "best_individual.json")
        _write_json(payload_path, payload)
        paths["individual"] = payload_path
        return paths

    def _final_statistics(self) -> Dict[str, Any]:
        """The statistics of the full fidelity population of the whole run."""
        allowable = self._allowable_pa()
        tier3_values: List[float] = []
        failures: List[bool] = []
        for island in self.islands:
            for individual in island.population:
                if individual.tier >= 3:
                    tier3_values.append(float(individual.metric))
                    failures.append(individual_structural_failure(individual, allowable))
        statistics = compute_population_statistics(
            tier3_values, self.overall_values, failures
        )
        statistics["stat_mode"] = self.config.stat_mode
        statistics["stat_weights"] = dict(self.stat_weights)
        statistics["selected"] = select_statistic(
            statistics, self.config.stat_mode, self.stat_weights
        )
        statistics["objective"] = self.objective.key
        statistics["reference_target"] = float(self.objective.reference_target)
        return statistics

    def _result_payload(self, best: Optional[Individual]) -> Dict[str, Any]:
        """The complete payload of the best individual."""
        if best is None:
            return {
                "run_id": self.run_id,
                "objective": self.objective.key,
                "fitness": None,
                "stats": self._final_statistics(),
            }
        from .origami_genome import (
            crease_pattern_svg,
            recipe_markdown,
            recipe_steps,
            step_meshes,
        )

        geometry = self._geometry_of(best)
        return {
            "run_id": self.run_id,
            "objective": self.objective.key,
            "fingerprint": self.fingerprint,
            "genome": best.genome.to_dict(),
            "genes": {str(key): float(value) for key, value in best.genes.items()},
            "recipe": {
                "steps": recipe_steps(best.genome, geometry),
                "markdown": recipe_markdown(best.genome, geometry),
            },
            "crease_pattern_svg": crease_pattern_svg(best.genome),
            "step_meshes": step_meshes(best.genome),
            "throw_params": best.throw.to_dict(),
            "stats": self._final_statistics(),
            "fitness": float(best.fitness) if math.isfinite(best.fitness) else None,
            "metric": float(best.metric),
            "tier": int(best.tier),
            "flight_result": {
                key: value
                for key, value in best.result.items()
                if isinstance(value, (int, float, str, bool, type(None)))
            },
        }

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        """The progress of the run (used by the API)."""
        return {
            "run_id": self.run_id,
            "status": "completed" if self.generation >= self.config.generations else "running",
            "objective": self.objective.key,
            "generation": int(self.generation),
            "generations": int(self.config.generations),
            "evaluations": int(self.evaluation_count),
            "best_fitness": float(self.hall_of_fame[0].fitness)
            if self.hall_of_fame and math.isfinite(self.hall_of_fame[0].fitness)
            else None,
            "islands": [
                {
                    "id": int(island.id),
                    "size": len(island.population),
                    "best": float(island.best_fitness)
                    if math.isfinite(island.best_fitness)
                    else None,
                    "diversity": float(island.diversity),
                    "max_fold_steps": int(island.max_fold_steps),
                    "micro_flaps": bool(island.micro_flaps),
                }
                for island in self.islands
            ],
            "tier": _highest_tier(self.tier_counts),
            "elapsed_s": float(time.time() - self.started_at),
        }

    # ------------------------------------------------------------------
    # The live progress
    # ------------------------------------------------------------------
    def _update_best_fitness(self) -> None:
        """Fold the results of the last generation into the best fitness.

        The value is a running maximum, so the curve of the generations is
        monotone: every generation reports the best fitness found *up to and
        including* that generation.
        """
        current = float("-inf")
        for island in self.islands:
            if island.best_fitness > current:
                current = float(island.best_fitness)
        if self.hall_of_fame and self.hall_of_fame[0].fitness > current:
            current = float(self.hall_of_fame[0].fitness)
        if current > self._best_fitness_so_far:
            self._best_fitness_so_far = current

    def _best_fitness_value(self) -> Optional[float]:
        """The best fitness found so far (``None`` while there is none)."""
        if math.isfinite(self._best_fitness_so_far):
            return float(self._best_fitness_so_far)
        return None

    def progress_snapshot(self) -> Dict[str, Any]:
        """The live progress of the run.

        The payload is the one the API publishes while the run is in flight.
        It carries the number of the completed generations, the number of the
        evaluations, the best fitness found so far, the state of every island
        and the per generation curve.

        The curve is *compact*: one ``{"generation": int, "best_fitness":
        float | None}`` entry per generation, and only the last
        :data:`PROGRESS_HISTORY_LIMIT` entries, so a run of thousands of
        generations still answers with a small payload.
        """
        status = self.status()
        return {
            "run_id": str(status["run_id"]),
            "status": str(status["status"]),
            "objective": str(status["objective"]),
            "generation": int(self.generation),
            "generations": int(self.config.generations),
            "evaluations": int(self.evaluation_count),
            "best_fitness": self._best_fitness_value(),
            "islands": list(status["islands"]),
            "tier": _highest_tier(self.tier_counts),
            "elapsed_s": float(status["elapsed_s"]),
            "history": list(self._progress_history[-self.config.progress_history_limit :]),
        }

    def _emit_progress(self) -> None:
        """Publish the live progress to the listener of the run.

        A listener that raises must never take the research run down, so the
        exception of the listener is swallowed.
        """
        callback = self.progress_callback
        if callback is None:
            return
        try:
            callback(self.progress_snapshot())
        except Exception:
            return

    # ------------------------------------------------------------------
    # The run
    # ------------------------------------------------------------------
    def run(self) -> Dict[str, Any]:
        """Run the research programme and return its summary."""
        self._prepare_output()
        resumed = False
        if self.config.resume_from:
            self._load_checkpoint(self.config.resume_from)
            resumed = True
            self.journal.record(
                self.generation,
                None,
                "resume",
                f"resumed from checkpoint {self.config.resume_from}",
                {
                    "overrides": dict(self.overrides),
                    "max_trials": int(self.config.max_trials),
                    "top_k": int(self.config.top_k),
                    "evaluation_count": int(self.evaluation_count),
                    "generation": int(self.generation),
                },
            )
        else:
            self._build_islands()
            self.journal.record(
                0,
                None,
                "run_start",
                "research run started",
                {
                    "objective": self.objective.key,
                    "max_trials": int(self.config.max_trials),
                    "top_k": int(self.config.top_k),
                    "tier1_trials": int(self.config.tier1_trials),
                    "tier2_trials": int(self.config.tier2_trials),
                    "tier3_trials": int(self.config.tier3_trials),
                    "generations": int(self.config.generations),
                    "islands": int(self.config.islands),
                    "stat_mode": self.config.stat_mode,
                    "seed": int(self.config.seed),
                },
            )

        start_generation = self.generation
        status = "completed"
        for generation in range(start_generation, int(self.config.generations)):
            if self.budget_exhausted:
                status = "budget_exhausted"
                break
            self.generation = generation
            reports: List[Dict[str, Any]] = []
            for island in self.islands:
                report = self._run_generation(island)
                reports.append(report)
                if generation == 0 and island.id == 0:
                    self.generation0_feasible_fraction = (
                        float(report["survivors"]) / float(max(report["candidates"], 1))
                    )
                self._check_stagnation(island)
            migration = self._migrate()
            self.history.append(
                {
                    "generation": int(generation),
                    "reports": reports,
                    "migration": migration,
                    "evaluations": int(self.evaluation_count),
                    # The structure is a property of every island, so the
                    # history reports it per island and not once for the
                    # whole run.
                    "island_structure": [
                        {
                            "id": int(island.id),
                            "max_fold_steps": int(island.max_fold_steps),
                            "micro_flaps": bool(island.micro_flaps),
                        }
                        for island in self.islands
                    ],
                }
            )
            # The generation is over: the live progress of the run is
            # published here, so a monitor can draw the curve of the run
            # while it is still going.
            self._update_best_fitness()
            self._progress_history.append(
                {
                    "generation": int(self.generation),
                    "best_fitness": self._best_fitness_value(),
                }
            )
            self._emit_progress()
            interval = max(int(self.config.checkpoint_interval), 1)
            if generation % interval == 0:
                self._save_checkpoint()

        self.generation = max(self.generation, start_generation)
        final_checkpoint = self._save_checkpoint()
        best = self._best_individual()
        artifacts = self._write_artifacts(best)
        summary = self._summary(status, best, artifacts, final_checkpoint, resumed)
        self.journal.record(
            self.generation,
            None,
            "run_end",
            "research run finished",
            {
                "status": status,
                "evaluations": int(self.evaluation_count),
                "best_fitness": summary.get("best_fitness"),
                "objective": self.objective.key,
            },
        )
        summary["journal_entries"] = int(self.journal.count)
        return summary

    def _summary(
        self,
        status: str,
        best: Optional[Individual],
        artifacts: Dict[str, Any],
        checkpoint: str,
        resumed: bool,
    ) -> Dict[str, Any]:
        statistics = self._final_statistics()
        promotion = {
            "tier0_evaluated": int(self.tier_counts["tier0"]),
            "tier1_evaluated": int(self.tier_counts["tier1"]),
            "tier2_evaluated": int(self.tier_counts["tier2"]),
            "tier3_evaluated": int(self.tier_counts["tier3"]),
            "promotion_ratio_tier1": _ratio(
                self.tier_counts["tier1"], self.tier_counts["tier0"]
            ),
            "promotion_ratio_tier2": _ratio(
                self.tier_counts["tier2"], self.tier_counts["tier1"]
            ),
            "promotion_ratio_tier3": _ratio(
                self.tier_counts["tier3"], self.tier_counts["tier2"]
            ),
        }
        return {
            "run_id": self.run_id,
            "fingerprint": self.fingerprint,
            "status": status,
            "objective": self.objective.key,
            "objective_detail": self.objective.to_dict(),
            "resumed": bool(resumed),
            "config": self.config.to_dict(),
            "overrides": dict(self.overrides),
            "generations": int(self.generation),
            "evaluations": int(self.evaluation_count),
            "tier_counts": dict(self.tier_counts),
            "promotion": promotion,
            "best_fitness": float(best.fitness)
            if best is not None and math.isfinite(best.fitness)
            else None,
            "best_metric": float(best.metric) if best is not None else None,
            "best_tier": int(best.tier) if best is not None else 0,
            "stats": statistics,
            "islands": [
                {
                    "id": int(island.id),
                    "size": len(island.population),
                    "best": float(island.best_fitness)
                    if math.isfinite(island.best_fitness)
                    else None,
                    "diversity": float(island.diversity),
                    "max_fold_steps": int(island.max_fold_steps),
                    "micro_flaps": bool(island.micro_flaps),
                }
                for island in self.islands
            ],
            "generation0_feasible_fraction": float(self.generation0_feasible_fraction),
            "artifacts": artifacts,
            "checkpoint": checkpoint,
            "journal_entries": int(self.journal.count),
            "elapsed_s": float(time.time() - self.started_at),
            "result": self._result_payload(best),
        }


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _aircraft_from_geometry(geometry: Any, paper_gsm: float) -> AircraftConfig:
    """The flight model configuration of a folded genome."""
    from .origami_genome import genome_to_aircraft_config

    return genome_to_aircraft_config(geometry, paper_gsm=float(paper_gsm))


def _metric_snapshot(result: Dict[str, Any]) -> Dict[str, float]:
    """The numeric diagnostics of one flight result."""
    keys = (
        "distance_m",
        "flight_time_s",
        "max_altitude_m",
        "max_ld_ratio",
        "max_stress_index",
        "max_stress_pa",
        "stall_margin",
        "stability",
        "landing_distance_m",
    )
    snapshot: Dict[str, float] = {}
    for key in keys:
        try:
            value = float(result.get(key, 0.0))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            snapshot[key] = value
    return snapshot


def _metric_average(results: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    """The mean of the numeric diagnostics of several flight results.

    It is the diagnostic counterpart of the aggregated metric of a
    Monte-Carlo ensemble: the value of a key is the mean over the samples
    that carry it.
    """
    totals: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    for result in results:
        for key, value in _metric_snapshot(result).items():
            totals[key] = totals.get(key, 0.0) + float(value)
            counts[key] = counts.get(key, 0) + 1
    return {key: totals[key] / counts[key] for key in totals if counts.get(key, 0) > 0}


def _grip_quality(report: Any, thresholds: Any) -> float:
    """How comfortably a design can be pinched and thrown.

    The value is the **smallest relative margin** over the three grip
    conditions (the height of the grip, its length and the number of paper
    layers that cover it), scaled by the *balance* of the design: a sheet
    whose centre of gravity sits in the middle of the grip is balanced on
    the hand, a sheet whose centre of gravity is at the edge of the grip is
    not.

    A design that just passes the filter has a quality of about ``1``, a
    design that passes it with a large margin has a quality well above
    ``1``, and a design without any grip has the quality ``0``.

    The grip *penalty* of a design that passes the filter is always zero
    (the penalty is the sum of the failed conditions), so the penalty
    alone cannot rank the survivors of the cheap tier; the quality is the
    merit signal that does.
    """
    height = float(getattr(report, "grip_height_m", 0.0) or 0.0)
    length = float(getattr(report, "grip_length_m", 0.0) or 0.0)
    layers = int(getattr(report, "grip_layers", 0) or 0)
    offset = float(getattr(report, "cg_offset_m", 0.0) or 0.0)
    ratios: List[float] = []
    if height > 0.0:
        ratios.append(height / max(float(getattr(thresholds, "min_grip_height_m", 0.0)), 1.0e-9))
    if length > 0.0:
        ratios.append(
            length / max(float(getattr(thresholds, "min_grip_length_m", 0.0)), 1.0e-9)
        )
    if layers > 0:
        ratios.append(layers / max(float(getattr(thresholds, "min_grip_layers", 1)), 1.0))
    if not ratios:
        return 0.0
    margin = float(min(ratios))
    reach = 0.5 * length + max(float(getattr(thresholds, "cg_tolerance_m", 0.0)), 0.0)
    balance = 1.0 - (offset / reach) if reach > 0.0 else 0.0
    balance = min(max(balance, 0.0), 1.0)
    return float(margin * balance)


def _population_diversity(population: Sequence[Individual]) -> float:
    """The genetic diversity of a population.

    It is the mean distance of the individuals from the centre of the
    population in the *normalised* gene space, divided by the square root of
    the dimension: ``0`` means "every individual is identical", a value of
    ``1`` means "the individuals are spread over the whole box".
    """
    if len(population) < 2:
        return 0.0
    vectors = [normalise_genes(individual.genes) for individual in population]
    dimension = float(len(vectors[0]))
    means = [sum(vector[index] for vector in vectors) / len(vectors) for index in range(int(dimension))]
    total = 0.0
    for vector in vectors:
        total += math.sqrt(
            sum((vector[index] - means[index]) ** 2 for index in range(int(dimension)))
        )
    return float(total / len(vectors) / math.sqrt(dimension))


def _ratio(numerator: int, denominator: int) -> float:
    """A safe ratio."""
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _highest_tier(tier_counts: Dict[str, int]) -> int:
    """The highest tier the run has reached.

    A tier counts as reached as soon as one individual was evaluated in it,
    so the value is the index of the highest tier with a non zero count.
    """
    tier = 0
    for name in ("tier0", "tier1", "tier2", "tier3"):
        if int(tier_counts.get(name, 0)) > 0:
            tier = int(name[-1])
    return tier


def _write_json(path: str, payload: Any) -> None:
    """Write a JSON file (UTF-8, indented, stable key order)."""
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, default=str)


# --------------------------------------------------------------------------
# The public runtime API
# --------------------------------------------------------------------------
def run_research(
    config_overrides: Optional[Dict[str, Any]] = None,
    *,
    base_config: Optional[Any] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Run one research programme and return its summary.

    ``config_overrides`` may set any field of :class:`physics.ga_config.GAConfig`
    -- the objective, the trial counts, the tier sizes, the statistic mode,
    the seed, the number of generations and islands, the output directory and
    the checkpoint to resume from.  The overrides are applied on top of the
    defaults *and* on top of the configuration stored in a checkpoint, so a
    resumed run honours the trial counts it was given.

    ``base_config`` is the configuration the overrides are merged **on top
    of**.  It may be a :class:`physics.ga_config.GAConfig` (for instance the
    one the command line tool read from ``config/genetic_research.yaml``) or
    a mapping of field names to values.  The precedence is always

    ``override > base configuration > dataclass default``

    so a value of a configuration file is only lost when an override says
    something else.

    ``progress_callback`` is an optional listener that is called at the end
    of every generation with the live progress of the run (see
    :meth:`physics.ga_domain.ResearchRun.progress_snapshot`).  It exists so
    that a monitor (the API, the web UI) can show the run while it is still
    going.  An exception of the listener never aborts the run.
    """
    overrides = {
        str(key): value
        for key, value in dict(config_overrides or {}).items()
        if value is not None
    }
    base = _coerce_base_config(base_config)
    config = base.merged(overrides).validate()
    run = ResearchRun(config, overrides, progress_callback=progress_callback)
    return run.run()


def _coerce_base_config(base_config: Optional[Any]) -> GAConfig:
    """The configuration the overrides of a run are merged on top of.

    ``None`` means "the dataclass defaults"; a :class:`physics.ga_config.GAConfig`
    is taken as it is; a mapping is turned into a configuration.
    """
    if base_config is None:
        return GAConfig()
    if isinstance(base_config, GAConfig):
        return base_config
    if isinstance(base_config, dict):
        return GAConfig().merged(
            {str(key): value for key, value in base_config.items() if value is not None}
        )
    raise TypeError(
        "the base configuration must be a GAConfig, a mapping or None, "
        f"not {type(base_config).__name__}"
    )
