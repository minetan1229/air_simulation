"""Independent physical laws, conservation principles, and coordinate transformation verification.

Resolves GitHub Issue #23:
[Testing] 物理法則・保存則・座標変換の独立した検証テストを追加する

This module validates:
1. Relative wind drag opposition and strictly non-positive work on kinetic energy.
2. Quiescent/zero conditions (zero velocity, zero flow, zero lift/drag).
3. Coordinate transformation round-trip, SO(3) orthogonality, and right-hand convention.
4. Energy balance with explicit mechanical work accounting (gravity, aero drag).
5. Analytic projectile trajectory comparison with clear error bounds.
"""

from __future__ import annotations

import math
import numpy as np
import pytest

from physics import aero_domain, flight_sim
from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    SimulationSettings,
    StateVector,
    derived_properties,
    deg2rad,
    rad2deg,
)
from physics.geometry_utils import (
    body_to_world,
    world_to_body,
    euler_to_matrix,
    matrix_to_euler,
    quaternion_from_euler,
    quaternion_to_matrix,
    matrix_to_quaternion,
    quaternion_normalize,
    euler_zyx_from_quaternion,
    wind_force_to_body,
    vec3,
)


# ==============================================================================
# 1. 抵抗力の向きと仕事の符号 (Drag Opposition & Non-Positive Work)
# ==============================================================================

@pytest.mark.parametrize(
    "vel_body",
    [
        (10.0, 0.0, 0.0),       # Pure forward flow
        (15.0, 5.0, 0.0),       # Forward + sideslip
        (12.0, 0.0, 4.0),       # Forward + pitch down
        (8.0, -3.0, 2.0),       # General oblique flow
        (-10.0, 0.0, 0.0),      # Reversed flow
        (0.0, 10.0, 0.0),       # Pure lateral flow
        (0.0, 0.0, 10.0),       # Pure vertical plunge
    ],
)
def test_drag_force_directly_opposes_relative_wind_vector(vel_body):
    """Verifies that aerodynamic drag opposes velocity and produces non-positive power."""
    v = np.array(vel_body, dtype=np.float64)
    v_mag = np.linalg.norm(v)
    drag_mag = 5.0  # 5 Newtons of drag

    # wind_force_to_body(v_body, drag_n, side_n, lift_n)
    drag_vec = wind_force_to_body(v, drag_mag, 0.0, 0.0)

    # 1. Drag vector must oppose velocity: drag_vec must be anti-parallel to v
    # Normalized dot product should be -1.0
    dot_prod = np.dot(drag_vec, v)
    cos_theta = dot_prod / (np.linalg.norm(drag_vec) * v_mag)
    assert cos_theta == pytest.approx(-1.0, rel=1e-12)

    # 2. Magnitude of drag vector equals drag_mag
    assert np.linalg.norm(drag_vec) == pytest.approx(drag_mag, rel=1e-12)

    # 3. Work rate / instantaneous power P = F_drag · v must be strictly non-positive
    # Specifically, P = - drag_mag * |v| <= 0
    power = dot_prod
    assert power <= 0.0
    assert power == pytest.approx(-drag_mag * v_mag, rel=1e-12)


def test_lift_and_side_forces_perform_zero_work():
    """By definition of wind axes, lift and side forces are perpendicular to relative wind."""
    rng = np.random.default_rng(42)
    for _ in range(20):
        v = rng.uniform(-20.0, 20.0, size=3)
        if np.linalg.norm(v) < 1e-3:
            continue
        lift_vec = wind_force_to_body(v, 0.0, 0.0, 7.5)
        side_vec = wind_force_to_body(v, 0.0, 4.2, 0.0)

        # Orthogonal to velocity vector -> instantaneous work rate is zero
        assert np.dot(lift_vec, v) == pytest.approx(0.0, abs=1e-12)
        assert np.dot(side_vec, v) == pytest.approx(0.0, abs=1e-12)
        # Cross product between lift and side: both are in the orthogonal plane
        assert np.linalg.norm(lift_vec) == pytest.approx(7.5, rel=1e-12)
        assert np.linalg.norm(side_vec) == pytest.approx(4.2, rel=1e-12)


# ==============================================================================
# 2. 単純化・ゼロ条件の境界検査 (Quiescent and Zero Limits)
# ==============================================================================

def test_zero_relative_velocity_produces_zero_aero_forces():
    """At zero airspeed, all aerodynamic forces and moments must be strictly zero."""
    airframe = AircraftConfig()
    env = EnvironmentConfig()

    probe = flight_sim.aero_forces(
        airframe,
        env,
        speed_mps=0.0,
        alpha_deg=0.0,
        beta_deg=0.0,
        mach=0.0,
        reynolds=0.0,
        ground_height_m=1.0,
        angular_rate=(0.0, 0.0, 0.0),
    )
    assert probe.lift_n == 0.0
    assert probe.drag_n == 0.0
    assert probe.side_n == 0.0
    assert probe.pitching_moment_nm == 0.0
    assert probe.rolling_moment_nm == 0.0
    assert probe.yawing_moment_nm == 0.0

    # Also wind_force_to_body with zero velocity
    f_zero = wind_force_to_body((0.0, 0.0, 0.0), 10.0, 5.0, 2.0)
    assert np.allclose(f_zero, 0.0, atol=1e-15)


