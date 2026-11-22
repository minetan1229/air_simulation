"""Tests for v3.physics deterministic benchmark and properties extraction."""

import pytest
from physics.config_schema import AircraftConfig
from v3.interfaces import (
    AircraftCandidate,
    BENCHMARK_SPEEDS,
    LAUNCH_HEIGHT_M,
    LAUNCH_PITCH_DEG,
    ParametricOrigamiConfig,
)
from v3.physics.benchmark import (
    DeterministicFlightBenchmark,
    evaluate_horizontal_glide,
)
from v3.physics.properties import (
    compute_behavior_descriptor,
    extract_airframe_geometry,
)


def test_fixed_horizontal_launch_constants():
    """Verify launch constants are strictly zero-pitch and 1.5m."""
    assert LAUNCH_PITCH_DEG == 0.0
    assert LAUNCH_HEIGHT_M == 1.5
    assert BENCHMARK_SPEEDS == (6.0, 10.0)


def test_properties_extraction_default():
    """Verify extraction handles default/None objects gracefully."""
    geom = extract_airframe_geometry(None, step_count=20)
    assert geom.step_count == 20
    assert geom.mass_kg > 0.0
    assert geom.wing_area_m2 > 0.0
    assert geom.aspect_ratio > 0.0

    bd = compute_behavior_descriptor(geom, static_stability=0.8)
    assert bd.wing_loading_kg_m2 > 0.0
    assert bd.static_stability == 0.8
    assert bd.aspect_ratio == geom.aspect_ratio


def test_evaluate_horizontal_glide_deterministic():
    """Verify horizontal glide returns non-empty evaluation metric."""
    cfg = AircraftConfig(
        mass_kg=0.005,
        wing_area_m2=0.015,
        wingspan_m=0.18,
        aspect_ratio=2.16,
        dihedral_deg=3.0,
        elevon_deg=1.5,
    )
    metric = evaluate_horizontal_glide(cfg, speed_mps=6.0, duration_s=0.3)
    assert metric.speed_mps == 6.0
    assert metric.flight_time_s > 0.0
    assert 0.0 <= metric.steady_glide_fraction <= 1.0


def test_benchmark_runner():
    """Verify benchmark runner produces a valid composite result."""
    candidate = AircraftCandidate(
        candidate_id="cand-001",
        config=ParametricOrigamiConfig(),
        step_count=20,
    )
    candidate.geometry = extract_airframe_geometry(None, step_count=20)
    runner = DeterministicFlightBenchmark(duration_s=0.3)
    res = runner.evaluate_candidate(candidate)

    assert res.low_speed.speed_mps == 6.0
    assert res.mid_speed.speed_mps == 10.0
    assert candidate.benchmark is not None
