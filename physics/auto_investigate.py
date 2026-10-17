"""Auto-Investigate domain for the 3D Paper Airplane CFD Simulator.

This module provides backwards compatibility with all previous imports
by re-exporting the modularised components from :mod:`physics.investigate`.
"""

from __future__ import annotations

from .investigate import (
    ADJUSTABLE_VARIABLES,
    AdjustableVariable,
    DiagnosisReport,
    InvestigationResult,
    SensitivityPoint,
    SensitivityResult,
    TuningAdvice,
    compute_flight_metrics,
    diagnose_flight,
    generate_advice,
    get_target_metric,
    investigate_airplane,
    run_focused_tuning,
    run_sensitivity_scan,
    simulate_trajectory,
)

# For backward compatibility with internal helper names
_compute_flight_metrics = compute_flight_metrics
_get_target_metric = get_target_metric
_simulate_trajectory = simulate_trajectory

__all__ = [
    "ADJUSTABLE_VARIABLES",
    "AdjustableVariable",
    "DiagnosisReport",
    "InvestigationResult",
    "SensitivityPoint",
    "SensitivityResult",
    "TuningAdvice",
    "diagnose_flight",
    "generate_advice",
    "investigate_airplane",
    "run_focused_tuning",
    "run_sensitivity_scan",
]
