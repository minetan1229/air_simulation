"""Regression fixation of the wind-tunnel bench mounting.

The tunnel stream is the velocity of the **air** along the tunnel axis, so a
model held on the bench has to face the oncoming flow.  The old mounting used
a heading of 0 deg, which turned a bench set to 4 deg into an incidence of
``180 + 4 = 184 deg`` (reported as ``-176 deg``): the model was blown from
behind and the aerodynamic evaluation ran on the wrong side of the polar.

The canonical function is :func:`physics.flight_sim.wind_tunnel_bench_quaternion`
(``no-finish.md`` K2-P0-8).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from physics import flight_sim
from physics.config_schema import AircraftConfig, EnvironmentConfig, SimulationSettings


def _bench(alpha_deg: float, beta_deg: float = 0.0, speed: float = 10.0):
    env = EnvironmentConfig(
        wind_tunnel_enabled=True,
        wind_tunnel_speed=speed,
        wind_tunnel_alpha_deg=alpha_deg,
        wind_tunnel_beta_deg=beta_deg,
    )
    sim = flight_sim.FlightSimulator(
        AircraftConfig(), env, settings=SimulationSettings(dt_max=0.01)
    )
    sim.reset()
    sim.simulate(duration_s=0.2, record=False)
    return sim, sim._diagnostics()


def test_bench_quaternion_mounts_the_model_facing_the_oncoming_flow():
    """The bench heading is 180 deg: the nose points into the flow."""
    q = flight_sim.wind_tunnel_bench_quaternion(0.0, 0.0)
    nose = flight_sim.body_to_world(q, (1.0, 0.0, 0.0))
    # +x is the tunnel axis, so the nose has to point at -x.
    assert float(nose[0]) == pytest.approx(-1.0, abs=1.0e-12)
    assert float(nose[1]) == pytest.approx(0.0, abs=1.0e-12)
    assert float(nose[2]) == pytest.approx(0.0, abs=1.0e-12)


def test_bench_quaternion_pitches_the_nose_up_by_the_requested_incidence():
    """A positive incidence lifts the nose above the oncoming flow."""
    q = flight_sim.wind_tunnel_bench_quaternion(4.0, 0.0)
    nose = flight_sim.body_to_world(q, (1.0, 0.0, 0.0))
    # The world frame is NED (+z down), so "nose up" is a negative z component.
    assert float(nose[2]) == pytest.approx(-math.sin(math.radians(4.0)), abs=1.0e-12)
    assert float(nose[0]) == pytest.approx(-math.cos(math.radians(4.0)), abs=1.0e-12)


def test_bench_incidence_is_the_requested_positive_alpha():
    """The simulator has to evaluate the bench at the requested incidence.

    The old mounting reported ``-176 deg`` for a 4 deg bench (the model was
    blown from behind), so the assertion is on the *reported* incidence and not
    only on the mounting quaternion.
    """
    _, diagnostics = _bench(4.0)
    assert float(diagnostics["alpha_deg"]) == pytest.approx(4.0, abs=1.0e-6)


def test_bench_incidence_is_zero_at_zero_deg():
    _, diagnostics = _bench(0.0)
    assert float(diagnostics["alpha_deg"]) == pytest.approx(0.0, abs=1.0e-6)
    assert abs(float(diagnostics["beta_deg"])) < 1.0e-6


def test_bench_sideslip_is_zero_without_a_requested_beta():
    _, diagnostics = _bench(4.0, 0.0)
    assert abs(float(diagnostics["beta_deg"])) < 1.0e-6


def test_bench_reset_and_step_use_the_same_mounting():
    """The reset and the step entry point must mount the model identically."""
    env = EnvironmentConfig(
        wind_tunnel_enabled=True,
        wind_tunnel_speed=10.0,
        wind_tunnel_alpha_deg=4.0,
        wind_tunnel_beta_deg=0.0,
    )
    sim = flight_sim.FlightSimulator(
        AircraftConfig(), env, settings=SimulationSettings(dt_max=0.01)
    )
    sim.reset()
    after_reset = tuple(float(value) for value in sim.state.quaternion)
    sim.step(0.05)
    after_step = tuple(float(value) for value in sim.state.quaternion)
    assert after_reset == pytest.approx(after_step, abs=1.0e-12)
    assert after_reset == pytest.approx(
        tuple(float(value) for value in flight_sim.wind_tunnel_bench_quaternion(4.0, 0.0)),
        abs=1.0e-12,
    )


def test_bench_quaternion_is_a_unit_quaternion():
    q = flight_sim.wind_tunnel_bench_quaternion(4.0, 2.0)
    assert float(np.linalg.norm(q)) == pytest.approx(1.0, abs=1.0e-12)
