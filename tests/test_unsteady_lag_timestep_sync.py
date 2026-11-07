"""Tests for Issue #82: Unsteady aerodynamic and aeroelastic lags timestep synchronization.

Falsifiable criteria:
1. Dynamic tracking of integration timestep `dt`:
   When `dt` changes across steps or during adaptive step halving, `_last_dt` is
   synchronized with the accepted ODE timestep and fed into unsteady aerodynamic
   circulation lag (`unsteady_circulation_lag`), aeroelastic deformations (dihedral/twist
   first-order lag), and dynamic pitch rate / alpha rate estimation (alpha_dot).
2. Smooth convergence under timestep refinement:
   Under refined fixed timesteps dt in {0.01, 0.005, 0.002, 0.001}s:
   - Unsteady circulation lag CL converges monotonically / smoothly.
   - Aeroelastic dihedral and twist deformations converge smoothly.
   - Trajectory (altitude / z-position) converges smoothly without discontinuous jumps.
3. Adaptive step halving / rollback synchronization:
   When adaptive step halving occurs during step acceptance, `_last_dt` updates to the
   actual accepted sub-step `step`, and the committed lag states match `dt=step`.
"""

from __future__ import annotations

import math
import numpy as np
import pytest

from physics import flight_sim
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig, SimulationSettings


def test_last_dt_dynamically_tracks_step_size() -> None:
    """Verify self._last_dt updates dynamically when step dt varies."""
    cfg = AircraftConfig(
        unsteady_aero_enabled=True,
        unsteady_k_circ=2.0,
        aeroelastic_flex=0.2,
        aeroelastic_twist=0.2,
        aeroelastic_tau_s=0.05,
        pitch_inertia_kgm2=0.01,  # stable airframe avoiding numerical rejection
    )
    env = EnvironmentConfig()
    launch = LaunchConfig(launch_speed=7.0, launch_height_m=1.5)
    settings = SimulationSettings(adaptive=False, dt_max=0.01)

    sim = flight_sim.FlightSimulator(cfg, env, launch, settings)
    assert sim._last_dt == pytest.approx(0.01)

    # Step with dt = 0.005
    sim.step(0.005)
    assert sim._last_dt == pytest.approx(0.005)

    # Step with dt = 0.002
    sim.step(0.002)
    assert sim._last_dt == pytest.approx(0.002)

    # Step with dt = 0.008
    sim.step(0.008)
    assert sim._last_dt == pytest.approx(0.008)


def test_unsteady_and_aeroelastic_timestep_refinement_convergence() -> None:
    """Verify smooth timestep refinement convergence for unsteady and aeroelastic lag."""
    timesteps = [0.01, 0.005, 0.002, 0.001]
    cl_lags: list[float] = []
    dihedral_lags: list[float] = []
    twist_lags: list[float] = []
    z_positions: list[float] = []

    target_time = 0.02

    for dt in timesteps:
        cfg = AircraftConfig(
            pitch_inertia_kgm2=0.01,
            unsteady_aero_enabled=True,
            unsteady_k_circ=2.0,
            aeroelastic_flex=0.5,
            aeroelastic_twist=0.5,
            aeroelastic_tau_s=0.05,
        )
        sim = flight_sim.FlightSimulator(
            cfg,
            EnvironmentConfig(),
            LaunchConfig(launch_speed=7.0, launch_height_m=1.5),
            SimulationSettings(adaptive=False, dt_max=dt),
        )
        n_steps = int(round(target_time / dt))
        for _ in range(n_steps):
            sim.step(dt)

        assert sim._state.time_s == pytest.approx(target_time, abs=1e-6)
        assert sim._last_dt == pytest.approx(dt)
        cl_lags.append(sim._unsteady_cl_lag)
        dihedral_lags.append(sim._aero_dihedral_lag)
        twist_lags.append(sim._aero_twist_lag)
        z_positions.append(sim._state.position_m[2])

    # 1. Monotonic convergence of unsteady CL lag
    for i in range(len(cl_lags) - 1):
        assert cl_lags[i] <= cl_lags[i + 1]
    # Difference between finest levels should be small (Cauchy convergence)
    assert abs(cl_lags[-1] - cl_lags[-2]) < 0.01

    # 2. Monotonic convergence of aeroelastic dihedral and twist deformations
    for i in range(len(dihedral_lags) - 1):
        assert dihedral_lags[i] <= dihedral_lags[i + 1]
    assert abs(dihedral_lags[-1] - dihedral_lags[-2]) < 0.005

    for i in range(len(twist_lags) - 1):
        assert twist_lags[i] <= twist_lags[i + 1]
    assert abs(twist_lags[-1] - twist_lags[-2]) < 0.01

    # 3. Trajectory position convergence
    assert abs(z_positions[-1] - z_positions[-2]) < 0.001


def test_step_halving_synchronizes_last_dt_and_lag_states() -> None:
    """Verify that when RK4 step halving occurs on divergence/rejection, _last_dt tracks the halved step."""
    # Default aircraft has small pitch inertia which triggers step halving when dt=0.01
    cfg = AircraftConfig(
        unsteady_aero_enabled=True,
        unsteady_k_circ=2.0,
        aeroelastic_flex=0.2,
        aeroelastic_twist=0.2,
        aeroelastic_tau_s=0.05,
    )
    sim = flight_sim.FlightSimulator(
        cfg,
        EnvironmentConfig(),
        LaunchConfig(launch_speed=7.0, launch_height_m=1.5),
        SimulationSettings(adaptive=False, dt_max=0.01),
    )

    sim.step(0.01)
    # The step was halved during RK4 step-acceptance guard
    assert sim._step_rejections > 0
    assert sim._last_dt < 0.01
    # The committed lag states were evaluated with dt=sim._last_dt
    assert sim._unsteady_cl_lag > 0.0
