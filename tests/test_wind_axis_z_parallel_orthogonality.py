"""Dedicated tests for wind-axis lift direction and basis orthonormality (Issue #86).

Verifies that for flow parallel to the body z-axis (vz = +/-10 with vx=vy=0),
and across 3D directions including AoA sweeps approaching +/-90 deg,
the wind-axis triad (drag_axis, lift_axis, side_axis) forms a strictly
orthonormal, right-handed basis with:
    ||d|| = 1, ||l|| = 1, ||s|| = 1,
    d . l = 0, d . s = 0, l . s = 0,
    s = d x l
and continuous force transitions without discrete flips.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from physics.geometry_utils import wind_force_to_body


def test_pure_z_flow_orthonormality():
    """Verify strict orthonormality when flow is parallel or anti-parallel to body z-axis."""
    test_cases = [
        ([0.0, 0.0, 10.0], np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])),
        ([0.0, 0.0, -10.0], np.array([-1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])),
        ([0.0, 0.0, 1.0], np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])),
        ([0.0, 0.0, -1.0], np.array([-1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])),
    ]

    for vel, expected_lift, expected_side in test_cases:
        v = np.array(vel, dtype=np.float64)
        speed = float(np.linalg.norm(v))
        d = v / speed

        f_d = wind_force_to_body(v, drag=1.0, side=0.0, lift=0.0)
        f_l = wind_force_to_body(v, drag=0.0, side=0.0, lift=1.0)
        f_s = wind_force_to_body(v, drag=0.0, side=1.0, lift=0.0)

        # Expected direction & right-handed triad
        assert np.allclose(f_d, -d, atol=1e-12)
        assert np.allclose(f_l, expected_lift, atol=1e-12)
        assert np.allclose(f_s, expected_side, atol=1e-12)

        # Unit lengths
        assert math.isclose(float(np.linalg.norm(f_l)), 1.0, abs_tol=1e-12)
        assert math.isclose(float(np.linalg.norm(f_s)), 1.0, abs_tol=1e-12)

        # Exact mutual orthogonality
        assert abs(float(np.dot(d, f_l))) < 1e-12
        assert abs(float(np.dot(d, f_s))) < 1e-12
        assert abs(float(np.dot(f_l, f_s))) < 1e-12

        # Right-handed coordinate system: s = d x l
        assert np.allclose(np.cross(d, f_l), f_s, atol=1e-12)


def test_aoa_sweep_continuous_across_plus_minus_90_deg():
    """Verify continuity and no discrete flips when AoA approaches +/- 90 degrees."""
    speed = 10.0

    # Sweep around +90 deg (vx = cos(alpha), vz = sin(alpha))
    alphas_pos = np.linspace(80.0, 100.0, 201)
    forces_pos = []
    for a in alphas_pos:
        rad = math.radians(a)
        v = np.array([speed * math.cos(rad), 0.0, speed * math.sin(rad)])
        f_l = wind_force_to_body(v, 0.0, 0.0, 1.0)
        d = v / speed
        assert abs(float(np.dot(d, f_l))) < 1e-12
        forces_pos.append(f_l)

    forces_pos_arr = np.array(forces_pos)
    diffs_pos = np.linalg.norm(np.diff(forces_pos_arr, axis=0), axis=1)
    assert float(diffs_pos.max()) < 0.05, f"Discontinuity found around +90 deg: max step = {diffs_pos.max()}"

    # Sweep around -90 deg
    alphas_neg = np.linspace(-100.0, -80.0, 201)
    forces_neg = []
    for a in alphas_neg:
        rad = math.radians(a)
        v = np.array([speed * math.cos(rad), 0.0, speed * math.sin(rad)])
        f_l = wind_force_to_body(v, 0.0, 0.0, 1.0)
        d = v / speed
        assert abs(float(np.dot(d, f_l))) < 1e-12
        forces_neg.append(f_l)

    forces_neg_arr = np.array(forces_neg)
    diffs_neg = np.linalg.norm(np.diff(forces_neg_arr, axis=0), axis=1)
    assert float(diffs_neg.max()) < 0.05, f"Discontinuity found around -90 deg: max step = {diffs_neg.max()}"


def test_random_3d_orthonormality():
    """Verify that for arbitrary random 3D velocities, wind_force_to_body is always orthonormal."""
    rng = np.random.default_rng(42)
    for _ in range(500):
        v = rng.standard_normal(3)
        speed = float(np.linalg.norm(v))
        if speed < 1e-6:
            continue
        d = v / speed

        f_d = wind_force_to_body(v, drag=1.0, side=0.0, lift=0.0)
        f_l = wind_force_to_body(v, drag=0.0, side=0.0, lift=1.0)
        f_s = wind_force_to_body(v, drag=0.0, side=1.0, lift=0.0)

        # Norms
        assert math.isclose(float(np.linalg.norm(f_d)), 1.0, abs_tol=1e-12)
        assert math.isclose(float(np.linalg.norm(f_l)), 1.0, abs_tol=1e-12)
        assert math.isclose(float(np.linalg.norm(f_s)), 1.0, abs_tol=1e-12)

        # Orthogonality
        assert abs(float(np.dot(d, f_l))) < 1e-12
        assert abs(float(np.dot(d, f_s))) < 1e-12
        assert abs(float(np.dot(f_l, f_s))) < 1e-12

        # Right-handedness
        assert np.allclose(np.cross(d, f_l), f_s, atol=1e-12)