def test_calm_wind_zero_ambient_speed():
    """Calm environment produces zero ambient wind everywhere."""
    env = EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0, thermal_strength=0.0)
    for pos in [(0, 0, 0), (10, 20, -100), (-50, 30, -5)]:
        w = flight_sim.wind_vector(pos, env, time_s=0.5)
        assert np.allclose(w, 0.0, atol=1e-15)


# ==============================================================================
# 3. 座標変換の往復、直交性、符号規約 (Coordinate Transformations & SO(3))
# ==============================================================================

@pytest.mark.parametrize(
    "roll_deg, pitch_deg, yaw_deg",
    [
        (0.0, 0.0, 0.0),
        (25.0, 0.0, 0.0),
        (0.0, 30.0, 0.0),
        (0.0, 0.0, 45.0),
        (15.0, -20.0, 60.0),
        (-35.0, 40.0, -120.0),
        (180.0, 0.0, 0.0),
        (0.0, 85.0, 0.0),  # Near gimbal lock
    ],
)
def test_coordinate_transforms_orthogonality_and_roundtrip(roll_deg, pitch_deg, yaw_deg):
    """Tests SO(3) properties: R^T R = I, det(R) = +1, and round-trip transforms."""
    # 1. Quaternion from Euler
    q = quaternion_from_euler(roll_deg, pitch_deg, yaw_deg)
    assert np.linalg.norm(q) == pytest.approx(1.0, rel=1e-12)

    # 2. Direction cosine matrix R (maps body -> world)
    R = quaternion_to_matrix(q)

    # Orthogonality: R^T @ R == I
    I = np.eye(3)
    assert np.allclose(R.T @ R, I, atol=1e-12)
    assert np.allclose(R @ R.T, I, atol=1e-12)

    # Proper rotation (Right-handed, det = +1)
    assert np.linalg.det(R) == pytest.approx(1.0, rel=1e-12)

    # 3. Body <-> World vector roundtrip
    test_vecs = [
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
        np.array([3.4, -2.1, 7.8]),
    ]
    for v_b in test_vecs:
        v_w = body_to_world(q, v_b)
        v_b_rec = world_to_body(q, v_w)
        assert np.allclose(v_b, v_b_rec, atol=1e-12)
        # Vector length is invariant under rotation
        assert np.linalg.norm(v_w) == pytest.approx(np.linalg.norm(v_b), rel=1e-12)

    # 4. Matrix to quaternion roundtrip
    q_rec = matrix_to_quaternion(R)
    # Note: q and -q represent the identical SO(3) rotation
    dot_q = abs(np.dot(q, q_rec))
    assert dot_q == pytest.approx(1.0, rel=1e-12)

    # 5. Euler roundtrip (avoiding near gimbal lock where yaw/roll are ambiguous)
    if abs(pitch_deg) < 80.0:
        roll_rec, pitch_rec, yaw_rec = euler_zyx_from_quaternion(q)
        assert pitch_rec == pytest.approx(pitch_deg, abs=1e-6)
        # Roll and yaw are wrapped modulo 360
        diff_roll = (roll_rec - roll_deg + 180) % 360 - 180
        diff_yaw = (yaw_rec - yaw_deg + 180) % 360 - 180
        assert diff_roll == pytest.approx(0.0, abs=1e-6)
        assert diff_yaw == pytest.approx(0.0, abs=1e-6)


# ==============================================================================
# 4. エネルギー収支と仕事の整合性 (Energy Balance & Work Done by Drag)
# ==============================================================================

