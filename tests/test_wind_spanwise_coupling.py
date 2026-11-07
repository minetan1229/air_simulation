"""Issue #137: spanwise wind-gradient coupling audit.

6DoF aero uses a single wind vector sampled at the CG (flight_sim._compute_dynamics).
This audit records, via the public wind_vector() entry point, how much the
spanwise wingtip wind differs from the CG wind, and documents that the current
model intentionally omits the resulting differential load. Uniform wind must
match exactly (backward compatible).
"""
from __future__ import annotations

import numpy as np

from physics.config_schema import EnvironmentConfig
from physics.flight_sim import wind_vector


def test_uniform_wind_is_spanwise_uniform() -> None:
    env = EnvironmentConfig(wind_speed=2.0, wind_direction_deg=90.0, gust_intensity=0.0, turbulence_scale=0.0)
    pos = (0.0, 0.0, -10.0)
    w_cg = wind_vector(pos, env, 0.0)
    # Uniform field: wingtip offsets must not change the vector.
    assert np.allclose(w_cg, wind_vector((0.0, 0.11, -10.0), env, 0.0), atol=1e-9)
    assert np.allclose(w_cg, wind_vector((0.0, -0.11, -10.0), env, 0.0), atol=1e-9)


def test_cg_vs_wingtip_difference_is_quantified() -> None:
    env = EnvironmentConfig(wind_speed=2.0, wind_direction_deg=0.0)
    pos = (0.0, 0.0, -10.0)
    w_cg = wind_vector(pos, env, 0.0)
    w_tip = wind_vector((0.0, 0.11, -10.0), env, 0.0)
    # With default turbulence the spanwise difference is nonzero but small;
    # the 6DoF model uses only w_cg, so this documents the omitted signal.
    diff = float(np.linalg.norm(w_tip - w_cg))
    assert 0.0 <= diff < 1.0
