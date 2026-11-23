"""Unit tests for Sequential 2.5D Origami Folding Kernel & MacroStep Bilevel Architecture."""

from __future__ import annotations

import math
import pytest
from core.interfaces import AircraftCandidate, AirframeGeometry, BehaviorDescriptor, FlightBenchmarkResult, EvaluationMetric, ParametricOrigamiConfig
from geometry.v3_origami.kernel_25d import (
    FoldedPolygon2D,
    MacroFoldStep,
    Sequential2DOrigamiKernel,
    compile_macro_origami,
    make_reflection_matrix,
    _matvec3,
)
from geometry.v3_origami.adapter import OrigamiAdapter
from optimization.map_elites.grid import MAPElitesGridArchive


def test_reflection_matrix_properties():
    """Verify 2D reflection matrix in homogeneous coordinates."""
    # Reflection across x=0.5 vertical line
    refl = make_reflection_matrix((0.5, 0.0), (0.0, 1.0))
    # Point (0.2, 0.3) reflected across x=0.5 -> (0.8, 0.3)
    p_ref = _matvec3(refl, (0.2, 0.3, 1.0))
    assert math.isclose(p_ref[0], 0.8, abs_tol=1e-6)
    assert math.isclose(p_ref[1], 0.3, abs_tol=1e-6)


def test_kernel_25d_initial_state():
    """Verify initial flat sheet state of 2.5D kernel."""
    kernel = Sequential2DOrigamiKernel()
    assert len(kernel.polygons) == 1
    init_poly = kernel.polygons[0]
    assert init_poly.fid == 0
    assert len(init_poly.vertices) == 4
    assert math.isclose(init_poly.area, 1.0, abs_tol=1e-6)
    assert init_poly.z_index == 0
    assert init_poly.face_up is True


def test_kernel_25d_halfspace_clip_and_fold():
    """Verify sequential fold clips polygons and produces valid UV crease mapping."""
    kernel = Sequential2DOrigamiKernel()
    # Fold across centerline (0.5, 0.0) -> (0.5, 1.0)
    uv_creases = kernel.execute_fold(
        p0=(0.5, 0.0),
        direction=(0.0, 1.0),
        mountain=False,
    )
    # Sheet split into 2 half-sheets: one staying, one moving/reflected
    assert len(kernel.polygons) >= 2
    # Crease produced along x=0.5
    assert len(uv_creases) >= 1
    p0, p1 = uv_creases[0]
    assert math.isclose(p0[0], 0.5, abs_tol=1e-5)
    assert math.isclose(p1[0], 0.5, abs_tol=1e-5)

    # All polygon UV mappings must stay within [0, 1] x [0, 1]
    for poly in kernel.polygons:
        for v in poly.vertices:
            u, v_uv = poly.to_uv(v)
            assert -1e-5 <= u <= 1.0 + 1e-5
            assert -1e-5 <= v_uv <= 1.0 + 1e-5


def test_compile_macro_origami_bilevel():
    """Verify compile_macro_origami decomposes human MacroSteps into physical FoldSteps."""
    cfg = ParametricOrigamiConfig()
    macro_steps, fold_steps = compile_macro_origami(cfg)

    # MacroSteps must be in the human practical sweet spot: 6 to 10 steps
    assert 6 <= len(macro_steps) <= 10
    # Atomic fold steps must be within physical range: 10 to 18 steps
    assert 10 <= len(fold_steps) <= 18

    # Check symmetry classifications
    axis_steps = [s for s in macro_steps if s.fold_type == "axis_symmetric"]
    bilateral_steps = [s for s in macro_steps if s.fold_type == "bilateral_symmetric"]
    assert len(axis_steps) >= 2  # Centerline, keel
    assert len(bilateral_steps) >= 3  # Nose, wings, winglets


def test_adapter_attaches_macro_steps_metadata():
    """Verify OrigamiAdapter populates macro_steps metadata."""
    adapter = OrigamiAdapter()
    candidate = adapter.compile_candidate(ParametricOrigamiConfig())

    assert "macro_steps" in candidate.metadata
    assert "macro_step_count" in candidate.metadata
    assert 6 <= candidate.metadata["macro_step_count"] <= 10
    assert len(candidate.metadata["macro_steps"]) == candidate.metadata["macro_step_count"]


def test_parsimony_pressure_tie_break():
    """Verify MAP-Elites grid prefers candidates with fewer steps during tie-breaks."""
    grid = MAPElitesGridArchive(bins=(5, 5, 5))
    dummy_metric = EvaluationMetric(
        speed_mps=6.0,
        flight_time_s=3.0,
        max_ld_ratio=8.0,
        steady_glide_fraction=0.8,
        stability=0.8,
        distance_m=18.0,
        max_altitude_m=1.6,
        landed=True,
        crashed=False,
        diverged=False,
        status="landed",
    )
    bench_result = FlightBenchmarkResult(
        low_speed=dummy_metric,
        mid_speed=dummy_metric,
        composite_score=15.0,
        valid=True,
    )
    desc = BehaviorDescriptor(
        wing_loading_kg_m2=3.0,
        static_stability=0.5,
        aspect_ratio=2.5,
    )

    # Candidate A: 16 steps, score 15.0
    cand_a = AircraftCandidate(
        candidate_id="cand-complex",
        config=ParametricOrigamiConfig(),
        step_count=16,
        descriptor=desc,
        benchmark=bench_result,
    )

    # Candidate B: 8 steps (simpler design), same score 15.0
    cand_b = AircraftCandidate(
        candidate_id="cand-simple",
        config=ParametricOrigamiConfig(),
        step_count=8,
        descriptor=desc,
        benchmark=bench_result,
    )

    # Insert candidate A first
    added_a = grid.add_candidate(cand_a)
    assert added_a is True

    # Insert candidate B with same score: Parsimony Pressure must replace A with simpler B
    bin_idx = grid.get_bin_index(desc)
    assert grid.cells[bin_idx][1].candidate_id == "cand-complex"

    added_b = grid.add_candidate(cand_b)
    assert added_b is True
    # Verify that the simpler design won the niche
    assert grid.cells[bin_idx][1].candidate_id == "cand-simple"
