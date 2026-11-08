"""Comprehensive evaluation and audit of low-Reynolds number aerodynamics (Issue #111).

Evaluates:
- Reynolds number dependence of lift curve slope dCL/dalpha, CLmax, and stall angle across Re in [1e4, 1e5].
- Helmbold linear attached lift slope vs flat plate stall and LSB (laminar separation bubble) effects.
- Verification of uncertainty/envelope diagnostics without inventing unverified empirical constants.
- Parity between NumPy and ScalarMath backends for low-Re evaluations.
"""
from __future__ import annotations

import math
import numpy as np
import pytest

from physics import aero_domain, aero_kernel
from physics.aero_kernel import (
    AeroParams,
    ScalarMath,
    helmbold_cl_alpha,
    flat_plate_lift_coefficient,
    post_stall_lift_coefficient,
    stall_blend_weight,
    evaluate_coefficients,
    evaluate_model_validity,
    lsb_dynamic_stall_shift,
    lsb_dynamic_stall_hysteresis,
    low_reynolds_aerodynamics_audit,
    MODEL_REYNOLDS_MIN,
    MODEL_REYNOLDS_MAX,
    POST_STALL_PLATE_BLEND_DEG,
)
from physics.config_schema import AircraftConfig, EnvironmentConfig


class TestLowReynoldsAerodynamicsEnvelope:
    """Audit of Reynolds number envelope bounds and validity diagnostics."""

    @pytest.mark.parametrize("re", [500.0, 1.0e3, 5.0e3, 1.0e4, 5.0e4, 1.0e5, 2.0e5])
    def test_reynolds_validity_flags(self, re: float):
        """Verify that evaluate_model_validity accurately tags the [1e3, 1e5] envelope."""
        val = evaluate_model_validity(np, mach=0.02, reynolds=re)
        expected_ok = bool(MODEL_REYNOLDS_MIN <= re <= MODEL_REYNOLDS_MAX)
        assert bool(val["re_ok"]) == expected_ok
        assert bool(val["valid"]) == expected_ok

    def test_flight_envelope_bounds_documented(self):
        """Paper aircraft flight envelope spans Re in [1e4, 1e5]. Verify calibration bounds."""
        # For typical paper aircraft: U = 3 - 15 m/s, c = 0.05 - 0.15 m, nu = 1.5e-5 m^2/s
        # Re_min = 3.0 * 0.05 / 1.5e-5 = 10,000
        # Re_max = 15.0 * 0.15 / 1.5e-5 = 150,000
        re_low = 3.0 * 0.05 / 1.5e-5
        re_high = 15.0 * 0.15 / 1.5e-5
        assert 1.0e4 <= re_low <= 1.5e4
        assert 1.0e5 <= re_high <= 1.6e5
        # Kernel envelope limits encompass this operational regime
        assert MODEL_REYNOLDS_MIN <= 1.0e4
        assert MODEL_REYNOLDS_MAX >= 1.0e5


class TestHelmboldAndAttachedLiftSlope:
    """Audit of Helmbold lift slope across aspect ratios and Re independence in static flow."""

    @pytest.mark.parametrize("aspect_ratio,taper", [(1.5, 0.5), (2.5, 0.7), (4.0, 1.0)])
    def test_helmbold_formula_bounds(self, aspect_ratio: float, taper: float):
        """Helmbold attached lift slope must be strictly positive and strictly less than 2*pi."""
        slope = helmbold_cl_alpha(aspect_ratio, taper)
        assert 0.0 < slope < 2.0 * math.pi
        # As aspect ratio increases, slope should increase towards 2*pi
        higher_ar_slope = helmbold_cl_alpha(aspect_ratio * 1.5, taper)
        assert higher_ar_slope > slope

    def test_attached_lift_slope_re_invariance(self):
        """Static attached lift-curve slope dCL/dalpha reports the low-Re correction."""
        p = AeroParams(
            aspect=2.667,
            cL0=0.0,
            cl_alpha=helmbold_cl_alpha(2.667, 0.7),
            cl_max=0.90,
            stall_alpha_deg=14.0,
        )
        audit = low_reynolds_aerodynamics_audit(np, p, reynolds_range=(1.0e4, 1.0e5), num_re_points=5)
        # With the gentle low-Re correction the slope falls ~4%/decade below 1e5.
        assert audit["slope_variation"] == pytest.approx(0.11, abs=0.05)
        # The legacy Re-invariant behaviour is recovered with low_re_enabled=False.
        p_off = AeroParams(
            aspect=2.667,
            cL0=0.0,
            cl_alpha=helmbold_cl_alpha(2.667, 0.7),
            cl_max=0.90,
            stall_alpha_deg=14.0,
            low_re_enabled=False,
        )
        audit_off = low_reynolds_aerodynamics_audit(np, p_off, reynolds_range=(1.0e4, 1.0e5), num_re_points=5)
        assert audit_off["slope_variation"] == pytest.approx(0.0, abs=1.0e-7)
        for pt in audit_off["re_sweep_data"]:
            assert pt["cl_alpha_per_rad"] == pytest.approx(p_off.cl_alpha, rel=1.0e-2)


