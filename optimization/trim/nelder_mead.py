"""Stage 3: Nelder-Mead local continuous parameter optimization.

Optimizes elevon angle and dihedral angle with fixed origami skeleton
using a 2D Nelder-Mead simplex algorithm against the deterministic benchmark.
"""

from __future__ import annotations

import copy
from typing import Callable, List, Optional, Tuple

from core.interfaces import (
    AircraftCandidate,
    ParametricOrigamiConfig,
    ParametricWingSpec,
    TrimResult,
)
from geometry.v3_origami.adapter import OrigamiAdapter
from physics.benchmark.benchmark import DeterministicFlightBenchmark


def _clamp(v: float, low: float, high: float) -> float:
    return max(low, min(high, v))


class NelderMeadTrimOptimizer:
    """Nelder-Mead simplex optimizer for continuous trim parameters."""

    def __init__(
        self,
        adapter: Optional[OrigamiAdapter] = None,
        benchmark: Optional[DeterministicFlightBenchmark] = None,
        max_iter: int = 20,
        elevon_bounds: Tuple[float, float] = (-5.0, 10.0),
        dihedral_bounds: Tuple[float, float] = (0.0, 10.0),
    ) -> None:
        self.adapter = adapter or OrigamiAdapter()
        self.benchmark = benchmark or DeterministicFlightBenchmark()
        self.max_iter = max_iter
        self.elevon_bounds = elevon_bounds
        self.dihedral_bounds = dihedral_bounds

    def _evaluate_point(
        self,
        elevon: float,
        dihedral: float,
        base_candidate: AircraftCandidate,
    ) -> Tuple[float, AircraftCandidate]:
        """Evaluate (elevon, dihedral) pair and return (-score, candidate)."""
        clamped_e = _clamp(elevon, self.elevon_bounds[0], self.elevon_bounds[1])
        clamped_d = _clamp(dihedral, self.dihedral_bounds[0], self.dihedral_bounds[1])

        new_wing = ParametricWingSpec(
            wingspan_ratio=base_candidate.config.wing.wingspan_ratio,
            dihedral_deg=clamped_d,
            elevon_left_deg=clamped_e,
            elevon_right_deg=clamped_e,
            winglet_angle_deg=base_candidate.config.wing.winglet_angle_deg,
            has_winglet=base_candidate.config.wing.has_winglet,
        )
        new_cfg = ParametricOrigamiConfig(
            nose=base_candidate.config.nose,
            keel=base_candidate.config.keel,
            wing=new_wing,
            paper_gsm=base_candidate.config.paper_gsm,
        )

        cand = self.adapter.compile_candidate(
            new_cfg,
            candidate_id=f"{base_candidate.candidate_id}-trim",
        )
        bench = self.benchmark.evaluate_candidate(cand)
        score = bench.composite_score if bench.valid else 0.0
        return -score, cand

    def optimize_trim(self, candidate: AircraftCandidate) -> TrimResult:
        """Optimize candidate's elevon and dihedral angles via Nelder-Mead simplex."""
        # Initial parameters
        e0 = 0.5 * (candidate.config.wing.elevon_left_deg + candidate.config.wing.elevon_right_deg)
        d0 = candidate.config.wing.dihedral_deg

        # Form initial simplex of 3 vertices in 2D with adaptive step sizes
        e_span = self.elevon_bounds[1] - self.elevon_bounds[0]
        d_span = self.dihedral_bounds[1] - self.dihedral_bounds[0]
        step_e = 0.10 * e_span
        step_d = 0.10 * d_span

        # Step towards domain interior if starting point is close to upper bound
        if e0 + step_e > self.elevon_bounds[1]:
            step_e = -step_e
        if d0 + step_d > self.dihedral_bounds[1]:
            step_d = -step_d

        v0 = (e0, d0)
        v1 = (e0 + step_e, d0)
        v2 = (e0, d0 + step_d)

        cost0, cand0 = self._evaluate_point(v0[0], v0[1], candidate)
        cost1, cand1 = self._evaluate_point(v1[0], v1[1], candidate)
        cost2, cand2 = self._evaluate_point(v2[0], v2[1], candidate)

        initial_score = -cost0

        simplex = [
            (cost0, v0, cand0),
            (cost1, v1, cand1),
            (cost2, v2, cand2),
        ]

        alpha = 1.0  # Reflection
        gamma = 2.0  # Expansion
        rho = 0.5    # Contraction
        sigma = 0.5  # Shrink

        iterations = 0
        converged = False

        for it in range(self.max_iter):
            iterations += 1
            simplex.sort(key=lambda item: item[0])
            best_cost, best_v, best_cand = simplex[0]
            worst_cost, worst_v, _ = simplex[-1]
            second_worst_cost, _, _ = simplex[-2]

            if abs(worst_cost - best_cost) < 1.0e-4:
                converged = True
                break

            # Centroid of all vertices except worst
            c_e = 0.5 * (simplex[0][1][0] + simplex[1][1][0])
            c_d = 0.5 * (simplex[0][1][1] + simplex[1][1][1])

            # 1. Reflection
            r_e = _clamp(c_e + alpha * (c_e - worst_v[0]), self.elevon_bounds[0], self.elevon_bounds[1])
            r_d = _clamp(c_d + alpha * (c_d - worst_v[1]), self.dihedral_bounds[0], self.dihedral_bounds[1])
            cost_r, cand_r = self._evaluate_point(r_e, r_d, candidate)

            if best_cost <= cost_r < second_worst_cost:
                simplex[-1] = (cost_r, (r_e, r_d), cand_r)
                continue

            # 2. Expansion
            if cost_r < best_cost:
                e_e = _clamp(c_e + gamma * (r_e - c_e), self.elevon_bounds[0], self.elevon_bounds[1])
                e_d = _clamp(c_d + gamma * (r_d - c_d), self.dihedral_bounds[0], self.dihedral_bounds[1])
                cost_e, cand_e = self._evaluate_point(e_e, e_d, candidate)
                if cost_e < cost_r:
                    simplex[-1] = (cost_e, (e_e, e_d), cand_e)
                else:
                    simplex[-1] = (cost_r, (r_e, r_d), cand_r)
                continue

            # 3. Contraction
            con_e = _clamp(c_e + rho * (worst_v[0] - c_e), self.elevon_bounds[0], self.elevon_bounds[1])
            con_d = _clamp(c_d + rho * (worst_v[1] - c_d), self.dihedral_bounds[0], self.dihedral_bounds[1])
            cost_con, cand_con = self._evaluate_point(con_e, con_d, candidate)
            if cost_con < worst_cost:
                simplex[-1] = (cost_con, (con_e, con_d), cand_con)
                continue

            # 4. Shrink
            v1_new = (
                _clamp(best_v[0] + sigma * (simplex[1][1][0] - best_v[0]), self.elevon_bounds[0], self.elevon_bounds[1]),
                _clamp(best_v[1] + sigma * (simplex[1][1][1] - best_v[1]), self.dihedral_bounds[0], self.dihedral_bounds[1]),
            )
            v2_new = (
                _clamp(best_v[0] + sigma * (simplex[2][1][0] - best_v[0]), self.elevon_bounds[0], self.elevon_bounds[1]),
                _clamp(best_v[1] + sigma * (simplex[2][1][1] - best_v[1]), self.dihedral_bounds[0], self.dihedral_bounds[1]),
            )
            cost1_new, cand1_new = self._evaluate_point(v1_new[0], v1_new[1], candidate)
            cost2_new, cand2_new = self._evaluate_point(v2_new[0], v2_new[1], candidate)
            simplex[1] = (cost1_new, v1_new, cand1_new)
            simplex[2] = (cost2_new, v2_new, cand2_new)

        simplex.sort(key=lambda item: item[0])
        final_best_cost, final_v, final_cand = simplex[0]
        final_score = -final_best_cost
        clamped_best_elevon = _clamp(final_v[0], self.elevon_bounds[0], self.elevon_bounds[1])
        clamped_best_dihedral = _clamp(final_v[1], self.dihedral_bounds[0], self.dihedral_bounds[1])

        # Update candidate with optimized configuration if improved
        if final_score >= initial_score:
            candidate.config = final_cand.config
            candidate.geometry = final_cand.geometry
            candidate.benchmark = final_cand.benchmark
            candidate.raw_genome = final_cand.raw_genome
            if final_cand.geometry:
                candidate.descriptor = final_cand.geometry.compute_behavior_descriptor()
            else:
                candidate.descriptor = final_cand.descriptor

        return TrimResult(
            candidate_id=candidate.candidate_id,
            optimal_elevon_deg=clamped_best_elevon,
            optimal_dihedral_deg=clamped_best_dihedral,
            initial_score=initial_score,
            optimized_score=final_score,
            iterations=iterations,
            converged=converged,
        )
