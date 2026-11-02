"""The generation loop of the ultra large genetic search.

One generation is the four stage pipeline of the specification:

1. **Stage 0** -- the geometry filter.  The cheap 2D screen runs over the
   whole population; the survivors get the exact 3D fold.
2. **Stage 1** -- the batched 6-DoF screen.  The population is split into
   the chunks of the scaling profile and every chunk is flown on the GPU
   with a captured CUDA graph.
3. **Stage 2** -- the quasi static structural screen.  The best individuals
   of stage 1 are checked against buckling and against the loss of the
   pitch stability caused by the elastic deformation.
4. **Stage 3** -- the non stationary flow solver with the two way coupling.
   The very best individuals are solved on the fluid grid of the profile,
   one individual per GPU.

The result of a generation feeds the MAP-Elites archive, the Pareto front
and the surrogate database, and the generation is written to an atomic
checkpoint.  A checkpoint written on the small machine can be loaded on the
cluster machine: the state is migrated to the new profile and the run
continues with the bigger population and the finer fluid grid.
"""

from __future__ import annotations

import json
import math
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig
from . import archive as archive_module
from . import checkpoint as checkpoint_module
from . import genome as genome_module
from . import scaling as scaling_module
from . import stage1 as stage1_module
from .genome import HybridGenome, hybrid_signature, hybrid_to_origami
from .scaling import ScalingProfile

__all__ = [
    "GenerationReport",
    "EvolutionRunner",
    "OBJECTIVES",
    "default_objective",
]

#: The objectives the Pareto front of the search is built on.
OBJECTIVES: Tuple[str, ...] = ("distance_m", "max_ld_ratio", "min_peak_stress_index")

#: The behaviour descriptors of the MAP-Elites grid.
BEHAVIOUR_DESCRIPTORS: Tuple[str, str, str] = ("wing_loading", "aspect_ratio", "fold_count")

#: The features of the surrogate model: the behaviour descriptors and the
#: aerodynamic values the screen produced.  The screening values carry their
#: own names because the targets of the model are the corrections.
SURROGATE_FEATURES: Tuple[str, ...] = BEHAVIOUR_DESCRIPTORS + (
    "screening_cl",
    "screening_cd",
    "distance_m",
)


def _to_host(xp: Any) -> Any:
    """A function that brings a device array back to the host."""
    if xp is np:
        return lambda array: np.asarray(array)

    def pull(array: Any) -> Any:
        return np.asarray(array.get())

    return pull


def default_objective(records: Dict[str, float]) -> float:
    """The scalar fitness of one individual.

    The flight distance is the primary objective (the longest hang time of an
    indoor design), and the static stability and the structural head room
    enter as multipliers: a design that flies far but stalls or buckles is
    worse than a slightly shorter flight that does neither.
    """
    distance = float(records.get("distance_m", 0.0))
    ld_ratio = float(records.get("max_ld_ratio", 0.0))
    stability = float(records.get("stability", 0.0))
    stress_index = float(records.get("peak_stress_index", 1.0))
    if distance <= 0.0:
        return 0.0
    quality = max(0.0, min(1.0, stability)) * max(0.0, min(1.0, 1.0 - stress_index))
    return distance * (0.25 + 0.75 * quality) * (1.0 + 0.05 * min(ld_ratio, 20.0))


@dataclass
class GenerationReport:
    """The summary of one generation."""

    generation: int
    population: int
    stage0_passed: int
    stage1_passed: int
    stage2_passed: int
    stage3_passed: int
    best_key: str
    best_fitness: float
    archive_size: int
    pareto_size: int
    elapsed_s: float
    stage_seconds: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generation": int(self.generation),
            "population": int(self.population),
            "stage0_passed": int(self.stage0_passed),
            "stage1_passed": int(self.stage1_passed),
            "stage2_passed": int(self.stage2_passed),
            "stage3_passed": int(self.stage3_passed),
            "best_key": str(self.best_key),
            "best_fitness": float(self.best_fitness),
            "archive_size": int(self.archive_size),
            "pareto_size": int(self.pareto_size),
            "elapsed_s": float(self.elapsed_s),
            "stage_seconds": {key: float(value) for key, value in self.stage_seconds.items()},
        }


