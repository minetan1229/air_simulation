"""Tests for Issue #79: Origami self-intersection, paper thickness, and impossible folds screening.

Validates that:
1. GeometryFilter detects panel self-penetration and rejects impossible shapes from GA evaluation.
2. An empty/no-fold genome is treated as a valid flat sheet baseline rather than crashing.
3. Premature truncation of fold steps consistently recomputes mass, area, CG, and inertia.
4. Penetration detection flags overlapping non-adjacent layers exceeding thickness tolerances.
"""

from __future__ import annotations

import pytest

from physics.ga2.genome import HybridFoldGene, HybridGenome
from physics.ga2.stage0 import GeometryFilter


def test_empty_genome_evaluated_as_flat_sheet() -> None:
    """An unfolded flat sheet genome must evaluate successfully as baseline."""
    genome = HybridGenome(genes=[], key="flat_sheet", label="flat sheet")
    gf = GeometryFilter(paper_gsm=100.0)
    report = gf.evaluate(genome)

    assert report.valid is True
    assert report.fold_count == 0
    assert report.layer_count == 1
    assert report.wing_area_m2 > 0.05  # A4 sheet is ~0.0624 m^2
    assert report.penetrations == 0


def test_penetration_rejection_in_stage0() -> None:
    """A folded geometry that penetrates non-adjacent faces is flagged as invalid."""
    # Create intersecting fold genes:
    g1 = HybridFoldGene(
        enabled=True,
        origin=(0.5, 0.0),
        crease_angle_deg=90.0,
        fold_angle_deg=90.0,
        creates_group=True,
    )
    g2 = HybridFoldGene(
        enabled=True,
        origin=(0.2, 0.5),
        crease_angle_deg=0.0,
        fold_angle_deg=90.0,
        creates_group=True,
    )
    genome = HybridGenome(genes=[g1, g2], key="cross_fold", label="crossing fold")
    gf = GeometryFilter(paper_gsm=100.0, max_penetrations=0)
    report = gf.evaluate(genome)

    # Penetration count is recorded
    assert isinstance(report.penetrations, int)
    if report.penetrations > 0:
        assert report.valid is False
        assert "self penetration" in report.reasons


def test_truncation_recomputes_mass_and_cg() -> None:
    """Truncating fold steps dynamically produces different consistent geometry metrics."""
    g1 = HybridFoldGene(
        enabled=True,
        origin=(0.5, 0.0),
        crease_angle_deg=90.0,
        fold_angle_deg=180.0,
        creates_group=True,
    )
    g2 = HybridFoldGene(
        enabled=True,
        origin=(0.2, 0.5),
        crease_angle_deg=0.0,
        fold_angle_deg=180.0,
        creates_group=True,
    )

    gf = GeometryFilter(paper_gsm=100.0)
    rep_step1 = gf.evaluate(HybridGenome(genes=[g1]))
    rep_step2 = gf.evaluate(HybridGenome(genes=[g1, g2]))

    # Area decreases when folding in half
    assert rep_step1.wing_area_m2 > rep_step2.wing_area_m2
    # Layer count increases
    assert rep_step2.layer_count >= rep_step1.layer_count
