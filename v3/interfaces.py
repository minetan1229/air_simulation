"""Backward-compatibility re-export.

The canonical module is :mod:`core.interfaces`.
"""
import core.interfaces as _orig
from core.interfaces import *  # noqa: F401,F403

if hasattr(_orig, "__all__"):
    __all__ = getattr(_orig, "__all__")
