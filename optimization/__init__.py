"""Optimization package — search, evolutionary algorithms, and parameter tuning.

This package contains all optimization and search algorithms including genetic
algorithms (GA), CMA-ES, Pareto archive management, MAP-Elites illumination,
and automated parameter investigation. It depends on :mod:`core` for data
contracts, :mod:`physics` for simulation evaluation, and :mod:`geometry` for
origami genome operations.

Modules
-------
optimizer_domain
    Multi-objective optimization algorithms (GA, CMA-ES, Nelder-Mead, etc.).
ga_domain
    Autonomous island-model genetic research engine.
ga_config
    GA hyperparameters and configuration parser.
ga_cma
    CMA-ES (Covariance Matrix Adaptation Evolution Strategy).
pareto_archive
    Pareto frontier and crowding distance management.
auto_investigate
    Facade for automated flight investigation pipeline.
"""

from __future__ import annotations

__all__ = [
    "optimizer_domain",
    "ga_domain",
    "ga_config",
    "ga_cma",
    "pareto_archive",
    "auto_investigate",
]
