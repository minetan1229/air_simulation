"""Sensitivity scan and focused tuning engine for Auto Investigation."""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from ..config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    FlightLogSample,
    LaunchConfig,
    SimulationSettings,
    clamp,
)
from ..flight_sim import FlightSimulator
from .diagnostics import compute_flight_metrics
from .models import ADJUSTABLE_VARIABLES, SensitivityResult


def simulate_trajectory(
    aircraft: AircraftConfig,
    environment: EnvironmentConfig,
    launch: LaunchConfig,
    duration_s: float = 5.0,
    dt_max: float = 0.05,
    max_substeps: int = 8,
    record: bool = True,
    adaptive: bool = False,
) -> Tuple[Dict[str, Any], List[FlightLogSample]]:
    """Run full 6-DoF simulator and return summary and sample history."""
    settings = SimulationSettings(
        dt_max=dt_max,
        dt_min=min(1.0e-4, dt_max),
        max_substeps=max_substeps,
        duration_s=duration_s,
        record_interval_s=max(dt_max, 0.05),
        time_scale=1.0,
        adaptive=adaptive,
        collision_enabled=True,
        restitution=0.15,
        ring_buffer_capacity=max(256, int(duration_s / dt_max) + 16),
    )
    sim = FlightSimulator(aircraft, environment, launch, settings)
    summary = sim.simulate(duration_s=duration_s, record=record)
    samples = sim.history() if record else []
    return summary, samples


def get_target_metric(metrics: Dict[str, Any], target_metric: str) -> float:
    if target_metric == "flight_time":
        return float(metrics.get("flight_time_s", 0.0))
    return float(metrics.get("distance_m", 0.0))