class TestStallAndPostStallFlatPlateTransition:
    """Audit of maximum lift CLmax, stall angle, and post-stall transition to Hoerner flat plate."""

    def test_cl_max_ceiling_and_re_invariance(self):
        """Static CLmax cap and peak behaviour vary gently with Re below 1e5."""
        p_off = AeroParams(
            aspect=4.0,
            cl_alpha=4.0,
            cl_max=0.80,
            stall_alpha_deg=15.0,
            stall_sharpness=1.4,
            low_re_enabled=False,
        )
        audit_off = low_reynolds_aerodynamics_audit(np, p_off, reynolds_range=(1.0e4, 1.0e5), num_re_points=5)
        assert audit_off["cl_max_variation"] == pytest.approx(0.0, abs=1.0e-7)
        for pt in audit_off["re_sweep_data"]:
            assert pt["cl_max"] == pytest.approx(0.80, abs=0.01)
            assert pt["nominal_stall_alpha_deg"] == pytest.approx(15.0, abs=1.0e-7)

        p_capped = AeroParams(
            aspect=4.0,
            cl_alpha=4.0,
            cl_max=0.80,
            stall_alpha_deg=15.0,
            stall_sharpness=1.4,
        )
        audit_capped = low_reynolds_aerodynamics_audit(np, p_capped, reynolds_range=(1.0e4, 1.0e5), num_re_points=5)
        assert audit_capped["cl_max_variation"] > 0.05
        for pt in audit_capped["re_sweep_data"]:
            base = 0.80 * (1.0 - 0.12 * max(0.0, min(2.0, np.log10(1.0e5 / pt["reynolds"]))))
            assert pt["cl_max"] == pytest.approx(base, abs=0.02)

        p_std = AeroParams(
            aspect=2.5,
            cl_alpha=helmbold_cl_alpha(2.5, 0.7),
            cl_max=0.95,
            stall_alpha_deg=15.0,
            stall_sharpness=1.4,
            low_re_enabled=False,
        )
        audit_std = low_reynolds_aerodynamics_audit(np, p_std, reynolds_range=(1.0e4, 1.0e5), num_re_points=5)
        assert audit_std["cl_max_variation"] == pytest.approx(0.0, abs=1.0e-7)

    def test_post_stall_monotonic_blend_to_hoerner(self):
        """Verify post-stall curve joins Hoerner flat plate at POST_STALL_PLATE_BLEND_DEG without dip."""
        stall_alpha = 14.0
        ref_cl = 0.90
        alphas = np.linspace(stall_alpha, POST_STALL_PLATE_BLEND_DEG, 32)
        cl_post = np.asarray([
            float(post_stall_lift_coefficient(ScalarMath, float(a), ref_cl, stall_alpha))
            for a in alphas
        ])
        # Value at stall matches ref_cl
        assert cl_post[0] == pytest.approx(ref_cl, abs=1.0e-5)
        # Target at blend angle matches Hoerner plate
        plate_at_blend = float(flat_plate_lift_coefficient(ScalarMath, POST_STALL_PLATE_BLEND_DEG))
        assert cl_post[-1] == pytest.approx(plate_at_blend, abs=1.0e-5)
        # Curve is strictly non-negative and finite
        assert np.all(cl_post >= 0.0)
        assert np.all(np.isfinite(cl_post))

    def test_stall_blend_weight_zero_below_stall(self):
        """Stall blend weight is identically zero at and below stall incidence (|alpha| <= stall_alpha)."""
        # Symmetric airframe: attached when |alpha| <= stall_alpha_deg
        for a in [-14.0, -10.0, -5.0, 0.0, 5.0, 10.0, 14.0]:
            w = stall_blend_weight(ScalarMath, a, 14.0, 1.4)
            assert w == 0.0
        # Above stall incidence (|alpha| > stall_alpha_deg), weight monotonically increases towards 1.0
        w_15 = stall_blend_weight(ScalarMath, 15.0, 14.0, 1.4)
        w_20 = stall_blend_weight(ScalarMath, 20.0, 14.0, 1.4)
        w_30 = stall_blend_weight(ScalarMath, 30.0, 14.0, 1.4)
        assert 0.0 < w_15 < w_20 < w_30 <= 1.0
        # Symmetry check: negative angles past stall have identical blend weight
        assert stall_blend_weight(ScalarMath, -15.0, 14.0, 1.4) == pytest.approx(w_15)
        assert stall_blend_weight(ScalarMath, -20.0, 14.0, 1.4) == pytest.approx(w_20)


