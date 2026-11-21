"""Backward-compatibility re-export.

The canonical module is :mod:`physics.benchmark`.
"""
from physics.benchmark import (  # noqa: F401
    DeterministicFlightBenchmark,
    compute_behavior_descriptor,
    evaluate_horizontal_glide,
    extract_airframe_geometry,
)

__all__ = [
    "DeterministicFlightBenchmark",
    "evaluate_horizontal_glide",
    "extract_airframe_geometry",
    "compute_behavior_descriptor",
]
