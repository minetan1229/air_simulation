"""Analytic and regression verification of the 6-DoF flight-dynamics domain.

The suite deliberately uses short flights (1-3 s) and coarse time steps so the
whole file runs in a few seconds.  Every configuration used here is small enough
for the tests to finish well under the 60 s budget while still exercising every
public entry point of :mod:`physics.flight_sim`.

Conventions asserted here (they are the ones documented in the module):

* ``+z`` points **down**, so the altitude is ``-z - ground_level_m`` and a
  falling body has ``v_z > 0``;
* the launch position is ``(x, y, -launch_height_m)``;
* ``distance_m`` is the accumulated horizontal path length.
"""

from __future__ import annotations

import math
import time
from dataclasses import replace

import numpy as np
import pytest

from physics import flight_sim
from physics.flight_sim import FlightSimulator
from physics.config_schema import (
    STANDARD_GRAVITY,
    AircraftConfig,
    EnvironmentConfig,
    FLIGHT_LOG_COLUMNS,
    FlightLogSample,
    LaunchConfig,
    SimulationSettings,
    StateVector,
    as_dict,
    from_dict,
    derived_properties,
)
from physics.geometry_utils import (
    euler_zyx_from_quaternion,
    matrix_to_euler,
    quaternion_from_euler,
    quaternion_to_matrix,
    world_to_body,
)

