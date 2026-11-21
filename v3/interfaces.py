"""Backward-compatibility re-export.

The canonical module is :mod:`core.interfaces`.
"""
from core.interfaces import *  # noqa: F401,F403

try:
    from core.interfaces import __all__ as __all__  # noqa: F401
except ImportError:
    pass
