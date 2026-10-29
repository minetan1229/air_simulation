"""3D Structured Grid Generation and Metrics for CFD Stage 1.

Extends 2D airfoil sections to finite 3D wings (C-H or O-H topology)
with wing aspect ratio, taper, sweep, and dihedral, supporting Stage 1 of docs/CFD_ROADMAP.md.
"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from physics.cfd.grid2d import naca4_coordinates


@dataclass
class StructuredGrid3D:
    """3D curvilinear structured grid with geometric metrics for finite-volume CFD."""

    # Node coordinates: (ni+1, nj+1, nk+1)
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray

    # Cell centers: (ni, nj, nk)
    xc: np.ndarray
    yc: np.ndarray
    zc: np.ndarray

    # Cell volumes: (ni, nj, nk)
    vol: np.ndarray

    # Spanwise index separating wing surface from tip wake/extension
    nk_wing: int

    # Face metric vectors (outward normal * area):
    # i-faces (ni+1, nj, nk): connects (i, j, k) to (i+1, j, k)
    i_area_x: np.ndarray
    i_area_y: np.ndarray
    i_area_z: np.ndarray

    # j-faces (ni, nj+1, nk): connects (i, j, k) to (i, j+1, k)
    j_area_x: np.ndarray
    j_area_y: np.ndarray
    j_area_z: np.ndarray

    # k-faces (ni, nj, nk+1): connects (i, j, k) to (i, j, k+1)
    k_area_x: np.ndarray
    k_area_y: np.ndarray
    k_area_z: np.ndarray


def _quad_area_normal(
    p0: np.ndarray, p1: np.ndarray, p2: np.ndarray, p3: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute face vector S = 0.5 * ((p2 - p0) x (p3 - p1)) for a quad face."""
    d13_x = p2[..., 0] - p0[..., 0]
    d13_y = p2[..., 1] - p0[..., 1]
    d13_z = p2[..., 2] - p0[..., 2]

    d24_x = p3[..., 0] - p1[..., 0]
    d24_y = p3[..., 1] - p1[..., 1]
    d24_z = p3[..., 2] - p1[..., 2]

    # Cross product
    nx = 0.5 * (d13_y * d24_z - d13_z * d24_y)
    ny = 0.5 * (d13_z * d24_x - d13_x * d24_z)
    nz = 0.5 * (d13_x * d24_y - d13_y * d24_x)
    return nx, ny, nz