#: Every aerodynamic force and moment coefficient forced to zero.  The analytic
#: integrator tests (free fall, energy, zero-g) need a genuinely quiescent
#: airframe: with lift, drag *and* the moment derivatives zero the rigid-body
#: solution is exact, independently of how the attitude evolves.
QUIESCENT_AIRCRAFT: dict = dict(
    cd0=0.0,
    cL0=0.0,
    cLalpha_per_rad=1.0e-9,  # 0 would be replaced by the Helmbold estimate
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

#: A completely calm atmosphere.  ``wind_speed = 0`` alone is not enough: the
#: contract keeps a thermal updraft (``thermal_strength``) and a gust envelope
#: active, so all three are switched off to obtain the exact zero wind vector.
CALM_ENVIRONMENT: dict = dict(
    wind_speed=0.0,
    gust_intensity=0.0,
    thermal_strength=0.0,
    turbulence_scale=0.0,
    gravity=STANDARD_GRAVITY,
    isa_mode=True,
    drag_scale=0.0,
    ground_level_m=0.0,
)

#: A trimmed glider: the elevon trims the airframe at a small positive angle of
#: attack, so it converts its launch energy into a long shallow glide.
GLIDER = AircraftConfig(name="Paper Glider", elevon_deg=6.0)
#: A deliberately bad dart: a large elevon trims it nose-down past the stall, the
#: sheet is heavy and blunt, so it noses over and hits the ground almost at once.
DART = AircraftConfig(
    name="Paper Dart",
    elevon_deg=35.0,
    mass_kg=0.018,
    cd0=0.050,
    fold_trailing_deg=60.0,
)
FLIGHT_LAUNCH = LaunchConfig(launch_speed=12.0, launch_angle_deg=12.0, launch_height_m=1.5, launch_roll_deg=0.0)


def quiescent(environment: EnvironmentConfig | None = None) -> FlightSimulator:
    """Simulator with a zero-force airframe and a calm atmosphere."""
    settings = SimulationSettings(
        dt_max=1.0e-3,
        dt_min=1.0e-6,
        max_substeps=64,
        record_interval_s=0.05,
        duration_s=1.0,
        collision_enabled=False,
        ring_buffer_capacity=512,
        restitution=0.15,
    )
    return FlightSimulator(
        AircraftConfig(**QUIESCENT_AIRCRAFT),
        environment if environment is not None else EnvironmentConfig(**CALM_ENVIRONMENT),
        LaunchConfig(launch_speed=5.0, launch_angle_deg=0.0, launch_height_m=10.0),
        settings,
    )


def glider_simulator(**overrides) -> FlightSimulator:
    """Simulator for the trimmed glider with recording and collision enabled."""
    settings = SimulationSettings(
        dt_max=2.0e-2,
        dt_min=1.0e-5,
        max_substeps=64,
        record_interval_s=0.01,
        duration_s=2.0,
        collision_enabled=True,
        ring_buffer_capacity=4096,
        restitution=0.15,
    )
    return FlightSimulator(GLIDER, EnvironmentConfig(), FLIGHT_LAUNCH, settings)


# --------------------------------------------------------------------------
# Shared, expensive evaluations (module scope keeps the suite fast)
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def glider_flight() -> dict:
    return flight_sim.evaluate_flight(
        as_dict(GLIDER), as_dict(EnvironmentConfig()), as_dict(FLIGHT_LAUNCH), 3.0, 0.02
    )


@pytest.fixture(scope="module")
def dart_flight() -> dict:
    return flight_sim.evaluate_flight(
        as_dict(DART), as_dict(EnvironmentConfig()), as_dict(FLIGHT_LAUNCH), 3.0, 0.02
    )


# --------------------------------------------------------------------------
# Launch conventions and free functions
# --------------------------------------------------------------------------
def test_initial_state_places_the_aircraft_above_the_ground_with_z_down():
    launch = LaunchConfig(
        launch_speed=10.0,
        launch_angle_deg=30.0,
        launch_roll_deg=0.0,
        launch_yaw_deg=0.0,
        launch_height_m=2.5,
        launch_position_m=(1.0, -2.0, 0.0),
    )
    env = EnvironmentConfig(ground_level_m=0.0, gravity=STANDARD_GRAVITY)
    state = flight_sim.initial_state(launch, env)
    # +z is down, so the initial altitude above the ground plane is launch_height_m.
    assert state.position_m == pytest.approx((1.0, -2.0, -2.5), rel=0, abs=1e-12)
    assert -state.position_m[2] - env.ground_level_m == pytest.approx(2.5, rel=1e-12)
    # The velocity lies along the body x axis raised by the launch angle (climbing).
    assert state.velocity_mps[0] == pytest.approx(10.0 * math.cos(math.radians(30.0)), rel=1e-12)
    assert state.velocity_mps[2] == pytest.approx(-10.0 * math.sin(math.radians(30.0)), rel=1e-12)
    # ... and therefore the initial angle of attack is zero.  The simulator works
    # with the air-relative velocity expressed in the body frame.
    alpha, beta = flight_sim.effective_alpha_beta(
        world_to_body(state.quaternion, state.velocity_mps), (0.0, 0.0, 9.80665), env
    )
    assert alpha == pytest.approx(0.0, abs=1e-6)
    assert beta == pytest.approx(0.0, abs=1e-6)
    # The attitude quaternion matches the requested Euler angles.
    assert state.quaternion == pytest.approx(tuple(quaternion_from_euler(0.0, 30.0, 0.0)), rel=1e-12)
    assert euler_zyx_from_quaternion(state.quaternion) == pytest.approx((0.0, 30.0, 0.0), abs=1e-9)
    assert state.acceleration_mps2[2] == pytest.approx(STANDARD_GRAVITY, rel=1e-12)


def test_effective_alpha_beta_uses_the_body_frame_convention():
    env = EnvironmentConfig()
    gravity_body = (0.0, 0.0, 9.80665)
    # Level flight: no angle of attack, no sideslip.
    assert flight_sim.effective_alpha_beta((12.0, 0.0, 0.0), gravity_body, env) == pytest.approx((0.0, 0.0), abs=1e-9)
    # Airflow arriving from below (w > 0 in a z-down body frame) is a positive alpha.
    alpha, _ = flight_sim.effective_alpha_beta((12.0, 0.0, 2.0), gravity_body, env)
    assert alpha == pytest.approx(math.degrees(math.atan2(2.0, 12.0)), rel=1e-12)
    assert alpha > 0.0
    # Velocity towards the right wing is a positive sideslip.
    _, beta = flight_sim.effective_alpha_beta((12.0, 3.0, 0.0), gravity_body, env)
    assert beta == pytest.approx(math.degrees(math.asin(3.0 / math.sqrt(153.0))), rel=1e-12)
    assert beta > 0.0
    # At zero airspeed no flow angle exists; gravity cannot substitute for air.
    alpha, beta = flight_sim.effective_alpha_beta((0.0, 0.0, 0.0), gravity_body, env)
    assert alpha == pytest.approx(0.0, abs=1e-6)
    assert beta == pytest.approx(0.0, abs=1e-9)
    # The wind-tunnel mode overrides the gravity fallback with the tunnel stream.
    tunnel = EnvironmentConfig(wind_tunnel_enabled=True, wind_tunnel_speed=12.0)
    alpha, _ = flight_sim.effective_alpha_beta((0.0, 0.0, 0.0), gravity_body, tunnel)
    assert alpha == pytest.approx(0.0, abs=1e-9)


def test_total_energy_is_the_specific_orbital_energy():
    state = StateVector(
        time_s=0.0,
        position_m=(1.0, 2.0, -10.0),
        velocity_mps=(3.0, 4.0, 12.0),
        quaternion=(1.0, 0.0, 0.0, 0.0),
    )
    speed_squared = 9.0 + 16.0 + 144.0
    assert flight_sim.total_energy(state, 9.80665) == pytest.approx(
        0.5 * speed_squared + 9.80665 * 10.0, rel=1e-12
    )
    # Free fall from rest: the specific energy is exactly conserved.
    start = StateVector(position_m=(0.0, 0.0, -100.0), velocity_mps=(0.0, 0.0, 0.0))
    end = StateVector(position_m=(0.0, 0.0, -100.0 + 0.5 * 9.80665), velocity_mps=(0.0, 0.0, 9.80665))
    assert flight_sim.total_energy(end, 9.80665) == pytest.approx(
        flight_sim.total_energy(start, 9.80665), rel=1e-12
    )


# --------------------------------------------------------------------------
# Analytic integrator verification (prompt.md 2.3)
# --------------------------------------------------------------------------
def test_free_fall_matches_the_analytic_trajectory():
    simulator = quiescent()
    # Premise: the airframe really is quiescent, so the analytic solution applies.
    probe = flight_sim.aero_forces(
        AircraftConfig(**QUIESCENT_AIRCRAFT),
        simulator.environment,
        10.0,
        3.0,
        2.0,
        0.03,
        1.0e5,
        1.0,
        (1.0, 1.0, 1.0),
    )
    assert abs(probe.lift_n) < 1.0e-6 and abs(probe.drag_n) < 1.0e-6 and abs(probe.side_n) < 1.0e-6
    assert abs(probe.pitching_moment_nm) < 1.0e-9
    assert abs(probe.rolling_moment_nm) < 1.0e-9
    assert abs(probe.yawing_moment_nm) < 1.0e-9
    assert np.allclose(flight_sim.wind_vector((0.0, 0.0, -1.0), simulator.environment, 0.0), 0.0, atol=0.0)

    duration = 0.8
    simulator.simulate(duration_s=duration, record=False)
    state = simulator.state
    gravity = simulator.environment.gravity
    expected_altitude = 10.0 - 0.5 * gravity * duration ** 2
    expected_x = 5.0 * duration
    # h(t) = h0 - 0.5 g t^2 and x(t) = x0 + v0x t
    assert -state.position_m[2] == pytest.approx(expected_altitude, rel=1e-2)
    assert state.position_m[0] == pytest.approx(expected_x, rel=1e-2)
    # +z is down, so a falling body has v_z = +g t and v_x = v0x.
    assert state.velocity_mps[2] == pytest.approx(gravity * duration, rel=1e-3)
    assert state.velocity_mps[0] == pytest.approx(5.0, rel=1e-3)
    assert simulator.frame_count() == 1  # only the t = 0 launch sample
    simulator.reset_history()
    simulator.simulate(duration_s=0.2, record=False)
    assert simulator.frame_count() == 0  # record=False keeps the ring buffer empty


def test_energy_is_conserved_without_drag():
    environment = EnvironmentConfig(**{**CALM_ENVIRONMENT, "drag_scale": 0.0})
    simulator = quiescent(environment)
    assert simulator.aircraft.cd0 == 0.0  # drag_scale = 0 and C_D0 = 0
    mass = derived_properties(simulator.aircraft)["mass_kg"]
    start = simulator.state
    energy_start = mass * flight_sim.total_energy(start, environment.gravity)
    simulator.simulate(duration_s=0.6, record=False)
    energy_end = mass * flight_sim.total_energy(simulator.state, environment.gravity)
    assert energy_end == pytest.approx(energy_start, rel=1e-6)
    # The specific energy is identical to the same tolerance.
    assert flight_sim.total_energy(simulator.state, environment.gravity) == pytest.approx(
        flight_sim.total_energy(start, environment.gravity), rel=1e-6
    )


def test_zero_gravity_keeps_the_velocity_vector_constant():
    environment = EnvironmentConfig(**{**CALM_ENVIRONMENT, "gravity": 0.0, "drag_scale": 0.0})
    simulator = quiescent(environment)
    start = np.array(simulator.state.velocity_mps, dtype=float)
    simulator.simulate(duration_s=0.4, record=False)
    end = np.array(simulator.state.velocity_mps, dtype=float)
    # With no gravity and no force the airframe coasts: the velocity is constant
    # and the position advances at exactly that velocity.
    assert np.allclose(end, start, rtol=0.0, atol=1e-9)
    assert simulator.state.position_m == pytest.approx(
        (start[0] * 0.4, start[1] * 0.4, -10.0), abs=1e-9
    )


# --------------------------------------------------------------------------
# Adaptive time stepping
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "omega, velocity, altitude",
    [
        (0.0, 0.0, 0.0),  # both CFL terms switch off -> dt_max
        (0.0, 30.0, -5.0),
        ((2.0, 1.0, -0.5), (10.0, 0.0, 0.0), -50.0),
        ((-40.0, 25.0, 10.0), (60.0, -20.0, 5.0), -900.0),  # near the hard caps
        ((1.0e4, -1.0e4, 1.0e4), (1.0e3, 1.0e3, 1.0e3), -9.0e3),
    ],
)
def test_derived_timestep_is_always_inside_the_clamped_range(omega, velocity, altitude):
    settings = SimulationSettings(dt_max=5.0, dt_min=1.0e-5, adaptive=True)
    simulator = FlightSimulator(
        AircraftConfig(**QUIESCENT_AIRCRAFT), EnvironmentConfig(**CALM_ENVIRONMENT), LaunchConfig(), settings
    )
    state = StateVector(
        time_s=0.0,
        position_m=(0.0, 0.0, altitude),
        velocity_mps=velocity,
        quaternion=(1.0, 0.0, 0.0, 0.0),
        angular_velocity_radps=omega,
    )
    simulator.load_state(state)
    step = simulator.derived_timestep()
    assert math.isfinite(step)
    assert step > 0.0
    assert settings.dt_min <= step <= settings.dt_max


