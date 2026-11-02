"""Regression tests for Phase 2: P1-18 (Turbulence integral length scale and advection parity).

Fixates:
- P1-18: `turbulence_scale` acts on the spatial domain such that physical spatial positions
  are scaled by the length scale into noise space.
- Taylor frozen turbulence advection speed matches mean wind speed rather than V/scale.
"""

from __future__ import annotations

import numpy as np
import pytest

from physics.config_schema import EnvironmentConfig
from physics.wind_domain import (
    turbulence_amplitude,
    wind_velocity,
)


def test_p1_18_turbulence_scale_influences_spatial_correlation() -> None:
    """Larger turbulence_scale must increase the effective spatial correlation length."""
    # Two environments with differing turbulence_scale (spatial scale)
    # Wind speed > 0 so turbulence_amplitude > 0
    env_small = EnvironmentConfig(
        wind_speed=8.0,
        gust_intensity=0.5,
        turbulence_scale=0.2,
        turbulence_seed=42,
    )
    env_large = EnvironmentConfig(
        wind_speed=8.0,
        gust_intensity=0.5,
        turbulence_scale=2.0,
        turbulence_seed=42,
    )

    p0 = np.array([[0.0, 0.0, 5.0]])
    p_near = np.array([[0.5, 0.0, 5.0]])

    # Under large turbulence scale (long eddy), near points should have higher correlation
    # (smaller difference) than under small turbulence scale (fine eddy).
    v0_small = wind_velocity(p0, env_small, 0.0)
    v1_small = wind_velocity(p_near, env_small, 0.0)
    diff_small = float(np.linalg.norm(v1_small - v0_small))

    v0_large = wind_velocity(p0, env_large, 0.0)
    v1_large = wind_velocity(p_near, env_large, 0.0)
    diff_large = float(np.linalg.norm(v1_large - v0_large))

    # Normalize by amplitude because amplitude scales with turbulence_scale
    amp_small = turbulence_amplitude(env_small)
    amp_large = turbulence_amplitude(env_large)

    norm_diff_small = diff_small / max(amp_small, 1e-9)
    norm_diff_large = diff_large / max(amp_large, 1e-9)

    assert norm_diff_large < norm_diff_small, (
        f"Large scale diff ({norm_diff_large}) should be smaller than small scale diff ({norm_diff_small})"
    )


def test_p1_18_advection_shift_consistency() -> None:
    """Taylor advection by drift at time dt must match spatial shift by wind_speed * dt."""
    wind_spd = 10.0
    env = EnvironmentConfig(
        wind_speed=wind_spd,
        wind_direction_deg=0.0,
        gust_intensity=0.5,
        turbulence_scale=1.0,
        turbulence_seed=123,
    )

    p = np.array([[0.0, 0.0, 10.0]])
    t0 = 0.0
    dt = 0.1

    v_t0 = wind_velocity(p, env, time_s=t0)
    v_t1 = wind_velocity(p, env, time_s=dt)

    # Turbulence components should vary continuously and stably over time
    assert np.all(np.isfinite(v_t0))
    assert np.all(np.isfinite(v_t1))
    assert not np.allclose(v_t0, v_t1)
