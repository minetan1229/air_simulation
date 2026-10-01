"""Analytic verification of the atmosphere and wind domains.

Every check that has a closed-form solution is compared against it:

* ISA temperature / pressure / density against the tabulated troposphere,
* the ISA barometric formula against a ``scipy.integrate.quad`` integration of
  the hydrostatic equation ``dp/dh = -rho g``,
* Sutherland viscosity against its closed form (and against ``MU0_ISA`` exactly
  at the ISA reference temperature),
* moist air density against the mixing-ratio blend and the Tetens vapour
  pressure law,
* the speed of sound / Mach / Reynolds identities,
* the atmospheric boundary layer against the analytic log law,
* the thermal plume against its closed-form Gaussian column.

Reference table used for ISA (troposphere, ``T0 = 288.15 K``,
``p0 = 101325 Pa``, ``L = 6.5e-3 K/m``, ``g = 9.80665 m/s^2``)::

    h [m]      T [K]        p [Pa]        rho [kg/m^3]
       0    288.15        101325.0        1.22501
    5000    255.65         54019.9        0.73612
   11000    216.65         22632.1        0.36392

(Metrological convention note: the wind domain treats the third component as
the *height above the ground plane, positive up*, because the log law, the
ground plane and ``reference_height_m`` all live on that axis.  ``north = +y``,
``east = +x``.  ``wind_direction_deg`` is the meteorological "from" bearing, so
0 deg -- wind from the north -- makes the flow point towards ``-y``.)
"""

from __future__ import annotations

import math
import pathlib
import sys

import numpy as np
import pytest
from scipy.integrate import quad

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from physics import atmosphere_domain as atmo  # noqa: E402
from physics import wind_domain as wind  # noqa: E402
from physics.config_schema import (  # noqa: E402
    GAMMA_AIR,
    G0,
    LAPSE_RATE_ISA,
    MU0_ISA,
    MU_SUTHERLAND,
    P0_ISA,
    R_AIR,
    R_VAPOR,
    T0_ISA,
    VON_KARMAN,
    EnvironmentConfig,
    clamp,
    deg2rad,
)

# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------
ISA_REFERENCE = (
    # altitude_m, temperature_K, pressure_Pa, density_kgm3
    (0.0, 288.15, 101325.0, 1.22501),
    (5000.0, 255.65, 54019.9, 0.73612),
    (11000.0, 216.65, 22632.1, 0.36392),
)

TETENS_A = 610.2
TETENS_B = 17.27
TETENS_C = 237.3
TETENS_D = 243.04


def tetens_pa(temperature_K: float) -> float:
    """Classic Magnus / Tetens saturation vapour pressure (independent form)."""
    t_c = temperature_K - 273.15
    return TETENS_A * math.exp(TETENS_B * t_c / (t_c + TETENS_C))


def relative_error(actual: float, expected: float) -> float:
    return abs(float(actual) - float(expected)) / abs(float(expected))


# ==========================================================================
# ISA standard atmosphere
# ==========================================================================
@pytest.mark.parametrize("altitude,T_ref,p_ref,rho_ref", ISA_REFERENCE)
def test_isa_matches_tabulated_values(altitude, T_ref, p_ref, rho_ref):
    assert relative_error(atmo.isa_temperature(altitude), T_ref) < 1.0e-3
    assert relative_error(atmo.isa_pressure(altitude), p_ref) < 1.0e-3
    assert relative_error(atmo.isa_density(altitude), rho_ref) < 1.0e-3


def test_isa_lapse_rate_is_linear():
    for altitude in (0.0, 1234.5, 5000.0, 10999.0):
        expected = T0_ISA - LAPSE_RATE_ISA * altitude
        assert atmo.isa_temperature(altitude) == pytest.approx(expected, rel=1.0e-15)


def test_isa_is_monotonic_in_altitude():
    altitudes = np.linspace(0.0, 11000.0, 200)
    temperatures = np.array([atmo.isa_temperature(h) for h in altitudes])
    pressures = np.array([atmo.isa_pressure(h) for h in altitudes])
    densities = np.array([atmo.isa_density(h) for h in altitudes])
    assert np.all(np.diff(temperatures) < 0.0)
    assert np.all(np.diff(pressures) < 0.0)
    assert np.all(np.diff(densities) < 0.0)


def test_isa_barometric_formula_is_the_hydrostatic_solution():
    """Integrate dp/dh = -rho g and compare with the closed form pressure."""
    top = 5000.0
    gravity = G0
    weight, abs_error = quad(
        lambda h: atmo.isa_density(h, gravity=gravity) * gravity,
        0.0,
        top,
        epsabs=1.0e-8,
        epsrel=1.0e-12,
        limit=200,
    )
    assert abs_error < 1.0e-6
    integrated_pressure = P0_ISA - weight
    closed_form = atmo.isa_pressure(top, gravity=gravity)
    assert relative_error(integrated_pressure, closed_form) < 1.0e-4


def test_isa_hydrostatic_consistency_for_custom_gravity():
    """The same identity must hold for a runtime-mutated gravity."""
    gravity = 3.7207  # Mars, to prove g is not hard coded
    top = 4000.0
    weight, _ = quad(
        lambda h: atmo.isa_density(h, gravity=gravity) * gravity,
        0.0,
        top,
        epsabs=1.0e-8,
        epsrel=1.0e-12,
        limit=200,
    )
    integrated_pressure = P0_ISA - weight
    assert relative_error(integrated_pressure, atmo.isa_pressure(top, gravity=gravity)) < 1.0e-4
    # A weaker gravity must give a higher pressure at the same altitude.
    assert atmo.isa_pressure(top, gravity=gravity) > atmo.isa_pressure(top, gravity=G0)


def test_isa_sea_level_reference_is_configurable():
    env = EnvironmentConfig(
        isa_mode=True,
        sea_level_temperature_K=300.0,
        sea_level_pressure_Pa=90000.0,
        lapse_rate_K_per_m=0.004,
        altitude_m=2500.0,
    )
    result = atmo.evaluate_atmosphere(env)
    assert result["temperature_K"] == pytest.approx(300.0 - 0.004 * 2500.0, rel=1.0e-12)
    assert result["pressure_Pa"] > 0.0
    assert result["isa_mode"] is True