class EvolutionRunner:
    """The generation loop of the search."""

    def __init__(
        self,
        profile: Optional[ScalingProfile] = None,
        out_dir: str = "runs/ga2",
        seed: int = 20251021,
        paper_gsm: float = 100.0,
        launch: Optional[LaunchConfig] = None,
        env: Optional[EnvironmentConfig] = None,
        use_gpu: bool = True,
        checkpoint_interval: int = 1,
        surrogate_interval: int = 10,
        stage3_enabled: bool = True,
    ) -> None:
        self.profile = (profile or scaling_module.detect_profile()).validate()
        self.out_dir = out_dir
        self.seed = int(seed)
        self.paper_gsm = float(paper_gsm)
        # The throw of the search: a lofted indoor throw.  The flat, fast
        # throw of the default launch hits the floor before the airframe can
        # glide, which would make every design look like a crash; the lofted
        # throw is what an indoor hang time search has to be run with.
        self.launch = launch or LaunchConfig(
            launch_speed=8.0, launch_angle_deg=25.0, launch_height_m=1.5
        )
        self.env = env or EnvironmentConfig(wind_speed=0.0)
        self.checkpoint_interval = max(1, int(checkpoint_interval))
        self.surrogate_interval = max(1, int(surrogate_interval))
        self.stage3_enabled = bool(stage3_enabled)

        self.rng = random.Random(self.seed)
        self.generation = 0
        self._counter = 0
        self.islands: List[List[Dict[str, Any]]] = []
        self.archive = archive_module.MapElitesArchive(
            bins=(6, 6, 4),
            capacity=self.profile.archive_capacity,
            metric_names=BEHAVIOUR_DESCRIPTORS,
        )
        self.pareto = archive_module.ParetoFront(capacity=max(64, self.profile.archive_capacity // 8))
        self.database: Optional[Any] = None
        self.model: Optional[Any] = None
        self.screener = stage1_module.BatchScreener(
            xp=None,
            env=self.env,
            dt_max=self.profile.stage1_dt_max,
            duration_s=self.profile.stage1_duration_s,
            use_graph=use_gpu,
            seed=self.seed,
            max_steps=self.profile.stage1_max_steps,
        )
        self.pool: Optional[Any] = None
        self._stage0: Optional[Any] = None
        self._stage0_module: Optional[Any] = None
        self._stage2: Optional[Any] = None
        self._stage2_module: Optional[Any] = None
        self._stage3: Optional[Any] = None
        self._stage3_module: Optional[Any] = None
        self.history: List[GenerationReport] = []
        if self.out_dir:
            os.makedirs(self.out_dir, exist_ok=True)
            try:
                from . import surrogate as surrogate_module

                self.database = surrogate_module.SurrogateDatabase(
                    os.path.join(self.out_dir, "surrogate.sqlite3")
                )
            except Exception:  # pragma: no cover - the database is optional
                self.database = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        """A JSON serialisable description of the runner."""
        return {
            "profile": self.profile.to_dict(),
            "screener": self.screener.describe(),
            "generation": int(self.generation),
            "islands": len(self.islands),
            "archive": len(self.archive),
            "pareto": len(self.pareto),
            "out_dir": str(self.out_dir),
            "vram": self.pool.stats() if self.pool is not None else {},
        }

    def run(self, generations: int = 1) -> List[GenerationReport]:
        """Run the given number of generations."""
        reports: List[GenerationReport] = []
        for _ in range(max(0, int(generations))):
            reports.append(self.run_generation())
        return reports

    def run_generation(self) -> GenerationReport:
        """Run one generation of the pipeline."""
        started = time.perf_counter()
        timings: Dict[str, float] = {}

        mark = time.perf_counter()
        population = self._population()
        timings["population"] = time.perf_counter() - mark

        mark = time.perf_counter()
        self._reserve_vram()
        timings["vram"] = time.perf_counter() - mark

        mark = time.perf_counter()
        screened = self._stage0_filter(population)
        timings["stage0"] = time.perf_counter() - mark

        mark = time.perf_counter()
        flown = self._stage1_fly(screened)
        timings["stage1"] = time.perf_counter() - mark

        # The graph of the screening stage is released before the stages that
        # allocate a lot of device memory: the graph keeps the memory of the
        # temporaries it captured alive, and the next replay would read the
        # wrong addresses.  The next generation captures it again.
        self.screener.release()

        mark = time.perf_counter()
        loaded = self._stage2_structure(flown)
        timings["stage2"] = time.perf_counter() - mark

        mark = time.perf_counter()
        resolved = self._stage3_flow(loaded)
        timings["stage3"] = time.perf_counter() - mark

        mark = time.perf_counter()
        self._absorb(flown)
        timings["archive"] = time.perf_counter() - mark

        mark = time.perf_counter()
        self._reproduce()
        timings["reproduce"] = time.perf_counter() - mark

        report = GenerationReport(
            generation=int(self.generation),
            population=len(population),
            stage0_passed=len(screened),
            stage1_passed=len(flown),
            stage2_passed=len(loaded),
            stage3_passed=len(resolved),
            best_key=self._best_key(),
            best_fitness=self._best_fitness(),
            archive_size=len(self.archive),
            pareto_size=len(self.pareto),
            elapsed_s=time.perf_counter() - started,
            stage_seconds=timings,
        )
        self.history.append(report)
        self.generation += 1
        self._write_checkpoint()
        self._maybe_retrain()
        return report

    def state(self) -> checkpoint_module.GenerationState:
        """The state of the search, ready to be written to a checkpoint."""
        return checkpoint_module.GenerationState(
            generation=int(self.generation),
            profile=self.profile.to_dict(),
            islands=[
                {
                    "index": index,
                    "population": list(population),
                    "elites": list(population[: max(1, len(population) // 100)]),
                }
                for index, population in enumerate(self.islands)
            ],
            archive=self.archive.to_dict(),
            pareto=self.pareto.to_dict(),
            best=self._best_payload(),
            statistics={
                "history": [report.to_dict() for report in self.history[-50:]],
                "surrogate_samples": int(self.database.count()) if self.database else 0,
            },
            rng_state={"seed": int(self.seed), "state": list(self.rng.getstate()[1])},
            surrogate=self.model.to_dict() if self.model is not None else {},
        )

    def load(self, path: str) -> None:
        """Load a checkpoint and migrate it to the profile of this runner."""
        state = checkpoint_module.load_checkpoint(path)
        state = checkpoint_module.migrate_state(state, self.profile)
        self.generation = int(state.generation)
        self.islands = []
        for island in state.islands:
            population = list(island.get("population") or [])
            self.islands.append(population)
        self.archive = archive_module.MapElitesArchive.from_dict(state.archive)
        self.pareto = archive_module.ParetoFront.from_dict(state.pareto)
        if state.surrogate:
            try:
                from . import surrogate as surrogate_module

                self.model = surrogate_module.CorrectionModel.from_dict(state.surrogate)
            except Exception:  # pragma: no cover - a broken model must not stop the run
                self.model = None
        self.history = []

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------
    def _population(self) -> List[Dict[str, Any]]:
        """The population of the generation.

        The first generation is the warm start of the specification: two
        percent of the individuals are the known good designs, eight percent
        are mutations of them and the rest are random programmes.  Every
        later generation is the offspring of the previous one.
        """
        if not self.islands:
            seeds = genome_module.seed_population(
                self.profile.population_size,
                rng=self.rng,
                archetype_fraction=self.profile.seed_fraction_archetype,
                mutant_fraction=self.profile.seed_fraction_mutant,
                min_steps=self.profile.min_fold_steps,
                max_steps=self.profile.max_fold_steps,
            )
            size = max(1, self.profile.islands)
            per_island = max(1, len(seeds) // size)
            self.islands = [
                [self._record(genome) for genome in seeds[index * per_island : (index + 1) * per_island]]
                for index in range(size)
            ]
            tail = seeds[per_island * size :]
            if tail:
                self.islands[-1].extend(self._record(genome) for genome in tail)
        return [record for island in self.islands for record in island]

    def _reproduce(self) -> None:
        """Build the next generation from the evaluated individuals."""
        elites_per_island = max(
            2, self.profile.population_size // max(1, self.profile.islands) // 10
        )
        next_islands: List[List[Dict[str, Any]]] = []
        for population in self.islands:
            ranked = sorted(
                population, key=lambda item: float(item.get("fitness", 0.0)), reverse=True
            )
            elites = ranked[:elites_per_island]
            target = len(population)
            children: List[Dict[str, Any]] = list(elites)
            while len(children) < target:
                parent_a = self._tournament(ranked)
                if parent_a is None:
                    break
                parent_b = self._tournament(ranked)
                if parent_b is None or self.rng.random() < 0.35:
                    child = genome_module.mutate_hybrid(
                        parent_a,
                        rng=self.rng,
                        rate=0.2,
                        max_steps=self.profile.max_fold_steps,
                        min_steps=self.profile.min_fold_steps,
                    )
                else:
                    child = genome_module.crossover_hybrid(
                        parent_a,
                        parent_b,
                        rng=self.rng,
                        min_steps=self.profile.min_fold_steps,
                        max_steps=self.profile.max_fold_steps,
                    )
                children.append(self._record(child))
            next_islands.append(children)
        self.islands = next_islands

    def _tournament(self, ranked: Sequence[Dict[str, Any]]) -> Optional[HybridGenome]:
        """Pick one parent by tournament selection."""
        if not ranked:
            return None
        winner: Optional[Dict[str, Any]] = None
        for _ in range(3):
            candidate = ranked[self.rng.randrange(len(ranked))]
            if winner is None or float(candidate.get("fitness", 0.0)) > float(
                winner.get("fitness", 0.0)
            ):
                winner = candidate
        if winner is None:
            return None
        return self._genome_of(winner)

    # ------------------------------------------------------------------
    # The four stages
    # ------------------------------------------------------------------
    def _stage0_filter(self, population: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Stage 0: the geometry and topology filter."""
        if self._stage0 is None:
            stage0_module = self._optional_module("stage0")
            if stage0_module is None:
                return [record for record in population]
            self._stage0_module = stage0_module
            self._stage0 = stage0_module.GeometryFilter(paper_gsm=self.paper_gsm)
        survivors: List[Dict[str, Any]] = []
        for record in population:
            genome = self._genome_of(record)
            if genome is None:
                continue
            report = self._stage0.quick_screen(genome)
            if not report.valid:
                record["stage0"] = report.to_dict()
                continue
            record["stage0"] = report.to_dict()
            survivors.append(record)
        return survivors

    def _optional_module(self, name: str) -> Optional[Any]:
        """Import one stage module, or return ``None`` when it is absent.

        A missing stage module must not stop the search: the stage is
        skipped and the run continues with the remaining ones, which is what
        keeps a partially installed environment usable.
        """
        try:
            module = __import__(f"physics.ga2.{name}", fromlist=[name])
        except Exception:
            return None
        return module

    def _reserve_vram(self) -> None:
        """Reserve the fixed workspace of the generation loop, once.

        The budget of the pool is the eighty five percent of the VRAM the
        profile allows; the reserved part is the workspace the screening
        stage needs for one chunk.  The reservation happens in the first
        generation and is never given back, so the memory of the loop is
        fixed and cannot fragment over months of running.
        """
        if self.pool is not None or self.profile.device_vram_bytes <= 0:
            return
        try:
            from . import vram as vram_module

            width = max(1, int(self.profile.stage1_chunk_width))
            reserve = width * 13 * 8 * 8
            self.pool = vram_module.VramPool(
                device_index=0,
                fraction=self.profile.vram_pool_fraction,
                total_bytes=int(self.profile.device_vram_bytes),
                reserve_bytes=reserve,
            )
            self.pool.reserve()
        except Exception:  # pragma: no cover - the pool is an optimisation
            self.pool = None

    def _stage1_fly(self, candidates: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Stage 1: the batched 6-DoF screen."""
        if not candidates:
            return []
        cases: List[stage1_module.Stage1Case] = []
        for record in candidates:
            genome = self._genome_of(record)
            if genome is None:
                continue
            aircraft = self._screening_aircraft(record)
            state = self._launch_state(aircraft)
            cases.append(
                stage1_module.Stage1Case(
                    index=int(record.get("index", len(cases))),
                    aircraft=aircraft,
                    initial_state=state,
                    duration_s=float(self.profile.stage1_duration_s),
                    label=str(record.get("key", "")),
                )
            )
        if not cases:
            return []
        results = self.screener.screen(cases, repeats=self.profile.stage1_chunk_repeats)
        flown: List[Dict[str, Any]] = []
        for record, result in zip(candidates, results):
            record["stage1"] = result.to_dict()
            record["distance_m"] = result.distance_m
            record["max_ld_ratio"] = result.max_ld_ratio
            record["stability"] = result.stability
            record["peak_stress_index"] = result.peak_stress_index
            record["cl"] = result.cl
            record["cd"] = result.cd
            record["screening_cl"] = result.cl
            record["screening_cd"] = result.cd
            record["status"] = result.status
            record["fitness"] = default_objective(record)
            if result.status not in ("crashed", "degenerate"):
                flown.append(record)
        self._correct_screening(flown)
        return flown

    def _stage2_structure(self, candidates: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Stage 2: the quasi static structural screen."""
        if not candidates:
            return []
        limit = self.profile.stage2_passthrough
        ordered = sorted(candidates, key=lambda item: float(item.get("distance_m", 0.0)), reverse=True)
        selected = ordered[:limit]
        if self._stage2 is None:
            stage2_module = self._optional_module("stage2")
            if stage2_module is None:
                return list(candidates)
            self._stage2_module = stage2_module
            self._stage2 = stage2_module.StaticFsiScreener(paper_gsm=self.paper_gsm)
        cases: List[Any] = []
        stage2_api: Any = self._stage2_module
        for record in selected:
            genome = self._genome_of(record)
            if genome is None:
                continue
            geometry = self._exact_geometry(genome)
            if geometry is None:
                record["stage2"] = {"feasible": False, "reasons": ["geometry failed"]}
                continue
            aircraft = self._aircraft_from_geometry(geometry)
            record["geometry"] = {
                "wing_area_m2": float(geometry.wing_area_m2),
                "span_m": float(geometry.span_m),
                "mass_kg": float(geometry.mass_kg),
                "chord_m": float(geometry.chord_m),
                "aspect_ratio": float(geometry.span_m) ** 2 / max(float(geometry.wing_area_m2), 1.0e-9),
            }
            cases.append(
                stage2_api.Stage2Case(
                    index=int(record.get("index", len(cases))),
                    aircraft=aircraft,
                    dynamic_pressure_pa=self._dynamic_pressure(record),
                    load_factor=float(record.get("peak_load_factor", 1.0)),
                    speed_mps=float(self.launch.launch_speed),
                    span_m=float(geometry.span_m),
                    chord_m=float(geometry.chord_m),
                    thickness_m=max(self.paper_gsm * 1.0e-3 / 800.0, 1.0e-6),
                )
            )
        if not cases:
            return []
        reports = self._stage2.screen(cases)
        survivors: List[Dict[str, Any]] = []
        for record, report in zip(selected, reports):
            record["stage2"] = report.to_dict()
            if report.feasible:
                survivors.append(record)
        self._reference_refly(survivors)
        return survivors

    def _reference_refly(self, records: Sequence[Dict[str, Any]]) -> None:
        """Fly the survivors again with the reference integrator.

        The screening stage uses a fixed step, because a CUDA graph cannot
        have a data dependent one.  The individuals that survive are flown
        again with the adaptive reference integrator of the project, and
        their reference values are the ones the ranking uses: the screen
        filters, the reference decides.
        """
        if not records:
            return
        from .. import gpu_dynamics

        aircraft: List[AircraftConfig] = []
        states: List[Any] = []
        for record in records:
            genome = self._genome_of(record)
            if genome is None:
                continue
            geometry = self._exact_geometry(genome)
            if geometry is None:
                continue
            frame = self._aircraft_from_geometry(geometry)
            aircraft.append(frame)
            states.append(self._launch_state(frame))
        if not states:
            return
        xp = self.screener.xp
        count = len(states)
        try:
            params = gpu_dynamics.build_params(
                xp, aircraft, self.env, wind_override=np.zeros((count, 3), dtype=np.float64)
            )
            summary = gpu_dynamics.simulate_batch(
                xp,
                params,
                xp.asarray(np.stack(states)),
                float(self.profile.stage1_duration_s),
                float(self.profile.stage1_dt_max),
                int(self.profile.stage1_max_steps),
            )
        except Exception:  # pragma: no cover - the reference must not stop the run
            return
        pull = _to_host(xp)
        distance = pull(summary["distance_m"])
        flight_time = pull(summary["flight_time_s"])
        max_ld = pull(summary["max_ld_ratio"])
        peak_stress = pull(summary["peak_stress_pa"])
        crashed = pull(summary["crashed"])
        landed = pull(summary["landed"])
        for index, record in enumerate(records[:count]):
            record["reference"] = {
                "distance_m": float(distance[index]),
                "flight_time_s": float(flight_time[index]),
                "max_ld_ratio": float(max_ld[index]),
                "peak_stress_pa": float(peak_stress[index]),
                "status": (
                    "crashed"
                    if bool(crashed[index])
                    else ("landed" if bool(landed[index]) else "timeout")
                ),
            }
            record["distance_m"] = float(distance[index])
            record["max_ld_ratio"] = float(max_ld[index])
            record["fitness"] = default_objective(record)

    def _stage3_flow(self, candidates: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Stage 3: the non stationary flow solver with the two way coupling."""
        if not candidates or not self.stage3_enabled:
            return list(candidates)
        limit = self.profile.stage3_passthrough
        ordered = sorted(candidates, key=lambda item: float(item.get("distance_m", 0.0)), reverse=True)
        selected = ordered[:limit]
        try:
            stage3_module = self._optional_module("stage3")
        except Exception:
            stage3_module = None
        if stage3_module is None:
            return list(candidates)
        self._stage3_module = stage3_module
        requests: List[Any] = []
        for record in selected:
            genome = self._genome_of(record)
            if genome is None:
                continue
            geometry = self._exact_geometry(genome)
            if geometry is None:
                continue
            aircraft = self._aircraft_from_geometry(geometry)
            requests.append(
                stage3_module.Stage3Request(
                    index=int(record.get("index", len(requests))),
                    genome=genome.to_dict(),
                    aircraft=dict(vars(aircraft)),
                    launch={
                        "speed_mps": float(self.launch.launch_speed),
                        "angle_deg": float(self.launch.launch_angle_deg),
                        "height_m": float(self.launch.launch_height_m),
                    },
                    grid_cells=self.profile.effective_grid_cells(),
                    transition_steps=int(self.profile.stage3_transition_steps),
                    glide_steps=int(self.profile.stage3_glide_steps),
                )
            )
        if not requests:
            return list(candidates)
        results = stage3_module.run_stage3_parallel(requests, self.profile)
        resolved: List[Dict[str, Any]] = []
        for record, result in zip(selected, results):
            record["stage3"] = result.to_dict()
            if result.lift_to_drag > 0.0:
                record["lift_to_drag"] = float(result.lift_to_drag)
                resolved.append(record)
        return resolved

    # ------------------------------------------------------------------
    # Archive, surrogate, checkpoint
    # ------------------------------------------------------------------
    def _absorb(self, records: Sequence[Dict[str, Any]]) -> None:
        """Feed the evaluated individuals into the archive and the front."""
        for record in records:
            metrics = self._behaviour(record)
            entry = archive_module.ArchiveEntry(
                key=str(record.get("key", "")),
                fitness=float(record.get("fitness", 0.0)),
                metrics=metrics,
                payload={"genome": record.get("genome")},
                generation=int(self.generation),
            )
            self.archive.add(entry)
            self.pareto.add(
                str(record.get("key", "")),
                {
                    "distance_m": float(record.get("distance_m", 0.0)),
                    "max_ld_ratio": float(record.get("max_ld_ratio", 0.0)),
                    "min_peak_stress_index": float(record.get("peak_stress_index", 1.0)),
                },
            )
            self._record_sample(record)

    def _record_sample(self, record: Dict[str, Any]) -> None:
        """Store one sample of the surrogate database.

        The features are what the screen knew about the design; the targets
        are the correction the flow solver found: the difference between the
        resolved coefficients and the screening ones.
        """
        if self.database is None or not self.out_dir:
            return
        stage3 = record.get("stage3") or {}
        if not stage3:
            return
        features = self._behaviour(record)
        features["distance_m"] = float(record.get("distance_m", 0.0))
        features["screening_cl"] = float(record.get("screening_cl", 0.0))
        features["screening_cd"] = float(record.get("screening_cd", 0.0))
        features["stability"] = float(record.get("stability", 0.0))
        features["peak_stress_index"] = float(record.get("peak_stress_index", 1.0))
        targets = {
            "cl": float(stage3.get("cl", 0.0)) - float(record.get("screening_cl", 0.0)),
            "cd": float(stage3.get("cd", 0.0)) - float(record.get("screening_cd", 0.0)),
            "cm": float(stage3.get("cm", 0.0)),
        }
        self.database.record(int(self.generation), str(record.get("key", "")), features, targets)

    def _correct_screening(self, records: Sequence[Dict[str, Any]]) -> None:
        """Apply the learned correction to the screening coefficients.

        The correction model is trained on the flow solver's answer; with
        it, the screen itself gets more accurate from generation to
        generation, which is what the specification asks for.
        """
        if self.model is None or not bool(getattr(self.model, "fitted", False)):
            return
        try:
            from . import surrogate as surrogate_module

            corrected = surrogate_module.apply_correction(
                list(records), self.model, SURROGATE_FEATURES
            )
        except Exception:  # pragma: no cover - a broken model must not stop the run
            return
        for record, values in zip(records, corrected):
            for name in ("cl", "cd", "cm"):
                if name in values and name in record:
                    record[name] = float(values[name])

    def _maybe_retrain(self) -> None:
        """Retrain the correction model every few generations."""
        if self.database is None:
            return
        if self.generation % self.surrogate_interval != 0:
            return
        try:
            from . import surrogate as surrogate_module

            self.model = surrogate_module.train_correction_model(
                self.database,
                feature_names=SURROGATE_FEATURES,
                target_names=("cl", "cd", "cm"),
            )
        except Exception:  # pragma: no cover - a broken model must not stop the run
            self.model = None

    def _write_checkpoint(self) -> None:
        """Write the atomic checkpoint of the generation."""
        if not self.out_dir:
            return
        if self.generation % self.checkpoint_interval != 0:
            return
        path = os.path.join(self.out_dir, f"generation_{self.generation:06d}.json")
        try:
            checkpoint_module.save_checkpoint(self.state(), path)
        except Exception:  # pragma: no cover - a failed write must not stop the run
            return

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _record(self, genome: HybridGenome) -> Dict[str, Any]:
        """Wrap a genome into a population record."""
        self._counter += 1
        return {
            "key": hybrid_signature(genome),
            "index": int(self._counter),
            "genome": genome.to_dict(),
            "fitness": 0.0,
            "metrics": {},
        }

    def _all_records(self) -> List[Dict[str, Any]]:
        return [record for island in self.islands for record in island]

    def _genome_of(self, record: Dict[str, Any]) -> Optional[HybridGenome]:
        payload = record.get("genome")
        if not payload:
            return None
        return genome_module.HybridGenome.from_dict(payload)

    def _screening_aircraft(self, record: Dict[str, Any]) -> AircraftConfig:
        """The airframe of the screening stage.

        The screening uses the estimate of the geometry filter: the exact
        3D fold of every individual of the population would cost more than
        the flight itself.  The survivors are re-built with the exact
        geometry in stage 2.
        """
        report = record.get("stage0") or {}
        area = float(report.get("wing_area_m2", 0.0)) or 0.02
        span = float(report.get("span_m", 0.0)) or 0.15
        mass = float(report.get("mass_kg", 0.0)) or 0.005
        config = AircraftConfig()
        config.wing_area_m2 = max(area, 1.0e-6)
        config.wingspan_m = max(span, 1.0e-3)
        config.aspect_ratio = max(span * span / max(area, 1.0e-6), 1.0e-3)
        config.mass_kg = max(mass, 1.0e-9)
        config.sheet_area_m2 = max(area, 1.0e-6)
        config.paper_gsm = self.paper_gsm
        inertia = mass * span * span / 12.0
        config.roll_inertia_kgm2 = max(inertia, 1.0e-12)
        config.pitch_inertia_kgm2 = max(inertia, 1.0e-12)
        config.yaw_inertia_kgm2 = max(inertia, 1.0e-12)
        return config

    def _exact_geometry(self, genome: HybridGenome) -> Optional[Any]:
        """Fold the sheet and return the exact geometry of the design."""
        from ..origami_genome import fold_genome

        try:
            return fold_genome(hybrid_to_origami(genome), 1.0, self.paper_gsm)
        except Exception:
            return None

    def _aircraft_from_geometry(self, geometry: Any) -> AircraftConfig:
        """The airframe of the exact geometry."""
        from ..origami_genome import genome_to_aircraft_config

        return genome_to_aircraft_config(geometry, self.paper_gsm)

    def _launch_state(self, aircraft: AircraftConfig) -> Any:
        """The launch state of one airframe."""
        from .. import gpu_dynamics

        return gpu_dynamics.launch_state_vector(self.launch, self.env, aircraft)

    def _dynamic_pressure(self, record: Dict[str, Any]) -> float:
        """The dynamic pressure of the flight of one record."""
        stage1 = record.get("stage1") or {}
        value = float(stage1.get("peak_dynamic_pressure_pa", 0.0))
        if value > 0.0:
            return value
        speed = float(self.launch.launch_speed)
        return 0.5 * 1.225 * speed * speed

    def _behaviour(self, record: Dict[str, Any]) -> Dict[str, float]:
        """The behaviour descriptors of the MAP-Elites grid."""
        geometry = record.get("geometry") or {}
        area = float(geometry.get("wing_area_m2", 0.0)) or float(
            (record.get("stage0") or {}).get("wing_area_m2", 0.02)
        )
        span = float(geometry.get("span_m", 0.0)) or float(
            (record.get("stage0") or {}).get("span_m", 0.15)
        )
        mass = float(geometry.get("mass_kg", 0.0)) or float(
            (record.get("stage0") or {}).get("mass_kg", 0.005)
        )
        wing_loading = mass * 9.80665 / max(area, 1.0e-9)
        aspect_ratio = span * span / max(area, 1.0e-9)
        fold_count = float((record.get("stage0") or {}).get("fold_count", 0) or 0)
        return {
            "wing_loading": float(wing_loading),
            "aspect_ratio": float(aspect_ratio),
            "fold_count": float(fold_count),
        }

    def _best_key(self) -> str:
        best = self.archive.best()
        return str(best.key) if best is not None else ""

    def _best_fitness(self) -> float:
        best = self.archive.best()
        return float(best.fitness) if best is not None else 0.0

    def _best_payload(self) -> Dict[str, Any]:
        best = self.archive.best()
        if best is None:
            return {}
        return {
            "key": best.key,
            "fitness": best.fitness,
            "metrics": dict(best.metrics),
            "generation": best.generation,
        }
