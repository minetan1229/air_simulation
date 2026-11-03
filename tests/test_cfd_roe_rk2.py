"""Roe shear-wave completeness and RK2 intermediate-state evaluation (#101, #104, #105, #106)."""

from __future__ import annotations

import numpy as np
import pytest

from physics.cfd import CFDConfig2D, CFDConfig3D
from physics.cfd.grid2d import generate_c_grid
from physics.cfd.grid3d import generate_wing_grid_3d
from physics.cfd.solver2d import Solver2D
from physics.cfd.solver3d import Solver3D

GAMMA = 1.4


def _cons2d(rho: float, u: float, v: float, p: float) -> np.ndarray:
    e = p / ((GAMMA - 1.0) * rho) + 0.5 * (u * u + v * v)
    return np.array([rho, rho * u, rho * v, rho * e], dtype=np.float64)


def _cons3d(rho: float, u: float, v: float, w: float, p: float) -> np.ndarray:
    e = p / ((GAMMA - 1.0) * rho) + 0.5 * (u * u + v * v + w * w)
    return np.array([rho, rho * u, rho * v, rho * w, rho * e], dtype=np.float64)


@pytest.fixture
def solver2d() -> Solver2D:
    cfg = CFDConfig2D(mach=0.3, alpha_deg=0.0, gamma=GAMMA)
    grid = generate_c_grid("0012", ni=16, nj=8, farfield_r=8.0)
    return Solver2D(cfg, grid)


@pytest.fixture
def solver3d() -> Solver3D:
    cfg = CFDConfig3D(mach=0.3, alpha_deg=0.0, gamma=GAMMA, max_steps=2)
    grid = generate_wing_grid_3d(
        airfoil="0012",
        ni=12,
        nj=6,
        nk=6,
        aspect_ratio=4.0,
        taper_ratio=0.6,
        sweep_deg=0.0,
        farfield_r=6.0,
    )
    return Solver3D(cfg, grid)


def test_2d_roe_pure_shear_has_tangential_dissipation(solver2d: Solver2D) -> None:
    """#106: density/pressure/normal-velocity jumps of zero must still dissipate dvt."""
    rho, p = 1.0, 1.0 / GAMMA
    u_l = _cons2d(rho, 0.4, 0.0, p)
    u_r = _cons2d(rho, 0.4, 0.25, p)
    nx = np.array(1.0)
    ny = np.array(0.0)

    flux = solver2d.compute_flux(u_l, u_r, nx, ny)
    rho_l, u_lv, v_lv, p_l, _ = solver2d.get_primitive(u_l)
    rho_r, u_rv, v_rv, p_r, _ = solver2d.get_primitive(u_r)
    vn_l = u_lv * nx + v_lv * ny
    vn_r = u_rv * nx + v_rv * ny
    h_l = (u_l[3] + p_l) / rho_l
    h_r = (u_r[3] + p_r) / rho_r
    f_avg = 0.5 * np.array(
        [
            rho_l * vn_l + rho_r * vn_r,
            rho_l * u_lv * vn_l + p_l * nx + rho_r * u_rv * vn_r + p_r * nx,
            rho_l * v_lv * vn_l + p_l * ny + rho_r * v_rv * vn_r + p_r * ny,
            rho_l * h_l * vn_l + rho_r * h_r * vn_r,
        ]
    )
    diss = 2.0 * (f_avg - flux)
    assert abs(float(diss[2])) > 1.0e-8
    assert abs(float(diss[0])) < 1.0e-10


def test_2d_roe_uniform_oblique_flow_is_non_dissipative(solver2d: Solver2D) -> None:
    """Uniform states on an oblique face must recover the physical flux."""
    u_state = _cons2d(1.0, 0.3, 0.1, 1.0 / GAMMA)
    nx = np.array(1.0 / np.sqrt(2.0))
    ny = np.array(1.0 / np.sqrt(2.0))
    flux = solver2d.compute_flux(u_state, u_state, nx, ny)
    rho, u_vel, v_vel, p, _ = solver2d.get_primitive(u_state)
    vn = u_vel * nx + v_vel * ny
    h = (u_state[3] + p) / rho
    physical = np.array(
        [rho * vn, rho * u_vel * vn + p * nx, rho * v_vel * vn + p * ny, rho * h * vn]
    )
    assert np.allclose(flux, physical, atol=1.0e-12)


def test_2d_roe_contact_discontinuity_dissipates_density(solver2d: Solver2D) -> None:
    """Entropy wave: equal p and velocity, unequal density."""
    p = 1.0 / GAMMA
    u_l = _cons2d(1.0, 0.2, 0.0, p)
    u_r = _cons2d(1.4, 0.2, 0.0, p)
    flux = solver2d.compute_flux(u_l, u_r, np.array(1.0), np.array(0.0))
    rho_l, u_lv, v_lv, p_l, _ = solver2d.get_primitive(u_l)
    rho_r, u_rv, v_rv, p_r, _ = solver2d.get_primitive(u_r)
    vn_l = u_lv
    vn_r = u_rv
    h_l = (u_l[3] + p_l) / rho_l
    h_r = (u_r[3] + p_r) / rho_r
    f_avg0 = 0.5 * (rho_l * vn_l + rho_r * vn_r)
    assert abs(float(flux[0] - f_avg0)) > 1.0e-8


