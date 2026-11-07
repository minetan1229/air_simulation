"""Issue #88: exploration quota and elite preservation guards."""
from __future__ import annotations

from physics.ga2.scaling import ScalingProfile


def test_exploration_fraction_exists() -> None:
    profile = ScalingProfile.small_long()
    frac = float(profile.exploration_fraction)
    assert 0.0 < frac < 1.0


def test_diversity_metric_available() -> None:
    from physics.ga2.runner import diversity_of

    assert callable(diversity_of)
