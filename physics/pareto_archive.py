"""Backward-compatibility re-export.

The canonical module is :mod:`optimization.pareto_archive`.
"""
import optimization.pareto_archive as _orig
from optimization.pareto_archive import *  # noqa: F401,F403

for _k, _v in _orig.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v

try:
    from optimization.pareto_archive import __all__ as __all__  # noqa: F401
except ImportError:
    pass
