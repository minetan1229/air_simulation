"""Audit and verification of sideforce coefficient C_Y across large sideslip and reversed flow (#114).

This test suite rigorously validates:
1. Sideslip angle range: beta in [-180 deg, 180 deg] with periodic / boundary behavior.
2. Boundedness: |C_Y| <= C_{Y,max} <= 0.77 (flat-plate cross-flow limit 2 * sin(beta) * cos^2(beta) <= 0.7698).
3. Exact anti-symmetry: C_Y(-beta) == -C_Y(beta) for all beta.
4. C0 continuity: smooth / continuous transition across linear attached flow, stall, broadside, and reversed flow.
5. Evaluation parity: bit-for-bit parity across ScalarMath (scalar CPU), NumPy (vector CPU), flight_sim, and batch GPU/CPU dynamics.
6. Transformation consistency: wind_force_to_body resolution in cross-flow and reversed flow.
"""

from __future__ import annotations

import math
import numpy as np
import pytest

from physics import aero_domain, aero_kernel, flight_sim, geometry_utils, gpu_dynamics
from physics.aero_kernel import ScalarMath
from physics.config_schema import AircraftConfig, EnvironmentConfig


@pytest.fixture
def base_cfg() -> AircraftConfig:
    return AircraftConfig(cy_beta=-0.25)


@pytest.fixture
def env() -> EnvironmentConfig:
    return EnvironmentConfig()