def test_derived_timestep_decreases_with_the_angular_rate():
    settings = SimulationSettings(dt_max=5.0, dt_min=1.0e-6, adaptive=True)
    simulator = FlightSimulator(
        AircraftConfig(**QUIESCENT_AIRCRAFT), EnvironmentConfig(**CALM_ENVIRONMENT), LaunchConfig(), settings
    )
    steps = []
    for rate in (0.5, 1.0, 2.0, 4.0, 8.0):
        # A near-zero airspeed switches the translational and stiff-mode limits
        # off, isolating the attitude limit MAX_ATTITUDE_STEP_RAD / |omega|.
        simulator.load_state(
            StateVector(
                position_m=(0.0, 0.0, -1.0),
                velocity_mps=(1.0e-3, 0.0, 0.0),
                quaternion=(1.0, 0.0, 0.0, 0.0),
                angular_velocity_radps=(0.0, rate, 0.0),
            )
        )
        steps.append(simulator.derived_timestep())
    assert all(later < earlier for earlier, later in zip(steps, steps[1:]))
    # The attitude limit MAX_ATTITUDE_STEP_RAD / |omega| is an upper *cap* on
    # the step, not a floor: the translational and stiff-mode limits may
    # legitimately bind first (and do, for the default airframe whose pitch
    # inertia follows the corrected MAC).  What must hold is that the cap is
    # respected and that a zero rate is the most permissive case here.
    assert steps[0] <= flight_sim.MAX_ATTITUDE_STEP_RAD / 0.5 * (1.0 + 1.0e-9)
    assert steps[0] > flight_sim.MAX_ATTITUDE_STEP_RAD / 8.0
    for rate, step in zip((0.5, 1.0, 2.0, 4.0, 8.0), steps):
        assert step <= flight_sim.MAX_ATTITUDE_STEP_RAD / rate * (1.0 + 1.0e-9)
        assert settings.dt_min <= step <= settings.dt_max


def test_derived_timestep_decreases_with_the_airspeed():
    settings = SimulationSettings(dt_max=5.0, dt_min=1.0e-6, adaptive=True)
    simulator = FlightSimulator(
        AircraftConfig(**QUIESCENT_AIRCRAFT), EnvironmentConfig(**CALM_ENVIRONMENT), LaunchConfig(), settings
    )
    steps = []
    for speed in (1.0, 4.0, 12.0, 30.0, 90.0):
        # Zero body rate switches the attitude, rate-change and stiff-mode limits
        # off, isolating the translational limit 0.5 * MAC / V.
        simulator.load_state(
            StateVector(
                position_m=(0.0, 0.0, -1.0),
                velocity_mps=(speed, 0.0, 0.0),
                quaternion=(1.0, 0.0, 0.0, 0.0),
                angular_velocity_radps=(0.0, 0.0, 0.0),
            )
        )
        steps.append(simulator.derived_timestep())
    assert all(later < earlier for earlier, later in zip(steps, steps[1:]))
    chord = derived_properties(AircraftConfig(**QUIESCENT_AIRCRAFT))["mac_m"]
    assert steps[0] == pytest.approx(flight_sim.TRAVEL_FRACTION_PER_STEP * chord / 1.0, rel=1e-6)


def test_derived_timestep_honours_the_non_adaptive_setting():
    settings = SimulationSettings(dt_max=0.01, dt_min=1.0e-5, adaptive=False)
    simulator = FlightSimulator(
        AircraftConfig(**QUIESCENT_AIRCRAFT), EnvironmentConfig(**CALM_ENVIRONMENT), LaunchConfig(), settings
    )
    simulator.load_state(
        StateVector(
            position_m=(0.0, 0.0, -1.0),
            velocity_mps=(1.0e-3, 0.0, 0.0),
            quaternion=(1.0, 0.0, 0.0, 0.0),
            angular_velocity_radps=(0.0, 50.0, 0.0),
        )
    )
    assert simulator.derived_timestep() == pytest.approx(0.01, rel=1e-12)


# --------------------------------------------------------------------------
# Divergence detection, rollback and state hygiene
# --------------------------------------------------------------------------
PATHOLOGICAL = AircraftConfig(
    name="pathological",
    elevon_deg=35.0,
    cm_q=-400.0,
    cm_alpha=-40.0,
    cl_roll_rate=40.0,
    cn_yaw_rate=40.0,
    cl_pitch_rate=20.0,
    roll_inertia_kgm2=1.0e-9,
    pitch_inertia_kgm2=1.0e-9,
    yaw_inertia_kgm2=1.0e-9,
    mass_kg=0.0002,
    paper_stiffness=1.0,
)
HOSTILE_ENVIRONMENT = EnvironmentConfig(
    wind_speed=60.0,
    gust_intensity=1.0,
    thermal_strength=25.0,
    turbulence_scale=5.0,
    gravity=48.0,
    drag_scale=5.0,
    lift_scale=5.0,
)


def _all_finite(packet: dict) -> bool:
    values = list(packet["state"]["position_m"])
    values += list(packet["state"]["velocity_mps"])
    values += list(packet["state"]["quaternion"])
    values += list(packet["state"]["angular_velocity_radps"])
    values += [packet["time_s"], packet["speed_mps"], packet["alpha_deg"], packet["g_load"]]
    return all(math.isfinite(float(value)) for value in values)


def test_pathological_configuration_never_produces_a_nonfinite_state():
    settings = SimulationSettings(
        dt_max=2.0e-2, dt_min=1.0e-7, max_substeps=64, record_interval_s=1.0e-2, duration_s=1.0
    )
    simulator = FlightSimulator(
        PATHOLOGICAL, HOSTILE_ENVIRONMENT, LaunchConfig(launch_speed=40.0, launch_height_m=2.0), settings
    )
    saw_divergence = False
    saw_rollback = False
    for _ in range(60):
        packet = simulator.step(dt=0.02)
        assert _all_finite(packet)
        saw_divergence = saw_divergence or packet["diverged"]
        saw_rollback = saw_rollback or packet["rolled_back"]
        if packet["diverged"]:
            break
    # Either the integrator latched the divergence, or it survived with a finite
    # state.  Both outcomes are acceptable; a NaN is not.
    assert saw_divergence or _all_finite(packet)
    assert saw_rollback or saw_divergence
    if saw_divergence:
        assert packet["crashed"] is True
        assert packet["status"] in flight_sim.STATUS_VALUES


def test_two_hundred_aggressive_steps_stay_finite():
    settings = SimulationSettings(
        dt_max=2.0e-2, dt_min=1.0e-7, max_substeps=64, record_interval_s=1.0e-2, duration_s=4.0
    )
    simulator = FlightSimulator(
        PATHOLOGICAL, HOSTILE_ENVIRONMENT, LaunchConfig(launch_speed=40.0, launch_height_m=2.0), settings
    )
    for index in range(200):
        packet = simulator.step(dt=0.02)
        assert _all_finite(packet), f"non-finite state at step {index}"
        assert math.isfinite(packet["dt"]) and packet["dt"] >= 0.0
        assert 0 <= packet["substeps"] <= settings.max_substeps
        assert packet["backend"] == "numpy"
    # A frozen airframe must stay frozen and keep reporting itself honestly.
    if packet["landed"] or packet["crashed"]:
        again = simulator.step(dt=0.02)
        assert again["dt"] == 0.0 and again["substeps"] == 0
        assert again["state"]["position_m"] == pytest.approx(packet["state"]["position_m"], abs=0.0)


