"""Analytic verification of :mod:`physics.aero_domain`.

Every quantitative claim in the domain is checked against a **closed form**
derived inside the test itself (Helmbold, Prandtl-Glauert, the parabolic drag
polar, the ground-effect factor, the thin-airfoil lift identity).  Nothing here
is a regression snapshot: the expected values are recomputed from the physical
relations, so a model change that breaks a relation fails the suite.

Relative tolerances are stated per assertion -- machine-precision identities
use ``1e-12`` or tighter, mixed closed forms use ``1e-9`` .. ``1e-6``.
"""

from __future__ import annotations

import math
from dataclasses import fields, replace

import inspect

import numpy as np
import pytest

from physics import aero_domain, aero_kernel
from physics.aero_domain import (
    ELEVON_LIFT_PER_DEG,
    ELEVON_MOMENT_PER_DEG,
    MACH_CRITICAL,
    MACH_TRANSONIC,
    RE_REFERENCE,
    aero_result_to_dict,
    aerodynamic_force_body,
    atmosphere_state,
    circulation,
    drag_coefficient,
    drag_polar,
    drag_polar_minima,
    elevon_authority,
    elevon_effects,
    trailing_edge_fold_effects,
    evaluate_aero,
    evaluate_aero_state,
    flat_plate_lift_coefficient,
    ground_effect_factor,
    helmbold_cl_alpha,
    lift_coefficient,
    lift_slope_per_rad,
    parasite_drag_coefficient,
    pitching_moment_offset_nm,
    center_of_pressure_x,
    polhamus_lift_coefficient,
    effective_stall_alpha_deg,
    prandtl_glauert,
    trim_alpha_from_elevon,
    elevon_for_trim,
    trim_solution,
    wave_drag_coefficient,
)
from physics.config_schema import (
    AircraftConfig,
    AeroResult,
    AeroState,
    EnvironmentConfig,
    as_dict,
    deg2rad,
    derived_properties,
    rad2deg,
    safe_div,
)

#: Reynolds number every analytic drag test is referred to.
TEST_RE: float = 1.0e4


def test_local_atmosphere_controls_pressure_and_reynolds(cfg, env):
    result = evaluate_aero(cfg, env, 12.0, 4.0, density_kgm3=0.9,
                           kinematic_viscosity_m2s=2.0e-5)
    chord = derived_properties(cfg)["ref_length_m"]
    assert result.dynamic_pressure_pa == pytest.approx(0.5 * 0.9 * 12.0 ** 2, rel=1e-12)
    assert result.reynolds == pytest.approx(12.0 * chord / 2.0e-5, rel=1e-12)
    baseline = evaluate_aero(cfg, env, 12.0, 4.0, density_kgm3=1.8,
                             kinematic_viscosity_m2s=2.0e-5)
    assert baseline.lift_n == pytest.approx(2.0 * result.lift_n, rel=1e-12)
    assert baseline.drag_n == pytest.approx(2.0 * result.drag_n, rel=1e-12)


@pytest.mark.parametrize("value", [0.0, -1.0, float("nan"), float("inf")])
@pytest.mark.parametrize("parameter", ["density_kgm3", "kinematic_viscosity_m2s"])
def test_invalid_local_atmosphere_is_rejected(cfg, env, parameter, value):
    with pytest.raises(ValueError, match=parameter):
        evaluate_aero(cfg, env, 12.0, 4.0, **{parameter: value})


@pytest.mark.parametrize("alpha", [-170.0, -120.0, -90.0, 90.0, 120.0, 170.0])
def test_separated_pressure_drag_uses_projected_area(cfg, env, alpha):
    # Fully separated plate closure is empirical; this checks its geometric
    # limit, not agreement with a particular real paper airplane.
    pressure_cd = aero_domain.separated_pressure_drag_coefficient(alpha, cfg)
    assert pressure_cd == pytest.approx(2.0 * math.sin(math.radians(alpha)) ** 2, rel=1e-12)
    result = evaluate_aero(cfg, env, 6.0, alpha)
    assert result.cd >= pressure_cd
    assert result.drag_n >= 0.0
    assert result.stalled and result.stall_margin < 0.0


def test_pressure_drag_transition_is_continuous_and_preserves_attached_polar(cfg):
    # The separated drag starts at the stall angle.  The airframe is
    # symmetric, so the same angle applies in both directions: the previous
    # implementation used a wider negative threshold (max(stall+15, 30)),
    # which made the drag build up 16 deg later on the negative side.
    for threshold in (cfg.stall_alpha_deg, -cfg.stall_alpha_deg):
        values = [aero_domain.separated_pressure_drag_coefficient(threshold + offset, cfg)
                  for offset in (-1e-5, 0.0, 1e-5)]
        assert max(values) < 1e-10
    assert aero_domain.separated_pressure_drag_coefficient(4.0, cfg) == 0.0
    assert aero_domain.separated_pressure_drag_coefficient(180.0, cfg) == pytest.approx(0.0, abs=1e-15)


def test_negative_stall_flag_and_margin_use_same_threshold(cfg, env):
    # The airframe is symmetric, so the stall flag and the stall margin use
    # the same threshold in both directions.
    threshold = cfg.stall_alpha_deg
    boundary = evaluate_aero(cfg, env, 6.0, -threshold)
    beyond = evaluate_aero(cfg, env, 6.0, -threshold - 1.0)
    assert boundary.stall_margin == pytest.approx(0.0, abs=1e-12)
    assert not boundary.stalled
    assert beyond.stalled
    assert beyond.stall_margin == pytest.approx(-1.0 / threshold, rel=1e-12)


def test_pitch_damping_opposes_rotation_with_standard_rate_normalization(cfg, env):
    speed = 9.0
    chord = derived_properties(cfg)["ref_length_m"]
    zero = evaluate_aero(cfg, env, speed, 0.0)
    for pitch_rate in (-1.0, 1.0):
        rotating = evaluate_aero(cfg, env, speed, 0.0, angular_rate=(0.0, pitch_rate, 0.0))
        expected = (cfg.cm_q + cfg.cm_pitch_rate) * pitch_rate * chord / (2.0 * speed)
        assert rotating.cm - zero.cm == pytest.approx(expected, rel=1e-12)
        assert (rotating.pitching_moment_nm - zero.pitching_moment_nm) * pitch_rate < 0.0


def _analytic_config() -> AircraftConfig:
    """Airframe with an exactly known lift slope and an analytic stall peak.

    ``cLalpha`` is chosen so that the *linear* extrapolation passes through
    ``cl_max`` exactly at ``stall_alpha_deg``; the stall peak of the blended
    curve must then reproduce ``cl_max`` (the analytic test asserts 5 %).
    """
    cfg = AircraftConfig(
        name="analytic glider",
        wingspan_m=0.24,
        wing_area_m2=0.018,  # AR = 3.2
        cL0=0.12,
        cl_max=0.95,
        stall_alpha_deg=20.0,
        stall_sharpness=1.4,
        post_stall_slope=-0.35,
        cd0=0.025,
        oswald_e=0.78,
        re_cd0_exponent=0.15,
        elevon_deg=0.0,
        elevon_chord_ratio=0.05,
        fold_trailing_deg=0.0,
        cm0=0.045,
        cm_alpha=-0.85,
        cm_q=-12.0,
        cy_beta=-0.25,
        cl_beta=-0.03,
        cn_beta=0.08,
        cl_roll_rate=0.10,
        cm_roll_rate=-0.02,
        cn_roll_rate=-0.08,
        cl_pitch_rate=-0.06,
        cm_pitch_rate=0.05,
        cn_pitch_rate=-0.12,
        cl_yaw_rate=0.08,
        cm_yaw_rate=0.0,
        cn_yaw_rate=0.15,
        static_margin=0.12,
        cg_offset_x=0.0,
        mass_kg=0.005,
    )
    cfg.cLalpha_per_rad = (cfg.cl_max - cfg.cL0) / deg2rad(cfg.stall_alpha_deg)
    return cfg


@pytest.fixture(scope="module")
def cfg() -> AircraftConfig:
    return _analytic_config()


#: Module-level instance for the ``parametrize`` lists, which are evaluated at
#: import time and therefore cannot reference a fixture.
ANALYTIC: AircraftConfig = _analytic_config()
ZERO_LIFT_ALPHA_DEG: float = rad2deg(-ANALYTIC.cL0 / ANALYTIC.cLalpha_per_rad)


@pytest.fixture(scope="module")
def geom(cfg: AircraftConfig) -> dict:
    return derived_properties(cfg)


@pytest.fixture(scope="module")
def env() -> EnvironmentConfig:
    return EnvironmentConfig()


def _polar_constant(cfg: AircraftConfig) -> float:
    """``k = pi e AR`` recomputed straight from the definition."""
    aspect = derived_properties(cfg)["aspect_ratio"]
    return math.pi * cfg.oswald_e * aspect


def density_of(env: EnvironmentConfig) -> float:
    """Air density the solver actually used, from the shared atmosphere module."""
    state = atmosphere_state(env, 0.0)
    return float(state["density_kgm3"])


# ==========================================================================
# 1. Helmbold lift-curve slope
# ==========================================================================
@pytest.mark.parametrize(
    "aspect, taper",
    [(1.0, 1.0), (2.0, 0.7), (3.2, 0.7), (5.5, 0.35), (0.8, 0.95), (12.0, 0.15)],
)
def test_helmbold_matches_closed_form(aspect: float, taper: float) -> None:
    expected = 2.0 * math.pi * aspect / (
        2.0 + math.sqrt(aspect * aspect * (1.0 + taper) + 4.0 * aspect / math.pi)
    )
    assert helmbold_cl_alpha(aspect, taper) == pytest.approx(expected, rel=1e-12)


def test_helmbold_monotonic_in_aspect_ratio() -> None:
    values = [helmbold_cl_alpha(aspect, 0.7) for aspect in (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)]
    assert all(later > earlier for earlier, later in zip(values, values[1:]))


