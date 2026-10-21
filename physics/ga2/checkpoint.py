"""Atomic checkpoints and the hot migration of the second generation search.

A run of the second generation search is long: it has to survive months of
continuous operation, and it may be started on a laptop and finished on a
cluster.  Two properties of the checkpoint system make that possible.

*Atomicity.*  :func:`save_checkpoint` never writes into the file the reader
uses.  The payload goes into ``<path>.tmp`` in the same directory, the
handle is flushed and synchronised, and only then is the temporary file
renamed onto the final path with :func:`os.replace`, which is atomic on
every supported file system.  A crash therefore leaves either the old
checkpoint or the new one, never a half written file.

*Hot migration.*  :func:`migrate_state` takes a checkpoint that was written
by one hardware class and prepares it for another one.  The archive, the
Pareto front, the elites of every island, the best individual, the statistics
and the state of the random number generator survive the move; the profile
is replaced by the new one and the island populations are resized to the
population size of the new profile, padded with the elites of their island.

The file format is JSON.  A payload that grows large is stored gzip
compressed; the compression is detected by the gzip magic header of the
file, so the reader accepts a plain and a compressed checkpoint alike and
the name of the file is the choice of the caller.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .scaling import ScalingProfile

__all__ = [
    "GenerationState",
    "CheckpointError",
    "save_checkpoint",
    "load_checkpoint",
    "migrate_state",
    "checkpoint_paths",
    "CHECKPOINT_VERSION",
    "GZIP_THRESHOLD_BYTES",
]


#: The version of the checkpoint format written by this module.
CHECKPOINT_VERSION = 1

#: A payload of at least this size is stored gzip compressed.  The archive of
#: a cluster run holds a hundred thousand individuals and passes that mark
#: after a few generations, so the threshold keeps the checkpoints small.
GZIP_THRESHOLD_BYTES = 1 << 20

#: The first two bytes of a gzip file.
GZIP_MAGIC = b"\x1f\x8b"

#: The keys under which an island may keep its population.
_POPULATION_KEYS: Tuple[str, ...] = ("population", "individuals", "members")

#: The keys under which an island may keep its elites.
_ELITE_KEYS: Tuple[str, ...] = ("elites", "elite", "elite_population")


class CheckpointError(ValueError):
    """Raised when a checkpoint cannot be read.

    The error is a :class:`ValueError` because a damaged checkpoint is a
    damaged *value*, not a missing file: a file that is not there raises the
    usual :class:`FileNotFoundError`.
    """


def _utc_now_iso() -> str:
    """The current time as an ISO-8601 timestamp in UTC."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class GenerationState:
    """The complete state of the search at the end of one generation.

    The state carries everything a run needs to continue: the profile that
    describes the hardware budget, the islands with their populations, the
    archive, the Pareto front, the best individual, the statistics of the
    run, the state of the random number generator and the surrogate model.
    """

    generation: int
    profile: Dict[str, Any]
    islands: List[Dict[str, Any]] = field(default_factory=list)
    archive: Dict[str, Any] = field(default_factory=dict)
    pareto: Dict[str, Any] = field(default_factory=dict)
    best: Dict[str, Any] = field(default_factory=dict)
    statistics: Dict[str, Any] = field(default_factory=dict)
    rng_state: Dict[str, Any] = field(default_factory=dict)
    surrogate: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """A JSON serialisable copy of the state."""
        return {
            "version": CHECKPOINT_VERSION,
            "generation": int(self.generation),
            "profile": dict(self.profile),
            "islands": [dict(island) for island in self.islands],
            "archive": dict(self.archive),
            "pareto": dict(self.pareto),
            "best": dict(self.best),
            "statistics": dict(self.statistics),
            "rng_state": dict(self.rng_state),
            "surrogate": dict(self.surrogate),
            "created_at": str(self.created_at),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "GenerationState":
        """Rebuild a state from its serialised form."""
        data = dict(payload or {})
        return cls(
            generation=int(data.get("generation", 0)),
            profile=dict(data.get("profile") or {}),
            islands=[dict(island) for island in data.get("islands") or []],
            archive=dict(data.get("archive") or {}),
            pareto=dict(data.get("pareto") or {}),
            best=dict(data.get("best") or {}),
            statistics=dict(data.get("statistics") or {}),
            rng_state=dict(data.get("rng_state") or {}),
            surrogate=dict(data.get("surrogate") or {}),
            created_at=str(data.get("created_at", "")),
        )


def save_checkpoint(state: GenerationState, path: str) -> str:
    """Write the checkpoint of a state and return the path of the file.

    The payload is written into ``<path>.tmp`` in the same directory, the
    handle is flushed and synchronised with :func:`os.fsync`, and the
    temporary file is then renamed onto ``path`` with :func:`os.replace`.
    A reader therefore never sees a half written checkpoint, and a crash
    during the write leaves the previous checkpoint untouched.

    The file holds JSON.  A payload of at least
    :data:`GZIP_THRESHOLD_BYTES` bytes -- and every file whose name ends
    with ``.gz`` -- is stored gzip compressed; the reader detects the
    compression by the gzip magic header of the file.
    """
    target = os.path.abspath(path)
    directory = os.path.dirname(target)
    if directory:
        os.makedirs(directory, exist_ok=True)
    blob = json.dumps(state.to_dict(), ensure_ascii=False, default=str).encode("utf-8")
    if target.lower().endswith(".gz") or len(blob) >= GZIP_THRESHOLD_BYTES:
        body = gzip.compress(blob, compresslevel=6)
    else:
        body = blob
    temporary = target + ".tmp"
    with open(temporary, "wb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    _sync_directory(directory)
    return path


def load_checkpoint(path: str) -> GenerationState:
    """Read a checkpoint and return the state it holds.

    Plain and gzip compressed checkpoints are both accepted; the compression
    is detected by the magic header of the file.  A file that is corrupt,
    truncated or that does not hold a state raises a :class:`CheckpointError`
    instead of returning a state that is wrong.
    """
    target = os.path.abspath(path)
    with open(target, "rb") as handle:
        raw = handle.read()
    if not raw:
        raise CheckpointError(f"the checkpoint '{path}' is empty")
    body = raw
    if raw[:2] == GZIP_MAGIC:
        try:
            body = gzip.decompress(raw)
        except Exception as exc:
            raise CheckpointError(
                f"the checkpoint '{path}' is a damaged gzip file: {exc}"
            ) from exc
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CheckpointError(
            f"the checkpoint '{path}' is corrupt or truncated: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise CheckpointError(
            f"the checkpoint '{path}' does not hold a state but a {type(payload).__name__}"
        )
    return GenerationState.from_dict(payload)


def migrate_state(state: GenerationState, profile: ScalingProfile) -> GenerationState:
    """Move a state onto another hardware profile (the hot migration).

    The archive, the Pareto front, the elites of every island, the best
    individual, the statistics and the state of the random number generator
    survive the move unchanged.  The profile is replaced by ``profile`` and
    the island populations are resized to the population size of the new
    profile: a population that is too small is padded with the elites of its
    island, a population that is too large is truncated but keeps its
    elites.  The number of islands is not changed.

    The move is recorded in ``statistics["migration"]`` with the tier the
    state came from and the tier it goes to.  Migrating a state onto the
    tier it already runs on changes nothing, so the operation is idempotent.
    """
    target = profile.validate()
    old_profile = dict(state.profile or {})
    old_tier = str(old_profile.get("tier", "unknown"))
    new_tier = str(target.tier)
    islands = [dict(island) for island in state.islands]
    sizes = _target_sizes(len(islands), int(target.population_size))
    resized = [
        _resize_island(island, size) for island, size in zip(islands, sizes)
    ]
    statistics = dict(state.statistics or {})
    if old_tier != new_tier:
        migration = {
            "from_tier": old_tier,
            "to_tier": new_tier,
            "from": old_tier,
            "to": new_tier,
            "generation": int(state.generation),
            "from_population_size": int(old_profile.get("population_size", 0)),
            "to_population_size": int(target.population_size),
            "islands_kept": len(islands),
            "profile_islands": int(target.islands),
            "from_grid_cells": int(old_profile.get("stage3_grid_cells", 0)),
            "to_grid_cells": int(target.stage3_grid_cells),
            "at": _utc_now_iso(),
        }
        statistics["migration"] = migration
        history = list(statistics.get("migration_history") or [])
        history.append(migration)
        statistics["migration_history"] = history
    return GenerationState(
        generation=int(state.generation),
        profile=target.to_dict(),
        islands=resized,
        archive=dict(state.archive or {}),
        pareto=dict(state.pareto or {}),
        best=dict(state.best or {}),
        statistics=statistics,
        rng_state=dict(state.rng_state or {}),
        surrogate=dict(state.surrogate or {}),
        created_at=str(state.created_at),
    )


def checkpoint_paths(directory: str) -> List[str]:
    """The checkpoints of a directory, newest first.

    Temporary files (the ``<name>.tmp`` of a write that is in flight) and
    files that are not checkpoints are ignored.  A directory that does not
    exist has no checkpoints.
    """
    if not os.path.isdir(directory):
        return []
    entries: List[Tuple[float, str]] = []
    for name in os.listdir(directory):
        if name.startswith(".") or name.lower().endswith(".tmp"):
            continue
        lowered = name.lower()
        if not (lowered.endswith(".json") or lowered.endswith(".gz")):
            continue
        full = os.path.join(directory, name)
        if not os.path.isfile(full):
            continue
        try:
            stamp = os.path.getmtime(full)
        except OSError:
            stamp = 0.0
        entries.append((stamp, name))
    entries.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [os.path.join(directory, name) for _, name in entries]


def _sync_directory(directory: str) -> None:
    """Make the rename of a checkpoint durable.

    The rename of the temporary file is atomic on its own; the flush of the
    directory makes it survive a power failure as well.  Windows cannot
    open a directory as a file, so the flush is skipped there.
    """
    if not directory:
        return
    try:
        handle = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        with contextlib.suppress(OSError):
            os.fsync(handle)
    finally:
        os.close(handle)


def _target_sizes(island_count: int, population: int) -> List[int]:
    """Split a population over the islands of a state."""
    count = int(island_count)
    if count <= 0:
        return []
    total = max(0, int(population))
    base = total // count
    remainder = total % count
    return [base + (1 if index < remainder else 0) for index in range(count)]


def _first_key(payload: Dict[str, Any], candidates: Sequence[str]) -> Optional[str]:
    """The first key of ``candidates`` that the payload uses."""
    for key in candidates:
        if key in payload:
            return key
    return None


def _copy_individual(value: Any) -> Any:
    """A private copy of one individual of a population."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    return value


def _resize_island(island: Dict[str, Any], target_size: int) -> Dict[str, Any]:
    """Resize the population of one island to ``target_size``.

    A population that is too small is padded with the elites of the island
    (with its own individuals when it keeps no elites), a population that is
    too large is truncated but keeps its elites.
    """
    result = dict(island)
    population_key = _first_key(result, _POPULATION_KEYS)
    elite_key = _first_key(result, _ELITE_KEYS)
    population = list(result.get(population_key) or []) if population_key else []
    elites = list(result.get(elite_key) or []) if elite_key else []
    if target_size <= 0:
        if population_key:
            result[population_key] = []
        return result
    if len(population) > target_size:
        kept = list(population[:target_size])
        for elite in elites:
            if elite in kept:
                continue
            if len(kept) >= target_size:
                kept.pop()
            kept.append(_copy_individual(elite))
        population = kept
    elif len(population) < target_size:
        fillers = elites if elites else population
        if fillers:
            index = 0
            while len(population) < target_size:
                population.append(_copy_individual(fillers[index % len(fillers)]))
                index += 1
    if population_key:
        result[population_key] = population
    return result