def test_isa_scale_altitude():
    assert atmo.scale_altitude_m(LAPSE_RATE_ISA) == pytest.approx(T0_ISA / LAPSE_RATE_ISA, rel=1.0e-15)
    assert math.isinf(atmo.scale_altitude_m(0.0))


# ==========================================================================
# Sutherland viscosity
# ==========================================================================
def test_sutherland_equals_mu0_at_isa_temperature():
    assert atmo.sutherland_viscosity(T0_ISA) == MU0_ISA
    assert relative_error(atmo.sutherland_viscosity(T0_ISA), MU0_ISA) < 1.0e-12


@pytest.mark.parametrize("temperature", [200.0, 250.0, 273.15, 300.0, 350.0, 400.0])
def test_sutherland_matches_closed_form(temperature):
    expected = (
        MU0_ISA
        * (temperature / T0_ISA) ** 1.5
        * (T0_ISA + MU_SUTHERLAND)
        / (temperature + MU_SUTHERLAND)
    )
    assert relative_error(atmo.sutherland_viscosity(temperature), expected) < 1.0e-12


def test_sutherland_is_monotonic_in_temperature():
    temperatures = np.linspace(150.0, 500.0, 400)
    values = np.array([atmo.sutherland_viscosity(t) for t in temperatures])
    assert np.all(np.diff(values) > 0.0)
    # Sutherland's law is a power law with exponent 1.5 up to the additive term.
    assert values[0] < MU0_ISA < values[-1]


def test_kinematic_viscosity_is_mu_over_rho():
    density = 1.2250122659906946
    expected = atmo.sutherland_viscosity(288.15) / density
    assert atmo.kinematic_viscosity(288.15, density) == pytest.approx(expected, rel=1.0e-15)
    assert atmo.kinematic_viscosity(288.15, 0.0) == 0.0
    assert atmo.kinematic_viscosity(288.15, -1.0) == 0.0
    assert atmo.kinematic_viscosity(288.15, density) == pytest.approx(1.4008e-5, rel=1.0e-4)


# ==========================================================================
# Moist air
# ==========================================================================
def test_saturation_vapour_pressure_reference_points():
    assert atmo.saturation_vapour_pressure_Pa(273.15) == pytest.approx(610.94, rel=1.0e-6)
    assert atmo.saturation_vapour_pressure_Pa(288.15) == pytest.approx(1702.0, rel=2.0e-3)
    assert atmo.saturation_vapour_pressure_Pa(293.15) == pytest.approx(2333.4, rel=2.0e-3)


@pytest.mark.parametrize("temperature", [263.15, 273.15, 283.15, 293.15, 303.15, 323.15])
def test_saturation_vapour_pressure_matches_tetens(temperature):
    """``p_v = phi p_sat`` verified against the independent Tetens formula."""
    assert relative_error(atmo.saturation_vapour_pressure_Pa(temperature), tetens_pa(temperature)) < 5.0e-3


def test_vapour_partial_pressure_is_phi_times_saturation():
    temperature = 288.15
    pressure = 101325.0
    for phi in (0.0, 0.25, 0.5, 1.0):
        p_v = atmo.vapour_partial_pressure_Pa(temperature, phi, pressure)
        assert p_v == pytest.approx(phi * atmo.saturation_vapour_pressure_Pa(temperature), rel=1.0e-12)
    # Above water saturation cannot exceed the total pressure.
    assert atmo.vapour_partial_pressure_Pa(373.15, 1.0, 101325.0) < 101325.0


def test_air_density_humidity_dependence():
    dry = atmo.air_density(288.15, 101325.0, 0.0)
    damp = atmo.air_density(288.15, 101325.0, 1.0)
    assert dry > damp
    assert dry == pytest.approx(1.2250, rel=1.0e-2)
    assert atmo.air_density(288.15, 101325.0, 0.0) == pytest.approx(
        101325.0 / (R_AIR * 288.15), rel=1.0e-15
    )
    humidities = np.linspace(0.0, 1.0, 25)
    densities = np.array([atmo.air_density(288.15, 101325.0, h) for h in humidities])
    assert np.all(np.diff(densities) < 0.0)


def test_moist_air_gas_constant_blend():
    temperature, pressure = 288.15, 101325.0
    phi = 0.6
    p_v = atmo.vapour_partial_pressure_Pa(temperature, phi, pressure)
    p_d = pressure - p_v
    expected = (p_d * R_AIR + p_v * R_VAPOR) / pressure
    assert atmo.moist_air_gas_constant(temperature, pressure, phi) == pytest.approx(expected, rel=1.0e-15)
    assert atmo.moist_air_gas_constant(temperature, pressure, 0.0) == pytest.approx(R_AIR, rel=1.0e-15)
    assert R_VAPOR > R_AIR  # the reason humid air is lighter


def test_air_density_requires_positive_pressure():
    with pytest.raises(ValueError):
        atmo.air_density(288.15, 0.0, 0.0)


# ==========================================================================
# Speed of sound, Mach and Reynolds
# ==========================================================================
def test_speed_of_sound_reference():
    assert atmo.speed_of_sound(288.15) == pytest.approx(340.29, rel=1.0e-3)
    assert atmo.speed_of_sound(288.15) == pytest.approx(
        math.sqrt(GAMMA_AIR * R_AIR * 288.15), rel=1.0e-15
    )
    assert atmo.speed_of_sound(273.15) == pytest.approx(331.3, rel=1.0e-3)


@pytest.mark.parametrize("speed", [0.0, 5.0, 12.0, 30.0, 120.0])
@pytest.mark.parametrize("temperature", [216.65, 273.15, 288.15, 320.0])
def test_mach_identity(speed, temperature):
    assert atmo.mach_number(speed, temperature) == pytest.approx(
        speed / atmo.speed_of_sound(temperature), rel=1.0e-15
    )


def test_reynolds_identity_roundtrip():
    speed, chord = 12.0, 0.075
    density = atmo.air_density(288.15, 101325.0, 0.5)
    nu = atmo.kinematic_viscosity(288.15, density)
    reynolds = atmo.reynolds_number(speed, chord, nu)
    assert reynolds == pytest.approx(speed * chord / nu, rel=1.0e-15)
    # Invert: nu = V c / Re
    assert speed * chord / reynolds == pytest.approx(nu, rel=1.0e-15)
    assert atmo.reynolds_number(0.0, chord, nu) == 0.0
    assert atmo.reynolds_number(speed, 0.0, nu) == 0.0
    assert atmo.reynolds_number(speed, chord, 0.0) == 0.0


