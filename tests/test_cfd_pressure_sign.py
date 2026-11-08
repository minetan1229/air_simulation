"""Tests that CFD 2D/3D pressure force integration properly pushes body opposite outward normals.

Resolves Issue #145 and Issue #150:
- Verifies that solver.compute_forces() returns CL, CD, CM directly matching
  independent physical integration of -p_gauge * n * dS.
- Verifies that inverting the sign (+p_gauge * n * dS) fails assertions.
"""

from __future__ import annotations

import math
import numpy as np
import pytest

from physics.cfd.grid2d import generate_c_grid
from physics.cfd.grid3d import generate_wing_grid_3d as generate_grid3d
from physics.cfd.solver2d import CFDConfig2D, Solver2D
from physics.cfd.solver3d import CFDConfig3D, Solver3D


def _solver2d_p_wall() -> Solver2D:
    grid = generate_c_grid("0012", ni=16, nj=8, farfield_r=5.0)
    solver = Solver2D(CFDConfig2D(), grid)
    rho = np.full_like(solver.u[..., 0], 1.225)
    u = np.full_like(solver.u[..., 1], 340.0 * 1.225)
    v = np.zeros_like(solver.u[..., 2])
    solver.u = np.stack(
        [rho, u, v, np.full_like(rho, 1.225 * 1005 * 288)],
        axis=-1,
    ).copy()
    # Inject non-uniform wall pressure: lower surface higher pressure, upper surface lower pressure
    # to produce positive lift (fy > 0)
    rho_w = solver.u[:, 0, 0]
    u_w = solver.u[:, 0, 1] / rho_w
    v_w = solver.u[:, 0, 2] / rho_w

    # Grid in C-topology wraps around airfoil: lower surface has ny < 0 (pointing into fluid downwards),
    # so -p*ny pushes upward (+y, lift > 0).
    ny = solver.grid.j_ny[:, 0]
    # Set positive gauge pressure on cells where ny < 0
    p_gauge = np.where(ny < 0, 1000.0, 100.0)
    e_w = (solver.p_inf + p_gauge) / ((solver.gamma - 1.0) * rho_w) + 0.5 * (u_w**2 + v_w**2)
    solver.u[:, 0, 3] = rho_w * e_w
    return solver


def test_2d_pressure_traction_opposes_outward_normal() -> None:
    solver = _solver2d_p_wall()
    cl, cd, cm = solver.compute_forces()

    # Independently compute expected forces and coefficients
    _, _, _, p_wall, _ = solver.get_primitive(solver.u[:, 0, :])
    p_gauge = p_wall - solver.p_inf

    nx = solver.grid.j_nx[:, 0]
    ny = solver.grid.j_ny[:, 0]
    ds = solver.grid.j_len[:, 0]

    # Exact expected body force: -p_gauge * n * ds
    expected_fx = float(np.sum(-p_gauge * nx * ds))
    expected_fy = float(np.sum(-p_gauge * ny * ds))

    q_inf = 0.5 * solver.rho_inf * (solver.v_inf**2)
    alpha_rad = math.radians(solver.config.alpha_deg)

    expected_lift = expected_fy * math.cos(alpha_rad) - expected_fx * math.sin(alpha_rad)
    expected_drag = expected_fy * math.sin(alpha_rad) + expected_fx * math.cos(alpha_rad)

    expected_cl = float(expected_lift / q_inf)
    expected_cd = float(expected_drag / q_inf)

    xc_wall = 0.5 * (solver.grid.x[:-1, 0] + solver.grid.x[1:, 0])
    yc_wall = 0.5 * (solver.grid.y[:-1, 0] + solver.grid.y[1:, 0])
    arm_x = xc_wall - 0.25
    arm_y = yc_wall
    dfx = -p_gauge * nx * ds
    dfy = -p_gauge * ny * ds
    expected_cm = float(np.sum(arm_x * dfy - arm_y * dfx) / (q_inf * 1.0))

    # Assert that compute_forces() directly matches the physical formula
    assert cl == pytest.approx(expected_cl, rel=1e-12)
    assert cd == pytest.approx(expected_cd, rel=1e-12)
    assert cm == pytest.approx(expected_cm, rel=1e-12)

    # Physical verification: pressure on lower surface pushes upward -> cl > 0
    assert cl > 0.0

    # Inverted naive sign check (+p_gauge * n * ds): must differ and have opposite sign for lift
    naive_cl = -expected_cl
    assert cl != naive_cl
    assert (cl > 0 and naive_cl < 0)


