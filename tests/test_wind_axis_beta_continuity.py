"""Regression test: wind_force_to_body must be continuous at beta = +/- 90 deg.

At beta = +/- 90 deg the horizontal flow component vanishes, so the legacy
x-z lift axis lost its reference direction and jumped between approach
azimuths.  The lift axis is now Gram-Schmidt projected from body-down onto
the plane orthogonal to the flow and spherically blended to the azimuthal
axis, which is continuous at the pole and identical to the previous formula
at zero sideslip (vy == 0).
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from physics.geometry_utils import wind_force_to_body


def _legacy_wind_force(velocity, drag, side, lift):
    v = np.asarray(velocity, dtype=np.float64)
    speed = float(np.linalg.norm(v))
    if speed <= 1.0e-12:
        return np.zeros(3)
    drag_axis = v / speed
    horizontal = math.hypot(float(v[0]), float(v[2]))
    if horizontal > 1.0e-12:
        lift_axis = np.array([float(v[2]), 0.0, -float(v[0])]) / horizontal
    else:
        lift_axis = np.array([0.0, 0.0, -1.0])
    side_axis = np.cross(drag_axis, lift_axis)
    side_axis = side_axis / float(np.linalg.norm(side_axis))
    return -float(drag) * drag_axis + float(side) * side_axis + float(lift) * lift_axis


@pytest.mark.parametrize("velocity", [
    (5.0, 0.0, 0.0),
    (0.0, 0.0, 5.0),
    (3.0, 0.0, 4.0),
    (-3.0, 0.0, 4.0),
    (0.0, 0.0, -5.0),
    (1.0, 0.0, -1.0),
])
def test_zero_sideslip_matches_legacy_formula_exactly(velocity):
    drag, side, lift = 1.3, 2.7, 4.1
    actual = wind_force_to_body(velocity, drag, side, lift)
    expected = _legacy_wind_force(velocity, drag, side, lift)
    assert np.allclose(actual, expected, atol=1.0e-15, rtol=0.0)


def _force_at_beta(beta_deg, speed=10.0, drag=0.0, side=0.0, lift=1.0):
    beta = math.radians(beta_deg)
    v = np.array([speed * math.cos(beta), speed * math.sin(beta), 0.0], dtype=np.float64)
    return wind_force_to_body(v, drag, side, lift), v


def test_continuous_at_plus_90_deg():
    f_mid, _ = _force_at_beta(90.0)
    assert np.allclose(f_mid, np.array([0.0, 0.0, -1.0]), atol=1.0e-12)
    jump = max(
        float(np.linalg.norm(_force_at_beta(90.0 + d)[0] - _force_at_beta(90.0 - d)[0]))
        for d in (1e-3, 1e-4, 1e-5)
    )
    assert jump < 1.0e-2


def test_continuous_at_minus_90_deg():
    f_mid, _ = _force_at_beta(-90.0)
    assert np.allclose(f_mid, np.array([0.0, 0.0, -1.0]), atol=1.0e-12)
    jump = max(
        float(np.linalg.norm(_force_at_beta(-90.0 + d)[0] - _force_at_beta(-90.0 - d)[0]))
        for d in (1e-3, 1e-4, 1e-5)
    )
    assert jump < 1.0e-2


def test_fine_scan_has_no_discontinuity():
    speed = 10.0
    beta_lo, beta_hi = math.radians(89.99), math.radians(90.01)
    betas = np.linspace(beta_lo, beta_hi, 201)
    forces = np.stack([
        wind_force_to_body(np.array([speed * math.cos(b), speed * math.sin(b), 0.0]), 0.0, 0.0, 1.0)
        for b in betas
    ])
    steps = np.linalg.norm(np.diff(forces, axis=0), axis=1)
    assert float(steps.max()) < 1.0e-3
    assert np.isfinite(forces).all()


@pytest.mark.parametrize("beta_deg", [-90.0, -89.5, 0.0, 89.5, 90.0])
def test_lift_and_side_do_no_work_near_pole(beta_deg):
    speed = 10.0
    beta = math.radians(beta_deg)
    v = np.array([speed * math.cos(beta), speed * math.sin(beta), 0.0], dtype=np.float64)
    lift_vec = wind_force_to_body(v, 0.0, 0.0, 7.5)
    side_vec = wind_force_to_body(v, 0.0, 4.2, 0.0)
    assert float(lift_vec @ v) == pytest.approx(0.0, abs=1e-12)
    assert float(side_vec @ v) == pytest.approx(0.0, abs=1e-12)
    assert float(np.linalg.norm(lift_vec)) == pytest.approx(7.5, rel=1e-12)
    assert float(np.linalg.norm(side_vec)) == pytest.approx(4.2, rel=1e-12)
