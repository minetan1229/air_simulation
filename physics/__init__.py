"""Physics package for the 3D paper airplane simulator.

The public contract lives in :mod:`physics.config_schema` and the rotation
algebra in :mod:`physics.geometry_utils`; every other module is a pure
function/collection-of-classes layer built on top of those two.
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
    "flight_sim",
    "parallel_engine",
    "optimizer_domain",
    "job_manager",
    "storage_domain",
    "gravity_modifier",
]
