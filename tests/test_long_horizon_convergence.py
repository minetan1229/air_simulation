"""Long-horizon numerical convergence, quaternion attitude error, and energy audit.

Addresses Issue #139:
[Numerics/Long-Horizon] 長時間滑空における姿勢・エネルギー誤差の累積と時間刻み収束を検証する

Tests:
1. Analytic constant angular velocity rotation over long horizon (t=60s).
   Verifies that quaternion normalisation preserves the rotation axis and attitude geodesic distance.
2. Torque-free asymmetric rigid-body rotation (Euler's equations).
   Verifies kinetic energy and angular momentum conservation over long horizons without dissipation.
3. Timestep convergence order on unpowered unforced 6DoF glide (dt, dt/2, dt/4).
   Verifies position, velocity, and attitude geodesic error convergence.
4. Total mechanical energy conservation in unpowered, unforced calm glide.
   Verifies monotonic mechanical energy decay (kinetic + rotational + potential <= initial).
5. Step-level quaternion raw norm drift vs QUATERNION_NORM_TOLERANCE.
   Verifies that raw (pre-normalisation) quaternion drift remains well within the acceptance guard.
6. Long-glide error consistency between 6DoF reference and GPU/vectorised dynamics.
"""

from __future__ import annotations

import math
from typing import Sequence, Tuple

import numpy as np
import pytest

from physics import flight_sim, gpu_dynamics, parallel_engine
from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    SimulationSettings,
    StateVector,
    as_dict,
)
from physics.geometry_utils import (
    ArrayLike,
    angular_acceleration_body,
    quaternion_derivative,
    quaternion_from_euler,
    quaternion_normalize,
)


def _geodesic_distance_rad(q1: ArrayLike, q2: ArrayLike) -> float:
    """Geodesic distance on SO(3) between two unit quaternions: 2 * arccos(|q1 . q2|)."""
    a = quaternion_normalize(q1)
    b = quaternion_normalize(q2)
    inner = abs(float(np.dot(a, b)))
    inner = min(1.0, max(-1.0, inner))
    return 2.0 * math.acos(inner)


# -----------------------------------------------------------------------------
# 1. Analytic constant angular velocity rotation over long horizon (60s)
# -----------------------------------------------------------------------------
def test_constant_angular_velocity_long_term_attitude_accuracy():
    """Verify attitude accuracy and geodesic distance under constant angular rate for 60 seconds."""
    dt = 0.01
    duration_s = 60.0
    steps = int(duration_s / dt)
    omega = np.array([1.2, -0.8, 2.5], dtype=np.float64)  # 3D arbitrary spin
    omega_norm = float(np.linalg.norm(omega))
    axis = omega / omega_norm

    # Exact solution at t: q(t) = exp(0.5 * omega * t)
    total_angle = omega_norm * duration_s
    q_exact = np.array(
        [
            math.cos(total_angle * 0.5),
            axis[0] * math.sin(total_angle * 0.5),
            axis[1] * math.sin(total_angle * 0.5),
            axis[2] * math.sin(total_angle * 0.5),
        ],
        dtype=np.float64,
    )

    # Numerical integration using RK4 matching flight_sim._rk4
    q_num = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
    max_raw_norm_err = 0.0

    for _ in range(steps):
        k1 = quaternion_derivative(q_num, omega)
        k2 = quaternion_derivative(q_num + 0.5 * dt * k1, omega)
        k3 = quaternion_derivative(q_num + 0.5 * dt * k2, omega)
        k4 = quaternion_derivative(q_num + dt * k3, omega)
        q_next = q_num + (dt / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)
        raw_norm_err = abs(float(np.linalg.norm(q_next)) - 1.0)
        if raw_norm_err > max_raw_norm_err:
            max_raw_norm_err = raw_norm_err
        q_num = quaternion_normalize(q_next)

    # 1. Raw norm drift per step must be well within QUATERNION_NORM_TOLERANCE (5e-3)
    assert max_raw_norm_err < flight_sim.QUATERNION_NORM_TOLERANCE, (
        f"Max raw norm error {max_raw_norm_err} exceeded QUATERNION_NORM_TOLERANCE"
    )
    assert max_raw_norm_err < 1.0e-8, f"Unexpectedly large raw norm error {max_raw_norm_err}"

    # 2. Long-term geodesic distance after 60s must remain negligible
    geod_err = _geodesic_distance_rad(q_num, q_exact)
    assert geod_err < 1.0e-10, f"Long horizon attitude drifted: geodesic error {geod_err} rad"


