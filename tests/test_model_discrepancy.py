"""Pin panel-vs-Helmbold discrepancy quantification (#133)."""

from __future__ import annotations

import math

import pytest

from physics.model_discrepancy import (
    discrepancy_report,
    flight_impact_summary,
    helmbold_slope_per_rad,
    panel_slope_per_rad,
)


def test_panel_slope_is_2pi():
    assert panel_slope_per_rad() == pytest.approx(2.0 * math.pi)


def test_helmbold_default_slope_value():
    sl = helmbold_slope_per_rad(2.666666666666667, 0.7)
    assert sl == pytest.approx(2.823, rel=0.01)


def test_ratio_is_documented_2p2():
    rep = discrepancy_report()
    assert rep.ratio == pytest.approx(2.22, rel=0.02)
    assert rep.relative_error == pytest.approx(1.22, rel=0.02)


def test_trim_alpha_scales_inversely_with_slope():
    rep = discrepancy_report(reference_cl=0.5)
    assert rep.trim_alpha_deg_panel == pytest.approx(
        math.degrees(0.5 / rep.panel_slope_per_rad), rel=1e-9
    )
    assert rep.trim_alpha_deg_helmbold == pytest.approx(
        math.degrees(0.5 / rep.helmbold_slope_per_rad), rel=1e-9
    )
    assert rep.trim_alpha_deg_helmbold > rep.trim_alpha_deg_panel


def test_flight_impact_summary_consistency():
    rep = discrepancy_report()
    out = flight_impact_summary(rep)
    assert out["lift_ratio_at_same_alpha"] == pytest.approx(rep.ratio)
    assert out["trim_alpha_ratio_panel_over_helmbold"] == pytest.approx(
        1.0 / rep.ratio
    )
    assert out["induced_drag_ratio_at_same_cl"] == pytest.approx(1.0)
    # Trim regime: same C_L -> same induced drag -> distance ratio 1.0.
    assert out["flight_distance_ratio_at_same_trim_cl"] == pytest.approx(1.0)
    # Induced-dominated bound is the other regime.
    assert out["flight_distance_ratio_induced_bound"] == pytest.approx(
        1.0 / rep.ratio
    )
    assert out["rank_inversion_risk"] == pytest.approx(rep.ratio - 1.0)
    assert out["rank_inversion_risk"] > 1.0  # not interchangeable


def test_helmbold_wrapper_delegates_to_canonical():
    """Wrapper must equal the canonical aero_domain implementation (not a copy)."""
    from physics.aero_domain import helmbold_cl_alpha

    for ar, lam in [(2.67, 0.7), (4.0, 1.0), (1.0, 0.0), (3.5, 0.5)]:
        assert helmbold_slope_per_rad(ar, lam) == pytest.approx(
            helmbold_cl_alpha(ar, lam), rel=1e-12
        )


def test_guidance_tracks_aspect_ratio():
    rep_default = discrepancy_report()
    rep_wide = discrepancy_report(aspect_ratio=4.0, taper_ratio=1.0)
    assert "4.00" in rep_wide.guidance
    assert f"{rep_wide.ratio:.2f}" in rep_wide.guidance
    assert f"{rep_default.ratio:.2f}" in rep_default.guidance
