"""Issue #87: 0-2 fold steps incl. unfolded baseline.

Hypothesis: MIN_FOLD_STEPS=0 + ScalingProfile min=0 + GeometryFilter without
a step-count veto lets 0/1/2-step genomes be stored, converted and screened
by geometry, while >=3-step behaviour is unchanged.  Pass = new tests below
green + existing 47 ga2 tests green.
"""
from __future__ import annotations

import random

from physics.ga2.genome import (
    HybridGenome,
    baseline_genome,
    crossover_hybrid,
    hybrid_to_origami,
    mutate_hybrid,
    random_hybrid_genome,
)
from physics.ga2.scaling import ScalingProfile
from physics.ga2.stage0 import GeometryFilter
from physics.origami_genome import fold_genome


def _genes(n: int):
    rng = random.Random(100 + n)
    return random_hybrid_genome(rng, min_steps=n, max_steps=n).genes


def test_min_fold_steps_is_zero() -> None:
    assert ScalingProfile.small_long().validate().min_fold_steps == 0
    assert ScalingProfile.small_long().validate().min_fold_steps == ScalingProfile.from_dict(
        ScalingProfile.small_long().to_dict()
    ).min_fold_steps


def test_zero_step_genome_round_trips() -> None:
    genome = baseline_genome()
    restored = HybridGenome.from_dict(genome.to_dict())
    assert restored.to_dict() == genome.to_dict()
    assert restored.step_count == 0
    assert restored.active_step_count == 0


def test_zero_step_converts_and_folds() -> None:
    genome = baseline_genome()
    programme = hybrid_to_origami(genome)
    assert programme.steps == []
    geometry = fold_genome(programme, 1.0, 100.0)
    assert geometry.total_area_m2 > 0.0
    assert geometry.mass_kg > 0.0
    assert geometry.span_m > 0.0


def test_baseline_screened_by_geometry_not_step_count() -> None:
    filt = GeometryFilter()
    report = filt.quick_screen(baseline_genome())
    assert report.fold_count == 0
    assert report.valid
    assert "too few folds" not in report.reasons
    assert "no fold acts on the base sheet" not in report.reasons
    full = filt.full_check(baseline_genome())
    assert full.fold_count == 0
    assert full.valid


def test_one_and_two_step_genomes_not_rejected_for_count() -> None:
    filt = GeometryFilter()
    for n in (1, 2, 3):
        genome = HybridGenome(genes=_genes(n), key=f"n{n}")
        report = filt.quick_screen(genome)
        assert "too few folds" not in report.reasons


def test_random_genome_covers_zero_to_three() -> None:
    rng = random.Random(7)
    seen = {random_hybrid_genome(rng, min_steps=0, max_steps=3).step_count for _ in range(300)}
    assert 0 in seen
    assert 1 in seen
    assert 2 in seen


def test_mutate_and_crossover_keep_zero_step_possible() -> None:
    rng = random.Random(3)
    base = baseline_genome()
    # Structural add may grow the baseline; crossover of baselines stays empty.
    child = crossover_hybrid(base, base, rng, min_steps=0, max_steps=5)
    assert child.step_count == 0
    assert hybrid_to_origami(child).steps == []
    grown = mutate_hybrid(base, rng, min_steps=0, max_steps=5)
    assert grown.step_count >= 0
    # Shrinking a 3-step genome never goes below 0.
    genome = HybridGenome(genes=_genes(3))
    for _ in range(20):
        assert mutate_hybrid(genome, rng, min_steps=0).step_count >= 0
