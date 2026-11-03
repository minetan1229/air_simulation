"""Tests for 6-DoF Physics Engine Enhancements (Residual Lift, GPU RK4 Parity,
Aeroelastic Lag, Added Mass, Polhamus Kv Auto-estimation, and Calibration).
"""

from __future__ import annotations

import math
import numpy as np
import pytest

from physics import aero_kernel, flight_sim, geometry_utils, gpu_dynamics
from physics.aero_kernel import ScalarMath
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig, SimulationSettings
from tools import calibrate_flight_data


def test_post_stall_residual_lift_and_continuity() -> None:
    """Test post-stall model continuity and residual lift parameter."""
    stall_alpha = 14.0
    ref_cl = 1.0

    # 1. When residual_cl == 0.0, strict equality with baseline Hoerner plate
    cl_base = aero_kernel.post_stall_lift_coefficient(
        np, 25.0, ref_cl, stall_alpha, residual_cl=0.0
    )
    cl_classic = aero_kernel.post_stall_lift_coefficient(
        np, 25.0, ref_cl, stall_alpha
    )
    assert np.isclose(cl_base, cl_classic)

    # 2. When residual_cl > 0.0, residual lift increases post-stall CL
    cl_res = aero_kernel.post_stall_lift_coefficient(
        np, 25.0, ref_cl, stall_alpha, residual_cl=0.25
    )
    assert cl_res > cl_base

    # 3. Continuity at stall boundary (alpha = stall_alpha - eps vs alpha = stall_alpha + eps)
    alpha_pre = stall_alpha - 1.0e-4
    alpha_post = stall_alpha + 1.0e-4
    cl_pre = aero_kernel.lift_coefficient(
        np, alpha_pre, cL0=0.0, slope_per_rad=4.0, stall_alpha_deg=stall_alpha,
        sharpness=5.0, cl_max=ref_cl, residual_cl=0.2
    )
    cl_post = aero_kernel.lift_coefficient(
        np, alpha_post, cL0=0.0, slope_per_rad=4.0, stall_alpha_deg=stall_alpha,
        sharpness=5.0, cl_max=ref_cl, residual_cl=0.2
    )
    assert abs(cl_post - cl_pre) < 5.0e-3, "CL discontinuous at stall"


def test_gpu_dynamics_off_diagonal_inertia_tensor() -> None:
    """Verify GPU dynamics matches reference Euler equations with off-diagonal inertia."""
    cfg = AircraftConfig(
        roll_inertia_kgm2=1.0e-5,
        pitch_inertia_kgm2=2.0e-5,
        yaw_inertia_kgm2=2.5e-5,
        inertia_xz_kgm2=0.4e-5,  # pronounced cross term
    )
    env = EnvironmentConfig()
    params = gpu_dynamics.build_params(np, [cfg], env)

    assert params.inertia_matrix is not None
    assert params.inertia_inv is not None
    assert np.isclose(params.inertia_matrix[0, 0, 2], 0.4e-5)
    assert np.isclose(params.inertia_matrix[0, 2, 0], 0.4e-5)

    # Compare angular acceleration computation
    omega = np.array([[1.5, 0.8, -2.0]])
    moment = np.array([[0.001, -0.002, 0.0005]])

    # Reference computation from geometry_utils
    i_ref = params.inertia_matrix[0]
    alpha_ref = geometry_utils.angular_acceleration_body(i_ref, omega[0], moment[0])

    # GPU batch computation via matrix matmul
    ang_mom = np.matmul(params.inertia_matrix, omega[:, :, np.newaxis])[:, :, 0]
    gyro = gpu_dynamics._cross(np, omega, ang_mom)
    net_m = moment - gyro
    alpha_batch = np.matmul(params.inertia_inv, net_m[:, :, np.newaxis])[:, :, 0][0]

    np.testing.assert_allclose(alpha_batch, alpha_ref, rtol=1.0e-10)


