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
    assert out["rank_inversion_risk"] == pytest.approx(rep.ratio - 1.0)
    assert out["rank_inversion_risk"] > 1.0  # not interchangeable
