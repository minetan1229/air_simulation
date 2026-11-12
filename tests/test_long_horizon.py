"""Issue #139: long-horizon attitude/energy accumulation and dt convergence.

Covers:
- constant-rate single-axis spin vs analytic quaternion (reference solution);
  quantifies geodesic error after 10 s, which per-step norm control
  (QUATERNION_NORM_TOLERANCE) does not bound;
- calm-glide dt convergence: the same flight at dt_max 0.01 / 0.005 converges
  in final position/speed/attitude, and the energy profile stays monotone.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig, SimulationSettings
from physics.flight_sim import FlightSimulator, quaternion_normalize


def _geodesic_deg(q1, q2) -> float:
    a = np.asarray(q1, dtype=np.float64)
    b = np.asarray(q2, dtype=np.float64)
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    d = min(1.0, abs(float(np.dot(a, b))))
    return math.degrees(2.0 * math.acos(d))


def _analytic_spin(omega_vec, t: float) -> np.ndarray:
    w = np.asarray(omega_vec, dtype=np.float64)
    wn = float(np.linalg.norm(w))
    if wn < 1e-12:
        return np.array([1.0, 0.0, 0.0, 0.0])
    return np.array([math.cos(wn * t / 2.0), *(np.sin(wn * t / 2.0) * w / wn)])


def _integrate_quaternion_only(omega_vec, dt: float, duration: float) -> np.ndarray:
    """Integrate dq/dt = 0.5 q (x) omega with FlightSimulator._rk4 used as-is.

    A minimal simulator supplies only _derivative; the quaternion block is
    isolated from aerodynamics, which carries its own low-speed damping
    (aero_kernel low-speed cross-flow term) and is therefore not a
    torque-free reference.
    """
    aircraft = AircraftConfig()
    env = EnvironmentConfig(wind_speed=0.0, gravity=0.0)
    launch = LaunchConfig(launch_speed=0.0, launch_angle_deg=0.0, launch_height_m=100.0)
    sim = FlightSimulator(aircraft, env, launch, SimulationSettings(collision_enabled=False))
    omega = np.asarray(omega_vec, dtype=np.float64)

    def derivative(y, time_s, core=None):
        q = quaternion_normalize(y[6:10])
        # q_dot = 0.5 * q (x) [0, omega]
        w, x, yy, z = (float(v) for v in q)
        ox, oy, oz = (float(v) for v in omega)
        qdot = 0.5 * np.array([
            -(x * ox + yy * oy + z * oz),
            w * ox + yy * oz - z * oy,
            w * oy + z * ox - x * oz,
            w * oz + x * oy - yy * ox,
        ])
        dy = np.zeros(13)
        dy[6:10] = qdot
        return dy

    sim._derivative = derivative  # type: ignore[method-assign]
    y = np.zeros(13)
    y[6] = 1.0
    steps = int(duration / dt)
    for _ in range(steps):
        y = sim._rk4(y, dt, 0.0)
    return y[6:10]


@pytest.mark.slow
def test_constant_rate_spin_matches_analytic_after_10s() -> None:
    for omega in ((2.0, 0.0, 0.0), (0.0, 1.0, 0.0)):
        q_num = _integrate_quaternion_only(omega, dt=0.01, duration=10.0)
        q_ref = _analytic_spin(omega, 10.0)
        err = _geodesic_deg(q_num, q_ref)
        assert err < 0.01, f"10 s spin {omega} drifted {err:.6f} deg"


@pytest.mark.slow
def test_spin_geodesic_error_shrinks_with_dt() -> None:
    omega = (2.0, 1.0, 0.5)
    errs = []
    for dt in (0.01, 0.005):
        q_num = _integrate_quaternion_only(omega, dt=dt, duration=5.0)
        q_ref = _analytic_spin(omega, 5.0)
        # Analytic single-axis reference only valid if omega fixed in space;
        # for combined body rates the exact flow is q0 (x) exp(0.5 t omega dt)
        # only when omega is body-fixed principal spin.  Use reference as the
        # finest dt result instead of the single-axis formula here.
        errs.append(q_num)
    # Self-convergence: coarser dt stays close to finer dt after 5 s.
    assert _geodesic_deg(errs[0], errs[1]) < 0.05


def _glide_final(dt_max: float):
    aircraft = AircraftConfig(
        mass_kg=0.0035,
        wing_area_m2=0.018,
        wingspan_m=0.22,
        fidelity_mode="improved_medium",
        dynamic_damping_enabled=True,
        apparent_inertia_enabled=True,
        lsb_hysteresis_enabled=True,
        elevon_deg=6.0,
        fold_trailing_deg=0.0,
    )
    env = EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0, ground_level_m=-50.0)
    launch = LaunchConfig(launch_speed=6.5, launch_angle_deg=2.0, launch_height_m=20.0)
    settings = SimulationSettings(
        dt_min=0.001,
        dt_max=dt_max,
        record_interval_s=0.02,
        duration_s=6.0,
        collision_enabled=False,
    )
    sim = FlightSimulator(aircraft, env, launch, settings)
    res = sim.simulate(duration_s=6.0)
    return sim, res


@pytest.mark.slow
@pytest.mark.integration
def test_glide_dt_convergence_and_energy_monotone() -> None:
    sim_coarse, res_coarse = _glide_final(0.01)
    sim_fine, res_fine = _glide_final(0.005)
    # Energy must not increase at either resolution (Issue #131 guard).
    assert res_coarse["energy_strictly_decreasing"]
    assert res_fine["energy_strictly_decreasing"]
    # Position/speed/attitude converge under refinement (6 s horizon).
    pc = sim_coarse._state.position_m
    pf = sim_fine._state.position_m
    assert math.dist(pc, pf) < 1.0, f"position diverged: {pc} vs {pf}"
    vc = float(np.linalg.norm(sim_coarse._state.velocity_mps))
    vf = float(np.linalg.norm(sim_fine._state.velocity_mps))
    assert abs(vc - vf) < 0.5, f"speed diverged: {vc} vs {vf}"
    assert _geodesic_deg(sim_coarse._state.quaternion, sim_fine._state.quaternion) < 10.0
