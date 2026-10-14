"""Gravity and anti-gravity modifier domain for 3D paper airplane physics.

This module provides robust, side-effect-free gravity scaling and anti-gravity
computations. It isolates all gravitational modifications from Python's standard
library (such as the 'antigravity' module easter egg) to prevent module shadowing,
circular imports, or unexpected browser launches in headless/CI environments.
"""

from __future__ import annotations

import logging
import math
import sys
from dataclasses import dataclass
from typing import Optional, Tuple

from physics.config_schema import STANDARD_GRAVITY, clamp, EnvironmentConfig

logger = logging.getLogger(__name__)

# Mean Earth radius in metres for altitude-based gravity decay
EARTH_RADIUS_M: float = 6371000.0


@dataclass(frozen=True)
class GravityConfig:
    """Configuration for gravitational field and anti-gravity modifications.

    Attributes:
        base_gravity: Standard gravitational acceleration at sea level (m/s^2).
        anti_gravity_factor: Scaling factor for counter-gravity (0.0 = normal gravity,
            1.0 = zero gravity / complete levitation, >1.0 = inverted gravity).
        altitude_decay: Whether to compute Newtonian inverse-square decay with altitude.
    """

    base_gravity: float = STANDARD_GRAVITY
    anti_gravity_factor: float = 0.0
    altitude_decay: bool = False

    def __post_init__(self) -> None:
        if not math.isfinite(self.base_gravity) or self.base_gravity < 0.0:
            raise ValueError(f"base_gravity must be non-negative and finite, got {self.base_gravity}")
        if not math.isfinite(self.anti_gravity_factor):
            raise ValueError(f"anti_gravity_factor must be finite, got {self.anti_gravity_factor}")


class GravityModifier:
    """Encapsulates gravitational field evaluation, anti-gravity factor, and environment safety."""

    def __init__(self, config: Optional[GravityConfig] = None) -> None:
        self.config: GravityConfig = config or GravityConfig()
        self._validate_environment()

    def _validate_environment(self) -> None:
        """Inspect sys.modules to diagnose potential module shadowing or unwanted imports."""
        if "antigravity" in sys.modules:
            mod = sys.modules["antigravity"]
            # Standard library antigravity imports webbrowser and has geohash/easter egg
            if hasattr(mod, "geohash") or hasattr(mod, "webbrowser"):
                logger.debug(
                    "Standard Python 'antigravity' module is loaded in sys.modules; "
                    "physics.gravity_modifier is safely isolated."
                )

    def get_effective_gravity(self, altitude_m: float = 0.0) -> float:
        """Calculate the scalar effective gravity acceleration magnitude (m/s^2).

        Args:
            altitude_m: Altitude above sea level in metres.

        Returns:
            Effective gravitational acceleration in m/s^2.
            May be 0.0 (anti_gravity_factor=1.0) or negative (anti_gravity_factor > 1.0).
        """
        alt = max(0.0, float(altitude_m))
        base = self.config.base_gravity

        if self.config.altitude_decay and alt > 0.0:
            # Newtonian 1/r^2 scaling
            scale = (EARTH_RADIUS_M / (EARTH_RADIUS_M + alt)) ** 2
            base *= scale

        effective_g = base * (1.0 - self.config.anti_gravity_factor)
        return effective_g

    def get_gravity_vector_ned(self, altitude_m: float = 0.0) -> Tuple[float, float, float]:
        """Compute the gravity acceleration vector in the NED (North-East-Down) frame.

        In NED coordinates:
            x = North/Forward
            y = East/Right
            z = Down (+z is pointing towards Earth center)

        Returns:
            Tuple (gx, gy, gz) in m/s^2.
        """
        gz = self.get_effective_gravity(altitude_m=altitude_m)
        return (0.0, 0.0, gz)

    def compute_apparent_weight(self, mass_kg: float, altitude_m: float = 0.0) -> float:
        """Compute the apparent weight force (N) acting on an aircraft of given mass.

        Args:
            mass_kg: Mass in kilograms.
            altitude_m: Altitude above sea level in metres.

        Returns:
            Downward weight force in Newtons (can be negative if inverted gravity).
        """
        if mass_kg < 0.0 or not math.isfinite(mass_kg):
            raise ValueError(f"mass_kg must be non-negative and finite, got {mass_kg}")
        return mass_kg * self.get_effective_gravity(altitude_m)

    def apply_to_environment(self, env: EnvironmentConfig, altitude_m: float = 0.0) -> EnvironmentConfig:
        """Return an EnvironmentConfig with the effective gravity field updated."""
        g_eff = self.get_effective_gravity(altitude_m)
        # Note: env.gravity represents scalar gravity magnitude in the flight simulator
        clamped_g = clamp(g_eff, -50.0, 50.0)
        return EnvironmentConfig(
            temperature_K=env.temperature_K,
            pressure_Pa=env.pressure_Pa,
            humidity_rel=env.humidity_rel,
            isa_mode=env.isa_mode,
            altitude_m=env.altitude_m,
            sea_level_temperature_K=env.sea_level_temperature_K,
            sea_level_pressure_Pa=env.sea_level_pressure_Pa,
            lapse_rate_K_per_m=env.lapse_rate_K_per_m,
            wind_speed=env.wind_speed,
            wind_direction_deg=env.wind_direction_deg,
            gust_intensity=env.gust_intensity,
            thermal_strength=env.thermal_strength,
            turbulence_scale=env.turbulence_scale,
            turbulence_seed=env.turbulence_seed,
            boundary_layer_enabled=env.boundary_layer_enabled,
            surface_roughness_m=env.surface_roughness_m,
            reference_height_m=env.reference_height_m,
            ground_level_m=env.ground_level_m,
            gravity=clamped_g,
            ground_friction=env.ground_friction,
            drag_scale=env.drag_scale,
            lift_scale=env.lift_scale,
            ground_effect_enabled=env.ground_effect_enabled,
            compressibility_enabled=env.compressibility_enabled,
            wind_tunnel_enabled=env.wind_tunnel_enabled,
            wind_tunnel_speed=env.wind_tunnel_speed,
            wind_tunnel_alpha_deg=env.wind_tunnel_alpha_deg,
            wind_tunnel_beta_deg=env.wind_tunnel_beta_deg,
        )
