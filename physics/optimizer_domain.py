"""Backward-compatibility re-export.

The canonical module is :mod:`optimization.optimizer_domain`.
"""
import optimization.optimizer_domain as _orig
from optimization.optimizer_domain import *  # noqa: F401,F403

for _k, _v in _orig.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v

if hasattr(_orig, "__all__"):
    __all__ = getattr(_orig, "__all__")