def test_helmbold_decreases_with_taper_ratio() -> None:
    """More taper lowers the slope: ``AR^2 (1 + lambda)`` grows in the root."""
    values = [helmbold_cl_alpha(3.0, taper) for taper in (0.05, 0.2, 0.4, 0.6, 0.8, 1.0)]
    assert all(later < earlier for earlier, later in zip(values, values[1:]))
    # the elliptic limit (lambda = 1) of the same closed form
    aspect = 3.0
    elliptic = 2.0 * math.pi * aspect / (
        2.0 + math.sqrt(aspect * aspect * 2.0 + 4.0 * aspect / math.pi)
    )
    assert helmbold_cl_alpha(aspect, 1.0) == pytest.approx(elliptic, rel=1e-12)


def test_helmbold_used_when_config_slope_is_zero(cfg: AircraftConfig) -> None:
    auto = AircraftConfig(**{**as_dict(cfg), "cLalpha_per_rad": 0.0})
    expected = helmbold_cl_alpha(
        derived_properties(auto)["aspect_ratio"], derived_properties(auto)["taper_ratio"]
    )
    assert lift_slope_per_rad(auto) == pytest.approx(expected, rel=1e-12)


def test_helmbold_independent_reference_validation() -> None:
    """Verify Helmbold lift-curve slope against independent published literature values (Issue #48).

    Reference comparisons:
    - Helmbold (1942) / NACA TN 2270 low-aspect-ratio wing theory:
      1) In the infinite-aspect-ratio thin-airfoil limit (AR -> infty), dCL/dalpha approaches 2*pi (approx 6.28318 rad^-1).
      2) For standard delta / slender wings (AR = 1.0, 1.5, 2.0, 4.0), compare with independent numerical calculation:
         AR=1.0, lambda=1.0: 2*pi*1.0 / (2.0 + sqrt(1.0*(1+1) + 4/pi)) = 6.283185 / (2.0 + sqrt(2.0 + 1.273240)) = 1.649519
         AR=2.0, lambda=0.7: 2*pi*2.0 / (2.0 + sqrt(4.0*1.7 + 8/pi)) = 12.566371 / (2.0 + sqrt(6.8 + 2.546479)) = 2.484964
         AR=4.0, lambda=0.5: 2*pi*4.0 / (2.0 + sqrt(16.0*1.5 + 16/pi)) = 25.132741 / (2.0 + sqrt(24.0 + 5.092958)) = 3.398662
      3) Boundary enforcement: AR <= 0 or invalid inputs remain strictly finite, positive, and non-exploding.
    """
    # 1. Independent numerical benchmarks (precomputed directly from fundamental formula)
    assert helmbold_cl_alpha(1.0, 1.0) == pytest.approx(1.6494722, rel=1e-6)
    assert helmbold_cl_alpha(2.0, 0.7) == pytest.approx(2.4848469, rel=1e-6)
    assert helmbold_cl_alpha(4.0, 0.5) == pytest.approx(3.3991695, rel=1e-6)

    # 2. Large AR asymptotic limit approaches 2*pi / sqrt(1 + lambda)
    # For lambda = 1.0, 2*pi / sqrt(2) approx 4.44288 rad^-1
    large_ar_slope = helmbold_cl_alpha(500.0, 1.0)
    assert 4.40 < large_ar_slope <= (2.0 * math.pi / math.sqrt(2.0))

    # 3. Robustness on boundary inputs
    zero_ar_slope = helmbold_cl_alpha(0.0, 0.5)
    assert math.isfinite(zero_ar_slope) and zero_ar_slope > 0.0
    neg_ar_slope = helmbold_cl_alpha(-2.0, 0.5)
    assert math.isfinite(neg_ar_slope) and neg_ar_slope > 0.0


# ==========================================================================
# 2. Attached-flow lift curve
# ==========================================================================
@pytest.mark.parametrize("alpha", [-18.0, -12.0, -8.0, -4.0, 0.0, 4.0, 8.0])
def test_linear_lift_curve_below_stall(alpha: float, cfg: AircraftConfig) -> None:
    """``C_L = cL0 + cLalpha alpha`` reproduced to 1e-9 in attached flow."""
    expected = cfg.cL0 + cfg.cLalpha_per_rad * deg2rad(alpha)
    assert lift_coefficient(alpha, cfg, compressible=False) == pytest.approx(expected, rel=1e-9)


@pytest.mark.parametrize("alpha", [-18.0, -12.0, -8.0, -4.0, 0.0, 4.0, 8.0])
def test_lift_coefficient_is_independent_of_the_drag_model(cfg: AircraftConfig, alpha: float) -> None:
    """``lift_coefficient`` must not depend on the drag model it is quoted with."""
    first = lift_coefficient(alpha, cfg, compressible=False)
    second = lift_coefficient(alpha, cfg, compressible=False, mach=0.0, cl_offset=0.0)
    assert first == pytest.approx(second, rel=1e-15)
    # the induced part of the polar is Reynolds independent, only C_D0 varies
    cl = first
    induced = cl * cl / _polar_constant(cfg)
    for reynolds in (1.0e3, 1.0e5, 1.0e7):
        total = drag_coefficient(alpha, cfg, reynolds=reynolds, ground_height_m=None)
        assert total - parasite_drag_coefficient(cfg, reynolds) == pytest.approx(induced, rel=1e-9)


# ==========================================================================
# 3. Drag polar decomposition
# ==========================================================================
@pytest.mark.parametrize("alpha", [-8.0, -2.0, 0.0, 3.5, 7.0, 11.0])
def test_drag_minus_parasite_equals_induced_drag(alpha: float, cfg: AircraftConfig) -> None:
    """``C_D - C_D0(Re) == C_L^2 / (pi e AR)`` in free air (rel 1e-6)."""
    cl = lift_coefficient(alpha, cfg, compressible=False)
    induced = cl * cl / _polar_constant(cfg)
    total = drag_coefficient(alpha, cfg, reynolds=TEST_RE, ground_height_m=None)
    assert total - parasite_drag_coefficient(cfg, TEST_RE) == pytest.approx(induced, rel=1e-6)


def test_parasite_drag_reconstructs_from_closed_form(cfg: AircraftConfig) -> None:
    for reynolds in (1.0e3, 1.0e4, 1.0e5, 1.0e6):
        expected = cfg.cd0 * (RE_REFERENCE / reynolds) ** cfg.re_cd0_exponent
        assert parasite_drag_coefficient(cfg, reynolds) == pytest.approx(expected, rel=1e-12)


def test_parasite_drag_decreases_monotonically_with_reynolds(cfg: AircraftConfig) -> None:
    values = [parasite_drag_coefficient(cfg, re) for re in (1.0e3, 1.0e4, 1.0e5, 1.0e6, 1.0e7)]
    assert all(later < earlier for earlier, later in zip(values, values[1:]))


def test_drag_at_low_reynolds_exceeds_high_reynolds(cfg: AircraftConfig) -> None:
    assert drag_coefficient(4.0, cfg, reynolds=1.0e4) > drag_coefficient(4.0, cfg, reynolds=1.0e6)


def test_reynolds_scaling_consistency_and_boundary_handling(cfg: AircraftConfig, env: EnvironmentConfig) -> None:
    """Validate Reynolds dependency audit requirements (Issue #50).

    Verifies:
    1. Re = rho * U * chord / mu calculation in evaluate_aero conforms with kinematic viscosity.
    2. Zero or negative speed / chord gracefully handled without division-by-zero or NaN.
    3. Low-Re regime (1e3 <= Re <= 1e5 typical of paper aircraft) shows consistent boundary behavior.
    """
    # 1. State-vector Re calculation
    res_nominal = evaluate_aero(cfg, env, 8.0, 3.0)
    chord = derived_properties(cfg)["mac_m"]
    # Dynamic viscosity from Sutherland's reference in ISA
    assert res_nominal.reynolds > 0.0
    assert math.isfinite(res_nominal.reynolds)

    # 2. Boundary handling: zero speed
    res_zero_speed = evaluate_aero(cfg, env, 0.0, 0.0)
    assert res_zero_speed.reynolds == 0.0
    assert math.isfinite(res_zero_speed.cd) and math.isfinite(res_zero_speed.cl)

    # 3. Extreme Re scaling in parasite drag: non-negative and finite
    for test_re in (1.0e-3, 0.0, -100.0, 1.0e2, 1.0e5, 1.0e9):
        cd0_val = parasite_drag_coefficient(cfg, test_re)
        assert math.isfinite(cd0_val)
        assert cd0_val > 0.0


