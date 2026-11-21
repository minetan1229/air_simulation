"""Backward-compatibility re-export.

The canonical module is :mod:`geometry.origami_genome`.
"""
import geometry.origami_genome as _orig
from geometry.origami_genome import *  # noqa: F401,F403

for _k, _v in _orig.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v

try:
    from geometry.origami_genome import __all__ as __all__  # noqa: F401
except ImportError:
    pass