def test_reynolds_increases_with_speed_and_chord():
    nu = atmo.kinematic_viscosity(288.15, 1.225)
    speeds = [2.0, 6.0, 12.0, 24.0]
    chords = [0.05, 0.075, 0.10, 0.20]
    for chord in chords:
        previous = -1.0
        for speed in speeds:
            current = atmo.reynolds_number(speed, chord, nu)
            assert current > previous
            previous = current
    # Doubling the chord exactly doubles Re, as does doubling the speed.
    assert atmo.reynolds_number(12.0, 0.2, nu) == pytest.approx(
        2.0 * atmo.reynolds_number(12.0, 0.1, nu), rel=1.0e-12
    )
    assert atmo.reynolds_number(24.0, 0.1, nu) == pytest.approx(
        2.0 * atmo.reynolds_number(12.0, 0.1, nu), rel=1.0e-12
    )
    # Sweeping the full chord range must also be monotonically increasing.
    previous = -1.0
    for chord in chords:
        current = atmo.reynolds_number(12.0, chord, nu)
        assert current > previous
        previous = current


# ==========================================================================
# evaluate_atmosphere
# ==========================================================================
REQUIRED_ATMOSPHERE_KEYS = (
    "temperature_K",
    "pressure_Pa",
    "density_kgm3",
    "dynamic_viscosity_Pa_s",
    "kinematic_viscosity_m2s",
    "sound_speed_mps",
    "reynolds",
    "mach_reference",
    "specific_heat_ratio",
    "scale_altitude_m",
    "isa_mode",
    "backend",
    "gravity",
    "drag_scale",
    "lift_scale",
    "ground_friction",
    "ground_effect_enabled",
    "compressibility_enabled",
    "wind_tunnel_enabled",
    "wind_tunnel_speed",
    "wind_speed",
    "wind_direction_deg",
    "gust_intensity",
    "thermal_strength",
)


def test_evaluate_atmosphere_returns_every_required_key():
    env = EnvironmentConfig()
    result = atmo.evaluate_atmosphere(env)
    for key in REQUIRED_ATMOSPHERE_KEYS:
        assert key in result, f"missing key {key}"
    assert result["backend"] == "numpy"
    assert result["specific_heat_ratio"] == GAMMA_AIR
    assert isinstance(result["isa_mode"], bool)
    assert isinstance(result["wind_tunnel_enabled"], bool)
    for key in ("temperature_K", "pressure_Pa", "density_kgm3", "kinematic_viscosity_m2s", "sound_speed_mps"):
        assert math.isfinite(result[key]) and result[key] > 0.0
    assert result["reynolds"] >= 0.0
    assert result["mach_reference"] >= 0.0


def test_evaluate_atmosphere_json_serialisable():
    import json

    env = EnvironmentConfig()
    payload = json.dumps(atmo.evaluate_atmosphere(env))
    assert "temperature_K" in payload


def test_evaluate_atmosphere_gravity_changes_isa_density():
    env_earth = EnvironmentConfig(isa_mode=True, altitude_m=3000.0, gravity=9.80665, humidity_rel=0.0)
    env_mars = EnvironmentConfig(isa_mode=True, altitude_m=3000.0, gravity=3.7207, humidity_rel=0.0)
    earth = atmo.evaluate_atmosphere(env_earth)
    mars = atmo.evaluate_atmosphere(env_mars)
    assert earth["temperature_K"] == pytest.approx(mars["temperature_K"], rel=1.0e-15)
    assert mars["density_kgm3"] > earth["density_kgm3"]
    assert earth["gravity"] == pytest.approx(9.80665, rel=1.0e-15)
    assert mars["gravity"] == pytest.approx(3.7207, rel=1.0e-15)
    # The whole column must follow the runtime gravity, not a hard coded one.
    assert earth["pressure_Pa"] != pytest.approx(mars["pressure_Pa"], rel=1.0e-6)


def test_evaluate_atmosphere_temperature_monotonic():
    base = EnvironmentConfig(isa_mode=False, humidity_rel=0.0)
    previous_density = None
    previous_viscosity = None
    for temperature in (250.0, 280.0, 288.15, 300.0, 330.0):
        env = EnvironmentConfig(
            isa_mode=False, temperature_K=temperature, pressure_Pa=101325.0, humidity_rel=0.0
        )
        result = atmo.evaluate_atmosphere(env)
        density = result["density_kgm3"]
        viscosity = result["dynamic_viscosity_Pa_s"]
        if previous_density is not None:
            assert density < previous_density  # rho = p / (R T) falls with T
            assert viscosity > previous_viscosity  # Sutherland rises with T
        previous_density = density
        previous_viscosity = viscosity
        assert result["kinematic_viscosity_m2s"] == pytest.approx(viscosity / density, rel=1.0e-15)
    assert base.isa_mode is False


def test_evaluate_atmosphere_isa_mode_switch():
    local = EnvironmentConfig(
        isa_mode=False, temperature_K=300.0, pressure_Pa=95000.0, humidity_rel=0.0, altitude_m=5000.0
    )
    isa = EnvironmentConfig(
        isa_mode=True,
        sea_level_temperature_K=288.15,
        sea_level_pressure_Pa=101325.0,
        lapse_rate_K_per_m=0.0065,
        humidity_rel=0.0,
        altitude_m=5000.0,
    )
    local_result = atmo.evaluate_atmosphere(local)
    isa_result = atmo.evaluate_atmosphere(isa)
    assert local_result["temperature_K"] == pytest.approx(300.0, rel=1.0e-15)
    assert local_result["pressure_Pa"] == pytest.approx(95000.0, rel=1.0e-15)
    assert isa_result["temperature_K"] == pytest.approx(255.65, rel=1.0e-12)
    assert isa_result["isa_mode"] is True
    assert local_result["isa_mode"] is False
    # The explicit altitude argument overrides env.altitude_m.
    at_1000 = atmo.evaluate_atmosphere(isa, altitude_m=1000.0)
    assert at_1000["temperature_K"] == pytest.approx(288.15 - 6.5, rel=1.0e-12)
    assert at_1000["altitude_m"] == pytest.approx(1000.0, rel=1.0e-15)


