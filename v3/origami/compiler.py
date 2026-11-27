"""Compatibility alias pointing directly to :mod:`geometry.v3_origami.compiler`."""
from __future__ import annotations

import importlib
import sys

_canonical = importlib.import_module('geometry.v3_origami.compiler')
sys.modules[__name__] = _canonical
