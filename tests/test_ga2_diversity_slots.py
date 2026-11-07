"""Issue #88: diversity / exploration slots.

Hypothesis: EvolutionRunner._reproduce reserves profile.exploration_fraction
of each island for shuffled non-elite survivors plus fresh random injects,
while elites are always preserved and diversity_of() is reported.  Pass =
new tests green + existing ga2 tests green.
"""
from __future__ import annotations

import random

from physics.ga2.genome import HybridGenome, random_hybrid_genome
from physics.ga2.runner import EvolutionRunner, diversity_of, structural_signature
from physics.ga2.scaling import ScalingProfile


def _runner(pop: int = 40, islands: int = 1, expl: float = 0.10) -> EvolutionRunner:
    profile = ScalingProfile.small_long().with_overrides(
        population_size=pop, islands=islands, exploration_fraction=expl
    )
    runner = EvolutionRunner(profile=profile, out_dir="", use_gpu=False, stage3_enabled=False)
    rng = random.Random(0)
    records = [
        {"key": f"k{i}", "fitness": 200.0 - i, "genome": random_hybrid_genome(rng).to_dict()}
        for i in range(pop)
    ]
    per = pop // islands
    runner.islands = [records[i * per : (i + 1) * per] for i in range(islands)]
    return runner


def test_structural_signature_counts_symmetry() -> None:
    genome = random_hybrid_genome(random.Random(4), min_steps=4, max_steps=4)
    sig = structural_signature(genome)
    assert sig[0] == genome.active_step_count
    assert 0 <= sig[1] <= sig[0]


def test_diversity_of_empty_and_monoculture() -> None:
    assert diversity_of([])["unique_signatures"] == 0.0
    genome = random_hybrid_genome(random.Random(1), min_steps=5, max_steps=5)
    records = [{"genome": genome.to_dict()} for _ in range(5)]
    div = diversity_of(records)
    assert div["unique_signatures"] == 1.0
    assert div["signature_entropy"] == 0.0


def test_diversity_of_spread_population() -> None:
    rng = random.Random(2)
    records = [
        {"genome": random_hybrid_genome(rng, min_steps=3, max_steps=15).to_dict()}
        for _ in range(20)
    ]
    div = diversity_of(records)
    assert div["unique_signatures"] > 1.0
    assert div["signature_entropy"] > 0.0


def test_reproduce_preserves_best_and_size() -> None:
    runner = _runner(40, 1, 0.10)
    best_key = runner.islands[0][0]["key"]
    runner._reproduce()
    assert len(runner.islands[0]) == 40
    assert best_key in {record["key"] for record in runner.islands[0]}
    assert runner.last_diversity["unique_signatures"] >= 1.0


def test_exploration_slots_keep_low_fitness() -> None:
    runner = _runner(20, 1, 0.50)
    worst_key = runner.islands[0][-1]["key"]
    runner._reproduce()
    assert worst_key in {record["key"] for record in runner.islands[0]}


def test_exploration_fraction_validated() -> None:
    profile = ScalingProfile.small_long()
    assert 0.0 <= profile.exploration_fraction <= 0.5
    try:
        ScalingProfile.small_long().with_overrides(exploration_fraction=0.9).validate()
    except ValueError:
        return
    raise AssertionError("exploration_fraction=0.9 must be rejected")