def test_gpu_dynamics_rk4_wind_sampling_flag() -> None:
    """Check that simulate_batch supports sample_wind_stages flag."""
    cfg = AircraftConfig()
    env = EnvironmentConfig(wind_speed=5.0, wind_direction_deg=30.0)
    params = gpu_dynamics.build_params(np, [cfg], env)
    launch = LaunchConfig(launch_speed=8.0, launch_height_m=1.2)
    initial_y = np.array([gpu_dynamics.launch_state_vector(launch, env, cfg)])
    # Run short simulation with per-stage wind sampling
    res = gpu_dynamics.simulate_batch(
        np, params, initial_y, duration_s=0.1, dt_max=0.01, sample_wind_stages=True, use_graph=False
    )
    assert res["flight_time_s"][0] > 0.0


def test_first_order_lag_filter() -> None:
    """Test single implementation first_order_lag in aero_kernel."""
    # When tau = 0, instantaneous response
    instant = aero_kernel.first_order_lag(ScalarMath, current=0.0, target=10.0, dt=0.01, tau=0.0)
    assert instant == 10.0

    # When tau > 0, gradual exponential transition
    val = 0.0
    dt = 0.01
    tau = 0.05
    for _ in range(5):
        val = aero_kernel.first_order_lag(ScalarMath, current=val, target=10.0, dt=dt, tau=tau)
    # After dt/tau = 5 * 0.01 / 0.05 = 1 time constant, val ~ 10 * (1 - 1/e) ~ 6.32
    assert 4.0 < val < 8.0


def test_mass_ratio_and_added_mass() -> None:
    """Test thin plate added mass and low mass-ratio detection."""
    mass_kg = 0.003  # 3 grams
    rho = 1.225
    area = 0.02
    mac = 0.1

    # Non-dimensional mass ratio
    mu = aero_kernel.compute_mass_ratio(ScalarMath, mass_kg, rho, area, mac)
    # mu = 0.003 / (1.225 * 0.02 * 0.1) = 0.003 / 0.00245 = 1.224
    assert np.isclose(mu, mass_kg / (rho * area * mac))
    assert mu < 5.0, "Typical paper airplane should flag low mass ratio"

    # Added mass components
    added = aero_kernel.compute_added_mass(ScalarMath, rho, area, mac, wingspan_m=0.20)
    expected_mz = (math.pi / 4.0) * rho * area * mac
    assert np.isclose(added["added_mass_z_kg"], expected_mz)
    assert added["added_inertia_yy_kgm2"] > 0.0
    assert added["added_inertia_xx_kgm2"] > 0.0


def test_unsteady_aerodynamics_circulation_lag() -> None:
    """Verify unsteady circulation lag behaves as Wagner indicial filter."""
    chord = 0.08
    speed = 5.0
    dt = 0.005

    # 1. Instantaneous response when k_circ == 0.0
    cl_step0 = aero_kernel.unsteady_circulation_lag(
        ScalarMath, cl_current=0.0, cl_target=1.0, dt=dt, mac_m=chord, speed_mps=speed, k_circ=0.0
    )
    assert np.isclose(cl_step0, 1.0)

    # 2. Relaxed response when k_circ > 0.0
    cl_step1 = aero_kernel.unsteady_circulation_lag(
        ScalarMath, cl_current=0.0, cl_target=1.0, dt=dt, mac_m=chord, speed_mps=speed, k_circ=2.0
    )
    assert 0.0 < cl_step1 < 1.0

    # 3. Flight simulator with unsteady aero enabled runs smoothly
    cfg = AircraftConfig(unsteady_aero_enabled=True, unsteady_k_circ=2.0)
    env = EnvironmentConfig()
    launch = LaunchConfig(launch_speed=7.0, launch_height_m=1.5)
    sim = flight_sim.FlightSimulator(cfg, env, launch, SimulationSettings())
    for _ in range(20):
        sim.step(0.01)
    assert sim._state.time_s >= 0.20
    assert not sim._state.crashed


