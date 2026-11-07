"""Issue #89: archetype lineages and free-search quota scope.

8 typed lineages (classic_dart, nakamura_lock, sky_king, baseline_flat,
few_fold, wide_wing, asymmetric, glider) plus the open ``free`` lineage.
Free exploration is always the majority (90%) so typed exploration never
crowds out open search; every lineage seed is a fold programme that
mutation/crossover can change.
"""
from __future__ import annotations

import random

from physics.ga2.genome import ARCHETYPE_KEYS, lineage_of, seed_population


def test_archetype_keys_documented() -> None:
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


def test_free_search_dominates() -> None:
    pop = seed_population(100, random.Random(0))
    lineages = [lineage_of(g) for g in pop]
    assert lineages.count("free") >= 80, lineages
    assert len(pop) == 100
