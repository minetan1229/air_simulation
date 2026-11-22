"""Backward-compatibility re-export.

The canonical module is :mod:`infrastructure.records_domain`.
"""
import infrastructure.records_domain as _orig
from infrastructure.records_domain import *  # noqa: F401,F403

for _k, _v in _orig.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v

if hasattr(_orig, "__all__"):
    __all__ = getattr(_orig, "__all__")