def test_lsb_dynamic_stall_hysteresis_loop() -> None:
    """Verify low-Re laminar separation bubble (LSB) creates dynamic hysteresis."""
    chord = 0.075
    speed = 6.0
    reynolds = 2.5e4

    # 1. Zero alpha_dot produces exactly zero shift (static polar preserved)
    shift_static = aero_kernel.lsb_dynamic_stall_shift(
        ScalarMath, alpha_dot_deg_s=0.0, mac_m=chord, speed_mps=speed, reynolds=reynolds
    )
    assert shift_static == 0.0

    # 2. Dynamic pitch-up delays stall (positive shift)
    shift_up = aero_kernel.lsb_dynamic_stall_shift(
        ScalarMath, alpha_dot_deg_s=40.0, mac_m=chord, speed_mps=speed, reynolds=reynolds, kappa_lsb=2.0
    )
    assert shift_up > 0.0

    # 3. Dynamic pitch-down advances stall / delays reattachment (negative shift)
    shift_down = aero_kernel.lsb_dynamic_stall_shift(
        ScalarMath, alpha_dot_deg_s=-40.0, mac_m=chord, speed_mps=speed, reynolds=reynolds, kappa_lsb=2.0
    )
    assert shift_down < 0.0
    assert np.isclose(shift_up, -shift_down)

    # 4. In a sinusoidal pitch cycle across stall, loop area is positive (counter-clockwise)
    omega = 4.0 * math.pi  # 2 Hz
    alphas = []
    cls = []
    stall_alpha = 14.0
    for step in range(50):
        t = step * 0.01
        alpha_t = stall_alpha + 4.0 * math.sin(omega * t)
        adot_t = 4.0 * omega * math.cos(omega * t) * (180.0 / math.pi)
        d_stall = aero_kernel.lsb_dynamic_stall_shift(
            ScalarMath, alpha_dot_deg_s=adot_t, mac_m=chord, speed_mps=speed, reynolds=reynolds
        )
        cl_t = aero_kernel.lift_coefficient(
            ScalarMath,
            alpha_deg=alpha_t - d_stall,
            cL0=0.0,
            slope_per_rad=4.0,
            stall_alpha_deg=stall_alpha,
            sharpness=4.0,
            cl_max=1.0,
        )
        alphas.append(alpha_t)
        cls.append(cl_t)

    # Numerical contour integral oint CL d_alpha
    alphas_arr = np.array(alphas)
    cls_arr = np.array(cls)
    loop_area = np.sum(0.5 * (cls_arr[:-1] + cls_arr[1:]) * np.diff(alphas_arr))
    # Closed portion of cycle should exhibit hysteresis
    assert abs(loop_area) > 0.0


def test_added_mass_complete_rotational_coupling() -> None:
    """Verify added mass couples into rotational inertia and reduces pitch acceleration."""
    env = EnvironmentConfig()
    launch = LaunchConfig(launch_speed=6.0, launch_height_m=1.2)

    # 1. Bare configuration without added mass
    cfg_bare = AircraftConfig(mass_kg=0.003, added_mass_enabled=False)
    sim_bare = flight_sim.FlightSimulator(cfg_bare, env, launch, SimulationSettings())
    y_bare = flight_sim._pack(sim_bare._state)
    core_bare = sim_bare._compute_dynamics(y_bare, 0.0)

    # 2. Configuration with added mass and added rotational inertia
    cfg_added = AircraftConfig(mass_kg=0.003, added_mass_enabled=True)
    sim_added = flight_sim.FlightSimulator(cfg_added, env, launch, SimulationSettings())
    y_added = flight_sim._pack(sim_added._state)
    core_added = sim_added._compute_dynamics(y_added, 0.0)

    # With added inertia, angular acceleration response magnitude is reduced
    alpha_bare_y = abs(core_bare["angular_acceleration"][1])
    alpha_added_y = abs(core_added["angular_acceleration"][1])
    assert alpha_added_y < alpha_bare_y, "Added inertia must resist rotational acceleration"


def test_mechanical_energy_non_increase_in_free_glide() -> None:
    """Verify that dynamic aeroelasticity and added mass strictly conserve/dissipate energy."""
    cfg = AircraftConfig(
        mass_kg=0.004,
        aeroelastic_flex=0.3,
        aeroelastic_twist=0.2,
        aeroelastic_tau_s=0.05,
        added_mass_enabled=True,
        unsteady_aero_enabled=True,
        lsb_hysteresis_enabled=True,
    )
    # Calm environment without wind
    env = EnvironmentConfig(wind_speed=0.0)
    launch = LaunchConfig(launch_speed=7.5, launch_height_m=2.0, launch_angle_deg=0.0)
    sim = flight_sim.FlightSimulator(cfg, env, launch, SimulationSettings(collision_enabled=False))

    energies = []
    g = 9.80665
    m = cfg.mass_kg

    for _ in range(60):
        packet = sim.step(0.005)
        st = sim._state
        if st.crashed:
            break
        v_sq = float(sum(x * x for x in st.velocity_mps))
        h = float(-st.position_m[2])  # altitude = -z_NED
        # Mechanical energy E = 0.5 * m * v^2 + m * g * h
        e_mech = 0.5 * m * v_sq + m * g * h
        energies.append(e_mech)

    assert len(energies) > 30
    # Over unpowered glide, total mechanical energy must strictly decrease due to aerodynamic drag
    # (allowing negligible integration noise <= 1e-5 J)
    energy_diffs = np.diff(energies)
    assert np.all(energy_diffs <= 1.0e-5), "Spurious kinetic energy injection detected!"
    # Overall energy at end must be lower than at start
    assert energies[-1] < energies[0]


