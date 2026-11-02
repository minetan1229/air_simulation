"""2D Grid generation and geometry metrics for CFD Stage 0."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple

import numpy as np


@dataclass
class StructuredGrid2D:
    """2D curvilinear structured grid with geometric metrics."""

    # Node coordinates (ni+1, nj+1)
    x: np.ndarray
    y: np.ndarray

    # Cell centers (ni, nj)
    xc: np.ndarray
    yc: np.ndarray

    # Cell areas (ni, nj)
    vol: np.ndarray

    # Face metrics:
    # i-faces (ni+1, nj): normals (nx, ny) pointing in +i direction, and face lengths
    # Face connects node (i, j) to node (i, j+1).
    i_nx: np.ndarray
    i_ny: np.ndarray
    i_len: np.ndarray

    # j-faces (ni, nj+1): normals (nx, ny) pointing in +j direction, and face lengths
    # Face connects node (i, j) to node (i+1, j).
    j_nx: np.ndarray
    j_ny: np.ndarray
    j_len: np.ndarray


def naca4_coordinates(code: str, n_points: int) -> Tuple[np.ndarray, np.ndarray]:
    """Generate coordinates for NACA 4-digit airfoil from x=1 (TE) to x=0 (LE) back to x=1."""
    if len(code) != 4 or not code.isdigit():
        raise ValueError(f"Invalid NACA 4-digit code: {code}")

    m = int(code[0]) / 100.0
    p = int(code[1]) / 10.0
    t = int(code[2:]) / 100.0

    n_half = n_points // 2
    beta = np.linspace(0, np.pi, n_half + 1)
    x_dist = 0.5 * (1.0 - np.cos(beta))

    yt = 5.0 * t * (
        0.2969 * np.sqrt(x_dist)
        - 0.1260 * x_dist
        - 0.3516 * (x_dist**2)
        + 0.2843 * (x_dist**3)
        - 0.1015 * (x_dist**4)
    )

    yc = np.zeros_like(x_dist)
    dyc_dx = np.zeros_like(x_dist)
    if p > 0.0 and m > 0.0:
        for idx, xv in enumerate(x_dist):
            if xv < p:
                yc[idx] = (m / (p**2)) * (2.0 * p * xv - xv**2)
                dyc_dx[idx] = (2.0 * m / (p**2)) * (p - xv)
            else:
                yc[idx] = (m / ((1.0 - p) ** 2)) * ((1.0 - 2.0 * p) + 2.0 * p * xv - xv**2)
                dyc_dx[idx] = (2.0 * m / ((1.0 - p) ** 2)) * (p - xv)

    theta = np.arctan(dyc_dx)

    xu = x_dist - yt * np.sin(theta)
    yu = yc + yt * np.cos(theta)

    xl = x_dist + yt * np.sin(theta)
    yl = yc - yt * np.cos(theta)

    x_full = np.concatenate([xu[::-1], xl[1:]])
    y_full = np.concatenate([yu[::-1], yl[1:]])

    return x_full, y_full


def generate_c_grid(
    airfoil: str = "0012",
    ni: int = 64,
    nj: int = 32,
    farfield_r: float = 12.0,
) -> StructuredGrid2D:
    """Generate a standard O/C-type structured grid around a 2D airfoil."""
    x_airfoil, y_airfoil = naca4_coordinates(airfoil, ni)
    actual_ni = len(x_airfoil) - 1

    x_nodes = np.zeros((actual_ni + 1, nj + 1), dtype=np.float64)
    y_nodes = np.zeros((actual_ni + 1, nj + 1), dtype=np.float64)

    s_j = np.linspace(0.0, 1.0, nj + 1)
    r_dist = farfield_r * (np.exp(3.5 * s_j) - 1.0) / (np.exp(3.5) - 1.0)

    theta = np.linspace(0.0, 2.0 * np.pi, actual_ni + 1)
    r_outer_x = 0.5 + farfield_r * np.cos(theta)
    r_outer_y = farfield_r * np.sin(theta)

    for j in range(nj + 1):
        frac = r_dist[j] / farfield_r
        x_nodes[:, j] = (1.0 - frac) * x_airfoil + frac * r_outer_x
        y_nodes[:, j] = (1.0 - frac) * y_airfoil + frac * r_outer_y

    xc = 0.25 * (
        x_nodes[:-1, :-1] + x_nodes[1:, :-1] + x_nodes[1:, 1:] + x_nodes[:-1, 1:]
    )
    yc = 0.25 * (
        y_nodes[:-1, :-1] + y_nodes[1:, :-1] + y_nodes[1:, 1:] + y_nodes[:-1, 1:]
    )

    x0 = x_nodes[:-1, :-1]
    y0 = y_nodes[:-1, :-1]
    x1 = x_nodes[1:, :-1]
    y1 = y_nodes[1:, :-1]
    x2 = x_nodes[1:, 1:]
    y2 = y_nodes[1:, 1:]
    x3 = x_nodes[:-1, 1:]
    y3 = y_nodes[:-1, 1:]

    vol = 0.5 * np.abs(
        (x0 * y1 - x1 * y0)
        + (x1 * y2 - x2 * y1)
        + (x2 * y3 - x3 * y2)
        + (x3 * y0 - x0 * y3)
    )

    assert np.all(vol > 1.0e-12), "Negative or zero cell volume detected in CFD grid"

    # i-faces (connecting (i, j) to (i, j+1))
    # Face tangent goes from (i, j) to (i, j+1) which is approximately in +j direction.
    # A vector pointing in +i direction (outward/forward) has normal (dy, -dx) or (-dy, dx).
    # Since j is radially outward, dx_i is positive. To point in +i (along airfoil circumference),
    # normal is (-dy, dx) or (dy, -dx).
    dx_i = x_nodes[:, 1:] - x_nodes[:, :-1]
    dy_i = y_nodes[:, 1:] - y_nodes[:, :-1]
    i_len = np.hypot(dx_i, dy_i)
    # Tangent is along +j (radial). Normal pointing in +i (counter-clockwise) is (-dy, dx):
    i_nx = -dy_i / np.maximum(i_len, 1.0e-12)
    i_ny = dx_i / np.maximum(i_len, 1.0e-12)

    # j-faces (connecting (i, j) to (i+1, j))
    # Face tangent goes from (i, j) to (i+1, j) along the airfoil surface.
    # Normal pointing outward (+j, into fluid) is (dy, -dx) relative to tangent vector (dx, dy).
    dx_j = x_nodes[1:, :] - x_nodes[:-1, :]
    dy_j = y_nodes[1:, :] - y_nodes[:-1, :]
    j_len = np.hypot(dx_j, dy_j)
    j_nx = dy_j / np.maximum(j_len, 1.0e-12)
    j_ny = -dx_j / np.maximum(j_len, 1.0e-12)

    return StructuredGrid2D(
        x=x_nodes,
        y=y_nodes,
        xc=xc,
        yc=yc,
        vol=vol,
        i_nx=i_nx,
        i_ny=i_ny,
        i_len=i_len,
        j_nx=j_nx,
        j_ny=j_ny,
        j_len=j_len,
    )
