"""Compatibility alias pointing directly to :mod:`optimization.eval.robustness`."""
from __future__ import annotations

import importlib
import sys

_canonical = importlib.import_module('optimization.eval.robustness')
sys.modules[__name__] = _canonical
