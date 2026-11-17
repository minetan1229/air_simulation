"""Deterministic MAP-Elites search engine.

Drives the population through parametric mutation, Tier 0 validation,
and deterministic 6DoF horizontal benchmark illumination.
"""

from __future__ import annotations

import random
import time
from typing import Callable, List, Optional

from v3.interfaces import AircraftCandidate
from v3.origami.adapter import OrigamiAdapter, validate_tier0
from v3.origami.mcts import MCTSOrigamiGenerator
from v3.physics.benchmark import DeterministicFlightBenchmark
from v3.search.grid import MAPElitesGridArchive
from v3.search.mutation import mutate_parametric_config


class MAPElitesEngine:
    """Orchestrates the 3D MAP-Elites illumination search."""

    def __init__(
        self,
        archive: Optional[MAPElitesGridArchive] = None,
        adapter: Optional[OrigamiAdapter] = None,
        benchmark: Optional[DeterministicFlightBenchmark] = None,
        seed: int = 42,
    ) -> None:
        self.archive = archive or MAPElitesGridArchive()
        self.adapter = adapter or OrigamiAdapter()
        self.benchmark = benchmark or DeterministicFlightBenchmark()
        self.rng = random.Random(seed)

    def bootstrap_population(self, count: int = 5, mcts_iterations: int = 30) -> None:
        """Seed the archive using high-quality MCTS skeletons."""
        mcts = MCTSOrigamiGenerator(adapter=self.adapter, seed=self.rng.randint(1, 100000))
        seeds = mcts.search_valid_candidates(iterations=mcts_iterations, top_k=count)

        for candidate in seeds:
            valid, _ = validate_tier0(candidate)
            if valid:
                self.benchmark.evaluate_candidate(candidate)
                self.archive.add_candidate(candidate)

    def run_iterations(
        self,
        iterations: int = 50,
        progress_callback: Optional[Callable[[int, float, int], None]] = None,
    ) -> AircraftCandidate:
        """Execute illumination iterations and return the global best candidate."""
        # Ensure archive has at least one elite
        if not self.archive.cells:
            self.bootstrap_population(count=3)

        for it in range(iterations):
            # 1. Select parent from existing elites
            elites = self.archive.get_elites()
            parent = self.rng.choice(elites) if elites else None
            if parent is None:
                continue

            # 2. Mutate parameters
            mutated_cfg = mutate_parametric_config(
                parent.config,
                mutation_rate=0.40,
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
                self.adapter.compiler.compile_steps.__func__.__defaults__[0]  # type: ignore[attr-defined]
                if hasattr(self.adapter.compiler, "default")
                else None  # type: ignore[arg-type]
            )
        return global_best