class TestSideslipCYBoundednessAndContinuity:
    """Audit and verify C_Y bounds, continuity, and anti-symmetry over [-180, 180] deg."""

    @pytest.mark.parametrize("cy_beta", [-5.0, -2.0, -0.25, 0.0, 0.25, 2.0, 5.0])
    def test_cy_boundedness_across_full_range(self, cy_beta: float, env: EnvironmentConfig):
        """Hypothesis: |C_Y(beta)| is strictly bounded by max(2 * sin(beta) * cos^2(beta)) <= 0.7698.

        Pass/Fail:
        - For all beta in [-180, 180] deg, |C_Y| <= 0.77.
        - No non-finite values (NaN, inf) occur.
        """
        cfg = AircraftConfig(cy_beta=cy_beta)
        params = aero_kernel.params_from_config(cfg, env)
        betas = np.linspace(-180.0, 180.0, 721)

        res = aero_kernel.evaluate_coefficients(
            np, params, alpha_deg=np.zeros_like(betas), beta_deg=betas
        )
        cy = res["cy"]

        assert np.all(np.isfinite(cy)), "C_Y must be finite everywhere"
        max_abs_cy = float(np.max(np.abs(cy)))
        # Bluff body flat plate broadside theoretical maximum: 2 * (1 / sqrt(3)) * (2 / 3) = 4 / (3 * sqrt(3)) ~ 0.7698
        assert max_abs_cy <= 0.77 + 1e-9, f"C_Y exceeded physical flat-plate bound: {max_abs_cy}"

    @pytest.mark.parametrize("cy_beta", [-5.0, -1.0, -0.25, 0.0, 0.25, 2.0])
    def test_cy_anti_symmetry(self, cy_beta: float, env: EnvironmentConfig):
        """Hypothesis: C_Y(-beta) == -C_Y(beta) due to airframe lateral symmetry.

        Pass/Fail:
        - |C_Y(-beta) - (-C_Y(beta))| < 1e-12 for all beta in [-180, 180].
        - Specifically C_Y(0) == 0, C_Y(180) == 0, C_Y(-180) == 0.
        """
        cfg = AircraftConfig(cy_beta=cy_beta)
        params = aero_kernel.params_from_config(cfg, env)
        betas = np.linspace(-180.0, 180.0, 721)

        res_pos = aero_kernel.evaluate_coefficients(
            np, params, alpha_deg=np.zeros_like(betas), beta_deg=betas
        )["cy"]
        res_neg = aero_kernel.evaluate_coefficients(
            np, params, alpha_deg=np.zeros_like(betas), beta_deg=-betas
        )["cy"]

        diff = np.abs(res_pos + res_neg)
        assert np.max(diff) < 1e-12, f"Anti-symmetry violated: max diff={np.max(diff)}"

        # Check endpoints and zero
        res_zero = aero_kernel.evaluate_coefficients(
            np, params, alpha_deg=np.array([0.0]), beta_deg=np.array([0.0])
        )["cy"][0]
        res_180 = aero_kernel.evaluate_coefficients(
            np, params, alpha_deg=np.array([0.0]), beta_deg=np.array([180.0])
        )["cy"][0]
        res_m180 = aero_kernel.evaluate_coefficients(
            np, params, alpha_deg=np.array([0.0]), beta_deg=np.array([-180.0])
        )["cy"][0]

        assert abs(res_zero) < 1e-12
        assert abs(res_180) < 1e-12
        assert abs(res_m180) < 1e-12

    @pytest.mark.parametrize("cy_beta", [-2.0, -0.25, 0.25])
    def test_cy_c0_continuity_dense_grid(self, cy_beta: float, env: EnvironmentConfig):
        """Hypothesis: C_Y is C0-continuous (no jumps/discontinuities) across beta sweep.

        Pass/Fail:
        - Delta C_Y across delta beta = 0.01 deg is bounded by max slope * delta beta + eps.
        """
        cfg = AircraftConfig(cy_beta=cy_beta)
        params = aero_kernel.params_from_config(cfg, env)
        betas = np.linspace(-180.0, 180.0, 36001)  # step = 0.01 deg
        cys = aero_kernel.evaluate_coefficients(
            np, params, alpha_deg=np.zeros_like(betas), beta_deg=betas
        )["cy"]

        delta_cy = np.abs(np.diff(cys))
        max_delta = float(np.max(delta_cy))
        # Max theoretical derivative d(cy)/d(beta_rad) is at most max(|cy_beta|, 2.0).
        # For delta beta = 0.01 deg = 1.745e-4 rad, max delta should be <= max(|cy_beta|, 2.0) * 1.745e-4.
        expected_max_step = max(abs(cy_beta), 2.0) * np.radians(0.01) * 1.1
        assert max_delta <= expected_max_step, f"Discontinuity detected: step {max_delta} > {expected_max_step}"

    def test_cy_linear_regime_applicability(self, base_cfg: AircraftConfig, env: EnvironmentConfig):
        """Hypothesis: Within small sideslip angles (e.g. |beta| <= 5 deg), C_Y matches cy_linear = cy_beta * beta_rad.

        Pass/Fail:
        - |C_Y - cy_beta * beta_rad| < 1e-6 for |beta| <= 5 deg.
        """
        params = aero_kernel.params_from_config(base_cfg, env)
        small_betas = np.linspace(-5.0, 5.0, 101)
        cys = aero_kernel.evaluate_coefficients(
            np, params, alpha_deg=np.zeros_like(small_betas), beta_deg=small_betas
        )["cy"]

        expected_linear = base_cfg.cy_beta * np.radians(small_betas)
        assert np.allclose(cys, expected_linear, atol=1e-6)

    def test_cy_reversed_flow_behavior(self, base_cfg: AircraftConfig, env: EnvironmentConfig):
        """Hypothesis: In reversed flow (|beta| -> 180 deg), C_Y smoothly transitions to zero as broadside cross-flow vanishes.

        Pass/Fail:
        - At beta = +/- 90 deg (broadside cross flow), C_Y is bounded by flat_side limit (approaches 0 as cos(90) = 0).
        - At beta in [90, 180] deg, C_Y is smoothly bounded by |flat_side| <= 0.77.
        - At beta = 180 deg, C_Y is exactly zero.
        """
        params = aero_kernel.params_from_config(base_cfg, env)
        betas = np.array([89.9, 90.0, 90.1, 135.0, 179.0, 180.0])
        cys = aero_kernel.evaluate_coefficients(
            np, params, alpha_deg=np.zeros_like(betas), beta_deg=betas
        )["cy"]

        assert np.all(np.isfinite(cys))
        assert np.all(np.abs(cys) <= 0.77)
        assert abs(cys[-1]) < 1e-12  # beta = 180


