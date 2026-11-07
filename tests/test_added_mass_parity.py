"""Issue #136: Added Mass CPU/GPU Parity, Tensor Coupling, and Physical Bounds Audit.

Hypothesis:
1. Physical Formulation & Dimensions:
   - Wing-normal added mass: m_added_z = (pi/4) * rho * S * mac [kg]
   - Roll apparent inertia:  I_added_xx = (pi/48) * rho * b^3 * mac^2 [kg m^2]
   - Pitch apparent inertia: I_added_yy = (pi/128) * rho * S * mac^3 [kg m^2]
   - Yaw apparent inertia:   I_added_zz = (pi/128) * rho * h_keel * mac^4 [kg m^2]
   All added terms are strictly positive (m_z > 0, I_xx > 0, I_yy > 0, I_zz > 0)
   for physical airframes in fluid (rho > 0). The augmented 3x3 inertia tensor
   J_eff = J_body + diag(I_added_xx, I_added_yy, I_added_zz) is symmetric
   positive-definite whenever J_body is symmetric positive-definite.
2. Coordinate Frames & Dynamic Attenuation:
   - Added mass acts purely normal to the wing (body z-axis, z-down convention).
   - In both CPU (FlightSimulator) and GPU (simulate_batch):
     a_body[2] = (force_body[2] + m * gravity_body[2]) / (m + m_add_z)
     attenuating high-frequency normal translational jerk.
   - Enabling added mass attenuates peak z-acceleration and resultant G-load / peak root stress.
3. CPU vs GPU Parity:
   - Under both active (added_mass_enabled=True, apparent_inertia_enabled=True)
     and inactive states, CPU (FlightSimulator) and GPU (gpu_dynamics.simulate_batch)
     evaluate consistent accelerations, angular accelerations, peak load factors,
     and peak root stresses within tight numeric parity tolerances (rel <= 1e-4).
"""
from __future__ import annotations

import numpy as np
import pytest

from physics import aero_kernel, flight_sim, gpu_dynamics
from physics.aero_kernel import ScalarMath
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig, SimulationSettings


def test_added_mass_positive_definite_diagonal() -> None:
    """Verify physical positive-definiteness and dimensional consistency of added mass tensor."""
    rho = 1.225
    area = 0.018
    mac = 0.0826
    span = 0.22
    added = aero_kernel.compute_added_mass(ScalarMath, rho, area, mac, wingspan_m=span)
    
    for key in ("added_mass_z_kg", "added_inertia_xx_kgm2", "added_inertia_yy_kgm2", "added_inertia_zz_kgm2"):
        val = float(added[key])
        assert val > 0.0, f"Component {key} must be positive, got {val}"
        assert np.isfinite(val), f"Component {key} must be finite"

    # Effective mass ratio should be realistic for paper aircraft (~10% to 40% apparent fluid mass)
    m_body = 0.0035
    m_add_z = float(added["added_mass_z_kg"])
    eff_ratio = m_body / (m_body + m_add_z)
    assert 0.60 < eff_ratio < 0.95


def test_effective_inertia_tensor_positive_definite() -> None:
    """Verify coupling of body inertia tensor with apparent inertia retains positive-definiteness."""
    aircraft = AircraftConfig(added_mass_enabled=True, apparent_inertia_enabled=True)
    env = EnvironmentConfig()
    params = gpu_dynamics.build_params(np, [aircraft], env)

    added = aero_kernel.compute_added_mass(
        np, 1.225, params.area, params.chord, wingspan_m=params.span
    )
    eff_mat = np.copy(params.inertia_matrix[0])
    eff_mat[0, 0] += float(np.asarray(added["added_inertia_xx_kgm2"]).ravel()[0])
    eff_mat[1, 1] += float(np.asarray(added["added_inertia_yy_kgm2"]).ravel()[0])
    eff_mat[2, 2] += float(np.asarray(added["added_inertia_zz_kgm2"]).ravel()[0])

    # Check symmetry
    assert np.allclose(eff_mat, eff_mat.T, atol=1e-15)

    # Check positive definiteness via eigenvalues and Cholesky decomposition
    eigvals = np.linalg.eigvalsh(eff_mat)
    assert np.all(eigvals > 0.0), f"Eigenvalues must be strictly positive: {eigvals}"
    np.linalg.cholesky(eff_mat)

    # Verify analytic batch inversion matches linalg.inv
    batch_mat = eff_mat[np.newaxis, :, :]
    batch_inv = gpu_dynamics._invert_3x3_batch(np, batch_mat)[0]
    exact_inv = np.linalg.inv(eff_mat)
    assert np.allclose(batch_inv, exact_inv, rtol=1e-12, atol=1e-15)


