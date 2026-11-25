"""Falsification tests for the OrigamiAdapter (Milcy Week 4)."""

from __future__ import annotations

import math
from typing import Any
from unittest.mock import patch

import pytest

from core.interfaces import (
    AircraftCandidate,
    AirframeGeometry,
    ParametricNoseSpec,
    ParametricOrigamiConfig,
)
from geometry.origami_domain import FoldedFace
from geometry.origami_genome import (
    FoldStep,
    OrigamiGenome,
    SHEET_OUTLINE,
    evaluate_grip,
    fold_genome,
    genome_to_aircraft_config,
)
from geometry.validation_fixes import origami_to_frd_inertia
from geometry.v3_origami.adapter import (
    OrigamiAdapter,
    detect_strict_self_intersections,
    extract_airframe_geometry,
    validate_tier0,
)


def test_f10_strict_self_intersection_fid_vs_index_adjacency() -> None:
    """X-crossing faces: strict self-intersection must detect crossing via fid."""
    face_a = FoldedFace(
        fid=10,
        poly3d=[(-0.05, 0.0, -0.02), (0.05, 0.0, -0.02), (0.05, 0.0, 0.02), (-0.05, 0.0, 0.02)],
        normal=(0.0, 1.0, 0.0),
        front=(0.0, 1.0, 0.0),
        level=0,
        group="wing_l",
        role="wing",
        parent=None,
        centroid3d=(0.0, 0.0, 0.0),
        area_m2=0.004,
    )
    face_b = FoldedFace(
        fid=20,
        poly3d=[(0.0, -0.05, -0.01), (0.0, 0.05, -0.01), (0.0, 0.05, 0.01), (0.0, -0.05, 0.01)],
        normal=(1.0, 0.0, 0.0),
        front=(1.0, 0.0, 0.0),
        level=0,
        group="wing_r",
        role="wing",
        parent=None,
        centroid3d=(0.0, 0.0, 0.0),
        area_m2=0.002,
    )
    hits = detect_strict_self_intersections([face_a, face_b], adjacency=[(0, 1)])
    assert len(hits) == 1
    assert {hits[0]["face_a"], hits[0]["face_b"]} == {10, 20}


def test_f16_origami_genome_pitch_yaw_inertia_axes() -> None:
    """Unfolded sheet inertias match analytic formulas; asymmetric genome maps to FRD correctly."""
    # Part A: unfolded flat sheet
    geom = fold_genome(OrigamiGenome(steps=[], sheet=list(SHEET_OUTLINE)), paper_gsm=100.0)
    # Sheet lies in x-z plane (y=up). Origami frame: x=roll, y=up=yaw, z=span=pitch
    # roll (I_xx) = m * span^2 / 12
    # pitch (I_zz) = m * length^2 / 12
    # yaw (I_yy) = m * (length^2 + span^2) / 12
    assert math.isclose(
        geom.roll_inertia_kgm2,
        geom.mass_kg * (geom.span_m ** 2) / 12.0,
        rel_tol=1e-3,
    )
    assert math.isclose(
        geom.pitch_inertia_kgm2,
        geom.mass_kg * (geom.length_m ** 2) / 12.0,
        rel_tol=1e-3,
    )
    assert math.isclose(
        geom.yaw_inertia_kgm2,
        geom.mass_kg * (geom.length_m ** 2 + geom.span_m ** 2) / 12.0,
        rel_tol=1e-3,
    )
    assert geom.yaw_inertia_kgm2 > geom.pitch_inertia_kgm2 > geom.roll_inertia_kgm2
    assert math.isclose(
        geom.yaw_inertia_kgm2,
        geom.roll_inertia_kgm2 + geom.pitch_inertia_kgm2,
        rel_tol=1e-3,
    )

    # Part B: asymmetric 3D genome
    asym_genome = OrigamiGenome(
        steps=[
            FoldStep(
                step_index=1,
                crease_line=((0.3, 0.0), (0.8, 1.0)),
                fold_angle_deg=60.0,
                symmetry="right",
            )
        ]
    )
    asym_geom = fold_genome(asym_genome, paper_gsm=100.0)
    expected_frd = origami_to_frd_inertia(asym_geom.inertia_tensor_kgm2)
    config = genome_to_aircraft_config(asym_geom, paper_gsm=100.0, wingspan_m=1.2)
    fit4 = (1.2 / asym_geom.span_m) ** 4
    for key in (
        "roll_inertia_kgm2",
        "pitch_inertia_kgm2",
        "yaw_inertia_kgm2",
        "inertia_xy_kgm2",
        "inertia_xz_kgm2",
        "inertia_yz_kgm2",
    ):
        assert getattr(config, key) == pytest.approx(expected_frd[key] * fit4, rel=1e-6, abs=1e-18)


