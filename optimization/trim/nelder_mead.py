"""Stage 3: Bounded Nelder-Mead local continuous parameter optimization."""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

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
    """Bounded Nelder-Mead simplex optimizer with stall-plateau restart."""

    def __init__(
        self,
        adapter: Optional[OrigamiAdapter] = None,
        benchmark: Optional[DeterministicFlightBenchmark] = None,
        max_iter: int = 20,
        elevon_bounds: Tuple[float, float] = (-5.0, 10.0),
        dihedral_bounds: Tuple[float, float] = (0.0, 10.0),
        max_restarts: int = 2,
        cost_tol: float = 1.0e-4,
        param_tol: float = 5.0e-2,
    ) -> None:
        self.adapter = adapter or OrigamiAdapter()
        self.benchmark = benchmark or DeterministicFlightBenchmark()
        self.max_iter = max(1, int(max_iter))
        self.elevon_bounds = elevon_bounds
        self.dihedral_bounds = dihedral_bounds
        self.max_restarts = max(0, int(max_restarts))
        self.cost_tol = float(cost_tol)
        self.param_tol = float(param_tol)

    def _project(self, elevon: float, dihedral: float) -> Tuple[float, float, float]:
        """Project (elevon, dihedral) into box bounds and return smooth violation penalty."""
        e_lo, e_hi = self.elevon_bounds
        d_lo, d_hi = self.dihedral_bounds
        clamped_e = _clamp(elevon, e_lo, e_hi)
        clamped_d = _clamp(dihedral, d_lo, d_hi)
        e_viol = abs(elevon - clamped_e) / max(1.0e-6, e_hi - e_lo)
        d_viol = abs(dihedral - clamped_d) / max(1.0e-6, d_hi - d_lo)
        barrier_penalty = 0.05 * (e_viol * e_viol + d_viol * d_viol)
        return clamped_e, clamped_d, barrier_penalty

    @staticmethod
    def _simplex_geometry(
        simplex: List[Tuple[float, Tuple[float, float], AircraftCandidate]]
    ) -> Tuple[float, float]:
        """Return (diameter, 2D triangle area) of the 3-vertex simplex."""
        p0, p1, p2 = simplex[0][1], simplex[1][1], simplex[2][1]
        d01 = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        d12 = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        d20 = math.hypot(p0[0] - p2[0], p0[1] - p2[1])
        diameter = max(d01, d12, d20)
        area = 0.5 * abs(
            (p1[0] - p0[0]) * (p2[1] - p0[1]) - (p2[0] - p0[0]) * (p1[1] - p0[1])
        )
        return diameter, area

    def _evaluate_point(
        self,
        elevon: float,
        dihedral: float,
        base_candidate: AircraftCandidate,
    ) -> Tuple[float, Tuple[float, float], AircraftCandidate]:
        """Evaluate projected (elevon, dihedral) and return (cost, feasible_pt, candidate)."""
        clamped_e, clamped_d, barrier_penalty = self._project(elevon, dihedral)
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
        cand.benchmark = bench
        score = (
            bench.composite_score
            if (bench.valid and math.isfinite(bench.composite_score))
            else 0.0
        )
        cost = -score + barrier_penalty
        return cost, (clamped_e, clamped_d), cand

    def _build_simplex(
        self,
        e_center: float,
        d_center: float,
        scale: float,
        base_candidate: AircraftCandidate,
    ) -> List[Tuple[float, Tuple[float, float], AircraftCandidate]]:
        """Build a well-conditioned, strictly feasible 2D simplex inside the box."""
        e_lo, e_hi = self.elevon_bounds
        d_lo, d_hi = self.dihedral_bounds
        e0 = _clamp(e_center, e_lo, e_hi)
        d0 = _clamp(d_center, d_lo, d_hi)

        step_e = scale * (e_hi - e_lo)
        step_d = scale * (d_hi - d_lo)
        if e0 + step_e > e_hi:
            step_e = -step_e
        if d0 + step_d > d_hi:
            step_d = -step_d

        p0 = (e0, d0)
        p1 = (_clamp(e0 + step_e, e_lo, e_hi), d0)
        p2 = (e0, _clamp(d0 + step_d, d_lo, d_hi))

        c0, v0, cand0 = self._evaluate_point(p0[0], p0[1], base_candidate)
        c1, v1, cand1 = self._evaluate_point(p1[0], p1[1], base_candidate)
        c2, v2, cand2 = self._evaluate_point(p2[0], p2[1], base_candidate)
        return [(c0, v0, cand0), (c1, v1, cand1), (c2, v2, cand2)]

    def optimize_trim(self, candidate: AircraftCandidate) -> TrimResult:
        """Optimize candidate's elevon and dihedral angles via bounded Nelder-Mead."""
        e0 = _clamp(
            0.5 * (candidate.config.wing.elevon_left_deg + candidate.config.wing.elevon_right_deg),
            self.elevon_bounds[0],
            self.elevon_bounds[1],
        )
        d0 = _clamp(
            candidate.config.wing.dihedral_deg,
            self.dihedral_bounds[0],
            self.dihedral_bounds[1],
        )
        simplex = self._build_simplex(e0, d0, scale=0.12, base_candidate=candidate)
        initial_score = max(0.0, -(simplex[0][0]))

        alpha = 1.0
        gamma = 2.0
        rho = 0.5
        sigma = 0.5

        iterations = 0
        converged = False
        restarts_used = 0

        e_mid = 0.5 * (self.elevon_bounds[0] + self.elevon_bounds[1])
        d_mid = 0.5 * (self.dihedral_bounds[0] + self.dihedral_bounds[1])
        restart_anchors = [
            (e_mid, d_mid),
            (
                _clamp(1.5, self.elevon_bounds[0], self.elevon_bounds[1]),
                _clamp(3.0, self.dihedral_bounds[0], self.dihedral_bounds[1]),
            ),
        ]

        global_best = min(simplex, key=lambda item: item[0])

        for _ in range(self.max_iter):
            iterations += 1
            simplex.sort(key=lambda item: item[0])
            best_cost, best_v, best_cand = simplex[0]
            worst_cost, worst_v, _ = simplex[-1]
            second_worst_cost, _, _ = simplex[-2]

            if best_cost < global_best[0]:
                global_best = simplex[0]

            diameter, area = self._simplex_geometry(simplex)
            cost_spread = abs(worst_cost - best_cost)
            in_stall_plateau = best_cost >= -1.0e-6
            is_degenerate = area < 1.0e-5

            if not in_stall_plateau and cost_spread < self.cost_tol and diameter < self.param_tol:
                converged = True
                break

            if (in_stall_plateau or is_degenerate) and restarts_used < self.max_restarts:
                anchor = restart_anchors[restarts_used % len(restart_anchors)]
                restarts_used += 1
                simplex = self._build_simplex(
                    anchor[0], anchor[1], scale=0.18, base_candidate=candidate
                )
                continue

            c_e = 0.5 * (simplex[0][1][0] + simplex[1][1][0])
            c_d = 0.5 * (simplex[0][1][1] + simplex[1][1][1])

            r_e_raw = c_e + alpha * (c_e - worst_v[0])
            r_d_raw = c_d + alpha * (c_d - worst_v[1])
            cost_r, v_r, cand_r = self._evaluate_point(r_e_raw, r_d_raw, candidate)

            if best_cost <= cost_r < second_worst_cost:
                simplex[-1] = (cost_r, v_r, cand_r)
                continue

            if cost_r < best_cost:
                e_e_raw = c_e + gamma * (v_r[0] - c_e)
                e_d_raw = c_d + gamma * (v_r[1] - c_d)
                cost_e, v_e, cand_e = self._evaluate_point(e_e_raw, e_d_raw, candidate)
                if cost_e < cost_r:
                    simplex[-1] = (cost_e, v_e, cand_e)
                else:
                    simplex[-1] = (cost_r, v_r, cand_r)
                continue

            if cost_r < worst_cost:
                con_e_raw = c_e + rho * (v_r[0] - c_e)
                con_d_raw = c_d + rho * (v_r[1] - c_d)
            else:
                con_e_raw = c_e + rho * (worst_v[0] - c_e)
                con_d_raw = c_d + rho * (worst_v[1] - c_d)

            cost_con, v_con, cand_con = self._evaluate_point(con_e_raw, con_d_raw, candidate)
            if cost_con < worst_cost:
                simplex[-1] = (cost_con, v_con, cand_con)
                continue

            v1_raw = (
                best_v[0] + sigma * (simplex[1][1][0] - best_v[0]),
                best_v[1] + sigma * (simplex[1][1][1] - best_v[1]),
            )
            v2_raw = (
                best_v[0] + sigma * (simplex[2][1][0] - best_v[0]),
                best_v[1] + sigma * (simplex[2][1][1] - best_v[1]),
            )
            cost1_new, v1_new, cand1_new = self._evaluate_point(v1_raw[0], v1_raw[1], candidate)
            cost2_new, v2_new, cand2_new = self._evaluate_point(v2_raw[0], v2_raw[1], candidate)
            simplex[1] = (cost1_new, v1_new, cand1_new)
            simplex[2] = (cost2_new, v2_new, cand2_new)

        simplex.sort(key=lambda item: item[0])
        if simplex[0][0] < global_best[0]:
            global_best = simplex[0]

        _, final_v, final_cand = global_best
        final_score = (
            float(final_cand.benchmark.composite_score)
            if (final_cand.benchmark and final_cand.benchmark.valid)
            else 0.0
        )
        clamped_best_elevon = _clamp(final_v[0], self.elevon_bounds[0], self.elevon_bounds[1])
        clamped_best_dihedral = _clamp(final_v[1], self.dihedral_bounds[0], self.dihedral_bounds[1])

        if final_score > initial_score:
            candidate.config = final_cand.config
            candidate.geometry = final_cand.geometry
            candidate.benchmark = final_cand.benchmark
            candidate.raw_genome = final_cand.raw_genome
            if candidate.geometry and candidate.benchmark:
                try:
                    stability = min(
                        float(candidate.benchmark.low_speed.stability),
                        float(candidate.benchmark.mid_speed.stability),
                    )
                    candidate.descriptor = candidate.geometry.compute_behavior_descriptor(stability=stability)
                except Exception:
                    candidate.descriptor = final_cand.descriptor
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
