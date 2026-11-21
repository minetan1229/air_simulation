"""Backward-compatibility re-export.

The canonical module is :mod:`optimization.map_elites`.
"""
from optimization.map_elites import (  # noqa: F401
    MAPElitesEngine,
    MAPElitesGridArchive,
    mutate_parametric_config,
)

__all__ = [
    "MAPElitesGridArchive",
    "mutate_parametric_config",
    "MAPElitesEngine",
]