class TestLaminarSeparationBubbleDynamics:
    """Audit of Laminar Separation Bubble (LSB) dynamic stall shift and hysteresis across Re."""

    def test_lsb_pitch_up_stall_delay_scaling(self):
        """Dynamic stall delay delta_alpha scales with sqrt(1e4 / Re)."""
        re_low = 1.0e4
        re_high = 4.0e4
        shift_low = float(lsb_dynamic_stall_shift(
            ScalarMath, alpha_dot_deg_s=20.0, mac_m=0.1, speed_mps=8.0, reynolds=re_low
        ))
        shift_high = float(lsb_dynamic_stall_shift(
            ScalarMath, alpha_dot_deg_s=20.0, mac_m=0.1, speed_mps=8.0, reynolds=re_high
        ))
        assert shift_low > 0.0
        assert shift_high > 0.0
        ratio = shift_low / shift_high
        # sqrt(4)=2 plus the (1+0.15*x) LSB gain: fairly gentle change.
        assert ratio == pytest.approx(2.0, rel=0.15)

    def test_lsb_dynamic_stall_hysteresis_loop(self):
        """LSB hysteresis distinguishes pitch-up vs pitch-down effective stall angles."""
        a_stall_static = 14.0
        # Pitch-up (adot > 0, unattached -> delayed stall)
        eff_up, is_stalled_up = lsb_dynamic_stall_hysteresis(
            ScalarMath,
            alpha_deg=14.5,
            alpha_dot_deg_s=15.0,
            stall_alpha_deg=a_stall_static,
            mac_m=0.1,
            speed_mps=8.0,
            reynolds=2.0e4,
            is_stalled_prev=False,
        )
        # Pitch-down (adot < 0, separated -> delayed reattachment)
        eff_down, is_stalled_down = lsb_dynamic_stall_hysteresis(
            ScalarMath,
            alpha_deg=12.0,
            alpha_dot_deg_s=-15.0,
            stall_alpha_deg=a_stall_static,
            mac_m=0.1,
            speed_mps=8.0,
            reynolds=2.0e4,
            is_stalled_prev=True,
        )
        assert eff_up > a_stall_static
        assert eff_down < a_stall_static
        # Hysteresis loop gap
        assert eff_up - eff_down > 2.0


class TestReynoldsParasiteDragScaling:
    """Audit of parasite drag scaling across Re."""

    def test_parasite_drag_power_law(self):
        """cd0 scales with Re^-exp plus the gentle low-Re excess."""
        p_off = AeroParams(
            cd0=0.02,
            re_cd0_exponent=0.15,
            cl_alpha=helmbold_cl_alpha(2.667, 0.7),
            low_re_enabled=False,
        )
        audit_off = low_reynolds_aerodynamics_audit(np, p_off, reynolds_range=(1.0e4, 1.0e5), num_re_points=3)
        assert audit_off["cd0_variation"] > 0.0
        expected_ratio = (1.0e5 / 1.0e4) ** 0.15
        cd0_1e4 = audit_off["re_sweep_data"][0]["cd0"]
        cd0_1e5 = audit_off["re_sweep_data"][-1]["cd0"]
        assert cd0_1e4 / cd0_1e5 == pytest.approx(expected_ratio, rel=1.0e-4)

        p_on = AeroParams(
            cd0=0.02,
            re_cd0_exponent=0.15,
            cl_alpha=helmbold_cl_alpha(2.667, 0.7),
        )
        audit = low_reynolds_aerodynamics_audit(np, p_on, reynolds_range=(1.0e4, 1.0e5), num_re_points=3)
        assert audit["cd0_variation"] > 0.0
        data = audit["re_sweep_data"]
        cd0_1e4 = data[0]["cd0"]
        cd0_1e5 = data[-1]["cd0"]
        assert cd0_1e4 / cd0_1e5 == pytest.approx(expected_ratio * (1.0 + 0.03), rel=5.0e-3)


class TestBackendParity:
    """Verify exact parity between ScalarMath and NumPy backends across Re sweep."""

    @pytest.mark.parametrize("re", [1.0e4, 5.0e4, 1.0e5])
    def test_scalar_vs_numpy_parity(self, re: float):
        """ScalarMath and NumPy produce identical coefficient sets."""
        cfg = AircraftConfig()
        env = EnvironmentConfig()
        p = aero_kernel.params_from_config(cfg, env)

        res_numpy = aero_kernel.evaluate_coefficients(
            np, p, alpha_deg=5.0, beta_deg=1.0, speed=8.0, reynolds=re
        )
        res_scalar = aero_kernel.evaluate_coefficients(
            ScalarMath, p, alpha_deg=5.0, beta_deg=1.0, speed=8.0, reynolds=re
        )

        for key in ["cl", "cd", "cm", "cy", "stall_margin", "stalled"]:
            val_np = float(np.asarray(res_numpy[key]).reshape(-1)[0])
            val_sc = float(ScalarMath.asarray(res_scalar[key]))
            assert val_np == pytest.approx(val_sc, abs=1.0e-7)
