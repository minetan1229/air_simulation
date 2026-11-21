"""Geometry package — shape generation, mesh I/O, and origami folding.

This package handles all geometric operations including procedural origami
crease-pattern generation, 3D mesh import/export (glTF, OBJ, STL), and
geometry auditing. It depends on :mod:`core` for data contracts and may
reference :mod:`physics` for aerodynamic property extraction.

Modules
-------
origami_domain
    Procedural origami crease-pattern and folding engine.
origami_genome
    Genetic encoding of origami designs with mutation/crossover.
origami_geometry_audit
    Validation of origami geometry vs analytical airfoil properties.
mesh_domain
    3D mesh import (glTF/OBJ/STL), panel aerodynamics, and property extraction.
"""

from __future__ import annotations

__all__ = [
    "origami_domain",
    "origami_genome",
    "origami_geometry_audit",
    "mesh_domain",
]