# -----------------------------------------------------------------------------
# 2. Torque-free asymmetric rigid-body rotation over long horizon
# -----------------------------------------------------------------------------
def test_torque_free_rigid_body_long_term_conservation():
    """Verify Euler's rotational equations conserve angular momentum magnitude and energy over 30s."""
    inertia = np.diag([2.5e-5, 5.0e-5, 6.5e-5])
    omega0 = np.array([2.5, -1.8, 3.2], dtype=np.float64)

    # Initial invariants
    l0 = inertia @ omega0
    l0_norm = float(np.linalg.norm(l0))
    e0_rot = 0.5 * float(omega0 @ (inertia @ omega0))

    dt = 0.001
    duration_s = 30.0
    steps = int(duration_s / dt)

    omega = omega0.copy()
    zero_moment = np.zeros(3, dtype=np.float64)

    for _ in range(steps):
        k1 = angular_acceleration_body(inertia, omega, zero_moment)
        k2 = angular_acceleration_body(inertia, omega + 0.5 * dt * k1, zero_moment)
        k3 = angular_acceleration_body(inertia, omega + 0.5 * dt * k2, zero_moment)
        k4 = angular_acceleration_body(inertia, omega + dt * k3, zero_moment)
        omega += (dt / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)

    l_final = inertia @ omega
    l_final_norm = float(np.linalg.norm(l_final))
    e_final_rot = 0.5 * float(omega @ (inertia @ omega))

    assert math.isclose(l_final_norm, l0_norm, rel_tol=1e-8), (
        f"Angular momentum drift: |L0|={l0_norm}, |L_final|={l_final_norm}"
    )
    assert math.isclose(e_final_rot, e0_rot, rel_tol=1e-8), (
        f"Rotational energy drift: E0={e0_rot}, E_final={e_final_rot}"
    )


# -----------------------------------------------------------------------------
# 3. Timestep convergence order on unpowered unforced glide
# -----------------------------------------------------------------------------
def test_glide_timestep_convergence_order():
    """Verify spatial, velocity, and attitude convergence order under dt halving."""
    aircraft = AircraftConfig(mass_kg=0.005)
    env = EnvironmentConfig(gravity=9.80665, drag_scale=1.0, lift_scale=1.0, wind_speed=0.0)
    launch = LaunchConfig(launch_speed=8.0, launch_angle_deg=0.0, launch_height_m=500.0)

    # High altitude and collision disabled to test pure 6DoF flight dynamics
    duration_s = 1.0
    dts = [0.002, 0.001, 0.0005]
    final_states: list[StateVector] = []

    for dt in dts:
        sim = flight_sim.FlightSimulator(
            aircraft,
            env,
            launch,
            SimulationSettings(
                dt_max=dt,
                dt_min=dt,
                max_substeps=4000,
                collision_enabled=False,
                adaptive=False,
            ),
        )
        sim.simulate(duration_s=duration_s, record=False)
        assert sim._step_rejections == 0, f"dt={dt} experienced unexpected step rejections"
        final_states.append(sim.state)

    p0, p1, p2 = [np.array(s.position_m) for s in final_states]
    v0, v1, v2 = [np.array(s.velocity_mps) for s in final_states]
    q0, q1, q2 = [s.quaternion for s in final_states]

    # Differences between consecutive refinement levels
    dp_coarse = float(np.linalg.norm(p0 - p1))
    dp_fine = float(np.linalg.norm(p1 - p2))
    assert dp_fine < dp_coarse, f"Position error did not decrease: coarse={dp_coarse}, fine={dp_fine}"
    order_p = math.log2(dp_coarse / dp_fine)
    # RK4 theoretical order is 4th order global convergence; ensure at least order >= 3.5
    assert order_p >= 3.5, f"Observed position convergence order {order_p} < 3.5"

    dv_coarse = float(np.linalg.norm(v0 - v1))
    dv_fine = float(np.linalg.norm(v1 - v2))
    assert dv_fine < dv_coarse, f"Velocity error did not decrease: coarse={dv_coarse}, fine={dv_fine}"
    order_v = math.log2(dv_coarse / dv_fine)
    assert order_v >= 3.5, f"Observed velocity convergence order {order_v} < 3.5"

    # Attitude geodesic error convergence
    dq_coarse = float(np.linalg.norm(np.array(q0) - np.array(q1)))
    dq_fine = float(np.linalg.norm(np.array(q1) - np.array(q2)))
    assert dq_fine < dq_coarse, f"Quaternion error did not decrease: coarse={dq_coarse}, fine={dq_fine}"
    order_q = math.log2(dq_coarse / dq_fine)
    assert order_q >= 2.5, f"Observed attitude convergence order {order_q} < 2.5"


