"""Backward-compatibility re-export.

The canonical module is :mod:`core.payload_contract`.
"""
import core.payload_contract as _orig
from core.payload_contract import *  # noqa: F401,F403

for _k, _v in _orig.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v

try:
    from core.payload_contract import __all__ as __all__  # noqa: F401
except ImportError:
    pass
