"""Issue #71: coefficient buildup audit.

Single-domain buildup audit: finiteness, CL odd / CD even symmetry below
stall, CY odd in beta, bounded adjacent slopes across stall, envelope
validity, and the explicit non-modelled interference limit.
"""
from __future__ import annotations

import numpy as np

from physics.aero_kernel import coefficient_buildup_audit, evaluate_coefficients, params_from_config
from physics.config_schema import AircraftConfig, EnvironmentConfig


def _params():
    return params_from_config(AircraftConfig(), EnvironmentConfig())


def test_audit_finite_and_envelope() -> None:
    report = coefficient_buildup_audit(np, _params())
    assert report["finite"] is True
    assert report["envelope_valid"] is True
    assert report["reference_area_m2"] > 0.0
    assert report["reference_chord_m"] > 0.0


def test_audit_interference_not_modelled() -> None:
    report = coefficient_buildup_audit(np, _params())
    assert report["interference_modelled"] is False
    assert "NOT modelled" in report["interference_note"]


def test_audit_continuity_clamped() -> None:
    report = coefficient_buildup_audit(np, _params())
    assert report["clamped"] is True
    assert report["cl_slope_max"] < 2.0
    assert report["cd_slope_max"] < 2.0
    assert report["cm_slope_max"] < 2.0


def test_audit_cy_odd_in_beta() -> None:
    report = coefficient_buildup_audit(np, _params())
    assert report["cy_odd_err"] < 1e-9


def test_cl_odd_and_cd_even_below_stall() -> None:
    params = _params()
    alphas = np.array([-3.0, -1.5, 0.0, 1.5, 3.0])
    coeffs = evaluate_coefficients(
        np, params, alpha_deg=alphas, beta_deg=0.0, speed=8.0, reynolds=1e5, mach=0.0
    )
    cl = np.asarray(coeffs["cl"], dtype=float)
    cd = np.asarray(coeffs["cd"], dtype=float)
    # Near alpha=0 the polar is symmetric; farther out CD rises with CL^2
    # which is even by construction, so |CD(a)-CD(-a)| stays small.
    assert abs((cl[4] + cl[0]) - 2.0 * cl[2]) < 0.05
    assert abs(cd[4] - cd[0]) < 0.05


def test_no_nan_across_stall() -> None:
    params = _params()
    alphas = np.arange(-30.0, 31.0, 1.0)
    coeffs = evaluate_coefficients(
        np, params, alpha_deg=alphas, beta_deg=0.0, speed=8.0, reynolds=1e5, mach=0.0
    )
    assert np.all(np.isfinite(np.asarray(coeffs["cl"], dtype=float)))
    assert np.all(np.isfinite(np.asarray(coeffs["cd"], dtype=float)))
    assert np.all(np.isfinite(np.asarray(coeffs["cm"], dtype=float)))
