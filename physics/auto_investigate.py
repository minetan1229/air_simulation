"""Backward-compatibility re-export.

The canonical module is :mod:`optimization.auto_investigate`.
"""
import optimization.auto_investigate as _orig
from optimization.auto_investigate import *  # noqa: F401,F403

for _k, _v in _orig.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v

if hasattr(_orig, "__all__"):
    __all__ = getattr(_orig, "__all__")
