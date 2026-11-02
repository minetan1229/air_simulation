"""Quality-diversity archive and Pareto front of the second generation search.

The search of :mod:`physics.ga2` is not looking for *the* best design but for
a *set* of good designs that behave differently.  Two programmes that trade
range against stability, or that spend a different number of folds, are both
worth keeping, because the crossover operator needs the diversity and the
report needs the trade-off curve.  A single elite list throws that diversity
away, so the archive of this module is a MAP-Elites grid: the behavioural
space is cut into cells, every cell keeps the best individual that ever fell
into it, and the search may re-discover a cell without losing its incumbent.

The grid is three dimensional because the three behaviour descriptors of the
paper airplane are:

``wing_loading``
    the weight of the sheet divided by its wing area, in N/m^2.  It is the
    parameter that decides how hard the air has to work to hold the plane up.
``aspect_ratio``
    the span of the wing divided by its mean chord.  A high aspect ratio
    glides far but rolls slowly and is tip sensitive.
``fold_count``
    the number of folds of the programme, 3 to 8.  A cheap programme is easy
    to fold and hard to stabilise, so the grid keeps the cheap solutions and
    the expensive ones apart instead of mixing them into one ranking.

The bookkeeping has to survive 5000 generations of a population of 100000
individuals, so the hot path -- :meth:`MapElitesArchive.add` -- is a dict
lookup plus a heap push, and the *ordering* work happens only when the
archive overflows.  The eviction order is the order of a lazy priority queue:
the worst fitness leaves first and, among equal fitnesses, the older
incumbent leaves first, so a fresh individual never loses its cell to a
stale one.  The queue is rebuilt from the live cells whenever the stale
entries outnumber the live ones, which keeps its memory bounded and its
amortised cost logarithmic.

The module also owns the Pareto front of the run.  The front is the
*multi-objective* view of the same search -- range against stability against
fold cost -- and it is capped, because a front that grows without limit is
only a second copy of the population.  When the cap bites, the point with
the worst mean of the normalised objectives is dropped, so the surviving
front stays spread over the trade-off surface.

Both classes serialise into plain dictionaries, so an interrupted run can be
restored from the generation checkpoint bit for bit.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "ArchiveEntry",
    "MapElitesArchive",
    "ParetoFront",
    "DEFAULT_BINS",
    "DEFAULT_CAPACITY",
    "DEFAULT_METRIC_NAMES",
    "DEFAULT_METRIC_RANGES",
]


#: The default resolution of the behaviour grid.
DEFAULT_BINS: Tuple[int, int, int] = (6, 6, 4)

#: The default number of cells the archive may hold.
DEFAULT_CAPACITY: int = 20000

#: The behaviour descriptors the grid is built on.
DEFAULT_METRIC_NAMES: Tuple[str, str, str] = ("wing_loading", "aspect_ratio", "fold_count")

#: The physical range of every default behaviour descriptor.
DEFAULT_METRIC_RANGES: Dict[str, Tuple[float, float]] = {
    "wing_loading": (1.0, 200.0),
    "aspect_ratio": (1.0, 12.0),
    "fold_count": (3.0, 8.0),
}


def _quote_identifier(name: str) -> str:
    """Quote a column name so that it can be used in a SQL statement."""
    escaped = str(name).replace('"', '""')
    return f'"{escaped}"'


def _dominates(
    first: Sequence[float],
    second: Sequence[float],
    directions: Sequence[int],
) -> bool:
    """True when ``first`` is at least as good as ``second`` everywhere.

    A direction of ``+1`` means that a larger value is better, ``-1`` that a
    smaller value is better.  The relation is strict in at least one
    objective, so two identical points do not dominate each other.
    """
    strictly_better = False
    for index, direction in enumerate(directions):
        delta = direction * (first[index] - second[index])
        if delta < 0.0:
            return False
        if delta > 0.0:
            strictly_better = True
    return strictly_better


@dataclass
class ArchiveEntry:
    """One elite that lives in a cell of the behaviour grid.

    ``key`` identifies the individual (the hash of its genome), ``fitness``
    is the scalar the search maximises, ``metrics`` carries the three
    behaviour descriptors that place the individual in the grid and
    ``payload`` holds whatever the caller wants to remember about it.
    """

    key: str
    fitness: float
    metrics: Dict[str, float]
    payload: Dict[str, Any] = field(default_factory=dict)
    generation: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """A JSON serialisable copy of the entry."""
        return {
            "key": str(self.key),
            "fitness": float(self.fitness),
            "metrics": {str(name): float(value) for name, value in self.metrics.items()},
            "payload": dict(self.payload),
            "generation": int(self.generation),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ArchiveEntry":
        """Rebuild an entry from its serialised form."""
        data = dict(payload or {})
        metrics = dict(data.get("metrics") or {})
        return cls(
            key=str(data.get("key", "")),
            fitness=float(data.get("fitness", 0.0)),
            metrics={str(name): float(value) for name, value in metrics.items()},
            payload=dict(data.get("payload") or {}),
            generation=int(data.get("generation", 0)),
        )


class MapElitesArchive:
    """A MAP-Elites quality-diversity archive over a behaviour grid.

    Parameters
    ----------
    bins:
        The number of cells per behaviour dimension.
    capacity:
        The maximum number of cells the archive may hold.  When the archive
        is full a new individual may still enter, but only by evicting an
        occupant that is worse than it.
    metric_names:
        The behaviour descriptors, in the order of ``bins``.
    metric_ranges:
        The physical range of every descriptor.  When it is omitted the
        default range of the descriptor is used; a descriptor without a
        default range has to be given one explicitly.
    """

    def __init__(
        self,
        bins: Tuple[int, int, int] = DEFAULT_BINS,
        capacity: int = DEFAULT_CAPACITY,
        metric_names: Tuple[str, str, str] = DEFAULT_METRIC_NAMES,
        metric_ranges: Optional[Tuple[Tuple[float, float], ...]] = None,
    ) -> None:
        names = tuple(str(name) for name in metric_names)
        if not names:
            raise ValueError("the archive needs at least one behaviour descriptor")
        bin_counts = tuple(int(count) for count in bins)
        if len(bin_counts) != len(names):
            raise ValueError("one bin count per behaviour descriptor is required")
        if any(count < 1 for count in bin_counts):
            raise ValueError("a bin count has to be at least one")

        self.bins: Tuple[int, ...] = bin_counts
        self.metric_names: Tuple[str, ...] = names
        self.capacity: int = max(1, int(capacity))
        self.metric_ranges: Tuple[Tuple[float, float], ...] = self._resolve_ranges(metric_ranges)

        self._offsets: Tuple[float, ...] = tuple(low for low, _ in self.metric_ranges)
        self._scales: Tuple[float, ...] = tuple(
            float(count) / (high - low)
            for (low, high), count in zip(self.metric_ranges, self.bins)
        )
        self._axes: Tuple[Tuple[str, float, float, int], ...] = tuple(
            (name, offset, scale, count)
            for name, offset, scale, count in zip(
                self.metric_names, self._offsets, self._scales, self.bins
            )
        )

        self._cells: Dict[Tuple[int, ...], ArchiveEntry] = {}
        self._versions: Dict[Tuple[int, ...], int] = {}
        self._keys: Dict[str, Tuple[int, ...]] = {}
        self._heap: List[Tuple[float, int, Tuple[int, ...]]] = []
        self._version_counter: int = 0
        self._best: Optional[ArchiveEntry] = None
        self._best_cell: Optional[Tuple[int, ...]] = None

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def _resolve_ranges(
        self,
        metric_ranges: Optional[Tuple[Tuple[float, float], ...]],
    ) -> Tuple[Tuple[float, float], ...]:
        """Turn the requested ranges into one validated range per descriptor."""
        if metric_ranges is None:
            resolved: List[Tuple[float, float]] = []
            for name in self.metric_names:
                default = DEFAULT_METRIC_RANGES.get(name)
                if default is None:
                    raise ValueError(
                        f"behaviour descriptor '{name}' has no default range; "
                        "pass 'metric_ranges' explicitly"
                    )
                resolved.append((float(default[0]), float(default[1])))
            return tuple(resolved)

        given = tuple((float(low), float(high)) for low, high in metric_ranges)
        if len(given) != len(self.metric_names):
            raise ValueError("one range per behaviour descriptor is required")
        for (low, high), name in zip(given, self.metric_names):
            if not math.isfinite(low) or not math.isfinite(high):
                raise ValueError(f"the range of '{name}' is not finite")
            if high <= low:
                raise ValueError(f"the range of '{name}' is empty")
        return given

    # ------------------------------------------------------------------
    # Grid
    # ------------------------------------------------------------------
    def cell_index(self, metrics: Dict[str, float]) -> Tuple[int, ...]:
        """The cell of the grid the metrics fall into.

        A value outside the physical range is clamped into the outermost
        cell, so a descriptor that leaves its range never makes the archive
        fail; a descriptor that is missing or not finite is an error, because
        silently binning it would corrupt the diversity of the archive.
        """
        indices: List[int] = []
        for name, offset, scale, count in self._axes:
            try:
                value = float(metrics[name])
            except KeyError as exc:
                raise ValueError(
                    f"the entry is missing the behaviour descriptor '{name}'"
                ) from exc
            if not math.isfinite(value):
                raise ValueError(f"the behaviour descriptor '{name}' is not finite: {value!r}")
            index = int((value - offset) * scale)
            if index < 0:
                index = 0
            elif index >= count:
                index = count - 1
            indices.append(index)
        return tuple(indices)

    # ------------------------------------------------------------------
    # Insertion
    # ------------------------------------------------------------------
    def add(self, entry: ArchiveEntry) -> bool:
        """Offer an entry to the archive.

        The entry is stored when its cell is free or when it beats the
        occupant of its cell, and when the archive is full it is stored only
        if it beats the worst occupant of the whole archive.  The return
        value tells whether the entry ended up in the archive.
        """
        cell = self.cell_index(entry.metrics)
        cells = self._cells
        occupant = cells.get(cell)
        if occupant is not None:
            if entry.fitness <= occupant.fitness:
                return False
            previous_cell = self._keys.get(entry.key)
            if previous_cell is not None and previous_cell != cell:
                self._remove_cell(previous_cell)
            if occupant.key != entry.key:
                self._keys.pop(occupant.key, None)
            self._store(cell, entry)
            return True

        previous_cell = self._keys.get(entry.key)
        if previous_cell is not None:
            self._remove_cell(previous_cell)

        if len(cells) >= self.capacity:
            worst = self._peek_worst()
            if worst is None:
                return False
            if entry.fitness <= worst[0]:
                return False
            self._remove_cell(worst[2])

        self._store(cell, entry)
        return True

    def _store(
        self,
        cell: Tuple[int, ...],
        entry: ArchiveEntry,
        version: Optional[int] = None,
    ) -> None:
        """Write an entry into a cell and register it in the eviction queue."""
        if version is None:
            self._version_counter += 1
            version = self._version_counter
        elif version > self._version_counter:
            self._version_counter = version
        self._cells[cell] = entry
        self._versions[cell] = version
        self._keys[entry.key] = cell
        heapq.heappush(self._heap, (float(entry.fitness), version, cell))
        best = self._best
        if best is None or entry.fitness > best.fitness:
            self._best = entry
            self._best_cell = cell
        if len(self._heap) > 2 * len(self._cells) + 1024:
            self._rebuild_heap()

    def _remove_cell(self, cell: Tuple[int, ...]) -> None:
        """Drop the occupant of a cell."""
        occupant = self._cells.pop(cell, None)
        self._versions.pop(cell, None)
        if occupant is None:
            return
        if self._keys.get(occupant.key) == cell:
            del self._keys[occupant.key]
        if self._best is occupant:
            self._best = None
            self._best_cell = None

    def _peek_worst(self) -> Optional[Tuple[float, int, Tuple[int, ...], ArchiveEntry]]:
        """The occupant that would leave the archive next.

        Stale queue entries -- the ones of cells that have been overwritten
        or emptied since they were pushed -- are dropped on the way, so the
        queue stays proportional to the number of live cells.
        """
        heap = self._heap
        cells = self._cells
        versions = self._versions
        while heap:
            fitness, version, cell = heap[0]
            occupant = cells.get(cell)
            if occupant is None or versions.get(cell) != version:
                heapq.heappop(heap)
                continue
            return (float(occupant.fitness), version, cell, occupant)
        return None

    def _rebuild_heap(self) -> None:
        """Rebuild the eviction queue from the live cells."""
        self._heap = [
            (float(entry.fitness), int(self._versions.get(cell, 0)), cell)
            for cell, entry in self._cells.items()
        ]
        heapq.heapify(self._heap)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def entries(self) -> List[ArchiveEntry]:
        """Every stored elite, ordered by the cell it occupies."""
        return [self._cells[cell] for cell in sorted(self._cells)]

    def best(self) -> Optional[ArchiveEntry]:
        """The elite with the highest fitness, or ``None`` for an empty archive."""
        best = self._best
        if best is not None and self._best_cell is not None:
            if self._cells.get(self._best_cell) is best:
                return best
        best = None
        best_cell = None
        for cell, entry in self._cells.items():
            if best is None or entry.fitness > best.fitness:
                best = entry
                best_cell = cell
        self._best = best
        self._best_cell = best_cell
        return best

    def __len__(self) -> int:
        return len(self._cells)

    def __contains__(self, key: str) -> bool:
        return key in self._keys

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """A JSON serialisable copy of the archive."""
        cells: List[Dict[str, Any]] = []
        for cell in sorted(self._cells):
            cells.append(
                {
                    "cell": [int(index) for index in cell],
                    "version": int(self._versions.get(cell, 0)),
                    "entry": self._cells[cell].to_dict(),
                }
            )
        return {
            "kind": "map_elites_archive",
            "bins": [int(count) for count in self.bins],
            "capacity": int(self.capacity),
            "metric_names": [str(name) for name in self.metric_names],
            "metric_ranges": [[float(low), float(high)] for low, high in self.metric_ranges],
            "cells": cells,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "MapElitesArchive":
        """Rebuild an archive from its serialised form."""
        data = dict(payload or {})
        bins = tuple(int(count) for count in data.get("bins", DEFAULT_BINS))
        names = tuple(str(name) for name in data.get("metric_names", DEFAULT_METRIC_NAMES))
        raw_ranges = data.get("metric_ranges")
        ranges: Optional[Tuple[Tuple[float, float], ...]] = None
        if raw_ranges is not None:
            ranges = tuple((float(low), float(high)) for low, high in raw_ranges)
        archive = cls(
            bins=bins,  # type: ignore[arg-type]
            capacity=int(data.get("capacity", DEFAULT_CAPACITY)),
            metric_names=names,  # type: ignore[arg-type]
            metric_ranges=ranges,
        )
        for record in data.get("cells", []) or []:
            entry = ArchiveEntry.from_dict(record.get("entry", record))
            raw_cell = record.get("cell")
            if raw_cell is None:
                cell = archive.cell_index(entry.metrics)
            else:
                cell = tuple(int(index) for index in raw_cell)
            version = record.get("version")
            archive._store(cell, entry, None if version is None else int(version))
        return archive


class ParetoFront:
    """A capped, non-dominated front of the multi-objective search.

    The front keeps the points that no other point dominates.  A point
    dominates another one when it is at least as good in every objective and
    strictly better in one of them; the direction of an objective follows its
    name -- a name that starts with ``min_`` is a cost and is minimised, every
    other name is maximised -- and an explicit direction may be given in the
    constructor.

    The front never grows beyond ``capacity``.  When it does, the point with
    the worst mean of the normalised objectives is dropped, so the surviving
    points stay spread over the trade-off surface instead of collapsing onto
    one end of it.
    """

    def __init__(
        self,
        capacity: int = 500,
        directions: Optional[Mapping[str, int]] = None,
    ) -> None:
        self.capacity: int = max(1, int(capacity))
        self._directions: Dict[str, int] = {
            str(name): (1 if int(direction) >= 0 else -1)
            for name, direction in dict(directions or {}).items()
        }
        self._objective_names: Tuple[str, ...] = ()
        self._direction_vector: Tuple[int, ...] = ()
        self._points: List[Tuple[str, Tuple[float, ...]]] = []

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def _direction_of(self, name: str) -> int:
        """The direction of one objective: ``+1`` maximised, ``-1`` minimised."""
        explicit = self._directions.get(name)
        if explicit is not None:
            return explicit
        if name.lower().startswith("min_"):
            return -1
        return 1

    def _bind_objectives(self, names: Sequence[str]) -> None:
        """Fix the objective set and the direction of every objective."""
        self._objective_names = tuple(names)
        self._direction_vector = tuple(self._direction_of(name) for name in self._objective_names)

    # ------------------------------------------------------------------
    # Insertion
    # ------------------------------------------------------------------
    def add(self, key: str, objectives: Dict[str, float]) -> bool:
        """Offer a point to the front.

        The point enters when no point of the front dominates it; the points
        it dominates leave the front.  The return value tells whether the
        point is part of the front afterwards.
        """
        values = {str(name): float(value) for name, value in objectives.items()}
        if len(values) < 2:
            raise ValueError("a Pareto point needs at least two objectives")
        for name, value in values.items():
            if not math.isfinite(value):
                raise ValueError(f"the objective '{name}' is not finite: {value!r}")
        text = str(key)
        if not self._objective_names:
            self._bind_objectives(tuple(values.keys()))
        elif set(values) != set(self._objective_names):
            raise ValueError(
                "the objective set of the front is fixed; "
                f"expected {list(self._objective_names)}"
            )

        vector = tuple(values[name] for name in self._objective_names)
        points = [point for point in self._points if point[0] != text]
        for other_key, other_vector in points:
            if other_vector == vector:
                self._points = points
                return False
            if _dominates(other_vector, vector, self._direction_vector):
                self._points = points
                return False

        survivors = [
            point
            for point in points
            if not _dominates(vector, point[1], self._direction_vector)
        ]
        survivors.append((text, vector))
        self._points = survivors
        self._trim()
        return any(point[0] == text for point in self._points)

    def _trim(self) -> None:
        """Drop the worst points until the front fits into its capacity."""
        if len(self._points) <= self.capacity:
            return
        scores = self._normalised_scores()
        while len(self._points) > self.capacity:
            worst_index = 0
            for index in range(1, len(scores)):
                if scores[index] <= scores[worst_index]:
                    worst_index = index
            self._points.pop(worst_index)
            scores.pop(worst_index)

    def _normalised_scores(self) -> List[float]:
        """The mean of the normalised objectives of every point of the front.

        Every objective is mapped onto ``[0, 1]`` with its own minimum and
        maximum of the front and is flipped when it is a cost, so that a
        larger score is always better.
        """
        points = self._points
        count = len(self._objective_names)
        if not points:
            return []
        lows = [math.inf] * count
        highs = [-math.inf] * count
        for _, vector in points:
            for index in range(count):
                value = self._direction_vector[index] * vector[index]
                if value < lows[index]:
                    lows[index] = value
                if value > highs[index]:
                    highs[index] = value
        scores: List[float] = []
        for _, vector in points:
            total = 0.0
            for index in range(count):
                value = self._direction_vector[index] * vector[index]
                span = highs[index] - lows[index]
                if span > 0.0:
                    total += (value - lows[index]) / span
                else:
                    total += 0.5
            scores.append(total / count)
        return scores

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def front(self) -> List[Tuple[str, Dict[str, float]]]:
        """The points of the front, best first.

        The order is the mean of the normalised objectives, descending, with
        the key as the tie breaker, so the listing is deterministic.
        """
        scores = self._normalised_scores()
        order = sorted(
            range(len(self._points)),
            key=lambda index: (-scores[index], self._points[index][0]),
        )
        return [
            (
                self._points[index][0],
                {
                    name: float(value)
                    for name, value in zip(self._objective_names, self._points[index][1])
                },
            )
            for index in order
        ]

    def __len__(self) -> int:
        return len(self._points)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """A JSON serialisable copy of the front."""
        return {
            "kind": "pareto_front",
            "capacity": int(self.capacity),
            "directions": {str(name): int(value) for name, value in self._directions.items()},
            "objective_names": [str(name) for name in self._objective_names],
            "points": [
                {
                    "key": str(key),
                    "objectives": {
                        name: float(value)
                        for name, value in zip(self._objective_names, vector)
                    },
                }
                for key, vector in self._points
            ],
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ParetoFront":
        """Rebuild a front from its serialised form."""
        data = dict(payload or {})
        directions = {
            str(name): int(value) for name, value in dict(data.get("directions") or {}).items()
        }
        front = cls(capacity=int(data.get("capacity", 500)), directions=directions)
        names = tuple(str(name) for name in data.get("objective_names", ()))
        if names:
            front._bind_objectives(names)
        for record in data.get("points", []) or []:
            objectives = {
                str(name): float(value)
                for name, value in dict(record.get("objectives") or {}).items()
            }
            key = str(record.get("key", ""))
            if not front._objective_names:
                if len(objectives) < 2:
                    continue
                front._bind_objectives(tuple(objectives.keys()))
            if set(objectives) != set(front._objective_names):
                continue
            front.add(key, objectives)
        return front
