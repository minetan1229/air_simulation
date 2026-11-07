"""Tests for GA2 stage candidate and result matching by unique ID / index (Issue #99).

Verifies that Stage 1, Stage 2, and Stage 3 match reports and results
to their corresponding candidate records via index / ID rather than
relying on zip() order, even when intermediate candidates fail geometry
or are skipped.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from physics.config_schema import AircraftConfig
from physics.ga2.genome import HybridGenome
from physics.ga2.runner import EvolutionRunner
from physics.ga2.scaling import ScalingProfile
from physics.ga2.stage1 import Stage1Result
from physics.ga2.stage2 import Stage2Case, Stage2Report
from physics.ga2.stage3 import Stage3Request, Stage3Result


def _dummy_profile() -> ScalingProfile:
    return ScalingProfile.small_long(1, 8 * 1024 ** 3).with_overrides(
        stage2_passthrough=10,
        stage3_passthrough=10,
        stage3_scheme="w_mles",
        stage3_grid_cells=1024,
    )


def test_stage2_matching_by_id_when_geometry_skipped(tmp_path: Path) -> None:
    """When a candidate fails geometry generation, subsequent candidates must

    still receive their own Stage 2 report without index shifting.
    """
    runner = EvolutionRunner(profile=_dummy_profile(), out_dir=str(tmp_path), seed=42)

    # Mock _genome_of to return dummy genomes
    g1 = MagicMock(spec=HybridGenome)
    g2 = MagicMock(spec=HybridGenome)
    g3 = MagicMock(spec=HybridGenome)

    records = [
        {"key": "rec_1", "index": 101, "genome": {"mock": 1}, "fitness": 10.0, "distance_m": 10.0},
        {"key": "rec_2", "index": 202, "genome": {"mock": 2}, "fitness": 9.0, "distance_m": 9.0},
        {"key": "rec_3", "index": 303, "genome": {"mock": 3}, "fitness": 8.0, "distance_m": 8.0},
    ]

    genome_map = {101: g1, 202: g2, 303: g3}
    runner._genome_of = lambda r: genome_map.get(r["index"])  # type: ignore

    # rec_2 will fail geometry generation (returns None)
    dummy_geom = MagicMock()
    dummy_geom.wing_area_m2 = 0.02
    dummy_geom.span_m = 0.2
    dummy_geom.mass_kg = 0.005
    dummy_geom.chord_m = 0.1

    def mock_exact_geom(genome: Any) -> Any:
        if genome is g2:
            return None
        return dummy_geom

    runner._exact_geometry = mock_exact_geom  # type: ignore
    runner._aircraft_from_geometry = lambda geom: AircraftConfig()  # type: ignore
    runner._dynamic_pressure = lambda rec: 50.0  # type: ignore
    runner._reference_refly = MagicMock()

    # Mock Stage 2 screener
    mock_screener = MagicMock()

    def mock_screen(cases: List[Stage2Case]) -> List[Stage2Report]:
        # Return reports corresponding to the cases received
        # cases should only contain rec_1 (index=101) and rec_3 (index=303)
        reports = []
        for c in cases:
            reports.append(
                Stage2Report(
                    index=c.index,
                    feasible=True,
                    buckling_margin=2.0,
                    max_tip_deflection_m=0.001,
                    pitch_stability_margin=0.05,
                    reasons=[],
                )
            )
        return reports

    runner._stage2 = mock_screener
    mock_screener.screen = mock_screen
    import physics.ga2.stage2 as stage2_module
    runner._stage2_module = stage2_module

    survivors = runner._stage2_structure(records)

    # rec_2 should have failed with reasons
    rec_2 = records[1]
    assert rec_2["stage2"]["feasible"] is False
    assert "geometry failed" in rec_2["stage2"]["reasons"]

    # rec_1 and rec_3 should have received their own reports matching their indices
    rec_1 = records[0]
    rec_3 = records[2]

    assert rec_1["stage2"]["feasible"] is True
    assert rec_3["stage2"]["feasible"] is True

    # Check survivors list: should contain rec_1 and rec_3, NOT rec_2
    survivor_keys = [s["key"] for s in survivors]
    assert survivor_keys == ["rec_1", "rec_3"]


def test_stage3_matching_by_id_when_geometry_skipped(tmp_path: Path) -> None:
    """When a candidate fails geometry generation in Stage 3, subsequent

    candidates must still match their own Stage 3 result.
    """
    runner = EvolutionRunner(profile=_dummy_profile(), out_dir=str(tmp_path), seed=42)

    g1 = MagicMock(spec=HybridGenome)
    g1.to_dict.return_value = {"mock": 1}
    g2 = MagicMock(spec=HybridGenome)
    g2.to_dict.return_value = {"mock": 2}
    g3 = MagicMock(spec=HybridGenome)
    g3.to_dict.return_value = {"mock": 3}

    records = [
        {"key": "cand_1", "index": 11, "genome": {"g": 1}, "fitness": 10.0, "distance_m": 10.0},
        {"key": "cand_2", "index": 22, "genome": {"g": 2}, "fitness": 9.0, "distance_m": 9.0},
        {"key": "cand_3", "index": 33, "genome": {"g": 3}, "fitness": 8.0, "distance_m": 8.0},
    ]

    genome_map = {11: g1, 22: g2, 33: g3}
    runner._genome_of = lambda r: genome_map.get(r["index"])  # type: ignore

    dummy_geom = MagicMock()
    def mock_exact_geom(genome: Any) -> Any:
        if genome is g2:
            return None
        return dummy_geom

    runner._exact_geometry = mock_exact_geom  # type: ignore
    runner._aircraft_from_geometry = lambda geom: AircraftConfig()  # type: ignore

    # Mock stage 3 module
    import physics.ga2.stage3 as stage3_module
    runner._stage3_module = stage3_module

    # Mock run_stage3_parallel to return results with indices out of order or filtered
    def mock_run_stage3_parallel(requests: List[Stage3Request], profile: Any) -> List[Stage3Result]:
        # Return results reversed to test order independence
        results = []
        for req in reversed(requests):
            results.append(
                Stage3Result(
                    index=req.index,
                    status="converged",
                    lift_to_drag=4.5 if req.index == 33 else 5.0,
                    cl=0.4,
                    cd=0.08,
                    cm=0.0,
                    pitch_damping=-0.5,
                    flutter_margin=0.3,
                    separation_tolerance=0.2,
                    grid_cells=1024,
                    wall_time_s=0.1,
                )
            )
        return results

    stage3_module.run_stage3_parallel = mock_run_stage3_parallel  # type: ignore
    runner._optional_module = lambda name: stage3_module if name == "stage3" else None  # type: ignore

    resolved = runner._stage3_flow(records)

    # cand_1 and cand_3 must have received correct results matching their index
    assert records[0].get("stage3") is not None
    assert records[0]["stage3"]["index"] == 11
    assert records[0]["lift_to_drag"] == 5.0

    # cand_2 should have no stage3 results
    assert "stage3" not in records[1]

    assert records[2].get("stage3") is not None
    assert records[2]["stage3"]["index"] == 33
    assert records[2]["lift_to_drag"] == 4.5

    resolved_keys = {r["key"] for r in resolved}
    assert resolved_keys == {"cand_1", "cand_3"}


def test_stage1_matching_by_id_order_independent(tmp_path: Path) -> None:
    """Stage 1 results must match candidates by index, even if results are reordered."""
    runner = EvolutionRunner(profile=_dummy_profile(), out_dir=str(tmp_path), seed=42)

    g1 = MagicMock(spec=HybridGenome)
    g2 = MagicMock(spec=HybridGenome)

    records = [
        {"key": "flow_1", "index": 501, "genome": {"mock": 1}},
        {"key": "flow_2", "index": 502, "genome": {"mock": 2}},
    ]
    genome_map = {501: g1, 502: g2}
    runner._genome_of = lambda r: genome_map.get(r["index"])  # type: ignore
    runner._screening_aircraft = lambda r: AircraftConfig()  # type: ignore
    runner._launch_state = lambda a: [0.0] * 13  # type: ignore

    def mock_screen(cases: Any, repeats: int = 1) -> List[Stage1Result]:
        # Return in reversed order
        return [
            Stage1Result(
                index=502,
                status="landed",
                flight_time_s=3.0,
                distance_m=15.0,
                max_ld_ratio=4.0,
                stability=0.9,
                peak_stress_pa=100.0,
                peak_stress_index=0.2,
                peak_load_factor=1.5,
                peak_dynamic_pressure_pa=40.0,
                stall_events=0.0,
            ),
            Stage1Result(
                index=501,
                status="landed",
                flight_time_s=2.0,
                distance_m=10.0,
                max_ld_ratio=3.0,
                stability=0.8,
                peak_stress_pa=80.0,
                peak_stress_index=0.15,
                peak_load_factor=1.2,
                peak_dynamic_pressure_pa=35.0,
                stall_events=0.0,
            ),
        ]

    runner.screener.screen = mock_screen  # type: ignore
    runner._correct_screening = MagicMock()

    flown = runner._stage1_fly(records)

    assert len(flown) == 2
    # Verify records[0] (501) has distance_m == 10.0, NOT 15.0
    assert records[0]["distance_m"] == 10.0
    assert records[0]["index"] == 501
    # Verify records[1] (502) has distance_m == 15.0, NOT 10.0
    assert records[1]["distance_m"] == 15.0
    assert records[1]["index"] == 502
