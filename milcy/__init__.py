"""milcy Algorithm Package (formerly v3).

Provides the complete headless non-GA morphogenetic pipeline:
- MAP-Elites illumination quality-diversity search
- MCTS 20-step parametric origami generation
- Nelder-Mead glide trim optimization
- Multi-dimensional robustness grid evaluation
"""

from __future__ import annotations

import v3
from optimization.cli import main, run_pipeline

__all__ = [
    "run_pipeline",
    "main",
    "v3",
]
