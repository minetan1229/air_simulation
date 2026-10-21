"""Hardware scaling profiles of the genetic search.

The search has to run unchanged on a laptop that spends a year on the problem
and on a multi GPU server that burns through 100 000 individuals per
generation.  The *physics* must not differ between the two -- otherwise the
result of a small scale run says nothing about the large scale one -- so the
only thing that changes is the *budget*: how many individuals are evaluated,
how many of them reach the expensive stages, how fine the fluid grid is and
how many workers share the GPUs.

This module owns that budget.  A :class:`ScalingProfile` is a frozen
description of the three tiers of the specification:

``small_long``
    one consumer GPU with about 8 GB of VRAM (the RTX 4060 reference), a
    population of 10 000, 5 million fluid cells and a run that has to survive
    months of continuous operation.
``workstation``
    one or two GPUs with 24..48 GB, a population of 30 000 and 25 million
    fluid cells.
``cluster_20m``
    four to eight GPUs with 48..94 GB each, a population of 100 000 and
    100..150 million fluid cells per individual.

The profile is selected automatically from the CUDA devices of the host, but it
can be pinned explicitly so that a run is reproducible on a machine with a
different GPU.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "GpuDevice",
    "ScalingProfile",
    "TIER_SMALL_LONG",
    "TIER_WORKSTATION",
    "TIER_CLUSTER_20M",
    "TIERS",
    "detect_devices",
    "detect_profile",
]


#: The three tiers of the specification.
TIER_SMALL_LONG = "small_long"
TIER_WORKSTATION = "workstation"
TIER_CLUSTER_20M = "cluster_20m"

#: Every tier name, in ascending order of ambition.
TIERS: Tuple[str, ...] = (TIER_SMALL_LONG, TIER_WORKSTATION, TIER_CLUSTER_20M)

#: Bytes in one gibibyte.
GIB = 1024 ** 3

#: The LBM lattice of the fluid solver: 19 populations, two buffers (the
#: ping-pong pair) and 4 bytes per lattice value.
LBM_BYTES_PER_CELL = 19 * 2 * 4

#: The fraction of the VRAM the solver may occupy.  The rest is left to the
#: driver so that a multi process server does not run into an out of memory
#: error when a second worker starts.
DEFAULT_VRAM_FRACTION = 0.85

#: A GPU with less than this much memory belongs to the "small" class.
SMALL_GPU_GIB = 12.0

#: A GPU with at least this much memory belongs to the "server" class.
SERVER_GPU_GIB = 64.0


@dataclass(frozen=True)
class GpuDevice:
    """One CUDA device of the host."""

    index: int
    name: str
    total_vram_bytes: int

    @property
    def vram_gib(self) -> float:
        """The VRAM of the device in gibibytes."""
        return self.total_vram_bytes / GIB

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": int(self.index),
            "name": str(self.name),
            "total_vram_bytes": int(self.total_vram_bytes),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "GpuDevice":
        return cls(
            index=int(payload.get("index", 0)),
            name=str(payload.get("name", "unknown")),
            total_vram_bytes=int(payload.get("total_vram_bytes", 0)),
        )


def detect_devices() -> List[GpuDevice]:
    """List the CUDA devices of the host.

    The probe never raises: a host without CuPy (or without a driver) simply
    has no devices, and the caller falls back to the CPU profile.
    """
    try:
        import cupy as cp
    except Exception:  # pragma: no cover - depends on the host
        return []
    try:
        count = int(cp.cuda.runtime.getDeviceCount())
    except Exception:  # pragma: no cover - depends on the driver
        return []
    devices: List[GpuDevice] = []
    for index in range(count):
        try:
            props = cp.cuda.runtime.getDeviceProperties(index)
        except Exception:  # pragma: no cover - depends on the driver
            continue
        raw_name = props["name"] if isinstance(props, dict) else getattr(props, "name", "GPU")
        name = raw_name.decode("utf-8", "replace") if isinstance(raw_name, bytes) else str(raw_name)
        raw_mem = (
            props.get("totalGlobalMem")
            if isinstance(props, dict)
            else getattr(props, "totalGlobalMem", 0)
        )
        devices.append(
            GpuDevice(
                index=index,
                name=name,
                total_vram_bytes=int(raw_mem or 0),
            )
        )
    return devices


@dataclass(frozen=True)
class ScalingProfile:
    """The budget of one hardware class.

    Every field of the specification table is represented here, and every
    consumer of the search (the runner, the stage modules, the checkpoint
    writer) reads it from this object instead of from a hard coded number.
    """

    tier: str
    device_count: int
    device_vram_bytes: int
    population_size: int
    target_generations: int
    stage1_chunk_width: int
    stage1_chunk_repeats: int
    stage2_passthrough: int
    stage3_passthrough: int
    stage3_grid_cells: int
    stage3_scheme: str
    stage3_workers: int
    multi_gpu_strategy: str
    islands: int
    stage1_duration_s: float
    stage1_dt_max: float
    stage1_max_steps: int
    stage2_load_cases: int
    stage3_transition_steps: int
    stage3_glide_steps: int
    vram_pool_fraction: float = DEFAULT_VRAM_FRACTION
    seed_fraction_archetype: float = 0.02
    seed_fraction_mutant: float = 0.08
    seed_fraction_random: float = 0.90
    min_fold_steps: int = 3
    max_fold_steps: int = 8
    archive_capacity: int = 20000

    # ------------------------------------------------------------------
    # Constructors of the three tiers
    # ------------------------------------------------------------------
    @classmethod
    def small_long(cls, device_count: int = 1, device_vram_bytes: int = 8 * GIB) -> "ScalingProfile":
        """The consumer GPU profile: months of continuous operation."""
        return cls(
            tier=TIER_SMALL_LONG,
            device_count=max(1, int(device_count)),
            device_vram_bytes=int(device_vram_bytes),
            population_size=10_000,
            target_generations=3_000,
            stage1_chunk_width=5_000,
            stage1_chunk_repeats=2,
            stage2_passthrough=500,
            stage3_passthrough=10,
            stage3_grid_cells=6_000_000,
            stage3_scheme="w_mles",
            stage3_workers=1,
            multi_gpu_strategy="sequential_single_gpu",
            islands=4,
            stage1_duration_s=4.0,
            stage1_dt_max=2.5e-3,
            stage1_max_steps=1_500,
            stage2_load_cases=2,
            stage3_transition_steps=2_000,
            stage3_glide_steps=6_000,
            vram_pool_fraction=DEFAULT_VRAM_FRACTION,
            archive_capacity=20_000,
        )

    @classmethod
    def workstation(cls, device_count: int = 1, device_vram_bytes: int = 24 * GIB) -> "ScalingProfile":
        """The single workstation profile: one or two mid range GPUs."""
        return cls(
            tier=TIER_WORKSTATION,
            device_count=max(1, int(device_count)),
            device_vram_bytes=int(device_vram_bytes),
            population_size=30_000,
            target_generations=3_000,
            stage1_chunk_width=15_000,
            stage1_chunk_repeats=2,
            stage2_passthrough=1_500,
            stage3_passthrough=25,
            stage3_grid_cells=25_000_000,
            stage3_scheme="les_dynamic_fsi",
            stage3_workers=max(1, int(device_count)),
            multi_gpu_strategy="one_gpu_per_individual",
            islands=6,
            stage1_duration_s=5.0,
            stage1_dt_max=2.0e-3,
            stage1_max_steps=2_500,
            stage2_load_cases=3,
            stage3_transition_steps=4_000,
            stage3_glide_steps=12_000,
            vram_pool_fraction=DEFAULT_VRAM_FRACTION,
            archive_capacity=40_000,
        )

    @classmethod
    def cluster_20m(cls, device_count: int = 4, device_vram_bytes: int = 48 * GIB) -> "ScalingProfile":
        """The production profile: four to eight server GPUs."""
        workers = max(1, min(8, int(device_count)))
        return cls(
            tier=TIER_CLUSTER_20M,
            device_count=workers,
            device_vram_bytes=int(device_vram_bytes),
            population_size=100_000,
            target_generations=5_000,
            stage1_chunk_width=25_000,
            stage1_chunk_repeats=4,
            stage2_passthrough=5_000,
            stage3_passthrough=80,
            stage3_grid_cells=100_000_000,
            stage3_scheme="local_dns_bidirectional_fsi",
            stage3_workers=workers,
            multi_gpu_strategy="one_gpu_per_individual",
            islands=8,
            stage1_duration_s=6.0,
            stage1_dt_max=1.5e-3,
            stage1_max_steps=4_000,
            stage2_load_cases=4,
            stage3_transition_steps=8_000,
            stage3_glide_steps=20_000,
            vram_pool_fraction=DEFAULT_VRAM_FRACTION,
            archive_capacity=100_000,
        )

    @classmethod
    def for_tier(
        cls,
        tier: str,
        device_count: int = 1,
        device_vram_bytes: int = 8 * GIB,
    ) -> "ScalingProfile":
        """Build the profile of one named tier."""
        if tier == TIER_SMALL_LONG:
            return cls.small_long(device_count, device_vram_bytes)
        if tier == TIER_WORKSTATION:
            return cls.workstation(device_count, device_vram_bytes)
        if tier == TIER_CLUSTER_20M:
            return cls.cluster_20m(device_count, device_vram_bytes)
        raise ValueError(f"unknown tier '{tier}'; expected one of {', '.join(TIERS)}")

    # ------------------------------------------------------------------
    # Automatic detection
    # ------------------------------------------------------------------
    @classmethod
    def detect(cls, devices: Optional[Sequence[GpuDevice]] = None) -> "ScalingProfile":
        """Pick the profile that matches the CUDA devices of the host.

        The rules are deliberately simple and documented, because a run has to
        be reproducible: three or more GPUs always mean the cluster profile,
        two GPUs only do when they are server class, and a single GPU is a
        workstation as soon as it holds at least 12 GiB.
        """
        found = list(devices) if devices is not None else detect_devices()
        if not found:
            return cls.small_long(1, 0)
        count = len(found)
        largest = max(device.total_vram_bytes for device in found)
        if count >= 3:
            return cls.cluster_20m(count, largest)
        if count == 2:
            if largest >= int(SERVER_GPU_GIB * GIB):
                return cls.cluster_20m(count, largest)
            return cls.workstation(count, largest)
        if largest >= int(SMALL_GPU_GIB * GIB):
            return cls.workstation(count, largest)
        return cls.small_long(count, largest)

    # ------------------------------------------------------------------
    # Derived quantities
    # ------------------------------------------------------------------
    @property
    def vram_budget_bytes(self) -> int:
        """The number of VRAM bytes the solver may use."""
        return int(self.device_vram_bytes * self.vram_pool_fraction)

    @property
    def stage3_workers_available(self) -> int:
        """How many DNS/FSI individuals may run at the same time."""
        return max(1, min(int(self.stage3_workers), max(1, int(self.device_count))))

    def max_grid_cells(self) -> int:
        """The largest fluid grid that fits into the VRAM budget.

        The LBM needs 19 populations in two buffers, so the grid size is
        bounded by the memory and not by the wish of the operator.  The
        cluster tier asks for 100 million cells; a smaller card simply gets a
        smaller grid instead of an out of memory crash.
        """
        budget = self.vram_budget_bytes
        if budget <= 0:
            return 0
        usable = int(budget * 0.6)
        return max(0, usable // LBM_BYTES_PER_CELL)

    def effective_grid_cells(self) -> int:
        """The grid size the solver may really use."""
        return max(0, min(int(self.stage3_grid_cells), self.max_grid_cells()))

    def stage1_chunks(self, population: Optional[int] = None) -> int:
        """How many chunks the population is split into for stage 1."""
        size = int(population if population is not None else self.population_size)
        width = max(1, int(self.stage1_chunk_width))
        return max(1, int(math.ceil(size / width)))

    def stage1_chunk_plan(self, population: Optional[int] = None) -> List[Tuple[int, int]]:
        """The ``(offset, size)`` plan of the stage 1 chunks.

        The plan covers the whole population exactly once; the number of
        repeats of the specification is the number of independent passes over
        the population (the perturbation ensemble), and every pass uses the
        same chunk boundaries so that the memory footprint never changes.
        """
        size = int(population if population is not None else self.population_size)
        width = max(1, int(self.stage1_chunk_width))
        plan: List[Tuple[int, int]] = []
        offset = 0
        while offset < size:
            count = min(width, size - offset)
            plan.append((offset, count))
            offset += count
        return plan

    def stage2_batch_size(self) -> int:
        """How many individuals the static FSI stage evaluates at once."""
        return max(1, int(self.stage2_passthrough))

    def stage3_batch_size(self) -> int:
        """How many individuals the DNS/FSI stage evaluates at once."""
        return max(1, int(self.stage3_passthrough))

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """A JSON serialisable copy of the profile."""
        return {spec.name: getattr(self, spec.name) for spec in dataclasses.fields(self)}

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ScalingProfile":
        """Rebuild a profile from its serialised form."""
        data = dict(payload or {})
        known = {spec.name for spec in dataclasses.fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"unknown scaling profile key(s): {', '.join(unknown)}")
        return cls(**data)  # type: ignore[arg-type]

    def with_overrides(self, **overrides: Any) -> "ScalingProfile":
        """A copy of the profile with a few fields replaced."""
        data = self.to_dict()
        for key, value in overrides.items():
            if value is None:
                continue
            if key not in data:
                raise ValueError(f"unknown scaling profile key '{key}'")
            data[key] = value
        return ScalingProfile.from_dict(data)

    def validate(self) -> "ScalingProfile":
        """Check the internal consistency of the profile."""
        if self.tier not in TIERS:
            raise ValueError(f"unknown tier '{self.tier}'")
        if self.population_size < 2:
            raise ValueError("'population_size' must be at least 2")
        if self.stage1_chunk_width < 1:
            raise ValueError("'stage1_chunk_width' must be at least 1")
        if self.stage1_chunk_repeats < 1:
            raise ValueError("'stage1_chunk_repeats' must be at least 1")
        if self.stage2_passthrough < 0:
            raise ValueError("'stage2_passthrough' must not be negative")
        if self.stage3_passthrough < 0:
            raise ValueError("'stage3_passthrough' must not be negative")
        if self.islands < 1:
            raise ValueError("'islands' must be at least 1")
        if not (0.0 < self.vram_pool_fraction <= 1.0):
            raise ValueError("'vram_pool_fraction' must be within (0, 1]")
        if self.min_fold_steps < 1 or self.max_fold_steps < self.min_fold_steps:
            raise ValueError("the fold step range is empty")
        total = (
            self.seed_fraction_archetype
            + self.seed_fraction_mutant
            + self.seed_fraction_random
        )
        if abs(total - 1.0) > 1.0e-6:
            raise ValueError("the seed fractions must add up to one")
        return self


def detect_profile(devices: Optional[Sequence[GpuDevice]] = None) -> ScalingProfile:
    """Detect the CUDA devices and return the matching profile."""
    return ScalingProfile.detect(devices).validate()
