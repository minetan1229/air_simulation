"""Compatibility alias pointing directly to :mod:`optimization.map_elites.mutation`."""
from __future__ import annotations

import importlib
import sys

_canonical = importlib.import_module('optimization.map_elites.mutation')
sys.modules[__name__] = _canonical
