"""Tests for 3D CFD anisotropic cell CFL stable time step (Issue #103)."""

import numpy as np
import pytest

from physics.cfd.grid3d import generate_wing_grid_3d
from physics.cfd.solver3d import CFDConfig3D, Solver3D


def test_compute_stable_dt_anisotropic_scaling():
    """Verify compute_stable_dt respects directional cell dimensions and does not overestimate dt on thin cells."""
    grid = generate_wing_grid_3d(ni=8, nj=6, nk=4, farfield_r=10.0)
    cfg = CFDConfig3D(mach=0.2, alpha_deg=2.0, cfl=0.1)
    solver = Solver3D(cfg, grid)

    dt = solver.compute_stable_dt()
    assert dt > 0.0
    assert np.isfinite(dt)

    # Physical check: On the finest wall cells (j=0), cell height dy is small (~ 1e-3 or less)
    # The stable time step must be strictly smaller than the acoustic time across the whole domain
    max_char_speed = solver.v_inf + solver.c_inf
    # The volumetric cube root estimate (old buggy method)
    h_cube = float(np.min(grid.vol ** (1.0 / 3.0)))
    dt_cube = cfg.cfl * h_cube / max_char_speed

    # The new directional method must be more restrictive (smaller or equal) than the isotropic cube root
    # because of the thin wall boundary layers
    assert dt <= dt_cube + 1e-12, f"Anisotropic dt ({dt}) must not exceed isotropic cube root dt ({dt_cube})"


def test_compute_stable_dt_cfl_proportionality():
    """Verify compute_stable_dt scales linearly with the CFL parameter."""
    grid = generate_wing_grid_3d(ni=8, nj=6, nk=4, farfield_r=10.0)
    cfg1 = CFDConfig3D(cfl=0.1)
    cfg2 = CFDConfig3D(cfl=0.2)
    s1 = Solver3D(cfg1, grid)
    s2 = Solver3D(cfg2, grid)

    dt1 = s1.compute_stable_dt()
    dt2 = s2.compute_stable_dt()

    assert np.isclose(dt2, 2.0 * dt1, rtol=1e-5)
