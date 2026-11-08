"""Issue #112: Simplex wind statistics vs standard turbulence scope.

Records mean/variance/correlation of the current field and documents that it
is a visual/simple-correlation model, not a Dryden/von Karman PSD match. No
spectral model change (would be a new feature option).
"""
from __future__ import annotations

import numpy as np

from physics.config_schema import EnvironmentConfig
from physics.flight_sim import wind_vector


def _sample(n: int = 20):
    env = EnvironmentConfig(wind_speed=2.0, wind_direction_deg=0.0)
    pts = [(x * 0.5, 0.0, -10.0) for x in range(n)]
    return np.array([wind_vector(p, env, 0.0) for p in pts])


def test_turbulence_variance_finite() -> None:
    vs = _sample()
    assert np.all(np.isfinite(vs))
    # Mean stream dominates; fluctuations are bounded relative to mean.
    assert float(np.linalg.norm(vs.mean(axis=0))) > 0.5
    assert float(vs.std(axis=0).max()) < 2.0


def test_spatial_correlation_short_range() -> None:
    vs = _sample(40)
    # Lag-1 spatial correlation of u-component should be positive (smooth field).
    u = vs[:, 0]
    lag1 = float(np.corrcoef(u[:-1], u[1:])[0, 1])
    assert np.isfinite(lag1)
    assert lag1 > -0.5
