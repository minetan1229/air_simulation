"""Issue #144: finite-volume face-flux sign must be outward-normal.

The canonical residual is R = sum(F . n_out * A) and U_new = U_old -
dt*R/V.  A face flux pointing +i/+j/+k contributes +F to the low-index
cell and -F to the high-index cell.  Uniform freestream does NOT catch
a global sign flip (R=0 either way), so these tests use a density blob
perturbation whose outward divergence has a definite sign.
"""

from __future__ import annotations

import numpy as np

from physics.cfd import CFDConfig2D, CFDConfig3D
from physics.cfd.grid2d import generate_c_grid
from physics.cfd.grid3d import generate_wing_grid_3d
from physics.cfd.solver2d import Solver2D
from physics.cfd.solver3d import Solver3D


def test_uniform_flow_residual_zero_both_signs():
    """Freestream preservation holds regardless of sign (documents the trap)."""
    cfg = CFDConfig2D(mach=0.2, alpha_deg=0.0)
    grid = generate_c_grid("0012", ni=32, nj=16, farfield_r=10.0)
    solver = Solver2D(cfg, grid)
    res = solver.evaluate_residuals()
    assert abs(float(np.sum(res[..., 0]))) < 1.0e-3


def test_density_blob_diverges_outward_2d():
    """A density blob in the interior must give positive outward divergence.

    With R = sum(F.n_out*A), a positive density perturbation must flow
    outward, so the residual inside the blob is positive (mass source
    term) and the RK2 update U -= dt*R/V decreases the blob.  The old
    reversed-sign code gave negative divergence (blob grows).
    """
    cfg = CFDConfig2D(mach=0.2, alpha_deg=0.0)
    grid = generate_c_grid("0012", ni=24, nj=12, farfield_r=10.0)
    solver = Solver2D(cfg, grid)
    solver.u[..., 0] += 0.05  # uniform +delta rho
    # Re-centre: keep farfield cells at free density
    solver.u[:, -2:, 0] = solver.u_free[0]
    res = solver.evaluate_residuals()
    interior = res[4:-4, 2:-3, 0]
    assert float(np.mean(interior)) > 0.0, (
        "density blob must diverge outward (positive R), "
        f"got mean R={float(np.mean(interior))}"
    )


def test_1d_mass_wave_propagates_downwind_2d():
    """Uniform +x flow over a rho step must advance the step downstream."""
    cfg = CFDConfig2D(mach=0.3, alpha_deg=0.0)
    grid = generate_c_grid("0012", ni=24, nj=12, farfield_r=10.0)
    solver = Solver2D(cfg, grid)
    # rho step downstream of the first columns, then one Euler step at
    # the interface: the dense (left) side must lose mass to the right.
    solver.u[:, :, 0] = solver.u_free[0]
    solver.u[8:14, 4:-2, 0] = solver.u_free[0] * 1.5
    res = solver.evaluate_residuals()
    dt = min(solver.compute_stable_dt(), 1e-4)
    vol = grid.vol[..., np.newaxis]
    u_after = solver.u - (dt / vol) * res
    front = (slice(9, 12), slice(4, -2), 0)
    rho_before = float(np.mean(solver.u[front]))
    rho_after = float(np.mean(u_after[front]))
    assert rho_after < rho_before, (
        "dense blob must lose mass rightward (advection downstream), "
        f"before={rho_before} after={rho_after}"
    )


def test_density_blob_diverges_outward_3d():
    """3D analogue: positive interior divergence for a density blob."""
    cfg = CFDConfig3D(mach=0.2, alpha_deg=0.0)
    grid = generate_wing_grid_3d(
        aspect_ratio=6.0,
        taper_ratio=0.7,
        ni=16,
        nj=10,
        nk=6,
        farfield_r=10.0,
    )
    solver = Solver3D(cfg, grid)
    solver.u[..., 0] += 0.05
    solver.u[:, -2:, :, 0] = solver.u_free[0]
    solver.u[:, :, -2:, 0] = solver.u_free[0]
    res = solver.evaluate_residuals()
    interior = res[3:-3, 1:-3, 1:-2, 0]
    assert float(np.mean(interior)) > 0.0, (
        "3D density blob must diverge outward (positive R), "
        f"got mean R={float(np.mean(interior))}"
    )