def test_f19_extract_airframe_geometry_no_fake_keel_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Single-fold genome without keel yields zero keel dims and fails Tier 0."""
    geom_obj = fold_genome(
        OrigamiGenome(
            steps=[
                FoldStep(
                    step_index=1,
                    crease_line=((0.0, 0.5), (1.0, 0.5)),
                    fold_angle_deg=180.0,
                    symmetry="symmetric",
                )
            ]
        ),
        paper_gsm=100.0,
    )
    airframe = extract_airframe_geometry(geom_obj, step_count=12)
    # No keel detected -> zero keel dimensions (no fake fallback)
    assert airframe.keel_height_m == 0.0
    assert airframe.keel_length_m == 0.0
    assert airframe.keel_layers == 0

    cand = AircraftCandidate(
        candidate_id="no-keel",
        config=ParametricOrigamiConfig(),
        step_count=12,
        geometry=airframe,
    )
    assert validate_tier0(cand)[0] is False

    # evaluate_grip error -> fold_valid False, keel dims remain zero
    def _raise_eval(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("grip eval error")

    with patch("geometry.v3_origami.adapter.evaluate_grip", side_effect=_raise_eval):
        airframe_err = extract_airframe_geometry(geom_obj, step_count=12)
        assert airframe_err.fold_valid is False
        assert airframe_err.keel_height_m == 0.0


def test_f20_validate_tier0_fail_closed_on_invalid_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tier 0 must reject None/NaN/negative penetrations, NaN dims, and fold_genome exceptions."""
    def valid_geom(**overrides: Any) -> AirframeGeometry:
        base = dict(
            mass_kg=0.005,
            wing_area_m2=0.015,
            wingspan_m=0.18,
            chord_m=0.12,
            aspect_ratio=2.16,
            cg_m=(0.08, 0.0, 0.005),
            keel_height_m=0.014,
            keel_length_m=0.060,
            keel_layers=4,
            step_count=12,
            planform_area_m2=0.015,
            planform_aspect_ratio=2.16,
        )
        base.update(overrides)
        return AirframeGeometry(**base)

    # Baseline: valid geometry passes
    assert validate_tier0(AircraftCandidate(candidate_id="ok", config=ParametricOrigamiConfig(), step_count=12, geometry=valid_geom()))[0] is True

    assert validate_tier0(AircraftCandidate(candidate_id="p-none", config=ParametricOrigamiConfig(), step_count=12, geometry=valid_geom(penetrations=None)))[0] is False
    assert validate_tier0(AircraftCandidate(candidate_id="p-neg", config=ParametricOrigamiConfig(), step_count=12, geometry=valid_geom(penetrations=-1)))[0] is False

    assert validate_tier0(AircraftCandidate(candidate_id="nan-keel", config=ParametricOrigamiConfig(), step_count=12, geometry=valid_geom(keel_height_m=float("nan"))))[0] is False
    assert validate_tier0(AircraftCandidate(candidate_id="neg-wing", config=ParametricOrigamiConfig(), step_count=12, geometry=valid_geom(wing_area_m2=-0.01)))[0] is False
    assert validate_tier0(AircraftCandidate(candidate_id="none-mass", config=ParametricOrigamiConfig(), step_count=12, geometry=valid_geom(mass_kg=None)))[0] is False

    def _raise_fold(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("fold boom")

    with patch("geometry.v3_origami.adapter.fold_genome", side_effect=_raise_fold):
        geom_with_genome = valid_geom()
        assert validate_tier0(AircraftCandidate(candidate_id="fold-exc", config=ParametricOrigamiConfig(), step_count=12, geometry=geom_with_genome, raw_genome=OrigamiGenome(steps=[])))[0] is False


def test_f23_unclamped_nose_fold_count_gallivan_bypass() -> None:
    """Nose fold_count bypass (ratio or count) must be caught by Gallivan check."""
    # Case 1: fold_count bypass via tiny nose_length_ratio (available < required)
    cfg_ratio = ParametricOrigamiConfig(
        nose=ParametricNoseSpec(fold_count=6, nose_length_ratio=5.0),
        paper_gsm=300.0,
    )
    cand_ratio = OrigamiAdapter().compile_candidate(cfg_ratio, candidate_id="f23-ratio-bypass")
    assert cand_ratio.geometry is not None
    assert cand_ratio.geometry.thickness_limit_ok is False
    assert cand_ratio.metadata["tier0_valid"] is False
    assert any("Gallivan" in r for r in cand_ratio.metadata["tier0_reasons"])

    # Case 2: high fold_count with extreme gsm exceeds available length
    cfg_count = ParametricOrigamiConfig(
        nose=ParametricNoseSpec(fold_count=0, nose_length_ratio=0.20),
        paper_gsm=1200.0,
    )
    cand_count = OrigamiAdapter().compile_candidate(cfg_count, candidate_id="f23-count-bypass")
    assert cand_count.geometry is not None
    assert cand_count.geometry.thickness_limit_ok is False
    assert cand_count.metadata["tier0_valid"] is False
    assert any("Gallivan" in r for r in cand_count.metadata["tier0_reasons"])