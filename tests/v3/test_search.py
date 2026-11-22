"""Unit tests for v3.search 3D MAP-Elites engine, grid, and mutation."""

import pytest
from v3.interfaces import (
    AircraftCandidate,
    BehaviorDescriptor,
    EvaluationMetric,
    FlightBenchmarkResult,
    ParametricOrigamiConfig,
)
from v3.search.grid import MAPElitesGridArchive
from v3.search.mutation import mutate_parametric_config


def test_grid_archive_binning():
    """Verify behavior descriptor maps into correct 3D grid bins."""
    grid = MAPElitesGridArchive(bins=(4, 4, 4))
    desc1 = BehaviorDescriptor(wing_loading_kg_m2=0.105, static_stability=0.62, aspect_ratio=0.16)
    desc2 = BehaviorDescriptor(wing_loading_kg_m2=0.195, static_stability=0.94, aspect_ratio=0.44)

    bin1 = grid.get_bin_index(desc1)
    bin2 = grid.get_bin_index(desc2)

    assert bin1 == (0, 0, 0)
    assert bin2 == (3, 3, 3)


def test_grid_archive_add_and_coverage():
    """Verify adding candidate updates grid and increases coverage."""
    grid = MAPElitesGridArchive(bins=(4, 4, 4))

    cand = AircraftCandidate(
        candidate_id="cand-test",
        config=ParametricOrigamiConfig(),
        step_count=20,
    )
    cand.descriptor = BehaviorDescriptor(wing_loading_kg_m2=0.15, static_stability=0.75, aspect_ratio=0.30)

    metric = EvaluationMetric(
        speed_mps=6.0,
        flight_time_s=5.0,
        max_ld_ratio=8.0,
        steady_glide_fraction=0.8,
        stability=0.9,
        distance_m=25.0,
        max_altitude_m=2.0,
        landed=True,
        crashed=False,
        diverged=False,
        status="landed",
    )
    cand.benchmark = FlightBenchmarkResult(
        low_speed=metric,
        mid_speed=metric,
        composite_score=metric.deterministic_score,
        valid=True,
    )

    added = grid.add_candidate(cand)
    assert added is True
    assert grid.coverage() > 0.0
    assert len(grid.get_elites()) == 1
    assert grid.get_global_best() == cand


def test_parametric_mutation_maintains_bounds():
    """Verify mutation produces valid bounded parameters."""
    cfg = ParametricOrigamiConfig()
    mutated = mutate_parametric_config(cfg, mutation_rate=1.0)

    assert 3 <= mutated.nose.fold_count <= 6
    assert 0.45 <= mutated.nose.taper_ratio <= 0.85
    assert mutated.keel.keel_depth_ratio > 0.0
    assert mutated.wing.wingspan_ratio > 0.0
