"""geometry.v3_origami: 20-step parametric origami compiler, adapter, and MCTS generator."""

from .adapter import OrigamiAdapter, validate_tier0
from .compiler import ParametricOrigamiCompiler
from .kernel_25d import (
    FoldedPolygon2D,
    MacroFoldStep,
    Sequential2DOrigamiKernel,
    compile_macro_origami,
)
from .mcts import MCTSOrigamiGenerator

__all__ = [
    "ParametricOrigamiCompiler",
    "OrigamiAdapter",
    "validate_tier0",
    "MCTSOrigamiGenerator",
    "Sequential2DOrigamiKernel",
    "MacroFoldStep",
    "FoldedPolygon2D",
    "compile_macro_origami",
]

