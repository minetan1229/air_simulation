"""Ground contact, Continuous Collision Detection (CCD), and boundary wall resolution.

Handles ground stations geometry, impulse-based collision resolution with
Coulomb friction, and world boundary wall clamping.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np

from core.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    SimulationSettings,
    StateVector,
    clamp,
)
from core.geometry_utils import body_to_world, quaternion_normalize

VectorLike = Any

HARD_ALTITUDE_LIMIT_M = 1.1e4
HARD_HORIZONTAL_LIMIT_M = 1.0e5
GROUND_KEEL_DEPTH_M = 0.015
CRASH_IMPACT_SPEED_MPS = 60.0


def _finite(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def compute_ground_stations(
    position_m: VectorLike,
    quaternion: VectorLike,
    environment: EnvironmentConfig,
    derived: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """Compute ground altitudes for CG and key airframe stations.

    Altitudes are positive above ground, <= 0 in contact/penetration.
    """
    env_ground = getattr(environment, "ground_level_m", 0.0)
    ground = _finite(env_ground)
    z_cg = float(position_m[2])
    altitude_cg = -z_cg - ground

    mac = max((derived.get("mac_m", 0.08) if derived else 0.08), 1.0e-3)
    span = max((derived.get("wingspan_m", 0.20) if derived else 0.20), 1.0e-3)
    semispan = span * 0.5
    keel_depth = max(
        (derived.get("keel_height_m", GROUND_KEEL_DEPTH_M) if derived else GROUND_KEEL_DEPTH_M),
        1.0e-3,
    )

    # Key body stations relative to CG (x fwd, y right, z down)
    r_nose = np.array([0.5 * mac, 0.0, 0.0], dtype=np.float64)
    r_ltip = np.array([-0.3 * mac, -semispan, 0.0], dtype=np.float64)
    r_rtip = np.array([-0.3 * mac, semispan, 0.0], dtype=np.float64)
    r_keel = np.array([0.0, 0.0, keel_depth], dtype=np.float64)
    r_tail = np.array([-0.7 * mac, 0.0, keel_depth], dtype=np.float64)

    q = quaternion
    z_nose = z_cg + float(body_to_world(q, r_nose)[2])
    z_ltip = z_cg + float(body_to_world(q, r_ltip)[2])
    z_rtip = z_cg + float(body_to_world(q, r_rtip)[2])
    z_keel = z_cg + float(body_to_world(q, r_keel)[2])
    z_tail = z_cg + float(body_to_world(q, r_tail)[2])

    alt_nose = -z_nose - ground
    alt_ltip = -z_ltip - ground
    alt_rtip = -z_rtip - ground
    alt_keel = -z_keel - ground
    alt_tail = -z_tail - ground

    min_alt = min(altitude_cg, alt_nose, alt_ltip, alt_rtip, alt_keel, alt_tail)
    return {
        "altitude_cg": altitude_cg,
        "alt_nose": alt_nose,
        "alt_ltip": alt_ltip,
        "alt_rtip": alt_rtip,
        "alt_keel": alt_keel,
        "alt_tail": alt_tail,
        "min_alt": min_alt,
    }


def world_wall_contact(
    start: StateVector, end: StateVector
) -> Optional[StateVector]:
    """Resolve contact with the horizontal world boundary (100 km)."""
    x0 = float(start.position_m[0])
    y0 = float(start.position_m[1])
    x1 = float(end.position_m[0])
    y1 = float(end.position_m[1])
    radius0 = math.hypot(x0, y0)
    if radius0 >= HARD_HORIZONTAL_LIMIT_M:
        tau = 0.0
    else:
        dx = x1 - x0
        dy = y1 - y0
        a = dx * dx + dy * dy
        if a <= 0.0:
            return None
        b = 2.0 * (x0 * dx + y0 * dy)
        c = radius0 * radius0 - HARD_HORIZONTAL_LIMIT_M * HARD_HORIZONTAL_LIMIT_M
        discriminant = b * b - 4.0 * a * c
        if discriminant < 0.0:
            return None
        tau = (-b + math.sqrt(discriminant)) / (2.0 * a)
        if tau < 0.0 or tau > 1.0:
            return None

    px = x0 + tau * (x1 - x0)
    py = y0 + tau * (y1 - y0)
    radius = math.hypot(px, py)
    if radius > 0.0:
        scale = HARD_HORIZONTAL_LIMIT_M / radius
        px *= scale
        py *= scale
    pz = float(start.position_m[2]) + tau * (float(end.position_m[2]) - float(start.position_m[2]))
    vx = float(start.velocity_mps[0]) + tau * (float(end.velocity_mps[0]) - float(start.velocity_mps[0]))
    vy = float(start.velocity_mps[1]) + tau * (float(end.velocity_mps[1]) - float(start.velocity_mps[1]))
    vz = float(start.velocity_mps[2]) + tau * (float(end.velocity_mps[2]) - float(start.velocity_mps[2]))
    t_hit = float(start.time_s) + tau * (float(end.time_s) - float(start.time_s))
    return StateVector(
        time_s=t_hit,
        position_m=(px, py, pz),
        velocity_mps=(vx, vy, vz),
        quaternion=start.quaternion,
        angular_velocity_radps=start.angular_velocity_radps,
        acceleration_mps2=(0.0, 0.0, 0.0),
        angular_accel_radps2=(0.0, 0.0, 0.0),
        landed=False,
        crashed=True,
    )
