"""3D Compressible Finite-Volume Solver for Stage 1 validation.

Solves 3D Euler equations over structured hexahedral grids with Roe flux splitting,
supporting 3D wing validation (induced drag, tip vortex, Helmbold comparison)
under Stage 1 of docs/CFD_ROADMAP.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Tuple

import numpy as np

from physics.cfd.grid3d import StructuredGrid3D


@dataclass(frozen=True)
class CFDConfig3D:
    """Configuration for 3D CFD finite wing calculation."""

    mach: float = 0.2
    alpha_deg: float = 2.0
    beta_deg: float = 0.0
    gamma: float = 1.4
    cfl: float = 0.05
    max_steps: int = 500
    convergence_tol: float = 1.0e-5
    airfoil: str = "0012"
    aspect_ratio: float = 4.0
    taper_ratio: float = 0.5
    sweep_deg: float = 15.0


@dataclass(frozen=True)
class CFDResult3D:
    """Result from 3D CFD wing calculation."""

    converged: bool
    iterations: int
    residual_l2: float
    cl: float
    cd: float
    cm: float
    mass_conservation_residual: float
    grid_cells: int
    metadata: Dict[str, Any] = field(default_factory=dict)


class Solver3D:
    """Cell-centered finite-volume solver for 3D compressible flow over finite wings."""

    def __init__(self, config: CFDConfig3D, grid: StructuredGrid3D) -> None:
        self.config = config
        self.grid = grid
        self.gamma = config.gamma

        self.ni, self.nj, self.nk = grid.vol.shape

        # Conserved variables U: [rho, rho*u, rho*v, rho*w, rho*E]
        self.u = np.zeros((self.ni, self.nj, self.nk, 5), dtype=np.float64)

        # Free-stream reference values
        self.rho_inf = 1.0
        self.p_inf = 1.0 / self.gamma
        self.c_inf = 1.0
        self.v_inf = config.mach * self.c_inf

        alpha_rad = math.radians(config.alpha_deg)
        beta_rad = math.radians(config.beta_deg)

        self.u_inf = self.v_inf * math.cos(alpha_rad) * math.cos(beta_rad)
        self.v_flow_inf = -self.v_inf * math.sin(beta_rad)
        self.w_inf = self.v_inf * math.sin(alpha_rad) * math.cos(beta_rad)

        e_inf = self.p_inf / ((self.gamma - 1.0) * self.rho_inf) + 0.5 * (
            self.u_inf**2 + self.v_flow_inf**2 + self.w_inf**2
        )
        self.E_inf = e_inf

        self.u_free = np.array(
            [
                self.rho_inf,
                self.rho_inf * self.u_inf,
                self.rho_inf * self.v_flow_inf,
                self.rho_inf * self.w_inf,
                self.rho_inf * self.E_inf,
            ],
            dtype=np.float64,
        )

        # Initialize domain with freestream
        self.u[:, :, :] = self.u_free

    def get_primitive(
        self, u_state: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Convert conserved variables U to primitive variables: (rho, u, v, w, p, c)."""
        rho = np.maximum(u_state[..., 0], 1.0e-6)
        u_vel = u_state[..., 1] / rho
        v_vel = u_state[..., 2] / rho
        w_vel = u_state[..., 3] / rho
        e_tot = u_state[..., 4] / rho
        kinetic = 0.5 * (u_vel**2 + v_vel**2 + w_vel**2)
        p = np.maximum((self.gamma - 1.0) * rho * (e_tot - kinetic), 1.0e-6)
        c = np.sqrt(self.gamma * p / rho)
        return rho, u_vel, v_vel, w_vel, p, c

    def compute_flux(
        self,
        u_l: np.ndarray,
        u_r: np.ndarray,
        ax: np.ndarray,
        ay: np.ndarray,
        az: np.ndarray,
    ) -> np.ndarray:
        """Compute 3D Roe flux vector across face with area vector (ax, ay, az)."""
        area = np.sqrt(ax**2 + ay**2 + az**2)
        nx = ax / np.maximum(area, 1.0e-12)
        ny = ay / np.maximum(area, 1.0e-12)
        nz = az / np.maximum(area, 1.0e-12)

        rho_l, u_l_vel, v_l_vel, w_l_vel, p_l, c_l = self.get_primitive(u_l)
        rho_r, u_r_vel, v_r_vel, w_r_vel, p_r, c_r = self.get_primitive(u_r)

        vn_l = u_l_vel * nx + v_l_vel * ny + w_l_vel * nz
        vn_r = u_r_vel * nx + v_r_vel * ny + w_r_vel * nz

        h_l = (u_l[..., 4] + p_l) / rho_l
        h_r = (u_r[..., 4] + p_r) / rho_r

        # Roe averages
        w_l = np.sqrt(rho_l)
        w_r = np.sqrt(rho_r)
        w_sum = w_l + w_r

        rho_roe = w_l * w_r
        u_roe = (w_l * u_l_vel + w_r * u_r_vel) / w_sum
        v_roe = (w_l * v_l_vel + w_r * v_r_vel) / w_sum
        w_vel_roe = (w_l * w_l_vel + w_r * w_r_vel) / w_sum
        h_roe = (w_l * h_l + w_r * h_r) / w_sum
        q2_roe = u_roe**2 + v_roe**2 + w_vel_roe**2
        c_roe2 = np.maximum((self.gamma - 1.0) * (h_roe - 0.5 * q2_roe), 1.0e-6)
        c_roe = np.sqrt(c_roe2)
        vn_roe = u_roe * nx + v_roe * ny + w_vel_roe * nz

        # Physical fluxes
        f_l = np.empty_like(u_l)
        f_l[..., 0] = rho_l * vn_l
        f_l[..., 1] = rho_l * u_l_vel * vn_l + p_l * nx
        f_l[..., 2] = rho_l * v_l_vel * vn_l + p_l * ny
        f_l[..., 3] = rho_l * w_l_vel * vn_l + p_l * nz
        f_l[..., 4] = rho_l * h_l * vn_l

        f_r = np.empty_like(u_r)
        f_r[..., 0] = rho_r * vn_r
        f_r[..., 1] = rho_r * u_r_vel * vn_r + p_r * nx
        f_r[..., 2] = rho_r * v_r_vel * vn_r + p_r * ny
        f_r[..., 3] = rho_r * w_r_vel * vn_r + p_r * nz
        f_r[..., 4] = rho_r * h_r * vn_r

        # Wave speeds
        dvn = vn_r - vn_l
        d_p = p_r - p_l
        d_rho = rho_r - rho_l

        lambda1 = np.abs(vn_roe)
        lambda4 = np.abs(vn_roe + c_roe)
        lambda5 = np.abs(vn_roe - c_roe)

        delta = 0.1 * c_roe
        lambda1 = np.where(lambda1 < delta, 0.5 * (lambda1**2 + delta**2) / delta, lambda1)
        lambda4 = np.where(lambda4 < delta, 0.5 * (lambda4**2 + delta**2) / delta, lambda4)
        lambda5 = np.where(lambda5 < delta, 0.5 * (lambda5**2 + delta**2) / delta, lambda5)

        alpha4 = (d_p + rho_roe * c_roe * dvn) / (2.0 * c_roe2)
        alpha5 = (d_p - rho_roe * c_roe * dvn) / (2.0 * c_roe2)
        alpha1 = d_rho - (alpha4 + alpha5)

        diss = np.empty_like(u_l)
        diss[..., 0] = lambda1 * alpha1 + lambda4 * alpha4 + lambda5 * alpha5
        diss[..., 1] = (
            lambda1 * alpha1 * u_roe
            + lambda4 * alpha4 * (u_roe + c_roe * nx)
            + lambda5 * alpha5 * (u_roe - c_roe * nx)
        )
        diss[..., 2] = (
            lambda1 * alpha1 * v_roe
            + lambda4 * alpha4 * (v_roe + c_roe * ny)
            + lambda5 * alpha5 * (v_roe - c_roe * ny)
        )
        diss[..., 3] = (
            lambda1 * alpha1 * w_vel_roe
            + lambda4 * alpha4 * (w_vel_roe + c_roe * nz)
            + lambda5 * alpha5 * (w_vel_roe - c_roe * nz)
        )
        diss[..., 4] = (
            lambda1 * alpha1 * (0.5 * q2_roe)
            + lambda4 * alpha4 * (h_roe + c_roe * vn_roe)
            + lambda5 * alpha5 * (h_roe - c_roe * vn_roe)
        )

        num_flux = np.asarray(0.5 * (f_l + f_r - diss) * area[..., np.newaxis], dtype=np.float64)
        return num_flux

    def evaluate_residuals(self) -> np.ndarray:
        """Compute spatial residual R(U) for 3D cells."""
        res = np.zeros_like(self.u)

        # 1. i-faces (periodic / wrap-around around airfoil in i)
        u_left_i = np.roll(self.u, 1, axis=0)
        u_right_i = self.u
        f_i = self.compute_flux(
            u_left_i,
            u_right_i,
            self.grid.i_area_x[:-1],
            self.grid.i_area_y[:-1],
            self.grid.i_area_z[:-1],
        )
        res += f_i
        res -= np.roll(f_i, -1, axis=0)

        # 2. Interior j-faces
        u_bottom_j = self.u[:, :-1, :, :]
        u_top_j = self.u[:, 1:, :, :]
        f_j = self.compute_flux(
            u_bottom_j,
            u_top_j,
            self.grid.j_area_x[:, 1:-1, :],
            self.grid.j_area_y[:, 1:-1, :],
            self.grid.j_area_z[:, 1:-1, :],
        )
        res[:, 1:, :, :] += f_j
        res[:, :-1, :, :] -= f_j

        # Wall boundary at j=0 (Solid slip wall)
        ax_w = self.grid.j_area_x[:, 0, :]
        ay_w = self.grid.j_area_y[:, 0, :]
        az_w = self.grid.j_area_z[:, 0, :]
        area_w = np.sqrt(ax_w**2 + ay_w**2 + az_w**2)
        nx_w = ax_w / np.maximum(area_w, 1.0e-12)
        ny_w = ay_w / np.maximum(area_w, 1.0e-12)
        nz_w = az_w / np.maximum(area_w, 1.0e-12)

        u_first_j = self.u[:, 0, :, :]
        rho_w, u_w, v_w, w_w, p_w, _ = self.get_primitive(u_first_j)
        vn_w = u_w * nx_w + v_w * ny_w + w_w * nz_w

        u_ghost_vel = u_w - 2.0 * vn_w * nx_w
        v_ghost_vel = v_w - 2.0 * vn_w * ny_w
        w_ghost_vel = w_w - 2.0 * vn_w * nz_w

        e_ghost = p_w / ((self.gamma - 1.0) * rho_w) + 0.5 * (
            u_ghost_vel**2 + v_ghost_vel**2 + w_ghost_vel**2
        )
        u_ghost_j = np.empty_like(u_first_j)
        u_ghost_j[..., 0] = rho_w
        u_ghost_j[..., 1] = rho_w * u_ghost_vel
        u_ghost_j[..., 2] = rho_w * v_ghost_vel
        u_ghost_j[..., 3] = rho_w * w_ghost_vel
        u_ghost_j[..., 4] = rho_w * e_ghost

        f_wall = self.compute_flux(u_ghost_j, u_first_j, ax_w, ay_w, az_w)
        res[:, 0, :, :] += f_wall

        # Farfield boundary at j=nj
        ax_far = self.grid.j_area_x[:, -1, :]
        ay_far = self.grid.j_area_y[:, -1, :]
        az_far = self.grid.j_area_z[:, -1, :]
        u_inner_j = self.u[:, -1, :, :]
        u_outer_j = np.broadcast_to(self.u_free, u_inner_j.shape)
        f_far_j = self.compute_flux(u_inner_j, u_outer_j, ax_far, ay_far, az_far)
        res[:, -1, :, :] -= f_far_j

        # 3. k-faces (Spanwise: k=0 symmetry plane, k=nk outer farfield)
        u_in_k = self.u[:, :, :-1, :]
        u_out_k = self.u[:, :, 1:, :]
        f_k = self.compute_flux(
            u_in_k,
            u_out_k,
            self.grid.k_area_x[:, :, 1:-1],
            self.grid.k_area_y[:, :, 1:-1],
            self.grid.k_area_z[:, :, 1:-1],
        )
        res[:, :, 1:, :] += f_k
        res[:, :, :-1, :] -= f_k

        # Root symmetry plane (k=0, normal points along +y, so mirror v_velocity)
        ax_sym = self.grid.k_area_x[:, :, 0]
        ay_sym = self.grid.k_area_y[:, :, 0]
        az_sym = self.grid.k_area_z[:, :, 0]

        u_root = self.u[:, :, 0, :]
        u_sym = np.array(u_root)
        u_sym[..., 2] = -u_sym[..., 2]  # reflect v_vel
        f_sym = self.compute_flux(u_sym, u_root, ax_sym, ay_sym, az_sym)
        res[:, :, 0, :] += f_sym

        # Wing tip outer farfield (k=nk)
        ax_tip = self.grid.k_area_x[:, :, -1]
        ay_tip = self.grid.k_area_y[:, :, -1]
        az_tip = self.grid.k_area_z[:, :, -1]
        u_tip = self.u[:, :, -1, :]
        u_tip_outer = np.broadcast_to(self.u_free, u_tip.shape)
        f_tip = self.compute_flux(u_tip, u_tip_outer, ax_tip, ay_tip, az_tip)
        res[:, :, -1, :] -= f_tip

        return res

    def compute_forces(self) -> Tuple[float, float, float]:
        """Compute aerodynamic coefficients (cl, cd, cm) by surface pressure integration on the wing."""
        _, _, _, _, p_wall, _ = self.get_primitive(self.u[:, 0, :, :])
        p_gauge = p_wall - self.p_inf

        # Isolate wing surface cells up to tip (k < nk_wing)
        kw = self.grid.nk_wing
        ax = self.grid.j_area_x[:, 0, :kw]
        ay = self.grid.j_area_y[:, 0, :kw]
        az = self.grid.j_area_z[:, 0, :kw]
        p_w = p_gauge[:, :kw]

        # Total force on semi-span wing (multiply by 2 for full aircraft)
        fx = 2.0 * float(np.sum(p_w * ax))
        fy = 2.0 * float(np.sum(p_w * ay))
        fz = 2.0 * float(np.sum(p_w * az))

        q_inf = 0.5 * self.rho_inf * (self.v_inf**2)
        alpha_rad = math.radians(self.config.alpha_deg)

        # Reference area S = b * c_mean = AR * (1 + taper)^2 / 4
        s_ref = self.config.aspect_ratio * ((1.0 + self.config.taper_ratio) / 2.0) ** 2
        c_ref = (1.0 + self.config.taper_ratio) / 2.0

        lift = fz * math.cos(alpha_rad) - fx * math.sin(alpha_rad)
        drag = fz * math.sin(alpha_rad) + fx * math.cos(alpha_rad)

        cl = float(lift / (q_inf * s_ref))
        cd = float(drag / (q_inf * s_ref))

        # Pitching moment about (0.25*c_root, 0, 0)
        # Surface cell centers on j=0 wall face: average of 4 corners (ni, kw)
        xc_wall = 0.25 * (
            self.grid.x[:-1, 0, :kw]
            + self.grid.x[1:, 0, :kw]
            + self.grid.x[1:, 0, 1 : kw + 1]
            + self.grid.x[:-1, 0, 1 : kw + 1]
        )
        zc_wall = 0.25 * (
            self.grid.z[:-1, 0, :kw]
            + self.grid.z[1:, 0, :kw]
            + self.grid.z[1:, 0, 1 : kw + 1]
            + self.grid.z[:-1, 0, 1 : kw + 1]
        )
        arm_x = xc_wall - 0.25
        arm_z = zc_wall

        dfx = 2.0 * p_w * ax
        dfz = 2.0 * p_w * az
        my = float(np.sum(arm_z * dfx - arm_x * dfz))
        cm = float(my / (q_inf * s_ref * c_ref))

        return cl, cd, cm

    def compute_stable_dt(self) -> float:
        """Compute stable time-step based on 3D minimum cell width."""
        _, _, _, _, _, c_field = self.get_primitive(self.u)
        vel_mag = np.sqrt(
            (self.u[..., 1] / self.u[..., 0]) ** 2
            + (self.u[..., 2] / self.u[..., 0]) ** 2
            + (self.u[..., 3] / self.u[..., 0]) ** 2
        )
        char_speed = vel_mag + c_field
        # Cell width estimate from volume^(1/3)
        h_cell = self.grid.vol ** (1.0 / 3.0)
        dt_local = self.config.cfl * h_cell / np.maximum(char_speed, 1.0e-6)
        return float(np.min(dt_local))

    def step_rk2(self, dt: float) -> float:
        """Advance solution one step using 2-stage Runge-Kutta scheme."""
        vol_5 = self.grid.vol[..., np.newaxis]

        res1 = self.evaluate_residuals()
        u1 = self.u - (dt / vol_5) * res1

        res2 = self.evaluate_residuals()
        self.u = 0.5 * (self.u + u1 - (dt / vol_5) * res2)

        l2_res = float(np.sqrt(np.mean((res1[..., 0] / self.grid.vol) ** 2)))
        return l2_res

    def run(self) -> CFDResult3D:
        """Execute 3D simulation until convergence or max_steps."""
        dt = self.compute_stable_dt()
        res_norm = 1.0
        converged = False
        step = 0

        for step in range(1, self.config.max_steps + 1):
            res_norm = self.step_rk2(dt)
            if res_norm < self.config.convergence_tol:
                converged = True
                break

        cl, cd, cm = self.compute_forces()
        total_mass_res = float(np.sum(self.evaluate_residuals()[..., 0]))

        return CFDResult3D(
            converged=converged,
            iterations=step,
            residual_l2=res_norm,
            cl=cl,
            cd=cd,
            cm=cm,
            mass_conservation_residual=abs(total_mass_res),
            grid_cells=int(self.ni * self.nj * self.nk),
            metadata={"dt": dt, "aspect_ratio": self.config.aspect_ratio},
        )
