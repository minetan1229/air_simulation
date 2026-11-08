"""Unit tests for low-Re steady corrections (Re 1e3-1e5).

Covers the opt-in gentle corrections in aero_kernel:
- _log_Re bounded coordinate
- re_cl_alpha_factor / re_stall_factor / re_cl_max_factor / re_lsb_scale / re_cd0_factor
- _coefficient_core interaction with low_re_enabled True/False
- CPU (ScalarMath) vs NumPy parity
- model_valid envelope consistency
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from physics import aero_kernel
from physics.aero_kernel import AeroParams, ScalarMath


def test_log_Re_bounds():
    assert float(aero_kernel._log_Re(ScalarMath, 1.0e5)) == pytest.approx(0.0, abs=1.0e-12)
    assert float(aero_kernel._log_Re(ScalarMath, 1.0e4)) == pytest.approx(1.0, abs=1.0e-12)
    assert float(aero_kernel._log_Re(ScalarMath, 1.0e3)) == pytest.approx(2.0, abs=1.0e-12)
    assert float(aero_kernel._log_Re(ScalarMath, 1.0e2)) == pytest.approx(2.0, abs=1.0e-12)
    assert float(aero_kernel._log_Re(ScalarMath, 1.0e6)) == pytest.approx(0.0, abs=1.0e-12)


def test_cl_alpha_factor_monotone():
    vals = [float(aero_kernel.re_cl_alpha_factor(ScalarMath, re)) for re in (1e3, 1e4, 1e5)]
    assert vals[0] < vals[1] < vals[2]
    assert vals[2] == pytest.approx(1.0, abs=1.0e-12)


def test_stall_factor_monotone():
    vals = [float(aero_kernel.re_stall_factor(ScalarMath, re)) for re in (1e3, 1e4, 1e5)]
    assert vals[0] < vals[1] < vals[2]
    assert vals[2] == pytest.approx(1.0, abs=1.0e-12)


def test_cl_max_factor_monotone():
    vals = [float(aero_kernel.re_cl_max_factor(ScalarMath, re)) for re in (1e3, 1e4, 1e5)]
    assert vals[0] < vals[1] < vals[2]
    assert vals[2] == pytest.approx(1.0, abs=1.0e-12)


def test_lsb_scale_monotone():
    vals = [float(aero_kernel.re_lsb_scale(ScalarMath, re)) for re in (1e3, 1e4, 1e5)]
    assert vals[0] > vals[1] > vals[2]
    assert vals[2] == pytest.approx(1.0, abs=1.0e-12)


def test_cd0_factor_monotone():
    vals = [float(aero_kernel.re_cd0_factor(ScalarMath, re)) for re in (1e3, 1e4, 1e5)]
    assert vals[0] > vals[1] > vals[2]
    assert vals[2] == pytest.approx(1.0, abs=1.0e-12)


def test_off_reproduces_legacy():
    p_off = AeroParams(cl_alpha=3.0, cl_max=0.9, stall_alpha_deg=14.0, low_re_enabled=False)
    p_on = AeroParams(cl_alpha=3.0, cl_max=0.9, stall_alpha_deg=14.0, low_re_enabled=True)
    for re in (1e4, 5e4):
        off = float(aero_kernel.evaluate_coefficients(ScalarMath, p_off, alpha_deg=5.0, speed=8.0, reynolds=re)["cl"])
        on = float(aero_kernel.evaluate_coefficients(ScalarMath, p_on, alpha_deg=5.0, speed=8.0, reynolds=re)["cl"])
        assert on < off
    # At Re >= 1e5 the correction vanishes.
    off = float(aero_kernel.evaluate_coefficients(ScalarMath, p_off, alpha_deg=5.0, speed=8.0, reynolds=1e5)["cl"])
    on = float(aero_kernel.evaluate_coefficients(ScalarMath, p_on, alpha_deg=5.0, speed=8.0, reynolds=1e5)["cl"])
    assert on == pytest.approx(off, abs=1.0e-12)


def test_scalar_numpy_parity_with_low_re():
    p = AeroParams(cl_alpha=3.0, low_re_enabled=True)
    for re in (1e4, 5e4, 1e5):
        r_np = np.asarray(aero_kernel.evaluate_coefficients(np, p, alpha_deg=5.0, speed=8.0, reynolds=re)["cl"]).flat[0]
        r_sc = float(aero_kernel.evaluate_coefficients(ScalarMath, p, alpha_deg=5.0, speed=8.0, reynolds=re)["cl"])
        assert r_np == pytest.approx(r_sc, abs=1.0e-12)


def test_stall_margin_uses_effective_stall():
    p_off = AeroParams(stall_alpha_deg=14.0, low_re_enabled=False)
    p_on = AeroParams(stall_alpha_deg=14.0, low_re_enabled=True)
    # At Re 1e4 the effective stall is ~12.6 deg, so alpha 13 deg is stalled when on.
    off = aero_kernel.evaluate_coefficients(ScalarMath, p_off, alpha_deg=13.0, speed=8.0, reynolds=1e4)
    on = aero_kernel.evaluate_coefficients(ScalarMath, p_on, alpha_deg=13.0, speed=8.0, reynolds=1e4)
    assert float(off["stalled"]) == 0.0
    assert float(on["stalled"]) == 1.0


def test_lsb_gain_increases_shift():
    s_default = float(aero_kernel.lsb_dynamic_stall_shift(ScalarMath, 20.0, 0.1, 8.0, 1e4))
    s_no_gain = float(aero_kernel.lsb_dynamic_stall_shift(ScalarMath, 20.0, 0.1, 8.0, 1e4, low_re_lsb_gain=0.0))
    assert s_default > s_no_gain
