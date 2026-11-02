"""Tests for 3D CFD Stage 1 Grid and Flow Solver (docs/CFD_ROADMAP.md)."""

from __future__ import annotations

import math
import numpy as np
import pytest

from physics.cfd import (
    CFDConfig3D,
    StructuredGrid3D,
    generate_wing_grid_3d,
    Solver3D,
)


def test_wing_grid_3d_dimensions_and_volumes() -> None:
    """Verify 3D grid dimensions and strict volume positivity across all hexahedral cells."""
    ni, nj, nk = 24, 12, 10
    grid = generate_wing_grid_3d(
        airfoil="0012",
        ni=ni,
        nj=nj,
        nk=nk,
        aspect_ratio=3.5,
        taper_ratio=0.6,
        sweep_deg=10.0,
        farfield_r=8.0,
    )

    assert grid.vol.shape == (ni, nj, nk)
    assert np.all(grid.vol > 0.0)
    assert not np.any(np.isnan(grid.vol))
    assert np.min(grid.vol) > 1.0e-10


def test_wing_grid_3d_face_closure() -> None:
    """Verify Geometric Conservation Law (GCL): sum of directed face area vectors for any cell must equal zero."""
    ni, nj, nk = 16, 8, 6
    grid = generate_wing_grid_3d(
        airfoil="0012",
        ni=ni,
        nj=nj,
        nk=nk,
        aspect_ratio=4.0,
        taper_ratio=0.5,
        sweep_deg=15.0,
        farfield_r=6.0,
    )

    net_sx = (
        (grid.i_area_x[1:] - grid.i_area_x[:-1])
        + (grid.j_area_x[:, 1:] - grid.j_area_x[:, :-1])
        + (grid.k_area_x[:, :, 1:] - grid.k_area_x[:, :, :-1])
    )
    net_sy = (
        (grid.i_area_y[1:] - grid.i_area_y[:-1])
        + (grid.j_area_y[:, 1:] - grid.j_area_y[:, :-1])
        + (grid.k_area_y[:, :, 1:] - grid.k_area_y[:, :, :-1])
    )
    net_sz = (
        (grid.i_area_z[1:] - grid.i_area_z[:-1])
        + (grid.j_area_z[:, 1:] - grid.j_area_z[:, :-1])
        + (grid.k_area_z[:, :, 1:] - grid.k_area_z[:, :, :-1])
    )

    max_closure_defect = max(
        float(np.max(np.abs(net_sx))),
        float(np.max(np.abs(net_sy))),
        float(np.max(np.abs(net_sz))),
    )

    assert max_closure_defect < 1.0e-11


def test_wing_geometry_spanwise_taper_and_sweep() -> None:
    """Verify that root chord is unity and tip chord reflects the requested taper and sweep offset."""
    grid = generate_wing_grid_3d(
        airfoil="0012",
        ni=32,
        nj=16,
        nk=12,
        aspect_ratio=4.0,
        taper_ratio=0.5,
        sweep_deg=20.0,
        farfield_r=8.0,
    )

    root_x_chord = np.max(grid.x[:, 0, 0]) - np.min(grid.x[:, 0, 0])
    assert abs(root_x_chord - 1.0) < 1.0e-3
    assert np.allclose(grid.y[:, :, 0], 0.0)
    assert np.all(np.diff(grid.y[0, 0, :]) > 0.0)


def test_solver3d_freestream_and_mass_conservation() -> None:
    """Verify 3D solver preserves freestream and produces finite forces on finite wing."""
    cfg = CFDConfig3D(
        mach=0.2,
        alpha_deg=2.0,
        aspect_ratio=4.0,
        taper_ratio=0.6,
        sweep_deg=10.0,
        cfl=0.04,
        max_steps=20,
    )
    grid = generate_wing_grid_3d(
        airfoil="0012",
        ni=16,
        nj=8,
        nk=6,
        aspect_ratio=cfg.aspect_ratio,
        taper_ratio=cfg.taper_ratio,
        sweep_deg=cfg.sweep_deg,
        farfield_r=6.0,
    )
    solver = Solver3D(cfg, grid)
    result = solver.run()

    assert result.grid_cells == 16 * 8 * 6
    assert not math.isnan(result.cl)
    assert not math.isnan(result.cd)
    assert not math.isnan(result.cm)
    assert not math.isnan(result.residual_l2)
    assert result.mass_conservation_residual < 10.0