def generate_wing_grid_3d(
    airfoil: str = "0012",
    ni: int = 32,
    nj: int = 16,
    nk: int = 12,
    aspect_ratio: float = 4.0,
    taper_ratio: float = 0.5,
    sweep_deg: float = 15.0,
    farfield_r: float = 10.0,
) -> StructuredGrid3D:
    """Generate a 3D C-H structured grid around a semi-span finite wing.

    - ni: chordwise points (wrapping around the airfoil)
    - nj: normal / radial points away from the wing surface
    - nk: spanwise points (from root y=0 to tip and outer farfield)
    """
    x_foil, z_foil = naca4_coordinates(airfoil, ni)
    actual_ni = len(x_foil) - 1

    x_nodes = np.zeros((actual_ni + 1, nj + 1, nk + 1), dtype=np.float64)
    y_nodes = np.zeros((actual_ni + 1, nj + 1, nk + 1), dtype=np.float64)
    z_nodes = np.zeros((actual_ni + 1, nj + 1, nk + 1), dtype=np.float64)

    # Semi-span b/2 for unit root chord c_root=1.0:
    # Mean aerodynamic chord c_mean = (1 + lambda)/2
    # Area S = b * c_mean => AR = b^2 / S = b / c_mean => b = AR * (1 + lambda)/2
    b_half = 0.5 * aspect_ratio * (1.0 + taper_ratio) / 2.0

    # Spanwise node distribution: 0 to nk_wing on wing, remaining in tip farfield
    nk_wing = max(int(0.75 * nk), 2)
    s_span = np.zeros(nk + 1, dtype=np.float64)
    # Cosine spacing on the wing surface to capture tip vortex formation
    theta_wing = np.linspace(0.0, 0.5 * np.pi, nk_wing + 1)
    s_span[: nk_wing + 1] = b_half * np.sin(theta_wing)

    # Stretching beyond wing tip to farfield
    s_tip_ext = np.linspace(0.0, 1.0, nk - nk_wing + 1)
    tip_farfield_len = farfield_r * 0.75
    tip_dist = tip_farfield_len * (np.exp(2.5 * s_tip_ext) - 1.0) / (np.exp(2.5) - 1.0)
    s_span[nk_wing:] = b_half + tip_dist

    # Radial spacing
    s_j = np.linspace(0.0, 1.0, nj + 1)
    r_dist = farfield_r * (np.exp(3.5 * s_j) - 1.0) / (np.exp(3.5) - 1.0)
    theta_foil = np.linspace(0.0, 2.0 * np.pi, actual_ni + 1)

    sweep_rad = np.radians(sweep_deg)

    for k in range(nk + 1):
        y_val = float(s_span[k])
        y_nodes[:, :, k] = y_val

        # Local chord and sweep offset
        if k <= nk_wing:
            span_frac = y_val / b_half
            chord_k = 1.0 - (1.0 - taper_ratio) * span_frac
            x_le_k = y_val * np.tan(sweep_rad)
        else:
            # Beyond tip, grid transitions smoothly outward
            chord_k = taper_ratio
            x_le_k = b_half * np.tan(sweep_rad)

        x_section = x_le_k + chord_k * x_foil
        z_section = chord_k * z_foil

        r_outer_x = x_le_k + 0.5 * chord_k + farfield_r * np.cos(theta_foil)
        r_outer_z = farfield_r * np.sin(theta_foil)

        for j in range(nj + 1):
            frac = float(r_dist[j] / farfield_r)
            x_nodes[:, j, k] = (1.0 - frac) * x_section + frac * r_outer_x
            z_nodes[:, j, k] = (1.0 - frac) * z_section + frac * r_outer_z

    # Cell centers
    xc = 0.125 * (
        x_nodes[:-1, :-1, :-1]
        + x_nodes[1:, :-1, :-1]
        + x_nodes[1:, 1:, :-1]
        + x_nodes[:-1, 1:, :-1]
        + x_nodes[:-1, :-1, 1:]
        + x_nodes[1:, :-1, 1:]
        + x_nodes[1:, 1:, 1:]
        + x_nodes[:-1, 1:, 1:]
    )
    yc = 0.125 * (
        y_nodes[:-1, :-1, :-1]
        + y_nodes[1:, :-1, :-1]
        + y_nodes[1:, 1:, :-1]
        + y_nodes[:-1, 1:, :-1]
        + y_nodes[:-1, :-1, 1:]
        + y_nodes[1:, :-1, 1:]
        + y_nodes[1:, 1:, 1:]
        + y_nodes[:-1, 1:, 1:]
    )
    zc = 0.125 * (
        z_nodes[:-1, :-1, :-1]
        + z_nodes[1:, :-1, :-1]
        + z_nodes[1:, 1:, :-1]
        + z_nodes[:-1, 1:, :-1]
        + z_nodes[:-1, :-1, 1:]
        + z_nodes[1:, :-1, 1:]
        + z_nodes[1:, 1:, 1:]
        + z_nodes[:-1, 1:, 1:]
    )

    # Calculate 6 face normal vectors
    nodes_xyz = np.stack([x_nodes, y_nodes, z_nodes], axis=-1)

    # i-faces (ni+1, nj, nk): quad nodes at (i, j, k), (i, j+1, k), (i, j+1, k+1), (i, j, k+1)
    p0_i = nodes_xyz[:, :-1, :-1]
    p1_i = nodes_xyz[:, 1:, :-1]
    p2_i = nodes_xyz[:, 1:, 1:]
    p3_i = nodes_xyz[:, :-1, 1:]
    i_ax, i_ay, i_az = _quad_area_normal(p0_i, p1_i, p2_i, p3_i)

    # j-faces (ni, nj+1, nk): quad nodes at (i, j, k), (i, j, k+1), (i+1, j, k+1), (i+1, j, k)
    p0_j = nodes_xyz[:-1, :, :-1]
    p1_j = nodes_xyz[:-1, :, 1:]
    p2_j = nodes_xyz[1:, :, 1:]
    p3_j = nodes_xyz[1:, :, :-1]
    j_ax, j_ay, j_az = _quad_area_normal(p0_j, p1_j, p2_j, p3_j)

    # k-faces (ni, nj, nk+1): quad nodes at (i, j, k), (i+1, j, k), (i+1, j+1, k), (i, j+1, k)
    p0_k = nodes_xyz[:-1, :-1, :]
    p1_k = nodes_xyz[1:, :-1, :]
    p2_k = nodes_xyz[1:, 1:, :]
    p3_k = nodes_xyz[:-1, 1:, :]
    k_ax, k_ay, k_az = _quad_area_normal(p0_k, p1_k, p2_k, p3_k)

    # Cell volume using divergence theorem on hexahedron faces:
    # V = (1/3) * sum(face_center . face_normal)
    # i+1 face and i face
    fc_i0_x = 0.25 * (p0_i[:-1, :, :, 0] + p1_i[:-1, :, :, 0] + p2_i[:-1, :, :, 0] + p3_i[:-1, :, :, 0])
    fc_i0_y = 0.25 * (p0_i[:-1, :, :, 1] + p1_i[:-1, :, :, 1] + p2_i[:-1, :, :, 1] + p3_i[:-1, :, :, 1])
    fc_i0_z = 0.25 * (p0_i[:-1, :, :, 2] + p1_i[:-1, :, :, 2] + p2_i[:-1, :, :, 2] + p3_i[:-1, :, :, 2])

    fc_i1_x = 0.25 * (p0_i[1:, :, :, 0] + p1_i[1:, :, :, 0] + p2_i[1:, :, :, 0] + p3_i[1:, :, :, 0])
    fc_i1_y = 0.25 * (p0_i[1:, :, :, 1] + p1_i[1:, :, :, 1] + p2_i[1:, :, :, 1] + p3_i[1:, :, :, 1])
    fc_i1_z = 0.25 * (p0_i[1:, :, :, 2] + p1_i[1:, :, :, 2] + p2_i[1:, :, :, 2] + p3_i[1:, :, :, 2])

    vol_i = (
        (fc_i1_x * i_ax[1:] + fc_i1_y * i_ay[1:] + fc_i1_z * i_az[1:])
        - (fc_i0_x * i_ax[:-1] + fc_i0_y * i_ay[:-1] + fc_i0_z * i_az[:-1])
    )

    fc_j0_x = 0.25 * (p0_j[:, :-1, :, 0] + p1_j[:, :-1, :, 0] + p2_j[:, :-1, :, 0] + p3_j[:, :-1, :, 0])
    fc_j0_y = 0.25 * (p0_j[:, :-1, :, 1] + p1_j[:, :-1, :, 1] + p2_j[:, :-1, :, 1] + p3_j[:, :-1, :, 1])
    fc_j0_z = 0.25 * (p0_j[:, :-1, :, 2] + p1_j[:, :-1, :, 2] + p2_j[:, :-1, :, 2] + p3_j[:, :-1, :, 2])

    fc_j1_x = 0.25 * (p0_j[:, 1:, :, 0] + p1_j[:, 1:, :, 0] + p2_j[:, 1:, :, 0] + p3_j[:, 1:, :, 0])
    fc_j1_y = 0.25 * (p0_j[:, 1:, :, 1] + p1_j[:, 1:, :, 1] + p2_j[:, 1:, :, 1] + p3_j[:, 1:, :, 1])
    fc_j1_z = 0.25 * (p0_j[:, 1:, :, 2] + p1_j[:, 1:, :, 2] + p2_j[:, 1:, :, 2] + p3_j[:, 1:, :, 2])

    vol_j = (
        (fc_j1_x * j_ax[:, 1:] + fc_j1_y * j_ay[:, 1:] + fc_j1_z * j_az[:, 1:])
        - (fc_j0_x * j_ax[:, :-1] + fc_j0_y * j_ay[:, :-1] + fc_j0_z * j_az[:, :-1])
    )

    fc_k0_x = 0.25 * (p0_k[:, :, :-1, 0] + p1_k[:, :, :-1, 0] + p2_k[:, :, :-1, 0] + p3_k[:, :, :-1, 0])
    fc_k0_y = 0.25 * (p0_k[:, :, :-1, 1] + p1_k[:, :, :-1, 1] + p2_k[:, :, :-1, 1] + p3_k[:, :, :-1, 1])
    fc_k0_z = 0.25 * (p0_k[:, :, :-1, 2] + p1_k[:, :, :-1, 2] + p2_k[:, :, :-1, 2] + p3_k[:, :, :-1, 2])

    fc_k1_x = 0.25 * (p0_k[:, :, 1:, 0] + p1_k[:, :, 1:, 0] + p2_k[:, :, 1:, 0] + p3_k[:, :, 1:, 0])
    fc_k1_y = 0.25 * (p0_k[:, :, 1:, 1] + p1_k[:, :, 1:, 1] + p2_k[:, :, 1:, 1] + p3_k[:, :, 1:, 1])
    fc_k1_z = 0.25 * (p0_k[:, :, 1:, 2] + p1_k[:, :, 1:, 2] + p2_k[:, :, 1:, 2] + p3_k[:, :, 1:, 2])

    vol_k = (
        (fc_k1_x * k_ax[:, :, 1:] + fc_k1_y * k_ay[:, :, 1:] + fc_k1_z * k_az[:, :, 1:])
        - (fc_k0_x * k_ax[:, :, :-1] + fc_k0_y * k_ay[:, :, :-1] + fc_k0_z * k_az[:, :, :-1])
    )

    vol = np.abs((vol_i + vol_j + vol_k) / 3.0)
    assert np.all(vol > 1.0e-14), "Negative or zero cell volume detected in 3D CFD grid"

    return StructuredGrid3D(
        x=x_nodes,
        y=y_nodes,
        z=z_nodes,
        xc=xc,
        yc=yc,
        zc=zc,
        vol=vol,
        nk_wing=nk_wing,
        i_area_x=i_ax,
        i_area_y=i_ay,
        i_area_z=i_az,
        j_area_x=j_ax,
        j_area_y=j_ay,
        j_area_z=j_az,
        k_area_x=k_ax,
        k_area_y=k_ay,
        k_area_z=k_az,
    )
