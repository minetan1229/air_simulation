"""Backward-compatibility re-export.

The canonical module is :mod:`infrastructure.job_manager`.
"""
import infrastructure.job_manager as _orig
from infrastructure.job_manager import *  # noqa: F401,F403

for _k, _v in _orig.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v

try:
    from infrastructure.job_manager import __all__ as __all__  # noqa: F401
except ImportError:
    pass