# -----------------------------------------------------------------------------
# 4. Long-glide energy conservation in unpowered calm air
# -----------------------------------------------------------------------------
def test_long_glide_mechanical_energy_monotonic_dissipation():
    """Verify total mechanical energy (E_kin + E_rot + E_pot) is monotonically non-increasing."""
    aircraft = AircraftConfig(mass_kg=0.005)
    env = EnvironmentConfig(gravity=9.80665, drag_scale=1.0, lift_scale=1.0, wind_speed=0.0)
    launch = LaunchConfig(launch_speed=8.0, launch_angle_deg=0.0, launch_height_m=200.0)

    sim = flight_sim.FlightSimulator(
        aircraft,
        env,
        launch,
        SimulationSettings(
            dt_max=0.005,
            dt_min=0.005,
            max_substeps=2000,
            collision_enabled=False,
            adaptive=False,
            record_interval_s=0.05,
        ),
    )
    sim.simulate(duration_s=5.0, record=True)

    metrics = sim.metrics()
    energy_profile = metrics["energy_profile_j"]
    assert len(energy_profile) > 10, "Insufficient recorded energy samples"
    assert metrics["energy_strictly_decreasing"], "Energy profile was not strictly non-increasing"

    # Total energy at end of glide must be lower than at launch due to aerodynamic drag
    e_initial = energy_profile[0]
    e_final = energy_profile[-1]
    assert e_final < e_initial, f"Mechanical energy was not dissipated: E0={e_initial}, E_end={e_final}"

    # Verify per-step maximum raw quaternion norm drift stayed well within tolerance
    assert sim._last_quaternion_norm_error < flight_sim.QUATERNION_NORM_TOLERANCE, (
        f"Raw quaternion norm error {sim._last_quaternion_norm_error} exceeded QUATERNION_NORM_TOLERANCE"
    )


# -----------------------------------------------------------------------------
# 5. Long-glide CPU 6DoF vs GPU device batch consistency
# -----------------------------------------------------------------------------
def test_cpu_gpu_long_glide_consistency():
    """Verify CPU 6DoF reference and vectorized batch engine agree on long unforced glide."""
    aircraft = AircraftConfig(mass_kg=0.005)
    env = EnvironmentConfig(gravity=9.80665, drag_scale=1.0, lift_scale=1.0, wind_speed=0.0)
    launch = LaunchConfig(launch_speed=8.0, launch_angle_deg=0.0, launch_height_m=50.0)

    duration_s = 4.0
    dt_max = 0.02

    # 1. CPU Reference flight
    res_cpu = flight_sim.evaluate_flight(
        as_dict(aircraft), as_dict(env), as_dict(launch), duration_s=duration_s, dt_max=dt_max
    )

    # 2. Device Batch flight (CPU numpy backend)
    case = {
        "aircraft": as_dict(aircraft),
        "environment": as_dict(env),
        "launch": as_dict(launch),
        "duration_s": duration_s,
        "dt_max": dt_max,
    }
    res_batch = parallel_engine.evaluate_cases_on_device([case], np, backend="numpy")[0]

    # Verify trajectory metrics consistency within physical tolerance
    # In evaluate_cases_on_device, non-landed non-crashed flights reach duration and report "timeout",
    # whereas evaluate_flight reports "flying" if still in the air. Both denote ongoing flight.
    assert (res_cpu["status"] == res_batch["status"]) or (
        res_cpu["status"] == "flying" and res_batch["status"] == "timeout"
    )
    assert res_batch["flight_time_s"] == pytest.approx(res_cpu["flight_time_s"], rel=1e-3)
    assert res_batch["distance_m"] == pytest.approx(res_cpu["distance_m"], rel=5.0e-2)
    assert res_batch["max_stress_pa"] == pytest.approx(res_cpu["max_stress_pa"], rel=1e-3)
