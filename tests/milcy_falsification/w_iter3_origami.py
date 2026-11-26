"""Falsification tests for v3 origami adapter Tier 0 validation, compilation, and viewer consistency."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from v3.origami.adapter import OrigamiAdapter as V3OrigamiAdapter, validate_tier0 as v3_validate_tier0
from geometry.v3_origami.adapter import OrigamiAdapter as GeomV3OrigamiAdapter
from core.interfaces import (
    ParametricOrigamiConfig,
    ParametricNoseSpec,
    ParametricKeelSpec,
    ParametricWingSpec,
    AircraftCandidate,
    AirframeGeometry,
)
from tools.view_milcy_elite import load_elites
from tools.generate_printable_a4 import _resolve_step_crease_style


class TestIter3OrigamiFalsification:
    """Falsification tests for v3 origami adapter behavior."""

    def test_i3_o1_v3_validate_tier0_rejects_bool_nan_none_degenerate_dims(self) -> None:
        """validate_tier0 must reject bool, NaN, None, and degenerate dimensions without TypeError."""
        adapter = V3OrigamiAdapter()
        baseline = adapter.compile_candidate(ParametricOrigamiConfig())
        assert baseline.geometry is not None

        invalid_cases = [
            ("keel_height_m", True),
            ("keel_height_m", None),
            ("keel_layers", float("nan")),
            ("keel_layers", None),
            ("mass_kg", True),
            ("planform_area_m2", 0.0),
            ("planform_area_m2", float("nan")),
        ]

        for field_name, bad_value in invalid_cases:
            if field_name in ("keel_height_m", "keel_layers", "mass_kg", "planform_area_m2"):
                if field_name == "keel_layers":
                    new_geom = replace(baseline.geometry, keel_layers=bad_value)
                elif field_name == "mass_kg":
                    new_geom = replace(baseline.geometry, mass_kg=bad_value)
                elif field_name == "planform_area_m2":
                    new_geom = replace(baseline.geometry, planform_area_m2=bad_value)
                else:
                    new_geom = replace(baseline.geometry, keel_height_m=bad_value)
                cand = replace(baseline, geometry=new_geom, raw_genome=None)
            else:
                cand = replace(baseline, raw_genome=None)

            valid, reasons = v3_validate_tier0(cand)
            assert valid is False, f"Expected rejection for {field_name}={bad_value!r}, got valid=True"
            assert len(reasons) > 0, f"Expected reasons for {field_name}={bad_value!r}, got empty list"

    def test_i3_o2_v3_compile_candidate_clamps_nan_fold_count(self) -> None:
        """compile_candidate must clamp NaN fold_count and nose_length_ratio without raising."""
        adapter = V3OrigamiAdapter()

        cfg_nan_fold = ParametricOrigamiConfig(nose=ParametricNoseSpec(fold_count=float("nan")))
        cand1 = adapter.compile_candidate(cfg_nan_fold)
        assert cand1.geometry is not None, "Geometry should not be None for NaN fold_count"
        assert cand1.step_count >= 16, f"Step count {cand1.step_count} should be >= 16"

        cfg_nan_length = ParametricOrigamiConfig(nose=ParametricNoseSpec(nose_length_ratio=float("nan")))
        cand2 = adapter.compile_candidate(cfg_nan_length)
        assert cand2.geometry is not None, "Geometry should not be None for NaN nose_length_ratio"
        assert cand2.step_count >= 16, f"Step count {cand2.step_count} should be >= 16"

    def test_i3_o3_steep_winglet_positive_elevon_no_false_penetration(self) -> None:
        """Steep winglet (80 deg) with positive elevon must not trigger false penetration."""
        cfg = ParametricOrigamiConfig(
            wing=ParametricWingSpec(
                dihedral_deg=0.0,
                elevon_left_deg=2.0,
                elevon_right_deg=2.0,
                winglet_angle_deg=80.0,
            )
        )

        adapters = [
            ("v3.origami.adapter", V3OrigamiAdapter()),
            ("geometry.v3_origami.adapter", GeomV3OrigamiAdapter()),
        ]

        for name, adapter in adapters:
            cand = adapter.compile_candidate(cfg)
            assert cand.geometry is not None, f"{name}: geometry should not be None"
            assert cand.geometry.penetrations == 0, f"{name}: Expected 0 penetrations, got {cand.geometry.penetrations}"
            assert cand.metadata.get("tier0_valid") is True, f"{name}: tier0_valid should be True, got {cand.metadata.get('tier0_valid')}"

    def test_i3_o4_viewer_elevon_mountain_matches_a4(self, tmp_path: Path) -> None:
        """Viewer crease lines for elevon must have mountain=False matching A4 resolver."""
        cfg = ParametricOrigamiConfig(
            wing=ParametricWingSpec(elevon_left_deg=5.0, elevon_right_deg=5.0)
        )

        elite_file = tmp_path / "elite_rank1_test.json"
        elite_file.write_text(
            '{"candidate_id": "test_elevon", "config": ' + json.dumps(cfg.to_dict()) + '}',
            encoding="utf-8",
        )

        elites = load_elites(tmp_path)
        assert len(elites) == 1, "Expected exactly one elite loaded"
        elite = elites[0]

        crease_lines = elite.get("crease_lines", [])
        elevon_l = next((cl for cl in crease_lines if cl["label"] == "elevon_l"), None)
        elevon_r = next((cl for cl in crease_lines if cl["label"] == "elevon_r"), None)

        assert elevon_l is not None, "elevon_l crease line should exist"
        assert elevon_r is not None, "elevon_r crease line should exist"

        assert elevon_l["mountain"] is False, "elevon_l should be valley (mountain=False)"
        assert elevon_r["mountain"] is False, "elevon_r should be valley (mountain=False)"

        # Compile candidate directly to get raw_genome for A4 resolver comparison
        adapter = GeomV3OrigamiAdapter()
        cand = adapter.compile_candidate(cfg, candidate_id="test_elevon")
        assert cand.raw_genome is not None

        wing_cfg = cfg.wing
        step_l = next(s for s in cand.raw_genome.steps if s.label == "elevon_l")
        step_r = next(s for s in cand.raw_genome.steps if s.label == "elevon_r")

        resolved_l = _resolve_step_crease_style(step_l.label, step_l.mountain, wing_cfg)[0]
        resolved_r = _resolve_step_crease_style(step_r.label, step_r.mountain, wing_cfg)[0]

        assert resolved_l is False, "A4 resolver says elevon_l should be valley"
        assert resolved_r is False, "A4 resolver says elevon_r should be valley"
        assert elevon_l["mountain"] == resolved_l, "Viewer elevon_l mountain must match A4 resolver"
        assert elevon_r["mountain"] == resolved_r, "Viewer elevon_r mountain must match A4 resolver"
