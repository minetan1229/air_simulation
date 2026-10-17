"""Auto Investigation and Tuning Pipeline for 3D Paper Airplane CFD Simulator."""

from __future__ import annotations

import copy
from typing import Callable, Dict, List, Optional

from ..config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    FlightLogSample,
    LaunchConfig,
    as_dict,
)
from .advice import generate_advice
from .diagnostics import compute_flight_metrics, diagnose_flight
from .models import (
    ADJUSTABLE_VARIABLES,
    AdjustableVariable,
    DiagnosisReport,
    InvestigationResult,
    SensitivityPoint,
    SensitivityResult,
    TuningAdvice,
)
from .tuning import (
    get_target_metric,
    run_focused_tuning,
    run_sensitivity_scan,
    simulate_trajectory,
)

__all__ = [
    "ADJUSTABLE_VARIABLES",
    "AdjustableVariable",
    "DiagnosisReport",
    "InvestigationResult",
    "SensitivityPoint",
    "SensitivityResult",
    "TuningAdvice",
    "compute_flight_metrics",
    "diagnose_flight",
    "generate_advice",
    "get_target_metric",
    "investigate_airplane",
    "run_focused_tuning",
    "run_sensitivity_scan",
    "simulate_trajectory",
]


def investigate_airplane(
    aircraft: AircraftConfig,
    environment: Optional[EnvironmentConfig] = None,
    launch: Optional[LaunchConfig] = None,
    target_metric: str = "distance",
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    sample_steps: int = 3,
    max_tune_evaluations: int = 15,
    skip_scan: bool = False,
    skip_tuning: bool = False,
) -> InvestigationResult:
    """Run full automated investigation pipeline: Diagnosis -> Sensitivity -> Tuning -> Advice."""
    env = environment if environment is not None else EnvironmentConfig()
    lau = launch if launch is not None else LaunchConfig()

    # Step 1: Baseline Trajectory & Diagnosis
    base_summary, base_samples = simulate_trajectory(aircraft, env, lau)
    base_metrics = compute_flight_metrics(
        base_summary, base_samples, abs(float(aircraft.stall_alpha_deg))
    )
    diagnosis = diagnose_flight(aircraft, env, lau, base_summary, base_samples)

    if progress_callback:
        progress_callback(10, 100, f"診断完了: {diagnosis.headline_ja}")

    # Step 2: Parametric Sensitivity Scan (1-D Sweeps)
    if skip_scan:
        sensitivities = []
    else:
        def scan_cb(curr: int, tot: int, msg: str) -> None:
            if progress_callback:
                pct = 10 + int((curr / max(tot, 1)) * 50)
                progress_callback(pct, 100, msg)

        sensitivities = run_sensitivity_scan(
            aircraft,
            env,
            lau,
            target_metric=target_metric,
            sample_steps=sample_steps,
            progress_callback=scan_cb,
        )

    # Step 3: Focused Multi-Variable Tuning
    if skip_tuning:
        rec_air = copy.deepcopy(aircraft)
        rec_lau = copy.deepcopy(lau)
        rec_summary = base_summary
        rec_samples = base_samples
        rec_metrics = base_metrics
    else:
        def tune_cb(curr: int, tot: int, msg: str) -> None:
            if progress_callback:
                pct = 60 + int((curr / max(tot, 1)) * 30)
                progress_callback(pct, 100, msg)

        rec_air, rec_lau, rec_summary, rec_samples = run_focused_tuning(
            aircraft,
            env,
            lau,
            target_metric=target_metric,
            max_evaluations=max_tune_evaluations,
            progress_callback=tune_cb,
        )
        rec_metrics = compute_flight_metrics(
            rec_summary, rec_samples, abs(float(rec_air.stall_alpha_deg))
        )

    # Step 4: Advice Generation
    advice_list = generate_advice(
        diagnosis, aircraft, rec_air, lau, rec_lau, sensitivities
    )

    if progress_callback:
        progress_callback(100, 100, "調査完了")

    base_dist = base_metrics["distance_m"]
    rec_dist = rec_metrics["distance_m"]
    dist_gain_pct = (
        ((rec_dist - base_dist) / max(base_dist, 0.1)) * 100.0 if base_dist > 0 else 0.0
    )

    base_time = base_metrics["flight_time_s"]
    rec_time = rec_metrics["flight_time_s"]
    time_gain_pct = (
        ((rec_time - base_time) / max(base_time, 0.1)) * 100.0 if base_time > 0 else 0.0
    )

    def downsample(samples: List[FlightLogSample], max_pts: int = 120) -> List[Dict[str, float]]:
        if not samples:
            return []
        step = max(1, len(samples) // max_pts)
        pts = samples[::step]
        if pts[-1] != samples[-1]:
            pts.append(samples[-1])
        return [
            {
                "time_s": round(s.time_s, 3),
                "x_m": round(s.x_m, 3),
                "y_m": round(s.y_m, 3),
                "altitude_m": round(s.altitude_m, 3),
                "speed_mps": round(s.speed_mps, 2),
                "alpha_deg": round(s.alpha_deg, 2),
                "roll_deg": round(s.roll_deg, 2),
                "pitch_deg": round(s.pitch_deg, 2),
                "ld_ratio": round(s.ld_ratio, 2),
            }
            for s in pts
        ]

    baseline_payload = {
        "aircraft": as_dict(aircraft),
        "launch": as_dict(lau),
        "metrics": base_metrics,
        "track": downsample(base_samples),
    }

    recommended_payload = {
        "aircraft": as_dict(rec_air),
        "launch": as_dict(rec_lau),
        "metrics": rec_metrics,
        "track": downsample(rec_samples),
    }

    summary_payload = {
        "target_metric": target_metric,
        "distance_gain_pct": round(dist_gain_pct, 1),
        "time_gain_pct": round(time_gain_pct, 1),
        "base_distance_m": round(base_dist, 2),
        "rec_distance_m": round(rec_dist, 2),
        "base_flight_time_s": round(base_time, 2),
        "rec_flight_time_s": round(rec_time, 2),
        "primary_issue": diagnosis.headline_ja,
    }

    return InvestigationResult(
        target_metric=target_metric,
        baseline=baseline_payload,
        diagnosis=as_dict(diagnosis),
        sensitivities=[as_dict(s) for s in sensitivities],
        recommended=recommended_payload,
        advice_list=[as_dict(a) for a in advice_list],
        summary=summary_payload,
    )