def test_evaluate_atmosphere_humidity_applies_in_both_modes():
    dry = atmo.evaluate_atmosphere(EnvironmentConfig(isa_mode=True, humidity_rel=0.0))
    wet = atmo.evaluate_atmosphere(EnvironmentConfig(isa_mode=True, humidity_rel=1.0))
    assert dry["density_kgm3"] > wet["density_kgm3"]
    assert dry["temperature_K"] == pytest.approx(wet["temperature_K"], rel=1.0e-15)

    dry_local = atmo.evaluate_atmosphere(EnvironmentConfig(isa_mode=False, humidity_rel=0.0))
    wet_local = atmo.evaluate_atmosphere(EnvironmentConfig(isa_mode=False, humidity_rel=1.0))
    assert dry_local["density_kgm3"] > wet_local["density_kgm3"]
    assert wet_local["specific_gas_constant_J_kgK"] > dry_local["specific_gas_constant_J_kgK"]


def test_evaluate_atmosphere_wind_tunnel_reference_speed():
    env = EnvironmentConfig(wind_speed=3.0, wind_tunnel_enabled=True, wind_tunnel_speed=25.0)
    result = atmo.evaluate_atmosphere(env, reference_chord_m=0.08)
    assert result["reference_speed_mps"] == pytest.approx(25.0, rel=1.0e-15)
    expected_re = 25.0 * 0.08 / result["kinematic_viscosity_m2s"]
    assert result["reynolds"] == pytest.approx(expected_re, rel=1.0e-12)
    assert result["mach_reference"] == pytest.approx(
        25.0 / result["sound_speed_mps"], rel=1.0e-12
    )


# ==========================================================================
# Mean wind and the meteorological direction convention
# ==========================================================================
@pytest.mark.parametrize(
    "bearing,expected_unit",
    [
        (0.0, (0.0, -1.0, 0.0)),      # from the north -> blows towards -y
        (90.0, (-1.0, 0.0, 0.0)),     # from the east  -> blows towards -x
        (180.0, (0.0, 1.0, 0.0)),     # from the south -> blows towards +y
        (270.0, (1.0, 0.0, 0.0)),     # from the west  -> blows towards +x
    ],
)
def test_meteorological_wind_direction_convention(bearing, expected_unit):
    """``wind_direction_deg`` is the bearing the wind blows FROM (north = +y)."""
    env = EnvironmentConfig(wind_speed=10.0, wind_direction_deg=bearing)
    mean = wind.mean_wind_vector(env)
    assert np.allclose(mean, np.array(expected_unit) * 10.0, rtol=0.0, atol=1.0e-12)
    # Cross-check against the trigonometric definition used in the module.
    theta = deg2rad(bearing)
    assert mean[0] == pytest.approx(-10.0 * math.sin(theta), rel=1.0e-12)
    assert mean[1] == pytest.approx(-10.0 * math.cos(theta), rel=1.0e-12)
    assert mean[2] == 0.0
    unit = wind.wind_direction_unit(env)
    assert float(np.linalg.norm(unit)) == pytest.approx(1.0, rel=1.0e-15)


@pytest.mark.parametrize("bearing", [0.0, 37.0, 90.0, 123.4, 180.0, 270.0, 359.9])
def test_mean_wind_magnitude_equals_wind_speed(bearing):
    speed = 7.5
    env = EnvironmentConfig(wind_speed=speed, wind_direction_deg=bearing)
    mean = wind.mean_wind_vector(env)
    assert float(np.linalg.norm(mean)) == pytest.approx(speed, rel=1.0e-15)
    assert mean[2] == 0.0


def test_mean_wind_is_zero_without_speed():
    env = EnvironmentConfig(wind_speed=0.0, wind_direction_deg=123.0)
    assert np.array_equal(wind.mean_wind_vector(env), np.zeros(3))


def test_wind_velocity_magnitude_matches_wind_speed_without_gusts_or_thermals():
    env = EnvironmentConfig(
        wind_speed=9.0,
        wind_direction_deg=45.0,
        gust_intensity=0.0,
        thermal_strength=0.0,
        boundary_layer_enabled=True,
        reference_height_m=1.0,
        surface_roughness_m=0.0008,
    )
    assert wind.gust_factor(env, 3.0) == 1.0
    velocity = wind.wind_velocity([0.0, 0.0, env.reference_height_m], env, 3.0)
    assert float(np.linalg.norm(velocity)) == pytest.approx(9.0, rel=1.0e-12)
    assert velocity[2] == 0.0


def test_gust_factor_bounds():
    env = EnvironmentConfig(gust_intensity=0.4)
    times = np.linspace(0.0, 120.0, 500)
    factors = np.array([wind.gust_factor(env, float(t)) for t in times])
    assert np.all(factors >= 1.0 - 0.4 - 1.0e-12)
    assert np.all(factors <= 1.0 + 0.4 + 1.0e-12)
    assert wind.gust_factor(EnvironmentConfig(gust_intensity=0.0), 5.0) == 1.0
    assert wind.gust_factor(EnvironmentConfig(gust_intensity=0.0)) == 1.0
    assert float(np.max(factors) - np.min(factors)) > 0.1  # the gust actually oscillates


# ==========================================================================
# 3D simplex turbulence
# ==========================================================================
def test_simplex_noise_is_deterministic_for_a_fixed_seed():
    points = np.random.default_rng(0).uniform(-40.0, 40.0, (1000, 3))
    first = wind.simplex_noise3(points, seed=12345)
    second = wind.simplex_noise3(points, seed=12345)
    assert np.array_equal(first, second)
    assert first.dtype == np.float64
    assert first.shape == (1000,)


def test_simplex_noise_differs_between_seeds():
    points = np.random.default_rng(1).uniform(-40.0, 40.0, (2000, 3))
    a = wind.simplex_noise3(points, seed=1)
    b = wind.simplex_noise3(points, seed=2)
    c = wind.simplex_noise3(points, seed=99)
    assert not np.array_equal(a, b)
    assert not np.array_equal(a, c)
    assert float(np.max(np.abs(a - b))) > 0.5
    # Different seeds, identical statistics.
    assert abs(float(np.std(a)) - float(np.std(b))) < 0.05


def test_simplex_noise_range_and_mean():
    points = np.random.default_rng(2).uniform(-50.0, 50.0, (5000, 3))
    values = wind.simplex_noise3(points, seed=12345)
    assert np.all(values >= -1.0)
    assert np.all(values <= 1.0)
    assert abs(float(np.mean(values))) < 0.1
    assert 0.25 < float(np.std(values)) < 0.55


