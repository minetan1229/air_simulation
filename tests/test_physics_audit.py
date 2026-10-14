"""Cross-checks of the internal physics against published relations and tables.

This module is deliberately *not* a regression snapshot.  Every expected value
here is either a **published table entry** or a relation quoted from the
literature and re-derived inside the test, so a model that drifts away from the
reference fails on the physics rather than on a stored number.

References used
---------------
* ISO 2533:1975 / ICAO *Manual of the ICAO Standard Atmosphere* (troposphere
  table).
* Sutherland (1895) via the ICAO viscosity table, and Sutherland's law as
  tabulated in White, *Fluid Mechanics*, 7th ed., table 1.1.
* Raymer, *Aircraft Design: A Conceptual Approach*, 5th ed. -- aspect ratio,
  mean aerodynamic chord, induced drag of a finite wing.
* Helmbold / Jensen lifting-surface theory (the ``2 pi AR / (2 + sqrt(...))``
  lift-curve slope) and the induced-drag polar ``C_Di = C_L^2 / (pi e AR)``.
* Prandtl (1922) / Glauert (1924) compressibility correction.
* Hoerner, *Fluid-Dynamic Drag*, fully separated flat plate ``2 sin a cos^2 a``.
* McCormick (1985) ground-effect induced-drag reduction ``16(h/b)^2/(1+16(h/b)^2)``.
* Alduchov & Eskridge (1996) over water, Magnus/Tetens over liquid.
* Meridian, the International Standard Atmosphere.
* Standard sheet-mass relation ``m = grammage * area`` and the paper bulk
  density/thickness relation ``t = sigma / rho``.

Tolerances are stated per assertion and justified in the comment beside it.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from physics import aero_domain, atmosphere_domain, flight_sim, mesh_domain, stress_domain
from physics.aero_domain import (
    atmosphere_state,
    drag_coefficient,
    drag_polar_minima,
    evaluate_aero,
    flat_plate_lift_coefficient,
    ground_effect_factor,
    helmbold_cl_alpha,
    prandtl_glauert,
)
from physics.atmosphere_domain import (
    air_density,
    isa_density,
    isa_pressure,
    isa_temperature,
    saturation_vapour_pressure_Pa,
    speed_of_sound,
    sutherland_viscosity,
)
from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    derived_properties,
    deg2rad,
)
from physics.geometry_utils import (
    angular_acceleration_body,
    body_to_world,
    euler_to_matrix,
    matrix_to_euler,
    quaternion_from_euler,
    quaternion_to_matrix,
    world_to_body,
)


# ==========================================================================
# 1. Mean aerodynamic chord -- Raymer eq. 12.10 / Shevell eq. 6.1-6.2
# ==========================================================================
def _reference_mac(area: float, span: float, taper: float) -> float:
    """MAC of a linearly tapered wing from the textbook closed form."""
    root_chord = 2.0 * area / (span * (1.0 + taper))
    return (2.0 / 3.0) * root_chord * (1.0 + taper + taper * taper) / (1.0 + taper)


def _numeric_mac(area: float, span: float, taper: float, samples: int = 200001) -> float:
    y = np.linspace(0.0, span * 0.5, samples)
    c = (2.0 * area / (span * (1.0 + taper))) * (1.0 - (1.0 - taper) * (2.0 * y / span))
    trapz_fn = getattr(np, "trapezoid", getattr(np, "trapz", None))
    return float(2.0 / area * trapz_fn(c * c, y))


@pytest.mark.parametrize(
    "area, span, taper",
    [
        (0.0150, 0.200, 0.70),  # the shipped default glider
        (0.0180, 0.240, 1.00),  # rectangular
        (0.0080, 0.120, 0.30),  # strongly tapered
        (0.0300, 0.360, 0.85),
    ],
)
def test_mac_matches_the_textbook_closed_form(area: float, span: float, taper: float) -> None:
    cfg = AircraftConfig(wing_area_m2=area, wingspan_m=span, taper_ratio=taper)
    mac = derived_properties(cfg)["mac_m"]
    # Raymer / Shevell closed form
    assert mac == pytest.approx(_reference_mac(area, span, taper), rel=1e-12)
    # ... and the defining integral, to 1e-6 (trapezoid end-point quadrature)
    assert mac == pytest.approx(_numeric_mac(area, span, taper), rel=1e-6)


def test_mac_is_a_length_and_collapses_to_the_mean_chord_for_a_rectangular_wing() -> None:
    for taper in (0.30, 0.70, 1.00):
        area, span = 0.0150, 0.200
        cfg = AircraftConfig(wing_area_m2=area, wingspan_m=span, taper_ratio=taper)
        props = derived_properties(cfg)
        mean_chord = props["mean_chord_m"]
        mac = props["mac_m"]
        # A rectangular wing (taper = 1) has MAC == c_root == S / b exactly.
        if taper == 1.0:
            assert mac == pytest.approx(mean_chord, rel=1e-12)
        # Dimension guard: scaling the span must not scale MAC with it.  The
        # pre-audit formula carried a spurious factor b, so MAC came out with the
        # units of an area (m^2) and shrank by exactly 1/b.
        assert mac == pytest.approx(_reference_mac(area, span, taper), rel=1e-12)
        assert 0.5 * mean_chord < mac < 1.5 * mean_chord
    # Doubling the span at fixed area must halve both chords, never square them.
    wide = derived_properties(AircraftConfig(wing_area_m2=0.0150, wingspan_m=0.400, taper_ratio=0.70))
    narrow = derived_properties(AircraftConfig(wing_area_m2=0.0150, wingspan_m=0.200, taper_ratio=0.70))
    assert wide["mac_m"] == pytest.approx(narrow["mac_m"] / 2.0, rel=1e-12)
    assert wide["ref_length_m"] == pytest.approx(narrow["ref_length_m"] / 2.0, rel=1e-12)


def test_ref_length_defaults_to_the_mac_and_is_overridable() -> None:
    base = derived_properties(AircraftConfig())
    assert base["ref_length_m"] == pytest.approx(base["mac_m"], rel=1e-15)
    pinned = derived_properties(AircraftConfig(ref_length_m=0.05))
    assert pinned["ref_length_m"] == 0.05


# ==========================================================================
# 2. Sheet mass and thickness from grammage
# ==========================================================================
def test_sheet_mass_is_grammage_times_area() -> None:
    # 100 g/m^2 over 0.02 m^2 is 2 g; an A4 sheet (0.06237 m^2) is 6.24 g.
    assert mesh_domain.paper_mass_from_mesh(0.02, 100.0) == pytest.approx(0.002, rel=1e-15)
    assert mesh_domain.paper_mass_from_mesh(0.06237, 100.0) == pytest.approx(0.006237, rel=1e-12)
    assert mesh_domain.paper_mass_from_mesh(0.02, 300.0) == pytest.approx(0.006, rel=1e-15)
    # linear in both arguments, zero at zero
    assert mesh_domain.paper_mass_from_mesh(0.0, 100.0) == 0.0
    assert mesh_domain.paper_mass_from_mesh(0.02, 0.0) == 0.0


def test_sheet_thickness_follows_the_paper_bulk_density() -> None:
    # t = sigma / rho; 100 g/m^2 = 0.1 kg/m^2 of 800 kg/m^3 stock is 0.125 mm.
    thickness = stress_domain._sheet_thickness(100.0)
    assert thickness == pytest.approx(0.1 / 800.0, rel=1e-12)
    assert thickness == pytest.approx(0.125e-3, rel=1e-9)
    # the two independent thickness routes agree
    assert mesh_domain.PAPER_THICKNESS_M == pytest.approx(thickness, rel=1e-12)


# ==========================================================================
# 3. ISA standard atmosphere -- ICAO table
# ==========================================================================
#: (geometric altitude [m], T [K], p [Pa]) from the ICAO troposphere table
#: (g0 = 9.80665, L = 0.0065 K/m, T0 = 288.15 K, p0 = 101325 Pa).  The densities
#: are deliberately *not* tabulated here: they are re-derived from p / (R T) in
#: the identity test below, which is exact and does not depend on remembering a
#: fourth significant figure.
ICAO_TROPOSPHERE = (
    (0.0, 288.15, 101325.0),
    (1000.0, 281.65, 89874.6),
    (2000.0, 275.15, 79495.2),
    (5000.0, 255.65, 54019.9),
    (10000.0, 223.15, 26436.3),
    (11000.0, 216.65, 22632.1),
)


@pytest.mark.parametrize("altitude, temperature, pressure", ICAO_TROPOSPHERE)
def test_isa_column_matches_the_icao_table(altitude: float, temperature: float, pressure: float) -> None:
    # The table is rounded to 6 significant figures, so 1e-4 relative is the
    # tightest honest bound.
    assert isa_temperature(altitude) == pytest.approx(temperature, rel=1e-6)
    assert isa_pressure(altitude) == pytest.approx(pressure, rel=1e-4)


@pytest.mark.parametrize("altitude, _, _", ICAO_TROPOSPHERE)
def test_isa_density_is_pressure_over_r_times_t(altitude: float, _: float, __: float) -> None:
    t = isa_temperature(altitude)
    p = isa_pressure(altitude)
    assert isa_density(altitude) == pytest.approx(p / (287.05 * t), rel=1e-12)


def test_isa_density_matches_the_two_table_entries_that_are_exact() -> None:
    # 0 m: 1.2250 kg/m^3 (sea-level ISA) and 11 km: 0.36392 kg/m^3 (tropopause).
    assert isa_density(0.0) == pytest.approx(1.2250, rel=1e-4)
    assert isa_density(11000.0) == pytest.approx(0.36392, rel=1e-4)


def test_isa_exponent_is_g_over_lapse_times_r() -> None:
    # p/p0 = (T/T0)^(g0/(L R_air)); 9.80665 / (0.0065 * 287.05) = 5.25588...
    exponent = 9.80665 / (0.0065 * 287.05)
    expected = 101325.0 * ((288.15 - 0.0065 * 5000.0) / 288.15) ** exponent
    assert isa_pressure(5000.0) == pytest.approx(expected, rel=1e-12)


def test_isa_isothermal_limit_matches_the_barometric_formula() -> None:
    # lapse = 0 -> dp/p = -rho g dz with rho = p/(R T0)
    pressure = isa_pressure(1000.0, lapse_rate_K_per_m=0.0)
    assert pressure == pytest.approx(101325.0 * math.exp(-9.80665 * 1000.0 / (287.05 * 288.15)), rel=1e-12)


def test_isa_density_is_pressure_over_r_times_t_discrete() -> None:
    for altitude in (0.0, 2500.0, 8000.0):
        t = isa_temperature(altitude)
        p = isa_pressure(altitude)
        assert isa_density(altitude) == pytest.approx(p / (287.05 * t), rel=1e-12)


# ==========================================================================
# 4. Transport properties -- Sutherland and the speed of sound
# ==========================================================================
def test_sutherland_viscosity_matches_the_published_table() -> None:
    # White, Fluid Mechanics 7th ed., table 1.1 (dry air):
    #   T = 0 degC   -> mu = 1.71e-5 Pa s   (this is Sutherland's own reference)
    #   T = 20 degC  -> mu = 1.825e-5 Pa s
    #   T = 100 degC -> mu = 2.181e-5 Pa s
    # Sutherland's law is itself a fit: it is expected to sit within ~0.5-0.7 %
    # of the measured tables over 0..100 degC (it slightly under-predicts near
    # room temperature), so 1e-2 relative is the honest bound.  Before the audit
    # the law was referenced to T0_ISA = 288.15 K, which put *every* value ~4 %
    # low -- 20x outside this band.
    for kelvin, expected in ((273.15, 1.71e-5), (293.15, 1.825e-5), (373.15, 2.181e-5)):
        assert sutherland_viscosity(kelvin) == pytest.approx(expected, rel=1e-2)
    # exact at the reference point itself
    assert sutherland_viscosity(273.15) == pytest.approx(1.716e-5, rel=1e-12)
    # the real viscosity at 15 degC is 1.789e-5, i.e. ~4.3 % above MU0_ISA; the
    # audit found the law referenced to T0_ISA = 288.15 K, which put it 4.1 % low.
    assert sutherland_viscosity(288.15) == pytest.approx(1.7894e-5, rel=1e-3)
    assert sutherland_viscosity(288.15) > 1.716e-5
    # the two fallback copies agree with the primary domain
    fallback = aero_domain._fallback_atmosphere(EnvironmentConfig(isa_mode=False))
    assert fallback[1] == pytest.approx(
        atmosphere_domain.kinematic_viscosity(288.15, fallback[0]), rel=1e-12
    )


def test_sound_speed_matches_the_closed_form() -> None:
    # a = sqrt(gamma R T); at ISA sea level 340.29 m/s
    assert speed_of_sound(288.15) == pytest.approx(340.294, rel=1e-5)
    assert speed_of_sound(288.15) == pytest.approx(math.sqrt(1.4 * 287.05 * 288.15), rel=1e-12)


def test_reynolds_and_mach_use_the_airframe_chord() -> None:
    cfg = AircraftConfig()
    chord = derived_properties(cfg)["ref_length_m"]
    for speed in (2.0, 6.0, 12.0):
        state = atmosphere_state(EnvironmentConfig(), speed, reference_chord_m=chord)
        expected_re = speed * chord / state["kinematic_viscosity_m2s"]
        assert state["reynolds"] == pytest.approx(expected_re, rel=1e-12)
        assert state["mach"] == pytest.approx(speed / state["sound_speed_mps"], rel=1e-12)
    # Re must follow the geometry: widening the wing at fixed area shortens the
    # MAC and lowers Re.  (The pre-audit code used a hard-coded 0.075 m chord, so
    # dragging the span slider left Re untouched.)
    wide = evaluate_aero(AircraftConfig(wingspan_m=0.36), EnvironmentConfig(), 6.0, 4.0)
    narrow = evaluate_aero(AircraftConfig(wingspan_m=0.12), EnvironmentConfig(), 6.0, 4.0)
    assert wide.reynolds < narrow.reynolds
    assert wide.reynolds != pytest.approx(narrow.reynolds, rel=1e-6)


def test_humid_air_is_less_dense_than_dry_air() -> None:
    # R_vapour (461.5) > R_air (287.05), so humid air is lighter at fixed T, p.
    dry = air_density(288.15, 101325.0, 0.0)
    wet = air_density(288.15, 101325.0, 1.0)
    assert wet < dry
    assert dry == pytest.approx(101325.0 / (287.05 * 288.15), rel=1e-12)
    # ideal-gas bookkeeping: rho = p / (R_moist T)
    payload = atmosphere_domain.evaluate_atmosphere(
        EnvironmentConfig(humidity_rel=0.5), reference_chord_m=0.075
    )
    assert payload["density_kgm3"] == pytest.approx(
        payload["pressure_Pa"] / (payload["specific_gas_constant_J_kgK"] * payload["temperature_K"]),
        rel=1e-12,
    )
    assert payload["specific_gas_constant_J_kgK"] > 287.05


def test_saturation_vapour_pressure_matches_the_alduchov_eskridge_table() -> None:
    # 610.94 exp(17.625 t / (t + 243.04)) [Pa] over supercooled liquid water.
    assert saturation_vapour_pressure_Pa(273.15) == pytest.approx(610.94, rel=1e-9)
    for celsius, expected in ((0.0, 611.2), (20.0, 2339.0), (-10.0, 286.5)):
        got = saturation_vapour_pressure_Pa(celsius + 273.15)
        assert got == pytest.approx(expected, rel=2e-2)


# ==========================================================================
# 5. Lift-curve slope -- Helmbold / Jensen
# ==========================================================================
def test_helmbold_slope_limits_and_closed_form() -> None:
    for aspect, taper in ((1.0, 0.05), (2.0, 0.5), (4.0, 0.7), (9.0, 1.0), (14.5, 0.4)):
        closed = 2.0 * math.pi * aspect / (
            2.0 + math.sqrt(aspect * aspect * (1.0 + taper) + 4.0 * aspect / math.pi)
        )
        assert helmbold_cl_alpha(aspect, taper) == pytest.approx(closed, rel=1e-12)
        # bounded by the 2 pi thin-airfoil value and strictly above zero
        assert 0.0 < helmbold_cl_alpha(aspect, taper) < 2.0 * math.pi
    # High-aspect-ratio limit: a0 -> 2 pi / sqrt(1 + lambda).  For a rectangular
    # wing that is 4.4429 /rad, the classic Helmbold finite-wing correction.
    assert helmbold_cl_alpha(1.0e6, 1.0) == pytest.approx(2.0 * math.pi / math.sqrt(2.0), rel=1e-5)
    assert helmbold_cl_alpha(1.0e6, 0.05) == pytest.approx(2.0 * math.pi / math.sqrt(1.05), rel=1e-5)


def test_lift_slope_matches_the_lifting_surface_estimate_used_in_the_polar() -> None:
    cfg = AircraftConfig()
    geom = derived_properties(cfg)
    slope = aero_domain.lift_slope_per_rad(cfg)
    assert slope == pytest.approx(
        helmbold_cl_alpha(geom["aspect_ratio"], geom["taper_ratio"]), rel=1e-12
    )
    # explicit override wins
    pinned = aero_domain.lift_slope_per_rad(AircraftConfig(cLalpha_per_rad=3.5))
    assert pinned == pytest.approx(3.5, rel=1e-12)


# ==========================================================================
# 6. Induced drag and the drag polar
# ==========================================================================
def test_induced_drag_follows_the_finite_wing_polar() -> None:
    # Raymer eq. 12.48: C_Di = C_L^2 / (pi e AR)
    cfg = AircraftConfig(cL0=0.0, cLalpha_per_rad=4.0, cd0=0.0, stall_alpha_deg=90.0)
    geom = derived_properties(cfg)
    polar_constant = math.pi * cfg.oswald_e * geom["aspect_ratio"]
    for alpha in (0.0, 2.0, 5.0, 9.0):
        cl = aero_domain.lift_coefficient(alpha, cfg, compressible=False)
        cd = drag_coefficient(alpha, cfg, reynolds=1.0e6, compressible=False)
        assert cd == pytest.approx(cl * cl / polar_constant, rel=1e-9)


def test_polar_minima_match_the_parabolic_polar_closed_form() -> None:
    cfg = AircraftConfig(cL0=0.0, cLalpha_per_rad=4.0, cd0=0.02, oswald_e=0.8, stall_alpha_deg=90.0)
    reynolds = 5.0e4
    minima = drag_polar_minima(cfg, reynolds=reynolds)
    geom = derived_properties(cfg)
    k = math.pi * cfg.oswald_e * geom["aspect_ratio"]
    cd0 = cfg.cd0 * (1.0e5 / reynolds) ** cfg.re_cd0_exponent
    assert minima["polar_constant"] == pytest.approx(k, rel=1e-12)
    assert minima["cd_min"] == pytest.approx(cd0, rel=1e-12)
    # (L/D)_max = 0.5 sqrt(k / C_D0) at C_L = sqrt(C_D0 k)
    assert minima["ld_max_theory"] == pytest.approx(0.5 * math.sqrt(k / cd0), rel=1e-12)
    assert minima["cl_at_ld_max"] == pytest.approx(math.sqrt(cd0 * k), rel=1e-12)
    assert minima["polar_constant"] == pytest.approx(k, rel=1e-12)


def test_parasite_drag_reduces_with_reynolds() -> None:
    cfg = AircraftConfig(cd0=0.02, re_cd0_exponent=0.15)
    values = [aero_domain.parasite_drag_coefficient(cfg, re) for re in (1.0e3, 1.0e4, 1.0e5, 1.0e6)]
    assert all(later < earlier for earlier, later in zip(values, values[1:]))
    assert aero_domain.parasite_drag_coefficient(cfg, 1.0e5) == pytest.approx(0.02, rel=1e-12)
    assert aero_domain.parasite_drag_coefficient(cfg, 1.0e4) == pytest.approx(
        0.02 * 10.0 ** 0.15, rel=1e-12
    )


# ==========================================================================
# 7. Compressibility -- Prandtl-Glauert
# ==========================================================================
def test_prandtl_glauert_closed_form_and_saturation() -> None:
    for mach in (0.0, 0.1, 0.3, 0.6, 0.8):
        assert prandtl_glauert(mach) == pytest.approx(
            1.0 / math.sqrt(1.0 - mach * mach), rel=1e-12
        )
    # monotone below the critical Mach, then held (never NaN)
    values = [prandtl_glauert(m) for m in (0.0, 0.2, 0.4, 0.6, 0.8, 0.84)]
    assert all(later > earlier for earlier, later in zip(values, values[1:]))
    held = prandtl_glauert(aero_domain.MACH_CRITICAL)
    for mach in (0.9, 1.0, 3.0, 25.0):
        assert prandtl_glauert(mach) == pytest.approx(held, rel=1e-12)
    assert math.isfinite(held)


# ==========================================================================
# 8. Fully separated flow -- Hoerner flat plate
# ==========================================================================
def test_flat_plate_lift_matches_the_hoerner_curve() -> None:
    # C_L = 2 sin a cos^2 a; peak 0.7698 at 35.26 deg; zero at 90 deg.
    for degrees in (0.0, 5.0, 15.0, 35.26, 60.0, 85.0, 90.0, 120.0):
        a = deg2rad(degrees)
        assert flat_plate_lift_coefficient(degrees) == pytest.approx(
            2.0 * math.sin(a) * math.cos(a) ** 2, rel=1e-12
        )
    # d/dalpha [2 sin a cos^2 a] = 0  =>  cos^2 a = 2 sin^2 a  =>  tan a = 1/sqrt(2)
    peak_angle = math.degrees(math.atan(1.0 / math.sqrt(2.0)))
    assert peak_angle == pytest.approx(35.264, abs=1e-3)
    assert flat_plate_lift_coefficient(peak_angle) == pytest.approx(0.7698, rel=1e-3)
    assert flat_plate_lift_coefficient(90.0) == pytest.approx(0.0, abs=1e-15)


# ==========================================================================
# 9. Ground effect -- McCormick
# ==========================================================================
def test_ground_effect_factor_free_air_value_is_unity() -> None:
    span = 0.20
    # disabling the model must reproduce the *free-air* polar (multiplier 1.0),
    # never delete the induced drag (multiplier 0.0)
    assert ground_effect_factor(0.5, span, enabled=False) == 1.0
    assert ground_effect_factor(0.5, span, enabled=True) < 1.0
    assert flight_sim.ground_effect_factor(0.5, span, enabled=False) == 1.0
    assert flight_sim.ground_effect_factor(0.5, span, enabled=True) < 1.0
    # both copies agree
    for height in (0.0, 0.02, 0.1, 0.5, 2.0, 50.0):
        assert flight_sim.ground_effect_factor(height, span) == pytest.approx(
            aero_domain.ground_effect_factor(height, span), rel=1e-12
        )


def test_ground_effect_reduces_only_the_induced_part() -> None:
    cfg = AircraftConfig()
    near = evaluate_aero(cfg, EnvironmentConfig(ground_effect_enabled=True), 9.0, 5.0, ground_height_m=0.02)
    far = evaluate_aero(cfg, EnvironmentConfig(ground_effect_enabled=True), 9.0, 5.0, ground_height_m=1.0e8)
    parasite = aero_domain.parasite_drag_coefficient(cfg, near.reynolds)
    assert near.cl == pytest.approx(far.cl, rel=1e-12)
    assert near.cd < far.cd
    # the parasite part is height independent, only C_Di is attenuated
    k = math.pi * cfg.oswald_e * derived_properties(cfg)["aspect_ratio"]
    assert (far.cd - parasite) == pytest.approx(far.cl * far.cl / k, rel=1e-9)
    assert (near.cd - parasite) == pytest.approx(far.cl * far.cl / k * near.ground_effect_ratio, rel=1e-9)


# ==========================================================================
# 10. Rigid-body dynamics -- Newton/Euler and the attitude kinematics
# ==========================================================================
def test_eulers_equation_conserves_angular_momentum_when_torque_free() -> None:
    # d/dt (I w) = 0 for M = 0.  Integrating angular_acceleration_body on a
    # torque-free asymmetric body must keep |L| constant to integrator accuracy.
    inertia = np.diag([2.0e-6, 6.0e-6, 1.1e-5])
    omega = np.array([3.0, -1.0, 0.5])
    dt = 1.0e-6
    steps = 20000
    momentum0 = float(np.linalg.norm(inertia @ omega))
    for _ in range(steps):
        omega = omega + angular_acceleration_body(inertia, omega, np.zeros(3)) * dt
    momentum = float(np.linalg.norm(inertia @ omega))
    assert momentum == pytest.approx(momentum0, rel=1e-6)
    # and the kinetic energy of the torque-free motion is constant
    def kinetic(w: np.ndarray) -> float:
        return 0.5 * float(w @ (inertia @ w))

    assert kinetic(omega) == pytest.approx(kinetic(np.array([3.0, -1.0, 0.5])), rel=1e-6)


def test_attitude_matrix_round_trips_for_every_euler_triple() -> None:
    for roll, pitch, yaw in ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 20.0, 0.0), (0.0, 0.0, 45.0),
                            (-30.0, 15.0, 100.0), (5.0, -60.0, -170.0)):
        quaternion = quaternion_from_euler(roll, pitch, yaw)
        matrix = quaternion_to_matrix(quaternion)
        assert np.allclose(matrix, euler_to_matrix(roll, pitch, yaw), atol=1e-12)
        # orthogonal: R R^T = I
        assert np.allclose(matrix @ matrix.T, np.eye(3), atol=1e-12)
        assert float(np.linalg.det(matrix)) == pytest.approx(1.0, rel=1e-12)
        assert matrix_to_euler(matrix) == pytest.approx((roll, pitch, yaw), abs=1e-9)
        # body -> world -> body is the identity
        vector = np.array([0.3, -0.5, 0.81])
        assert world_to_body(quaternion, body_to_world(quaternion, vector)) == pytest.approx(
            vector, abs=1e-12
        )


def test_pitch_up_is_positive_and_alpha_follows_it() -> None:
    # +z is down, so a positive rotation about +y raises the nose and alpha
    # (the angle between the nose and the relative wind) grows with it.
    q = quaternion_from_euler(0.0, 10.0, 0.0)
    body_velocity = world_to_body(q, np.array([10.0, 0.0, 0.0]))
    alpha = math.degrees(math.atan2(float(body_velocity[2]), float(body_velocity[0])))
    assert alpha == pytest.approx(10.0, rel=1e-9)
    # the wind-axis lift resolves "up", i.e. towards -z in the body frame
    a = deg2rad(10.0)
    lift_component = np.array([math.sin(a), 0.0, -math.cos(a)])
    assert lift_component[2] < 0.0
    # and it is perpendicular to the relative wind
    assert float(lift_component @ body_velocity) == pytest.approx(0.0, abs=1e-12)


def test_dynamic_pressure_and_force_identities() -> None:
    cfg = AircraftConfig()
    speed, alpha = 8.0, 4.0
    result = evaluate_aero(cfg, EnvironmentConfig(), speed, alpha)
    area = derived_properties(cfg)["ref_area_m2"]
    air = atmosphere_state(EnvironmentConfig(), speed, reference_chord_m=derived_properties(cfg)["ref_length_m"])
    # q = 0.5 rho V^2
    assert result.dynamic_pressure_pa == pytest.approx(
        0.5 * air["density_kgm3"] * speed * speed, rel=1e-12
    )
    # L = q S C_L, D = q S C_D
    assert result.lift_n == pytest.approx(result.dynamic_pressure_pa * area * result.cl, rel=1e-12)
    assert result.drag_n == pytest.approx(result.dynamic_pressure_pa * area * result.cd, rel=1e-12)
    # M = q S c C_m for the centred configuration
    chord = derived_properties(cfg)["ref_length_m"]
    assert result.pitching_moment_nm == pytest.approx(
        result.dynamic_pressure_pa * area * chord * result.cm, rel=1e-12
    )
    # the reported circulation satisfies L = rho V Gamma b
    density = air["density_kgm3"]
    span = derived_properties(cfg)["ref_span_m"]
    assert result.lift_n == pytest.approx(
        density * speed * result.circulation_m2ps * span, rel=1e-9
    )


def test_circulation_inverts_the_lift_identity() -> None:
    cfg = AircraftConfig()
    geom = derived_properties(cfg)
    for speed, alpha in ((5.0, 3.0), (9.0, 7.0)):
        gamma = aero_domain.circulation(cfg, speed, alpha)
        lift = 1.225 * speed * gamma * geom["ref_span_m"]
        cl = aero_domain.lift_coefficient(alpha, cfg, compressible=False)
        assert lift == pytest.approx(0.5 * 1.225 * speed * speed * geom["ref_area_m2"] * cl, rel=1e-9)


# ==========================================================================
# 11. Pitching-moment trim -- sign conventions
# ==========================================================================
def test_cg_offset_sign_is_aft_cg_is_nose_up() -> None:
    lift = 0.25
    aft = aero_domain.pitching_moment_offset_nm(AircraftConfig(cg_offset_x=0.01), lift)
    forward = aero_domain.pitching_moment_offset_nm(AircraftConfig(cg_offset_x=-0.01), lift)
    # an aft CG places the AC ahead of the CG, producing a destabilising nose-up moment
    assert aft == pytest.approx(+0.01 * lift, rel=1e-12)
    # a forward CG places the AC behind the CG, producing a stabilising nose-down moment
    assert forward == pytest.approx(-0.01 * lift, rel=1e-12)
    assert forward == pytest.approx(-aft, rel=1e-12)
    assert aero_domain.pitching_moment_offset_nm(AircraftConfig(cg_offset_x=0.0), lift) == 0.0


def test_trim_nulls_the_static_pitching_moment() -> None:
    cfg = AircraftConfig()
    env = EnvironmentConfig()
    for elevon in (-10.0, 0.0, 5.0, 15.0):
        solution = aero_domain.trim_solution(cfg, env, 6.0, elevon_deg=elevon)
        assert solution["residual_cm"] == pytest.approx(0.0, abs=1e-9)
        assert solution["trimmed"] is True
        assert solution["elevon_deg"] == pytest.approx(elevon, rel=1e-12)


# ==========================================================================
# 12. ISA is referred to the height above sea level
# ==========================================================================
def test_isa_column_adds_the_environment_reference_altitude() -> None:
    # flight_sim.atmosphere_state takes a height *above the ground plane*; the
    # ISA barometric column is a function of the height above sea level.  A
    # plateau scene at altitude_m = 1000 m flown from the ground must therefore
    # see exactly the column a sea-level scene sees from 1000 m up.
    plateau_ground = flight_sim.atmosphere_state(
        EnvironmentConfig(altitude_m=1000.0, humidity_rel=0.0), 0.0, 0.075
    )
    sea_level_high = flight_sim.atmosphere_state(
        EnvironmentConfig(altitude_m=0.0, humidity_rel=0.0), 1000.0, 0.075
    )
    assert plateau_ground["density_kgm3"] == pytest.approx(sea_level_high["density_kgm3"], rel=1e-12)
    # and a sea-level scene at ground level is denser than the plateau one
    at_sea_level = flight_sim.atmosphere_state(
        EnvironmentConfig(altitude_m=0.0, humidity_rel=0.0), 0.0, 0.075
    )
    assert plateau_ground["density_kgm3"] < at_sea_level["density_kgm3"]
    assert at_sea_level["density_kgm3"] / plateau_ground["density_kgm3"] == pytest.approx(
        isa_density(0.0) / isa_density(1000.0), rel=1e-9
    )
