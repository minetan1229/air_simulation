"""Deterministic MAP-Elites search engine.

Drives the population through parametric mutation, Tier 0 validation,
and deterministic 6DoF horizontal benchmark illumination.
"""

from __future__ import annotations

import random
import time
from typing import Callable, List, Optional

from core.interfaces import AircraftCandidate, ParametricOrigamiConfig
from geometry.v3_origami.adapter import OrigamiAdapter, validate_tier0
from geometry.v3_origami.mcts import MCTSOrigamiGenerator
from physics.benchmark.benchmark import DeterministicFlightBenchmark
from optimization.map_elites.grid import MAPElitesGridArchive
from optimization.map_elites.mutation import mutate_parametric_config


class MAPElitesEngine:
    """Orchestrates the 3D MAP-Elites illumination search."""

    def __init__(
        self,
        archive: Optional[MAPElitesGridArchive] = None,
        adapter: Optional[OrigamiAdapter] = None,
        benchmark: Optional[DeterministicFlightBenchmark] = None,
        seed: int = 42,
        mutation_rate: float = 0.35,
    ) -> None:
        self.archive = archive or MAPElitesGridArchive()
        self.adapter = adapter or OrigamiAdapter()
        self.benchmark = benchmark or DeterministicFlightBenchmark()
        self.rng = random.Random(seed)
        self.mutation_rate = float(mutation_rate)

    def bootstrap_population(self, count: int = 5, mcts_iterations: int = 30) -> None:
        """Seed the archive using high-quality MCTS skeletons."""
        mcts = MCTSOrigamiGenerator(adapter=self.adapter, seed=self.rng.randint(1, 100000))
        seeds = mcts.search_valid_candidates(iterations=mcts_iterations, top_k=count)

        for candidate in seeds:
            valid, _ = validate_tier0(candidate)
            if valid:
                self.benchmark.evaluate_candidate(candidate)
                self.archive.add_candidate(candidate)

        if not self.archive.cells:
            default_cand = self.adapter.compile_candidate(
                ParametricOrigamiConfig(), candidate_id="init-seed-001"
            )
            self.benchmark.evaluate_candidate(default_cand)
            self.archive.add_candidate(default_cand)

    def run_iterations(
        self,
        iterations: int = 50,
        progress_callback: Optional[Callable[[int, float, int], None]] = None,
        mutation_rate: Optional[float] = None,
    ) -> AircraftCandidate:
        """Execute illumination iterations and return the global best candidate."""
        # Ensure archive has at least one elite
        if not self.archive.cells:
            self.bootstrap_population(count=3)

        effective_mutation_rate = (
            float(mutation_rate) if mutation_rate is not None else self.mutation_rate
        )

        for it in range(iterations):
            # 1. Select parent from existing elites with curiosity / sparse-niche bias
            elites = self.archive.get_elites()
            if not elites:
                continue
            if len(elites) <= 2 or self.rng.random() < 0.25:
                parent = self.rng.choice(elites)
            else:
                coords = [
                    self.archive.get_bin_index(e.descriptor)
                    for e in elites
                    if e.descriptor is not None
                ]
                if len(coords) == len(elites):
                    weights = []
                    for c in coords:
                        crowding = sum(
                            1
                            for o in coords
                            if abs(c[0] - o[0]) <= 1 and abs(c[1] - o[1]) <= 1 and abs(c[2] - o[2]) <= 1
                        )
                        weights.append(1.0 / float(crowding))
                    total_w = sum(weights)
                    parent = self.rng.choices(elites, weights=weights, k=1)[0] if total_w > 0.0 else self.rng.choice(elites)
                else:
                    parent = self.rng.choice(elites)

            # 2. Mutate parameters
            mutated_cfg = mutate_parametric_config(
                parent.config,
                mutation_rate=effective_mutation_rate,
                rng=self.rng,
            )

            # 3. Compile & Validate Tier 0
            candidate = self.adapter.compile_candidate(
                mutated_cfg,
                candidate_id=f"map-elite-{it:04d}",
            )
            valid, _ = validate_tier0(candidate)
            if not valid:
                continue

            # 4. Evaluate via deterministic 6DoF horizontal benchmark
            self.benchmark.evaluate_candidate(candidate)

            # 5. Insert into 3D behavior grid
            improved = self.archive.add_candidate(candidate)

            # 6. Optional progress callback
            if progress_callback:
                best = self.archive.get_global_best()
                best_score = best.benchmark.composite_score if (best and best.benchmark) else 0.0
                progress_callback(it + 1, best_score, len(self.archive.cells))

        global_best = self.archive.get_global_best()
        if global_best is None:
            # Fallback compile default candidate
            global_best = self.adapter.compile_candidate(
                ParametricOrigamiConfig(),
                candidate_id="fallback-elite-001",
            )
            self.benchmark.evaluate_candidate(global_best)
            self.archive.add_candidate(global_best)
        return global_best