def test_cpu_gpu_derivative_parity_added_mass_active() -> None:
    """Verify bit-accurate or near bit-accurate parity of initial derivatives when added mass is active."""
    aircraft = AircraftConfig(added_mass_enabled=True, apparent_inertia_enabled=True)
    env = EnvironmentConfig(wind_speed=0.0)
    launch = LaunchConfig(launch_speed=8.0, launch_angle_deg=0.0, launch_height_m=1.2)
    settings = SimulationSettings()

    sim = flight_sim.FlightSimulator(aircraft, env, launch, settings)
    y0 = gpu_dynamics.launch_state_vector(launch, env, aircraft)
    params = gpu_dynamics.build_params(np, [aircraft], env)
    w_gpu = gpu_dynamics.wind_batch(np, params, y0[:3][np.newaxis, :], 0.0)

    core_cpu = sim._compute_dynamics(y0, 0.0)
    core_gpu = gpu_dynamics._evaluate_state(np, params, y0[np.newaxis, :], w_gpu)

    # World acceleration parity
    np.testing.assert_allclose(
        core_gpu["acceleration"][0],
        core_cpu["acceleration"],
        rtol=1e-7,
        atol=1e-9,
        err_msg="Linear acceleration mismatch between CPU and GPU with added mass enabled",
    )

    # Angular acceleration parity
    np.testing.assert_allclose(
        core_gpu["angular_acceleration"][0],
        core_cpu["angular_acceleration"],
        rtol=1e-7,
        atol=1e-9,
        err_msg="Angular acceleration mismatch between CPU and GPU with added mass enabled",
    )

    # Load factor (g_load) parity
    assert float(core_gpu["g_load"][0]) == pytest.approx(float(core_cpu["g_load"]), rel=1e-7)


def test_added_mass_attenuation_effect() -> None:
    """Verify that enabling added mass attenuates body normal acceleration and peak stress."""
    env = EnvironmentConfig(wind_speed=0.0)
    launch = LaunchConfig(launch_speed=9.0, launch_angle_deg=0.0, launch_height_m=1.2)
    settings = SimulationSettings(duration_s=1.0)

    # Run without added mass
    ac_off = AircraftConfig(added_mass_enabled=False, apparent_inertia_enabled=False)
    sim_off = flight_sim.FlightSimulator(ac_off, env, launch, settings)
    y0_off = gpu_dynamics.launch_state_vector(launch, env, ac_off)
    core_off = sim_off._compute_dynamics(y0_off, 0.0)

    # Run with added mass
    ac_on = AircraftConfig(added_mass_enabled=True, apparent_inertia_enabled=True)
    sim_on = flight_sim.FlightSimulator(ac_on, env, launch, settings)
    y0_on = gpu_dynamics.launch_state_vector(launch, env, ac_on)
    core_on = sim_on._compute_dynamics(y0_on, 0.0)

    # Normal acceleration magnitude must be attenuated when apparent fluid mass is accounted for
    assert abs(core_on["acceleration"][2]) < abs(core_off["acceleration"][2])
    assert float(core_on["g_load"]) < float(core_off["g_load"])


def test_full_flight_parity_with_added_mass() -> None:
    """Verify that full trajectory simulation with added mass yields consistent metrics on CPU and GPU."""
    aircraft = AircraftConfig(added_mass_enabled=True, apparent_inertia_enabled=True)
    env = EnvironmentConfig(wind_speed=0.0)
    launch = LaunchConfig(launch_speed=8.0, launch_angle_deg=0.0, launch_height_m=1.2)
    settings = SimulationSettings(
        dt_max=2.0e-2,
        dt_min=1.0e-5,
        max_substeps=64,
        duration_s=6.0,
        record_interval_s=2.0e-2,
        time_scale=1.0,
        adaptive=True,
        collision_enabled=True,
        restitution=0.15,
        ring_buffer_capacity=4096,
    )

    sim = flight_sim.FlightSimulator(aircraft, env, launch, settings)
    sim.simulate(duration_s=6.0, record=True)
    ref = flight_sim.summarize_flight(sim)
    samples = sim.history()

    params = gpu_dynamics.build_params(np, [aircraft], env)
    initial = gpu_dynamics.launch_state_vector(launch, env, aircraft)[np.newaxis, :]
    batch = gpu_dynamics.simulate_batch(np, params, initial, 6.0, 2.0e-2)

    # Peak stress agreement
    gpu_stress = float(np.asarray(batch["peak_stress_pa"])[0])
    ref_stress = float(ref["max_stress_pa"])
    assert gpu_stress == pytest.approx(ref_stress, rel=1e-6)

    # Peak load factor agreement
    ref_g = max(float(s.g_load) for s in samples)
    assert float(np.asarray(batch["peak_load_factor"])[0]) == pytest.approx(ref_g, rel=1e-6)

    # Flight time and distance agreement
    assert float(np.asarray(batch["flight_time_s"])[0]) == pytest.approx(float(ref["flight_time_s"]), rel=5.0e-3)
    assert float(np.asarray(batch["distance_m"])[0]) == pytest.approx(float(ref["distance_m"]), rel=6.0e-2)