def test_2d_rk2_stage2_evaluates_intermediate_state(solver2d: Solver2D) -> None:
    """#105: Heun stage 2 must call residuals at U* rather than U^n."""
    solver2d.u[..., 1] += 0.05 * solver2d.u[..., 0]
    seen: list[np.ndarray] = []
    original = Solver2D.evaluate_residuals

    def _wrapped(self: Solver2D, u_state: np.ndarray | None = None) -> np.ndarray:
        u = self.u if u_state is None else u_state
        seen.append(np.array(u, copy=True))
        return original(self, u_state)

    Solver2D.evaluate_residuals = _wrapped  # type: ignore[method-assign]
    try:
        solver2d.step_rk2(1.0e-4)
    finally:
        Solver2D.evaluate_residuals = original  # type: ignore[method-assign]

    assert len(seen) >= 2
    assert not np.allclose(seen[0], seen[1], atol=1.0e-14)


def test_3d_roe_pure_shear_has_tangential_dissipation(solver3d: Solver3D) -> None:
    """#104: a pure tangential-velocity jump must produce shear dissipation."""
    rho, p = 1.0, 1.0 / GAMMA
    u_l = _cons3d(rho, 0.4, 0.0, 0.0, p)
    u_r = _cons3d(rho, 0.4, 0.2, 0.15, p)
    ax = np.array(1.0)
    ay = np.array(0.0)
    az = np.array(0.0)
    flux = solver3d.compute_flux(u_l, u_r, ax, ay, az)

    rho_l, ul, vl, wl, p_l, _ = solver3d.get_primitive(u_l)
    rho_r, ur, vr, wr, p_r, _ = solver3d.get_primitive(u_r)
    vn_l = ul * ax + vl * ay + wl * az
    vn_r = ur * ax + vr * ay + wr * az
    h_l = (u_l[4] + p_l) / rho_l
    h_r = (u_r[4] + p_r) / rho_r
    f_avg = 0.5 * np.array(
        [
            rho_l * vn_l + rho_r * vn_r,
            rho_l * ul * vn_l + p_l * ax + rho_r * ur * vn_r + p_r * ax,
            rho_l * vl * vn_l + p_l * ay + rho_r * vr * vn_r + p_r * ay,
            rho_l * wl * vn_l + p_l * az + rho_r * wr * vn_r + p_r * az,
            rho_l * h_l * vn_l + rho_r * h_r * vn_r,
        ]
    )
    diss = 2.0 * (f_avg - flux)
    shear_mom = abs(float(diss[2])) + abs(float(diss[3]))
    assert shear_mom > 1.0e-8
    assert abs(float(diss[0])) < 1.0e-10


def test_3d_roe_uniform_oblique_flow_is_non_dissipative(solver3d: Solver3D) -> None:
    u_state = _cons3d(1.0, 0.25, 0.1, 0.05, 1.0 / GAMMA)
    inv = 1.0 / np.sqrt(3.0)
    ax = np.array(inv)
    ay = np.array(inv)
    az = np.array(inv)
    flux = solver3d.compute_flux(u_state, u_state, ax, ay, az)
    rho, u_vel, v_vel, w_vel, p, _ = solver3d.get_primitive(u_state)
    vn = u_vel * ax + v_vel * ay + w_vel * az
    h = (u_state[4] + p) / rho
    area = 1.0
    physical = area * np.array(
        [
            rho * vn,
            rho * u_vel * vn + p * ax,
            rho * v_vel * vn + p * ay,
            rho * w_vel * vn + p * az,
            rho * h * vn,
        ]
    )
    assert np.allclose(flux, physical, atol=1.0e-12)


def test_3d_rk2_stage2_evaluates_intermediate_state(solver3d: Solver3D) -> None:
    """#101: Heun stage 2 must call residuals at U* rather than U^n."""
    solver3d.u[..., 1] += 0.05 * solver3d.u[..., 0]
    seen: list[np.ndarray] = []
    original = Solver3D.evaluate_residuals

    def _wrapped(self: Solver3D, u_state: np.ndarray | None = None) -> np.ndarray:
        u = self.u if u_state is None else u_state
        seen.append(np.array(u, copy=True))
        return original(self, u_state)

    Solver3D.evaluate_residuals = _wrapped  # type: ignore[method-assign]
    try:
        solver3d.step_rk2(1.0e-4)
    finally:
        Solver3D.evaluate_residuals = original  # type: ignore[method-assign]

    assert len(seen) >= 2
    assert not np.allclose(seen[0], seen[1], atol=1.0e-14)
