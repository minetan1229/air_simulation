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

