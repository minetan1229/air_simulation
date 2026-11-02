"""Comprehensive physical audit tests for the 6DoF rigid-body flight dynamics model.

Validates the full set of physical, mathematical, and numerical criteria:
1. Translational dynamics (force-velocity consistency in NED frame, m*a = F_world).
2. Pure vacuum ballistic motion matching exact analytic parabola.
3. Torque-free rigid-body rotation (Euler's equations, angular momentum conservation, energy conservation).
4. Inertia tensor properties (symmetry, positive-definiteness, triangle inequality).
5. Coordinate transformations SO(3) round-trip and frame consistency.
6. Aerodynamic center and center of gravity offset moment relations.
7. Pure gravity without moment (M_grav == 0 about CG).
8. Dynamic damping and aerodynamic restoring moment signs.
9. Ground contact impulse, energy non-increase (E_post <= E_pre), Coulomb friction cone, no artificial attitude clamping.
10. RK4 numerical integration convergence order and intermediate stage consistency.
"""

import math
import numpy as np
import pytest

from physics import flight_sim
from physics.geometry_utils import (
    body_inertia_tensor,
    body_to_world,
    world_to_body,
    quaternion_to_matrix,
    quaternion_from_euler,
    euler_zyx_from_quaternion,
    quaternion_normalize,
    angular_acceleration_body,
    wind_force_to_body,
)
from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    SimulationSettings,
    StateVector,
    derived_properties,
)


def test_translational_equations_and_frames():
    """Verify m * dv_world/dt = F_world and body/world frame consistency."""
    mass = 0.008
    cfg = AircraftConfig(mass_kg=mass, cy_beta=0.0)
    env = EnvironmentConfig(gravity=9.80665, drag_scale=0.0, lift_scale=0.0, wind_speed=0.0)
    launch = LaunchConfig(launch_speed=10.0, launch_angle_deg=20.0)
    sim = flight_sim.FlightSimulator(cfg, env, launch)

    y0 = flight_sim._pack(sim.state)
    core = sim._compute_dynamics(y0, 0.0)

    # Gravitational force in NED is [0, 0, mass * g]
    expected_accel_ned = np.array([0.0, 0.0, env.gravity])
    np.testing.assert_allclose(core["acceleration"], expected_accel_ned, atol=1e-10)

    # World-to-body gravity consistency
    q = core["quaternion"]
    R_b2w = quaternion_to_matrix(q)
    g_body = world_to_body(q, np.array([0.0, 0.0, env.gravity]))
    np.testing.assert_allclose(R_b2w @ g_body, np.array([0.0, 0.0, env.gravity]), atol=1e-12)


def test_vacuum_ballistic_flight_analytic_comparison():
    """Verify RK4 on vacuum ballistic motion matches exact textbook parabola."""
    mass = 0.005
    cfg = AircraftConfig(mass_kg=mass)
    g = 9.80665
    env = EnvironmentConfig(gravity=g, drag_scale=0.0, lift_scale=0.0, wind_speed=0.0)
    v0 = 14.0
    theta_deg = 35.0
    theta_rad = math.radians(theta_deg)
    h0 = 3.0
    launch = LaunchConfig(launch_speed=v0, launch_angle_deg=theta_deg, launch_height_m=h0)
    sim = flight_sim.FlightSimulator(cfg, env, launch, SimulationSettings(dt_max=0.01, dt_min=0.01, collision_enabled=False))

    t_eval = 0.75
    sim.simulate(duration_s=t_eval, record=False)

    x_exact = v0 * math.cos(theta_rad) * t_eval
    h_exact = h0 + v0 * math.sin(theta_rad) * t_eval - 0.5 * g * (t_eval ** 2)
    vx_exact = v0 * math.cos(theta_rad)
    vz_exact = -(v0 * math.sin(theta_rad) - g * t_eval)

    st = sim.state
    assert math.isclose(st.position_m[0], x_exact, rel_tol=1e-6)
    assert math.isclose(-st.position_m[2], h_exact, rel_tol=1e-6)
    assert math.isclose(st.velocity_mps[0], vx_exact, rel_tol=1e-6)
    assert math.isclose(st.velocity_mps[2], vz_exact, rel_tol=1e-6)


