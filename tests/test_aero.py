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
from dataclasses import fields

import pytest

from physics import aero_domain
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
    evaluate_aero,
    evaluate_aero_state,
    flat_plate_lift_coefficient,
    ground_effect_factor,
    helmbold_cl_alpha,
    lift_coefficient,
    lift_slope_per_rad,
    parasite_drag_coefficient,
    pitching_moment_offset_nm,
    prandtl_glauert,
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
    assert result.side_n == pytest.approx(dynamic * area * cfg.cy_beta * deg2rad(beta), rel=1e-12)
    assert result.pitching_moment_nm == pytest.approx(dynamic * area * chord * result.cm, rel=1e-12)

    p_hat = rates[0] * span / (2.0 * speed)
    q_hat = rates[1] * chord / (2.0 * speed)
    r_hat = rates[2] * span / (2.0 * speed)
    expected_cl = cfg.cl_beta * deg2rad(beta) + cfg.cl_roll_rate * p_hat + cfg.cl_pitch_rate * q_hat + cfg.cl_yaw_rate * r_hat
    expected_cn = cfg.cn_beta * deg2rad(beta) + cfg.cn_roll_rate * p_hat + cfg.cn_pitch_rate * q_hat + cfg.cn_yaw_rate * r_hat
    expected_cm = (
        cfg.cm0
        + cfg.cm_alpha * deg2rad(alpha)
        + 0.5 * cfg.cm_q * q_hat
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
    for alpha in (-2.0, 2.0, 6.0, 10.0):
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
