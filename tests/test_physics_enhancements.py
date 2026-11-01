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
    added = aero_kernel.compute_added_mass(ScalarMath, rho, area, mac)
    expected_mz = (math.pi / 4.0) * rho * area * mac
    assert np.isclose(added["added_mass_z_kg"], expected_mz)
    assert added["added_inertia_yy_kgm2"] > 0.0


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
