"""Backward-compatibility re-export.

The canonical module is :mod:`optimization.trim`.
"""
from optimization.trim import *  # noqa: F401,F403
from optimization.trim.nelder_mead import NelderMeadTrimOptimizer  # noqa: F401

__all__ = ["NelderMeadTrimOptimizer"]