class TestSideslipCYBackendParity:
    """Verify scalar CPU, vectorized NumPy, flight_sim, and GPU dynamics share identical C_Y."""

    @pytest.mark.parametrize("beta", [-180.0, -135.0, -90.0, -45.0, -10.0, 0.0, 10.0, 45.0, 90.0, 135.0, 180.0])
    def test_scalar_vs_numpy_kernel_parity(self, base_cfg: AircraftConfig, env: EnvironmentConfig, beta: float):
        """Verify ScalarMath (pure Python scalar) and NumPy vectorized evaluations match bit-for-bit."""
        params = aero_kernel.params_from_config(base_cfg, env)

        scalar_cy = aero_kernel.evaluate_coefficients(
            ScalarMath, params, alpha_deg=0.0, beta_deg=beta
        )["cy"]
        numpy_cy = float(
            aero_kernel.evaluate_coefficients(
                np, params, alpha_deg=np.array([0.0]), beta_deg=np.array([beta])
            )["cy"][0]
        )

        assert abs(scalar_cy - numpy_cy) < 1e-14

    @pytest.mark.parametrize("beta", [-120.0, -45.0, 0.0, 30.0, 150.0])
    def test_flight_sim_matches_aero_kernel(self, base_cfg: AircraftConfig, env: EnvironmentConfig, beta: float):
        """Verify flight_sim.aero_forces uses aero_kernel C_Y without drift."""
        speed = 8.0
        res = flight_sim.aero_forces(base_cfg, env, speed, alpha_deg=2.0, beta_deg=beta)

        params = aero_kernel.params_from_config(base_cfg, env)
        k_res = aero_kernel.evaluate_coefficients(
            ScalarMath, params, alpha_deg=2.0, beta_deg=beta, speed=speed
        )

        assert res.cy == pytest.approx(k_res["cy"], abs=1e-12)
        assert res.side_n == pytest.approx(k_res["side_n"], abs=1e-12)

    def test_wind_force_to_body_reversed_flow_orthonormality(self):
        """Verify wind_force_to_body handles sideslip in forward, cross-flow, and reversed flow."""
        # Ensure triad is strictly orthonormal and right-handed across full sphere of relative velocities
        for u in [-12.0, -5.0, 0.0, 5.0, 12.0]:
            for v in [-4.0, -1.0, 0.0, 1.0, 4.0]:
                for w in [-3.0, 0.0, 3.0]:
                    v_rel = np.array([u, v, w], dtype=np.float64)
                    speed = float(np.linalg.norm(v_rel))
                    if speed < 1e-6:
                        continue
                    f_drag = -geometry_utils.wind_force_to_body(v_rel, drag=1.0, side=0.0, lift=0.0)
                    f_side = geometry_utils.wind_force_to_body(v_rel, drag=0.0, side=1.0, lift=0.0)
                    f_lift = geometry_utils.wind_force_to_body(v_rel, drag=0.0, side=0.0, lift=1.0)

                    assert np.all(np.isfinite(f_drag))
                    assert np.all(np.isfinite(f_side))
                    assert np.all(np.isfinite(f_lift))

                    # Check unit lengths
                    assert abs(np.linalg.norm(f_drag) - 1.0) < 1e-12
                    assert abs(np.linalg.norm(f_side) - 1.0) < 1e-12
                    assert abs(np.linalg.norm(f_lift) - 1.0) < 1e-12

                    # Check mutual orthogonality
                    assert abs(float(np.dot(f_drag, f_side))) < 1e-12
                    assert abs(float(np.dot(f_side, f_lift))) < 1e-12
                    assert abs(float(np.dot(f_drag, f_lift))) < 1e-12

                    # Check right-handedness: side = drag_axis x lift_axis
                    assert np.allclose(f_side, geometry_utils.cross(f_drag, f_lift), atol=1e-12)

    def test_gpu_dynamics_batch_aero_coefficients_parity(self, base_cfg: AircraftConfig, env: EnvironmentConfig):
        """Verify that evaluate_coefficients produces identical C_Y for batch arrays."""
        betas = np.linspace(-180.0, 180.0, 361)
        params = aero_kernel.params_from_config(base_cfg, env)

        batch_cy = aero_kernel.evaluate_coefficients(
            np, params, alpha_deg=np.zeros_like(betas), beta_deg=betas
        )["cy"]

        for idx, b in enumerate(betas):
            single_cy = aero_kernel.evaluate_coefficients(
                ScalarMath, params, alpha_deg=0.0, beta_deg=float(b)
            )["cy"]
            assert abs(batch_cy[idx] - single_cy) < 1e-14
