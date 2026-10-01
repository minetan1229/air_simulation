"""Numerical verification of :mod:`physics.stress_domain`.

Coverage map (see the module docstring for the formula):

* analytically verified (closed form or exact identity):
  ``dynamic_pressure``, ``load_factor``, ``aircraft_load_factor`` at trim,
  ``wing_root_bending_moment``, ``paper_allowable_stress``,
  ``failure_index`` + the status bands, ``normalise_heatmap``,
  ``heatmap_colour``, the ``q``-linear stress scaling, the ``1/t^2`` gsm
  scaling, the ``1/Z`` stiffness scaling, the ``-cos`` shape endpoints.
* regression-checked (behaviour locked in, no analytic reference):
  the peak-station reporting, the dihedral left/right asymmetry growth, the
  winglet span reduction, the elevon trailing-edge concentration, the
  ``KAPPA_SIGMA`` calibration levels and the ``q = 0`` degenerate field.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Dict

import numpy as np
import pytest

from physics.config_schema import AircraftConfig, EnvironmentConfig, clamp
from physics import stress_domain as sd

# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------
REL_TIGHT = 1.0e-12
REL_LOOSE = 1.0e-6

REQUIRED_KEYS = {
    "stress_index",
    "stress_pa",
    "spanwise",
    "chordwise",
    "max_stress_index",
    "min_stress_index",
    "max_stress_pa",
    "mean_stress_pa",
    "wing_root_moment_nm",
    "load_factor",
    "dynamic_pressure_pa",
    "allowable_stress_pa",
    "failure_index",
    "status",
    "dihedral_rad",
    "semispan_m",
    "colours",
    "critical_spanwise_frac",
    "critical_chordwise_frac",
}

STATUS_VALUES = {"ok", "high_load", "failed"}


@pytest.fixture()
def cfg() -> AircraftConfig:
    return AircraftConfig()


@pytest.fixture()
def env() -> EnvironmentConfig:
    return EnvironmentConfig()


def _all_finite(value: Any) -> bool:
    """Recursively assert every float inside a payload is finite."""
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, (list, tuple)):
        return all(_all_finite(item) for item in value)
    if isinstance(value, dict):
        return all(_all_finite(item) for item in value.values())
    return True


def _relative(actual: float, expected: float) -> float:
    if expected == 0.0:
        return abs(actual)
    return abs(actual - expected) / abs(expected)


def _left_right_asymmetry(field: Dict[str, Any]) -> float:
    """Normalised port-minus-starboard row-sum mismatch of the stress field.

    The ``spanwise`` axis is the full span, so the fuselage centre-line sits
    at ``(spanwise - 1) / 2`` and the field splits into an equal number of
    port and starboard stations for even ``spanwise``.
    """
    grid = np.array(field["stress_pa"], dtype=float)
    split = (int(field["spanwise"]) + 1) // 2
    port = grid[:, :split]
    starboard = grid[:, split:]
    total = float(grid.sum())
    assert total > 0.0
    return float(np.abs(port.sum(axis=1) - starboard.sum(axis=1)).max() / total)


# ==========================================================================
# 1. dynamic pressure
# ==========================================================================
def test_dynamic_pressure_matches_closed_form(cfg, env):
    for rho, speed in ((1.225, 12.0), (0.9, 5.5), (1.6, 0.75), (101325.0, 340.0)):
        expected = 0.5 * rho * speed * speed
        assert _relative(sd.dynamic_pressure(rho, speed), expected) <= REL_TIGHT


def test_dynamic_pressure_is_zero_without_dynamic_pressure(cfg, env):
    assert sd.dynamic_pressure(1.225, 0.0) == 0.0
    assert sd.dynamic_pressure(0.0, 12.0) == 0.0
    assert sd.dynamic_pressure(0.0, 0.0) == 0.0
    assert math.copysign(1.0, sd.dynamic_pressure(1.225, 0.0)) > 0.0


def test_dynamic_pressure_scales_with_the_square_of_speed(cfg, env):
    single = sd.dynamic_pressure(1.225, 6.0)
    double = sd.dynamic_pressure(1.225, 12.0)
    assert _relative(double, 4.0 * single) <= REL_TIGHT


def test_dynamic_pressure_rejects_non_finite_input(cfg, env):
    assert math.isfinite(sd.dynamic_pressure(float("nan"), 10.0))
    assert math.isfinite(sd.dynamic_pressure(1.225, float("inf")))
    assert sd.dynamic_pressure(float("nan"), 10.0) == 0.0


# ==========================================================================
# 2. load factor
# ==========================================================================
def test_load_factor_is_one_at_trim_speed(cfg, env):
    derived = sd.derived_properties(cfg)
    speed = sd.trim_speed(cfg, env, 1.225, derived)
    assert speed > 0.0
    weight = sd.aircraft_weight(cfg, env)
    q = sd.dynamic_pressure(1.225, speed)
    nz = sd.aircraft_load_factor(cfg, q, weight, derived)
    assert _relative(nz, 1.0) <= REL_LOOSE


def test_load_factor_doubles_with_dynamic_pressure_quadruple(cfg, env):
    """Doubling the airspeed gives 4x q and 4x nz (L = q S CL is linear in q)."""
    derived = sd.derived_properties(cfg)
    weight = sd.aircraft_weight(cfg, env)
    q1 = sd.dynamic_pressure(1.225, 6.0)
    q2 = sd.dynamic_pressure(1.225, 12.0)
    assert _relative(q2, 4.0 * q1) <= REL_TIGHT
    nz1 = sd.aircraft_load_factor(cfg, q1, weight, derived)
    nz2 = sd.aircraft_load_factor(cfg, q2, weight, derived)
    assert _relative(nz2, 4.0 * nz1) <= REL_TIGHT


def test_scalar_load_factor_helper(cfg, env):
    weight = sd.aircraft_weight(cfg, env)
    assert _relative(sd.load_factor(2.0, 8.0), 0.25) <= REL_TIGHT
    assert sd.load_factor(2.0, 0.0) == 0.0
    assert sd.load_factor(0.0, 8.0) == 0.0
    assert math.isfinite(sd.load_factor(float("nan"), float("nan")))


def test_trim_lift_coefficient_is_bounded_and_finite(cfg):
    derived = sd.derived_properties(cfg)
    cl = sd.trim_lift_coefficient(cfg, derived)
    assert 0.02 <= cl <= cfg.cl_max
    assert math.isfinite(cl)
    # Helmbold triangular-wing slope for AR = b^2 / S, taper 0.70
    ar = cfg.wingspan_m ** 2 / cfg.wing_area_m2
    taper = cfg.taper_ratio
    expected = 2.0 * math.pi * ar / (
        2.0 + math.sqrt(ar * ar * (1.0 + taper) + 4.0 * ar / math.pi)
    )
    assert _relative(sd.lift_curve_slope(cfg, derived), expected) <= REL_TIGHT


# ==========================================================================
# 3. wing root bending moment
# ==========================================================================
def test_wing_root_moment_matches_k_times_span(cfg):
    q, area, span, nz = 88.2, 0.015, 0.2, 3.25
    expected = q * area * (span / 2.0) * nz * 0.35
    got = sd.wing_root_bending_moment(q, area, span, nz)
    assert _relative(got, expected) <= REL_TIGHT
    assert sd.SPAN_LOADING_FACTOR == 0.35


def test_wing_root_moment_is_linear_in_every_argument(cfg):
    base_q, base_area, base_span, base_nz = 50.0, 0.015, 0.2, 2.0
    base = sd.wing_root_bending_moment(base_q, base_area, base_span, base_nz)
    assert base > 0.0
    for factor in (0.5, 2.0, 3.7):
        assert _relative(
            sd.wing_root_bending_moment(base_q * factor, base_area, base_span, base_nz),
            base * factor,
        ) <= REL_TIGHT
        assert _relative(
            sd.wing_root_bending_moment(base_q, base_area * factor, base_span, base_nz),
            base * factor,
        ) <= REL_TIGHT
        assert _relative(
            sd.wing_root_bending_moment(base_q, base_area, base_span * factor, base_nz),
            base * factor,
        ) <= REL_TIGHT
        assert _relative(
            sd.wing_root_bending_moment(base_q, base_area, base_span, base_nz * factor),
            base * factor,
        ) <= REL_TIGHT


def test_wing_root_moment_is_zero_at_zero_dynamic_pressure(cfg):
    assert sd.wing_root_bending_moment(0.0, 0.015, 0.2, 4.0) == 0.0
    assert sd.wing_root_bending_moment(88.2, 0.0, 0.2, 4.0) == 0.0
    assert sd.wing_root_bending_moment(88.2, 0.015, 0.0, 4.0) == 0.0
    assert sd.wing_root_bending_moment(88.2, 0.015, 0.2, 0.0) == 0.0


# ==========================================================================
# 4. allowable stress
# ==========================================================================
def test_allowable_stress_matches_closed_form(cfg):
    for gsm, stiffness in ((100.0, 0.35), (80.0, 0.0), (250.0, 1.0), (140.0, 0.62)):
        expected = 2500.0 * math.sqrt(gsm / 100.0) * (0.6 + 0.8 * stiffness)
        assert _relative(sd.paper_allowable_stress(gsm, stiffness), expected) <= REL_TIGHT


def test_allowable_stress_is_monotonic_in_both_arguments(cfg):
    base = sd.paper_allowable_stress(100.0, 0.35)
    for gsm in (60.0, 80.0):
        assert sd.paper_allowable_stress(gsm, 0.35) < base
    for gsm in (120.0, 200.0, 300.0):
        assert sd.paper_allowable_stress(gsm, 0.35) > base
    for stiffness in (0.0, 0.1, 0.2):
        assert sd.paper_allowable_stress(100.0, stiffness) < base
    for stiffness in (0.5, 0.8, 1.0):
        assert sd.paper_allowable_stress(100.0, stiffness) > base
    assert sd.paper_allowable_stress(200.0, 0.7) > base
    # negative / non-finite arguments are floored, never NaN
    assert math.isfinite(sd.paper_allowable_stress(-5.0, 0.35))
    assert sd.paper_allowable_stress(-5.0, 0.35) == sd.paper_allowable_stress(0.0, 0.35)
    assert math.isfinite(sd.paper_allowable_stress(float("nan"), float("nan")))


# ==========================================================================
# 5. failure index and status bands
# ==========================================================================
def test_failure_index_ratio(cfg):
    assert _relative(sd.failure_index(1.2, 2.0), 0.6) <= REL_TIGHT
    assert sd.failure_index(0.0, 2.0) == 0.0
    assert _relative(sd.failure_index(50.0, 100.0), 0.5) <= REL_TIGHT
    assert math.isfinite(sd.failure_index(1.0e6, 0.0))
    assert math.isfinite(sd.failure_index(float("nan"), float("nan")))


def test_status_bands_switch_exactly_at_the_thresholds(cfg):
    assert sd.STATUS_HIGH_LOAD_THRESHOLD == 0.6
    assert sd.STATUS_FAILED_THRESHOLD == 1.0
    assert sd.status_for_failure_index(0.0) == "ok"
    assert sd.status_for_failure_index(0.5999999) == "ok"
    assert sd.status_for_failure_index(0.6) == "high_load"
    assert sd.status_for_failure_index(0.9999999) == "high_load"
    assert sd.status_for_failure_index(1.0) == "failed"
    assert sd.status_for_failure_index(4.5) == "failed"
    # the boundaries are also reachable through failure_index directly
    assert sd.status_for_failure_index(sd.failure_index(0.6, 1.0)) == "high_load"
    assert sd.status_for_failure_index(sd.failure_index(1.0, 1.0)) == "failed"


# ==========================================================================
# 6. normalise_heatmap
# ==========================================================================
def test_normalise_constant_gives_neutral_half(cfg):
    out = sd.normalise_heatmap([[3.0, 3.0, 3.0], [3.0, 3.0, 3.0]])
    assert out.shape == (2, 3)
    assert np.allclose(out, 0.5, rtol=0.0, atol=0.0)
    assert np.allclose(sd.normalise_heatmap([5.0]), [0.5])
    assert np.allclose(sd.normalise_heatmap([0.0, 0.0]), [0.5, 0.5])
    assert np.allclose(sd.normalise_heatmap(np.full((3, 4), -2.0)), 0.5)


def test_normalise_leaves_the_unit_interval_alone(cfg):
    out = sd.normalise_heatmap([0.0, 0.5, 1.0])
    assert out.tolist() == [0.0, 0.5, 1.0]
    out2 = sd.normalise_heatmap([[0.0, 0.25, 0.5, 0.75, 1.0]])
    assert out2.tolist() == [[0.0, 0.25, 0.5, 0.75, 1.0]]


def test_normalise_clips_out_of_range_values(cfg):
    out = sd.normalise_heatmap([-3.0, 0.0, 7.0])
    assert out.min() == 0.0
    assert out.max() == 1.0
    assert _relative(float(out[1]), 3.0 / 10.0) <= 1.0e-15
    # every output is inside [0, 1] even for a huge dynamic range
    wide = sd.normalise_heatmap([-1.0e9, 0.0, 1.0e9])
    assert wide.min() == 0.0
    assert wide.max() == 1.0


def test_normalise_ramp_stays_in_the_unit_interval(cfg):
    ramp = np.linspace(-5.0, 5.0, 101)
    out = sd.normalise_heatmap(ramp)
    assert out.min() >= 0.0
    assert out.max() <= 1.0
    assert _relative(float(out[0]), 0.0) <= 1.0e-12
    assert _relative(float(out[-1]), 1.0) <= 1.0e-12
    assert np.all(np.diff(out) > 0.0)


def test_normalise_handles_degenerate_input(cfg):
    assert sd.normalise_heatmap([]).size == 0
    assert np.allclose(sd.normalise_heatmap([1.0, float("nan"), 3.0]), [0.0, 0.5, 1.0])
    assert np.all(np.isfinite(sd.normalise_heatmap([float("inf"), 1.0, 2.0])))


# ==========================================================================
# 7. heat map colour ramp
# ==========================================================================
def test_colour_endpoints_are_exact(cfg):
    assert sd.heatmap_colour(0.0) == (0, 0, 255)
    assert sd.heatmap_colour(1.0) == (255, 0, 0)
    assert sd.heatmap_colour(0.5)[1] == 255
    assert sd.heatmap_colour(0.5)[0] > sd.heatmap_colour(0.0)[0]
    assert sd.heatmap_colour(0.5)[2] < sd.heatmap_colour(0.0)[2]


def test_colour_channels_are_monotone_and_in_range(cfg):
    samples = np.linspace(0.0, 1.0, 257)
    reds = [sd.heatmap_colour(float(t))[0] for t in samples]
    blues = [sd.heatmap_colour(float(t))[2] for t in samples]
    greens = [sd.heatmap_colour(float(t))[1] for t in samples]
    assert reds == sorted(reds)
    assert blues == sorted(blues, reverse=True)
    assert reds[0] == 0 and reds[-1] == 255
    assert blues[0] == 255 and blues[-1] == 0
    for channel in (reds, blues, greens):
        assert min(channel) >= 0 and max(channel) <= 255
    for t in samples:
        r, g, b = sd.heatmap_colour(float(t))
        assert isinstance(r, int) and isinstance(g, int) and isinstance(b, int)


def test_colour_out_of_range_indices_are_clamped(cfg):
    assert sd.heatmap_colour(-3.0) == sd.heatmap_colour(0.0)
    assert sd.heatmap_colour(7.0) == sd.heatmap_colour(1.0)
    assert sd.heatmap_colour(float("nan")) == sd.heatmap_colour(0.0)


# ==========================================================================
# 8. stress_field structure
# ==========================================================================
def test_stress_field_returns_exactly_the_contract_keys(cfg):
    out = sd.stress_field(cfg, 88.2, 3.0)
    assert set(out) == REQUIRED_KEYS


@pytest.mark.parametrize("spanwise,chordwise", [(24, 16), (2, 2), (5, 3), (64, 32)])
def test_stress_field_shapes_and_ranges(cfg, spanwise, chordwise):
    out = sd.stress_field(cfg, 88.2, 3.0, spanwise=spanwise, chordwise=chordwise)
    assert out["spanwise"] == spanwise
    assert out["chordwise"] == chordwise
    index = np.array(out["stress_index"], dtype=float)
    stress = np.array(out["stress_pa"], dtype=float)
    assert index.shape == (chordwise, spanwise)
    assert stress.shape == (chordwise, spanwise)
    assert _all_finite(out)
    assert index.min() >= 0.0 and index.max() <= 1.0
    assert stress.min() >= 0.0
    assert len(out["colours"]) == chordwise
    assert all(len(row) == spanwise for row in out["colours"])
    for j, row in enumerate(out["colours"]):
        for i, (r, g, b) in enumerate(row):
            assert (r, g, b) == sd.heatmap_colour(float(index[j, i]))


def test_stress_field_reports_the_true_array_maximum(cfg):
    out = sd.stress_field(cfg, 88.2, 3.0)
    index = np.array(out["stress_index"], dtype=float)
    stress = np.array(out["stress_pa"], dtype=float)
    assert _relative(out["max_stress_index"], float(index.max())) <= REL_TIGHT
    assert _relative(out["min_stress_index"], float(index.min())) <= REL_TIGHT
    assert _relative(out["max_stress_pa"], float(stress.max())) <= REL_TIGHT
    assert _relative(out["mean_stress_pa"], float(stress.mean())) <= REL_TIGHT
    assert out["max_stress_pa"] > 0.0


def test_stress_field_reports_the_true_peak_station(cfg):
    out = sd.stress_field(cfg, 88.2, 3.0, spanwise=24, chordwise=16)
    index = np.array(out["stress_index"], dtype=float)
    centre = (out["spanwise"] - 1) / 2.0
    i_arg = int(np.argmax(index.max(axis=0)))
    j_arg = int(np.argmax(index[:, i_arg]))
    expected_span = clamp(abs(i_arg - centre) / max(centre, 1.0), 0.0, 1.0)
    expected_chord = j_arg / (out["chordwise"] - 1)
    assert _relative(out["critical_spanwise_frac"], expected_span) <= REL_TIGHT
    assert _relative(out["critical_chordwise_frac"], expected_chord) <= REL_TIGHT
    # the peak is at the leading edge, within half a station of the root
    assert out["critical_chordwise_frac"] == 0.0
    assert out["critical_spanwise_frac"] < 0.1


def test_stress_field_passes_through_its_inputs(cfg):
    out = sd.stress_field(cfg, 41.5, 2.75, beta_deg=6.0)
    assert out["dynamic_pressure_pa"] == 41.5
    assert out["load_factor"] == 2.75
    assert _relative(out["wing_root_moment_nm"], 41.5 * 0.015 * 0.1 * 2.75 * 0.35) <= REL_TIGHT
    assert _relative(out["dihedral_rad"], math.radians(cfg.dihedral_deg)) <= REL_TIGHT
    assert out["status"] in STATUS_VALUES
    assert _relative(
        out["failure_index"], sd.failure_index(out["max_stress_pa"], out["allowable_stress_pa"])
    ) <= REL_TIGHT
    assert _relative(
        out["allowable_stress_pa"], sd.paper_allowable_stress(cfg.paper_gsm, cfg.paper_stiffness)
    ) <= REL_TIGHT


def test_stress_field_is_finite_at_zero_dynamic_pressure(cfg):
    out = sd.stress_field(cfg, 0.0, 1.0)
    assert _all_finite(out)
    assert out["max_stress_pa"] == 0.0
    assert out["wing_root_moment_nm"] == 0.0
    assert out["failure_index"] == 0.0
    assert out["status"] == "ok"
    index = np.array(out["stress_index"], dtype=float)
    assert np.all(index == 0.0)
    assert _all_finite(sd.stress_field(cfg, 0.0, 0.0))
    assert _all_finite(sd.stress_field(cfg, float("nan"), float("nan")))


def test_stress_field_clamps_its_grid_size(cfg):
    out = sd.stress_field(cfg, 88.2, 3.0, spanwise=1, chordwise=0)
    assert out["spanwise"] == 2
    assert out["chordwise"] == 2
    big = sd.stress_field(cfg, 88.2, 3.0, spanwise=4096, chordwise=4096)
    assert big["spanwise"] == 256
    assert big["chordwise"] == 256


# ==========================================================================
# 9. stress scaling with dynamic pressure
# ==========================================================================
def test_peak_stress_is_linear_in_dynamic_pressure(cfg):
    """stress ~ M_root ~ q, so doubling q must exactly double the peak."""
    single = sd.stress_field(cfg, 88.2, 1.0)["max_stress_pa"]
    double = sd.stress_field(cfg, 2.0 * 88.2, 1.0)["max_stress_pa"]
    assert double > single
    assert _relative(double, 2.0 * single) <= 1.0e-9


def test_peak_stress_is_linear_in_load_factor(cfg):
    single = sd.stress_field(cfg, 88.2, 1.0)["max_stress_pa"]
    quad = sd.stress_field(cfg, 88.2, 4.0)["max_stress_pa"]
    assert _relative(quad, 4.0 * single) <= 1.0e-9


# ==========================================================================
# 10. dihedral: outboard shift, roll couple and left/right asymmetry
# ==========================================================================
def test_dihedral_increases_left_right_asymmetry(cfg):
    metrics = []
    for degrees in (0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0):
        out = sd.stress_field(replace(cfg, dihedral_deg=degrees), 88.2, 1.0)
        metrics.append(_left_right_asymmetry(out))
    assert metrics[0] < 1.0e-12  # zero dihedral -> perfectly symmetric field
    for previous, current in zip(metrics, metrics[1:]):
        assert current > previous


def test_dihedral_is_antisymmetric_in_sign_but_opposed_in_roll_moment(cfg):
    """+8 deg and -8 deg give mirror-image fields (port loaded vs starboard)."""
    port = sd.stress_field(replace(cfg, dihedral_deg=8.0), 88.2, 1.0)
    starboard = sd.stress_field(replace(cfg, dihedral_deg=-8.0), 88.2, 1.0)
    assert port["max_stress_pa"] > 0.0
    port_grid = np.array(port["stress_pa"], dtype=float)
    starboard_grid = np.array(starboard["stress_pa"], dtype=float)
    assert np.allclose(port_grid, starboard_grid[:, ::-1], rtol=1.0e-12, atol=0.0)
    assert _relative(_left_right_asymmetry(port), _left_right_asymmetry(starboard)) <= REL_TIGHT
    roll_pos = sd.dihedral_roll_couple(replace(cfg, dihedral_deg=8.0), 88.2, 1.0)
    roll_neg = sd.dihedral_roll_couple(replace(cfg, dihedral_deg=-8.0), 88.2, 1.0)
    assert roll_pos > 0.0 > roll_neg
    assert _relative(roll_pos, -roll_neg) <= REL_TIGHT
    assert sd.dihedral_roll_couple(cfg, 0.0, 1.0) == 0.0
    assert sd.dihedral_roll_couple(replace(cfg, dihedral_deg=30.0), 88.2, 1.0) > roll_pos


# ==========================================================================
# 11. tip fold and elevon
# ==========================================================================
def test_tip_fold_shortens_the_effective_semispan(cfg):
    flat = sd.stress_field(replace(cfg, fold_tip_deg=0.0), 88.2, 1.0)
    assert _relative(flat["semispan_m"], cfg.wingspan_m / 2.0) <= REL_TIGHT
    previous = flat["semispan_m"]
    for degrees in (5.0, 15.0, 30.0, 60.0, 90.0):
        folded = sd.stress_field(replace(cfg, fold_tip_deg=degrees), 88.2, 1.0)
        assert folded["semispan_m"] < previous
        assert folded["semispan_m"] > 0.0
        previous = folded["semispan_m"]
    # the winglet also concentrates load, so the peak stress rises
    assert sd.stress_field(replace(cfg, fold_tip_deg=45.0), 88.2, 1.0)["max_stress_pa"] > (
        flat["max_stress_pa"]
    )


def test_elevon_raises_the_trailing_edge_row(cfg):
    base = np.array(
        sd.stress_field(replace(cfg, elevon_deg=0.0), 88.2, 1.0)["stress_pa"], dtype=float
    )
    previous = float(base[-1].max())
    for degrees in (2.0, 8.0, 20.0, 45.0):
        grid = np.array(
            sd.stress_field(replace(cfg, elevon_deg=degrees), 88.2, 1.0)["stress_pa"],
            dtype=float,
        )
        assert float(grid[-1].max()) > previous
        previous = float(grid[-1].max())
    # the trailing edge stays below the leading-edge peak
    assert previous < float(base[0].max())


def test_trailing_edge_fold_shortens_the_effective_chord(cfg):
    """A TE fold removes effective chord, so the whole field softens."""
    flat = sd.stress_field(replace(cfg, fold_trailing_deg=0.0), 88.2, 1.0)
    folded = sd.stress_field(replace(cfg, fold_trailing_deg=40.0), 88.2, 1.0)
    assert folded["max_stress_pa"] < flat["max_stress_pa"]


# ==========================================================================
# 12. material: gsm and stiffness
# ==========================================================================
def test_doubling_gsm_lowers_the_stress_index(cfg):
    single = sd.stress_field(replace(cfg, paper_gsm=100.0), 88.2, 1.0)
    double = sd.stress_field(replace(cfg, paper_gsm=200.0), 88.2, 1.0)
    assert double["max_stress_index"] < single["max_stress_index"]
    # stress ~ 1 / t^2 and t ~ gsm, so 2x gsm is exactly 1/4 of the stress
    assert _relative(double["max_stress_pa"], single["max_stress_pa"] / 4.0) <= 1.0e-9
    # ...while the allowable grows as sqrt(gsm)
    assert _relative(
        double["allowable_stress_pa"], math.sqrt(2.0) * single["allowable_stress_pa"]
    ) <= REL_TIGHT
    assert double["failure_index"] < single["failure_index"]


def test_stiffer_paper_lowers_the_peak_index(cfg):
    previous_index = None
    previous_pa = None
    for stiffness in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
        out = sd.stress_field(replace(cfg, paper_stiffness=stiffness), 88.2, 1.0)
        if previous_index is not None:
            assert out["max_stress_index"] < previous_index
            assert out["max_stress_pa"] < previous_pa
        previous_index = out["max_stress_index"]
        previous_pa = out["max_stress_pa"]
    # the section modulus gain is linear in stiffness
    soft = sd.stress_field(replace(cfg, paper_stiffness=0.2), 88.2, 1.0)
    stiff = sd.stress_field(replace(cfg, paper_stiffness=0.6), 88.2, 1.0)
    expected = (sd.Z_SCALE_OFFSET + sd.Z_SCALE_GAIN * 0.2) / (
        sd.Z_SCALE_OFFSET + sd.Z_SCALE_GAIN * 0.6
    )
    assert _relative(stiff["max_stress_pa"], soft["max_stress_pa"] * expected) <= 1.0e-9


# ==========================================================================
# 13. sideslip
# ==========================================================================
def test_sideslip_adds_a_side_load_contribution(cfg):
    symmetric = replace(cfg, dihedral_deg=0.0)
    neutral = sd.stress_field(symmetric, 88.2, 1.0, beta_deg=0.0)
    port = sd.stress_field(symmetric, 88.2, 1.0, beta_deg=12.0)
    starboard = sd.stress_field(symmetric, 88.2, 1.0, beta_deg=-12.0)
    assert _left_right_asymmetry(neutral) < 1.0e-12
    assert _left_right_asymmetry(port) > 1.0e-6
    port_grid = np.array(port["stress_pa"], dtype=float)
    starboard_grid = np.array(starboard["stress_pa"], dtype=float)
    assert np.allclose(port_grid, starboard_grid[:, ::-1], rtol=1.0e-12, atol=0.0)
    # the sideslip loads the windward semi-wing, so the peak rises above neutral
    assert port["max_stress_pa"] > neutral["max_stress_pa"]


# ==========================================================================
# 14. analyse end to end
# ==========================================================================
def test_analyse_end_to_end(cfg, env):
    out = sd.analyse(cfg, env, 12.0, 1.225)
    assert set(out) == REQUIRED_KEYS | {"speed_mps", "density_kgm3", "backend"}
    assert out["backend"] == "numpy"
    assert sd.active_backend() == "numpy"
    assert out["speed_mps"] == 12.0
    assert out["density_kgm3"] == 1.225
    assert _all_finite(out)
    assert out["max_stress_pa"] > 0.0
    assert out["status"] in STATUS_VALUES
    assert _relative(out["dynamic_pressure_pa"], 0.5 * 1.225 * 144.0) <= REL_TIGHT
    assert out["status"] == sd.status_for_failure_index(out["failure_index"])
    # the grid is a real nested-list payload, JSON serialisable as-is
    import json

    assert json.loads(json.dumps(out))["max_stress_pa"] == out["max_stress_pa"]


def test_analyse_load_factor_matches_the_trimmed_lift_equation(cfg, env):
    out = sd.analyse(cfg, env, 12.0, 1.225)
    derived = sd.derived_properties(cfg)
    weight = sd.aircraft_weight(cfg, env)
    expected = sd.aircraft_load_factor(
        cfg, out["dynamic_pressure_pa"], weight, derived
    )
    assert _relative(out["load_factor"], expected) <= REL_TIGHT
    assert out["load_factor"] > 1.0


def test_analyse_honours_an_explicit_load_factor(cfg, env):
    out = sd.analyse(cfg, env, 12.0, 1.225, load_factor=2.5)
    assert out["load_factor"] == 2.5
    assert _relative(
        out["wing_root_moment_nm"],
        0.5 * 1.225 * 144.0 * 0.015 * 0.1 * 2.5 * 0.35,
    ) <= REL_TIGHT
    # a negative load factor is floored, never propagated
    assert sd.analyse(cfg, env, 12.0, 1.225, load_factor=-3.0)["load_factor"] == 0.0


def test_analyse_reaches_every_status_band(cfg, env):
    # The failure index grows with the dynamic pressure, so the three UI
    # colour bands appear over increasing speed.  Sweep densely instead of
    # pinning magic speeds: the exact band edges move whenever the trim lift
    # coefficient is recalibrated, but all three bands must stay reachable for
    # the shipped default airframe.
    seen = {
        sd.analyse(cfg, env, v, 1.225)["status"]
        for v in np.linspace(0.5, 20.0, 400)
    }
    assert {"ok", "high_load", "failed"} <= seen


def test_analyse_rejects_degenerate_inputs_gracefully(cfg, env):
    out = sd.analyse(cfg, env, 0.0, 1.225)
    assert _all_finite(out)
    assert out["max_stress_pa"] == 0.0
    assert out["load_factor"] == 0.0
    assert out["status"] == "ok"
    out2 = sd.analyse(cfg, env, -5.0, -1.0)
    assert _all_finite(out2)
    assert out2["max_stress_pa"] == 0.0


def test_gravity_acts_only_through_the_load_factor(cfg, env):
    """``nz = L / W`` with ``W = m g``: doubling g doubles W and halves nz.

    The brief's intent ("doubling gravity changes the load factor at fixed
    aerodynamic load") holds; the direction follows from its own formula
    ``nz = L / W`` - more weight at the same lift is a *lower* load factor.
    """
    heavy = replace(env, gravity=env.gravity * 2.0)
    weight_light = sd.aircraft_weight(cfg, env)
    weight_heavy = sd.aircraft_weight(cfg, heavy)
    assert _relative(weight_heavy, 2.0 * weight_light) <= REL_TIGHT

    light = sd.analyse(cfg, env, 12.0, 1.225)
    heavy_field = sd.analyse(cfg, heavy, 12.0, 1.225)
    assert _relative(light["load_factor"], 2.0 * heavy_field["load_factor"]) <= REL_TIGHT
    assert heavy_field["load_factor"] < light["load_factor"]
    # the aerodynamic load itself is untouched
    assert light["dynamic_pressure_pa"] == heavy_field["dynamic_pressure_pa"]
    # and the structural response is only reachable through the load factor
    pinned = sd.analyse(cfg, heavy, 12.0, 1.225, load_factor=light["load_factor"])
    assert _relative(pinned["max_stress_pa"], light["max_stress_pa"]) <= REL_TIGHT


# ==========================================================================
# 15. helpers used by the UI
# ==========================================================================
def test_critical_point_body_is_finite_and_oriented(cfg):
    root_le = sd.critical_point_body(cfg, 0.0, 0.0)
    assert _all_finite(root_le)
    assert root_le[1] == 0.0
    port_tip = sd.critical_point_body(cfg, 0.0, -1.0)
    starboard_tip = sd.critical_point_body(cfg, 0.0, 1.0)
    assert port_tip[1] == -cfg.wingspan_m / 2.0
    assert starboard_tip[1] == cfg.wingspan_m / 2.0
    tail = sd.critical_point_body(cfg, 1.0, 0.0)
    assert tail[0] < 0.0 < root_le[0]
    out_of_range = sd.critical_point_body(cfg, 5.0, -5.0)
    assert _all_finite(out_of_range)


def test_payload_is_json_safe_and_finite(cfg, env):
    import json

    out = sd.analyse(cfg, env, 10.0, 1.225)
    numeric = {
        key: value
        for key, value in out.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    assert numeric
    assert all(math.isfinite(float(v)) for v in numeric.values())
    assert isinstance(out["status"], str)
    assert isinstance(out["stress_index"], list)
    assert isinstance(out["stress_pa"], list)
    assert all(isinstance(row, list) for row in out["stress_pa"])
    assert json.loads(json.dumps(out))["backend"] == "numpy"
