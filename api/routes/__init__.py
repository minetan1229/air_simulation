"""API Routes Package for 3D Paper Airplane CFD Simulator."""

from __future__ import annotations

from .jobs import jobs_bp
from .mesh import mesh_bp
from .meta import meta_bp
from .origami import origami_bp
from .physics import physics_bp
from .records import records_bp
from .simulation import sim_bp

__all__ = [
    "jobs_bp",
    "mesh_bp",
    "meta_bp",
    "origami_bp",
    "physics_bp",
    "records_bp",
    "sim_bp",
]
