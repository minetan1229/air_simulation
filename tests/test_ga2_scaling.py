"""Tests of the hardware scaling profiles of the second generation search.

The profile is the contract between the specification and the code: every
number of the three tier table of the specification has to appear in the
profile, and the automatic detection has to pick the right tier for the
hardware of the host.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from physics.ga2.scaling import (  # noqa: E402
    GIB,
    TIER_CLUSTER_20M,
    TIER_SMALL_LONG,
    TIER_WORKSTATION,
    GpuDevice,
    ScalingProfile,
    detect_devices,
    detect_profile,
)


def test_small_long_matches_the_specification_table() -> None:
    profile = ScalingProfile.small_long(1, 8 * GIB)
    assert profile.tier == TIER_SMALL_LONG
    assert profile.population_size == 10_000
    assert profile.stage1_chunk_width == 5_000
    assert profile.stage1_chunk_repeats == 2
    assert profile.stage2_passthrough == 500
    assert profile.stage3_passthrough == 10
    assert 5.0e6 <= profile.stage3_grid_cells <= 8.0e6
    assert profile.stage3_scheme == "w_mles"
    assert profile.multi_gpu_strategy == "sequential_single_gpu"


def test_workstation_matches_the_specification_table() -> None:
    profile = ScalingProfile.workstation(2, 32 * GIB)
    assert profile.tier == TIER_WORKSTATION
    assert profile.population_size == 30_000
    assert profile.stage1_chunk_width == 15_000
    assert profile.stage2_passthrough == 1_500
    assert profile.stage3_passthrough == 25
    assert profile.stage3_grid_cells == 25_000_000
    assert profile.stage3_workers == 2


def test_cluster_matches_the_specification_table() -> None:
    profile = ScalingProfile.cluster_20m(8, 80 * GIB)
    assert profile.tier == TIER_CLUSTER_20M
    assert profile.population_size == 100_000
    assert profile.stage1_chunk_width == 25_000
    assert profile.stage1_chunk_repeats == 4
    assert profile.stage2_passthrough == 5_000
    assert 80 <= profile.stage3_passthrough <= 100
    assert 1.0e8 <= profile.stage3_grid_cells <= 1.5e8
    assert profile.stage3_workers == 8
    assert profile.multi_gpu_strategy == "one_gpu_per_individual"


def test_detection_picks_the_small_tier_for_a_laptop_gpu() -> None:
    devices = [GpuDevice(0, "NVIDIA GeForce RTX 4060 Laptop GPU", 8 * GIB)]
    profile = ScalingProfile.detect(devices)
    assert profile.tier == TIER_SMALL_LONG
    assert profile.population_size == 10_000


def test_detection_picks_the_workstation_tier_for_a_single_big_gpu() -> None:
    devices = [GpuDevice(0, "RTX 4090", 24 * GIB)]
    assert ScalingProfile.detect(devices).tier == TIER_WORKSTATION


def test_detection_picks_the_cluster_tier_for_four_server_gpus() -> None:
    devices = [GpuDevice(index, "A100", 80 * GIB) for index in range(4)]
    profile = ScalingProfile.detect(devices)
    assert profile.tier == TIER_CLUSTER_20M
    assert profile.stage3_workers == 4


def test_detection_without_a_gpu_falls_back_to_the_small_tier() -> None:
    assert ScalingProfile.detect([]).tier == TIER_SMALL_LONG
    assert detect_profile([]).tier == TIER_SMALL_LONG


def test_the_chunk_plan_covers_the_population_exactly_once() -> None:
    profile = ScalingProfile.small_long()
    plan = profile.stage1_chunk_plan(10_000)
    assert len(plan) == 2
    assert plan[0] == (0, 5_000)
    assert plan[1] == (5_000, 5_000)
    covered = sum(size for _, size in plan)
    assert covered == profile.population_size


def test_the_chunk_plan_of_an_uneven_population_has_no_gap() -> None:
    profile = ScalingProfile.small_long()
    plan = profile.stage1_chunk_plan(12_345)
    offset = 0
    for start, size in plan:
        assert start == offset
        offset += size
    assert offset == 12_345


def test_the_vram_budget_is_eighty_five_percent() -> None:
    profile = ScalingProfile.small_long(1, 8 * GIB)
    assert profile.vram_pool_fraction == 0.85
    assert profile.vram_budget_bytes == int(8 * GIB * 0.85)


def test_the_grid_is_capped_by_the_memory_of_the_card() -> None:
    small = ScalingProfile.small_long(1, 8 * GIB)
    assert small.effective_grid_cells() <= small.max_grid_cells()
    assert small.effective_grid_cells() < small.stage3_grid_cells + 1
    huge = ScalingProfile.cluster_20m(8, 80 * GIB)
    assert huge.max_grid_cells() >= 100_000_000


def test_the_profile_round_trips_through_its_serialisation() -> None:
    profile = ScalingProfile.workstation(2, 24 * GIB)
    restored = ScalingProfile.from_dict(profile.to_dict())
    assert restored == profile


def test_an_unknown_key_is_rejected() -> None:
    profile = ScalingProfile.small_long()
    payload = profile.to_dict()
    payload["nonsense"] = 1
    with pytest.raises(ValueError):
        ScalingProfile.from_dict(payload)


def test_the_validation_catches_an_impossible_profile() -> None:
    profile = ScalingProfile.small_long()
    with pytest.raises(ValueError):
        profile.with_overrides(population_size=1).validate()
    with pytest.raises(ValueError):
        profile.with_overrides(seed_fraction_archetype=0.5, seed_fraction_mutant=0.1,
                               seed_fraction_random=0.1).validate()
    with pytest.raises(ValueError):
        profile.with_overrides(vram_pool_fraction=1.5).validate()


def test_the_overrides_keep_the_profile_valid() -> None:
    profile = ScalingProfile.small_long().with_overrides(population_size=64)
    assert profile.validate().population_size == 64
    assert profile.stage1_chunk_width == 5_000