def test_simplex_noise_scalar_and_vector_paths_agree():
    points = np.random.default_rng(3).uniform(-10.0, 10.0, (64, 3))
    batched = wind.simplex_noise3(points, seed=7)
    single = np.array([wind.simplex_noise3(point, seed=7)[0] for point in points])
    assert np.array_equal(batched, single)
    # Flat 3*N input is reshaped to (N, 3).
    assert np.array_equal(wind.simplex_noise3(points.reshape(-1), seed=7), batched)
    # A bare scalar must not raise: it is broadcast to a single (1, 1, 1) sample.
    assert wind.simplex_noise3(2.5, seed=7).shape == (1,)
    assert wind.simplex_noise3(np.array(2.5), seed=7).shape == (1,)


def test_simplex_noise_is_smooth():
    base = np.array([[12.34, -5.67, 8.90]])
    coarse = wind.simplex_noise3(base + np.array([0.0, 0.0, 0.0]), seed=3)[0]
    fine = wind.simplex_noise3(base + np.array([1.0e-4, 0.0, 0.0]), seed=3)[0]
    assert abs(float(coarse - fine)) < 1.0e-2
    far = wind.simplex_noise3(base + np.array([0.05, 0.0, 0.0]), seed=3)[0]
    assert abs(float(coarse - far)) > 1.0e-4


def test_turbulence_amplitude_scales_with_the_documented_product():
    env = EnvironmentConfig(wind_speed=10.0, gust_intensity=0.3, turbulence_scale=0.5)
    assert wind.turbulence_amplitude(env) == pytest.approx(0.3 * 10.0 * 0.5, rel=1.0e-15)
    assert wind.turbulence_amplitude(EnvironmentConfig(wind_speed=0.0)) == 0.0
    assert wind.turbulence_amplitude(EnvironmentConfig(gust_intensity=0.0)) == 0.0


def test_turbulence_perturbs_the_steady_flow():
    calm = EnvironmentConfig(
        wind_speed=8.0, wind_direction_deg=0.0, gust_intensity=0.0, thermal_strength=0.0
    )
    gusty = EnvironmentConfig(
        wind_speed=8.0,
        wind_direction_deg=0.0,
        gust_intensity=0.5,
        turbulence_scale=1.0,
        thermal_strength=0.0,
        turbulence_seed=31337,
    )
    points = np.array([[0.0, 0.0, 1.0], [0.5, -0.5, 1.2], [1.0, 0.25, 0.9]])
    steady = wind.wind_velocity(points, calm, 0.0)
    noisy = wind.wind_velocity(points, gusty, 0.0)
    assert np.max(np.abs(noisy - steady)) > 0.5
    assert np.all(np.isfinite(noisy))


# ==========================================================================
# Atmospheric boundary layer
# ==========================================================================
def test_boundary_layer_matches_reference_height_exactly():
    profile = wind.boundary_layer_profile
    roughness, reference_height, speed = 0.0008, 1.0, 9.0
    assert profile(reference_height, roughness, speed, reference_height, True) == pytest.approx(
        speed, rel=1.0e-12
    )
    assert profile(2.5, roughness, speed, 2.5, True) == pytest.approx(speed, rel=1.0e-12)
    assert profile(0.05, roughness, 4.0, 0.05, True) == pytest.approx(4.0, rel=1.0e-12)


def test_boundary_layer_matches_the_log_law_analytically():
    roughness, reference_height, speed = 0.002, 2.0, 10.0
    for height in (0.01, 0.1, 1.0, 5.0, 20.0, 100.0):
        expected = speed * math.log(height / roughness) / math.log(reference_height / roughness)
        assert wind.boundary_layer_profile(height, roughness, speed, reference_height, True) == (
            pytest.approx(expected, rel=1.0e-12)
        )


