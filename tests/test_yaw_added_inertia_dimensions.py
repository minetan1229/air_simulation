"""Tests for Issue #84: Yaw added-inertia term physical dimensions and tensor consistency.

Issue #84 identifies that in apparent inertia calculation, the yaw term was originally written as:
I_add_zz ~ rho * h_keel * c^3 with dimensions [kg/m^3] * [m] * [m^3] = [kg m], which is inconsistent
with moment of inertia [kg m^2].

Physical Formulation:
The physical added yaw inertia of a vertical fin/keel of height h_keel and chord c rotating about body normal
z-axis scales as:
    I_add_zz = (pi / 128) * rho * h_keel * c^4
with exact dimensions [kg/m^3] * [m] * [m^4] = [kg m^2].

Verification Criteria:
1. Dimensional scaling:
   - For an affine scale factor L -> alpha * L (S -> alpha^2 S, b -> alpha b, c -> alpha c, h_keel -> alpha h_keel):
     rho * h_keel * c^4 scales as alpha^5.
     If alpha = 2, I_add_zz must scale by 2^5 = 32x.
     This matches the dimensional order of I_add_xx (~ rho * b^3 * c^2 ~ L^5) and I_add_yy (~ rho * S * c^3 ~ L^5).
2. Effective Inertia Tensor Properties:
   - Positive-definiteness of added inertia: I_add_zz > 0.
   - Effective inertia J_eff = J_body + diag(I_add_xx, I_add_yy, I_add_zz) is symmetric positive-definite.
   - All eigenvalues of J_eff are strictly positive, satisfying Cholesky decomposition J_eff = L L^T.
   - Triangle inequalities for moments of inertia:
     For any rigid/effective body, J_xx + J_yy >= J_zz, J_yy + J_zz >= J_xx, J_zz + J_xx >= J_yy.
3. CPU vs GPU Dynamics Parity:
   - Yaw angular acceleration response (alpha_z / r_dot) under active apparent inertia matches between
     CPU (FlightSimulator) and GPU (gpu_dynamics._evaluate_state) within bit-accurate or near bit-accurate
     tolerances (rel <= 1e-6).
4. Physical Dynamic Attenuation:
   - Under active apparent inertia, effective yaw inertia J_eff_zz = J_zz + I_add_zz > J_zz attenuates
     yaw angular acceleration |r_dot| for a given aerodynamic yawing moment.
"""
from __future__ import annotations

import math
import numpy as np
import pytest

from physics import aero_kernel, flight_sim, gpu_dynamics
from physics.aero_kernel import ScalarMath
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig, SimulationSettings


def _quat_mult(q1: np.ndarray, q2: np.ndarray) -> np.ndarray:
    w1, x1, y1, z1 = q1
    w2, x2, y2, z2 = q2
    return np.array([
        w1*w2 - x1*x2 - y1*y2 - z1*z2,
        w1*x2 + x1*w2 + y1*z2 - z1*y2,
        w1*y2 - x1*z2 + y1*w2 + z1*x2,
        w1*z2 + x1*y2 - y1*x2 + z1*w2,
    ], dtype=np.float64)


def test_yaw_added_inertia_dimensional_scaling_order_5() -> None:
    """Verify yaw added inertia scales as L^5 [kg m^2] under geometric scaling."""
    rho = 1.225
    s0 = 0.018
    b0 = 0.22
    c0 = 0.0826

    base = aero_kernel.compute_added_mass(ScalarMath, rho, s0, c0, wingspan_m=b0)
    # Scale all linear dimensions by factor of 2.0
    scale = 2.0
    scaled = aero_kernel.compute_added_mass(
        ScalarMath,
        rho,
        s0 * (scale**2),
        c0 * scale,
        wingspan_m=b0 * scale,
    )

    ratio_m = scaled["added_mass_z_kg"] / base["added_mass_z_kg"]
    ratio_ixx = scaled["added_inertia_xx_kgm2"] / base["added_inertia_xx_kgm2"]
    ratio_iyy = scaled["added_inertia_yy_kgm2"] / base["added_inertia_yy_kgm2"]
    ratio_izz = scaled["added_inertia_zz_kgm2"] / base["added_inertia_zz_kgm2"]

    # Mass scales as L^3 => 2^3 = 8
    assert np.isclose(ratio_m, 8.0, rtol=1e-9), f"m_z ratio was {ratio_m}, expected 8.0"
    # Moments of inertia scale as mass * length^2 ~ L^5 => 2^5 = 32
    assert np.isclose(ratio_ixx, 32.0, rtol=1e-9), f"I_xx ratio was {ratio_ixx}, expected 32.0"
    assert np.isclose(ratio_iyy, 32.0, rtol=1e-9), f"I_yy ratio was {ratio_iyy}, expected 32.0"
    assert np.isclose(ratio_izz, 32.0, rtol=1e-9), f"I_zz ratio was {ratio_izz}, expected 32.0 (L^5)"


def test_yaw_added_inertia_exact_formula() -> None:
    """Verify yaw added inertia matches (pi / 128) * rho * h_keel * c^4 exactly."""
    rho = 1.204
    area = 0.025
    mac = 0.09
    span = 0.28

    added = aero_kernel.compute_added_mass(ScalarMath, rho, area, mac, wingspan_m=span)
    h_keel = max(span * 0.08, 0.01)
    expected_izz = (math.pi / 128.0) * rho * h_keel * (mac**4)

    assert math.isclose(added["added_inertia_zz_kgm2"], expected_izz, rel_tol=1e-12)
    assert added["added_inertia_zz_kgm2"] > 0.0