def test_energy_balance_in_draggy_fall():
    """In a vertical fall with drag, Delta(E_mech) must match the integral of drag work.

    Conservation of Energy with non-conservative forces:
        E_mech(t1) - E_mech(t0) = W_ext = integral(F_drag · v dt) <= 0
    """
    env = EnvironmentConfig(
        gravity=9.80665,
        drag_scale=1.0,
        wind_speed=0.0,
        gust_intensity=0.0,
        thermal_strength=0.0,
        turbulence_scale=0.0,
    )
    aircraft = AircraftConfig(
        mass_kg=0.010,
        wing_area_m2=0.025,
        wingspan_m=0.20,
        cL0=0.0,
        cd0=0.05,
    )
    # Vertical drop from 20m high (z = -20m in NED)
    launch = LaunchConfig(launch_speed=0.0, launch_angle_deg=-90.0, launch_height_m=20.0)
    settings = SimulationSettings(
        dt_max=0.002,
        dt_min=0.001,
        collision_enabled=False,
        record_interval_s=0.002,
        duration_s=0.5,
    )
    sim = flight_sim.FlightSimulator(aircraft, env, launch, settings)
    m = aircraft.mass_kg
    g = env.gravity

    initial_state = sim.state
    # Altitude h = -z
    h0 = -initial_state.position_m[2]
    v0 = np.linalg.norm(initial_state.velocity_mps)
    E0 = 0.5 * m * (v0 ** 2) + m * g * h0

    sim.simulate(duration_s=0.4, record=True)

    final_state = sim.state
    h1 = -final_state.position_m[2]
    v1 = np.linalg.norm(final_state.velocity_mps)
    E1 = 0.5 * m * (v1 ** 2) + m * g * h1

    # Mechanical energy must strictly decrease due to drag dissipation
    assert E1 < E0

    # Integrate F_drag · v dt along recorded history using the actual aerodynamic force
    history = sim.history()
    work_drag = 0.0
    for i in range(1, len(history)):
        dt = history[i].time_s - history[i - 1].time_s
        s = history[i]
        probe = flight_sim.aero_forces(
            aircraft, env, s.speed_mps, s.alpha_deg, s.beta_deg, s.mach, s.reynolds, -s.z_m
        )
        # Instantaneous power = - F_drag * speed
        work_drag += -probe.drag_n * s.speed_mps * dt

    delta_E = E1 - E0
    assert delta_E < 0.0
    assert work_drag < 0.0
    rel_error = abs(delta_E - work_drag) / max(abs(delta_E), 1e-6)
    # Numerical integration error between RK4 state and post-facto trapezoid of power is ~1.5%
    assert rel_error < 0.03, f"Energy dissipation {delta_E} vs drag work {work_drag}"


# ==============================================================================
# 5. 放物運動の解析解との比較 (Analytic Projectile Comparison)
# ==============================================================================

@pytest.mark.parametrize("launch_angle_deg", [15.0, 45.0, 75.0])
def test_vacuum_ballistic_flight_matches_exact_parabola(launch_angle_deg):
    """Without aerodynamic forces (vacuum / quiescent airframe), 6-DoF trajectory must follow Newtonian parabola:

        x(t) = v0 * cos(theta) * t
        y(t) = 0
        z(t) = z0 - (v0 * sin(theta) * t - 0.5 * g * t^2)
        v_x(t) = v0 * cos(theta)
        v_z(t) = -(v0 * sin(theta) - g * t)
    """
    quiescent_params = dict(
        cd0=0.0,
        cL0=0.0,
        cLalpha_per_rad=1.0e-9,
        cl_max=0.0,
        post_stall_slope=0.0,
        stall_alpha_deg=89.0,
        elevon_deg=0.0,
        cy_beta=0.0,
        cl_beta=0.0,
        cn_beta=0.0,
        cm0=0.0,
        cm_alpha=0.0,
        cm_q=0.0,
        cm_roll_rate=0.0,
        cm_pitch_rate=0.0,
        cm_yaw_rate=0.0,
        cl_roll_rate=0.0,
        cl_pitch_rate=0.0,
        cl_yaw_rate=0.0,
        cn_roll_rate=0.0,
        cn_pitch_rate=0.0,
        cn_yaw_rate=0.0,
        paper_stiffness=0.0,
        cg_offset_x=0.0,
        wing_area_m2=0.0150,
        mass_kg=0.0050,
    )
    aircraft = AircraftConfig(**quiescent_params)
    env = EnvironmentConfig(
        gravity=9.80665,
        drag_scale=0.0,
        lift_scale=0.0,
        wind_speed=0.0,
        gust_intensity=0.0,
        thermal_strength=0.0,
        turbulence_scale=0.0,
    )
    v0 = 15.0
    theta_rad = math.radians(launch_angle_deg)
    h0 = 5.0

    launch = LaunchConfig(launch_speed=v0, launch_angle_deg=launch_angle_deg, launch_height_m=h0)
    settings = SimulationSettings(
        dt_max=0.005,
        dt_min=0.001,
        collision_enabled=False,
        duration_s=1.0,
    )
    sim = flight_sim.FlightSimulator(aircraft, env, launch, settings)

    t_eval = 0.8  # Evaluate at t = 0.8 s
    sim.simulate(duration_s=t_eval, record=False)

    g = env.gravity
    expected_x = v0 * math.cos(theta_rad) * t_eval
    expected_h = h0 + v0 * math.sin(theta_rad) * t_eval - 0.5 * g * (t_eval ** 2)
    expected_vx = v0 * math.cos(theta_rad)
    expected_vz = -(v0 * math.sin(theta_rad) - g * t_eval)  # NED down is +z

    state = sim.state
    actual_x = state.position_m[0]
    actual_h = -state.position_m[2]
    actual_vx = state.velocity_mps[0]
    actual_vz = state.velocity_mps[2]

    # Verify positions and velocities with high precision
    assert actual_x == pytest.approx(expected_x, rel=1e-4)
    assert actual_h == pytest.approx(expected_h, rel=1e-4)
    assert actual_vx == pytest.approx(expected_vx, rel=1e-4)
    assert actual_vz == pytest.approx(expected_vz, rel=1e-4)
    assert state.position_m[1] == pytest.approx(0.0, abs=1e-9)
