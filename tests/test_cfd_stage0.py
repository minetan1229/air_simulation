"""Tests for 2D CFD Stage 0 foundation (docs/CFD_ROADMAP.md)."""

from __future__ import annotations

import math
import numpy as np
import pytest

from physics.cfd import CFDConfig2D
from physics.cfd.grid2d import generate_c_grid, naca4_coordinates
from physics.cfd.solver2d import Solver2D


def test_naca4_airfoil_geometry() -> None:
    """Verify NACA 0012 geometry conforms to analytical formula and closes at TE."""
    x, y = naca4_coordinates("0012", 64)
    # Starts at TE (x=1), passes LE (x=0), returns to TE (x=1)
    assert len(x) > 60
    assert abs(x[0] - 1.0) < 1.0e-5
    assert abs(x[-1] - 1.0) < 1.0e-5
    # Max thickness at ~30% chord is ~0.12
    idx_mid = np.argmin(np.abs(x[: len(x) // 2] - 0.3))
    assert 0.05 < y[idx_mid] < 0.07  # half thickness is 0.06


def test_grid_geometry_and_metrics() -> None:
    """Verify grid cells have strictly positive volumes and correct metric relations."""
    grid = generate_c_grid("0012", ni=32, nj=16, farfield_r=10.0)
    assert grid.vol.shape == (32, 16)
    assert np.all(grid.vol > 0.0)

    # Check normal vectors have unit length
    i_norm_len = np.hypot(grid.i_nx, grid.i_ny)
    j_norm_len = np.hypot(grid.j_nx, grid.j_ny)
    assert np.allclose(i_norm_len, 1.0, atol=1.0e-10)
    assert np.allclose(j_norm_len, 1.0, atol=1.0e-10)


def test_freestream_preservation() -> None:
    """Verify that a uniform free-stream flow maintains negligible spatial residuals in the field."""
    cfg = CFDConfig2D(mach=0.2, alpha_deg=0.0)
    grid = generate_c_grid("0012", ni=32, nj=16, farfield_r=10.0)
    solver = Solver2D(cfg, grid)

    res = solver.evaluate_residuals()
    # In interior cells, Roe flux on smooth grid exactly cancels uniform freestream
    interior_res_per_vol = np.abs(res[:, 1:-1, :] / grid.vol[:, 1:-1, np.newaxis])
    assert np.max(interior_res_per_vol) < 0.01

    # Total mass flux residual across entire domain should be near zero
    assert abs(float(np.sum(res[..., 0]))) < 1.0e-3


def test_solver_conservation_stage0_quick() -> None:
    """Run a quick execution to verify mass conservation residual and stable integration."""
    cfg = CFDConfig2D(mach=0.2, alpha_deg=2.0, max_steps=40, cfl=0.15)
    grid = generate_c_grid("0012", ni=32, nj=16, farfield_r=8.0)
    solver = Solver2D(cfg, grid)

    result = solver.run()
    assert result.grid_cells == 32 * 16
    assert not math.isnan(result.cl)
    assert not math.isnan(result.cd)
    assert not math.isnan(result.cm)
    assert not math.isnan(result.residual_l2)
    # Mass conservation residual must be finite and bounded
    assert result.mass_conservation_residual < 1.0