def test_2d_uniform_pressure_gives_zero_net_force() -> None:
    """Uniform gauge pressure (p_wall = p_inf) must give zero net body force."""
    grid = generate_c_grid("0012", ni=16, nj=8, farfield_r=5.0)
    solver = Solver2D(CFDConfig2D(), grid)
    solver.u = np.stack(
        [
            np.full_like(solver.u[..., 0], 1.225),
            np.zeros_like(solver.u[..., 1]),
            np.zeros_like(solver.u[..., 2]),
            np.full_like(solver.u[..., 3], 1.225 * 1005 * 288),
        ],
        axis=-1,
    ).copy()
    rho = solver.u[..., 0]
    e = solver.p_inf / ((solver.gamma - 1.0) * rho)
    solver.u[..., 3] = rho * e
    cl, cd, cm = solver.compute_forces()
    assert abs(cl) < 1e-9
    assert abs(cd) < 1e-9
    assert abs(cm) < 1e-9


def test_3d_pressure_traction_sign() -> None:
    grid = generate_grid3d("0012", ni=16, nj=8, nk=8, aspect_ratio=6.0, farfield_r=5.0)
    solver = Solver3D(CFDConfig3D(), grid)
    rho = np.full_like(solver.u[..., 0], 1.225)
    u = np.full_like(solver.u[..., 1], 340.0 * 1.225)
    v = np.zeros_like(solver.u[..., 2])
    w = np.zeros_like(solver.u[..., 3])
    solver.u = np.stack(
        [rho, u, v, w, np.full_like(rho, 1.225 * 1005 * 288)],
        axis=-1,
    ).copy()

    # Inject non-uniform wall pressure: lower surface higher pressure
    kw = grid.nk_wing
    az = grid.j_area_z[:, 0, :kw]
    # In 3D C-H grid, lower surface has az < 0 (area vector pointing downwards into fluid)
    p_gauge = np.where(az < 0, 1000.0, 100.0)

    rho_w = solver.u[:, 0, :kw, 0]
    u_w = solver.u[:, 0, :kw, 1] / rho_w
    v_w = solver.u[:, 0, :kw, 2] / rho_w
    w_w = solver.u[:, 0, :kw, 3] / rho_w
    e_w = (solver.p_inf + p_gauge) / ((solver.gamma - 1.0) * rho_w) + 0.5 * (u_w**2 + v_w**2 + w_w**2)
    solver.u[:, 0, :kw, 4] = rho_w * e_w

    cl, cd, cm = solver.compute_forces()

    # Independent expected calculation
    ax = grid.j_area_x[:, 0, :kw]
    ay = grid.j_area_y[:, 0, :kw]
    az = grid.j_area_z[:, 0, :kw]

    expected_fx = 2.0 * float(np.sum(-p_gauge * ax))
    expected_fz = 2.0 * float(np.sum(-p_gauge * az))

    q_inf = 0.5 * solver.rho_inf * (solver.v_inf**2)
    alpha_rad = math.radians(solver.config.alpha_deg)
    s_ref = solver.config.aspect_ratio * ((1.0 + solver.config.taper_ratio) / 2.0) ** 2
    c_ref = (1.0 + solver.config.taper_ratio) / 2.0

    expected_lift = expected_fz * math.cos(alpha_rad) - expected_fx * math.sin(alpha_rad)
    expected_drag = expected_fz * math.sin(alpha_rad) + expected_fx * math.cos(alpha_rad)

    expected_cl = float(expected_lift / (q_inf * s_ref))
    expected_cd = float(expected_drag / (q_inf * s_ref))

    xc_wall = 0.25 * (
        solver.grid.x[:-1, 0, :kw]
        + solver.grid.x[1:, 0, :kw]
        + solver.grid.x[1:, 0, 1 : kw + 1]
        + solver.grid.x[:-1, 0, 1 : kw + 1]
    )
    zc_wall = 0.25 * (
        solver.grid.z[:-1, 0, :kw]
        + solver.grid.z[1:, 0, :kw]
        + solver.grid.z[1:, 0, 1 : kw + 1]
        + solver.grid.z[:-1, 0, 1 : kw + 1]
    )
    arm_x = xc_wall - 0.25
    arm_z = zc_wall
    dfx = 2.0 * -p_gauge * ax
    dfz = 2.0 * -p_gauge * az
    expected_my = float(np.sum(arm_z * dfx - arm_x * dfz))
    expected_cm = float(expected_my / (q_inf * s_ref * c_ref))

    assert cl == pytest.approx(expected_cl, rel=1e-12)
    assert cd == pytest.approx(expected_cd, rel=1e-12)
    assert cm == pytest.approx(expected_cm, rel=1e-12)

    # Physical verification: lower surface pressure pushes upward -> positive lift cl > 0
    assert cl > 0.0
    naive_cl = -expected_cl
    assert cl != naive_cl
    assert (cl > 0 and naive_cl < 0)
