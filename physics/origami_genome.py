"""Backward-compatibility re-export.

The canonical module is :mod:`geometry.origami_genome`.
"""
import geometry.origami_genome as _orig
from geometry.origami_genome import *  # noqa: F401,F403
from geometry.origami_genome import (  # noqa: F401
    LAYER_ORDER_AUTO,
    MAX_FOLD_ANGLE_DEG,
    MIN_FOLD_ANGLE_DEG,
    _separated_polygons,
)

for _k, _v in _orig.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v

if hasattr(_orig, "__all__"):
    __all__ = getattr(_orig, "__all__")
