"""Issue #89: archetype expansion and lineage accounting.

8 ARCHETYPE_KEYS cover classic + baseline/few_fold/wide_wing/asymmetric/
glider; lineage_of() tags every seed; free stays 90%; typed lineages are
mutable programmes under the same max-steps clamp.
"""
from __future__ import annotations

import random

from physics.ga2.genome import (
    ARCHETYPE_KEYS,
    LINEAGE_KEYS,
    archetype_hybrid_genomes,
    lineage_of,
    mutate_hybrid,
    seed_population,
)


def test_eight_lineages_and_free() -> None:
    assert set(ARCHETYPE_KEYS) == {
        "classic_dart",
        "nakamura_lock",
        "sky_king",
        "baseline_flat",
        "few_fold",
        "wide_wing",
        "asymmetric",
        "glider",
    }
    assert "free" in LINEAGE_KEYS
    assert set(LINEAGE_KEYS) == set(ARCHETYPE_KEYS) | {"free"}


def test_lineage_of_archetype_and_free() -> None:
    genomes = archetype_hybrid_genomes()
    for genome in genomes:
        assert lineage_of(genome) == genome.key
    pop = seed_population(100, random.Random(0))
    assert any(lineage_of(g) == "free" for g in pop)
    assert any(lineage_of(g) in ARCHETYPE_KEYS for g in pop)


def test_typed_lineages_are_mutable_and_clamped() -> None:
    rng = random.Random(9)
    for genome in archetype_hybrid_genomes():
        child = mutate_hybrid(genome, rng, rate=0.5, max_steps=15, min_steps=0)
        assert child.step_count <= 15
    for genome in archetype_hybrid_genomes(max_steps=5):
        assert genome.step_count <= 5
    clamped = archetype_hybrid_genomes(max_steps=5)
    assert any(g.meta.get("clamped_to_max_steps") for g in clamped)


def test_baseline_and_few_fold_shapes() -> None:
    by_key = {g.key: g for g in archetype_hybrid_genomes()}
    assert by_key["baseline_flat"].step_count == 0
    assert by_key["few_fold"].step_count == 2
    assert by_key["wide_wing"].step_count >= 5


def test_free_quota_undisturbed() -> None:
    pop = seed_population(1000, random.Random(0))
    free = sum(1 for g in pop if lineage_of(g) == "free")
    assert free == 900
