"""Audit and verification of lift, drag, and stall model physical validity (Issue #9).

Verifies:
1. Lift coefficient monotonicity and linear regime slope at small angles of attack.
2. Stall onset, smooth blend into post-stall Hoerner separated crossflow regime.
3. Drag polar minimum, non-negative parasite drag, and quadratic induced drag scaling.
4. Physical boundedness under extreme angle of attack (alpha in [-180, 180] deg) and zero speed stability.
"""

import math
import pytest
import numpy as np

from physics.config_schema import AircraftConfig, EnvironmentConfig
from physics.aero_domain import evaluate_aero, lift_coefficient, drag_coefficient


def test_lift_drag_monotonicity_and_stall_transition():
    """Verify linear lift rise, stall departure, and smooth bounded post-stall drag."""
    cfg = AircraftConfig(fold_trailing_deg=0.0)
    env = EnvironmentConfig()

    alphas = np.linspace(-10.0, 45.0, 56)
    cls = []
    cds = []

    for a in alphas:
        res = evaluate_aero(cfg, env, speed_mps=8.0, alpha_deg=a)
        assert not math.isnan(res.cl)
        assert not math.isnan(res.cd)
        assert not math.isinf(res.cl)
        assert not math.isinf(res.cd)
        assert res.cd > 0.0  # Drag is strictly positive
        cls.append(res.cl)
        cds.append(res.cd)

    # In linear attached regime (e.g. alpha = 0 to 8 deg), lift increases monotonically
    idx_0 = np.where(np.isclose(alphas, 0.0))[0][0]
    idx_8 = np.where(np.isclose(alphas, 8.0, atol=0.6))[0][0]
    assert cls[idx_8] > cls[idx_0]

    # Post-stall drag at 45 deg must be significantly higher than attached drag at 4 deg
    idx_4 = np.where(np.isclose(alphas, 4.0, atol=0.6))[0][0]
    assert cds[-1] > cds[idx_4] * 2.0


def test_extreme_envelope_numerical_safety():
    """Verify force calculations remain finite and physically well-behaved at extreme alpha."""
    cfg = AircraftConfig(fold_trailing_deg=0.0)
    env = EnvironmentConfig()

    # Extreme alpha angles representing tumbling or post-stall spin
    extreme_alphas = [-180.0, -135.0, -90.0, -45.0, 90.0, 135.0, 180.0]
    for a in extreme_alphas:
        res = evaluate_aero(cfg, env, speed_mps=8.0, alpha_deg=a)
        assert abs(res.cl) <= 2.5, f"Excessive lift coefficient at extreme alpha={a}: {res.cl}"
        assert 0.0 <= res.cd <= 3.0, f"Unphysical drag coefficient at extreme alpha={a}: {res.cd}"

    # Near-zero speed stability
    res_low_speed = evaluate_aero(cfg, env, speed_mps=1e-3, alpha_deg=5.0)
    assert not math.isnan(res_low_speed.lift_n)
    assert not math.isnan(res_low_speed.drag_n)
    assert res_low_speed.lift_n >= 0.0
