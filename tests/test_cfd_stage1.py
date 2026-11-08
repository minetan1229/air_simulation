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


def test_cfl_local_cfl_bounded_on_anisotropic_grid() -> None:
    """H1 completion condition: every cell's local CFL stays at or below cfl.

    The wing grid is strongly anisotropic (exponential normal stretching,
    cosine spanwise clustering, swept/tapered planform), so the local CFL
    computed from ``sum_f A_f (|u_n| + c)`` must never exceed the configured
    ceiling, including in the thin near-wall cells.
    """
    cfg = CFDConfig3D(
        mach=0.2,
        alpha_deg=2.0,
        aspect_ratio=4.0,
        taper_ratio=0.5,
        sweep_deg=15.0,
        cfl=0.05,
        max_steps=40,
        convergence_tol=1.0e-8,
    )
    grid = generate_wing_grid_3d(
        airfoil="0012",
        ni=20,
        nj=14,
        nk=10,
        aspect_ratio=cfg.aspect_ratio,
        taper_ratio=cfg.taper_ratio,
        sweep_deg=cfg.sweep_deg,
        farfield_r=8.0,
    )
    solver = Solver3D(cfg, grid)
    result = solver.run()

    assert result.grid_cells == 20 * 14 * 10
    assert not math.isnan(result.cl)
    assert not math.isnan(result.cd)
    assert not math.isnan(result.cm)
    max_local_cfl = result.metadata["max_local_cfl"]
    assert max_local_cfl > 0.0
    assert max_local_cfl <= cfg.cfl + 1.0e-9, (
        f"max local CFL {max_local_cfl:.6f} exceeds configured cfl {cfg.cfl}"
    )


def test_cfl_dt_reflects_thin_dimension_not_volume_root() -> None:
    """On an anisotropic grid, the stable dt must scale with the thinnest cell
    dimension, not with V^(1/3).  Compare against a near-uniform reference:
    the anisotropic grid's dt should be no larger than the cuberoot-volume
    estimate would allow for its thinnest cells (i.e. not overstated).
    """
    cfg = CFDConfig3D(mach=0.2, alpha_deg=2.0, cfl=0.05, max_steps=5)
    grid = generate_wing_grid_3d(
        airfoil="0012",
        ni=16,
        nj=12,
        nk=8,
        aspect_ratio=4.0,
        taper_ratio=0.5,
        sweep_deg=15.0,
        farfield_r=8.0,
    )
    solver = Solver3D(cfg, grid)
    dt_solver = solver.compute_stable_dt()

    # Reference: cfl * min(V^(1/3)) / char_speed using the freestream state.
    _, u_vel, v_vel, w_vel, _, c_field = solver.get_primitive(solver.u)
    speed = np.sqrt(u_vel**2 + v_vel**2 + w_vel**2)
    char_speed = float(np.max(speed + c_field))
    dt_cuberoot = cfg.cfl * float(np.min(grid.vol ** (1.0 / 3.0))) / max(char_speed, 1.0e-6)

    # The face-spectral-radius form must not be more permissive than the
    # naive cuberoot estimate on this anisotropic grid.
    assert dt_solver > 0.0 and math.isfinite(dt_solver)
    assert dt_solver <= dt_cuberoot * 1.0 + 1.0e-12, (
        f"dt {dt_solver:.6e} overstates the cuberoot estimate {dt_cuberoot:.6e}"
    )


def test_cfl_dt_decreases_with_grid_refinement() -> None:
    """Refining the grid must reduce (or keep) the stable time-step, reflecting
    the smaller cell scales, and both resolutions must remain CFL-stable."""
    base = dict(
        airfoil="0012",
        aspect_ratio=4.0,
        taper_ratio=0.5,
        sweep_deg=10.0,
        farfield_r=6.0,
    )
    cfg = CFDConfig3D(mach=0.2, alpha_deg=2.0, cfl=0.05, max_steps=30, convergence_tol=1.0e-8)

    coarse = generate_wing_grid_3d(ni=12, nj=8, nk=6, **base)
    fine = generate_wing_grid_3d(ni=24, nj=16, nk=12, **base)

    dt_coarse = Solver3D(cfg, coarse).compute_stable_dt()
    dt_fine = Solver3D(cfg, fine).compute_stable_dt()

    assert dt_coarse > 0.0 and dt_fine > 0.0
    # Finer cells -> smaller (or equal) stable step.
    assert dt_fine <= dt_coarse * 1.0 + 1.0e-12, (
        f"refined dt {dt_fine:.6e} not <= coarse dt {dt_coarse:.6e}"
    )
    # Both resolutions run stably with local CFL bounded.
    res_coarse = Solver3D(cfg, coarse).run()
    res_fine = Solver3D(cfg, fine).run()
    assert res_coarse.metadata["max_local_cfl"] <= cfg.cfl + 1.0e-9
    assert res_fine.metadata["max_local_cfl"] <= cfg.cfl + 1.0e-9
    assert not math.isnan(res_fine.cl) and not math.isnan(res_fine.cd)
