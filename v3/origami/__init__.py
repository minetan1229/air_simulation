"""Backward-compatibility re-export.

The canonical module is :mod:`geometry.v3_origami`.
"""
from geometry.v3_origami import (  # noqa: F401
    MCTSOrigamiGenerator,
    OrigamiAdapter,
    ParametricOrigamiCompiler,
    validate_tier0,
)

__all__ = [
    "ParametricOrigamiCompiler",
    "OrigamiAdapter",
    "validate_tier0",
    "MCTSOrigamiGenerator",
]
