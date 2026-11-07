"""Tests of the generation loop of the second generation search.

The loop is tested end to end: a generation is run with the real stages, the
archive and the Pareto front are filled, the checkpoint is written, and the
checkpoint is continued on a bigger profile (the hot migration of the
specification).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from physics.ga2.runner import (  # noqa: E402
    EvolutionRunner,
    default_objective,
    select_high_fidelity_candidates,
)
from physics.ga2.scaling import ScalingProfile  # noqa: E402
from physics.ga2 import stage2 as stage2_module  # noqa: E402
from physics.ga2 import stage3 as stage3_module  # noqa: E402


def _profile(population: int = 16) -> ScalingProfile:
    return ScalingProfile.small_long(1, 8 * 1024 ** 3).with_overrides(
        population_size=population,
        stage1_chunk_width=max(4, population // 2),
        stage1_chunk_repeats=1,
        stage2_passthrough=4,
        stage3_passthrough=1,
        stage1_max_steps=120,
        stage1_duration_s=1.0,
        stage3_grid_cells=20_000,
        stage3_transition_steps=10,
        stage3_glide_steps=20,
        islands=2,
    )


def test_the_objective_rewards_a_long_and_stable_flight() -> None:
    good = default_objective(
        {"distance_m": 10.0, "max_ld_ratio": 4.0, "stability": 1.0, "peak_stress_index": 0.1}
    )
    bad = default_objective(
        {"distance_m": 10.0, "max_ld_ratio": 4.0, "stability": 0.0, "peak_stress_index": 2.0}
    )
    assert good > bad
    assert default_objective({"distance_m": 0.0}) == 0.0


def test_a_generation_fills_the_archive_and_writes_the_checkpoint(tmp_path: Path) -> None:
    runner = EvolutionRunner(profile=_profile(), out_dir=str(tmp_path), seed=5)
    report = runner.run_generation()
    assert report.population == 16
    assert report.stage0_passed <= report.population
    assert report.stage1_passed <= report.stage0_passed
    assert report.archive_size >= 1
    assert report.elapsed_s > 0.0
    checkpoints = list(tmp_path.glob("generation_*.json"))
    assert checkpoints, "the generation must leave a checkpoint"
    assert runner.archive.best() is not None
    assert runner.pareto.front()


def test_the_second_generation_continues_the_first(tmp_path: Path) -> None:
    runner = EvolutionRunner(profile=_profile(), out_dir=str(tmp_path), seed=6)
    first = runner.run_generation()
    second = runner.run_generation()
    assert second.generation == first.generation + 1
    assert len(runner.islands) == 2
    assert sum(len(island) for island in runner.islands) == 16


def test_a_checkpoint_of_the_laptop_continues_on_the_server(tmp_path: Path) -> None:
    runner = EvolutionRunner(profile=_profile(), out_dir=str(tmp_path), seed=7)
    runner.run_generation()
    archive_size = len(runner.archive)

    cluster = ScalingProfile.cluster_20m(8, 80 * 1024 ** 3).with_overrides(
        stage1_chunk_width=25_000,
        stage2_passthrough=5_000,
        stage3_passthrough=80,
    )
    migrated = EvolutionRunner(profile=cluster, out_dir=str(tmp_path), seed=7)
    migrated.load(str(tmp_path / "generation_000001.json"))
    assert migrated.generation == 1
    assert len(migrated.archive) == archive_size
    total = sum(len(island) for island in migrated.islands)
    assert total == cluster.population_size
    assert migrated.profile.tier == "cluster_20m"


def test_the_screener_reports_the_backend_it_used(tmp_path: Path) -> None:
    runner = EvolutionRunner(profile=_profile(), out_dir=str(tmp_path), seed=8)
    described = runner.describe()
    assert described["screener"]["backend"] in ("cupy", "numpy")
    assert described["profile"]["tier"] == "small_long"
    assert "vram" in described


def test_select_high_fidelity_candidates_empty_and_passthrough() -> None:
    assert select_high_fidelity_candidates([], 5) == []
    assert select_high_fidelity_candidates([{"fitness": 1.0}], 0) == []
    single = [{"fitness": 1.0, "distance_m": 5.0}]
    assert select_high_fidelity_candidates(single, 3) == single


def test_select_high_fidelity_candidates_preserves_pareto_and_diversity() -> None:
    # Construct candidates:
    # 3 candidates with high distance but low L/D and low stability
    # 3 candidates with moderate distance but high L/D, high stability, and good stress headroom
    candidates = [
        {"key": "dist_1", "fitness": 10.0, "distance_m": 12.0, "max_ld_ratio": 2.0, "stability": 0.2, "peak_stress_index": 0.8},
        {"key": "dist_2", "fitness": 9.5, "distance_m": 11.5, "max_ld_ratio": 2.1, "stability": 0.2, "peak_stress_index": 0.8},
        {"key": "dist_3", "fitness": 9.0, "distance_m": 11.0, "max_ld_ratio": 2.0, "stability": 0.3, "peak_stress_index": 0.7},
        {"key": "dist_4", "fitness": 8.5, "distance_m": 10.5, "max_ld_ratio": 2.2, "stability": 0.2, "peak_stress_index": 0.8},
        {"key": "pareto_ld", "fitness": 7.0, "distance_m": 8.0, "max_ld_ratio": 15.0, "stability": 0.5, "peak_stress_index": 0.2},
        {"key": "pareto_stab", "fitness": 6.8, "distance_m": 7.5, "max_ld_ratio": 6.0, "stability": 0.99, "peak_stress_index": 0.1},
        {"key": "pareto_stress", "fitness": 6.5, "distance_m": 7.0, "max_ld_ratio": 5.0, "stability": 0.8, "peak_stress_index": 0.05},
    ]

    # Select top 4 out of 7.
    # Under pure distance sorting, only dist_1, dist_2, dist_3, dist_4 would be selected.
    # Under select_high_fidelity_candidates (limit=4, 50% primary=2, 50% pareto/diversity=2):
    # Primary selects top 2: dist_1, dist_2.
    # Pareto/diversity selects non-dominated candidates: pareto_ld, pareto_stab, etc.
    selected = select_high_fidelity_candidates(candidates, limit=4, primary_ratio=0.5)
    assert len(selected) == 4
    keys = {c["key"] for c in selected}
    # dist_1 (highest fitness/distance) must be included
    assert "dist_1" in keys
    assert "dist_2" in keys
    # And at least one of the specialized Pareto/diverse solutions must be included instead of pure distance truncation
    assert ("pareto_ld" in keys) or ("pareto_stab" in keys) or ("pareto_stress" in keys)
    # dist_4 should not crowd out pareto solutions
    assert "dist_4" not in keys


def _make_stage2_report(
    index: int,
    feasible: bool,
    buckling_margin: float = 2.0,
    tip_deflection: float = 0.01,
    pitch_margin: float = 0.05,
    reasons: Optional[List[str]] = None,
) -> stage2_module.Stage2Report:
    """Helper to construct a Stage2Report for testing."""
    return stage2_module.Stage2Report(
        index=index,
        feasible=feasible,
        buckling_margin=buckling_margin,
        max_tip_deflection_m=tip_deflection,
        pitch_stability_margin=pitch_margin,
        reasons=reasons or [],
    )


def _make_stage3_result(
    index: int,
    lift_to_drag: float = 15.0,
    cl: float = 1.2,
    cd: float = 0.08,
    cm: float = -0.05,
    status: str = "ok",
) -> stage3_module.Stage3Result:
    """Helper to construct a Stage3Result for testing."""
    return stage3_module.Stage3Result(
        index=index,
        status=status,
        lift_to_drag=lift_to_drag,
        cl=cl,
        cd=cd,
        cm=cm,
        pitch_damping=0.1,
        flutter_margin=0.5,
        separation_tolerance=0.9,
        grid_cells=20000,
        wall_time_s=1.0,
    )


def test_stage2_failure_penalizes_fitness_and_stress() -> None:
    """Stage 2 structural failures must set fitness to 0.0 and apply stress penalty."""
    # Directly test the penalty logic by calling the internal method with mocked screen
    # Use empty string for out_dir to disable surrogate database
    runner = EvolutionRunner(profile=_profile(), out_dir="", seed=42)

    # Create mock records that passed stage 1
    records = [
        {"key": "good_1", "index": 1, "genome": {}, "distance_m": 10.0, "max_ld_ratio": 8.0, "stability": 0.8, "peak_stress_index": 0.2, "fitness": 12.0},
        {"key": "bad_1", "index": 2, "genome": {}, "distance_m": 10.0, "max_ld_ratio": 8.0, "stability": 0.8, "peak_stress_index": 0.2, "fitness": 12.0},
        {"key": "bad_2", "index": 3, "genome": {}, "distance_m": 10.0, "max_ld_ratio": 8.0, "stability": 0.8, "peak_stress_index": 0.2, "fitness": 12.0},
    ]

    # Initialize stage2
    runner._stage2_module = stage2_module
    runner._stage2 = stage2_module.StaticFsiScreener(paper_gsm=100.0)

    # Mock stage2 screen to return one feasible and two infeasible
    def mock_screen(cases):
        return [
            _make_stage2_report(int(cases[0].index), feasible=True),
            _make_stage2_report(int(cases[1].index), feasible=False, reasons=["wing panel buckles under the load"]),
            _make_stage2_report(int(cases[2].index), feasible=False, reasons=["pitch stability destroyed by the deflection"]),
        ]

    runner._stage2.screen = mock_screen  # type: ignore[assignment]

    # Mock exact geometry
    def mock_exact_geometry(genome):
        class MockGeometry:
            wing_area_m2 = 0.02
            span_m = 0.3
            mass_kg = 0.01
            chord_m = 0.1
        return MockGeometry()

    runner._exact_geometry = mock_exact_geometry  # type: ignore[assignment]

    # Mock aircraft from geometry
    from physics.config_schema import AircraftConfig
    def mock_aircraft_from_geometry(geometry):
        config = AircraftConfig()
        config.wing_area_m2 = 0.02
        config.wingspan_m = 0.3
        config.mass_kg = 0.01
        config.aspect_ratio = 4.5
        config.paper_gsm = 100.0
        config.roll_inertia_kgm2 = 1e-6
        config.pitch_inertia_kgm2 = 1e-6
        config.yaw_inertia_kgm2 = 1e-6
        return config

    runner._aircraft_from_geometry = mock_aircraft_from_geometry  # type: ignore[assignment]
    runner._dynamic_pressure = lambda r: 50.0  # type: ignore[assignment]

    # Mock reference refly to do nothing
    runner._reference_refly = lambda survivors: None  # type: ignore[assignment]

    # Ensure all 3 are selected (passthrough=4 > 3)
    survivors = runner._stage2_structure(records)

    # Check that only the feasible one survives
    assert len(survivors) == 1
    assert survivors[0]["key"] == "good_1"

    # Check that failed records got penalized (they are the same objects in records list)
    bad_1 = next(r for r in records if r["key"] == "bad_1")
    bad_2 = next(r for r in records if r["key"] == "bad_2")
    assert bad_1["fitness"] == 0.0, f"bad_1 fitness should be 0.0, got {bad_1['fitness']}"
    assert float(bad_1["peak_stress_index"]) >= 2.0, f"bad_1 peak_stress_index should be >= 2.0, got {bad_1['peak_stress_index']}"  # type: ignore[arg-type]
    assert bad_2["fitness"] == 0.0, f"bad_2 fitness should be 0.0, got {bad_2['fitness']}"
    assert float(bad_2["peak_stress_index"]) >= 2.0, f"bad_2 peak_stress_index should be >= 2.0, got {bad_2['peak_stress_index']}"  # type: ignore[arg-type]

    # Check that good record keeps its fitness
    good_1 = next(r for r in records if r["key"] == "good_1")
    assert float(good_1["fitness"]) > 0.0  # type: ignore[arg-type]


def test_stage3_updates_max_ld_ratio_and_fitness() -> None:
    """Stage 3 high-fidelity results must update max_ld_ratio and recalculate fitness."""
    # Use profile with stage3_passthrough=2 to select both candidates
    profile = _profile().with_overrides(stage3_passthrough=2)
    # Use empty string for out_dir to disable surrogate database
    runner = EvolutionRunner(profile=profile, out_dir="", seed=43)

    # Create mock records that passed stage 2
    records = [
        {"key": "candidate_1", "index": 1, "genome": {}, "distance_m": 10.0, "max_ld_ratio": 8.0, "stability": 0.8, "peak_stress_index": 0.2, "fitness": 12.0, "screening_cl": 1.0, "screening_cd": 0.1},
        {"key": "candidate_2", "index": 2, "genome": {}, "distance_m": 10.0, "max_ld_ratio": 8.0, "stability": 0.8, "peak_stress_index": 0.2, "fitness": 12.0, "screening_cl": 1.0, "screening_cd": 0.1},
    ]

    # Initialize stage3
    runner._stage3_module = stage3_module

    # Mock stage3 to return high-fidelity results
    def mock_run_stage3_parallel(requests, profile):
        return [
            _make_stage3_result(int(requests[0].index), lift_to_drag=20.0, cl=1.5, cd=0.075, cm=-0.06),
            _make_stage3_result(int(requests[1].index), lift_to_drag=18.0, cl=1.4, cd=0.078, cm=-0.05),
        ]

    stage3_module.run_stage3_parallel = mock_run_stage3_parallel  # type: ignore[assignment]

    # Mock exact geometry and aircraft
    def mock_exact_geometry(genome):
        class MockGeometry:
            wing_area_m2 = 0.02
            span_m = 0.3
            mass_kg = 0.01
            chord_m = 0.1
        return MockGeometry()

    runner._exact_geometry = mock_exact_geometry  # type: ignore[assignment]

    from physics.config_schema import AircraftConfig
    def mock_aircraft_from_geometry(geometry):
        config = AircraftConfig()
        config.wing_area_m2 = 0.02
        config.wingspan_m = 0.3
        config.mass_kg = 0.01
        config.aspect_ratio = 4.5
        config.paper_gsm = 100.0
        config.roll_inertia_kgm2 = 1e-6
        config.pitch_inertia_kgm2 = 1e-6
        config.yaw_inertia_kgm2 = 1e-6
        return config

    runner._aircraft_from_geometry = mock_aircraft_from_geometry  # type: ignore[assignment]

    # Enable stage3
    runner.stage3_enabled = True

    resolved = runner._stage3_flow(records)

    # Both should be resolved with updated values
    assert len(resolved) == 2
    for record in resolved:
        assert record["max_ld_ratio"] > 8.0, f"max_ld_ratio should be updated from stage3, got {record['max_ld_ratio']}"
        assert record["lift_to_drag"] == record["max_ld_ratio"]
        assert record["cl"] != 1.0
        assert record["cd"] != 0.1
        assert "cm" in record
        # Fitness should be recalculated with new L/D
        expected_fitness = default_objective(record)
        assert abs(record["fitness"] - expected_fitness) < 1e-6, f"fitness mismatch: {record['fitness']} vs {expected_fitness}"

