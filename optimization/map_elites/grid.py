"""3D MAP-Elites grid archive for behavior-space illumination.

Behavior Descriptors (3D):
  1. Wing loading (kg/m^2): [1.0, 7.0]
  2. Static stability (0..1): [0.0, 1.0]
  3. Aspect ratio: [1.0, 6.0]
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from core.interfaces import AircraftCandidate, BehaviorDescriptor


def _clamp(v: float, low: float, high: float) -> float:
    return max(low, min(high, v))


class MAPElitesGridArchive:
    """Discretized 3D grid storing the highest-scoring candidate in each niche."""

    def __init__(
        self,
        bins: Tuple[int, int, int] = (8, 8, 8),
        ranges: Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]] = (
            (1.0, 7.0),   # Wing loading range (kg/m^2)
            (0.0, 1.0),   # Static stability range
            (1.0, 6.0),   # Aspect ratio range
        ),
    ) -> None:
        self.bins = bins
        self.ranges = ranges
        # Key: (bin_x, bin_y, bin_z), Value: (best_score, AircraftCandidate)
        self.cells: Dict[Tuple[int, int, int], Tuple[float, AircraftCandidate]] = {}
        self.total_evaluations: int = 0
        self.improved_evaluations: int = 0

    @property
    def total_cells(self) -> int:
        return self.bins[0] * self.bins[1] * self.bins[2]

    def get_bin_index(self, descriptor: BehaviorDescriptor) -> Tuple[int, int, int]:
        """Convert a continuous 3D behavior descriptor into grid bin coordinates."""
        vals = (
            descriptor.wing_loading_kg_m2,
            descriptor.static_stability,
            descriptor.aspect_ratio,
        )

        indices: List[int] = []
        for val, (low, high), num_bins in zip(vals, self.ranges, self.bins):
            span = max(1.0e-7, high - low)
            clamped = _clamp(val, low, high - 1.0e-7)
            frac = max(0.0, min(1.0 - 1.0e-7, (clamped - low) / span))
            idx = int(frac * num_bins)
            idx = max(0, min(num_bins - 1, idx))
            indices.append(idx)

        return (indices[0], indices[1], indices[2])

    def add_candidate(self, candidate: AircraftCandidate) -> bool:
        """Add candidate to archive if niche is empty or candidate exceeds current best."""
        self.total_evaluations += 1
        if candidate.benchmark is None:
            return False

        desc = candidate.descriptor
        if desc is None:
            if candidate.geometry:
                desc = candidate.geometry.compute_behavior_descriptor(
                    stability=candidate.benchmark.low_speed.stability
                )
                candidate.descriptor = desc
            else:
                return False

        score = candidate.benchmark.composite_score
        bin_idx = self.get_bin_index(desc)

        if bin_idx not in self.cells:
            self.cells[bin_idx] = (score, candidate)
            self.improved_evaluations += 1
            return True

        current_score, current_cand = self.cells[bin_idx]
        if score > current_score:
            self.cells[bin_idx] = (score, candidate)
            self.improved_evaluations += 1
            return True
        elif math.isclose(score, current_score, abs_tol=1e-5):
            # Tie-break: prefer candidate with higher stability or lower mass
            cand_m = candidate.geometry.mass_kg if candidate.geometry else 1.0
            curr_m = current_cand.geometry.mass_kg if current_cand.geometry else 1.0
            if cand_m < curr_m:
                self.cells[bin_idx] = (score, candidate)
                return True

        return False

    def coverage(self) -> float:
        """Calculate archive coverage fraction (filled niches / total niches)."""
        return len(self.cells) / max(1, self.total_cells)

    def get_elites(self) -> List[AircraftCandidate]:
        """Return all niche elite candidates sorted by composite score descending."""
        sorted_items = sorted(self.cells.values(), key=lambda item: item[0], reverse=True)
        return [cand for _, cand in sorted_items]

    def get_global_best(self) -> Optional[AircraftCandidate]:
        """Return the globally highest-scoring candidate."""
        elites = self.get_elites()
        return elites[0] if elites else None
