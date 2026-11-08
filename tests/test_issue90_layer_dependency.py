"""Tests for Issue #90: Layer dependency preservation in hybrid GA2 crossover & repair.

Verifies:
1. When preceding folds are removed or reordered, invalid layer references
   are not silently reassigned to arbitrary unrelated groups.
2. Dangling references without available parent groups disable the dependent fold
   rather than degenerating into an unintended whole-sheet fold.
3. Crossover between genomes with different layer sequences produces repaired
   genomes with valid layer bounds and consistent step dependency.
"""

from __future__ import annotations

import random
import pytest

from physics.ga2.genome import (
    HybridFoldGene,
    HybridGenome,
    _repair_genes,
    crossover_hybrid,
    mutate_hybrid,
)


def test_dangling_target_layers_disabled_without_layers() -> None:
    """When a fold specifies target_layers=(0,) but no preceding folds created layers,
    it must be disabled to prevent whole-sheet deformation."""
    gene_dangling = HybridFoldGene(
        enabled=True,
        origin=(0.5, 0.0),
        crease_angle_deg=45.0,
        fold_angle_deg=30.0,
        target_layers=(0,),
        creates_group=False,
    )
    repaired = _repair_genes([gene_dangling])
    assert len(repaired) == 1
    # It must NOT be enabled as a whole-sheet fold!
    assert repaired[0].enabled is False


def test_valid_layer_reference_preserved() -> None:
    """When a preceding fold creates a group, a subsequent fold referencing it keeps the reference."""
    g0 = HybridFoldGene(
        enabled=True,
        origin=(0.5, 0.0),
        crease_angle_deg=90.0,
        fold_angle_deg=45.0,
        creates_group=True,
    )
    g1 = HybridFoldGene(
        enabled=True,
        origin=(0.2, 0.5),
        crease_angle_deg=0.0,
        fold_angle_deg=30.0,
        target_layers=(0,),
        creates_group=False,
    )
    repaired = _repair_genes([g0, g1])
    assert len(repaired) == 2
    assert repaired[0].enabled is True
    assert repaired[1].enabled is True
    assert repaired[1].target_layers == (0,)


def test_crossover_hybrid_preserves_layer_validity() -> None:
    """Crossover between multi-layer and single-layer parents yields valid, bounded layer references."""
    rng = random.Random(12345)
    p1_genes = [
        HybridFoldGene(enabled=True, origin=(0.5, 0.0), crease_angle_deg=90.0, fold_angle_deg=45.0, creates_pair=True),
        HybridFoldGene(enabled=True, origin=(0.2, 0.5), crease_angle_deg=0.0, fold_angle_deg=30.0, target_layers=(0, 1), creates_group=True),
        HybridFoldGene(enabled=True, origin=(0.8, 0.5), crease_angle_deg=0.0, fold_angle_deg=-30.0, target_layers=(2,)),
    ]
    p2_genes = [
        HybridFoldGene(enabled=True, origin=(0.5, 0.0), crease_angle_deg=90.0, fold_angle_deg=-45.0, creates_group=False),
        HybridFoldGene(enabled=True, origin=(0.3, 0.2), crease_angle_deg=45.0, fold_angle_deg=15.0, creates_group=False),
    ]

    parent1 = HybridGenome(genes=p1_genes, key="parent1")
    parent2 = HybridGenome(genes=p2_genes, key="parent2")

    for _ in range(20):
        child = crossover_hybrid(parent1, parent2, rng=rng, min_steps=2, max_steps=6)
        # Verify layer bounds in child
        avail = 0
        for gene in child.genes:
            if gene.enabled and gene.target_layers:
                for layer_idx in gene.target_layers:
                    assert 0 <= layer_idx < avail
            if gene.creates_pair and gene.enabled:
                avail += 2
            elif gene.creates_group and gene.enabled and gene.join_group is None:
                avail += 1
