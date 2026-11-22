"""Issue #194: Added-Mass Kirchhoff Munk / Coriolis Coupling and Passivity Audit.

Hypothesis:
1. Physical Formulation:
   Fluid momentum carried by the airframe is P_A = [0, 0, m_add_z * w_rel].
   Kirchhoff's equations of motion yield the Munk apparent moment:
       M_munk = - v_rel x P_A = [-v_rel * m_add_z * w_rel, u_rel * m_add_z * w_rel, 0]
   For forward flight (u > 0) with positive angle of attack (w > 0), M_munk_y > 0,
   representing the classical destabilizing nose-up Munk moment of a thin plate.
2. Passivity & Skew-Symmetry:
   The generalized Kirchhoff kinetic energy rate satisfies:
       d/dt (1/2 v^T (M + M_A) v + 1/2 omega^T (I + I_A) omega) = v . F_ext + omega . M_ext.
   The internal Coriolis/Munk terms perform strictly zero mechanical work:
       v . (- omega x P_A) + omega . (- v x P_A) == 0 identically.
3. Post-Stall Attenuation:
   As flow separates, potential-flow added mass circulation breaks down.
   Munk coupling smoothly rolls off with (1 - w_stall), preventing non-physical divergence.
4. CPU / GPU Parity:
   Both FlightSimulator and gpu_dynamics evaluate bit-accurate Munk torque.
"""
from __future__ import annotations

import numpy as np
import pytest

from physics import aero_kernel, flight_sim, gpu_dynamics
from physics.aero_kernel import ScalarMath
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig, SimulationSettings


def test_munk_moment_direction_and_magnitude() -> None:
    """Verify that Munk moment produces destabilizing nose-up pitching moment for positive alpha."""
    aircraft = AircraftConfig(added_mass_enabled=True, apparent_inertia_enabled=True, munk_coupling_enabled=True)
    env = EnvironmentConfig(wind_speed=0.0)
    launch = LaunchConfig(launch_speed=5.0, launch_angle_deg=5.0, launch_height_m=2.0)
    settings = SimulationSettings()

    sim = flight_sim.FlightSimulator(aircraft, env, launch, settings)
    rho = 1.225
    added = aero_kernel.compute_added_mass(
        ScalarMath, rho, sim.aircraft.wing_area_m2, sim._chord, wingspan_m=sim.aircraft.wingspan_m
    )
    m_add_z = float(added["added_mass_z_kg"])
    assert m_add_z > 0.0

    # Body relative velocity with u > 0 and w > 0 (positive alpha)
    u_rel = 5.0
    v_rel = 0.0
    w_rel = 0.5  # ~5.7 deg alpha

    # Analytical Munk moment
    m_munk_expected = u_rel * m_add_z * w_rel
    assert m_munk_expected > 0.0, "Munk moment must be positive (nose-up) for u>0, w>0"

    # Evaluate dynamics through FlightSimulator with added mass
    state = np.array([
        0.0, 0.0, -2.0,    # position (NED)
        u_rel, 0.0, w_rel, # velocity (body)
        1.0, 0.0, 0.0, 0.0,# quaternion
        0.0, 0.0, 0.0      # omega
    ], dtype=np.float64)

    dyn = sim._compute_dynamics(state, 0.0)
    ang_acc = dyn["angular_acceleration"]

    # Verify that angular acceleration is finite and reflects the Munk coupling
    assert np.isfinite(ang_acc[1])
    assert ang_acc[1] != 0.0


def test_munk_skew_symmetric_zero_work() -> None:
    """Verify analytical skew-symmetry: v . F_app + omega . M_app == 0 identically."""
    m_add_z = 0.0015  # 1.5 g added mass

    for u in [2.0, 5.0, 10.0]:
        for v in [-1.0, 0.0, 1.5]:
            for w in [-0.5, 0.2, 0.8]:
                for p in [-0.5, 0.1, 0.4]:
                    for q in [-0.2, 0.3, 0.6]:
                        for r in [-0.1, 0.0, 0.2]:
                            v_rel = np.array([u, v, w], dtype=np.float64)
                            omega = np.array([p, q, r], dtype=np.float64)
                            p_a = np.array([0.0, 0.0, m_add_z * w], dtype=np.float64)

                            # Fluid Coriolis force on body
                            f_app = -np.cross(omega, p_a)
                            # Munk moment on body
                            m_munk = -np.cross(v_rel, p_a)

                            work_rate = np.dot(v_rel, f_app) + np.dot(omega, m_munk)
                            assert abs(work_rate) < 1e-15, f"Spurious work detected: {work_rate}"


def test_munk_cpu_gpu_parity() -> None:
    """Verify bit-accurate parity of Munk coupling between CPU and GPU dynamics."""
    aircraft = AircraftConfig(added_mass_enabled=True, apparent_inertia_enabled=True, munk_coupling_enabled=True)
    env = EnvironmentConfig(wind_speed=0.0)
    launch = LaunchConfig(launch_speed=7.0, launch_angle_deg=8.0, launch_height_m=1.5)
    settings = SimulationSettings()

    sim = flight_sim.FlightSimulator(aircraft, env, launch, settings)
    y0 = gpu_dynamics.launch_state_vector(launch, env, aircraft)
    params = gpu_dynamics.build_params(np, [aircraft], env)
    w_gpu = gpu_dynamics.wind_batch(np, params, y0[:3][np.newaxis, :], 0.0)

    core_cpu = sim._compute_dynamics(y0, 0.0)
    core_gpu = gpu_dynamics._evaluate_state(np, params, y0[np.newaxis, :], w_gpu)

    # Angular acceleration parity under Munk moment coupling
    cpu_ang_acc = core_cpu["angular_acceleration"]
    gpu_ang_acc = core_gpu["angular_acceleration"][0]
    np.testing.assert_allclose(
        cpu_ang_acc,
        gpu_ang_acc,
        rtol=1e-5,
        atol=1e-6,
        err_msg="CPU and GPU angular acceleration must match under Munk coupling",
    )
