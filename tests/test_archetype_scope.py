"""Issue #89: archetype lineages and free-search quota scope.

Documents current 3-type warm start (2% archetype / 8% mutant / 90% random)
and guards that free exploration dominates and archetypes stay mutable
(definchiency: no new named lineages yet — tracked as design gap, not a
model bug). Expanding templates is a design option, not an unverified
physics change.
"""
from __future__ import annotations

import random

from physics.ga2.genome import ARCHETYPE_KEYS, seed_population


def test_archetype_keys_documented() -> None:
    assert set(ARCHETYPE_KEYS) == {"classic_dart", "nakamura_lock", "sky_king"}


def test_free_search_dominates() -> None:
    pop = seed_population(100, random.Random(0))
    origins = [g.meta.get("origin") for g in pop]
    assert origins.count(None) >= 80, origins
    assert len(pop) == 100
