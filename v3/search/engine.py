"""Compatibility alias pointing directly to :mod:`optimization.map_elites.engine`."""
from __future__ import annotations

import importlib
import sys

_canonical = importlib.import_module('optimization.map_elites.engine')
sys.modules[__name__] = _canonical
