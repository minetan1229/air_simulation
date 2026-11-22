"""Backward-compatibility re-export.

The canonical module is :mod:`optimization.eval`.
"""
from optimization.eval import *  # noqa: F401,F403
from optimization.eval.robustness import RobustnessEvaluator  # noqa: F401

__all__ = ["RobustnessEvaluator"]
