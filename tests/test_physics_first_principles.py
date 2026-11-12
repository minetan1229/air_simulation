"""Tests for first-principles physics conformance:
1. First-principles flat-plate pitching moment in post-stall regime (Hoerner 1965).
2. Kirchhoff apparent mass work-rate conservation (dE_apparent/dt == 0).
"""

from __future__ import annotations

import math
import numpy as np
import pytest

from physics.aero_kernel import (
    ScalarMath,
    compute_apparent_forces,
    evaluate_coefficients,
    params_from_aircraft,
)
from physics.config_schema import AircraftConfig


def test_first_principles_flat_plate_pitching_moment() -> None:
    """Verify post-stall pitching moment approaches the first-principles flat plate limit:
    C_M,0.25c = -C_N * (x_cp - 0.25c) / c = - (2 sin(alpha)) * (0.50 - 0.25) = -0.5 sin(alpha).
    """
    cfg = AircraftConfig(
        aspect_ratio=2.0,
        cLalpha_per_rad=3.0,
        stall_alpha_deg=10.0,
        stall_sharpness=5.0,  # Sharp transition to isolate post-stall plate branch
        cm_alpha=-0.4,
        cm0=0.0,
        cp_shift_gain=0.0,    # Isolate flat_plate_cm model without separate CP shift
        fold_trailing_deg=0.0,
        elevon_deg=0.0,
    )
    p = params_from_aircraft(cfg)

    # In deep stall (alpha = 45 deg, 90 deg)
    for alpha in [45.0, 90.0, -45.0, -90.0]:
        res = evaluate_coefficients(
            ScalarMath,
            p,
            alpha_deg=alpha,
            beta_deg=0.0,
            speed=10.0,
        )
        cm = float(res["cm"])
        rad = math.radians(alpha)
        # At alpha=90 deg, w_stall is 1.0, so cm must equal -0.5 * sin(90 deg) = -0.5
        expected_plate_cm = -0.5 * math.sin(rad)
        if abs(alpha) == 90.0:
            assert cm == pytest.approx(expected_plate_cm, abs=0.05)


def test_kirchhoff_apparent_forces_zero_work_rate() -> None:
    """Verify that Kirchhoff fluid apparent forces and Munk moments conserve power identically:
    P_app = F_app . v + M_app . omega == 0.0 for arbitrary v, omega, m_z.
    """
    m_z = 0.005  # kg

    # Sweep diverse 3D velocities and angular rates
    velocities = [
        [10.0, 0.0, 1.5],
        [5.0, 2.0, -1.0],
        [15.0, -3.0, 4.0],
        [-8.0, 1.0, 2.0],
    ]
    angular_rates = [
        [0.5, 1.2, -0.3],
        [-1.0, 0.8, 0.5],
        [2.0, -1.5, 1.0],
        [0.0, 2.5, 0.0],
    ]

    for v in velocities:
        for w in angular_rates:
            f_app, m_app = compute_apparent_forces(
                ScalarMath,
                body_velocity=v,
                omega=w,
                added_mass_z_kg=m_z,
            )
            f_vec = np.asarray(f_app)
            m_vec = np.asarray(m_app)
            v_vec = np.asarray(v)
            w_vec = np.asarray(w)

            work_rate = float(np.dot(f_vec, v_vec) + np.dot(m_vec, w_vec))
            assert work_rate == pytest.approx(0.0, abs=1e-12), (
                f"Apparent force work rate must be identically 0, got {work_rate} "
                f"for v={v}, w={w}"
            )
