"""Physics package for the 3D paper airplane simulator.

The canonical public contracts live in :mod:`core.config_schema` and the rotation
algebra in :mod:`core.geometry_utils`. This package provides aerodynamic kernels,
6-DoF numerical integration, atmospheric models, wind fields, and structural stress.
"""

from __future__ import annotations

__all__ = [
    "config_schema",
    "geometry_utils",
    "aero_domain",
    "atmosphere_domain",
    "wind_domain",
    "stress_domain",
    "mesh_domain",
    "origami_domain",
    "origami_genome",
    "flight_sim",
    "parallel_engine",
    "optimizer_domain",
    "job_manager",
    "storage_domain",
    "gravity_modifier",
]
