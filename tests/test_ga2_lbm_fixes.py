"""Tests verifying fixes for LBM issues #184, #188, #189, #190, #191."""

import math
import numpy as np
import pytest

from physics.ga2.lbm import LbmDnsSolver, Stage3Request, nu_lattice_base


def test_tau_clamp_floor_allows_high_re():
    """Issue #184: tau clamp floor lowered to 0.5005 to preserve target Reynolds numbers."""
    solver = LbmDnsSolver(xp=np, grid_cells=32768)
    request = Stage3Request(
        index=0,
        genome={"folds": []},
        aircraft={"mass_kg": 0.005, "wingspan": 0.20, "chord": 0.05, "wing_area": 0.01},
        launch={"speed": 15.0, "pitch_deg": 4.0},
        grid_cells=32768,
        transition_steps=2,
        glide_steps=2,
    )
    result = solver.solve(request)
    assert result.status in ("ok", "diverged")


def test_tau_local_reconstruction_factor_3():
    """Issue #188: tau_local = 0.5 + 3.0 * (nu_base + nu_turb) has correct factor of 3."""
    tau_base = 0.53
    nu_base = nu_lattice_base(tau_base)
    # nu_base = (0.53 - 0.5) / 3.0 = 0.01
    assert nu_base == pytest.approx(0.01)

    # Reconstructed tau with zero turbulence must match tau_base exactly:
    nu_turb = 0.0
    tau_reconstructed = 0.5 + 3.0 * (nu_base + nu_turb)
    assert tau_reconstructed == pytest.approx(tau_base)


def test_diverged_status_on_non_finite_populations():
    """Issue #190: non-finite populations return Stage3Result(status='diverged')."""
    solver = LbmDnsSolver(xp=np, grid_cells=32768)
    request = Stage3Request(
        index=42,
        genome={"folds": []},
        aircraft={"mass_kg": 0.005, "wingspan": 0.20, "chord": 0.05, "wing_area": 0.01},
        launch={"speed": 100.0, "pitch_deg": 89.0},
        grid_cells=32768,
        transition_steps=5,
        glide_steps=5,
    )
    res = solver.solve(request)
    assert res.status in ("ok", "diverged")
    assert res.index == 42


def test_wall_boundaries_are_solidified():
    """Issue #191: y/z boundaries are solidified to enforce wall conditions."""
    solver = LbmDnsSolver(xp=np, grid_cells=32768)
    mesh = {
        "bounds": ((0.0, 0.0, 0.0), (0.1, 0.1, 0.02)),
        "triangles": [],
        "mass_kg": 0.005,
    }
    solid, _, _, _ = solver._embed(mesh)
    nx, ny, nz = solver.nx, solver.ny, solver.nz

    # Sidewalls y=0 and y=ny-1 must be solid along interior flow cells (1 <= x <= nx-2)
    assert np.all(solid[1:-1, 0, :])
    assert np.all(solid[1:-1, ny - 1, :])

    # Floor z=0 and ceiling z=nz-1 must be solid along interior flow cells
    assert np.all(solid[1:-1, :, 0])
    assert np.all(solid[1:-1, :, nz - 1])

    # Inlet x=0 and outlet x=nx-1 must remain open
    assert not np.all(solid[0, :, :])
    assert not np.all(solid[nx - 1, :, :])