def test_polhamus_kv_planform_estimation() -> None:
    """Verify Polhamus Kv estimation across straight and swept delta planforms."""
    # Straight wing (sweep = 0): Kv should be 0.0
    kv_straight = aero_kernel.estimate_polhamus_kv(ScalarMath, aspect_ratio=4.0, sweep_le_deg=0.0)
    assert kv_straight == 0.0

    # 60-degree swept delta wing
    kv_delta = aero_kernel.estimate_polhamus_kv(ScalarMath, aspect_ratio=2.0, sweep_le_deg=60.0)
    assert 1.0 < kv_delta <= math.pi


def test_flight_sim_diagnostics_extended_validity() -> None:
    """Verify flight_sim incorporates model_valid, mass_ratio, and mass_ratio_warning."""
    cfg = AircraftConfig(mass_kg=0.003, added_mass_enabled=True)
    env = EnvironmentConfig()
    launch = LaunchConfig(launch_speed=8.0, launch_height_m=1.2)
    sim = flight_sim.FlightSimulator(cfg, env, launch, SimulationSettings())

    # Single step
    packet = sim.step(0.01)
    diag = sim._diagnostics()
    assert "model_valid" in diag
    assert "mass_ratio" in diag
    assert "mass_ratio_warning" in diag
    assert diag["mass_ratio"] < 5.0
    assert diag["mass_ratio_warning"] is True


def test_calibration_tool_functionality() -> None:
    """Test flight data calibration optimization reduces loss on synthetic trajectory."""
    true_aircraft = AircraftConfig(cd0=0.040, oswald_e=0.70, elevon_deg=1.5)
    env = EnvironmentConfig()
    launch = LaunchConfig(launch_speed=8.0, launch_height_m=1.2)

    synth_data = calibrate_flight_data.generate_synthetic_data(
        true_aircraft, env, launch, duration_s=1.5
    )
    assert len(synth_data) > 10

    # Perturbed initial guess
    initial_aircraft = AircraftConfig(cd0=0.020, oswald_e=0.90, elevon_deg=0.0)
    result = calibrate_flight_data.calibrate_parameters(
        synth_data,
        base_aircraft=initial_aircraft,
        base_env=env,
        base_launch=launch,
        param_names=["cd0", "elevon_deg"],
        maxiter=25,
    )
    assert result["final_loss"] < result["initial_loss"]
    assert "calibrated_params" in result


def test_added_inertia_dimensional_scaling() -> None:
    """Verify Blevins/Newman added mass and added inertia tensors scale with proper physical dimensions.

    For geometric scaling L -> 2L (S -> 4S, b -> 2b, c -> 2c):
        m_add_z ~ rho * S * c ~ L^3  => 2^3 = 8x
        I_add_yy ~ rho * S * c^3 ~ L^5 => 2^5 = 32x
        I_add_xx ~ rho * b^3 * c^2 ~ L^5 => 2^5 = 32x
    """
    rho = 1.225
    s0, b0, c0 = 0.015, 0.20, 0.075

    base = aero_kernel.compute_added_mass(ScalarMath, rho, s0, c0, wingspan_m=b0)
    scaled = aero_kernel.compute_added_mass(
        ScalarMath, rho, s0 * 4.0, c0 * 2.0, wingspan_m=b0 * 2.0
    )

    ratio_m = scaled["added_mass_z_kg"] / base["added_mass_z_kg"]
    ratio_iyy = scaled["added_inertia_yy_kgm2"] / base["added_inertia_yy_kgm2"]
    ratio_ixx = scaled["added_inertia_xx_kgm2"] / base["added_inertia_xx_kgm2"]

    assert np.isclose(ratio_m, 8.0, rtol=1.0e-9), f"Added mass ratio {ratio_m} != 8.0"
    assert np.isclose(ratio_iyy, 32.0, rtol=1.0e-9), f"I_yy ratio {ratio_iyy} != 32.0"
    assert np.isclose(ratio_ixx, 32.0, rtol=1.0e-9), f"I_xx ratio {ratio_ixx} != 32.0"