def test_effective_inertia_tensor_positive_definite_and_triangle_inequality() -> None:
    """Verify effective inertia tensor J_eff is symmetric positive-definite and obeys triangle inequalities."""
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

    # Symmetry
    assert np.allclose(eff_mat, eff_mat.T, atol=1e-15)

    # Strictly positive eigenvalues
    eigvals = np.linalg.eigvalsh(eff_mat)
    assert np.all(eigvals > 0.0), f"Eigenvalues must be positive: {eigvals}"

    # Cholesky decomposition succeeds
    chol = np.linalg.cholesky(eff_mat)
    assert np.allclose(chol @ chol.T, eff_mat, atol=1e-15)

    # Principal moments (eigenvalues of inertia tensor) must satisfy triangle inequalities:
    # A + B >= C, B + C >= A, C + A >= B
    i1, i2, i3 = sorted(eigvals)
    assert i1 + i2 >= i3, f"Triangle inequality violated: {i1} + {i2} < {i3}"
    assert i2 + i3 >= i1
    assert i1 + i3 >= i2


def test_yaw_acceleration_cpu_gpu_parity_with_apparent_inertia() -> None:
    """Verify CPU (flight_sim) and GPU (gpu_dynamics) yaw angular acceleration parity under active apparent inertia."""
    aircraft = AircraftConfig(
        added_mass_enabled=True,
        apparent_inertia_enabled=True,
        dihedral_deg=3.0,
    )
    env = EnvironmentConfig(wind_speed=0.0)
    launch = LaunchConfig(launch_speed=8.0, launch_angle_deg=5.0, launch_height_m=1.2)
    settings = SimulationSettings()

    sim = flight_sim.FlightSimulator(aircraft, env, launch, settings)
    y0 = gpu_dynamics.launch_state_vector(launch, env, aircraft)

    # Apply 10 deg yaw rotation to generate aerodynamic sideslip beta and yaw moment
    psi = np.radians(10.0)
    q_yaw = np.array([np.cos(psi / 2.0), 0.0, 0.0, np.sin(psi / 2.0)], dtype=np.float64)
    y0[6:10] = _quat_mult(q_yaw, y0[6:10])

    params = gpu_dynamics.build_params(np, [aircraft], env)
    w_gpu = gpu_dynamics.wind_batch(np, params, y0[:3][np.newaxis, :], 0.0)

    core_cpu = sim._compute_dynamics(y0, 0.0)
    core_gpu = gpu_dynamics._evaluate_state(np, params, y0[np.newaxis, :], w_gpu)

    ang_acc_cpu = np.array(core_cpu["angular_acceleration"], dtype=np.float64)
    ang_acc_gpu = np.array(core_gpu["angular_acceleration"][0], dtype=np.float64)

    # Sideslip beta parity
    assert np.isclose(float(core_cpu["beta_deg"]), float(core_gpu["beta"][0]), rtol=1e-7)

    # Yaw angular acceleration (index 2) parity
    assert np.isclose(ang_acc_cpu[2], ang_acc_gpu[2], rtol=1e-6, atol=1e-8), (
        f"Yaw angular acceleration mismatch: CPU={ang_acc_cpu[2]}, GPU={ang_acc_gpu[2]}"
    )

    # Full angular acceleration vector parity
    np.testing.assert_allclose(ang_acc_cpu, ang_acc_gpu, rtol=1e-6, atol=1e-8)


def test_apparent_yaw_inertia_attenuates_yaw_acceleration() -> None:
    """Verify that added yaw inertia attenuates yaw angular acceleration |r_dot| for an applied yaw moment."""
    env = EnvironmentConfig(wind_speed=0.0)
    launch = LaunchConfig(launch_speed=8.0, launch_angle_deg=0.0, launch_height_m=1.2)
    settings = SimulationSettings()

    ac_off = AircraftConfig(added_mass_enabled=False, apparent_inertia_enabled=False, dihedral_deg=3.0)
    ac_on = AircraftConfig(added_mass_enabled=True, apparent_inertia_enabled=True, dihedral_deg=3.0)

    sim_off = flight_sim.FlightSimulator(ac_off, env, launch, settings)
    sim_on = flight_sim.FlightSimulator(ac_on, env, launch, settings)

    y0 = gpu_dynamics.launch_state_vector(launch, env, ac_off)
    psi = np.radians(10.0)
    q_yaw = np.array([np.cos(psi / 2.0), 0.0, 0.0, np.sin(psi / 2.0)], dtype=np.float64)
    y0[6:10] = _quat_mult(q_yaw, y0[6:10])

    core_off = sim_off._compute_dynamics(y0, 0.0)
    core_on = sim_on._compute_dynamics(y0, 0.0)

    r_dot_off = abs(float(core_off["angular_acceleration"][2]))
    r_dot_on = abs(float(core_on["angular_acceleration"][2]))

    assert r_dot_off > 0.0, "Must have non-zero yaw acceleration under sideslip"
    assert r_dot_on < r_dot_off, (
        f"Added yaw inertia must attenuate yaw acceleration: on={r_dot_on} >= off={r_dot_off}"
    )
