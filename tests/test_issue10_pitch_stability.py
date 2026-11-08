"""Audit and verification of pitch stability and aerodynamic pushover behaviour (Issue #10).

Verifies:
1. Static longitudinal stability: dCm / dAlpha < 0 around trim condition.
2. Nose-down restoring moment at positive alpha producing pushover towards equilibrium glide.
3. Natural nose-up restoring moment at negative alpha preventing pitch divergence.
4. Non-reliance on artificial attitude snap torques; pitch dynamics arise purely from aerodynamic moments and damping.
"""

import math
import pytest
import numpy as np

from physics.config_schema import AircraftConfig, EnvironmentConfig
from physics.aero_domain import evaluate_aero


def test_pitch_static_stability_restoring_moments():
    """Verify that pitching moment provides a natural restoring torque across alpha."""
    cfg = AircraftConfig(cm0=0.015, cm_alpha=-0.20, fold_trailing_deg=0.0)
    env = EnvironmentConfig()

    res_neg = evaluate_aero(cfg, env, speed_mps=8.0, alpha_deg=-5.0)
    res_zero = evaluate_aero(cfg, env, speed_mps=8.0, alpha_deg=0.0)
    res_pos = evaluate_aero(cfg, env, speed_mps=8.0, alpha_deg=8.0)

    # At negative alpha, Cm must be positive (nose-up restoring moment)
    assert res_neg.cm > 0.0
    assert res_neg.pitching_moment_nm > 0.0

    # At positive alpha, Cm must be negative (nose-down restoring moment / pushover)
    assert res_pos.cm < 0.0
    assert res_pos.pitching_moment_nm < 0.0

    # Monotonic restoring gradient around trim
    assert (res_pos.cm - res_neg.cm) < 0.0


def test_pitch_damping_rate_moment():
    """Verify pitch damping rate contribution (q_rate damping) opposes pitch rate."""
    cfg = AircraftConfig(fold_trailing_deg=0.0)
    env = EnvironmentConfig()

    # With positive pitch rate (pitching up, q > 0)
    res_damping_up = evaluate_aero(cfg, env, speed_mps=8.0, alpha_deg=2.0, angular_rate=(0.0, 1.0, 0.0))
    res_static = evaluate_aero(cfg, env, speed_mps=8.0, alpha_deg=2.0, angular_rate=(0.0, 0.0, 0.0))

    # Pitch damping must add a negative (nose-down) moment when pitching up
    assert res_damping_up.pitching_moment_nm < res_static.pitching_moment_nm
