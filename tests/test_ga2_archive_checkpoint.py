"""Tests of the archive, the Pareto front, the VRAM pool and the checkpoint.

The archive has to stay cheap even after thousands of generations, the
checkpoint has to be atomic (a crash may never leave a half written file)
and the hot migration has to carry a run from the laptop to the server
without losing the archive or the elites.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from physics.ga2.archive import (  # noqa: E402
    ArchiveEntry,
    MapElitesArchive,
    ParetoFront,
)
from physics.ga2.checkpoint import (  # noqa: E402
    CheckpointError,
    GenerationState,
    checkpoint_paths,
    load_checkpoint,
    migrate_state,
    save_checkpoint,
)
from physics.ga2.scaling import ScalingProfile  # noqa: E402
from physics.ga2.vram import VramPool  # noqa: E402


def _entry(index: int, fitness: float, wing_loading: float = 10.0) -> ArchiveEntry:
    return ArchiveEntry(
        key=f"individual-{index}",
        fitness=fitness,
        metrics={
            "wing_loading": wing_loading,
            "aspect_ratio": 4.0,
            "fold_count": 5.0,
        },
        payload={"genome": {"key": f"individual-{index}"}},
        generation=index,
    )


def test_the_archive_keeps_the_best_individual_of_a_cell() -> None:
    archive = MapElitesArchive(bins=(2, 2, 2), capacity=100)
    assert archive.add(_entry(0, 1.0, wing_loading=10.0)) is True
    assert archive.add(_entry(1, 5.0, wing_loading=10.0)) is True
    assert archive.add(_entry(2, 2.0, wing_loading=10.0)) is False
    assert len(archive) == 1
    assert archive.best() is not None
    assert archive.best().fitness == 5.0


def test_the_archive_reports_the_cell_of_a_behaviour() -> None:
    archive = MapElitesArchive(bins=(2, 2, 2), capacity=100)
    cell = archive.cell_index({"wing_loading": 10.0, "aspect_ratio": 4.0, "fold_count": 5.0})
    assert len(cell) == 3
    assert all(0 <= index < 2 for index in cell)


def test_the_archive_never_grows_beyond_its_capacity() -> None:
    archive = MapElitesArchive(bins=(8, 8, 4), capacity=500)
    for index in range(20_000):
        archive.add(_entry(index, float(index % 97), wing_loading=1.0 + index % 40.0))
    assert len(archive) <= 500


def test_the_archive_maintenance_stays_below_a_second_per_generation() -> None:
    archive = MapElitesArchive(bins=(8, 8, 4), capacity=20_000)
    worst = 0.0
    for generation in range(20):
        started = time.perf_counter()
        for index in range(2_000):
            archive.add(
                _entry(
                    generation * 2_000 + index,
                    float((index * 37) % 1_000) / 1_000.0,
                    wing_loading=1.0 + (index % 60),
                )
            )
        worst = max(worst, time.perf_counter() - started)
    assert worst < 1.0


def test_the_archive_round_trips_through_its_serialisation() -> None:
    archive = MapElitesArchive(bins=(4, 4, 4), capacity=1000)
    archive.add(_entry(0, 1.5))
    archive.add(_entry(1, 2.5, wing_loading=40.0))
    restored = MapElitesArchive.from_dict(archive.to_dict())
    assert len(restored) == len(archive)
    assert restored.best() is not None
    assert restored.best().key == archive.best().key


def test_the_pareto_front_keeps_the_non_dominated_points() -> None:
    front = ParetoFront(capacity=100)
    assert front.add("a", {"distance_m": 10.0, "max_ld_ratio": 5.0}) is True
    assert front.add("b", {"distance_m": 12.0, "max_ld_ratio": 4.0}) is True
    assert front.add("c", {"distance_m": 1.0, "max_ld_ratio": 1.0}) is False
    keys = {key for key, _ in front.front()}
    assert keys == {"a", "b"}


def test_the_pareto_front_is_capped() -> None:
    front = ParetoFront(capacity=10)
    for index in range(200):
        front.add(f"p{index}", {"distance_m": float(index), "max_ld_ratio": float(200 - index)})
    assert len(front) <= 10


def test_the_pareto_front_round_trips() -> None:
    front = ParetoFront(capacity=50)
    front.add("a", {"distance_m": 3.0, "max_ld_ratio": 2.0})
    restored = ParetoFront.from_dict(front.to_dict())
    assert len(restored) == len(front)


def test_the_vram_pool_reports_the_budget_and_hands_out_views(tmp_path: Path) -> None:
    pool = VramPool(device_index=0, fraction=0.85, total_bytes=8 * 1024 ** 3,
                    reserve_bytes=1 << 20)
    plan = pool.reserve()
    assert plan.budget_bytes == int(plan.total_bytes * 0.85)
    assert plan.reserved_bytes == 1 << 20
    first = pool.workspace("state", (64, 64), "float64")
    second = pool.workspace("summary", (128,), "float32")
    assert first.shape == (64, 64)
    assert second.shape == (128,)
    with pytest.raises(MemoryError):
        pool.workspace("huge", (1 << 30,), "float64")
    with pytest.raises(ValueError):
        pool.workspace("state", (32, 32), "float64")
    pool.release()


def test_the_checkpoint_is_written_atomically(tmp_path: Path) -> None:
    state = GenerationState(
        generation=7,
        profile=ScalingProfile.small_long().to_dict(),
        islands=[{"index": 0, "population": [{"key": "a", "genome": {}, "fitness": 1.0}]}],
        archive=MapElitesArchive().to_dict(),
        pareto=ParetoFront().to_dict(),
        best={"key": "a", "fitness": 1.0},
        statistics={"history": []},
        rng_state={"seed": 1},
    )
    path = tmp_path / "generation_000007.json"
    save_checkpoint(state, str(path))
    assert path.exists()
    assert not (tmp_path / "generation_000007.json.tmp").exists()
    restored = load_checkpoint(str(path))
    assert restored.generation == 7
    assert restored.best["key"] == "a"


def test_a_damaged_checkpoint_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CheckpointError):
        load_checkpoint(str(path))


def test_the_checkpoint_directory_is_listed_newest_first(tmp_path: Path) -> None:
    state = GenerationState(generation=1, profile=ScalingProfile.small_long().to_dict())
    save_checkpoint(state, str(tmp_path / "generation_000001.json"))
    save_checkpoint(state, str(tmp_path / "generation_000002.json"))
    paths = checkpoint_paths(str(tmp_path))
    assert len(paths) == 2
    assert "generation_000002" in paths[0]


def test_the_hot_migration_keeps_the_archive_and_the_elites() -> None:
    archive = MapElitesArchive(bins=(4, 4, 4), capacity=1000)
    archive.add(_entry(0, 3.0))
    pareto = ParetoFront(capacity=100)
    pareto.add("a", {"distance_m": 5.0, "max_ld_ratio": 3.0})
    state = GenerationState(
        generation=12,
        profile=ScalingProfile.small_long().to_dict(),
        islands=[
            {
                "index": 0,
                "population": [
                    {"key": f"k{index}", "genome": {}, "fitness": float(index)}
                    for index in range(4)
                ],
            }
        ],
        archive=archive.to_dict(),
        pareto=pareto.to_dict(),
        best={"key": "k3", "fitness": 3.0},
        statistics={},
        rng_state={"seed": 5},
    )
    cluster = ScalingProfile.cluster_20m(8, 80 * 1024 ** 3)
    migrated = migrate_state(state, cluster)
    assert migrated.generation == 12
    assert migrated.profile["tier"] == TIER_CLUSTER
    assert len(MapElitesArchive.from_dict(migrated.archive)) == 1
    assert len(ParetoFront.from_dict(migrated.pareto)) == 1
    total = sum(len(island["population"]) for island in migrated.islands)
    assert total == cluster.population_size
    assert migrated.statistics["migration"]["from_tier"] == "small_long"
    again = migrate_state(migrated, cluster)
    assert again.statistics.get("migration", {}).get("to_tier") == TIER_CLUSTER


TIER_CLUSTER = "cluster_20m"
