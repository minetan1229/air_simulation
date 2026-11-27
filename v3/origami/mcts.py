"""Compatibility alias pointing directly to :mod:`geometry.v3_origami.mcts`."""
from __future__ import annotations

import importlib
import sys

_canonical = importlib.import_module('geometry.v3_origami.mcts')
sys.modules[__name__] = _canonical