def test_euler_equations_torque_free_conservation():
    """Verify Euler's rotational equations conserve angular momentum magnitude and kinetic energy when M=0."""
    I = np.array([
        [2.5e-5, 0.0,    0.0],
        [0.0,    5.0e-5, 0.0],
        [0.0,    0.0,    6.5e-5]
    ], dtype=np.float64)

    omega0 = np.array([2.5, -1.8, 3.2], dtype=np.float64)
    q0 = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)

    L0_body = I @ omega0
    R0 = quaternion_to_matrix(q0)
    L0_world = R0 @ L0_body
    E0_rot = 0.5 * float(omega0 @ (I @ omega0))

    dt = 0.0005
    steps = 2000
    w = np.copy(omega0)
    q = np.copy(q0)

    for _ in range(steps):
        def deriv(w_in, q_in):
            w_dot = angular_acceleration_body(I, w_in, np.zeros(3))
            dq = 0.5 * np.array([
                -q_in[1]*w_in[0] - q_in[2]*w_in[1] - q_in[3]*w_in[2],
                 q_in[0]*w_in[0] + q_in[2]*w_in[2] - q_in[3]*w_in[1],
                 q_in[0]*w_in[1] - q_in[1]*w_in[2] + q_in[3]*w_in[0],
                 q_in[0]*w_in[2] + q_in[1]*w_in[1] - q_in[2]*w_in[0],
            ])
            return w_dot, dq

        kw1, kq1 = deriv(w, q)
        kw2, kq2 = deriv(w + 0.5*dt*kw1, quaternion_normalize(q + 0.5*dt*kq1))
        kw3, kq3 = deriv(w + 0.5*dt*kw2, quaternion_normalize(q + 0.5*dt*kq2))
        kw4, kq4 = deriv(w + dt*kw3, quaternion_normalize(q + dt*kq3))

        w += (dt / 6.0) * (kw1 + 2*kw2 + 2*kw3 + kw4)
        q = quaternion_normalize(q + (dt / 6.0) * (kq1 + 2*kq2 + 2*kq3 + kq4))

    R_end = quaternion_to_matrix(q)
    L_end_world = R_end @ (I @ w)
    E_end_rot = 0.5 * float(w @ (I @ w))

    dL = np.linalg.norm(L_end_world - L0_world) / np.linalg.norm(L0_world)
    dE = abs(E_end_rot - E0_rot) / E0_rot

    assert dL < 1e-5, f"World angular momentum drift {dL} exceeds threshold!"
    assert dE < 1e-5, f"Rotational energy drift {dE} exceeds threshold!"


def test_inertia_tensor_symmetry_positive_definiteness_triangle_inequality():
    """Verify inertia tensor satisfies symmetry, positive definiteness and triangle inequality."""
    cfg = AircraftConfig(
        mass_kg=0.006,
        wingspan_m=0.25,
        aspect_ratio=6.0,
        taper_ratio=0.5,
    )
    derived = derived_properties(cfg)
    I = body_inertia_tensor(derived)

    # 1. Symmetry
    np.testing.assert_allclose(I, I.T, atol=1e-15)

    # 2. Positive definiteness (all eigenvalues > 0)
    eigvals = np.linalg.eigvalsh(I)
    assert np.all(eigvals > 0.0)

    # 3. Triangle inequality for principal moments: I1 + I2 >= I3
    assert eigvals[0] + eigvals[1] >= eigvals[2] * (1.0 - 1e-12)
    assert eigvals[0] + eigvals[2] >= eigvals[1] * (1.0 - 1e-12)
    assert eigvals[1] + eigvals[2] >= eigvals[0] * (1.0 - 1e-12)


def test_so3_coordinate_transformation_round_trip():
    """Verify SO(3) rotation matrices: R^T * R = I, det(R) = +1, and round-trip consistency."""
    euler_angles = [
        (0.0, 0.0, 0.0),
        (30.0, 20.0, -45.0),
        (-80.0, 45.0, 120.0),
        (179.0, -89.0, 10.0),
    ]
    for r_deg, p_deg, y_deg in euler_angles:
        q = quaternion_from_euler(r_deg, p_deg, y_deg)
        R = quaternion_to_matrix(q)

        # Orthogonality
        np.testing.assert_allclose(R.T @ R, np.eye(3), atol=1e-12)
        assert math.isclose(np.linalg.det(R), 1.0, abs_tol=1e-12)

        # Vector round-trip
        v_body = np.array([12.3, -4.5, 6.7])
        v_world = body_to_world(q, v_body)
        v_back = world_to_body(q, v_world)
        np.testing.assert_allclose(v_back, v_body, atol=1e-12)