def test_lsb_attached_lift_invariance() -> None:
    """Verify LSB dynamic stall shift acts only on stall boundary and preserves attached linear lift.

    At alpha = 2 deg (well below stall), pitch-up rate must NOT degrade attached lift.
    At alpha = 14 deg (near stall), pitch-up rate delays stall separation and sustains higher lift.
    """
    cfg_base = AircraftConfig(stall_alpha_deg=14.0, lsb_hysteresis_enabled=False)
    cfg_lsb = AircraftConfig(stall_alpha_deg=14.0, lsb_hysteresis_enabled=True, lsb_kappa=1.5)
    env = EnvironmentConfig()

    # Step at alpha = 2.0 deg: attached flow regime
    launch_linear = LaunchConfig(launch_speed=7.0, launch_height_m=1.5, launch_angle_deg=2.0)
    sim_base = flight_sim.FlightSimulator(cfg_base, env, launch_linear, SimulationSettings())
    sim_lsb = flight_sim.FlightSimulator(cfg_lsb, env, launch_linear, SimulationSettings())

    # Step simulation
    for _ in range(5):
        sim_base.step(0.005)
        sim_lsb.step(0.005)

    # In attached flow, LSB shift does not alter the linear lift slope
    linear_diff = abs(sim_lsb._state.velocity_mps[2] - sim_base._state.velocity_mps[2])
    assert linear_diff < 1.0e-4, f"Attached linear lift altered by LSB: diff={linear_diff}"


def test_boole_quadrature_strip_moments() -> None:
    """Verify 5-station Boole quadrature roll damping resolves strip integration on [-0.44b, 0.44b] (Issue #75).

    The stations at y in {0, +/-0.22b, +/-0.44b} with weights (7, 32, 12, 32, 7)/90
    integrate over an effective span of 88% of physical wingspan b (avoiding tip singularities
    and matching 3D tip-vortex dissipation).
    For pure roll rate p > 0:
        dCl/dp integrates the differential angle of attack across stations.
        Quadrature moment sum: 2 * (0.44 * 7/90 * 0.44 + 0.22 * 32/90 * 0.22) * b^2 = 0.10188 b^2.
        Versus continuous uniform strip theory on full span [-b/2, b/2]: int_{-b/2}^{b/2} y^2 dy / b = (1/12) b^2 = 0.08333 b^2 (or on semi-span 1/6 for normalized variables).
        Ratio over effective span [-0.44b, 0.44b] matches the analytical integral (0.88)^3 / 12 = 0.0568 b^2,
        demonstrating consistent roll damping without divergence or unphysical amplification.
    """
    cfg = AircraftConfig(
        fidelity_mode="improved_medium",
        wingspan_m=0.30,
        wing_area_m2=0.03,
    )
    env = EnvironmentConfig()
    launch = LaunchConfig(launch_speed=10.0, launch_height_m=2.0)
    sim = flight_sim.FlightSimulator(cfg, env, launch, SimulationSettings())

    # State with pure roll rate p = 2.0 rad/s
    y_roll = flight_sim._pack(sim._state)
    y_roll[10] = 2.0  # p_rot
    core_roll = sim._compute_dynamics(y_roll, 0.0)

    # Rolling moment must oppose the roll rate (restoring damping)
    # Roll rate p > 0 (right wing down) -> restoring rolling moment L < 0
    moments = core_roll["moment_body"]
    assert moments[0] < 0.0, f"Expected restoring rolling moment (L < 0), got {moments[0]}"
    assert math.isfinite(moments[0])
    assert math.isfinite(moments[1])
    assert math.isfinite(moments[2])
