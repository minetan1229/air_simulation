"""Unit tests verifying fixes for milcy/v3 pipeline loopholes and vulnerabilities."""

from __future__ import annotations

import math
import pytest
from core.interfaces import (
    AircraftCandidate,
    AirframeGeometry,
    BehaviorDescriptor,
    EvaluationMetric,
    FlightBenchmarkResult,
    ParametricOrigamiConfig,
)
from geometry.origami_domain import CreasePattern, FoldedFace
from geometry.origami_genome import (
    GripThresholds,
    _panels_intersect,
    _segment_pierces_polygon,
)
from geometry.v3_origami.adapter import (
    OrigamiAdapter,
    extract_airframe_geometry,
    validate_tier0,
)
from optimization.map_elites.engine import MAPElitesEngine
from optimization.map_elites.grid import MAPElitesGridArchive


def test_tier0_rejects_fold_failure_and_fallback():
    """Verify fallback dummy geometry fails Tier 0 validation."""
    fallback_geom = extract_airframe_geometry(None)
    assert fallback_geom.keel_height_m == 0.0
    assert fallback_geom.keel_layers == 0
    assert fallback_geom.penetrations == 999

    cand = AircraftCandidate(
        candidate_id="test-fail",
        config=ParametricOrigamiConfig(),
        step_count=20,
        geometry=fallback_geom,
    )
    valid, reasons = validate_tier0(cand)
    assert not valid
    assert any("Keel height" in r for r in reasons)
    assert any("Self-intersection detected" in r for r in reasons)


def test_tier0_rejects_penetrating_mesh():
    """Verify that a candidate with panel penetrations is rejected by Tier 0."""
    geom = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.015,
        wingspan_m=0.18,
        chord_m=0.12,
        aspect_ratio=2.16,
        cg_m=(0.08, 0.0, 0.005),
        keel_height_m=0.012,
        keel_length_m=0.050,
        keel_layers=4,
        step_count=20,
        penetrations=2,
    )
    cand = AircraftCandidate(
        candidate_id="test-penetrate",
        config=ParametricOrigamiConfig(),
        step_count=20,
        geometry=geom,
    )
    valid, reasons = validate_tier0(cand)
    assert not valid
    assert any("Self-intersection detected" in r for r in reasons)


def test_edge_piercing_intersection_detection():
    """Verify that edge-piercing (X-shaped slicing without corners inside) is detected."""
    # Polygon A: horizontal square on z=0 from (-1, -1) to (1, 1)
    poly_a = [(-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0), (-1.0, 1.0, 0.0)]
    normal_a = (0.0, 0.0, 1.0)
    face_a = FoldedFace(
        fid=1,
        poly3d=poly_a,
        normal=normal_a,
        front=(0.0, 1.0, 0.0),
        level=0,
        group="base",
        role="wing",
        parent=None,
        centroid3d=(0.0, 0.0, 0.0),
        area_m2=4.0,
    )

    # Polygon B: vertical rectangle slicing through z=0 from z=-0.5 to z=0.5, x from -0.5 to 0.5, at y=0
    # All 4 vertices of B are outside polygon A (because z != 0), yet edges pierce polygon A
    poly_b = [(-0.5, 0.0, -0.5), (0.5, 0.0, -0.5), (0.5, 0.0, 0.5), (-0.5, 0.0, 0.5)]
    normal_b = (0.0, 1.0, 0.0)
    face_b = FoldedFace(
        fid=2,
        poly3d=poly_b,
        normal=normal_b,
        front=(0.0, 0.0, 1.0),
        level=0,
        group="base",
        role="wing",
        parent=None,
        centroid3d=(0.0, 0.0, 0.0),
        area_m2=1.0,
    )

    # The edge (-0.5, 0.0, -0.5) -> (-0.5, 0.0, 0.5) crosses z=0 at (-0.5, 0.0, 0.0), strictly inside poly_a
    assert _segment_pierces_polygon(poly_b[0], poly_b[3], poly_a, normal_a, 1.0e-5)
    # Reverse test: poly_a edge does not pierce poly_b
    assert not _segment_pierces_polygon(poly_a[0], poly_a[1], poly_b, normal_b, 1.0e-5)


def test_map_elites_ranges_accommodate_real_a4_paper_planes():
    """Verify real A4 paper airplane descriptors do not clamp to corner bins."""
    grid = MAPElitesGridArchive(bins=(8, 8, 8))

    # Typical folded paper plane descriptor: mass ~5g, S ~0.025m2 -> W/S ~0.20, AR ~0.8-1.5, stability ~0.5-0.7
    desc = BehaviorDescriptor(
        wing_loading_kg_m2=0.22,
        static_stability=0.55,
        aspect_ratio=0.85,
    )
    bin_idx = grid.get_bin_index(desc)

    # All indices should be comfortably inside [1, 6], not clamped to 0 or 7!
    assert 1 <= bin_idx[0] <= 6, f"Wing loading bin {bin_idx[0]} clamped to edge"
    assert 1 <= bin_idx[1] <= 6, f"Stability bin {bin_idx[1]} clamped to edge"
    assert 1 <= bin_idx[2] <= 6, f"Aspect ratio bin {bin_idx[2]} clamped to edge"


def test_behavior_descriptor_geometry_independence():
    """Verify behavior descriptor is computed from geometry static margin without circular simulation dependency."""
    geom = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.020,
        wingspan_m=0.16,
        chord_m=0.125,
        aspect_ratio=1.28,
        cg_m=(0.04, 0.0, 0.005),
        keel_height_m=0.015,
        keel_length_m=0.060,
        keel_layers=4,
        step_count=20,
        planform_area_m2=0.020,
        planform_aspect_ratio=1.28,
        penetrations=0,
    )
    # Call without passing simulation stability
    desc = geom.compute_behavior_descriptor()
    assert 0.0 <= desc.static_stability <= 1.0
    assert math.isclose(desc.wing_loading_kg_m2, 0.005 / 0.020, rel_tol=1e-5)
    assert math.isclose(desc.aspect_ratio, 1.28, rel_tol=1e-5)


def test_deterministic_score_penalizes_zero_distance_parachute():
    """Verify zero forward distance parachute stall receives massive penalty vs forward glide."""
    # Case A: Real glider flying 25m in 5s
    metric_glider = EvaluationMetric(
        speed_mps=5.0,
        flight_time_s=5.0,
        max_ld_ratio=10.0,
        steady_glide_fraction=0.8,
        stability=0.9,
        distance_m=25.0,
        max_altitude_m=1.8,
        landed=True,
        crashed=False,
        diverged=False,
        status="landed",
    )

    # Case B: Stalled parachute floating down with near zero forward speed
    metric_parachute = EvaluationMetric(
        speed_mps=0.2,
        flight_time_s=8.0,
        max_ld_ratio=25.0,  # Transient spike
        steady_glide_fraction=0.5,
        stability=0.5,
        distance_m=0.8,     # Almost no distance
        max_altitude_m=1.5,
        landed=True,
        crashed=False,
        diverged=False,
        status="landed",
    )

    score_glider = metric_glider.deterministic_score
    score_parachute = metric_parachute.deterministic_score

    assert score_glider > 10.0
    assert score_parachute < 1.0
    assert score_glider > 10.0 * score_parachute, "Glider must overwhelmingly outscore stalled parachute"