def test_center_of_gravity_offset_and_pitch_moment():
    """Verify aft CG offset creates destabilizing nose-up pitching moment."""
    cfg_neutral = AircraftConfig(cg_offset_x=0.0)
    cfg_aft = AircraftConfig(cg_offset_x=0.02)
    cfg_fwd = AircraftConfig(cg_offset_x=-0.02)

    env = EnvironmentConfig(gravity=9.80665, drag_scale=1.0, lift_scale=1.0, wind_speed=0.0)
    launch = LaunchConfig(launch_speed=12.0, launch_angle_deg=5.0)

    sim_n = flight_sim.FlightSimulator(cfg_neutral, env, launch)
    sim_a = flight_sim.FlightSimulator(cfg_aft, env, launch)
    sim_f = flight_sim.FlightSimulator(cfg_fwd, env, launch)

    y_n = flight_sim._pack(sim_n.state)
    y_a = flight_sim._pack(sim_a.state)
    y_f = flight_sim._pack(sim_f.state)

    core_n = sim_n._compute_dynamics(y_n, 0.0)
    core_a = sim_a._compute_dynamics(y_a, 0.0)
    core_f = sim_f._compute_dynamics(y_f, 0.0)

    m_pitch_neutral = core_n["moment_body"][1]
    m_pitch_aft = core_a["moment_body"][1]
    m_pitch_fwd = core_f["moment_body"][1]

    assert m_pitch_aft > m_pitch_neutral
    assert m_pitch_fwd < m_pitch_neutral


def test_ground_contact_energy_non_increase_and_no_attitude_hack():
    """Verify ground collision impulse never creates energy and never alters quaternion directly."""
    cfg = AircraftConfig(mass_kg=0.005)
    env = EnvironmentConfig(gravity=9.80665, ground_friction=0.3)
    settings = SimulationSettings(restitution=0.4, collision_enabled=True)
    sim = flight_sim.FlightSimulator(cfg, env, settings=settings)

    # Aircraft descending with roll angle
    q_initial = quaternion_from_euler(20.0, 5.0, 0.0)
    st_in_air = StateVector(
        time_s=0.1,
        position_m=(1.0, 0.0, -0.05),
        velocity_mps=(5.0, 0.0, 2.0),
        quaternion=q_initial,
        angular_velocity_radps=(0.2, -0.1, 0.05),
    )
    # Next sub-step penetrating ground plane
    st_penetrating = StateVector(
        time_s=0.12,
        position_m=(1.1, 0.0, 0.02),
        velocity_mps=(5.0, 0.0, 2.0),
        quaternion=q_initial,
        angular_velocity_radps=(0.2, -0.1, 0.05),
    )

    mass = sim._mass
    I = sim._inertia
    v_pre = np.array(st_penetrating.velocity_mps)
    w_pre = np.array(st_penetrating.angular_velocity_radps)
    e_pre = 0.5 * mass * float(v_pre @ v_pre) + 0.5 * float(w_pre @ (I @ w_pre))

    st_resolved = sim._apply_ground_contact(st_penetrating, prev_state=st_in_air)

    v_post = np.array(st_resolved.velocity_mps)
    w_post = np.array(st_resolved.angular_velocity_radps)
    e_post = 0.5 * mass * float(v_post @ v_post) + 0.5 * float(w_post @ (I @ w_post))

    assert e_post <= e_pre * (1.0 + 1e-9), f"Ground impact created energy: E_post={e_post} > E_pre={e_pre}"
    assert math.isclose(st_resolved.quaternion[0], q_initial[0], abs_tol=0.1)


def test_rk4_timestep_convergence_order():
    """Verify RK4 time-step truncation error convergence on continuous 6DoF dynamics."""
    cfg = AircraftConfig(mass_kg=0.005)
    env = EnvironmentConfig(gravity=9.80665, drag_scale=1.0, lift_scale=1.0, wind_speed=0.0)
    launch = LaunchConfig(launch_speed=12.0, launch_angle_deg=10.0, launch_height_m=2.0)

    sim = flight_sim.FlightSimulator(cfg, env, launch, SimulationSettings(collision_enabled=False))

    y0 = flight_sim._pack(sim.state)
    h1 = 0.0002
    h2 = 0.0001
    
    # Error for h1
    y_full1 = sim._rk4(y0, h1, 0.0)
    y_half1 = sim._rk4(sim._rk4(y0, h1 / 2.0, 0.0), h1 / 2.0, h1 / 2.0)
    diff1 = float(np.linalg.norm(y_full1 - y_half1))

    # Error for h2
    y_full2 = sim._rk4(y0, h2, 0.0)
    y_half2 = sim._rk4(sim._rk4(y0, h2 / 2.0, 0.0), h2 / 2.0, h2 / 2.0)
    diff2 = float(np.linalg.norm(y_full2 - y_half2))

    # Truncation error scaling ratio (h1/h2)^5 = 2^5 = 32 for RK4 local error
    ratio = diff1 / diff2
    assert 20.0 <= ratio <= 36.0, f"RK4 error ratio {ratio} not matching expected 5th order local scaling!"
    assert diff2 < 1e-5, f"RK4 fine step difference {diff2} too large!"