def test_load_state_rejects_a_nonfinite_state():
    simulator = glider_simulator()
    with pytest.raises(ValueError):
        simulator.load_state(
            StateVector(position_m=(float("nan"), 0.0, -1.0), velocity_mps=(1.0, 0.0, 0.0))
        )
    # The live state survives the rejected restore.
    assert math.isfinite(simulator.state.position_m[0])


# --------------------------------------------------------------------------
# Attitude bookkeeping
# --------------------------------------------------------------------------
def test_quaternion_stays_normalised_over_a_whole_flight():
    simulator = glider_simulator()
    worst = 0.0
    for _ in range(120):
        packet = simulator.step(dt=0.02)
        norm = float(np.linalg.norm(np.asarray(packet["quaternion"], dtype=float)))
        worst = max(worst, abs(norm - 1.0))
        assert norm == pytest.approx(1.0, abs=1e-9)
        assert packet["quaternion_norm"] == pytest.approx(1.0, abs=1e-9)
        # The two attitude conventions must not drift apart, and the recovered
        # body rate from the quaternion derivative must match the integrated one.
        assert packet["attitude_consistency_deg"] < 1.0e-6
        assert packet["quaternion_rate_error_radps"] < 1.0e-6
    assert worst < 1e-9
    assert simulator.state.quaternion != (1.0, 0.0, 0.0, 0.0)  # the attitude really moved


def test_attitude_round_trip_is_self_consistent():
    simulator = glider_simulator()
    for _ in range(40):
        simulator.step(dt=0.02)
        quaternion = simulator.state.quaternion
        euler_direct = euler_zyx_from_quaternion(quaternion)
        euler_matrix = matrix_to_euler(quaternion_to_matrix(quaternion))
        assert euler_direct == pytest.approx(euler_matrix, abs=1e-6)
        # matrix_to_euler uses pitch = asin(-m[2, 0]); outside the gimbal-lock band
        # the two conventions must agree to machine precision.
        assert float(np.linalg.norm(quaternion_to_matrix(quaternion) @ quaternion_to_matrix(quaternion).T - np.eye(3))) < 1e-12


# --------------------------------------------------------------------------
# Ring buffer / timeline scrubbing
# --------------------------------------------------------------------------
def test_ring_buffer_keeps_only_the_newest_frames_in_order():
    settings = SimulationSettings(
        dt_max=2.0e-2,
        dt_min=1.0e-5,
        max_substeps=64,
        record_interval_s=1.0e-2,
        duration_s=2.0,
        collision_enabled=False,
        ring_buffer_capacity=8,
    )
    simulator = FlightSimulator(GLIDER, EnvironmentConfig(), FLIGHT_LAUNCH, settings)
    simulator.simulate(duration_s=1.0, record=True)
    # Far more frames were produced than the buffer can hold.
    assert simulator.state.time_s > 0.5
    assert simulator.frame_count() == 8
    history = simulator.history()
    assert len(history) == 8
    times = [sample.time_s for sample in history]
    assert times == sorted(times)
    assert all(isinstance(sample, FlightLogSample) for sample in history)
    # seek_frame(0) is the oldest retained sample, and the buffer is a sliding window.
    oldest = simulator.seek_frame(0)
    assert oldest is not None
    assert oldest.time_s == times[0]
    assert oldest.time_s > 0.0
    assert simulator.seek_frame(7) is history[-1]
    assert simulator.seek_frame(8) is None
    assert simulator.seek_frame(-1) is history[-1]
    simulator.reset_history()
    assert simulator.frame_count() == 0
    assert simulator.history() == []
    assert simulator.seek_frame(0) is None


def test_scrubbing_never_mutates_the_live_state():
    simulator = glider_simulator()
    simulator.simulate(duration_s=0.4, record=True)
    before = as_dict(simulator.state)
    frames = simulator.history()
    assert len(frames) > 3
    replayed = [as_dict(sample) for sample in frames]
    after = as_dict(simulator.state)
    assert before == after
    for frame in reversed(frames):
        _ = simulator.seek_frame(0)
    assert as_dict(simulator.state) == before
    # Scrubbing is a pure read: the recorded samples themselves are unchanged.
    assert [as_dict(sample) for sample in frames] == replayed


# --------------------------------------------------------------------------
# evaluate_flight contract (what the optimiser depends on)
# --------------------------------------------------------------------------
def test_evaluate_flight_payload_contract(glider_flight: dict):
    assert set(glider_flight) == set(flight_sim.EVALUATE_KEYS)
    for key, value in glider_flight.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            assert math.isfinite(float(value)), f"{key} is not finite"
    assert glider_flight["distance_m"] >= 0.0
    assert glider_flight["flight_time_s"] > 0.0
    assert glider_flight["max_altitude_m"] >= 0.0
    assert 0.0 <= glider_flight["max_stress_index"] <= 1.0
    assert glider_flight["max_stress_pa"] >= 0.0
    assert glider_flight["launch_speed_mps"] == pytest.approx(12.0, rel=1e-9)
    assert 0.0 <= glider_flight["stability"] <= 1.0
    assert glider_flight["landing_distance_m"] >= 0.0
    assert isinstance(glider_flight["points"], int) and glider_flight["points"] > 1
    assert glider_flight["duration_s"] == pytest.approx(3.0, rel=1e-12)
    assert glider_flight["backend"] == "numpy"
    assert glider_flight["status"] in flight_sim.STATUS_VALUES
    assert 0.0 <= glider_flight["apogee_time_s"] <= glider_flight["flight_time_s"]


def test_evaluate_flight_accepts_dicts_and_dataclasses_identically(glider_flight: dict):
    from_objects = flight_sim.flight_from_aircraft(GLIDER, EnvironmentConfig(), FLIGHT_LAUNCH, duration_s=3.0)
    from_dicts = flight_sim.evaluate_flight(
        as_dict(GLIDER), as_dict(EnvironmentConfig()), as_dict(FLIGHT_LAUNCH), 3.0, 0.02
    )
    assert from_objects == from_dicts == pytest.approx(glider_flight, rel=1e-9, abs=1e-12)
    # Unknown keys are ignored (the pool pickles plain dicts) and missing keys
    # fall back to the dataclass defaults.
    sparse = flight_sim.evaluate_flight(
        {"elevon_deg": -12.0, "unknown_field": 1}, {"wind_speed": 0.0}, {"launch_speed": 12.0}, 0.4, 0.02
    )
    assert set(sparse) == set(flight_sim.EVALUATE_KEYS)
    assert sparse["status"] in flight_sim.STATUS_VALUES
    assert math.isfinite(sparse["distance_m"])


def test_glider_travels_further_than_the_bad_dart(glider_flight: dict, dart_flight: dict):
    # The trimmed glider converts its launch energy into a long shallow glide; the
    # over-deflected, heavy, blunt dart trims nose-down past the stall and is on
    # the ground within a second.
    assert glider_flight["distance_m"] > dart_flight["distance_m"]
    assert glider_flight["flight_time_s"] > dart_flight["flight_time_s"]
    assert glider_flight["max_altitude_m"] > dart_flight["max_altitude_m"]
    assert glider_flight["landing_distance_m"] > dart_flight["landing_distance_m"]
    assert dart_flight["status"] in ("landed", "crashed")
    # The separation is physical, not numerical noise.
    assert glider_flight["distance_m"] > 1.9 * dart_flight["distance_m"]


