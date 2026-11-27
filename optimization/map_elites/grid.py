"""3D MAP-Elites grid archive for behavior-space illumination.

Behavior Descriptors (3D):
  1. Wing loading (kg/m^2): [0.10, 0.45] (realistic range for folded A4 paper planes)
  2. Static stability margin: [0.10, 0.90] (geometric static stability spectrum)
  3. Aspect ratio: [0.30, 2.20] (folded aircraft aspect ratio spectrum)
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from core.interfaces import AircraftCandidate, BehaviorDescriptor, MAX_FOLD_STEPS, MIN_FOLD_STEPS


def _clamp(v: float, low: float, high: float) -> float:
    # Guard against NaN - reject non-finite values
    if not math.isfinite(v):
        raise ValueError(f"Non-finite value {v} passed to _clamp")
    return max(low, min(high, v))


class MAPElitesGridArchive:
    """Discretized 3D grid storing the highest-scoring valid candidate in each niche."""

    def __init__(
        self,
        bins: Tuple[int, int, int] = (6, 6, 4),
        ranges: Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]] = (
            (0.10, 0.45),  # Realistic wing loading range (kg/m^2) for folded A4 paper planes
            (0.10, 0.90),  # Geometric static stability margin spectrum
            (0.30, 2.20),  # Folded aircraft aspect ratio spectrum
        ),
        min_improvement_eps: float = 1.0e-4,
        reject_out_of_bounds: bool = True,
        use_planform_bd: bool = True,
    ) -> None:
        self.bins = bins
        self.ranges = ranges
        self.min_improvement_eps = float(min_improvement_eps)
        self.reject_out_of_bounds = bool(reject_out_of_bounds)
        self.use_planform_bd = bool(use_planform_bd)
        self.cells: Dict[Tuple[int, int, int], Tuple[float, AircraftCandidate]] = {}
        self.curiosity: Dict[Tuple[int, int, int], float] = {}
        self.total_evaluations: int = 0
        self.improved_evaluations: int = 0
        self.rejected_evaluations: int = 0

    @property
    def total_cells(self) -> int:
        return self.bins[0] * self.bins[1] * self.bins[2]

    def is_in_bounds(self, descriptor: BehaviorDescriptor) -> bool:
        """Return True iff all 3 descriptor coordinates are finite and within grid ranges."""
        if not descriptor.is_finite():
            return False
        for val, (low, high) in zip(descriptor.as_tuple(), self.ranges):
            if val < low or val > high:
                return False
        return True

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
            idx = max(0, min(num_bins - 1, int(frac * num_bins)))
            indices.append(idx)
        return (indices[0], indices[1], indices[2])

    @staticmethod
    def _is_candidate_valid(candidate: AircraftCandidate) -> bool:
        """Verify candidate passed benchmark without crashing, diverging, or hacking."""
        bench = candidate.benchmark
        if bench is None or not bench.valid:
            return False
        if not math.isfinite(bench.composite_score) or bench.composite_score <= 1.0e-6:
            return False
        if bench.low_speed.crashed or bench.low_speed.diverged:
            return False
        if bench.mid_speed.crashed or bench.mid_speed.diverged:
            return False
        if candidate.metadata.get("tier0_valid") is not True:
            return False
        if not (MIN_FOLD_STEPS <= candidate.step_count <= MAX_FOLD_STEPS):
            return False
        geom = candidate.geometry
        if geom is None:
            return False
        if not bool(geom.fold_valid):
            return False
        try:
            if isinstance(geom.penetrations, bool):
                return False
            pen_val = float(geom.penetrations)
            if not math.isfinite(pen_val) or not pen_val.is_integer() or pen_val < 0.0 or pen_val > 0.0:
                return False
            if isinstance(geom.keel_height_m, bool):
                return False
            keel_h = float(geom.keel_height_m)
            if not (math.isfinite(keel_h) and keel_h >= 0.008):
                return False
            if isinstance(geom.keel_length_m, bool):
                return False
            keel_l = float(geom.keel_length_m)
            if not (math.isfinite(keel_l) and keel_l >= 0.030):
                return False
            if isinstance(geom.keel_layers, bool):
                return False
            keel_layers = float(geom.keel_layers)
            if not (math.isfinite(keel_layers) and keel_layers >= 2.0):
                return False
        except (TypeError, ValueError):
            return False
        if not bool(getattr(geom, "thickness_limit_ok", True)):
            return False
        if not bool(getattr(geom, "flat_foldable_ok", True)):
            return False
        return True

    def add_candidate(self, candidate: AircraftCandidate) -> bool:
        """Add valid candidate to archive if niche is empty or improvement exceeds epsilon."""
        self.total_evaluations += 1
        if not self._is_candidate_valid(candidate):
            self.rejected_evaluations += 1
            return False

        bench = candidate.benchmark
        assert bench is not None

        desc: Optional[BehaviorDescriptor]
        if candidate.geometry is not None:
            conservative_stability = min(
                float(bench.low_speed.stability),
                float(bench.mid_speed.stability),
            )
            desc = candidate.geometry.compute_behavior_descriptor(
                stability=conservative_stability,
                use_planform=self.use_planform_bd,
            )
            candidate.descriptor = desc
        else:
            desc = candidate.descriptor
            if desc is None:
                self.rejected_evaluations += 1
                return False

        if not desc.is_finite():
            self.rejected_evaluations += 1
            return False

        if self.reject_out_of_bounds and not self.is_in_bounds(desc):
            self.rejected_evaluations += 1
            return False

        score = float(bench.composite_score)
        bin_idx = self.get_bin_index(desc)

        if bin_idx not in self.cells:
            self.cells[bin_idx] = (score, candidate)
            self.curiosity[bin_idx] = 1.0
            self.improved_evaluations += 1
            return True

        current_score, current_cand = self.cells[bin_idx]
        if score > current_score + self.min_improvement_eps:
            self.cells[bin_idx] = (score, candidate)
            self.curiosity[bin_idx] = self.curiosity.get(bin_idx, 0.0) + 1.0
            self.improved_evaluations += 1
            return True

        if abs(score - current_score) <= self.min_improvement_eps:
            if candidate.step_count < current_cand.step_count:
                self.cells[bin_idx] = (score, candidate)
                return True
            elif candidate.step_count == current_cand.step_count:
                cand_stab = min(bench.low_speed.stability, bench.mid_speed.stability)
                curr_stab = (
                    min(
                        current_cand.benchmark.low_speed.stability,
                        current_cand.benchmark.mid_speed.stability,
                    )
                    if current_cand.benchmark
                    else 0.0
                )
                if cand_stab > curr_stab + 1.0e-3:
                    self.cells[bin_idx] = (score, candidate)
                    return True
                if abs(cand_stab - curr_stab) <= 1.0e-3:
                    cand_m = candidate.geometry.mass_kg if candidate.geometry else 1.0
                    curr_m = current_cand.geometry.mass_kg if current_cand.geometry else 1.0
                    if cand_m < curr_m - 1.0e-6:
                        self.cells[bin_idx] = (score, candidate)
                        return True

        return False

    def record_parent_outcome(self, bin_idx: Tuple[int, int, int], succeeded: bool) -> None:
        """Update curiosity score for parent selection."""
        prev = self.curiosity.get(bin_idx, 0.0)
        self.curiosity[bin_idx] = prev + 1.0 if succeeded else max(-5.0, prev - 0.5)

    def purge_invalid_elites(self) -> int:
        """Audit existing archive cells and expel any invalid or hacking elites."""
        to_remove = [
            bin_idx
            for bin_idx, (_, cand) in self.cells.items()
            if not self._is_candidate_valid(cand)
        ]
        for bin_idx in to_remove:
            del self.cells[bin_idx]
            self.curiosity.pop(bin_idx, None)
        return len(to_remove)

    def coverage(self) -> float:
        return len(self.cells) / max(1, self.total_cells)

    def get_elites(self) -> List[AircraftCandidate]:
        sorted_items = sorted(self.cells.values(), key=lambda item: item[0], reverse=True)
        return [cand for _, cand in sorted_items]

    def get_global_best(self) -> Optional[AircraftCandidate]:
        elites = self.get_elites()
        return elites[0] if elites else None
