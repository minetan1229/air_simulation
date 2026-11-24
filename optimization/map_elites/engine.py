"""Deterministic MAP-Elites search engine with island diversity & curiosity selection."""
from __future__ import annotations

import math
import random
from typing import Callable, Dict, List, Optional, Tuple

from core.interfaces import AircraftCandidate, ParametricOrigamiConfig
from geometry.v3_origami.adapter import OrigamiAdapter, validate_tier0
from geometry.v3_origami.mcts import MCTSOrigamiGenerator
from optimization.map_elites.grid import MAPElitesGridArchive
from optimization.map_elites.mutation import mutate_parametric_config
from physics.benchmark.benchmark import DeterministicFlightBenchmark


class MAPElitesEngine:
    """Orchestrates the 3D MAP-Elites illumination search with island diversity."""

    def __init__(
        self,
        archive: Optional[MAPElitesGridArchive] = None,
        adapter: Optional[OrigamiAdapter] = None,
        benchmark: Optional[DeterministicFlightBenchmark] = None,
        seed: int = 42,
        mutation_rate: float = 0.35,
        num_islands: int = 3,
        migration_interval: int = 20,
    ) -> None:
        self.archive = archive or MAPElitesGridArchive()
        self.adapter = adapter or OrigamiAdapter()
        self.benchmark = benchmark or DeterministicFlightBenchmark()
        self.rng = random.Random(seed)
        self.mutation_rate = float(mutation_rate)
        self.num_islands = max(1, int(num_islands))
        self.migration_interval = max(5, int(migration_interval))
        self._islands: List[Dict[Tuple[int, int, int], AircraftCandidate]] = [
            {} for _ in range(self.num_islands)
        ]

    def _select_parent_curiosity(
        self, island_idx: int
    ) -> Tuple[Optional[Tuple[int, int, int]], Optional[AircraftCandidate]]:
        """Select parent via softmax over curiosity scores within the active island."""
        pool = self._islands[island_idx]
        if not pool:
            if not self.archive.cells:
                return None, None
            keys = list(self.archive.cells.keys())
            weights = [
                math.exp(max(-5.0, min(5.0, self.archive.curiosity.get(k, 0.0)))) for k in keys
            ]
            chosen_key = self.rng.choices(keys, weights=weights, k=1)[0]
            return chosen_key, self.archive.cells[chosen_key][1]

        keys = list(pool.keys())
        weights = [
            math.exp(max(-5.0, min(5.0, self.archive.curiosity.get(k, 0.0)))) for k in keys
        ]
        chosen_key = self.rng.choices(keys, weights=weights, k=1)[0]
        return chosen_key, pool[chosen_key]

    def _migrate_islands(self) -> None:
        """Ring-migrate the top elite from each island to the next island."""
        if self.num_islands <= 1:
            return
        migrants: List[Optional[Tuple[Tuple[int, int, int], AircraftCandidate]]] = []
        for pool in self._islands:
            if not pool:
                migrants.append(None)
                continue
            best_bin, best_cand = max(
                pool.items(),
                key=lambda kv: kv[1].benchmark.composite_score if kv[1].benchmark else 0.0,
            )
            migrants.append((best_bin, best_cand))

        for idx, item in enumerate(migrants):
            if item is None:
                continue
            target_idx = (idx + 1) % self.num_islands
            b_idx, cand = item
            existing = self._islands[target_idx].get(b_idx)
            existing_score = (
                existing.benchmark.composite_score if (existing and existing.benchmark) else -1.0
            )
            cand_score = cand.benchmark.composite_score if cand.benchmark else 0.0
            if cand_score > existing_score:
                self._islands[target_idx][b_idx] = cand

    def bootstrap_population(self, count: int = 5, mcts_iterations: int = 30) -> None:
        """Seed the archive and islands using high-quality MCTS skeletons."""
        mcts = MCTSOrigamiGenerator(adapter=self.adapter, seed=self.rng.randint(1, 100000))
        seeds = mcts.search_valid_candidates(iterations=mcts_iterations, top_k=count)
        for idx, candidate in enumerate(seeds):
            valid, _ = validate_tier0(candidate)
            if valid:
                self.benchmark.evaluate_candidate(candidate)
                if self.archive.add_candidate(candidate) and candidate.descriptor is not None:
                    b_idx = self.archive.get_bin_index(candidate.descriptor)
                    self._islands[idx % self.num_islands][b_idx] = candidate

        if not self.archive.cells:
            default_cand = self.adapter.compile_candidate(
                ParametricOrigamiConfig(), candidate_id="init-seed-001"
            )
            self.benchmark.evaluate_candidate(default_cand)
            if self.archive.add_candidate(default_cand) and default_cand.descriptor is not None:
                b_idx = self.archive.get_bin_index(default_cand.descriptor)
                self._islands[0][b_idx] = default_cand

    def run_iterations(
        self,
        iterations: int = 50,
        progress_callback: Optional[Callable[[int, float, int], None]] = None,
        mutation_rate: Optional[float] = None,
    ) -> AircraftCandidate:
        """Execute illumination iterations with island migration and adaptive mutation."""
        if not self.archive.cells:
            self.bootstrap_population(count=3)

        base_mutation_rate = (
            float(mutation_rate) if mutation_rate is not None else self.mutation_rate
        )
        stagnation_steps = 0

        for it in range(iterations):
            island_idx = it % self.num_islands
            parent_bin, parent = self._select_parent_curiosity(island_idx)
            if parent is None:
                continue

            adaptive_rate = min(0.85, base_mutation_rate * (1.0 + 0.05 * min(stagnation_steps, 10)))
            mutated_cfg = mutate_parametric_config(
                parent.config,
                mutation_rate=adaptive_rate,
                rng=self.rng,
            )
            candidate = self.adapter.compile_candidate(
                mutated_cfg,
                candidate_id=f"map-elite-{it:04d}",
            )
            valid, _ = validate_tier0(candidate)
            if not valid:
                if parent_bin is not None:
                    self.archive.record_parent_outcome(parent_bin, succeeded=False)
                stagnation_steps += 1
                continue

            self.benchmark.evaluate_candidate(candidate)
            improved = self.archive.add_candidate(candidate)
            if parent_bin is not None:
                self.archive.record_parent_outcome(parent_bin, succeeded=improved)

            if improved and candidate.descriptor is not None:
                b_idx = self.archive.get_bin_index(candidate.descriptor)
                self._islands[island_idx][b_idx] = candidate
                stagnation_steps = 0
            else:
                stagnation_steps += 1

            if (it + 1) % self.migration_interval == 0:
                self._migrate_islands()

            if progress_callback:
                best = self.archive.get_global_best()
                best_score = best.benchmark.composite_score if (best and best.benchmark) else 0.0
                progress_callback(it + 1, best_score, len(self.archive.cells))

        global_best = self.archive.get_global_best()
        if global_best is None:
            global_best = self.adapter.compile_candidate(
                ParametricOrigamiConfig(),
                candidate_id="fallback-elite-001",
            )
            self.benchmark.evaluate_candidate(global_best)
            self.archive.add_candidate(global_best)
        return global_best
