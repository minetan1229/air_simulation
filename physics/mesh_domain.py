"""Backward-compatibility re-export.

The canonical module is :mod:`geometry.mesh_domain`.
"""
import geometry.mesh_domain as _orig
from geometry.mesh_domain import *  # noqa: F401,F403

for _k, _v in _orig.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v

try:
    from geometry.mesh_domain import __all__ as __all__  # noqa: F401
except ImportError:
    pass
