"""Pareto frontier management and non-dominated ranking for aerodynamic optimization.

Extracts and centralizes non-dominated sorting, crowding distance calculation,
and Pareto archive management from optimizer_domain and ga_domain.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np


@dataclass
class ParetoMember:
    """An entry in the Pareto front or archive."""
    vector: List[float]
    objectives: Dict[str, float]
    score: float = 0.0
    params: Dict[str, float] = field(default_factory=dict)
    physics: Dict[str, Any] = field(default_factory=dict)
    verified: bool = False
    infeasible: bool = False


def dominance_matrix(
    matrix: np.ndarray, directions: np.ndarray
) -> np.ndarray:
    """Boolean (n, n) matrix; entry ``[i, j]`` is True when row ``i`` dominates row ``j``.

    Rows are processed in chunks to bound memory (the full (n, n, m) broadcast
    would otherwise allocate gigabytes for large populations).

    Finite vs non-finite handling:
    Any row with non-finite values (NaN / Inf) represents a numerical failure
    or invalid trial. Valid finite rows strictly dominate non-finite rows, and
    non-finite rows never dominate any other row.
    """
    size = matrix.shape[0]
    dom_matrix = np.zeros((size, size), dtype=bool)
    is_valid = np.isfinite(matrix).all(axis=1)

    valid_indices = np.nonzero(is_valid)[0]
    invalid_indices = np.nonzero(~is_valid)[0]
    if len(valid_indices) > 0 and len(invalid_indices) > 0:
        dom_matrix[np.ix_(valid_indices, invalid_indices)] = True

    chunk = max(1, min(size, 512))
    for start in range(0, size, chunk):
        stop = min(start + chunk, size)
        valid_in_chunk = is_valid[start:stop]
        if not np.any(valid_in_chunk):
            continue
        sub_mat = matrix[start:stop]
        valid_chunk_idx = np.nonzero(valid_in_chunk)[0]
        valid_all_idx = np.nonzero(is_valid)[0]
        dom = np.zeros((stop - start, size), dtype=bool)
        if len(valid_chunk_idx) > 0 and len(valid_all_idx) > 0:
            diff = directions[None, None, :] * (sub_mat[valid_chunk_idx, None, :] - matrix[None, valid_all_idx, :])
            ge = (diff >= 0.0).all(axis=2)
            gt = (diff > 0.0).any(axis=2)
            dom[np.ix_(valid_chunk_idx, valid_all_idx)] = ge & gt
        dom_matrix[start:stop] = dom_matrix[start:stop] | dom
    np.fill_diagonal(dom_matrix, False)
    return dom_matrix


def non_dominated_sort_matrix(
    matrix: np.ndarray, directions: np.ndarray
) -> List[List[int]]:
    """Fast non-dominated sort returning the ordered front indices."""
    size = matrix.shape[0]
    if size == 0:
        return []
    dominates = dominance_matrix(matrix, directions)
    domination_count = dominates.sum(axis=0).astype(int)
    dominated = domination_count > 0
    front = [index for index in range(size) if not dominated[index]]
    dominated_by = [np.nonzero(dominates[index])[0].tolist() for index in range(size)]

    fronts: List[List[int]] = []
    while front:
        fronts.append(list(front))
        nxt: List[int] = []
        for index in front:
            for other in dominated_by[index]:
                domination_count[other] -= 1
                if domination_count[other] == 0:
                    nxt.append(other)
        front = nxt
    return fronts


def crowding_distance_matrix(matrix: np.ndarray, front: Sequence[int]) -> List[float]:
    """NSGA-II crowding distance for a given front index list."""
    members = [int(index) for index in front]
    distances = [0.0] * len(members)
    if len(members) <= 2:
        return [math.inf] * len(members)
    block = matrix[members]
    for column in range(block.shape[1]):
        order = np.argsort(block[:, column], kind="stable")
        values = block[order, column]
        spread = float(values[-1] - values[0])
        if spread <= 1.0e-12:
            continue
        distances[order[0]] = math.inf
        distances[order[-1]] = math.inf
        for position in range(1, len(members) - 1):
            member = int(order[position])
            if math.isinf(distances[member]):
                continue
            distances[member] += float(values[position + 1] - values[position - 1]) / spread
    return distances


class ParetoArchiveManager:
    """Manages multi-objective Pareto front archives with duplicate suppression and crowding distance."""

    def __init__(
        self,
        objective_names: Sequence[str],
        objective_directions: Sequence[int],
        weights: Optional[Dict[str, float]] = None,
        max_size: Optional[int] = None,
    ) -> None:
        self.objective_names = list(objective_names)
        self.directions = np.asarray([float(d) for d in objective_directions], dtype=float)
        self.weights = dict(weights or {})
        self.max_size = max_size
        self._archive: List[Tuple[List[float], Dict[str, float], Dict[str, Any]]] = []

    def add(
        self,
        vector: Sequence[float],
        objectives: Dict[str, float],
        physics: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add an evaluated vector and its objective values to the archive."""
        self._archive.append((
            [float(v) for v in vector],
            {k: float(v) for k, v in objectives.items()},
            dict(physics or {})
        ))

    def as_matrix(self) -> np.ndarray:
        """Return the current archive as an (n, num_objectives) float matrix."""
        if not self._archive:
            return np.empty((0, len(self.objective_names)), dtype=float)
        rows = [
            [float(obj.get(name, float("nan"))) for name in self.objective_names]
            for _vec, obj, _phys in self._archive
        ]
        return np.asarray(rows, dtype=float)

    def extract_pareto_front(self, unique: bool = True) -> List[Dict[str, Any]]:
        """Extract the non-dominated Pareto front (Rank 0) from the archive."""
        if not self._archive:
            return []
        matrix = self.as_matrix()
        fronts = non_dominated_sort_matrix(matrix, self.directions)
        if not fronts:
            return []
        rank0 = fronts[0]

        front: List[Dict[str, Any]] = []
        seen_vectors: Set[Tuple[float, ...]] = set()
        for idx in rank0:
            vec, obj, phys = self._archive[idx]
            if unique:
                key = tuple(round(float(v), 12) for v in vec)
                if key in seen_vectors:
                    continue
                seen_vectors.add(key)
            front.append({
                "vector": list(vec),
                "objectives": dict(obj),
                "physics": dict(phys),
                "verified": False,
            })
        return front
