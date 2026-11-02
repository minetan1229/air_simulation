"""Regression tests for Phase 3: P1-3 (Optimization computational scalability and Pareto sorting).

Fixates:
- P1-3: Non-dominated Pareto sorting performance and dominance logic.
  Verifies vectorized chunked dominance matrix avoids quadratic pure-Python blowup
  and correctly extracts the Pareto-optimal front within strict execution time bounds.
"""

from __future__ import annotations

import time
import numpy as np
import pytest

from physics.optimizer_domain import pareto_front, non_dominated_sort, crowding_distance


def test_p1_3_pareto_front_correctness() -> None:
    """Verify pareto_front correctly identifies non-dominated individuals in synthetic objective space."""
    # 2 objectives: both maximized
    # (10, 10) dominates (5, 5)
    # (12, 4) and (4, 12) are mutually non-dominated
    matrix = np.array(
        [
            [10.0, 10.0],  # 0: non-dominated
            [5.0, 5.0],    # 1: dominated by 0
            [12.0, 4.0],   # 2: non-dominated
            [4.0, 12.0],   # 3: non-dominated
            [2.0, 1.0],    # 4: dominated by all
        ]
    )
    front = pareto_front(["max_distance", "max_flight_time"], matrix)
    assert set(front) == {0, 2, 3}


def test_p1_3_pareto_sorting_scalability() -> None:
    """Verify that chunked vectorized Pareto sorting completes in under 2.0s for 2,000 individuals."""
    np.random.seed(42)
    # 2000 individuals with 3 objectives
    data = np.random.randn(2000, 3)

    t0 = time.perf_counter()
    fronts = non_dominated_sort(["max_distance", "max_flight_time", "max_ld_ratio"], data)
    elapsed = time.perf_counter() - t0

    assert elapsed < 2.0, f"Pareto sorting for 2000 individuals took too long: {elapsed:.2f}s"
    assert len(fronts) > 0
    # Every individual must be accounted for across all fronts
    total_sorted = sum(len(f) for f in fronts)
    assert total_sorted == 2000


def test_p1_3_crowding_distance_preserves_boundaries() -> None:
    """Verify crowding distance correctly assigns infinite distance to extreme boundaries."""
    matrix = np.array(
        [
            [1.0, 10.0],
            [2.0, 8.0],
            [3.0, 6.0],
            [4.0, 4.0],
            [5.0, 2.0],
        ]
    )
    front = list(range(5))
    distances = crowding_distance(["max_distance", "max_flight_time"], matrix, front)

    assert len(distances) == 5
    # Extreme boundaries in the front must have infinite crowding distance
    assert np.isinf(distances[0])
    assert np.isinf(distances[-1])
    # Interior points must have finite non-negative crowding distances
    for d in distances[1:-1]:
        assert not np.isinf(d)
        assert d >= 0.0
