"""Falsification tests for v3.origami.adapter and geometry.v3_origami.adapter."""

import pytest
from dataclasses import replace
from geometry.origami_genome import OrigamiGenome, FoldStep, fold_genome
from geometry.origami_domain import FoldedFace
from v3.interfaces import ParametricOrigamiConfig, ParametricWingSpec
from v3.origami.adapter import OrigamiAdapter as V3OrigamiAdapter, validate_tier0 as v3_validate_tier0
from v3.physics.properties import extract_airframe_geometry
from geometry.v3_origami.adapter import OrigamiAdapter as GeoOrigamiAdapter, validate_tier0 as geo_validate_tier0


def test_i2_02_v3_compiler_elevon_l_side_symmetry() -> None:
    """Test that elevon left step has symmetry='right' and trailing_l face group exists."""
    config = ParametricOrigamiConfig(
        wing=ParametricWingSpec(elevon_left_deg=10.0, elevon_right_deg=10.0)
    )
    adapter = V3OrigamiAdapter()
    cand = adapter.compile_candidate(config)

    assert cand.geometry is not None

    elevon_l_step = None
    for step in cand.raw_genome.steps:
        if step.label == "elevon_l":
            elevon_l_step = step
            break
    assert elevon_l_step is not None, "elevon_l step not found in raw_genome.steps"
    assert elevon_l_step.symmetry == "right"

    paper_gsm = cand.config.paper_gsm
    geom_obj = fold_genome(cand.raw_genome, paper_gsm=paper_gsm)
    assert any(f.group == "trailing_l" for f in geom_obj.faces)

    assert abs(cand.geometry.cg_m[2]) < 1e-6


def test_i2_03_v3_no_fake_keel_strict_x_crossing_and_genome_geometry_tier0(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test flat genome keel rejection, penetrations type validation, X-crossing detection, and GenomeGeometry as raw_genome."""
    flat_geom = fold_genome(OrigamiGenome(steps=[FoldStep(1, ((0.1, 0.1), (0.9, 0.1)), 0.0)]))
    airframe = extract_airframe_geometry(flat_geom, step_count=16)
    assert airframe.keel_height_m == 0.0
    assert airframe.keel_length_m == pytest.approx(0.0, abs=1e-12)
    assert airframe.keel_layers == 0

    adapter = V3OrigamiAdapter()
    cand = adapter.compile_candidate(ParametricOrigamiConfig())

    invalid_bool = replace(cand.geometry, penetrations=False)
    valid, reasons = v3_validate_tier0(replace(cand, geometry=invalid_bool, raw_genome=None))
    assert not valid
    assert len(reasons) > 0

    invalid_float = replace(cand.geometry, penetrations=0.9)
    valid, reasons = v3_validate_tier0(replace(cand, geometry=invalid_float, raw_genome=None))
    assert not valid
    assert len(reasons) > 0

    face_a = FoldedFace(
        fid=100,
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
        fid=200,
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

    base_geom = fold_genome(OrigamiGenome(steps=[]))
    crossed_geom = replace(base_geom, faces=[face_a, face_b])

    def mock_fold_genome(genome, paper_gsm=100.0):
        return crossed_geom

    with monkeypatch.context() as m:
        m.setattr("v3.origami.adapter.fold_genome", mock_fold_genome)

        cand_x = adapter.compile_candidate(ParametricOrigamiConfig())
        assert cand_x.geometry is not None
        assert cand_x.geometry.penetrations >= 1
        assert cand_x.metadata.get("tier0_valid") is False

    cand_v3 = adapter.compile_candidate(ParametricOrigamiConfig())
    folded_v3 = fold_genome(cand_v3.raw_genome, paper_gsm=cand_v3.config.paper_gsm)
    cand_v3_gg = replace(cand_v3, raw_genome=folded_v3)
    assert v3_validate_tier0(cand_v3_gg)[0] is True

    geo_adapter = GeoOrigamiAdapter()
    cand_geo = geo_adapter.compile_candidate(ParametricOrigamiConfig())
    folded_geo = fold_genome(cand_geo.raw_genome, paper_gsm=cand_geo.config.paper_gsm)
    cand_geo_gg = replace(cand_geo, raw_genome=folded_geo)
    assert geo_validate_tier0(cand_geo_gg)[0] is True
