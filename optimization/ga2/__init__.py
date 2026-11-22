"""Generation 2 of the genetic search architecture.

The package holds the four stage evaluation pipeline, the hybrid fold
genome, the quality-diversity archive, the hardware scaling profiles and
the checkpoint machinery of the ultra large genetic search:

``scaling``
    the three hardware tiers and the budget of every one of them.
``genome``
    the variable length hybrid fold programme (3 to 8 folds).
``stage0``
    the geometry and topology filter.
``stage1``
    the batched 6-DoF screen with a captured CUDA graph.
``stage2``
    the quasi static structural screen.
``stage3``
    the non stationary flow solver with the two way fluid structure coupling.
``archive``
    the MAP-Elites archive and the Pareto front.
``surrogate``
    the accumulated flow database and the correction model.
``vram``
    the fixed VRAM pool of the generation loop.
``checkpoint``
    the atomic generation checkpoint and the hot migration between tiers.
``runner``
    the generation loop that ties the stages together.

The modules are imported lazily so that a partial installation (for example
a machine without a GPU) can still use the parts that work there.
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "archive",
    "checkpoint",
    "genome",
    "runner",
    "scaling",
    "stage0",
    "stage1",
    "stage2",
    "stage3",
    "surrogate",
    "vram",
]


def __getattr__(name: str) -> Any:
    """Import a submodule of the package on first use."""
    if name in __all__:
        module = importlib.import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(list(globals()) + __all__))