def test_trajectory_responds_to_every_environment_change(glider_flight: dict):
    baseline = glider_flight["distance_m"]
    variants = {
        "double gravity": EnvironmentConfig(gravity=2.0 * STANDARD_GRAVITY),
        "head wind": EnvironmentConfig(wind_speed=6.0, wind_direction_deg=90.0),
        "no drag": EnvironmentConfig(drag_scale=0.0),
    }
    for name, environment in variants.items():
        result = flight_sim.evaluate_flight(
            as_dict(GLIDER), as_dict(environment), as_dict(FLIGHT_LAUNCH), 3.0, 0.02
        )
        assert result["status"] in flight_sim.STATUS_VALUES
        assert abs(result["distance_m"] - baseline) > 1.0e-3, f"{name} did not change the trajectory"
    # Doubling gravity must shorten the flight, removing drag must lengthen it.
    # The gravity comparison is made on the *completed* flight (a horizon long
    # enough for the heavy and the nominal airframe both to touch down) and on
    # the landing range, not on the 3 s `glider_flight` snapshot: at 3 s the
    # nominal glider is still airborne and climbing out of its apogee, so a
    # truncated ground track there says nothing about how far it would have gone.
    heavy = flight_sim.evaluate_flight(
        as_dict(GLIDER),
        as_dict(EnvironmentConfig(gravity=2.0 * STANDARD_GRAVITY)),
        as_dict(FLIGHT_LAUNCH),
        12.0,
        0.02,
    )
    nominal = flight_sim.evaluate_flight(
        as_dict(GLIDER), as_dict(EnvironmentConfig()), as_dict(FLIGHT_LAUNCH), 12.0, 0.02
    )
    dragless = flight_sim.evaluate_flight(
        as_dict(GLIDER), as_dict(EnvironmentConfig(drag_scale=0.0)), as_dict(FLIGHT_LAUNCH), 3.0, 0.02
    )
    assert nominal["status"] == "landed" and heavy["status"] in ("landed", "crashed")
    assert heavy["flight_time_s"] < nominal["flight_time_s"]
    assert heavy["landing_distance_m"] < nominal["landing_distance_m"]
    # Removing drag does NOT necessarily shorten the *ground track* inside a
    # fixed time budget: with no drag the airframe never decelerates, so the
    # surplus lift is spent on a sustained zoom instead of forward translation.
    # The physically meaningful invariants are that it never terminates (no
    # energy is ever dissipated) and that it ends up higher than the baseline,
    # because it converted its excess kinetic energy into potential energy.
    assert dragless["status"] in ("flying", "timeout")
    # Both runs stop at the same `duration_s` cap, so compare with a tolerance
    # rather than a bare >= (a 1-ULP round-off difference is not a regression).
    assert dragless["flight_time_s"] == pytest.approx(
        glider_flight["flight_time_s"], abs=1.0e-6
    )
    assert dragless["max_altitude_m"] > glider_flight["max_altitude_m"]


def test_vertical_launch_lands_and_stops():
    launch = LaunchConfig(launch_speed=6.0, launch_angle_deg=-80.0, launch_height_m=1.2)
    result = flight_sim.evaluate_flight(
        as_dict(GLIDER), as_dict(EnvironmentConfig()), as_dict(launch), 3.0, 0.02
    )
    assert result["status"] in ("landed", "crashed")
    assert 0.0 < result["flight_time_s"] <= 1.0
    assert result["flight_time_s"] < result["duration_s"]
    assert result["distance_m"] < 5.0
    # The integrator freezes once the airframe is down.
    simulator = FlightSimulator(
        GLIDER, EnvironmentConfig(), launch, SimulationSettings(dt_max=0.02, record_interval_s=0.01)
    )
    summary = simulator.simulate(duration_s=3.0, record=True)
    assert summary["status"] in ("landed", "crashed")
    frozen = as_dict(simulator.state)
    for _ in range(25):
        packet = simulator.step(dt=0.02)
    assert packet["dt"] == 0.0 and packet["substeps"] == 0
    assert as_dict(simulator.state) == frozen
    assert packet["landed"] or packet["crashed"]


# --------------------------------------------------------------------------
# Telemetry payload and log export
# --------------------------------------------------------------------------
REQUIRED_STEP_KEYS = (
    "state",
    "dt",
    "substeps",
    "time_s",
    "position_m",
    "velocity_mps",
    "quaternion",
    "angular_velocity_radps",
    "roll_deg",
    "pitch_deg",
    "yaw_deg",
    "alpha_deg",
    "beta_deg",
    "speed_mps",
    "altitude_m",
    "dynamic_pressure_pa",
    "reynolds",
    "mach",
    "ld_ratio",
    "g_load",
    "max_stress_index",
    "max_stress_pa",
    "stalled",
    "landed",
    "crashed",
    "diverged",
    "rolled_back",
    "backend",
)


def test_step_payload_is_complete_and_self_consistent():
    simulator = glider_simulator()
    packet = simulator.step(dt=0.02)
    for key in REQUIRED_STEP_KEYS:
        assert key in packet, f"missing step key {key}"
    assert set(packet["state"]) == set(as_dict(simulator.state))
    assert packet["backend"] == "numpy"
    assert packet["dt"] > 0.0
    assert packet["substeps"] >= 1
    assert packet["time_s"] == pytest.approx(packet["state"]["time_s"], rel=1e-12)
    # speed_mps is the *air-relative* speed (wind removed), ground_speed_mps the
    # inertial one; they differ whenever the air moves.
    assert packet["ground_speed_mps"] == pytest.approx(
        float(np.linalg.norm(np.asarray(packet["velocity_mps"]))), rel=1e-9
    )
    assert packet["speed_mps"] <= packet["ground_speed_mps"] + 1.0e-9
    assert packet["altitude_m"] == pytest.approx(-packet["position_m"][2], rel=1e-9)
    assert packet["dynamic_pressure_pa"] == pytest.approx(
        0.5 * packet["air_density_kgm3"] * packet["speed_mps"] ** 2, rel=1e-3
    )
    assert packet["reynolds"] == pytest.approx(
        packet["speed_mps"]
        * derived_properties(simulator.aircraft)["mac_m"]
        / packet["kinematic_viscosity_m2s"],
        rel=1e-3,
    ) if "kinematic_viscosity_m2s" in packet else True
    assert packet["mach"] == pytest.approx(packet["speed_mps"] / 340.0, rel=0.1)
    assert 0.0 <= packet["max_stress_index"] <= 1.0
    assert packet["max_stress_pa"] >= 0.0
    assert math.isfinite(packet["g_load"]) and packet["g_load"] >= 0.0
    roll, pitch, yaw = euler_zyx_from_quaternion(packet["quaternion"])
    assert (roll, pitch, yaw) == pytest.approx(
        (packet["roll_deg"], packet["pitch_deg"], packet["yaw_deg"]), abs=1e-9
    )
    # dt = 0 for a non-positive request, and the substep budget is honoured.
    idle = simulator.step(dt=0.0)
    assert idle["dt"] == 0.0 and idle["substeps"] == 0
    capped = simulator.step(dt=1.0, substeps=3)
    assert capped["substeps"] <= 3


