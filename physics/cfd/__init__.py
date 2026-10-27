"""2D CFD Foundation Package (Stage 0).

Implements baseline finite-volume structured flow solver and grid generator
for 2D airfoil validation, supporting Stage 0 of docs/CFD_ROADMAP.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass(frozen=True)
class CFDConfig2D:
    """Configuration for 2D CFD airfoil calculation."""

    mach: float = 0.2
    alpha_deg: float = 2.0
    reynolds: float = 1.0e5
    gamma: float = 1.4
    cfl: float = 0.05
    max_steps: int = 1500
    convergence_tol: float = 1.0e-6
    airfoil: str = "0012"
    n_chord: int = 64
    n_normal: int = 32
    farfield_radius_chords: float = 15.0


@dataclass(frozen=True)
class CFDResult2D:
    """Result from 2D CFD calculation."""

    converged: bool
    iterations: int
    residual_l2: float
    cl: float
    cd: float
    cm: float
    mass_conservation_residual: float
    grid_cells: int
    metadata: Dict[str, Any] = field(default_factory=dict)
