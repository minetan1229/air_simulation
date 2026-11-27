"""Compatibility alias pointing directly to :mod:`physics.benchmark.properties`."""
from __future__ import annotations

import importlib
import sys

_canonical = importlib.import_module('physics.benchmark.properties')
sys.modules[__name__] = _canonical