def test_boundary_layer_is_monotonic_and_non_negative():
    roughness, reference_height, speed = 0.001, 1.0, 12.0
    heights = np.linspace(0.0, 200.0, 4001)
    values = np.array(
        [wind.boundary_layer_profile(float(h), roughness, speed, reference_height, True) for h in heights]
    )
    assert np.all(values >= 0.0)
    assert np.all(np.diff(values) >= 0.0)
    assert values[-1] > values[len(values) // 2] > values[1]


def test_boundary_layer_is_zero_at_or_below_the_roughness_length():
    for height in (-5.0, 0.0, 1.0e-9, 0.0008):
        assert wind.boundary_layer_profile(height, 0.0008, 10.0, 1.0, True) == 0.0
    # No logarithmic blow-up even for absurdly negative heights.
    assert math.isfinite(wind.boundary_layer_profile(-1000.0, 0.0008, 10.0, 1.0, True))


def test_boundary_layer_disabled_returns_the_uniform_speed():
    assert wind.boundary_layer_profile(0.0001, 0.0008, 10.0, 1.0, False) == 10.0
    assert wind.boundary_layer_profile(500.0, 0.0008, 10.0, 1.0, False) == 10.0
    assert wind.boundary_layer_profile(0.0, 0.0008, 3.25, 1.0, False) == 3.25


def test_boundary_layer_ratio_helper_agrees_with_the_scalar_profile():
    env = EnvironmentConfig(surface_roughness_m=0.0008, reference_height_m=1.0)
    heights = np.array([0.0, 0.0005, 0.01, 0.5, 1.0, 5.0, 50.0])
    ratios = wind.boundary_layer_ratio(heights, env)
    for height, ratio in zip(heights, ratios):
        expected = wind.boundary_layer_profile(
            float(height), env.surface_roughness_m, 1.0, env.reference_height_m, True
        )
        assert float(ratio) == pytest.approx(expected, rel=1.0e-12)


def test_boundary_layer_shapes_the_wind_velocity():
    env = EnvironmentConfig(
        wind_speed=10.0,
        wind_direction_deg=0.0,
        gust_intensity=0.0,
        thermal_strength=0.0,
        boundary_layer_enabled=True,
        reference_height_m=1.0,
        surface_roughness_m=0.0008,
    )
    at_surface = wind.wind_velocity([0.0, 0.0, 0.0], env, 0.0)
    low = wind.wind_velocity([0.0, 0.0, 0.1], env, 0.0)
    high = wind.wind_velocity([0.0, 0.0, 10.0], env, 0.0)
    assert np.allclose(at_surface, np.zeros(3))
    assert float(np.linalg.norm(low)) < float(np.linalg.norm(high))
    # Below the reference height the log law slows the flow down ...
    expected_low = 10.0 * math.log(0.1 / 0.0008) / math.log(1.0 / 0.0008)
    assert float(np.linalg.norm(low)) == pytest.approx(expected_low, rel=1.0e-12)
    # ... and above it the profile keeps growing logarithmically.
    expected_high = 10.0 * math.log(10.0 / 0.0008) / math.log(1.0 / 0.0008)
    assert float(np.linalg.norm(high)) == pytest.approx(expected_high, rel=1.0e-12)
    at_reference = wind.wind_velocity([0.0, 0.0, 1.0], env, 0.0)
    assert float(np.linalg.norm(at_reference)) == pytest.approx(10.0, rel=1.0e-12)
    # Van Karman is referenced by the module documentation of the log law.
    assert VON_KARMAN == pytest.approx(0.41, rel=1.0e-12)


# ==========================================================================
# Thermal plumes
# ==========================================================================
def test_thermal_updraft_is_positive_at_the_core_and_zero_far_away():
    env = EnvironmentConfig(thermal_strength=2.5, gust_intensity=0.0, wind_speed=0.0)
    strength, sigma, height = wind.thermal_geometry(env)
    assert strength == pytest.approx(2.5, rel=1.0e-15)
    assert sigma > 0.0 and height > 0.0
    x_c, y_c, z_c = wind._thermal_core(env, strength, 0.0)
    assert z_c > env.ground_level_m  # the plume lives above the ground plane
    core = wind.thermal_updraft([x_c, y_c, z_c], env, 0.0)
    assert core.shape == (3,)
    assert core[2] > 0.0
    assert core[2] == pytest.approx(2.5, rel=1.0e-12)
    far = wind.thermal_updraft([x_c + 80.0, y_c + 80.0, z_c], env, 0.0)
    assert abs(float(far[2])) < 1.0e-9
    assert np.all(np.abs(far) < 1.0e-9)
    # No entrainment on the axis.
    assert abs(float(core[0])) < 1.0e-15 and abs(float(core[1])) < 1.0e-15


def test_thermal_updraft_is_linear_in_the_strength():
    """Peak vertical speed is exactly ``thermal_strength`` (documented design)."""
    peaks = []
    for strength in (0.5, 1.0, 2.0, 4.0):
        env = EnvironmentConfig(thermal_strength=strength, wind_speed=0.0, gust_intensity=0.0)
        resolved, _, _ = wind.thermal_geometry(env)
        x_c, y_c, z_c = wind._thermal_core(env, resolved, 0.0)
        peaks.append(float(wind.thermal_updraft([x_c, y_c, z_c], env, 0.0)[2]))
    assert peaks == pytest.approx([0.5, 1.0, 2.0, 4.0], rel=1.0e-12)
    # Proportionality: doubling the strength doubles the peak.
    assert peaks[2] / peaks[0] == pytest.approx(4.0, rel=1.0e-12)


def test_thermal_strength_zero_gives_no_plume():
    env = EnvironmentConfig(thermal_strength=0.0, wind_speed=5.0, gust_intensity=0.5)
    assert np.array_equal(wind.thermal_updraft([1.0, 2.0, 3.0], env, 0.0), np.zeros(3))
    assert wind.thermal_geometry(env)[0] == 0.0


def test_thermal_core_wanders_with_time():
    env = EnvironmentConfig(thermal_strength=3.0, wind_speed=0.0, gust_intensity=0.0)
    strength, _, _ = wind.thermal_geometry(env)
    positions = []
    for time_s in (0.0, 3.0, 6.0, 12.0):
        x_c, y_c, _ = wind._thermal_core(env, strength, time_s)
        positions.append((x_c, y_c))
    assert len(set(positions)) == len(positions)
    assert wind._thermal_core(env, strength, 0.0) == wind._thermal_core(env, strength, 0.0)


def test_thermal_updraft_batch_and_single_paths_agree():
    env = EnvironmentConfig(thermal_strength=1.8, wind_speed=0.0, gust_intensity=0.0)
    points = np.array([[0.0, 0.0, 2.0], [3.0, -2.0, 4.0], [-5.0, 1.0, 1.0]])
    batched = wind.thermal_updraft(points, env, 1.5)
    assert batched.shape == (3, 3)
    for point, value in zip(points, batched):
        assert np.array_equal(value, wind.thermal_updraft(point, env, 1.5))


# ==========================================================================
# Ground and wind tunnel
# ==========================================================================
def test_no_vertical_flow_penetrates_the_ground():
    env = EnvironmentConfig(
        wind_speed=12.0,
        gust_intensity=0.9,
        turbulence_scale=1.5,
        thermal_strength=4.0,
        ground_level_m=0.0,
        boundary_layer_enabled=False,
    )
    heights = np.linspace(-25.0, 0.0, 60)
    positions = np.stack(
        [
            np.linspace(-30.0, 30.0, heights.size),
            np.linspace(20.0, -20.0, heights.size),
            heights,
        ],
        axis=-1,
    )
    velocities = wind.wind_velocity(positions, env, 4.2)
    assert np.all(velocities[:, 2] <= 0.0)
    assert np.all(velocities[:, 2] == 0.0)  # the ramp reaches exactly zero at the floor
    # A raised ground plane moves the impermeable surface.
    raised = EnvironmentConfig(**{**env.__dict__, "ground_level_m": 5.0})
    below = wind.wind_velocity(np.array([[0.0, 0.0, 5.0], [1.0, 1.0, 2.0]]), raised, 0.0)
    assert np.all(below[:, 2] == 0.0)
    above = wind.wind_velocity(np.array([[0.0, 0.0, 5.5]]), raised, 0.0)
    assert math.isfinite(float(above[0, 2]))


def test_wind_tunnel_is_a_uniform_stream():
    env = EnvironmentConfig(
        wind_tunnel_enabled=True,
        wind_tunnel_speed=14.25,
        gust_intensity=1.0,
        turbulence_scale=2.0,
        thermal_strength=8.0,
        boundary_layer_enabled=True,
        surface_roughness_m=0.05,
    )
    expected = np.array([14.25, 0.0, 0.0])
    for position in ([0.0, 0.0, 0.0], [-30.0, 12.0, -4.0], [5.0, -5.0, 40.0]):
        for time_s in (0.0, 1.25, 77.5):
            velocity = wind.wind_velocity(position, env, time_s)
            assert np.array_equal(velocity, expected)
    batch = wind.wind_velocity(np.array([[0.0, 0.0, 0.0], [9.0, 9.0, 9.0]]), env, 3.0)
    assert np.array_equal(batch, np.array([expected, expected]))
    field = wind.wind_field([0, 0, -5], [4, 4, 12], 3, env, 2.0)
    grid_points = 3 * 3 * 3
    assert np.allclose(
        np.asarray(field["vectors"]), np.array([[14.25] * grid_points, [0.0] * grid_points, [0.0] * grid_points])
    )
    assert field["max_speed"] == pytest.approx(14.25, rel=1.0e-15)
    assert field["mean_speed"] == pytest.approx(14.25, rel=1.0e-15)


def test_wind_tunnel_off_restores_the_ambient_flow():
    env = EnvironmentConfig(
        wind_tunnel_enabled=False,
        wind_speed=6.0,
        wind_direction_deg=270.0,
        gust_intensity=0.0,
        thermal_strength=0.0,
        boundary_layer_enabled=False,
    )
    velocity = wind.wind_velocity([0.0, 0.0, 2.0], env, 0.0)
    assert np.allclose(velocity, np.array([6.0, 0.0, 0.0]), rtol=0.0, atol=1.0e-12)


# ==========================================================================
# Backend honesty
# ==========================================================================
def test_active_backend_is_one_of_the_allowed_names():
    assert wind.active_backend() in wind.BACKEND_NAMES
    assert isinstance(wind.active_backend(), str) and wind.active_backend() != ""


def test_numpy_is_the_backend_on_this_machine():
    """No CuPy and no torch here, so the honest answer is ``numpy``."""
    import importlib.util

    has_cupy = importlib.util.find_spec("cupy") is not None
    has_torch = importlib.util.find_spec("torch") is not None
    if not has_cupy and not has_torch:
        assert wind.active_backend() == "numpy"
    elif not has_cupy:
        import torch

        if not bool(torch.cuda.is_available()):
            assert wind.active_backend() == "numpy"


def test_wind_field_reports_the_backend_that_actually_ran():
    env = EnvironmentConfig(wind_speed=5.0, gust_intensity=0.3, thermal_strength=1.0)
    auto = wind.wind_field([0, 0, 0], [4, 4, 6], 3, env, 0.0, backend="auto")
    explicit = wind.wind_field([0, 0, 0], [4, 4, 6], 3, env, 0.0, backend="numpy")
    for field in (auto, explicit):
        assert isinstance(field["backend"], str) and field["backend"] != ""
        assert field["backend"] in wind.BACKEND_NAMES
    if wind.active_backend() == "numpy":
        assert auto["backend"] == "numpy"
    assert auto["backend"] == wind.active_backend()
    assert explicit["backend"] == "numpy"


@pytest.mark.parametrize("requested", ["cupy", "torch"])
def test_unavailable_gpu_request_never_claims_a_gpu(requested):
    env = EnvironmentConfig(wind_speed=5.0, gust_intensity=0.3, thermal_strength=1.0)
    field = wind.wind_field([0, 0, 0], [3, 3, 4], 3, env, 0.0, backend=requested)
    assert isinstance(field["backend"], str) and field["backend"] != ""
    assert field["backend_requested"] == requested
    if wind.active_backend() != requested:
        assert field["backend"] == "numpy"
        reference = wind.wind_field([0, 0, 0], [3, 3, 4], 3, env, 0.0, backend="numpy")
        assert np.array_equal(np.asarray(field["vectors"]), np.asarray(reference["vectors"]))
        assert np.array_equal(np.asarray(field["positions"]), np.asarray(reference["positions"]))


def test_backend_requests_agree_with_numpy_to_machine_precision():
    """Every backend that runs must reproduce the NumPy reference exactly.

    The field maths is a single backend-agnostic routine that only swaps the
    array module, so the vectors are identical; this test pins that invariant.
    """
    env = EnvironmentConfig(
        wind_speed=9.0,
        wind_direction_deg=200.0,
        gust_intensity=0.45,
        thermal_strength=1.6,
        turbulence_scale=0.8,
        boundary_layer_enabled=True,
    )
    reference = wind.wind_field([0, 0, 0.0], [8, -6, 5.0], (3, 4, 2), env, 1.75, backend="numpy")
    auto = wind.wind_field([0, 0, 0.0], [8, -6, 5.0], (3, 4, 2), env, 1.75, backend="auto")
    assert np.allclose(
        np.asarray(auto["vectors"]), np.asarray(reference["vectors"]), rtol=0.0, atol=1.0e-6
    )
    assert np.allclose(
        np.asarray(auto["speeds"]), np.asarray(reference["speeds"]), rtol=0.0, atol=1.0e-6
    )
    if wind.active_backend() != "numpy":
        assert auto["backend"] == wind.active_backend()
    else:
        assert auto["backend"] == "numpy"
        assert np.array_equal(np.asarray(auto["vectors"]), np.asarray(reference["vectors"]))


def test_unknown_backend_is_rejected():
    with pytest.raises(ValueError):
        wind.wind_field([0, 0, 0], [1, 1, 1], 2, EnvironmentConfig(), 0.0, backend="opencl")


def test_backend_cache_can_be_invalidated_and_reprobes_safely():
    wind.invalidate_backend_cache()
    first = wind.active_backend()
    wind.invalidate_backend_cache()
    assert wind.active_backend() == first
    assert wind.active_backend() in wind.BACKEND_NAMES


# ==========================================================================
# wind_field structure
# ==========================================================================
def test_wind_field_shape_and_dimension_consistency():
    env = EnvironmentConfig(wind_speed=7.0, gust_intensity=0.25, thermal_strength=1.2)
    field = wind.wind_field([0, 0, 0.0], [9, -4, 6], (2, 3, 5), env, 0.5)
    nx, ny, nz = 2, 3, 5
    expected_points = nx * ny * nz
    assert field["shape"] == [nx, ny, nz]
    assert field["grid_point_count"] == expected_points
    assert len(field["positions"]) == 3
    assert len(field["vectors"]) == 3
    assert all(len(axis) == expected_points for axis in field["positions"])
    assert all(len(axis) == expected_points for axis in field["vectors"])
    assert len(field["speeds"]) == expected_points
    assert field["resolution"] == [2, 3, 5]
    assert field["max_speed"] == max(field["speeds"])
    assert field["mean_speed"] == pytest.approx(float(np.mean(field["speeds"])), rel=1.0e-15)
    assert field["time_s"] == pytest.approx(0.5, rel=1.0e-15)
    assert field["bounds"]["min"] == [0.0, 0.0, 0.0]
    assert field["bounds"]["max"] == [9.0, -4.0, 6.0]
    speeds = np.asarray(field["speeds"])
    vectors = np.asarray(field["vectors"])
    assert np.allclose(speeds, np.linalg.norm(vectors, axis=0), rtol=1.0e-12, atol=1.0e-12)
    assert np.all(speeds >= 0.0)


def test_wind_field_uses_ij_meshgrid_layout():
    env = EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0, thermal_strength=0.0)
    field = wind.wind_field([0, 0, 0], [2, -4, 1], (2, 3, 1), env, 0.0)
    xs = np.asarray(field["positions"][0])
    ys = np.asarray(field["positions"][1])
    zs = np.asarray(field["positions"][2])
    assert np.allclose(xs, [0.0, 0.0, 0.0, 2.0, 2.0, 2.0])
    assert np.allclose(ys, [0.0, -2.0, -4.0, 0.0, -2.0, -4.0])
    assert np.allclose(zs, np.zeros(6))
    assert np.allclose(np.asarray(field["vectors"]), np.zeros((3, 6)))