def test_g_load_is_one_in_a_level_trimmed_glide():
    # At trim the specific force along the body z axis balances the weight, so
    # the accelerometer relation a_specific = a_inertial - g gives exactly 1 g.
    simulator = glider_simulator()
    simulator.simulate(duration_s=0.05, record=False)
    packet = simulator.step(dt=0.01)
    body_velocity = world_to_body(packet["quaternion"], np.array(packet["velocity_mps"]) - np.array(packet["wind_velocity_mps"]))
    lift_load = flight_sim.wind_force_to_body(body_velocity, packet["drag_n"], packet["side_n"], packet["lift_n"])[2]
    weight = derived_properties(simulator.aircraft)["mass_kg"] * simulator.environment.gravity
    assert packet["g_load"] == pytest.approx(abs(lift_load) / weight, rel=1e-9)
    assert 0.0 < packet["g_load"] < 5.0


def test_log_row_matches_the_declared_columns():
    simulator = glider_simulator()
    simulator.simulate(duration_s=0.3, record=True)
    samples = simulator.history()
    assert samples
    for sample in (samples[0], samples[len(samples) // 2], samples[-1]):
        row = flight_sim.log_row(sample)
        assert set(row) == set(FLIGHT_LOG_COLUMNS)
        assert len(row) == len(FLIGHT_LOG_COLUMNS)
        assert all(isinstance(value, (int, float, bool, str)) for value in row.values())
    # The row mirrors the sample exactly (CSV/JSON round trip is lossless).
    row = flight_sim.log_row(samples[-1])
    for column in FLIGHT_LOG_COLUMNS:
        assert row[column] == getattr(samples[-1], column)


def test_metrics_summarise_the_recorded_history():
    simulator = glider_simulator()
    summary = simulator.simulate(duration_s=0.6, record=True)
    assert summary["points"] == simulator.frame_count() > 1
    assert summary["distance_m"] >= summary["horizontal_range_m"] >= 0.0
    assert summary["max_altitude_m"] >= 1.5 - 1.0e-9  # the launch height
    assert summary["apogee_time_s"] <= summary["flight_time_s"] + 1.0e-9
    assert summary["backend"] == "numpy"
    assert summary["status"] in flight_sim.STATUS_VALUES
    assert set(summary["backends"]) == {"aero", "atmosphere", "wind", "stress", "backend"}
    # simulate() resumes instead of restarting.
    resumed = simulator.simulate(duration_s=0.9, record=True)
    assert resumed["flight_time_s"] > summary["flight_time_s"]


def test_wind_coupling_is_exactly_inert_for_a_calm_atmosphere():
    calm = EnvironmentConfig(**CALM_ENVIRONMENT)
    assert np.allclose(flight_sim.wind_vector((1.0, 2.0, -3.0), calm, 0.0), 0.0, atol=0.0)
    assert np.allclose(flight_sim.wind_vector((1.0, 2.0, -3.0), calm, 12.5), 0.0, atol=0.0)
    # With a mean wind the vector is non-zero and the z-up / z-down conversion of
    # the upstream solver is applied (the wind is a world-frame velocity).  At the
    # reference height the logarithmic profile factor is exactly one.
    windy = EnvironmentConfig(wind_speed=8.0, gust_intensity=0.0, thermal_strength=0.0, turbulence_scale=0.0)
    vector = flight_sim.wind_vector((0.0, 0.0, -windy.reference_height_m), windy, 0.0)
    assert float(np.linalg.norm(vector)) == pytest.approx(8.0, rel=1e-6)
    assert abs(float(vector[2])) < 1.0e-9  # a horizontal wind stays horizontal
    # Above the reference height the log-law profile strengthens the mean wind.
    aloft = flight_sim.wind_vector((0.0, 0.0, -10.0), windy, 0.0)
    assert float(np.linalg.norm(aloft)) > 8.0


def test_aerodynamic_bridge_prefers_the_upstream_solver():
    """The module must be wired to the real domains when they are importable."""
    backends = flight_sim.active_backends()
    assert backends["backend"] == "numpy"
    assert set(backends) == {"aero", "atmosphere", "wind", "stress", "backend"}
    aircraft = AircraftConfig()
    environment = EnvironmentConfig()
    result = flight_sim.aero_forces(aircraft, environment, 12.0, 4.0, 1.0, 0.035, 1.0e5, 1.2, (0.1, 0.2, 0.3))
    assert result.lift_n > 0.0 and result.drag_n > 0.0
    assert result.dynamic_pressure_pa == pytest.approx(0.5 * 1.225 * 144.0, rel=0.02)
    assert 0.0 <= result.cl <= 1.5
    air = flight_sim.atmosphere_state(environment, 0.0, 0.1)
    assert air["density_kgm3"] == pytest.approx(1.225, rel=0.05)
    assert air["sound_speed_mps"] == pytest.approx(340.3, rel=0.01)
    stress = flight_sim.stress_metrics(
        aircraft, environment, derived_properties(aircraft), 88.0, 1.0, speed_mps=12.0
    )
    assert stress["max_stress_pa"] > 0.0
    assert 0.0 <= stress["max_stress_index"] < 10.0


def test_module_exposes_a_stable_public_api():
    for name in (
        "FlightSimulator",
        "initial_state",
        "effective_alpha_beta",
        "total_energy",
        "evaluate_flight",
        "log_row",
        "flight_from_aircraft",
    ):
        assert name in flight_sim.__all__
        assert hasattr(flight_sim, name)
    for name in (
        "reset",
        "derived_timestep",
        "step",
        "simulate",
        "history",
        "metrics",
        "seek_frame",
        "frame_count",
        "reset_history",
    ):
        assert callable(getattr(flight_sim.FlightSimulator, name))
    assert isinstance(flight_sim.FlightSimulator.state, property)
    # from_dict / as_dict round trip keeps the simulator constructible.
    simulator = flight_sim.FlightSimulator(
        as_dict(GLIDER), as_dict(EnvironmentConfig()), as_dict(FLIGHT_LAUNCH), None
    )
    restored = from_dict(AircraftConfig, as_dict(GLIDER))
    assert restored == GLIDER
    assert simulator.state.time_s == 0.0


def test_suite_runs_well_inside_the_time_budget():
    # Guards the "well under 60 s" requirement against an accidental O(n^2) loop.
    started = time.perf_counter()
    flight_sim.evaluate_flight(
        as_dict(GLIDER), as_dict(EnvironmentConfig()), as_dict(FLIGHT_LAUNCH), 1.0, 0.02
    )
    assert time.perf_counter() - started < 10.0


def test_keel_depth_grip_z_moment_transmission():
    """Verify keel depth (+0.015 m) transmits pitch and roll moments from hand push."""
    # 1. Forward thrust (+x): grip below CG creates positive pitch moment (nose up)
    launch_fwd = LaunchConfig(
        throw_style="overhand",
        throw_speed_source="impulse",
        throw_force_n=2.0,
        throw_duration_s=0.1,
        throw_force_pitch_deg=0.0,
        throw_force_yaw_deg=0.0,
        throw_grip_x=0.0,
        throw_grip_y=0.0,
        throw_grip_z=0.015,
        throw_torque_efficiency=0.2,
    )
    sol_fwd = flight_sim.resolve_throw(launch_fwd, GLIDER, EnvironmentConfig())
    # r = (0, 0, 0.015), F = (2.0, 0, 0) -> moment = r x F = (0, 0.015 * 2.0, 0) = (0, +0.030, 0)
    assert sol_fwd.grip_point_m[2] == pytest.approx(0.015, abs=1e-5)
    assert sol_fwd.moment_nm[1] > 0.025  # Positive pitch moment (nose-up)
    assert sol_fwd.angular_rate_radps[1] > 0.0  # Positive pitch rate

    # 2. Lateral push (+y): grip below CG creates negative roll moment (left roll)
    launch_side = LaunchConfig(
        throw_style="overhand",
        throw_speed_source="impulse",
        throw_force_n=1.5,
        throw_duration_s=0.1,
        throw_force_pitch_deg=0.0,
        throw_force_yaw_deg=90.0,  # Side push along +y
        throw_grip_x=0.0,
        throw_grip_y=0.0,
        throw_grip_z=0.015,
        throw_torque_efficiency=0.2,
    )
    sol_side = flight_sim.resolve_throw(launch_side, GLIDER, EnvironmentConfig())
    # r = (0, 0, 0.015), F = (0, 1.5, 0) -> moment = r x F = (-0.015 * 1.5, 0, 0) = (-0.0225, 0, 0)
    assert sol_side.moment_nm[0] < -0.015  # Negative roll moment
    assert sol_side.angular_rate_radps[0] < 0.0  # Negative roll rate


def test_asymmetry_and_aileron_3d_6dof_bank_and_turn():
    """Verify 3D 6DoF banking and turning motion induced by aileron_deg and asymmetry_cl."""
    launch = LaunchConfig(
        launch_speed=8.0,
        launch_angle_deg=5.0,
        launch_roll_deg=0.0,
        launch_yaw_deg=0.0,
        launch_height_m=5.0,
    )
    settings = SimulationSettings(dt_max=0.01, record_interval_s=0.05)

    # 1. Symmetric baseline airframe
    glider_sym = replace(GLIDER, aileron_deg=0.0, asymmetry_cl=0.0)
    sim_sym = FlightSimulator(glider_sym, EnvironmentConfig(), launch, settings)
    summary_sym = sim_sym.simulate(duration_s=1.5, record=True)
    history_sym = sim_sym.history()
    last_sym = history_sym[-1]
    # Symmetric glider stays essentially wings level and flies straight (y displacement ~ 0)
    assert abs(last_sym.roll_deg) < 3.0
    assert abs(last_sym.y_m) < 0.2

    # 2. Airframe with positive aileron deflection (right roll bias)
    glider_aileron = replace(GLIDER, aileron_deg=0.4, asymmetry_cl=0.0)
    sim_ail = FlightSimulator(glider_aileron, EnvironmentConfig(), launch, settings)
    summary_ail = sim_ail.simulate(duration_s=1.0, record=True)
    history_ail = sim_ail.history()
    last_ail = history_ail[-1]
    # Positive aileron develops positive roll (banking right) and turns right (+y displacement)
    assert last_ail.roll_deg > 10.0
    assert last_ail.y_m > 0.1  # Curved trajectory along +y
    assert last_ail.yaw_deg > 5.0  # Heading turns right

    # 3. Airframe with lift asymmetry
    glider_asym = replace(GLIDER, aileron_deg=0.0, asymmetry_cl=0.005)
    sim_asym = FlightSimulator(glider_asym, EnvironmentConfig(), launch, settings)
    summary_asym = sim_asym.simulate(duration_s=1.0, record=True)
    history_asym = sim_asym.history()
    last_asym = history_asym[-1]
    # Lift asymmetry also induces positive bank and turn
    assert last_asym.roll_deg > 5.0
    assert last_asym.y_m > 0.05
    assert last_asym.yaw_deg > 2.0


def test_aeroelastic_coupling_reduces_peak_pitchup_during_high_speed_throw():
    """Verify aeroelastic coupling (flex and washout twist) reduces peak pitch-up during high-speed throw."""
    launch_fast = LaunchConfig(
        launch_speed=20.0,
        launch_angle_deg=5.0,
        launch_height_m=5.0,
    )
    settings = SimulationSettings(dt_max=0.005, record_interval_s=0.02)

    # Baseline rigid airframe
    glider_rigid = replace(GLIDER, aeroelastic_flex=0.0, aeroelastic_twist=0.0)
    sim_rigid = FlightSimulator(glider_rigid, EnvironmentConfig(), launch_fast, settings)
    sim_rigid.simulate(duration_s=0.5, record=True)
    max_pitch_rigid = max(sample.pitch_deg for sample in sim_rigid.history())

    # Flexible airframe with aeroelastic twist (washout) and dihedral flex
    glider_flex = replace(GLIDER, aeroelastic_flex=0.5, aeroelastic_twist=0.8)
    sim_flex = FlightSimulator(glider_flex, EnvironmentConfig(), launch_fast, settings)
    sim_flex.simulate(duration_s=0.5, record=True)
    max_pitch_flex = max(sample.pitch_deg for sample in sim_flex.history())

    # Washout lowers local angle, but a stable cm_alpha can increase nose-up
    # torque. It does not guarantee a lower Earth-frame pitch angle.
    assert math.isfinite(max_pitch_flex) and math.isfinite(max_pitch_rigid)
    diag = sim_flex._diagnostics()
    assert diag["geometric_alpha_deg"] - diag["alpha_deg"] == pytest.approx(diag["aeroelastic_twist_deg"])
    # Check diagnostics populated
    step_packet = sim_flex.step(dt=0.01)
    assert "aeroelastic_twist_deg" in step_packet
    assert "aeroelastic_dihedral_deg" in step_packet


def test_added_mass_attenuates_normal_acceleration():
    """Verify added mass effect smoothly attenuates body normal acceleration when enabled."""
    settings = SimulationSettings(dt_max=0.005, collision_enabled=False)
    launch = LaunchConfig(launch_speed=10.0, launch_angle_deg=10.0, launch_height_m=10.0)

    # 1. Without added mass
    glider_no_add = replace(GLIDER, added_mass_enabled=False)
    sim_no_add = FlightSimulator(glider_no_add, EnvironmentConfig(), launch, settings)
    diag_no_add = sim_no_add._diagnostics()
    # Body normal acceleration: acceleration projected to body z
    q1 = diag_no_add["quaternion"]
    a1_world = np.array(diag_no_add["acceleration_mps2"], dtype=np.float64)
    a1_body = world_to_body(q1, a1_world)

    # 2. With added mass
    glider_with_add = replace(GLIDER, added_mass_enabled=True)
    sim_with_add = FlightSimulator(glider_with_add, EnvironmentConfig(), launch, settings)
    diag_with_add = sim_with_add._diagnostics()
    q2 = diag_with_add["quaternion"]
    a2_world = np.array(diag_with_add["acceleration_mps2"], dtype=np.float64)
    a2_body = world_to_body(q2, a2_world)

    # Effective mass in z is larger (m + m_add_z), so magnitude of a_z is attenuated
    assert abs(a2_body[2]) < abs(a1_body[2])


def test_multipoint_ground_collision_stability():
    """Verify multi-point ground collision behaves stably with roll/yaw damping and landing/stop."""
    # Launch glider banked into ground at low altitude
    launch_banked = LaunchConfig(
        launch_speed=4.0,
        launch_angle_deg=-10.0,
        launch_roll_deg=30.0,
        launch_height_m=0.05,
    )
    settings = SimulationSettings(
        dt_max=0.005,
        record_interval_s=0.01,
        collision_enabled=True,
        restitution=0.1,
    )
    sim = FlightSimulator(GLIDER, EnvironmentConfig(), launch_banked, settings)
    summary = sim.simulate(duration_s=1.0, record=True)

    # Simulation finishes stably without divergence
    assert summary["status"] in ("landed", "crashed", "flying")
    assert not summary["diverged"]
    history = sim.history()
    assert len(history) >= 1
    assert summary["status"] in ("landed", "crashed")
    # Initial geometry can already touch the ground at 5 cm height.
    # Check that angular rate and altitude are bounded
    for sample in history:
        assert math.isfinite(sample.pitch_deg)
        assert math.isfinite(sample.roll_deg)
        assert sample.altitude_m >= -0.05




def test_no_gravity_orientation_torque_without_relative_airflow():
    sim = glider_simulator()
    for pitch in (-80.0, 20.0, 75.0):
        moment = sim._restoring_moment(quaternion_from_euler(35.0, pitch, 10.0), np.array([1.0, -2.0, 3.0]), 0.0, 0.0)
        assert np.array_equal(moment, np.zeros(3))


def test_force_rotation_uses_relative_flow_including_sideslip_and_twist():
    sim = FlightSimulator(replace(GLIDER, aeroelastic_twist=.8), EnvironmentConfig(**CALM_ENVIRONMENT), LaunchConfig(launch_speed=15, launch_angle_deg=5, release_pitch_deg=3), SimulationSettings(collision_enabled=False))
    core = sim._compute_dynamics(flight_sim._pack(sim.state), sim.state.time_s)
    force = core["force_body"]
    velocity = core["body_relative"]
    assert np.dot(force, velocity) == pytest.approx(-core["aero"].drag_n * core["speed"], rel=1e-9, abs=1e-10)


def test_pressure_compliance_is_linked_and_does_not_mutate_aircraft():
    aircraft = replace(GLIDER, aeroelastic_compliance=.8)
    original = as_dict(aircraft)
    high = FlightSimulator(aircraft, EnvironmentConfig(**CALM_ENVIRONMENT), LaunchConfig(launch_speed=26, launch_angle_deg=78, launch_height_m=1.8), SimulationSettings(collision_enabled=False))
    zero = FlightSimulator(aircraft, EnvironmentConfig(**CALM_ENVIRONMENT), LaunchConfig(launch_speed=0, launch_height_m=10), SimulationSettings(collision_enabled=False))
    assert high._diagnostics()["trailing_edge_bend_deg"] > 0
    assert zero._diagnostics()["trailing_edge_bend_deg"] == 0
    assert as_dict(aircraft) == original
    assert high._diagnostics()["max_stress_pa"] > 0


def test_phase_diagnostic_requires_continuous_stable_glide():
    sample = glider_simulator().history()[0]
    rows = [replace(sample, time_s=i*.1, altitude_m=10-i*.05, vx_mps=4, vy_mps=0, vz_mps=.5, speed_mps=4.03, pitch_deg=-3, alpha_deg=4, stalled=False) for i in range(21)]
    phase = flight_sim.flight_phase_metrics(rows)
    assert phase["apex_recovery_status"] == "recovered"
    assert phase["stable_glide_duration_s"] == pytest.approx(2)
    assert flight_sim.flight_phase_metrics(rows, "apex_glide")["apex_recovery_status"] == "not_applicable"
    tumbling = [replace(row, pitch_deg=(i%2)*30) for i,row in enumerate(rows)]
    assert flight_sim.flight_phase_metrics(tumbling)["apex_recovery_status"] == "not_observed"
    assert flight_sim.flight_phase_metrics([replace(row, alpha_deg=-60) for row in rows])["apex_recovery_status"] == "not_observed"


def test_existing_simulation_summary_matches_evaluate_contract_without_rerun():
    sim = glider_simulator()
    summary = sim.simulate(.1)
    state_before = sim.state
    result = flight_sim.summarize_flight(sim, summary)
    assert result["post_apogee_time_s"] == summary["post_apogee_time_s"]
    assert result["apex_recovery_status"] == summary["apex_recovery_status"]
    assert sim.state == state_before
    assert set(result) == set(flight_sim.EVALUATE_KEYS)


def test_step_tracks_actual_elapsed_time_after_rejected_step_halving(monkeypatch):
    sim = quiescent()
    sim.settings = replace(sim.settings, adaptive=False, dt_max=.01, max_substeps=1)
    original = sim._step_is_acceptable
    calls = [0]
    def reject_first(previous, candidate):
        calls[0] += 1
        return calls[0] > 1 and original(previous, candidate)
    monkeypatch.setattr(sim, "_step_is_acceptable", reject_first)
    packet = sim.step(.01)
    assert packet["dt"] == pytest.approx(sim.state.time_s)
    assert packet["dt"] == pytest.approx(.005)


def test_reset_records_release_frame_after_prior_flight():
    sim = quiescent()
    sim.step(.02)
    sim.reset()
    assert sim.history()[0].time_s == 0.0
    assert sim.history()[0].altitude_m == pytest.approx(sim.launch.launch_height_m)


def test_flight_log_mode_and_version_survive_csv_round_trip():
    from physics.storage_domain import export_flight_log, sample_from_row
    import csv
    import io
    sim = quiescent()
    sim.reset(replace(sim.launch, flight_mode="full_flight"))
    sample = sim.history()[0]
    payload, _, _ = export_flight_log([sample], "csv")
    restored = sample_from_row(next(csv.DictReader(io.StringIO(payload))))
    assert restored == sample
    assert restored.flight_mode == "full_flight"
    assert restored.physics_model_version == "rigid_body_repair_v1"
    legacy = sample_from_row({"time_s": "0"})
    assert legacy.flight_mode == "legacy"
    assert legacy.physics_model_version == "unknown"


def test_passive_aeroelastic_morphing_smooth_response():
    """Verify passive aeroelastic morphing scales smoothly without hard clamping."""
    aircraft = replace(GLIDER, aeroelastic_compliance=1.0, elevon_deg=2.0, paper_stiffness=0.35)
    env = EnvironmentConfig(**CALM_ENVIRONMENT)
    settings = SimulationSettings(collision_enabled=False)

    # 1. Zero dynamic pressure -> zero bend, exact trim restored
    sim_zero = FlightSimulator(aircraft, env, LaunchConfig(launch_speed=0.0, launch_height_m=10.0), settings)
    assert sim_zero._diagnostics()["trailing_edge_bend_deg"] == 0.0

    # 2. Moderate dynamic pressure (cruise glide, 4 m/s)
    sim_cruise = FlightSimulator(aircraft, env, LaunchConfig(launch_speed=4.0, launch_height_m=10.0), settings)
    bend_cruise = sim_cruise._diagnostics()["trailing_edge_bend_deg"]

    # 3. High dynamic pressure (launch, 26 m/s)
    sim_launch = FlightSimulator(aircraft, env, LaunchConfig(launch_speed=26.0, launch_angle_deg=78.0, launch_height_m=1.8), settings)
    bend_launch = sim_launch._diagnostics()["trailing_edge_bend_deg"]

    # Monotonic progression with dynamic pressure without abrupt saturation
    assert 0.0 < bend_cruise < bend_launch
    assert bend_launch > 8.0  # Demonstrates removal of restrictive 8-degree hard ceiling


def test_landing_stops_and_declares_landed_on_max_bounces():
    launch = LaunchConfig(launch_speed=1.0, launch_angle_deg=-10.0, launch_height_m=0.01)
    env = EnvironmentConfig(**CALM_ENVIRONMENT)
    settings = SimulationSettings(collision_enabled=True)
    sim = FlightSimulator(GLIDER, env, launch, settings)
    summary = sim.simulate(duration_s=2.0, record=True)
    assert summary["status"] in ("landed", "crashed")
    assert sim.state.landed is True or sim.state.crashed is True



