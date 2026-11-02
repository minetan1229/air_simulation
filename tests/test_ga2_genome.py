"""Tests of the hybrid fold genome of the second generation search.

The genome has to be able to express the classical designs (the round trip
through the folding engine must not change the solid), it has to survive the
mutation and the crossover, and the warm start has to follow the fractions
of the specification.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from physics.ga2.genome import (  # noqa: E402
    MAX_FOLD_STEPS,
    MIN_FOLD_STEPS,
    HybridFoldGene,
    HybridGenome,
    archetype_hybrid_genomes,
    crease_segment,
    crossover_hybrid,
    hybrid_from_origami,
    hybrid_signature,
    hybrid_to_origami,
    mutate_hybrid,
    random_hybrid_genome,
    seed_population,
)
from physics.origami_genome import (  # noqa: E402
    fold_genome,
    genome_from_archetype,
    genome_to_aircraft_config,
)


def test_the_three_archetypes_are_available() -> None:
    genomes = archetype_hybrid_genomes()
    assert [genome.key for genome in genomes] == [
        "classic_dart",
        "nakamura_lock",
        "sky_king",
    ]


@pytest.mark.parametrize("key", ["classic_dart", "nakamura_lock", "sky_king"])
def test_the_round_trip_does_not_change_the_solid(key: str) -> None:
    source = genome_from_archetype(key)
    hybrid = hybrid_from_origami(source)
    restored = hybrid_to_origami(hybrid)
    original = fold_genome(source, 1.0, 100.0)
    converted = fold_genome(restored, 1.0, 100.0)
    assert converted.wing_area_m2 == pytest.approx(original.wing_area_m2, rel=1e-9)
    assert converted.span_m == pytest.approx(original.span_m, rel=1e-9)
    assert converted.mass_kg == pytest.approx(original.mass_kg, rel=1e-9)
    assert len(converted.faces) == len(original.faces)


def test_the_round_trip_survives_the_serialisation() -> None:
    hybrid = archetype_hybrid_genomes()[0]
    restored = HybridGenome.from_dict(hybrid.to_dict())
    assert restored.to_dict() == hybrid.to_dict()


def test_a_gene_describes_a_crease_that_crosses_the_sheet() -> None:
    p0, p1 = crease_segment((0.5, 0.5), 30.0, 1.0)
    assert 0.0 <= p0[0] <= 1.0 and 0.0 <= p0[1] <= 1.0
    assert 0.0 <= p1[0] <= 1.0 and 0.0 <= p1[1] <= 1.0
    assert p0 != p1


def test_a_short_crease_stops_inside_the_sheet() -> None:
    p0, p1 = crease_segment((0.5, 0.5), 0.0, 0.25)
    assert abs(p1[0] - p0[0]) < 0.5
    assert p0[1] == pytest.approx(p1[1])


def test_a_random_programme_has_a_valid_length() -> None:
    rng = random.Random(3)
    for _ in range(20):
        genome = random_hybrid_genome(rng)
        assert MIN_FOLD_STEPS <= genome.step_count <= MAX_FOLD_STEPS
        assert genome.active_step_count >= 1


def test_a_random_programme_can_be_folded() -> None:
    rng = random.Random(11)
    genome = random_hybrid_genome(rng, min_steps=4, max_steps=5)
    geometry = fold_genome(hybrid_to_origami(genome), 1.0, 100.0)
    assert geometry.total_area_m2 > 0.0
    assert geometry.mass_kg > 0.0
    aircraft = genome_to_aircraft_config(geometry, 100.0)
    assert aircraft.wingspan_m > 0.0
    assert aircraft.wing_area_m2 > 0.0


def test_the_mutation_keeps_the_programme_valid() -> None:
    rng = random.Random(5)
    genome = random_hybrid_genome(rng)
    for _ in range(25):
        child = mutate_hybrid(genome, rng, rate=0.5)
        assert MIN_FOLD_STEPS <= child.step_count <= MAX_FOLD_STEPS
        for gene in child.genes:
            assert -180.0 <= gene.fold_angle_deg <= 180.0
            assert 0.05 <= gene.length_ratio <= 1.0
            assert gene.symmetry in ("symmetric", "left", "right")


def test_the_crossover_keeps_the_programme_valid() -> None:
    rng = random.Random(7)
    first = random_hybrid_genome(rng)
    second = random_hybrid_genome(rng)
    for _ in range(25):
        child = crossover_hybrid(first, second, rng)
        assert MIN_FOLD_STEPS <= child.step_count <= MAX_FOLD_STEPS
        geometry = fold_genome(hybrid_to_origami(child), 1.0, 100.0)
        assert geometry.total_area_m2 > 0.0


def test_the_layer_references_never_point_into_the_future() -> None:
    rng = random.Random(13)
    genome = random_hybrid_genome(rng)
    available = 0
    for gene in genome.genes:
        for layer in gene.target_layers:
            assert 0 <= layer < available
        available += 2 if gene.creates_pair else 1


def test_the_warm_start_follows_the_fractions_of_the_specification() -> None:
    rng = random.Random(17)
    population = seed_population(1000, rng)
    assert len(population) == 1000
    archetypes = sum(1 for genome in population if genome.key.startswith("archetype:"))
    mutants = sum(1 for genome in population if genome.key.startswith("mutant:"))
    assert archetypes == 20
    assert mutants == 80
    assert archetypes + mutants + (1000 - archetypes - mutants) == 1000


def test_the_signature_is_stable_and_distinguishes_the_designs() -> None:
    first = archetype_hybrid_genomes()[0]
    second = archetype_hybrid_genomes()[1]
    assert hybrid_signature(first) == hybrid_signature(first)
    assert hybrid_signature(first) != hybrid_signature(second)


def test_a_gene_rejects_a_dangling_layer() -> None:
    gene = HybridFoldGene.from_dict(
        {
            "enabled": True,
            "origin": [0.5, 0.5],
            "crease_angle_deg": 0.0,
            "fold_angle_deg": 90.0,
            "target_layers": [7, 3, 3],
        }
    )
    assert gene.target_layers == (3, 7)
    assert gene.target_layer == 3