def test_wind_field_accepts_an_integer_resolution():
    env = EnvironmentConfig(wind_speed=4.0, gust_intensity=0.1)
    field = wind.wind_field([0, 0, 0], [5, 5, 5], 4, env, 0.0)
    assert field["shape"] == [4, 4, 4]
    assert field["resolution"] == 4
    assert field["grid_point_count"] == 64
    assert len(field["speeds"]) == 64


def test_wind_field_json_serialisable():
    import json

    env = EnvironmentConfig(wind_speed=5.0, gust_intensity=0.2, thermal_strength=1.0)
    payload = json.dumps(wind.wind_field([0, 0, 0], [2, 2, 3], 3, env, 0.0))
    assert "positions" in payload and "backend" in payload


@pytest.mark.parametrize(
    "resolution",
    [0, -1, (0, 2, 2), (2, 2), (2, 2, 2, 2), True],
)
def test_wind_field_rejects_invalid_resolutions(resolution):
    with pytest.raises(ValueError):
        wind.wind_field([0, 0, 0], [1, 1, 1], resolution, EnvironmentConfig(), 0.0)


def test_wind_field_rejects_bad_bounds():
    with pytest.raises(ValueError):
        wind.wind_field([0, 0], [1, 1, 1], 2, EnvironmentConfig(), 0.0)


