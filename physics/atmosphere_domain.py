"""Atmosphere domain -- ISA troposphere, moist air density and transport.

Everything in this module is a *pure* function of its arguments (no state, no
I/O) so that the same numbers are produced by the Flask API, by the parallel
optimiser workers and by the pytest suite.

Models implemented (prompt.md 2.1 / AGENTS.md 8.1):

* **ISA standard atmosphere** (troposphere, linear lapse rate)::

      T(h)   = T0 - L h
      p(h)   = p0 (T(h) / T0) ** (g / (L R_air))
      rho(h) = p(h) / (R_air T(h))

  ``g`` is **not** hard coded -- it is taken from :attr:`EnvironmentConfig.gravity`
  so that dragging the gravity slider in the UI immediately changes the ISA
  column and therefore the whole flight.

* **Local (moist) atmosphere override** -- the user supplied
  ``temperature_K`` / ``pressure_Pa`` / ``humidity_rel`` triple.  The moist air
  density follows from the mole fraction blend of dry air and water vapour::

      p_v   = phi * p_sat(T)                     (partial pressure of vapour)
      p_d   = p - p_v                             (partial pressure of dry air)
      R_moist = (p_d R_air + p_v R_vapour) / p    (specific gas constant)
      rho   = p / (R_moist T)

  Because ``R_vapour (461.5) > R_air (287.05)``, humid air is measurably less
  dense than dry air at identical ``T`` and ``p``.

* **Saturation vapour pressure** -- the Magnus / Tetens form in the
  Alduchov & Eskridge (1996) coefficients ``610.94 exp(17.625 t / (t + 243.04))``
  with ``t = T - 273.15`` (supercooled *liquid* water branch).

* **Sutherland viscosity**::

      mu(T) = MU0 (T/T_ref)^1.5 (T_ref + S) / (T + S)

  with the reference pair ``MU0 = 1.716e-5`` Pa s at ``T_ref = 273.15 K`` (0 degC)
  and ``S = 110.4 K`` -- the ICAO constants, and *not* the ISA sea-level
  temperature.

* **Derived transport / compressibility quantities** ``nu = mu / rho``,
  ``a = sqrt(gamma R T)``, ``Re = V c / nu`` and ``M = V / a``.

Every physical constant is imported from :mod:`physics.config_schema`; nothing
is redefined locally, so the whole simulator stays on one contract.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

from .config_schema import (
    GAMMA_AIR,
    G0,
    LAPSE_RATE_ISA,
    MU0_ISA,
    MU_SUTHERLAND,
    P0_ISA,
    R_AIR,
    R_VAPOR,
    SUTHERLAND_T0,
    T0_ISA,
    EnvironmentConfig,
    clamp,
)

__all__ = [
    "isa_temperature",
    "isa_pressure",
    "isa_density",
    "sutherland_viscosity",
    "saturation_vapour_pressure_Pa",
    "moist_air_gas_constant",
    "vapour_partial_pressure_Pa",
    "air_density",
    "kinematic_viscosity",
    "speed_of_sound",
    "reynolds_number",
    "scale_altitude_m",
    "reference_speed_mps",
    "evaluate_atmosphere",
    "ATMOSPHERE_BACKEND",
]

#: This domain is scalar NumPy / pure Python maths; the value is echoed in every
#: :func:`evaluate_atmosphere` payload so the UI never has to guess.
ATMOSPHERE_BACKEND: str = "numpy"

_ZERO_CELSIUS_K: float = 273.15
_MIN_TEMPERATURE_K: float = 100.0
_MIN_VAPOUR_FRACTION: float = 1.0e-6


# --------------------------------------------------------------------------
# ISA standard atmosphere
# --------------------------------------------------------------------------
#: Geometric altitude of the tropopause (the base of the isothermal layer).
TROPOPAUSE_ALTITUDE_M = 11000.0
#: Geometric altitude of the base of the stratospheric lapse layer.
STRATOSPHERE_BASE_ALTITUDE_M = 20000.0
#: Geometric altitude of the base of the upper isothermal layer.
STRATOSPHERE_TOP_ALTITUDE_M = 32000.0
#: Temperature lapse rate inside the 20-32 km stratospheric layer (ISA).
STRATOSPHERE_LAPSE_RATE_K_PER_M = 0.001


def isa_temperature(
    altitude_m: float,
    sea_level_temperature_K: float = T0_ISA,
    lapse_rate_K_per_m: float = LAPSE_RATE_ISA,
) -> float:
    """ISA temperature profile in kelvin.

    The profile is the standard layered ISA:

    * troposphere (0 - 11 km): ``T = T0 - L h``,
    * tropopause (11 - 20 km): isothermal at ``T11 = T0 - L * 11 km``,
    * stratosphere (20 - 32 km): ``T = T11 + 0.001 (h - 20 km)``,
    * upper isothermal layer (above 32 km): ``T = T32``.

    Below 11 km this is exactly the linear tropospheric profile; above it the
    previous implementation extrapolated the linear lapse until the
    temperature went negative (and :func:`isa_pressure` meanwhile floored the
    temperature at 50 K, so pressure and density stopped being consistent).

    Parameters
    ----------
    altitude_m:
        Geometric altitude **above sea level**, positive upwards.
    sea_level_temperature_K:
        Sea level temperature ``T0`` (ISA uses 288.15 K).
    lapse_rate_K_per_m:
        Temperature lapse rate ``L`` (ISA uses 6.5e-3 K/m).
    """
    h = float(altitude_m)
    t0 = float(sea_level_temperature_K)
    lapse = float(lapse_rate_K_per_m)
    t11 = t0 - lapse * TROPOPAUSE_ALTITUDE_M
    if h <= TROPOPAUSE_ALTITUDE_M:
        return t0 - lapse * h
    if h <= STRATOSPHERE_BASE_ALTITUDE_M:
        return t11
    if h <= STRATOSPHERE_TOP_ALTITUDE_M:
        return t11 + STRATOSPHERE_LAPSE_RATE_K_PER_M * (h - STRATOSPHERE_BASE_ALTITUDE_M)
    return t11 + STRATOSPHERE_LAPSE_RATE_K_PER_M * (
        STRATOSPHERE_TOP_ALTITUDE_M - STRATOSPHERE_BASE_ALTITUDE_M
    )


def isa_pressure(
    altitude_m: float,
    sea_level_pressure_Pa: float = P0_ISA,
    sea_level_temperature_K: float = T0_ISA,
    lapse_rate_K_per_m: float = LAPSE_RATE_ISA,
    gravity: float = G0,
) -> float:
    """ISA pressure with the standard layered profile.

    * troposphere (0 - 11 km): ``p = p0 (T/T0) ** (g / (L R_air))``,
    * tropopause (11 - 20 km): ``p = p11 exp(-g (h - 11 km) / (R T11))``,
    * stratosphere (20 - 32 km): ``p = p20 (T/T20) ** (-g / (L R_air))``,
    * upper isothermal layer (above 32 km): ``p = p32 exp(-g (h - 32 km) / (R T32))``.

    Below 11 km this is exactly the tropospheric closed form; above it the
    previous implementation extrapolated that power law to the top of the
    atmosphere while flooring the temperature at 50 K, so the reported
    pressure and density stopped being thermodynamically consistent (and at
    100 km the column reported sea-level pressure).

    ``gravity`` defaults to :data:`config_schema.G0` but the caller
    (:func:`evaluate_atmosphere`) always forwards
    :attr:`EnvironmentConfig.gravity` so the column stays consistent with the
    runtime-mutable gravity.  ``lapse_rate_K_per_m`` must be non-zero; a
    vanishing lapse rate degenerates to the isothermal limit which is handled
    explicitly with ``p = p0 exp(-g h / (R T0))``.
    """
    h = float(altitude_m)
    t0 = float(sea_level_temperature_K)
    p0 = float(sea_level_pressure_Pa)
    lapse = float(lapse_rate_K_per_m)
    g = abs(float(gravity))
    if lapse == 0.0 or t0 == 0.0:
        # Isothermal limit: dp/dh = -rho g with rho = p / (R T0).
        exponent = -g * h / (R_AIR * max(abs(t0), _MIN_TEMPERATURE_K))
        return p0 * math.exp(clamp(exponent, -700.0, 700.0))

    t11 = t0 - lapse * TROPOPAUSE_ALTITUDE_M
    if h <= TROPOPAUSE_ALTITUDE_M:
        # Troposphere: the closed-form hydrostatic solution.
        ratio = max((t0 - lapse * h) / max(t0, 1.0), 1.0e-6)
        exponent = g / (lapse * R_AIR)
        return p0 * (ratio ** exponent)

    # Tropopause boundary pressure (continuous with the troposphere).
    p11 = p0 * ((t11 / max(t0, 1.0)) ** (g / (lapse * R_AIR)))
    if h <= STRATOSPHERE_BASE_ALTITUDE_M:
        exponent = -g * (h - TROPOPAUSE_ALTITUDE_M) / (R_AIR * max(abs(t11), _MIN_TEMPERATURE_K))
        return p11 * math.exp(clamp(exponent, -700.0, 700.0))

    # Base of the stratospheric lapse layer.
    p20 = p11 * math.exp(clamp(-g * (STRATOSPHERE_BASE_ALTITUDE_M - TROPOPAUSE_ALTITUDE_M)
                               / (R_AIR * max(abs(t11), _MIN_TEMPERATURE_K)), -700.0, 700.0))
    t20 = t11 + STRATOSPHERE_LAPSE_RATE_K_PER_M * (STRATOSPHERE_BASE_ALTITUDE_M - TROPOPAUSE_ALTITUDE_M)
    if h <= STRATOSPHERE_TOP_ALTITUDE_M:
        temperature = t11 + STRATOSPHERE_LAPSE_RATE_K_PER_M * (h - STRATOSPHERE_BASE_ALTITUDE_M)
        ratio = max(temperature / max(t20, 1.0e-6), 1.0e-6)
        exponent = -g / (lapse * R_AIR)
        return p20 * (ratio ** exponent)

    # Upper isothermal layer.
    t32 = t11 + STRATOSPHERE_LAPSE_RATE_K_PER_M * (
        STRATOSPHERE_TOP_ALTITUDE_M - STRATOSPHERE_BASE_ALTITUDE_M
    )
    p32 = p20 * ((t32 / max(t20, 1.0e-6)) ** (-g / (lapse * R_AIR)))
    exponent = -g * (h - STRATOSPHERE_TOP_ALTITUDE_M) / (R_AIR * max(abs(t32), _MIN_TEMPERATURE_K))
    return p32 * math.exp(clamp(exponent, -700.0, 700.0))


def isa_density(
    altitude_m: float,
    sea_level_temperature_K: float = T0_ISA,
    sea_level_pressure_Pa: float = P0_ISA,
    lapse_rate_K_per_m: float = LAPSE_RATE_ISA,
    gravity: float = G0,
) -> float:
    """ISA tropospheric density ``rho = p / (R_air T)`` (dry standard air)."""
    temperature = isa_temperature(altitude_m, sea_level_temperature_K, lapse_rate_K_per_m)
    pressure = isa_pressure(
        altitude_m,
        sea_level_pressure_Pa,
        sea_level_temperature_K,
        lapse_rate_K_per_m,
        gravity=gravity,
    )
    return pressure / (R_AIR * temperature)


def scale_altitude_m(lapse_rate_K_per_m: float = LAPSE_RATE_ISA) -> float:
    """Tropospheric temperature scale altitude ``H = T0 / L`` (metres)."""
    lapse = float(lapse_rate_K_per_m)
    if lapse == 0.0:
        return float("inf")
    return abs(T0_ISA / lapse)


# --------------------------------------------------------------------------
# Moist air
# --------------------------------------------------------------------------
def saturation_vapour_pressure_Pa(temperature_K: float) -> float:
    """Saturation vapour pressure of water over *supercooled liquid*.

    Magnus / Tetens form with the Alduchov & Eskridge (1996) coefficients::

        p_sat(T) = 610.94 * exp(17.625 t / (t + 243.04))   [Pa]
        t = T - 273.15                                       [degC]

    The formulation is accurate to better than 0.2 % relative over
    ``-40 degC .. + 50 degC``; ``273.15 K -> 610.94 Pa`` exactly.
    """
    t = float(temperature_K) - _ZERO_CELSIUS_K
    return 610.94 * math.exp(clamp(17.625 * t / (t + 243.04), -60.0, 60.0))


def vapour_partial_pressure_Pa(temperature_K: float, humidity_rel: float, total_pressure_Pa: float) -> float:
    """Water vapour partial pressure ``p_v = phi p_sat(T)``, capped below ``p``."""
    phi = clamp(float(humidity_rel), 0.0, 1.0)
    total = max(float(total_pressure_Pa), 1.0)
    cap = (1.0 - _MIN_VAPOUR_FRACTION) * total
    return clamp(phi * saturation_vapour_pressure_Pa(temperature_K), 0.0, cap)


def moist_air_gas_constant(temperature_K: float, pressure_Pa: float, humidity_rel: float) -> float:
    """Specific gas constant of moist air from Dalton's law.

    The total density of moist air is the sum of partial densities:
        rho = rho_d + rho_v = p_d / (R_air * T) + p_v / (R_v * T) = p / (R_moist * T)
    hence the specific gas constant is the harmonic mean:
        1 / R_moist = (p_d / p) / R_air + (p_v / p) / R_v
        R_moist = p / (p_d / R_air + p_v / R_v)
    """
    total = float(pressure_Pa)
    if total <= 0.0:
        return R_AIR
    p_v = vapour_partial_pressure_Pa(temperature_K, humidity_rel, total)
    p_d = total - p_v
    denom = (p_d / R_AIR) + (p_v / R_VAPOR)
    if denom <= 0.0:
        return R_AIR
    return total / denom


def air_density(temperature_K: float, pressure_Pa: float, humidity_rel: float = 0.0) -> float:
    """Density of (optionally humid) air from ``T``, ``p`` and relative humidity.

    Dryer air is denser: raising ``humidity_rel`` raises ``R_moist`` (because
    ``R_vapour > R_air``) and therefore lowers the density at fixed ``T``/``p``.
    """
    total = float(pressure_Pa)
    if total <= 0.0:
        raise ValueError(f"air_density requires a positive pressure, got {total} Pa")
    temperature = max(float(temperature_K), _MIN_TEMPERATURE_K)
    r_moist = moist_air_gas_constant(temperature, total, humidity_rel)
    return total / (r_moist * temperature)


# --------------------------------------------------------------------------
# Transport properties
# --------------------------------------------------------------------------
def sutherland_viscosity(temperature_K: float) -> float:
    """Dynamic viscosity of air from Sutherland's law (Pa s).

    ``mu(T) = MU0 (T/T_ref)^1.5 (T_ref + S) / (T + S)`` with the reference pair
    ``MU0 = 1.716e-5`` Pa s at ``T_ref = 273.15 K`` (0 degC) and ``S = 110.4 K``
    -- the ICAO / Sutherland (1895) constants.  ``T_ref`` is *not* the ISA sea
    level temperature :data:`config_schema.T0_ISA` (15 degC); pairing it with
    that would bias the viscosity ~4 % low across the whole atmosphere.
    """
    temperature = max(float(temperature_K), 1.0)
    ratio = temperature / SUTHERLAND_T0
    return MU0_ISA * (ratio ** 1.5) * ((SUTHERLAND_T0 + MU_SUTHERLAND) / (temperature + MU_SUTHERLAND))


def kinematic_viscosity(temperature_K: float, density_kgm3: float) -> float:
    """Kinematic viscosity ``nu = mu / rho`` (m^2/s); 0 for a non-positive density."""
    rho = float(density_kgm3)
    if rho <= 0.0 or not math.isfinite(rho):
        return 0.0
    return sutherland_viscosity(temperature_K) / rho


def speed_of_sound(temperature_K: float) -> float:
    """Speed of sound ``a = sqrt(gamma R_air T)`` (m/s), incompressible form."""
    temperature = max(float(temperature_K), _MIN_TEMPERATURE_K)
    return math.sqrt(GAMMA_AIR * R_AIR * temperature)


def reynolds_number(speed_mps: float, chord_m: float, kinematic_viscosity_m2s: float) -> float:
    """Reynolds number ``Re = V c / nu``; 0 for a degenerate chord or viscosity."""
    nu = float(kinematic_viscosity_m2s)
    chord = float(chord_m)
    if nu <= 0.0 or chord <= 0.0 or not math.isfinite(nu):
        return 0.0
    return abs(float(speed_mps)) * chord / nu


def mach_number(speed_mps: float, temperature_K: float) -> float:
    """Mach number ``M = V / a``."""
    a = speed_of_sound(temperature_K)
    if a <= 0.0:
        return 0.0
    return abs(float(speed_mps)) / a


# --------------------------------------------------------------------------
# Aggregate evaluation used by the REST layer
# --------------------------------------------------------------------------
def reference_speed_mps(env: EnvironmentConfig) -> float:
    """Flow speed used for the ``Re`` / ``M`` reference values.

    Wind-tunnel mode (AGENTS.md 5 measurement mode) wins over the ambient wind
    so that the reported flow condition matches the stream the solver uses.
    """
    if bool(env.wind_tunnel_enabled):
        return abs(float(env.wind_tunnel_speed))
    return abs(float(env.wind_speed))


def evaluate_atmosphere(
    env: EnvironmentConfig,
    altitude_m: Optional[float] = None,
    reference_chord_m: float = 0.1,
) -> Dict[str, Any]:
    """Full atmospheric state for the REST layer.

    ``isa_mode=True``  -> ``T`` and ``p`` follow the ISA profile from
    ``altitude_m`` (which defaults to :attr:`EnvironmentConfig.altitude_m`),
    using the runtime ``gravity`` in the barometric exponent.
    ``isa_mode=False`` -> the user supplied ``temperature_K`` / ``pressure_Pa``
    are used verbatim.

    In both cases the moist-air density correction of :func:`air_density` is
    applied through ``humidity_rel``.

    The returned mapping always contains the thermodynamic state, the transport
    properties, the reference ``Re`` / ``M``, the ISA metadata, the backend tag
    and an echo of every environment scaling knob so that the UI can render the
    exact configuration that produced the numbers.
    """
    height = float(env.altitude_m if altitude_m is None else altitude_m)
    chord = max(float(reference_chord_m), 1.0e-9)
    humidity = clamp(float(env.humidity_rel), 0.0, 1.0)
    gravity = abs(float(env.gravity))

    if bool(env.isa_mode):
        temperature = isa_temperature(
            height,
            env.sea_level_temperature_K,
            env.lapse_rate_K_per_m,
        )
        pressure = isa_pressure(
            height,
            env.sea_level_pressure_Pa,
            env.sea_level_temperature_K,
            env.lapse_rate_K_per_m,
            gravity=gravity,
        )
        source = "isa"
    else:
        temperature = max(float(env.temperature_K), _MIN_TEMPERATURE_K)
        pressure = max(float(env.pressure_Pa), 1.0)
        source = "local"

    density = air_density(temperature, pressure, humidity)
    dynamic_viscosity = sutherland_viscosity(temperature)
    kinematic = kinematic_viscosity(temperature, density)
    sound_speed = speed_of_sound(temperature)
    speed = reference_speed_mps(env)

    payload: Dict[str, Any] = {
        # thermodynamic state
        "temperature_K": float(temperature),
        "temperature_C": float(temperature - _ZERO_CELSIUS_K),
        "pressure_Pa": float(pressure),
        "pressure_hPa": float(pressure) / 100.0,
        "density_kgm3": float(density),
        "humidity_rel": float(humidity),
        "humidity_source": source,
        "vapour_pressure_Pa": float(vapour_partial_pressure_Pa(temperature, humidity, pressure)),
        "saturation_vapour_pressure_Pa": float(saturation_vapour_pressure_Pa(temperature)),
        "specific_gas_constant_J_kgK": float(moist_air_gas_constant(temperature, pressure, humidity)),
        "isa_density_kgm3": float(
            isa_density(
                height,
                env.sea_level_temperature_K,
                env.sea_level_pressure_Pa,
                env.lapse_rate_K_per_m,
                gravity=gravity,
            )
        )
        if bool(env.isa_mode)
        else float(density),
        # transport / compressibility
        "dynamic_viscosity_Pa_s": float(dynamic_viscosity),
        "kinematic_viscosity_m2s": float(kinematic),
        "sound_speed_mps": float(sound_speed),
        "reynolds": float(reynolds_number(speed, chord, kinematic)),
        "mach_reference": float(mach_number(speed, temperature)),
        "reference_speed_mps": float(speed),
        "reference_chord_m": float(chord),
        "specific_heat_ratio": float(GAMMA_AIR),
        # ISA metadata
        "altitude_m": float(height),
        "scale_altitude_m": float(scale_altitude_m(env.lapse_rate_K_per_m)),
        "lapse_rate_K_per_m": float(env.lapse_rate_K_per_m),
        "isa_mode": bool(env.isa_mode),
        "gravity": float(gravity),
        "backend": ATMOSPHERE_BACKEND,
        # echoed scaling knobs (requirement A -- fully runtime mutable)
        "drag_scale": float(env.drag_scale),
        "lift_scale": float(env.lift_scale),
        "ground_friction": float(env.ground_friction),
        "ground_effect_enabled": bool(env.ground_effect_enabled),
        "compressibility_enabled": bool(env.compressibility_enabled),
        "wind_tunnel_enabled": bool(env.wind_tunnel_enabled),
        "wind_tunnel_speed": float(env.wind_tunnel_speed),
        "wind_speed": float(env.wind_speed),
        "wind_direction_deg": float(env.wind_direction_deg),
        "gust_intensity": float(env.gust_intensity),
        "thermal_strength": float(env.thermal_strength),
    }
    return payload
