"""2D Compressible Finite-Volume Solver for Stage 0 validation."""

from __future__ import annotations

import math
from typing import Tuple

import numpy as np

from physics.cfd import CFDConfig2D, CFDResult2D
from physics.cfd.grid2d import StructuredGrid2D


class Solver2D:
    """Cell-centered finite-volume solver for 2D compressible Euler / baseline flow."""

    def __init__(self, config: CFDConfig2D, grid: StructuredGrid2D) -> None:
        self.config = config
        self.grid = grid
        self.gamma = config.gamma

        self.ni, self.nj = grid.vol.shape

        # Conserved variables U: [rho, rho*u, rho*v, rho*E]
        self.u = np.zeros((self.ni, self.nj, 4), dtype=np.float64)

        # Free-stream non-dimensional reference values
        self.rho_inf = 1.0
        self.p_inf = 1.0 / self.gamma
        self.c_inf = 1.0
        self.v_inf = config.mach * self.c_inf
        alpha_rad = math.radians(config.alpha_deg)
        self.u_inf = self.v_inf * math.cos(alpha_rad)
        self.v_flow_inf = self.v_inf * math.sin(alpha_rad)
        e_inf = self.p_inf / ((self.gamma - 1.0) * self.rho_inf) + 0.5 * (
            self.u_inf**2 + self.v_flow_inf**2
        )
        self.E_inf = e_inf

        self.u_free = np.array(
            [
                self.rho_inf,
                self.rho_inf * self.u_inf,
                self.rho_inf * self.v_flow_inf,
                self.rho_inf * self.E_inf,
            ],
            dtype=np.float64,
        )

        # Initialize flow field to free-stream
        self.u[:, :] = self.u_free

    def get_primitive(self, u_state: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Convert conserved variables U to primitive variables: (rho, u, v, p, c)."""
        rho = np.maximum(u_state[..., 0], 1.0e-6)
        u_vel = u_state[..., 1] / rho
        v_vel = u_state[..., 2] / rho
        e_tot = u_state[..., 3] / rho
        kinetic = 0.5 * (u_vel**2 + v_vel**2)
        p = np.maximum((self.gamma - 1.0) * rho * (e_tot - kinetic), 1.0e-6)
        c = np.sqrt(self.gamma * p / rho)
        return rho, u_vel, v_vel, p, c

    def compute_flux(self, u_l: np.ndarray, u_r: np.ndarray, nx: np.ndarray, ny: np.ndarray) -> np.ndarray:
        """Compute numerical flux across face using Roe flux difference splitting."""
        rho_l, u_l_vel, v_l_vel, p_l, c_l = self.get_primitive(u_l)
        rho_r, u_r_vel, v_r_vel, p_r, c_r = self.get_primitive(u_r)

        # Normal velocities
        vn_l = u_l_vel * nx + v_l_vel * ny
        vn_r = u_r_vel * nx + v_r_vel * ny

        # Enthalpy
        h_l = (u_l[..., 3] + p_l) / rho_l
        h_r = (u_r[..., 3] + p_r) / rho_r

        # Roe averaging
        w_l = np.sqrt(rho_l)
        w_r = np.sqrt(rho_r)
        w_sum = w_l + w_r

        rho_roe = w_l * w_r
        u_roe = (w_l * u_l_vel + w_r * u_r_vel) / w_sum
        v_roe = (w_l * v_l_vel + w_r * v_r_vel) / w_sum
        h_roe = (w_l * h_l + w_r * h_r) / w_sum
        q2_roe = u_roe**2 + v_roe**2
        c_roe2 = np.maximum((self.gamma - 1.0) * (h_roe - 0.5 * q2_roe), 1.0e-6)
        c_roe = np.sqrt(c_roe2)
        vn_roe = u_roe * nx + v_roe * ny

        # Physical fluxes: F*nx + G*ny
        f_l = np.empty_like(u_l)
        f_l[..., 0] = rho_l * vn_l
        f_l[..., 1] = rho_l * u_l_vel * vn_l + p_l * nx
        f_l[..., 2] = rho_l * v_l_vel * vn_l + p_l * ny
        f_l[..., 3] = rho_l * h_l * vn_l

        f_r = np.empty_like(u_r)
        f_r[..., 0] = rho_r * vn_r
        f_r[..., 1] = rho_r * u_r_vel * vn_r + p_r * nx
        f_r[..., 2] = rho_r * v_r_vel * vn_r + p_r * ny
        f_r[..., 3] = rho_r * h_r * vn_r

        # Wave speeds with Harten entropy fix
        dvn = vn_r - vn_l
        d_p = p_r - p_l
        d_rho = rho_r - rho_l

        lambda1 = np.abs(vn_roe)
        lambda2 = np.abs(vn_roe + c_roe)
        lambda3 = np.abs(vn_roe - c_roe)

        delta = 0.1 * c_roe
        lambda1 = np.where(lambda1 < delta, 0.5 * (lambda1**2 + delta**2) / delta, lambda1)
        lambda2 = np.where(lambda2 < delta, 0.5 * (lambda2**2 + delta**2) / delta, lambda2)
        lambda3 = np.where(lambda3 < delta, 0.5 * (lambda3**2 + delta**2) / delta, lambda3)

        # Wave amplitudes
        alpha2 = (d_p + rho_roe * c_roe * dvn) / (2.0 * c_roe2)
        alpha3 = (d_p - rho_roe * c_roe * dvn) / (2.0 * c_roe2)
        alpha1 = d_rho - (alpha2 + alpha3)

        # Dissipation flux
        diss = np.empty_like(u_l)
        diss[..., 0] = lambda1 * alpha1 + lambda2 * alpha2 + lambda3 * alpha3
        diss[..., 1] = (
            lambda1 * alpha1 * u_roe
            + lambda2 * alpha2 * (u_roe + c_roe * nx)
            + lambda3 * alpha3 * (u_roe - c_roe * nx)
        )
        diss[..., 2] = (
            lambda1 * alpha1 * v_roe
            + lambda2 * alpha2 * (v_roe + c_roe * ny)
            + lambda3 * alpha3 * (v_roe - c_roe * ny)
        )
        diss[..., 3] = (
            lambda1 * alpha1 * (0.5 * q2_roe)
            + lambda2 * alpha2 * (h_roe + c_roe * vn_roe)
            + lambda3 * alpha3 * (h_roe - c_roe * vn_roe)
        )

        num_flux = np.asarray(0.5 * (f_l + f_r - diss), dtype=np.float64)
        return num_flux

    def evaluate_residuals(self) -> np.ndarray:
        """Compute spatial residual R(U) = sum(Flux * face_len)."""
        res = np.zeros_like(self.u)

        # 1. i-faces (periodic / wrap-around around airfoil in i)
        u_left = np.roll(self.u, 1, axis=0)  # i-1 cell
        u_right = self.u                     # i cell
        nx_i = self.grid.i_nx[:-1, :]
        ny_i = self.grid.i_ny[:-1, :]
        len_i = self.grid.i_len[:-1, :, np.newaxis]

        f_i = self.compute_flux(u_left, u_right, nx_i, ny_i) * len_i

        # Flux enters cell i and leaves cell i-1
        res += f_i
        res -= np.roll(f_i, -1, axis=0)

        # 2. Interior j-faces
        u_bottom = self.u[:, :-1, :]
        u_top = self.u[:, 1:, :]
        nx_j = self.grid.j_nx[:, 1:-1]
        ny_j = self.grid.j_ny[:, 1:-1]
        len_j = self.grid.j_len[:, 1:-1, np.newaxis]

        f_j = self.compute_flux(u_bottom, u_top, nx_j, ny_j) * len_j

        res[:, 1:, :] += f_j
        res[:, :-1, :] -= f_j

        # 3. Wall boundary (j=0) - Solid slip wall boundary condition
        # Wall face normal j_nx[:, 0], j_ny[:, 0] points into domain (+j direction)
        nx_w = self.grid.j_nx[:, 0]
        ny_w = self.grid.j_ny[:, 0]
        len_wall = self.grid.j_len[:, 0, np.newaxis]

        u_first = self.u[:, 0, :]
        rho_w, u_w_vel, v_w_vel, p_w, _ = self.get_primitive(u_first)
        vn_w = u_w_vel * nx_w + v_w_vel * ny_w
        # Reflect normal velocity: v_ghost = v_inner - 2 * vn * n
        u_ghost_vel = u_w_vel - 2.0 * vn_w * nx_w
        v_ghost_vel = v_w_vel - 2.0 * vn_w * ny_w

        e_ghost = p_w / ((self.gamma - 1.0) * rho_w) + 0.5 * (u_ghost_vel**2 + v_ghost_vel**2)
        u_ghost = np.empty_like(u_first)
        u_ghost[:, 0] = rho_w
        u_ghost[:, 1] = rho_w * u_ghost_vel
        u_ghost[:, 2] = rho_w * v_ghost_vel
        u_ghost[:, 3] = rho_w * e_ghost

        # Flux across j=0 face from ghost into first cell (normal points into domain)
        f_wall = self.compute_flux(u_ghost, u_first, nx_w, ny_w) * len_wall
        res[:, 0, :] += f_wall

        # 4. Farfield boundary (j=nj) - Characteristic Farfield
        nx_far = self.grid.j_nx[:, -1]
        ny_far = self.grid.j_ny[:, -1]
        len_far = self.grid.j_len[:, -1, np.newaxis]

        u_inner = self.u[:, -1, :]
        u_outer = np.broadcast_to(self.u_free, u_inner.shape)
        # Flux leaves domain at outer boundary
        f_far = self.compute_flux(u_inner, u_outer, nx_far, ny_far) * len_far
        res[:, -1, :] -= f_far

        return res

    def compute_forces(self) -> Tuple[float, float, float]:
        """Compute aerodynamic coefficients (cl, cd, cm) by integrating surface pressure."""
        _, _, _, p_wall, _ = self.get_primitive(self.u[:, 0, :])
        p_gauge = p_wall - self.p_inf

        nx = self.grid.j_nx[:, 0]
        ny = self.grid.j_ny[:, 0]
        ds = self.grid.j_len[:, 0]

        fx = float(np.sum(p_gauge * nx * ds))
        fy = float(np.sum(p_gauge * ny * ds))

        q_inf = 0.5 * self.rho_inf * (self.v_inf**2)
        alpha_rad = math.radians(self.config.alpha_deg)

        lift = fy * math.cos(alpha_rad) - fx * math.sin(alpha_rad)
        drag = fy * math.sin(alpha_rad) + fx * math.cos(alpha_rad)

        cl = float(lift / q_inf)
        cd = float(drag / q_inf)

        xc_wall = 0.5 * (self.grid.x[:-1, 0] + self.grid.x[1:, 0])
        yc_wall = 0.5 * (self.grid.y[:-1, 0] + self.grid.y[1:, 0])
        arm_x = xc_wall - 0.25
        arm_y = yc_wall

        dfx = p_gauge * nx * ds
        dfy = p_gauge * ny * ds
        cm = float(np.sum(arm_x * dfy - arm_y * dfx) / (q_inf * 1.0))

        return cl, cd, cm

    def compute_stable_dt(self) -> float:
        """Compute stable time-step based on minimum cell width and maximum wave speed."""
        _, _, _, _, c_field = self.get_primitive(self.u)
        vel_mag = np.hypot(self.u[..., 1], self.u[..., 2]) / self.u[..., 0]
        char_speed = vel_mag + c_field

        # For 2D curvilinear cell, effective normal width is volume / sum(half face lengths)
        dx_eff_i = self.grid.vol / np.maximum(self.grid.i_len[:-1, :], 1.0e-12)
        dx_eff_j = self.grid.vol / np.maximum(self.grid.j_len[:, :-1], 1.0e-12)
        min_dx = np.minimum(dx_eff_i, dx_eff_j)
        dt_local = self.config.cfl * min_dx / np.maximum(char_speed, 1.0e-6)
        return float(np.min(dt_local))

    def step_rk2(self, dt: float) -> float:
        """Advance solution one step using 2-stage Runge-Kutta scheme."""
        vol_4 = self.grid.vol[..., np.newaxis]

        # Stage 1
        res1 = self.evaluate_residuals()
        u1 = self.u - (dt / vol_4) * res1

        # Stage 2
        res2 = self.evaluate_residuals()
        self.u = 0.5 * (self.u + u1 - (dt / vol_4) * res2)

        # Return L2 norm of density residual
        l2_res = float(np.sqrt(np.mean((res1[..., 0] / self.grid.vol) ** 2)))
        return l2_res

    def run(self) -> CFDResult2D:
        """Execute simulation until convergence or max_steps."""
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

        return CFDResult2D(
            converged=converged,
            iterations=step,
            residual_l2=res_norm,
            cl=cl,
            cd=cd,
            cm=cm,
            mass_conservation_residual=abs(total_mass_res),
            grid_cells=int(self.ni * self.nj),
            metadata={"dt": dt, "airfoil": self.config.airfoil, "mach": self.config.mach},
        )
