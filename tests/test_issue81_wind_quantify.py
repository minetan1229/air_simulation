"""Issue #81: mean-wind-only screening risk quantification tests."""

from __future__ import annotations

import math

import numpy as np
import pytest

from physics.flight_sim import wind_vector
from physics.ga2.wind_model_scope import (
    MEAN_WIND_SCOPE,
    measure_wind_rank_risk,
)


def _population(n=8):
    from physics.config_schema import AircraftConfig, as_dict

    base = AircraftConfig()
    pop = []
    for i in range(n):
        cfg = AircraftConfig(
            mass_kg=base.mass_kg,
            wingspan_m=base.wingspan_m * (0.9 + 0.05 * i),
            wing_area_m2=base.wing_area_m2 * (0.9 + 0.05 * i),
            cL0=base.cL0 * (0.95 + 0.03 * i),
            cd0=base.cd0 * (0.9 + 0.05 * i),
        )
        pop.append(as_dict(cfg))
    return pop


@pytest.mark.slow
def test_variable_wind_changes_trajectory_vs_mean():
    from physics.config_schema import EnvironmentConfig

    constant = EnvironmentConfig(
        wind_speed=2.0, gust_intensity=0.0, thermal_strength=0.0,
        turbulence_scale=0.0, boundary_layer_enabled=False,
    )
    sheared = EnvironmentConfig(
        wind_speed=2.0, gust_intensity=0.0, thermal_strength=0.0,
        turbulence_scale=0.0, boundary_layer_enabled=True,
    )
    pos = np.array([0.0, 0.0, -10.0])
    w_const = wind_vector(pos, constant, 0.0)
    w_shear = wind_vector(pos, sheared, 0.0)
    assert not np.allclose(w_const, w_shear, atol=1e-9), "altitude shear must change the field"

    gusty = EnvironmentConfig(
        wind_speed=2.0, gust_intensity=1.0, thermal_strength=0.0,
        turbulence_scale=0.0, boundary_layer_enabled=False,
    )
    w_t0 = wind_vector(pos, gusty, 0.0)
    w_t1 = wind_vector(pos, gusty, 1.0)
    assert not np.allclose(w_t0, w_t1, atol=1e-9), "gust must make the field time-varying"


@pytest.mark.slow
@pytest.mark.integration
def test_rank_correlation_and_miss_rate_in_range():
    pop = _population()
    report = measure_wind_rank_risk(pop, top_k=3)
    assert report["n_individuals"] == 8
    assert report["n_scenarios"] >= 4
    assert report["mean_wind_only"] is True
    for name, corr in report["correlation"].items():
        assert -1.0 <= corr <= 1.0, (name, corr)
        assert math.isfinite(corr)
    for name, miss in report["miss_rate"].items():
        assert 0.0 <= miss <= 1.0, (name, miss)
        assert math.isfinite(miss)
    for name, scores in report["scores"].items():
        assert len(scores) == 8
        assert all(math.isfinite(s) for s in scores), name
    print("rank risk:", report["correlation"], report["miss_rate"])


@pytest.mark.slow
def test_deterministic_under_fixed_seed():
    pop = _population()
    r1 = measure_wind_rank_risk(pop, top_k=3, seed=99)
    r2 = measure_wind_rank_risk(pop, top_k=3, seed=99)
    assert r1["correlation"] == r2["correlation"]
    assert r1["miss_rate"] == r2["miss_rate"]
    assert r1["scores"] == r2["scores"]


@pytest.mark.slow
@pytest.mark.integration
def test_stable_glide_ranking_quantified_and_scope_stated():
    pop = _population()
    report = measure_wind_rank_risk(pop, top_k=2)
    assert "authoritative_for" in MEAN_WIND_SCOPE
    assert "not_authoritative_for" in MEAN_WIND_SCOPE
    assert any("shear" in s or "gust" in s for s in MEAN_WIND_SCOPE["not_authoritative_for"])
    # constant baseline is self-consistent
    assert report["correlation"]["constant"] == 1.0
    assert report["miss_rate"]["constant"] == 0.0
    # full report carries variable-wind scenarios
    assert set(["shear", "gust", "turbulence"]) <= set(report["scores"])
    print("scenario correlations:", report["correlation"])
