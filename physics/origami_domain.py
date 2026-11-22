"""Backward-compatibility re-export.

The canonical module is :mod:`geometry.origami_domain`.
"""
import geometry.origami_domain as _orig
from geometry.origami_domain import *  # noqa: F401,F403
from geometry.origami_domain import (  # noqa: F401
    _HALF_LENGTH,
    _HALF_WIDTH,
    _collar_crease,
    _corner_crease,
    _mirror_crease,
)

for _k, _v in _orig.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v

if hasattr(_orig, "__all__"):
    __all__ = getattr(_orig, "__all__")
