"""v3.physics: Core physics wrappers, deterministic horizontal benchmark, and properties extraction."""

from .benchmark import DeterministicFlightBenchmark, evaluate_horizontal_glide
from .properties import extract_airframe_geometry, compute_behavior_descriptor

__all__ = [
    "DeterministicFlightBenchmark",
    "evaluate_horizontal_glide",
    "extract_airframe_geometry",
    "compute_behavior_descriptor",
]