def run_sensitivity_scan(
    aircraft: AircraftConfig,
    environment: EnvironmentConfig,
    launch: LaunchConfig,
    target_metric: str = "distance",
    sample_steps: int = 5,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> List[SensitivityResult]:
    """Perform a 1-D sweep for every adjustable variable to evaluate sensitivity."""
    results: List[SensitivityResult] = []

    # Baseline run
    base_summary, base_samples = simulate_trajectory(aircraft, environment, launch, duration_s=6.0)
    base_metrics = compute_flight_metrics(
        base_summary, base_samples, abs(float(aircraft.stall_alpha_deg))
    )
    base_score = get_target_metric(base_metrics, target_metric)

    total_tasks = len(ADJUSTABLE_VARIABLES) * sample_steps
    completed = 0

    for var in ADJUSTABLE_VARIABLES:
        cur_val = getattr(
            aircraft if var.target_object == "aircraft" else launch, var.name
        )
        cur_val = float(cur_val)

        span_vals = np.linspace(var.low, var.high, sample_steps).tolist()
        if not any(abs(v - cur_val) < 1e-4 for v in span_vals):
            span_vals.append(cur_val)
        span_vals = sorted(set(round(v, 4) for v in span_vals))

        curve_points: List[Dict[str, float]] = []
        best_val = cur_val
        best_score = -1.0
        min_score = 1e9
        max_score = -1e9

        for val in span_vals:
            mod_air = copy.deepcopy(aircraft)
            mod_lau = copy.deepcopy(launch)

            if var.target_object == "aircraft":
                setattr(mod_air, var.name, val)
            else:
                setattr(mod_lau, var.name, val)

            summary, _ = simulate_trajectory(
                mod_air,
                environment,
                mod_lau,
                duration_s=4.0,
                dt_max=0.05,
                max_substeps=12,
                record=False,
            )
            dist_val = float(summary.get("distance_m", 0.0))
            time_val = float(summary.get("flight_time_s", 0.0))
            if target_metric == "flight_time":
                score = time_val
            elif target_metric == "ld_ratio":
                score = float(summary.get("max_ld_ratio", 0.0))
            elif target_metric == "stability":
                score = float(summary.get("stability", 0.0))
            else:
                score = dist_val

            curve_points.append(
                {
                    "value": float(val),
                    "score": float(score),
                    "distance_m": dist_val,
                    "flight_time_s": time_val,
                }
            )

            if score > best_score:
                best_score = score
                best_val = val
            if score > max_score:
                max_score = score
            if score < min_score:
                min_score = score

            completed += 1
            if progress_callback:
                progress_callback(
                    completed, total_tasks, f"Scanning {var.label_ja} ({val}{var.unit})"
                )

        score_spread = max(0.0, max_score - min_score)
        impact_pct = (
            (score_spread / max(base_score, 0.5)) * 100.0 if base_score > 0 else 0.0
        )

        results.append(
            SensitivityResult(
                name=var.name,
                category=var.category,
                label_en=var.label_en,
                label_ja=var.label_ja,
                unit=var.unit,
                baseline_val=cur_val,
                best_val=best_val,
                impact_score_pct=round(impact_pct, 1),
                description_ja=var.description_ja,
                curve=curve_points,
            )
        )

    results.sort(key=lambda r: r.impact_score_pct, reverse=True)
    return results


def run_focused_tuning(
    aircraft: AircraftConfig,
    environment: EnvironmentConfig,
    launch: LaunchConfig,
    target_metric: str = "distance",
    max_evaluations: int = 50,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Tuple[AircraftConfig, LaunchConfig, Dict[str, Any], List[FlightLogSample]]:
    """Run a focused local optimizer only modifying adjustable variables."""
    tuning_vars = [
        v
        for v in ADJUSTABLE_VARIABLES
        if v.name
        in (
            "elevon_deg",
            "dihedral_deg",
            "fold_tip_deg",
            "cg_offset_x",
            "launch_angle_deg",
            "launch_speed",
        )
    ]

    base_air = copy.deepcopy(aircraft)
    base_lau = copy.deepcopy(launch)

    init_vals = []
    bounds = []
    for v in tuning_vars:
        val = getattr(
            base_air if v.target_object == "aircraft" else base_lau, v.name
        )
        init_vals.append(float(val))
        bounds.append((float(v.low), float(v.high)))

    init_x: np.ndarray = np.array(
        [clamp(init_vals[i], bounds[i][0], bounds[i][1]) for i in range(len(init_vals))],
        dtype=float,
    )

    eval_count = 0
    best_score = -1.0
    best_x: np.ndarray = init_x.copy()

    def objective(x: np.ndarray) -> float:
        nonlocal eval_count, best_score, best_x
        eval_count += 1

        mod_air = copy.deepcopy(base_air)
        mod_lau = copy.deepcopy(base_lau)

        for i, v in enumerate(tuning_vars):
            clipped = clamp(float(x[i]), bounds[i][0], bounds[i][1])
            if v.target_object == "aircraft":
                setattr(mod_air, v.name, clipped)
            else:
                setattr(mod_lau, v.name, clipped)

        if eval_count > max_evaluations:
            return -float(best_score)

        summary, _ = simulate_trajectory(
            mod_air,
            environment,
            mod_lau,
            duration_s=4.0,
            dt_max=0.05,
            max_substeps=12,
            record=False,
        )
        dist_val = float(summary.get("distance_m", 0.0))
        time_val = float(summary.get("flight_time_s", 0.0))
        if target_metric == "flight_time":
            score = time_val
        elif target_metric == "ld_ratio":
            score = float(summary.get("max_ld_ratio", 0.0))
        elif target_metric == "stability":
            score = float(summary.get("stability", 0.0))
        else:
            score = dist_val

        if score > best_score:
            best_score = score
            best_x = x.copy()

        if progress_callback:
            progress_callback(
                eval_count,
                max_evaluations,
                f"Tuning iteration {eval_count}/{max_evaluations}",
            )

        return -float(score)

    try:
        from scipy.optimize import minimize
        minimize(
            objective,
            init_x,
            method="Nelder-Mead",
            bounds=bounds,
            options={"maxiter": max_evaluations, "maxfev": max_evaluations, "disp": False},
        )
    except Exception:
        pass

    rec_air = copy.deepcopy(base_air)
    rec_lau = copy.deepcopy(base_lau)
    for i, v in enumerate(tuning_vars):
        final_val = clamp(float(best_x[i]), bounds[i][0], bounds[i][1])
        if v.target_object == "aircraft":
            setattr(rec_air, v.name, round(final_val, 2))
        else:
            setattr(rec_lau, v.name, round(final_val, 2))

    rec_summary, rec_samples = simulate_trajectory(rec_air, environment, rec_lau)
    return rec_air, rec_lau, rec_summary, rec_samples
