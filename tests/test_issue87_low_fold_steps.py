"""Issue #87: 0-2 fold steps as evaluable baseline.

Hypothesis (pre-registered): with ``MIN_FOLD_STEPS = 0`` and
``ScalingProfile.min_fold_steps == 0`` the 0/1/2-fold genomes are created,
mutated, crossed over, serialised and screened by geometry alone, while an
explicitly configured ``min_steps >= 3`` bound is still respected.  A
physics-impossible 0-fold genome is rejected with a geometry/grip reason,
never a fold-count reason.  Pass = all tests here + existing ga2 genome
tests green.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from physics.ga2.genome import (  # noqa: E402
    MIN_FOLD_STEPS,
    HybridGenome,
    baseline_genome,
    crossover_hybrid,
    hybrid_signature,
    hybrid_to_origami,
    mutate_hybrid,
    random_hybrid_genome,
)
from physics.ga2.scaling import ScalingProfile  # noqa: E402
from physics.ga2.stage0 import GeometryFilter  # noqa: E402
from physics.origami_genome import fold_genome  # noqa: E402

FOLD_COUNT_REASONS = ("too few folds", "too many folds", "fold count")


def _genome(n: int) -> HybridGenome:
    return random_hybrid_genome(random.Random(100 + n), min_steps=n, max_steps=n)


def _roundtrip(genome: HybridGenome) -> HybridGenome:
    """Serialise through JSON and back; the path save/load uses."""
    return HybridGenome.from_dict(json.loads(json.dumps(genome.to_dict())))


@pytest.mark.parametrize("n", [0, 1, 2, 3])
def test_low_fold_create_mutate_crossover_roundtrip(n: int) -> None:
    genome = baseline_genome() if n == 0 else _genome(n)
    assert genome.step_count == n
    assert genome.active_step_count == n
    restored = _roundtrip(genome)
    assert restored.to_dict() == genome.to_dict()
    assert restored.step_count == n
    assert hybrid_signature(restored) == hybrid_signature(genome)
    assert hybrid_to_origami(restored).steps is not None

    rng = random.Random(200 + n)
    for _ in range(25):
        mutant = mutate_hybrid(genome, rng, rate=0.5)
        assert 0 <= mutant.step_count <= 15
        child = crossover_hybrid(genome, _genome(3), rng)
        assert 0 <= child.step_count <= 15

    folded = fold_genome(hybrid_to_origami(genome), 1.0, 100.0)
    assert folded.total_area_m2 > 0.0


def test_zero_fold_mutate_crossover_never_crash_empty() -> None:
    rng = random.Random(7)
    base = baseline_genome()
    for _ in range(50):
        mutate_hybrid(base, rng, min_steps=0, max_steps=5)
        crossover_hybrid(base, base, rng, min_steps=0, max_steps=5)
        crossover_hybrid(base, _genome(1), rng, min_steps=0, max_steps=5)


@pytest.mark.parametrize("n", [0, 1, 2, 3])
def test_filter_does_not_reject_on_count_alone(n: int) -> None:
    filt = GeometryFilter()
    genome = baseline_genome() if n == 0 else _genome(n)
    report = filt.quick_screen(genome)
    assert report.fold_count == n
    assert not any(reason in report.reasons for reason in FOLD_COUNT_REASONS)
    assert "no fold acts on the base sheet" not in report.reasons


def test_zero_fold_baseline_is_valid_by_geometry() -> None:
    filt = GeometryFilter()
    quick = filt.quick_screen(baseline_genome())
    assert quick.valid and quick.reasons == []
    full = filt.full_check(baseline_genome())
    assert full.valid and full.fold_count == 0


def test_impossible_zero_fold_rejected_for_physical_reason() -> None:
    filt = GeometryFilter(min_wing_area_m2=10.0)
    report = filt.quick_screen(baseline_genome())
    assert not report.valid
    assert report.reasons
    assert any(
        key in reason
        for reason in report.reasons
        for key in ("wing area", "span", "stack")
    )
    assert not any(reason in report.reasons for reason in FOLD_COUNT_REASONS)
    full = filt.full_check(baseline_genome())
    assert not full.valid
    assert not any(reason in full.reasons for reason in FOLD_COUNT_REASONS)


def test_defaults_allow_zero_and_explicit_bound_respected() -> None:
    assert MIN_FOLD_STEPS == 0
    assert ScalingProfile.small_long().validate().min_fold_steps == 0
    assert ScalingProfile.workstation().validate().min_fold_steps == 0
    assert ScalingProfile.cluster_20m().validate().min_fold_steps == 0
    assert GeometryFilter().min_fold_steps == 0

    rng = random.Random(9)
    genome = _genome(3)
    for _ in range(25):
        assert mutate_hybrid(genome, rng, min_steps=3).step_count >= 3
        assert crossover_hybrid(genome, genome, rng, min_steps=3).step_count >= 3
    strict = GeometryFilter(min_fold_steps=3)
    report = strict.quick_screen(baseline_genome())
    assert not report.valid
    assert "too few folds" in report.reasons
