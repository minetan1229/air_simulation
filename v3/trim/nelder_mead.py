"""Compatibility alias pointing directly to :mod:`optimization.trim.nelder_mead`."""
from __future__ import annotations

import importlib
import sys

_canonical = importlib.import_module('optimization.trim.nelder_mead')
sys.modules[__name__] = _canonical
