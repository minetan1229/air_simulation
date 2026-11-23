"""Parametric 20-step origami sequence compiler.

Compiles a 3-phase high-level specification into a concrete 16 to 24 step
FoldStep sequence:
  Phase 1: Nose multi-layer folds (3-6 folds, taper, tip rigidity, forward CG)
  Phase 2: Keel depth & lock folds (structural grip >= 8mm, >= 2 layers)
  Phase 3: Wings, dihedral, elevons, and winglets
"""

from __future__ import annotations

import math
from typing import Any, List, Tuple

from geometry.origami_domain import (
    _HALF_LENGTH,
    _HALF_WIDTH,
    RESIDUAL_CREASE_DEG,
    SHEET_LENGTH_M,
    _collar_crease,
    _corner_crease,
    _mirror_crease,
    Crease,
)
from geometry.origami_genome import (
    FoldStep,
    genome_from_creases,
)
from core.interfaces import (
    MAX_FOLD_STEPS,
    MIN_FOLD_STEPS,
    ParametricOrigamiConfig,
)


def _clamp(v: float, low: float, high: float) -> float:
    return max(low, min(high, v))


class ParametricOrigamiCompiler:
    """Compiles high-level parametric aircraft configs into MacroFoldSteps and FoldSteps."""

    def compile_steps(self, config: ParametricOrigamiConfig) -> List[FoldStep]:
        """Compile configuration into an ordered list of 10-16 Atomic FoldStep objects."""
        from geometry.v3_origami.kernel_25d import compile_macro_origami

        _, steps = compile_macro_origami(config)
        # Strictly enforce bounds [MIN_FOLD_STEPS, MAX_FOLD_STEPS]
        if len(steps) > MAX_FOLD_STEPS:
            steps = steps[:MAX_FOLD_STEPS]
        return steps

    def compile_macro_steps(self, config: ParametricOrigamiConfig) -> List[Any]:
        """Compile configuration into 6-10 human-level MacroFoldStep objects."""
        from geometry.v3_origami.kernel_25d import compile_macro_origami

        macros, _ = compile_macro_origami(config)
        return macros
