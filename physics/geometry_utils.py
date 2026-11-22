"""Backward-compatibility re-export.

The canonical module is :mod:`core.geometry_utils`.
"""
import core.geometry_utils as _orig
from core.geometry_utils import *  # noqa: F401,F403

for _k, _v in _orig.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v

if hasattr(_orig, "__all__"):
    __all__ = getattr(_orig, "__all__")