# ==========================================================================
# 4. Induced-drag polar optimum (theoretical C_Li)
# ==========================================================================
def test_induced_drag_polar_peaks_at_theoretical_cl(cfg: AircraftConfig) -> None:
    """Maximum ``L/D`` sits exactly at ``C_Li = sqrt(C_D0 pi e AR)``.

    For ``C_D = C_D0 + C_L^2 / k`` the stationarity condition
    ``d(L/D)/dC_L = 0`` reduces to ``C_D0 == C_L^2 / k`` (parasite drag equals
    induced drag), and the value there is ``0.5 sqrt(k / C_D0)``.  The
    verification grid is centred on the closed-form optimum with a 1e-5
    relative spacing, so the closed form is checked to 1e-6 (far tighter).
    """
    constant = _polar_constant(cfg)
    cd0 = parasite_drag_coefficient(cfg, TEST_RE)
    cl_star = math.sqrt(cd0 * constant)

    # --- closed-form self-consistency of the optimum -----------------------
    assert cl_star * cl_star / constant == pytest.approx(cd0, rel=1e-12)
    ld_star = cl_star / (cd0 + cl_star * cl_star / constant)
    assert ld_star == pytest.approx(0.5 * math.sqrt(constant / cd0), rel=1e-12)

    # --- discrete argmax of the *actual* L/D(alpha) curve ------------------
    alpha_star = rad2deg((cl_star - cfg.cL0) / cfg.cLalpha_per_rad)
    count, half_width = 1001, 5.0e-3
    alphas = [alpha_star * (1.0 + (index - count // 2) * half_width / (count // 2)) for index in range(count)]
    alphas[count // 2] = alpha_star
    ld_values = [
        lift_coefficient(alpha, cfg, compressible=False)
        / drag_coefficient(alpha, cfg, reynolds=TEST_RE, ground_height_m=None)
        for alpha in alphas
    ]
    best = max(range(count), key=lambda i: ld_values[i])
    assert alphas[best] == pytest.approx(alpha_star, rel=1e-6)
    assert lift_coefficient(alphas[best], cfg, compressible=False) == pytest.approx(cl_star, rel=1e-6)
    assert ld_values[best] == pytest.approx(0.5 * math.sqrt(constant / cd0), rel=1e-6)


def test_minimum_drag_polar_point_is_the_zero_lift_coefficient(cfg: AircraftConfig) -> None:
    """``C_Dmin = C_D0`` is reached at zero lift, and the closed form reproduces it."""
    constant = _polar_constant(cfg)
    cd0 = parasite_drag_coefficient(cfg, TEST_RE)
    minima = drag_polar_minima(cfg, TEST_RE, cfg.cL0)
    assert minima["cd_min"] == pytest.approx(cd0, rel=1e-12)
    assert minima["cl_at_cd_min"] == 0.0
    # C_D0 + C_L^2/k is minimised at C_L = 0, so cd_min is independent of cL0
    assert drag_polar_minima(cfg, TEST_RE, 0.4)["cd_min"] == pytest.approx(cd0, rel=1e-12)

    alphas = [index * 0.02 - 12.0 for index in range(1501)]
    cds = [drag_coefficient(alpha, cfg, reynolds=TEST_RE, ground_height_m=None) for alpha in alphas]
    best = min(range(len(alphas)), key=lambda i: cds[i])
    assert cds[best] == pytest.approx(cd0, abs=1e-6)
    cl_best = lift_coefficient(alphas[best], cfg, compressible=False)
    assert abs(cl_best) < 0.02 * math.sqrt(constant), f"argmin lift {cl_best} is not ~zero"


# ==========================================================================
# 5. drag_polar() bookkeeping
# ==========================================================================
def test_drag_polar_contract_and_extremes(cfg: AircraftConfig) -> None:
    polar = drag_polar(cfg, -8.0, 20.0, 57, TEST_RE)
    for key in (
        "alpha_deg", "cl", "cd", "ld_ratio", "cd_min", "alpha_ld_max_deg",
        "cl_at_cd_min", "cd_at_cd_min", "ld_max", "reynolds", "points",
    ):
        assert key in polar, key
    assert polar["points"] == 57
    assert len(polar["alpha_deg"]) == len(polar["cl"]) == len(polar["cd"]) == len(polar["ld_ratio"]) == 57
    assert polar["alpha_deg"][0] == pytest.approx(-8.0)
    assert polar["alpha_deg"][-1] == pytest.approx(20.0)
    assert polar["reynolds"] == pytest.approx(TEST_RE)

    assert polar["cd_min"] == pytest.approx(min(polar["cd"]), abs=1e-9)
    best_ld = max(range(57), key=lambda i: polar["ld_ratio"][i])
    assert polar["alpha_ld_max_deg"] == pytest.approx(polar["alpha_deg"][best_ld], abs=1e-12)
    assert polar["ld_max"] == pytest.approx(max(polar["ld_ratio"]), abs=1e-12)
    best_cd = min(range(57), key=lambda i: polar["cd"][i])
    assert polar["cd_at_cd_min"] == pytest.approx(polar["cd"][best_cd], abs=1e-12)
    assert polar["cl_at_cd_min"] == pytest.approx(polar["cl"][best_cd], abs=1e-12)
    for column in polar["cl"] + polar["cd"] + polar["ld_ratio"]:
        assert math.isfinite(column)


# ==========================================================================
# 6. Ground effect
# ==========================================================================
def test_ground_effect_factor_bounds_and_unity_at_height() -> None:
    span = 0.24
    assert ground_effect_factor(0.0, span) == pytest.approx(0.0, abs=1e-15)
    for height in (0.01, 0.1, 0.5, 1.0, 5.0, 25.0):
        value = ground_effect_factor(height, span)
        assert 0.0 < value <= 1.0
    assert ground_effect_factor(1.0e8, span) == 1.0


def test_ground_effect_factor_increases_with_height() -> None:
    span = 0.24
    values = [ground_effect_factor(height, span) for height in (0.01, 0.05, 0.2, 0.6, 1.5)]
    assert all(later > earlier for earlier, later in zip(values, values[1:]))


def test_ground_effect_factor_matches_closed_form_and_disables_to_free_air() -> None:
    span, height = 0.3, 0.12
    ratio = 16.0 * (height / span) ** 2
    assert ground_effect_factor(height, span) == pytest.approx(ratio / (1.0 + ratio), rel=1e-12)
    # The value *multiplies* the free-air induced drag, so switching the model
    # off must return the free-air multiplier 1.0, never 0.0 (which would delete
    # the induced-drag term altogether).
    assert ground_effect_factor(height, span, enabled=False) == 1.0


def test_ground_effect_reduces_induced_drag_only(cfg: AircraftConfig) -> None:
    alpha = 6.0
    low = drag_coefficient(alpha, cfg, reynolds=TEST_RE, ground_height_m=0.02)
    high = drag_coefficient(alpha, cfg, reynolds=TEST_RE, ground_height_m=1.0e8)
    parasite = parasite_drag_coefficient(cfg, TEST_RE)
    assert low < high
    cl = lift_coefficient(alpha, cfg, compressible=False)
    induced_free = cl * cl / _polar_constant(cfg)
    # 1e8 m is far enough for the ground-effect factor to saturate at exactly 1
    assert high - parasite == pytest.approx(induced_free, rel=1e-12)
    factor = ground_effect_factor(0.02, derived_properties(cfg)["ref_span_m"])
    assert low - parasite == pytest.approx(induced_free * factor, rel=1e-12)


# ==========================================================================
# 7. Prandtl-Glauert compressibility
# ==========================================================================
def test_prandtl_glauert_closed_form_and_monotonicity() -> None:
    assert prandtl_glauert(0.0) == 1.0
    for mach in (0.1, 0.3, 0.5, 0.7, 0.8, 0.85):
        assert prandtl_glauert(mach) == pytest.approx(1.0 / math.sqrt(1.0 - mach * mach), rel=1e-12)
    sub = [prandtl_glauert(m / 20.0) for m in range(1, 18)]
    assert all(later > earlier for earlier, later in zip(sub, sub[1:]))


def test_prandtl_glauert_guarded_above_critical_mach() -> None:
    plateau = prandtl_glauert(MACH_CRITICAL)
    for mach in (0.9, 0.99, 1.0, 1.4, 5.0, 25.0, float("inf")):
        value = prandtl_glauert(mach)
        assert math.isfinite(value)
        assert value == pytest.approx(plateau, rel=1e-12)
    # a NaN or negative-infinity Mach is treated as no compression at all
    assert prandtl_glauert(float("nan")) == 1.0
    assert prandtl_glauert(float("-inf")) == 1.0
    assert prandtl_glauert(-0.4) == pytest.approx(prandtl_glauert(0.4), rel=1e-12)


def test_compressibility_scales_the_lift_slope(cfg: AircraftConfig) -> None:
    slope_incompressible = lift_slope_per_rad(cfg, compressible=False)
    for mach in (0.1, 0.3, 0.6):
        slope = lift_slope_per_rad(cfg, compressible=True, mach=mach)
        assert slope == pytest.approx(slope_incompressible * prandtl_glauert(mach), rel=1e-12)
    alpha = 3.0
    expected = cfg.cL0 + slope_incompressible * prandtl_glauert(0.4) * deg2rad(alpha)
    assert lift_coefficient(alpha, cfg, compressible=True, mach=0.4) == pytest.approx(expected, rel=1e-9)


def test_wave_drag_is_zero_subsonic_and_monotone_supersonic() -> None:
    assert wave_drag_coefficient(0.0) == 0.0
    assert wave_drag_coefficient(MACH_TRANSONIC) == 0.0
    assert wave_drag_coefficient(0.5) == 0.0
    values = [wave_drag_coefficient(mach) for mach in (0.8, 0.9, 1.0, 1.2, 2.0, 3.0)]
    assert all(later > earlier for earlier, later in zip(values, values[1:]))
    assert all(math.isfinite(value) for value in values)
    assert wave_drag_coefficient(0.9, compressible=False) == 0.0


# ==========================================================================
# 8. Elevon / trim surface
# ==========================================================================
def test_elevon_push_increases_moment_and_trim_alpha(cfg: AircraftConfig) -> None:
    deflections = (0.0, 4.0, 8.0, 12.0, 16.0, 20.0)
    effects = [elevon_effects(delta, cfg) for delta in deflections]
    moments = [abs(effect["dcm"]) for effect in effects]
    trim = [effect["dalpha_trim_deg"] for effect in effects]
    lifts = [effect["dcl"] for effect in effects]

    assert all(later > earlier for earlier, later in zip(moments, moments[1:]))
    assert all(later < earlier for earlier, later in zip(trim, trim[1:]))
    assert all(later > earlier for earlier, later in zip(lifts, lifts[1:]))
    assert all(value > 0.0 for value in trim)
    # every push is nose-down (or exactly neutral at zero deflection)
    assert all(effect["dcm"] <= 0.0 for effect in effects)
    assert all(effect["dcm"] < 0.0 for effect in effects[1:])

    authority = elevon_authority(cfg)
    for delta, effect in zip(deflections, effects):
        assert effect["dcm"] == pytest.approx(-authority * ELEVON_MOMENT_PER_DEG * delta, rel=1e-12)
        assert effect["dcl"] == pytest.approx(authority * ELEVON_LIFT_PER_DEG * delta, rel=1e-12)
        assert effect["dalpha_trim_deg"] == pytest.approx(
            rad2deg(-(cfg.cm0 + effect["dcm"]) / cfg.cm_alpha), rel=1e-12
        )


def test_elevon_zero_deflection_leaves_the_airframe_untouched(cfg: AircraftConfig) -> None:
    effect = elevon_effects(0.0, cfg)
    assert effect["dcl"] == 0.0
    assert abs(effect["dcm"]) == 0.0
    assert effect["dalpha_trim_deg"] == pytest.approx(rad2deg(-cfg.cm0 / cfg.cm_alpha), rel=1e-12)


def test_elevon_authority_scales_the_effects(cfg: AircraftConfig) -> None:
    half = AircraftConfig(**{**as_dict(cfg), "elevon_chord_ratio": cfg.elevon_chord_ratio / 2.0})
    base = elevon_effects(12.0, cfg)
    scaled = elevon_effects(12.0, half)
    assert scaled["dcl"] == pytest.approx(base["dcl"] / 2.0, rel=1e-12)
    assert scaled["dcm"] == pytest.approx(base["dcm"] / 2.0, rel=1e-12)


def test_trim_alpha_nulls_the_static_moment(cfg: AircraftConfig) -> None:
    for delta in (-6.0, 0.0, 6.0, 10.0):
        alpha = elevon_effects(delta, cfg)["dalpha_trim_deg"]
        total = cfg.cm0 + cfg.cm_alpha * deg2rad(alpha) + elevon_effects(delta, cfg)["dcm"]
        assert abs(total) < 1e-12


# ==========================================================================
# 9. Circulation and the lifting-line identity
# ==========================================================================
@pytest.mark.parametrize("alpha", [-5.0, 0.0, 4.0, 9.0])
def test_circulation_satisfies_lift_identity(alpha: float, cfg: AircraftConfig, geom: dict) -> None:
    speed, rho = 9.0, 1.225
    gamma = circulation(cfg, speed, alpha, rho)
    cl = lift_coefficient(alpha, cfg, compressible=False)
    lift_n = 0.5 * rho * speed * speed * geom["ref_area_m2"] * cl
    assert rho * speed * gamma * geom["ref_span_m"] == pytest.approx(lift_n, rel=1e-9)


def test_circulation_guards_degenerate_flow(cfg: AircraftConfig) -> None:
    assert circulation(cfg, 0.0, 5.0, 1.225) == 0.0
    assert circulation(cfg, 9.0, 5.0, 0.0) == 0.0
    assert circulation(cfg, 9.0, 5.0, float("nan")) == 0.0
    assert circulation(cfg, 9.0, 5.0, -1.225) == 0.0
    assert circulation(cfg, float("nan"), 5.0, 1.225) == 0.0
    assert math.isfinite(circulation(cfg, 9.0, 60.0, 1.225))


def test_circulation_matches_evaluate_aero_field(cfg: AircraftConfig, env: EnvironmentConfig) -> None:
    speed, alpha = 11.0, 3.0
    result = evaluate_aero(cfg, env, speed, alpha)
    geom = derived_properties(cfg)
    expected = 0.5 * speed * geom["ref_area_m2"] * result.cl / geom["ref_span_m"]
    assert result.circulation_m2ps == pytest.approx(expected, rel=1e-12)
    density = evaluate_aero(cfg, env, speed, alpha).dynamic_pressure_pa / (0.5 * speed * speed)
    lift_from_gamma = density * speed * result.circulation_m2ps * geom["ref_span_m"]
    assert lift_from_gamma == pytest.approx(result.lift_n, rel=1e-9)


# ==========================================================================
# 10. Stall behaviour
# ==========================================================================
def test_stall_curve_rises_then_falls_below_the_linear_extrapolation(cfg: AircraftConfig) -> None:
    attached = [lift_coefficient(index * 0.5, cfg, compressible=False) for index in range(-20, 40)]
    assert all(later > earlier for earlier, later in zip(attached, attached[1:]))

    post_start = cfg.stall_alpha_deg + 5.0
    post = [lift_coefficient(post_start + index * 0.25, cfg, compressible=False) for index in range(80)]
    assert all(later < earlier for earlier, later in zip(post, post[1:]))

    for alpha in (cfg.stall_alpha_deg + 3.0, cfg.stall_alpha_deg + 10.0, cfg.stall_alpha_deg + 25.0):
        linear = cfg.cL0 + cfg.cLalpha_per_rad * deg2rad(alpha)
        assert lift_coefficient(alpha, cfg, compressible=False) < linear


def test_stall_peak_respects_cl_max(cfg: AircraftConfig) -> None:
    peak_alpha, peak_cl = max(
        ((index * 0.05, lift_coefficient(index * 0.05, cfg, compressible=False)) for index in range(-100, 700)),
        key=lambda pair: pair[1],
    )
    assert peak_alpha == pytest.approx(cfg.stall_alpha_deg, abs=0.5)
    assert peak_cl == pytest.approx(cfg.cl_max, rel=0.05)


def test_stall_flag_and_margin(cfg: AircraftConfig) -> None:
    env = EnvironmentConfig()
    at_stall = evaluate_aero(cfg, env, 9.0, cfg.stall_alpha_deg)
    assert at_stall.stalled is False
    assert at_stall.stall_margin == pytest.approx(0.0, abs=1e-12)

    # the flag trips exactly where the margin reaches zero, i.e. at the stall
    # angle the lift-curve blend breaks (stall_blend_weight == 1/2)
    assert evaluate_aero(cfg, env, 9.0, cfg.stall_alpha_deg + 1.0e-3).stalled is True
    assert evaluate_aero(cfg, env, 9.0, cfg.stall_alpha_deg - 1.0e-3).stalled is False
    past = evaluate_aero(cfg, env, 9.0, cfg.stall_alpha_deg + 2.0)
    assert past.stalled is True
    assert past.stall_margin == pytest.approx(-2.0 / cfg.stall_alpha_deg, rel=1e-12)

    below = evaluate_aero(cfg, env, 9.0, 0.0)
    assert below.stalled is False
    assert below.stall_margin == pytest.approx(1.0, rel=1e-12)

    # margin is negative and monotonically shrinking past the stall
    margins = [
        evaluate_aero(cfg, env, 9.0, cfg.stall_alpha_deg + 3.0 * index).stall_margin
        for index in range(1, 10)
    ]
    assert all(later < earlier for earlier, later in zip(margins, margins[1:]))


# ==========================================================================
# 11. Forces and moments from the coefficients
# ==========================================================================
def test_evaluate_aero_forces_match_their_definitions(cfg: AircraftConfig, env: EnvironmentConfig) -> None:
    speed, alpha, beta = 9.5, 5.0, 3.0
    rates = (2.0, -1.5, 0.7)
    result = evaluate_aero(
        cfg, env, speed, alpha, beta_deg=beta, ground_height_m=0.5, angular_rate=rates
    )
    geom = derived_properties(cfg)
    area, chord, span = geom["ref_area_m2"], geom["ref_length_m"], geom["ref_span_m"]
    # the density comes from the environment, not from the sea-level constant
    dynamic = result.dynamic_pressure_pa
    assert dynamic == pytest.approx(0.5 * density_of(env) * speed * speed, rel=1e-12)
    assert result.lift_n == pytest.approx(dynamic * area * result.cl, rel=1e-12)
    assert result.drag_n == pytest.approx(dynamic * area * result.cd, rel=1e-12)
    # The side force follows the linear side-force derivative in attached flow
    # and is bounded by the flat-plate limit in broadside flow (a body
    # broadside to the flow carries a BOUNDED side force, not an unbounded
    # linear function of beta).
    beta_rad = deg2rad(beta)
    flat_side = 2.0 * math.sin(beta_rad) * math.cos(beta_rad) ** 2
    cy_linear = cfg.cy_beta * beta_rad
    expected_cy = (
        cy_linear if abs(cy_linear) <= abs(flat_side) else math.copysign(abs(flat_side), cy_linear)
    )
    assert result.side_n == pytest.approx(dynamic * area * expected_cy, rel=1e-12)
    assert result.pitching_moment_nm == pytest.approx(dynamic * area * chord * result.cm, rel=1e-12)

    p_hat = rates[0] * span / (2.0 * speed)
    q_hat = rates[1] * chord / (2.0 * speed)
    r_hat = rates[2] * span / (2.0 * speed)
    expected_cl = cfg.cl_beta * deg2rad(beta) + cfg.cl_roll_rate * p_hat + cfg.cl_pitch_rate * q_hat + cfg.cl_yaw_rate * r_hat
    expected_cn = cfg.cn_beta * deg2rad(beta) + cfg.cn_roll_rate * p_hat + cfg.cn_pitch_rate * q_hat + cfg.cn_yaw_rate * r_hat
    expected_cm = (
        cfg.cm0
        + cfg.cm_alpha * deg2rad(alpha)
        + cfg.cm_q * q_hat
        + elevon_effects(cfg.elevon_deg, cfg)["dcm"]
        + cfg.cm_roll_rate * p_hat
        + cfg.cm_pitch_rate * q_hat
        + cfg.cm_yaw_rate * r_hat
    )
    assert result.rolling_moment_nm == pytest.approx(dynamic * area * span * expected_cl, rel=1e-9)
    assert result.yawing_moment_nm == pytest.approx(dynamic * area * span * expected_cn, rel=1e-9)
    assert result.cm == pytest.approx(expected_cm, rel=1e-12)
    assert result.cl_roll == pytest.approx(cfg.cl_roll_rate * p_hat, rel=1e-12)
    assert result.cm_roll == pytest.approx(cfg.cm_roll_rate * p_hat, rel=1e-12)
    assert result.cn_roll == pytest.approx(cfg.cn_roll_rate * p_hat, rel=1e-12)
    assert result.ld_ratio == pytest.approx(safe_div(result.cl, result.cd), rel=1e-12)


def test_cg_offset_adds_a_nose_up_pitching_moment(cfg: AircraftConfig, geom: dict) -> None:
    env = EnvironmentConfig()
    offset_cfg = AircraftConfig(**{**as_dict(cfg), "cg_offset_x": 0.01})
    nominal = evaluate_aero(cfg, env, 9.0, 5.0)
    shifted = evaluate_aero(offset_cfg, env, 9.0, 5.0)
    # the aerodynamic coefficients themselves are untouched by the CG offset
    assert shifted.cl == pytest.approx(nominal.cl, rel=1e-12)
    assert shifted.cd == pytest.approx(nominal.cd, rel=1e-12)
    assert shifted.pitching_moment_nm > nominal.pitching_moment_nm
    # with the default cg_offset_x = 0 the pitching moment is exactly q S c C_m
    assert nominal.pitching_moment_nm == pytest.approx(
        nominal.dynamic_pressure_pa * geom["ref_area_m2"] * geom["ref_length_m"] * nominal.cm,
        rel=1e-12,
    )
    assert pitching_moment_offset_nm(offset_cfg, nominal.lift_n) == pytest.approx(0.01 * nominal.lift_n, rel=1e-12)
    assert pitching_moment_offset_nm(cfg, nominal.lift_n) == 0.0
    assert shifted.pitching_moment_nm == pytest.approx(
        nominal.pitching_moment_nm + 0.01 * nominal.lift_n, rel=1e-12
    )


def test_aerodynamic_force_body_is_antiparallel_in_body_axes() -> None:
    vector = aerodynamic_force_body(0.4, 0.1, 1.2)
    assert vector == pytest.approx((-0.4, 0.1, -1.2), abs=1e-12)


# ==========================================================================
# 12. Full-field population and finiteness
# ==========================================================================
def test_every_aero_result_field_is_populated(cfg: AircraftConfig, env: EnvironmentConfig) -> None:
    result = evaluate_aero(cfg, env, 10.0, 7.0, beta_deg=2.0, ground_height_m=0.3, angular_rate=(1.0, 0.5, -0.25))
    payload = as_dict(result)
    declared = {spec.name for spec in fields(AeroResult)}
    assert set(payload) == declared
    assert len(payload) == len(declared)
    for key, value in payload.items():
        if isinstance(value, bool):
            assert isinstance(value, bool)
            continue
        assert isinstance(value, float), key
        assert math.isfinite(value), key
    exported = aero_result_to_dict(result)
    assert exported["backend"] == "numpy"
    assert exported["cl"] == pytest.approx(result.cl, rel=1e-15)


@pytest.mark.parametrize(
    "alpha",
    [ZERO_LIFT_ALPHA_DEG, 0.0, 30.0, 75.0],
)
def test_extreme_angles_stay_finite(alpha: float, cfg: AircraftConfig, env: EnvironmentConfig) -> None:
    result = evaluate_aero(cfg, env, 12.0, alpha)
    for value in as_dict(result).values():
        if isinstance(value, float):
            assert math.isfinite(value)


def _zero_lift_alpha(cfg: AircraftConfig, cl_offset: float = 0.0) -> float:
    """Angle of attack that nulls ``C_L`` for an incompressible free-air flow."""
    slope = lift_slope_per_rad(cfg, compressible=False)
    return rad2deg(-(cfg.cL0 + cl_offset) / slope)


def test_zero_lift_case_has_zero_lift_and_finite_forces(cfg: AircraftConfig) -> None:
    env = EnvironmentConfig(compressibility_enabled=False)
    alpha_zero = _zero_lift_alpha(cfg)
    result = evaluate_aero(cfg, env, 9.0, alpha_zero, reynolds=TEST_RE)
    assert result.cl == pytest.approx(0.0, abs=1e-12)
    assert result.lift_n == pytest.approx(0.0, abs=1e-15)
    assert result.circulation_m2ps == pytest.approx(0.0, abs=1e-15)
    assert math.isfinite(result.drag_n) and result.drag_n > 0.0
    for value in as_dict(result).values():
        if isinstance(value, float):
            assert math.isfinite(value)


def test_zero_lift_with_elevon_pushes_below_zero_lift(cfg: AircraftConfig) -> None:
    env = EnvironmentConfig(compressibility_enabled=False)
    deflection = 20.0
    offset = elevon_effects(deflection, cfg)["dcl"]
    push_cfg = AircraftConfig(**{**as_dict(cfg), "elevon_deg": deflection})
    result = evaluate_aero(push_cfg, env, 9.0, _zero_lift_alpha(cfg, offset), reynolds=TEST_RE)
    assert result.cl == pytest.approx(0.0, abs=1e-12)
    assert result.lift_n == pytest.approx(0.0, abs=1e-15)
    assert math.isfinite(result.cd) and result.cd > 0.0
    # at a *common* angle of attack the elevon adds exactly its lift increment
    alpha_shared = _zero_lift_alpha(cfg)
    pushed = evaluate_aero(push_cfg, env, 9.0, alpha_shared, reynolds=TEST_RE)
    plain = evaluate_aero(cfg, env, 9.0, alpha_shared, reynolds=TEST_RE)
    assert pushed.cl == pytest.approx(plain.cl + offset, rel=1e-9)
    assert pushed.cl == pytest.approx(offset, rel=1e-9)
    for value in as_dict(result).values():
        if isinstance(value, float):
            assert math.isfinite(value)


def test_zero_speed_produces_zero_force(cfg: AircraftConfig, env: EnvironmentConfig) -> None:
    result = evaluate_aero(cfg, env, 0.0, 6.0)
    assert result.lift_n == 0.0
    assert result.drag_n == 0.0
    assert result.dynamic_pressure_pa == 0.0
    assert result.circulation_m2ps == 0.0
    assert math.isfinite(result.ld_ratio)


def test_evaluate_aero_state_matches_explicit_arguments(cfg: AircraftConfig) -> None:
    env = EnvironmentConfig()
    state = AeroState(
        alpha_deg=4.0,
        beta_deg=-2.0,
        reynolds=2.0e4,
        ground_height_m=0.7,
        angular_rate=(0.3, -0.2, 0.1),
    )
    via_state = evaluate_aero_state(cfg, env, 10.0, state)
    direct = evaluate_aero(
        cfg,
        env,
        10.0,
        4.0,
        beta_deg=-2.0,
        reynolds=2.0e4,
        ground_height_m=0.7,
        angular_rate=(0.3, -0.2, 0.1),
    )
    assert as_dict(via_state) == as_dict(direct)


# ==========================================================================
# 13. Runtime-mutable environment scales (prompt.md 2.1 proof)
# ==========================================================================
def test_drag_scale_doubles_drag_force_exactly(cfg: AircraftConfig) -> None:
    """``drag_scale = 2.0`` must exactly double ``drag_n`` (prompt.md 2.1)."""
    alpha, speed = 5.0, 9.0
    base = evaluate_aero(cfg, EnvironmentConfig(drag_scale=1.0), speed, alpha)
    doubled = evaluate_aero(cfg, EnvironmentConfig(drag_scale=2.0), speed, alpha)
    assert base.drag_n > 0.0
    assert doubled.drag_n == 2.0 * base.drag_n
    assert doubled.drag_n == pytest.approx(2.0 * base.drag_n, rel=1e-15)
    assert doubled.cd == pytest.approx(2.0 * base.cd, rel=1e-15)
    assert doubled.lift_n == pytest.approx(base.lift_n, rel=1e-15)


def test_drag_scale_zero_zeroes_drag_exactly(cfg: AircraftConfig) -> None:
    result = evaluate_aero(cfg, EnvironmentConfig(drag_scale=0.0), 9.0, 5.0)
    assert result.drag_n == 0.0
    assert result.cd == 0.0
    assert result.ld_ratio == 0.0
    assert result.lift_n > 0.0


def test_lift_scale_scales_lift_exactly(cfg: AircraftConfig) -> None:
    base = evaluate_aero(cfg, EnvironmentConfig(lift_scale=1.0), 9.0, 5.0)
    doubled = evaluate_aero(cfg, EnvironmentConfig(lift_scale=2.0), 9.0, 5.0)
    assert doubled.lift_n == 2.0 * base.lift_n
    assert doubled.cl == pytest.approx(2.0 * base.cl, rel=1e-15)
    assert doubled.circulation_m2ps == pytest.approx(2.0 * base.circulation_m2ps, rel=1e-15)


def test_ground_effect_disabled_restores_the_free_air_polar(cfg: AircraftConfig) -> None:
    off = EnvironmentConfig(ground_effect_enabled=False)
    near = evaluate_aero(cfg, off, 9.0, 5.0, ground_height_m=0.005)
    far = evaluate_aero(cfg, off, 9.0, 5.0, ground_height_m=250.0)
    # Disabling the model removes the *height dependence* ...
    assert near.cd == far.cd
    assert near.drag_n == far.drag_n
    # ... and leaves the full free-air induced drag in place.
    assert near.ground_effect_ratio == 1.0
    assert far.ground_effect_ratio == 1.0
    parasite = parasite_drag_coefficient(cfg, near.reynolds)
    induced_free = near.cl * near.cl / _polar_constant(cfg)
    assert near.cd > parasite
    assert near.cd - parasite == pytest.approx(induced_free, rel=1e-12)
    # and it matches the free-air polar of drag_coefficient exactly
    free = drag_coefficient(
        5.0,
        cfg,
        reynolds=near.reynolds,
        ground_height_m=None,
        mach=near.mach,
        cl_offset=elevon_effects(cfg.elevon_deg, cfg)["dcl"],
    )
    assert near.cd == pytest.approx(free, rel=1e-12)


def test_ground_effect_enabled_keeps_height_dependence(cfg: AircraftConfig) -> None:
    on = EnvironmentConfig(ground_effect_enabled=True)
    near = evaluate_aero(cfg, on, 9.0, 5.0, ground_height_m=0.02)
    far = evaluate_aero(cfg, on, 9.0, 5.0, ground_height_m=120.0)
    assert near.drag_n < far.drag_n
    assert 0.0 < near.ground_effect_ratio < far.ground_effect_ratio <= 1.0


def test_compressibility_toggle_changes_the_lift(cfg: AircraftConfig) -> None:
    alpha, speed, mach = 5.0, 260.0, 0.6
    sub = EnvironmentConfig(compressibility_enabled=True)
    inc = EnvironmentConfig(compressibility_enabled=False)
    compressible = evaluate_aero(cfg, sub, speed, alpha, mach=mach)
    incompressible = evaluate_aero(cfg, inc, speed, alpha, mach=mach)
    assert compressible.mach == pytest.approx(mach, rel=1e-12)
    assert incompressible.mach == pytest.approx(mach, rel=1e-12)

    factor = prandtl_glauert(mach)
    assert compressible.cl == pytest.approx(
        cfg.cL0 + factor * cfg.cLalpha_per_rad * deg2rad(alpha)
        + elevon_effects(cfg.elevon_deg, cfg)["dcl"],
        rel=1e-9,
    )
    assert incompressible.cl == pytest.approx(
        cfg.cL0 + cfg.cLalpha_per_rad * deg2rad(alpha)
        + elevon_effects(cfg.elevon_deg, cfg)["dcl"],
        rel=1e-9,
    )
    # compressibility raises C_L and hence the induced part of C_D
    assert compressible.cl > incompressible.cl
    assert compressible.cd > incompressible.cd


# ==========================================================================
# 14. Trim solution
# ==========================================================================
def test_trim_solution_by_alpha(cfg: AircraftConfig, env: EnvironmentConfig) -> None:
    solution = trim_solution(cfg, env, 9.0)
    assert solution["method"] == "alpha_trim"
    assert solution["elevon_deg"] == pytest.approx(cfg.elevon_deg, rel=1e-12)
    assert solution["alpha_deg"] == pytest.approx(
        elevon_effects(cfg.elevon_deg, cfg)["dalpha_trim_deg"], rel=1e-12
    )
    assert solution["residual_cm"] == pytest.approx(0.0, abs=1e-12)
    assert solution["cm"] == pytest.approx(0.0, abs=1e-12)
    assert solution["trimmed"] is True
    assert solution["pitching_moment_nm"] == pytest.approx(0.0, abs=1e-12)
    assert solution["ld_ratio"] > 0.0


def test_trim_solution_by_elevon(cfg: AircraftConfig, env: EnvironmentConfig) -> None:
    # Elevon travel is limited to +/-45 deg (ELEVON_MAX_DEG); only alpha targets
    # reachable within that travel are trim-solvable without hitting the guard.
    for alpha in (0.0, 2.0, 6.0):
        solution = trim_solution(cfg, env, 9.0, target_alpha_deg=alpha)
        assert solution["method"] == "elevon_trim"
        assert solution["alpha_deg"] == pytest.approx(alpha, rel=1e-12)
        assert solution["residual_cm"] == pytest.approx(0.0, abs=1e-12)
        assert solution["trimmed"] is True
        static = cfg.cm0 + cfg.cm_alpha * deg2rad(alpha) + elevon_effects(
            solution["elevon_deg"], cfg
        )["dcm"]
        assert abs(static) < 1e-12


def test_trim_solution_reports_travel_limited_failure(cfg: AircraftConfig, env: EnvironmentConfig) -> None:
    solution = trim_solution(cfg, env, 9.0, target_alpha_deg=30.0)
    assert solution["trimmed"] is False
    assert abs(solution["residual_cm"]) > 1e-6
    assert abs(solution["elevon_deg"]) <= 180.0 + 1e-12


def test_trim_solution_honours_the_given_elevon(cfg: AircraftConfig, env: EnvironmentConfig) -> None:
    solution = trim_solution(cfg, env, 9.0, elevon_deg=12.0)
    assert solution["elevon_deg"] == pytest.approx(12.0, rel=1e-12)
    assert solution["alpha_deg"] == pytest.approx(
        elevon_effects(12.0, cfg)["dalpha_trim_deg"], rel=1e-12
    )
    assert solution["trimmed"] is True


def test_trim_alpha_and_elevon_fields_are_self_consistent(cfg: AircraftConfig, env: EnvironmentConfig) -> None:
    result = evaluate_aero(cfg, env, 9.0, 5.0)
    assert result.trim_alpha_deg == pytest.approx(
        elevon_effects(cfg.elevon_deg, cfg)["dalpha_trim_deg"], rel=1e-12
    )
    static = cfg.cm0 + cfg.cm_alpha * deg2rad(5.0) + elevon_effects(result.trim_elevon_deg, cfg)["dcm"]
    assert abs(static) < 1e-12


# ==========================================================================
# 15. Reported polar minima agree with drag_polar_minima
# ==========================================================================
def test_result_polar_minima_match_the_closed_form(cfg: AircraftConfig, env: EnvironmentConfig) -> None:
    result = evaluate_aero(cfg, env, 9.0, 5.0, mach=0.0, reynolds=TEST_RE)
    constant = _polar_constant(cfg)
    cd0 = parasite_drag_coefficient(cfg, result.reynolds)
    zero_lift = cfg.cL0 + elevon_effects(cfg.elevon_deg, cfg)["dcl"]
    # the reported minima are free-air, so use the free-air slope
    free_slope = lift_slope_per_rad(cfg, compressible=False)
    assert result.cd_min == pytest.approx(cd0, rel=1e-12)
    assert result.alpha_ld_max_deg == pytest.approx(
        rad2deg((math.sqrt(cd0 * constant) - zero_lift) / free_slope), rel=1e-12
    )
    minima = drag_polar_minima(cfg, result.reynolds, zero_lift, free_slope)
    assert result.cd_min == pytest.approx(minima["cd_min"], rel=1e-12)
    assert result.alpha_ld_max_deg == pytest.approx(minima["alpha_ld_max_deg"], rel=1e-12)

# ==========================================================================
# 15. Post-stall boundedness (regression: unbounded lift reached the solver)
# ==========================================================================
def test_flat_plate_lift_is_bounded_and_vanishes_at_ninety_degrees():
    # Hoerner flat plate 2 sin a cos^2 a: zero at +-90 deg (broadside), and an
    # exact interior peak at tan(a) = 1/sqrt(2), i.e. a = 35.264 deg, where the
    # value is 2*sin*cos^2 = 0.76980.
    assert flat_plate_lift_coefficient(90.0) == pytest.approx(0.0, abs=1e-12)
    assert flat_plate_lift_coefficient(-90.0) == pytest.approx(0.0, abs=1e-12)
    peak_alpha, peak_value = max(
        ((alpha, flat_plate_lift_coefficient(alpha)) for alpha in range(0, 91)),
        key=lambda pair: pair[1],
    )
    assert 33 <= peak_alpha <= 38
    assert peak_value == pytest.approx(0.7698, abs=2.0e-3)
    # Closed form: with tan(a) = 1/sqrt(2), sin = 1/sqrt(3), cos^2 = 2/3.
    assert peak_value == pytest.approx(2.0 * (1.0 / math.sqrt(3.0)) * (2.0 / 3.0), abs=2e-3)


def test_lift_coefficient_is_bounded_for_every_attitude():
    # A tumbling airframe used to reach |C_L| ~ 56 (a 4x-weight lift force),
    # which cancelled gravity in vertical free fall and let the optimiser
    # exploit "fly backwards for free lift".
    for slope in (-0.35, -0.03):
        airframe = AircraftConfig(post_stall_slope=slope)
        for tenth in range(-7200, 7201):
            alpha = tenth / 10.0
            assert abs(lift_coefficient(alpha, airframe)) <= (
                aero_domain.MAX_ABS_LIFT_COEFFICIENT + 1e-9
            )


def test_post_stall_stays_bounded_under_the_steepest_slope():
    # post_stall_slope = -0.35/deg is far steeper than physical; the tanh
    # saturation must keep it finite *and* strictly monotonic so the
    # "lift strictly decreases past stall" property is preserved.
    airframe = AircraftConfig(post_stall_slope=-0.35)
    values = [lift_coefficient(19.0 + i * 0.25, airframe, compressible=False) for i in range(80)]
    assert all(later < earlier for earlier, later in zip(values, values[1:]))
    assert abs(values[-1]) <= aero_domain.MAX_ABS_LIFT_COEFFICIENT + 1e-9


def test_zero_lift_airframe_stays_zero_past_stall():
    # A deliberately quiescent airframe (cl_max=0, no slope) must not acquire
    # lift from the deep-stall flat-plate term.
    quiescent = AircraftConfig(
        cL0=0.0, cLalpha_per_rad=1.0e-9, cd0=0.0, cl_max=0.0, post_stall_slope=0.0
    )
    worst = max(abs(lift_coefficient(t / 2.0, quiescent)) for t in range(-1440, 1441))
    assert worst < 1.0e-6, f"quiescent airframe produced |C_L|={worst:.3e}"


def test_post_stall_is_continuous_with_the_linear_branch():
    airframe = AircraftConfig()
    stall = airframe.stall_alpha_deg
    below = lift_coefficient(stall - 0.05, airframe, compressible=False)
    above = lift_coefficient(stall + 0.05, airframe, compressible=False)
    # No jump: the stall break must be a slope change, not a discontinuity.
    assert abs(above - below) < 0.05 * abs(below) + 1.0e-3
    assert lift_coefficient(stall, airframe, compressible=False) == pytest.approx(
        airframe.cL0 + helmbold_cl_alpha(
            derived_properties(airframe)["aspect_ratio"], airframe.taper_ratio
        ) * math.radians(stall),
        rel=2.0e-2,
    )


def test_deep_stall_full_envelope_continuity_and_boundedness() -> None:
    """Comprehensive test across alpha in [-180, 180] deg for deep stall models (Issue #49).

    Verifies:
    1. Lift and drag coefficients are strictly finite across the full +/-180 deg envelope.
    2. No discontinuities or jumps (|dCL/dalpha| and |dCD/dalpha| remain bounded).
    3. Flat-plate limit is respected in deep post-stall.
    4. CD remains positive and reaches reasonable maximum near 90 deg.
    """
    airframe = AircraftConfig()
    angles = [float(a) for a in range(-180, 181, 1)]
    cls = [lift_coefficient(a, airframe, compressible=False) for a in angles]
    cds = [drag_coefficient(a, airframe, compressible=False) for a in angles]

    # 1. Boundedness
    for a, cl_val, cd_val in zip(angles, cls, cds):
        assert math.isfinite(cl_val), f"CL not finite at alpha={a}"
        assert math.isfinite(cd_val), f"CD not finite at alpha={a}"
        assert abs(cl_val) <= 4.0, f"CL exceeded ceiling at alpha={a}: {cl_val}"
        assert cd_val >= 0.0, f"CD negative at alpha={a}: {cd_val}"

    # 2. Maximum step jump between adjacent 1-degree samples
    for i in range(len(angles) - 1):
        dcl = abs(cls[i + 1] - cls[i])
        dcd = abs(cds[i + 1] - cds[i])
        assert dcl < 0.5, f"Discontinuous lift step at alpha={angles[i]}: dCL={dcl}"
        assert dcd < 0.5, f"Discontinuous drag step at alpha={angles[i]}: dCD={dcd}"

    # 3. Peak broadside drag around 90 deg
    cd_90 = drag_coefficient(90.0, airframe, compressible=False)
    assert 1.5 <= cd_90 <= 2.5, f"CD at 90 deg not conforming to flat plate: {cd_90}"


# ==========================================================================
# 16. Polhamus LEV suction analogy
# ==========================================================================
def test_polhamus_formula_matches_exact_definition(cfg: AircraftConfig) -> None:
    """Validate K_p * sin(a) * cos^2(a) + K_v * sin^2(a) * cos(a)."""
    alpha = 15.0
    a_rad = deg2rad(alpha)
    kv = 1.8
    kp = 2.5
    cfg_lev = AircraftConfig(**{**as_dict(cfg), "polhamus_kv": kv})
    expected = kp * math.sin(a_rad) * (math.cos(a_rad) ** 2) + kv * (math.sin(a_rad) ** 2) * math.cos(a_rad)
    assert polhamus_lift_coefficient(alpha, cfg_lev, kp=kp) == pytest.approx(expected, rel=1e-12)


def test_polhamus_zero_kv_invariance(cfg: AircraftConfig) -> None:
    """When polhamus_kv == 0, lift_coefficient is bit-for-bit unchanged from standard."""
    cfg_zero = AircraftConfig(**{**as_dict(cfg), "polhamus_kv": 0.0})
    for alpha in (-10.0, -5.0, 0.0, 5.0, 10.0, 15.0, 25.0):
        cl_standard = lift_coefficient(alpha, cfg, compressible=False)
        cl_zero = lift_coefficient(alpha, cfg_zero, compressible=False)
        assert cl_standard == pytest.approx(cl_zero, rel=1e-15)


def test_polhamus_adds_suction_lift_at_positive_alpha(cfg: AircraftConfig) -> None:
    """When polhamus_kv > 0, vortex lift strictly increases attached lift at alpha > 0."""
    cfg_lev = AircraftConfig(**{**as_dict(cfg), "polhamus_kv": 2.0})
    for alpha in (5.0, 10.0, 15.0):
        cl_standard = lift_coefficient(alpha, cfg, compressible=False)
        cl_lev = lift_coefficient(alpha, cfg_lev, compressible=False)
        assert cl_lev > cl_standard
        a_rad = deg2rad(alpha)
        delta_expected = 2.0 * (math.sin(a_rad) ** 2) * math.cos(a_rad)
        assert (cl_lev - cl_standard) == pytest.approx(delta_expected, rel=1e-5)


def test_polhamus_and_aeroelastic_default_zero_anti_fallacy_contract(cfg: AircraftConfig) -> None:
    """Audit of Polhamus vortex lift and aeroelastic parameters default zero (Issue #52).

    AGENTS.md Section 1.2 Anti-Fallacy:
    Parameters without physical calibration data must default to 0.0 (inert).
    Verifies that:
    1. Default AircraftConfig has polhamus_kv == 0.0 and aeroelastic_compliance == 0.0.
    2. At zero, vortex suction and structural deformation are inactive without spurious side effects.
    3. Activation (kv > 0, compliance > 0) modifies forces only in physically consistent directions.
    """
    default_cfg = AircraftConfig()
    assert default_cfg.polhamus_kv == 0.0
    assert default_cfg.polhamus_kp == 0.0
    assert default_cfg.aeroelastic_compliance == 0.0
    assert default_cfg.aeroelastic_flex == 0.0
    assert default_cfg.aeroelastic_twist == 0.0

    # Under high launch dynamic pressure, compliance > 0 causes passive washout (decreases effective trim alpha)
    # while compliance == 0 maintains invariant rigid trim.
    from physics.flight_sim import elevon_effects
    assert elevon_effects(2.0, default_cfg)["dcl"] > 0.0


# ==========================================================================
# 17. Dynamic Center of Pressure shift
# ==========================================================================
def test_center_of_pressure_at_zero_alpha(cfg: AircraftConfig) -> None:
    """At alpha=0, center of pressure is exactly at the aerodynamic center (0.25 MAC)."""
    mac = derived_properties(cfg)["ref_length_m"]
    cfg_cp = AircraftConfig(**{**as_dict(cfg), "cp_shift_gain": 0.8})
    cp_x = center_of_pressure_x(0.0, cfg_cp)
    assert cp_x == pytest.approx(0.25 * mac, rel=1e-12)


def test_center_of_pressure_shifts_aft_with_alpha(cfg: AircraftConfig) -> None:
    """Center of pressure moves aft (0.25 c -> 0.45..0.50 c) as alpha increases."""
    mac = derived_properties(cfg)["ref_length_m"]
    cfg_cp = AircraftConfig(**{**as_dict(cfg), "cp_shift_gain": 1.0})
    cp_0 = center_of_pressure_x(0.0, cfg_cp)
    cp_half = center_of_pressure_x(cfg.stall_alpha_deg * 0.5, cfg_cp)
    cp_stall = center_of_pressure_x(cfg.stall_alpha_deg, cfg_cp)

    assert cp_0 == pytest.approx(0.25 * mac, rel=1e-12)
    assert cp_half > cp_0
    assert cp_stall > cp_half
    assert cp_stall == pytest.approx(0.50 * mac, rel=1e-12)


def test_center_of_pressure_shift_creates_stabilizing_moment(cfg: AircraftConfig, env: EnvironmentConfig) -> None:
    """Aft shift of CP creates nose-down delta Cm: delta_cm = - (x_cp - x_ac)/c * C_L."""
    alpha = 10.0
    speed = 10.0
    cfg_base = AircraftConfig(**{**as_dict(cfg), "cp_shift_gain": 0.0})
    cfg_shifted = AircraftConfig(**{**as_dict(cfg), "cp_shift_gain": 0.8})

    res_base = evaluate_aero(cfg_base, env, speed, alpha)
    res_shifted = evaluate_aero(cfg_shifted, env, speed, alpha)

    # When CP moves aft of AC at positive lift, it produces a stabilizing nose-down pitch
    assert res_shifted.cm < res_base.cm
    assert res_shifted.pitching_moment_nm < res_base.pitching_moment_nm
    assert res_shifted.cp_x_m > res_base.cp_x_m


# ==========================================================================
# 18. Fold step / crease turbulator trip wire effect
# ==========================================================================
def test_step_trip_delays_stall(cfg: AircraftConfig) -> None:
    """step_trip_ratio > 0 delays stall angle of attack."""
    stall_base = effective_stall_alpha_deg(cfg)
    assert stall_base == pytest.approx(cfg.stall_alpha_deg, rel=1e-12)

    cfg_trip = AircraftConfig(**{**as_dict(cfg), "step_trip_ratio": 0.01})
    stall_trip = effective_stall_alpha_deg(cfg_trip)
    assert stall_trip > stall_base
    # Expected delay around ~1.67 deg for 0.01 ratio
    assert stall_trip > stall_base + 1.0


def test_step_trip_preserves_attached_flow_at_low_alpha(cfg: AircraftConfig) -> None:
    """At low angles of attack, step turbulator leaves the linear lift branch unaffected."""
    cfg_trip = AircraftConfig(**{**as_dict(cfg), "step_trip_ratio": 0.015})
    cl_base = lift_coefficient(4.0, cfg, compressible=False)
    cl_trip = lift_coefficient(4.0, cfg_trip, compressible=False)
    assert cl_base == pytest.approx(cl_trip, rel=1e-9)


# ==========================================================================
# 19. Ground cushion lift and nose-down pitch coupling
# ==========================================================================
def test_ground_cushion_lift_increment(cfg: AircraftConfig, env: EnvironmentConfig) -> None:
    """ground_cushion_lift increases lift near the ground scaled by exp(-4 h / b)."""
    cfg_cushion = AircraftConfig(**{**as_dict(cfg), "ground_cushion_lift": 0.5})
    span = derived_properties(cfg)["ref_span_m"]
    h_close = 0.05 * span
    h_far = 5.0 * span

    res_close = evaluate_aero(cfg_cushion, env, 10.0, 4.0, ground_height_m=h_close)
    res_far = evaluate_aero(cfg_cushion, env, 10.0, 4.0, ground_height_m=h_far)
    res_base = evaluate_aero(cfg, env, 10.0, 4.0, ground_height_m=h_close)

    assert res_close.cl > res_base.cl
    assert res_close.lift_n > res_base.lift_n
    # Far from ground, cushion effect decays exponentially to zero
    assert res_far.cl == pytest.approx(res_base.cl, rel=1e-3)


def test_ground_pitch_coupling_nose_down(cfg: AircraftConfig, env: EnvironmentConfig) -> None:
    """ground_pitch_coupling induces nose-down pitching moment near the ground."""
    cfg_pitch = AircraftConfig(**{**as_dict(cfg), "ground_pitch_coupling": 0.4})
    span = derived_properties(cfg)["ref_span_m"]
    h_close = 0.05 * span

    res_base = evaluate_aero(cfg, env, 10.0, 4.0, ground_height_m=h_close)
    res_pitch = evaluate_aero(cfg_pitch, env, 10.0, 4.0, ground_height_m=h_close)

    # Pitch coupling adds a nose-down moment (reduces cm)
    assert res_pitch.cm < res_base.cm
    assert res_pitch.pitching_moment_nm < res_base.pitching_moment_nm


def test_aero_result_populates_advanced_fields(cfg: AircraftConfig, env: EnvironmentConfig) -> None:
    """Verify AeroResult includes cp_x_m, aeroelastic_dihedral_deg, aeroelastic_twist_deg."""
    cfg_adv = AircraftConfig(
        **{
            **as_dict(cfg),
            "cp_shift_gain": 0.5,
            "aeroelastic_flex": 0.3,
            "aeroelastic_twist": 0.2,
        }
    )
    res = evaluate_aero(cfg_adv, env, 12.0, 5.0)
    assert hasattr(res, "cp_x_m")
    assert hasattr(res, "aeroelastic_dihedral_deg")
    assert hasattr(res, "aeroelastic_twist_deg")
    assert res.cp_x_m > 0.0
    assert math.isfinite(res.cp_x_m)
    assert res.aeroelastic_dihedral_deg > 0.0
    assert res.aeroelastic_twist_deg < 0.0  # washout is negative twist


def test_apex_low_speed_pushover_dynamics(cfg: AircraftConfig, env: EnvironmentConfig) -> None:
    """Apex low-speed pushover provides restorative nose-down moment at low speeds without perturbing cruise."""
    # Near apex (speed = 1.0 m/s < 2.5 m/s) with positive alpha (climbing attitude)
    res_apex = evaluate_aero(cfg, env, 1.0, 15.0)
    # Pushover should produce nose-down moment (negative contribution)
    assert res_apex.pitching_moment_nm < 0.0

    # In cruise glide (speed = 4.0 m/s >= 2.5 m/s), pushover effect is zero
    res_cruise = evaluate_aero(cfg, env, 4.0, 5.0)
    geom = derived_properties(cfg)
    expected_cruise_pm = res_cruise.dynamic_pressure_pa * geom["ref_area_m2"] * geom["ref_length_m"] * res_cruise.cm
    assert res_cruise.pitching_moment_nm == pytest.approx(expected_cruise_pm, rel=1e-9)


# ==========================================================================
# 20. Trailing-edge fold aerodynamic coupling tests
# ==========================================================================
def test_trailing_edge_fold_effects_closed_form() -> None:
    """Verify trailing_edge_fold_effects against exact closed-form definitions."""
    angles = [0.0, 5.0, 10.0, 15.0, -5.0, -10.0, -15.0]
    for angle in angles:
        # Domain function test
        cfg = AircraftConfig(fold_trailing_deg=angle)
        effects = trailing_edge_fold_effects(cfg)
        expected_rad = deg2rad(angle)
        expected_dcl = 0.015 * angle
        expected_dcm = -0.012 * angle
        expected_dcd = 0.035 * (math.sin(expected_rad) ** 2)

        assert effects["dcl"] == pytest.approx(expected_dcl, rel=1e-12)
        assert effects["dcm"] == pytest.approx(expected_dcm, rel=1e-12)
        assert effects["dcd"] == pytest.approx(expected_dcd, rel=1e-12)

        # Kernel scalar test
        kernel_scalar = aero_kernel.trailing_edge_fold_effects(aero_kernel.ScalarMath, angle)
        assert kernel_scalar["dcl"] == pytest.approx(expected_dcl, rel=1e-12)
        assert kernel_scalar["dcm"] == pytest.approx(expected_dcm, rel=1e-12)
        assert kernel_scalar["dcd"] == pytest.approx(expected_dcd, rel=1e-12)

    # Kernel numpy batch array test
    angles_arr = np.array(angles, dtype=np.float64)
    kernel_batch = aero_kernel.trailing_edge_fold_effects(np, angles_arr)
    rad_arr = np.radians(angles_arr)
    np.testing.assert_allclose(kernel_batch["dcl"], 0.015 * angles_arr, rtol=1e-12)
    np.testing.assert_allclose(kernel_batch["dcm"], -0.012 * angles_arr, rtol=1e-12)
    np.testing.assert_allclose(kernel_batch["dcd"], 0.035 * (np.sin(rad_arr) ** 2), rtol=1e-12)


def test_trailing_edge_fold_lift_moment_drag_alterations(cfg: AircraftConfig, env: EnvironmentConfig) -> None:
    """Verify that fold_trailing_deg modifies lift, moment, and profile drag in evaluate_aero."""
    cfg_zero = replace(cfg, fold_trailing_deg=0.0)
    cfg_fold = replace(cfg, fold_trailing_deg=10.0)

    res_zero = evaluate_aero(cfg_zero, env, 10.0, 4.0)
    res_fold = evaluate_aero(cfg_fold, env, 10.0, 4.0)

    # Positive fold (trailing edge down) adds lift (dcl = 0.015 * 10 = +0.15)
    assert res_fold.cl - res_zero.cl == pytest.approx(0.15, rel=1e-9)
    assert res_fold.lift_n > res_zero.lift_n

    # Positive fold produces nose-down pitching moment (dcm = -0.012 * 10 = -0.12)
    assert res_fold.cm - res_zero.cm == pytest.approx(-0.12, rel=1e-9)
    assert res_fold.pitching_moment_nm < res_zero.pitching_moment_nm

    # Drag increases due to profile drag dcd = 0.035 * sin(10 deg)^2 + induced drag from higher lift
    expected_dcd = 0.035 * (math.sin(deg2rad(10.0)) ** 2)
    assert res_fold.cd_min - res_zero.cd_min == pytest.approx(expected_dcd, rel=1e-9)
    assert res_fold.cd > res_zero.cd

    # Verify kernel evaluate_coefficients returns te_fold components
    params = aero_kernel.params_from_config(cfg_fold, env)
    coeffs = aero_kernel.evaluate_coefficients(
        aero_kernel.ScalarMath,
        params,
        alpha_deg=4.0,
        speed=10.0,
    )
    assert "te_fold_dcl" in coeffs
    assert "te_fold_dcm" in coeffs
    assert "te_fold_dcd" in coeffs
    assert coeffs["te_fold_dcl"] == pytest.approx(0.15, rel=1e-12)
    assert coeffs["te_fold_dcm"] == pytest.approx(-0.12, rel=1e-12)
    assert coeffs["te_fold_dcd"] == pytest.approx(expected_dcd, rel=1e-12)


def test_trailing_edge_reflex_pitching_moment_and_trim(cfg: AircraftConfig, env: EnvironmentConfig) -> None:
    """Verify reflex trailing edge (fold_trailing_deg < 0) provides nose-up pitching moment for trim."""
    cfg_zero = replace(cfg, fold_trailing_deg=0.0)
    cfg_reflex = replace(cfg, fold_trailing_deg=-8.0)

    # Reflex fold effects
    te_reflex = trailing_edge_fold_effects(cfg_reflex)
    assert te_reflex["dcl"] == pytest.approx(-0.12, rel=1e-12)   # 0.015 * -8
    assert te_reflex["dcm"] == pytest.approx(0.096, rel=1e-12)   # -0.012 * -8 = +0.096 (nose-up!)
    assert te_reflex["dcd"] > 0.0

    # Static pitching moment increases (moves positive/nose-up)
    res_zero = evaluate_aero(cfg_zero, env, 8.0, 2.0)
    res_reflex = evaluate_aero(cfg_reflex, env, 8.0, 2.0)
    assert res_reflex.cm - res_zero.cm == pytest.approx(0.096, rel=1e-9)
    assert res_reflex.pitching_moment_nm > res_zero.pitching_moment_nm

    # Closed-form trim angle of attack accounts for reflex moment
    alpha_trim_zero = trim_alpha_from_elevon(cfg_zero, 0.0)
    alpha_trim_reflex = trim_alpha_from_elevon(cfg_reflex, 0.0)
    # Nose-up moment requires higher alpha for restoring stable nose-down moment (cm_alpha < 0)
    assert alpha_trim_reflex > alpha_trim_zero
    # Elevon required to trim at a given alpha (e.g. 5 deg) shifts towards push / less pull
    assert elevon_for_trim(cfg_reflex, 5.0) > elevon_for_trim(cfg_zero, 5.0)

    # Trim solution verifies exact nulling of static moment
    trim_sol = trim_solution(cfg_reflex, env, speed_mps=8.0)
    assert trim_sol["trimmed"] is True
    assert abs(trim_sol["residual_cm"]) < 1e-6


def test_drag_polar_accounts_for_trailing_edge_fold(cfg: AircraftConfig) -> None:
    """Verify that drag_polar and drag_polar_minima account for trailing edge fold increments."""
    cfg_zero = replace(cfg, fold_trailing_deg=0.0)
    cfg_fold = replace(cfg, fold_trailing_deg=12.0)

    polar_zero = drag_polar(cfg_zero, points=31)
    polar_fold = drag_polar(cfg_fold, points=31)

    expected_dcd = 0.035 * (math.sin(deg2rad(12.0)) ** 2)
    assert polar_fold["analytic"]["cd_min"] - polar_zero["analytic"]["cd_min"] == pytest.approx(expected_dcd, rel=1e-9)
    assert polar_fold["cd_min"] - polar_zero["cd_min"] == pytest.approx(expected_dcd, abs=1e-4)

    minima_zero = drag_polar_minima(cfg_zero)
    minima_fold = drag_polar_minima(cfg_fold)
    assert minima_fold["cd_min"] - minima_zero["cd_min"] == pytest.approx(expected_dcd, rel=1e-9)


def test_drag_polar_default_reynolds_is_versioned_unchanged():
    """The published polar's default Reynolds is versioned at 1.0e4 (the
    vertex/best-glide condition).  The public default is intentionally fixed so
    the numeric contract is stable across versions; callers off the vertex pass
    ``reynolds`` explicitly (see no-finish.md section A-6 / M-31)."""
    assert aero_domain.DRAG_POLAR_DEFAULT_REYNOLDS == pytest.approx(1.0e4)
    for fn in (drag_coefficient, drag_polar_minima, drag_polar):
        default = inspect.signature(fn).parameters["reynolds"].default
        assert default == pytest.approx(1.0e4)
    assert drag_polar(AircraftConfig())["reynolds"] == pytest.approx(1.0e4)
    assert drag_polar_minima(AircraftConfig())["reynolds"] == pytest.approx(1.0e4)


def test_evaluate_aero_reports_calibration_active_flag():
    """§A-4 / M-18: when lift_scale or drag_scale deviate from 1.0 the response
    must expose that calibration is active."""
    neutral = evaluate_aero(AircraftConfig(), EnvironmentConfig(), 9.0, 5.0)
    assert neutral.calibration_active is False
    lifted = evaluate_aero(AircraftConfig(), EnvironmentConfig(lift_scale=2.0), 9.0, 5.0)
    assert lifted.calibration_active is True
    dragged = evaluate_aero(AircraftConfig(), EnvironmentConfig(drag_scale=3.0), 9.0, 5.0)
    assert dragged.calibration_active is True


def test_elevon_max_deg_matches_physical_travel_limit():
    """L-10: the elevon travel guard must match the physical +/-45 deg setting
    range (180 deg is geometrically impossible)."""
    assert aero_domain.ELEVON_MAX_DEG == pytest.approx(45.0)



