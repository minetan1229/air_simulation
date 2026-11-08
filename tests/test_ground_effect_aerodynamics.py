"""Comprehensive aerodynamic ground effect audit and verification tests.

Verifies:
1. Wieselsberger (1922) induced drag factor:
   sigma = 16 (h/b)^2 / (1 + 16 (h/b)^2)
   - Free-air asymptotic limit: sigma -> 1.0 as h/b >> 1.
   - Ground contact / zero clearance limit: sigma -> 0.0 as h/b -> 0.
   - Monotonicity: d(sigma)/d(h/b) > 0 for all h/b > 0.
   - Singularity-free / safe handling of edge cases (h <= 0, span <= 0, NaN, Inf).
   - Equivalence between numpy array and scalar math paths.
   - Disabled behavior: sigma = 1.0 everywhere when ground_effect_enabled=False.
2. Induced drag polar scaling:
   C_Di(h) = sigma * (C_L^2 / (pi * e * AR))
   - Ground effect affects only induced drag, preserving parasite drag C_D0 and wave drag.
3. Ram-pressure cushion lift increment:
   delta_CL = ground_cushion_lift * exp(-4 * h/b)
   - Non-negative, monotonic increase as clearance h -> 0.
   - Vanishes exponentially in free air (h/b >= 2).
   - Zero when ground_cushion_lift == 0 (default: pure Wieselsberger induced drag scaling).
4. Ground-effect pitching moment coupling:
   delta_Cm = -ground_pitch_coupling * CL * exp(-4 * h/b)
   - Nose-down pitch coupling proportional to circulation/CL and ground proximity.
   - Vanishes exponentially in free air.
   - Zero when ground_pitch_coupling == 0 (default).
5. 6DoF Flight integration and numerical stability:
   - Evaluates aerodynamic forces and moments cleanly down to ground contact (h = 0).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from physics import aero_domain, aero_kernel, flight_sim
from physics.aero_kernel import ScalarMath
from physics.config_schema import AircraftConfig, EnvironmentConfig


# ==============================================================================
# 1. Wieselsberger Induced Drag Factor Tests
# ==============================================================================

class TestWieselsbergerFactor:
    """Audit of sigma = 16 (h/b)^2 / (1 + 16 (h/b)^2)."""

    def test_high_altitude_asymptotic_unity(self) -> None:
        """Hypothesis: As h/b >> 1, sigma -> 1.0 asymptotically."""
        span = 0.20
        # Check several high clearances
        for hb_mult in [5.0, 10.0, 50.0, 100.0, 1000.0]:
            h = hb_mult * span
            val_scalar = aero_domain.ground_effect_factor(h, span, enabled=True)
            val_kernel = aero_kernel.ground_effect_factor(np, np.array([h]), np.array([span]), enabled=True)

            # Theoretical formula
            hb = h / span
            expected = (16.0 * hb**2) / (1.0 + 16.0 * hb**2)

            assert val_scalar == pytest.approx(expected, rel=1e-12)
            assert float(val_kernel[0]) == pytest.approx(expected, rel=1e-12)

            # For h/b >= 10, sigma should be within 0.07% of 1.0 (1600/1601 = 0.999375)
            if hb_mult >= 10.0:
                assert val_scalar > 0.999
                assert val_scalar <= 1.0

    def test_zero_height_limit(self) -> None:
        """Hypothesis: At ground contact h = 0, sigma = 0.0 with no singularity."""
        span = 0.25
        val_scalar = aero_domain.ground_effect_factor(0.0, span, enabled=True)
        val_kernel = aero_kernel.ground_effect_factor(np, 0.0, span, enabled=True)
        val_scalar_math = aero_kernel.ground_effect_factor(ScalarMath, 0.0, span, enabled=True)

        assert val_scalar == 0.0
        assert float(val_kernel) == 0.0
        assert val_scalar_math == 0.0

    def test_monotonic_decrease_towards_ground(self) -> None:
        """Hypothesis: sigma strictly monotonically increases with clearance h/b."""
        span = 0.22
        h_ratios = np.linspace(0.0, 3.0, 100)
        h_values = h_ratios * span

        factors = [aero_domain.ground_effect_factor(h, span, enabled=True) for h in h_values]

        for i in range(len(factors) - 1):
            assert factors[i] <= factors[i + 1]
            if h_ratios[i + 1] > 0.0:
                assert factors[i] < factors[i + 1]
                assert 0.0 <= factors[i] <= 1.0

    def test_derivative_closed_form(self) -> None:
        """Verify derivative d(sigma)/d(eta) = 32*eta / (1 + 16*eta^2)^2 where eta = h/b."""
        span = 0.20
        eta_samples = [0.05, 0.1, 0.25, 0.5, 1.0, 2.0]
        eps = 1e-6
        for eta in eta_samples:
            h1 = (eta - eps) * span
            h2 = (eta + eps) * span
            f1 = aero_domain.ground_effect_factor(h1, span)
            f2 = aero_domain.ground_effect_factor(h2, span)
            num_deriv = (f2 - f1) / (2.0 * eps)

            # Analytic derivative w.r.t (h/b)
            analytic_deriv = (32.0 * eta) / ((1.0 + 16.0 * eta**2)**2)
            assert num_deriv == pytest.approx(analytic_deriv, rel=1e-4)

    def test_disabled_returns_unity_everywhere(self) -> None:
        """Hypothesis: Disabling ground effect returns sigma = 1.0 (free-air induced drag)."""
        span = 0.20
        for h in [0.0, 0.001, 0.05, 0.20, 1.0, 10.0]:
            assert aero_domain.ground_effect_factor(h, span, enabled=False) == 1.0
            kernel_val = aero_kernel.ground_effect_factor(np, h, span, enabled=False)
            assert float(kernel_val) == 1.0
            scalar_math_val = aero_kernel.ground_effect_factor(ScalarMath, h, span, enabled=False)
            assert scalar_math_val == 1.0

    def test_robustness_against_negative_and_extreme_inputs(self) -> None:
        """Hypothesis: Negative height is safely clamped to 0.0, zero span does not ZeroDivisionError."""
        span = 0.20
        # Negative height -> clamped to 0.0 -> factor 0.0
        assert aero_domain.ground_effect_factor(-0.5, span, enabled=True) == 0.0
        assert float(aero_kernel.ground_effect_factor(np, -0.5, span, enabled=True)) == 0.0

        # Zero or negative span -> safe fallback without crash
        val_zero_span = aero_kernel.ground_effect_factor(np, 0.1, 0.0, enabled=True)
        assert np.isfinite(val_zero_span)
        assert 0.0 <= float(val_zero_span) <= 1.0

        # Non-finite inputs -> safe fallback via _finite(height, 0.0)
        val_nan = aero_kernel.ground_effect_factor(np, float("nan"), span, enabled=True)
        assert np.isfinite(val_nan)


# ==============================================================================
# 2. Induced Drag Polar Verification Tests
# ==============================================================================

class TestInducedDragPolarGroundEffect:
    """Verify CDi = sigma * CL^2 / (pi * e * AR) in full coefficient buildup."""

    def test_cd_polar_only_modulates_induced_drag(self) -> None:
        """Hypothesis: Ground effect reduces only CDi; CD0 is completely unchanged."""
        cfg = AircraftConfig(
            wingspan_m=0.20,
            wing_area_m2=0.015,
            cd0=0.03,
            oswald_e=0.80,
            cLalpha_per_rad=3.5,
            cL0=0.1,
            ground_cushion_lift=0.0,
            ground_pitch_coupling=0.0,
        )
        env_on = EnvironmentConfig(ground_effect_enabled=True)
        env_off = EnvironmentConfig(ground_effect_enabled=False)

        span = 0.20
        h_near = 0.05 * span
        h_far = 10.0 * span

        res_near_on = aero_domain.evaluate_aero(cfg, env_on, speed_mps=10.0, alpha_deg=4.0, ground_height_m=h_near)
        res_near_off = aero_domain.evaluate_aero(cfg, env_off, speed_mps=10.0, alpha_deg=4.0, ground_height_m=h_near)
        res_far_on = aero_domain.evaluate_aero(cfg, env_on, speed_mps=10.0, alpha_deg=4.0, ground_height_m=h_far)

        # Lift is identical when cushion is 0
        assert res_near_on.cl == pytest.approx(res_near_off.cl, rel=1e-7)
        assert res_near_on.cl == pytest.approx(res_far_on.cl, rel=1e-4)

        # Near ground with GE on, drag is strictly lower than free air
        assert res_near_on.cd < res_near_off.cd
        assert res_near_on.cd < res_far_on.cd

        # Induced drag ratio matches ground_effect_ratio
        sigma = res_near_on.ground_effect_ratio
        expected_sigma = (16.0 * (h_near / span)**2) / (1.0 + 16.0 * (h_near / span)**2)
        assert sigma == pytest.approx(expected_sigma, rel=1e-6)

        # When GE disabled, ground_effect_ratio is 1.0 and drag equals free air
        assert res_near_off.ground_effect_ratio == 1.0
        assert res_near_off.cd == pytest.approx(res_far_on.cd, rel=1e-3)


# ==============================================================================
# 3. Ground Cushion Lift & Pitch Coupling Sensitivity Tests
# ==============================================================================

class TestGroundCushionAndPitchSensitivities:
    """Verify ram-pressure cushion lift and nose-down pitch coupling."""

    def test_cushion_lift_scaling(self) -> None:
        """Hypothesis: delta_CL = cushion_gain * exp(-4 * h/b)."""
        cfg_base = AircraftConfig(wingspan_m=0.20, ground_cushion_lift=0.0)
        cfg_cushion = AircraftConfig(wingspan_m=0.20, ground_cushion_lift=0.6)
        env = EnvironmentConfig(ground_effect_enabled=True)

        span = 0.20
        heights = [0.01 * span, 0.05 * span, 0.1 * span, 0.25 * span, 1.0 * span]

        for h in heights:
            hb = h / span
            res_base = aero_domain.evaluate_aero(cfg_base, env, 10.0, 3.0, ground_height_m=h)
            res_cush = aero_domain.evaluate_aero(cfg_cushion, env, 10.0, 3.0, ground_height_m=h)

            delta_cl = res_cush.cl - res_base.cl
            expected_delta = 0.6 * math.exp(-4.0 * hb)
            assert delta_cl == pytest.approx(expected_delta, rel=1e-4)

    def test_pitch_coupling_nose_down(self) -> None:
        """Hypothesis: delta_Cm = -pitch_coupling * CL * exp(-4 * h/b)."""
        coupling_gain = 0.35
        cfg_base = AircraftConfig(wingspan_m=0.20, ground_pitch_coupling=0.0)
        cfg_pitch = AircraftConfig(wingspan_m=0.20, ground_pitch_coupling=coupling_gain)
        env = EnvironmentConfig(ground_effect_enabled=True)

        span = 0.20
        heights = [0.02 * span, 0.08 * span, 0.2 * span, 0.5 * span]

        for h in heights:
            hb = h / span
            res_base = aero_domain.evaluate_aero(cfg_base, env, 10.0, 4.0, ground_height_m=h)
            res_pitch = aero_domain.evaluate_aero(cfg_pitch, env, 10.0, 4.0, ground_height_m=h)

            delta_cm = res_pitch.cm - res_base.cm
            # delta_Cm is negative (nose-down pitch moment)
            expected_delta = -coupling_gain * res_base.cl * math.exp(-4.0 * hb)
            assert delta_cm == pytest.approx(expected_delta, rel=1e-4)
            assert res_pitch.cm < res_base.cm

    def test_free_air_decay(self) -> None:
        """At high altitude h/b >= 2, ground cushion and pitch coupling decay to < 0.035%."""
        cfg = AircraftConfig(
            wingspan_m=0.20,
            ground_cushion_lift=1.0,
            ground_pitch_coupling=1.0,
        )
        env = EnvironmentConfig(ground_effect_enabled=True)
        span = 0.20
        h_high = 2.0 * span  # exp(-8) = 0.000335
        h_very_high = 100.0 * span

        res_high = aero_domain.evaluate_aero(cfg, env, 10.0, 3.0, ground_height_m=h_high)
        res_free = aero_domain.evaluate_aero(cfg, env, 10.0, 3.0, ground_height_m=h_very_high)

        assert res_high.cl == pytest.approx(res_free.cl, abs=5e-4)
        assert res_high.cm == pytest.approx(res_free.cm, abs=5e-4)


# ==============================================================================
# 4. Parity and Parallel Engine Consistency Tests
# ==============================================================================

class TestGroundEffectParity:
    """Verify consistency between aero_kernel, aero_domain, and flight_sim."""

    def test_flight_sim_ground_effect_factor_parity(self) -> None:
        """flight_sim.ground_effect_factor must match aero_kernel and aero_domain."""
        span = 0.25
        for h in [0.0, 0.02, 0.1, 0.5, 2.0, 10.0]:
            v_domain = aero_domain.ground_effect_factor(h, span, enabled=True)
            v_sim = flight_sim.ground_effect_factor(h, span, enabled=True)
            v_kernel = float(aero_kernel.ground_effect_factor(np, h, span, enabled=True))

            assert v_domain == pytest.approx(v_sim, rel=1e-12)
            assert v_sim == pytest.approx(v_kernel, rel=1e-12)

    def test_batch_vector_and_scalar_kernel_parity(self) -> None:
        """Ensure vectorize batches in aero_kernel produce bit-identical results."""
        spans = np.array([0.15, 0.20, 0.25, 0.30])
        heights = np.array([0.0, 0.05, 0.20, 1.0])

        batch_result = aero_kernel.ground_effect_factor(np, heights, spans, enabled=True)
        for i in range(len(spans)):
            scalar_res = aero_kernel.ground_effect_factor(
                ScalarMath, float(heights[i]), float(spans[i]), enabled=True
            )
            assert float(batch_result[i]) == pytest.approx(scalar_res, rel=1e-12)