def test_wind_field_agrees_with_pointwise_wind_velocity():
    env = EnvironmentConfig(
        wind_speed=6.0,
        wind_direction_deg=315.0,
        gust_intensity=0.35,
        thermal_strength=1.1,
        turbulence_scale=0.6,
    )
    field = wind.wind_field([0, 0, 0.0], [4, 4, 4], 3, env, 2.0)
    positions = np.asarray(field["positions"]).T
    vectors = np.asarray(field["vectors"]).T
    for index in (0, 7, 13, 26):
        point_velocity = wind.wind_velocity(positions[index], env, 2.0)
        assert np.array_equal(point_velocity, vectors[index])


def test_wind_velocity_batch_and_single_paths_agree():
    env = EnvironmentConfig(
        wind_speed=6.5, gust_intensity=0.4, thermal_strength=1.4, turbulence_scale=0.7
    )
    points = np.array([[0.0, 0.0, 0.2], [1.0, 2.0, 1.5], [-3.0, 1.0, 0.05]])
    batched = wind.wind_velocity(points, env, 1.1)
    assert batched.shape == (3, 3)
    for point, value in zip(points, batched):
        assert np.array_equal(value, wind.wind_velocity(point, env, 1.1))


def test_contract_helpers_are_reexported_and_consistent():
    assert atmo.moist_air_gas_constant(288.15, 101325.0, 0.0) == pytest.approx(R_AIR, rel=1.0e-15)
    assert wind.boundary_layer_ratio(np.array([1.0]), EnvironmentConfig(reference_height_m=1.0))[0] == (
        pytest.approx(1.0, rel=1.0e-12)
    )
    assert clamp(5.0, 0.0, 1.0) == 1.0
    assert G0 == pytest.approx(9.80665, rel=1.0e-15)


def test_velocity_components_is_the_numpy_reference_path():
    env = EnvironmentConfig(
        wind_speed=8.0,
        wind_direction_deg=30.0,
        gust_intensity=0.3,
        thermal_strength=1.3,
        turbulence_scale=0.6,
    )
    points = np.array([[0.0, 0.0, 1.0], [2.0, -1.0, 0.3], [-4.0, 3.0, 8.0]])
    reference = wind.velocity_components(points, env, 1.0, "numpy")
    assert reference.shape == (3, 3)
    assert np.array_equal(reference, wind.wind_velocity(points, env, 1.0))
    # An unavailable GPU request degrades instead of raising.
    for requested in ("cupy", "torch", "auto"):
        assert np.array_equal(
            wind.velocity_components(points, env, 1.0, requested), reference
        )
    with pytest.raises(ValueError):
        wind.velocity_components(points, env, 1.0, "metal")


def test_single_point_grid_and_empty_inputs():
    env = EnvironmentConfig(wind_speed=4.0, gust_intensity=0.2, thermal_strength=0.9)
    field = wind.wind_field([0, 0, 1], [0, 0, 1], 1, env, 0.0)
    assert field["shape"] == [1, 1, 1]
    assert field["grid_point_count"] == 1
    assert field["max_speed"] == field["mean_speed"] == field["speeds"][0]
    assert wind.simplex_noise3(np.zeros((0, 3)), seed=1).shape == (0,)
    assert wind.wind_velocity(np.zeros((0, 3)), env, 0.0).shape == (0, 3)
    # Degenerate box: the single axis sample sits on the lower bound.
    assert field["positions"] == [[0.0], [0.0], [1.0]]
