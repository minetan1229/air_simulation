"""Throw solver, release profiles, and initial state generation.

Reconstructs the hand impulse that produces a launch state.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    StateVector,
    THROW_STYLE_CATALOGUE,
    clamp,
    deg2rad,
    derived_properties,
    rad2deg,
    safe_div,
    throw_style,
)
from core.geometry_utils import body_to_world, quaternion_from_euler
from physics.ground_contact import compute_ground_stations


MAX_RELEASE_PITCH_RATE_RADPS = 8.0
MAX_RELEASE_ROLL_RATE_RADPS = 45.0
MAX_RELEASE_YAW_RATE_RADPS = 14.0
MAX_RELEASE_SPEED_MPS = 20.0


def _finite(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _config_from(cls: Any, data: Any) -> Any:
    if isinstance(data, cls):
        return data
    if isinstance(data, dict):
        fields = getattr(cls, "__dataclass_fields__", {})
        kwargs = {k: v for k, v in data.items() if k in fields}
        return cls(**kwargs)
    return cls()


def _velocity_from_elevation_azimuth(
    speed: float, elevation_deg: float, azimuth_deg: float
) -> np.ndarray:
    """Convert speed, elevation above horizon, and azimuth into an NED velocity."""
    el = deg2rad(elevation_deg)
    az = deg2rad(azimuth_deg)
    cos_el = math.cos(el)
    # NED: x = north (forward), y = east (right), z = down (negative elevation climbs)
    return np.array(
        [
            speed * cos_el * math.cos(az),
            speed * cos_el * math.sin(az),
            -speed * math.sin(el),
        ],
        dtype=np.float64,
    )

@dataclass
class ThrowSolution:
    """Resolved hand impulse and the release state it produces.

    Attributes are plain JSON-friendly values so the REST layer can hand the
    object straight to :func:`physics.config_schema.as_dict`.
    """

    style: str = "direct"
    style_label: str = ""
    impulse_active: bool = False
    speed_source: str = "direct"
    force_n: float = 0.0
    duration_s: float = 0.0
    impulse_ns: float = 0.0
    mass_kg: float = 0.0
    peak_acceleration_mps2: float = 0.0
    force_direction_body: Tuple[float, float, float] = (1.0, 0.0, 0.0)
    grip_point_m: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    moment_nm: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    torque_rate_radps: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    angular_rate_radps: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    speed_mps: float = 0.0
    velocity_mps: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    flight_path_deg: Tuple[float, float] = (0.0, 0.0)
    release_alpha_deg: float = 0.0
    release_euler_deg: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    quaternion: Tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)
    release_height_m: float = 0.0
    position_m: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    kinetic_energy_j: float = 0.0
    impulse_to_weight: float = 0.0
    runup_m: float = 0.0
    warnings: List[str] = field(default_factory=list)


def _resolve_throw_style_key(launch: LaunchConfig) -> Tuple[str, Any]:
    """Return ``(style key, ThrowStyle)`` tolerating an unknown string.

    An unknown key never raises: it degrades to the neutral ``direct`` profile
    and adds a warning, so a stale client cannot break the simulation.
    """
    key = str(getattr(launch, "throw_style", "direct") or "direct")
    try:
        return key, throw_style(key)
    except KeyError:
        return "direct", throw_style("direct")


def throw_profile_catalogue() -> List[Dict[str, Any]]:
    """JSON payload describing every selectable throwing technique."""
    out: List[Dict[str, Any]] = []
    for style in THROW_STYLE_CATALOGUE:
        out.append(
            {
                "key": style.key,
                "label": style.label,
                "label_ja": style.label_ja,
                "description": style.description,
                "notes": style.notes,
                "speed_source": "direct" if style.key == "direct" else "impulse",
                "overrides": style.launch_overrides(),
            }
        )
    return out


def resolve_throw(
    launch: Any,
    aircraft: Any = None,
    environment: Any = None,
) -> ThrowSolution:
    """Resolve a launch condition into a hand impulse and a release state.

    Model
    -----
    The hand delivers a force ``F`` for a time ``dt`` along a direction that
    is tilted away from the nose axis by ``(force_pitch, force_yaw)``:

    1. **Impulse**  ``J = F * dt``  (Ns).
    2. **Release speed**  ``|v| = |J| / m`` when ``throw_speed_source`` is
       ``"impulse"``, otherwise the explicitly requested ``launch_speed``.
    3. **Flight path vs. attitude**  the airframe leaves the hand along
       ``(launch_angle_deg + throw_force_pitch_deg, launch_yaw_deg +
       throw_force_yaw_deg)`` in impulse mode -- the force direction is
       ``throw_force_*`` above the nose axis -- and along ``(launch_angle_deg,
       launch_yaw_deg)`` in direct mode.  The **attitude** is always
       ``(launch_roll + release_roll, launch_angle + release_pitch,
       launch_yaw + release_yaw)``, so ``release_pitch_deg`` is the angle of
       attack left on the wing at the instant of release (reported as
       :attr:`ThrowSolution.release_alpha_deg`).  With every offset at zero and
       no force tilt this is exactly the historical "velocity along the nose
       axis raised by ``launch_angle_deg``" rule.
    4. **Release rotation**  the contact force acts at the grip point
       ``r = (grip_x * MAC, grip_y * semispan, 0)`` relative to the centre of
       gravity, so the impulsive moment is ``M = r x F`` and the rate it would
       produce is ``w = I^-1 M dt``.  A finite contact time plus the compliance
       of the hand and forearm absorb most of that rotation, so the surviving
       fraction is ``throw_torque_efficiency`` (default 0.15); the wrist /
       forearm rates are then added and the total clamped to the release
       ceilings above.

    Parameters
    ----------
    launch, aircraft, environment:
        Contract dataclasses or plain dictionaries; ``aircraft`` is optional
        and only needed when the impulsive rotation has to be resolved.
    """
    cfg = _config_from(LaunchConfig, launch)
    air = _config_from(AircraftConfig, aircraft if aircraft is not None else AircraftConfig())
    env = (
        _config_from(EnvironmentConfig, environment)
        if environment is not None
        else EnvironmentConfig()
    )

    key, style = _resolve_throw_style_key(cfg)
    derived = derived_properties(air)
    mass = max(_finite(derived["mass_kg"]), 1.0e-9)
    mac = max(_finite(derived["mac_m"]), 1.0e-4)
    semispan = max(_finite(derived["wingspan_m"]), 1.0e-6) * 0.5
    inertia = np.diag(
        [
            max(_finite(derived["roll_inertia_kgm2"]), 1.0e-14),
            max(_finite(derived["pitch_inertia_kgm2"]), 1.0e-14),
            max(_finite(derived["yaw_inertia_kgm2"]), 1.0e-14),
        ]
    )

    warnings: List[str] = []
    if key != str(getattr(cfg, "throw_style", "direct")):
        warnings.append(
            f"unknown throw style '{cfg.throw_style}'; falling back to 'direct'"
        )

    force = abs(_finite(cfg.throw_force_n))
    duration = abs(_finite(cfg.throw_duration_s))
    pitch_tilt = deg2rad(_finite(cfg.throw_force_pitch_deg))
    yaw_tilt = deg2rad(_finite(cfg.throw_force_yaw_deg))
    active = bool(cfg.impulse_active)

    # --- force direction and magnitude (body frame, +z is down) ----------
    direction_body = np.array(
        [
            math.cos(pitch_tilt) * math.cos(yaw_tilt),
            math.cos(pitch_tilt) * math.sin(yaw_tilt),
            -math.sin(pitch_tilt),
        ],
        dtype=np.float64,
    )
    force_body = force * direction_body
    impulse_ns = force * duration
    if force <= 0.0 and active:
        warnings.append("impulse mode is on but 'throw_force_n' is 0; release speed is 0")
    if duration <= 0.0 and active:
        warnings.append("impulse mode is on but 'throw_duration_s' is 0; release speed is 0")

    # --- release attitude vs. flight path ---------------------------------
    # ``(flight_pitch_deg, flight_yaw_deg)`` is the **flight path** the airframe
    # is thrown along.  The ``release_*`` fields then rotate the **attitude**
    # away from it, so ``release_pitch_deg`` is exactly the angle of attack the
    # hand leaves on the wing (see the :class:`LaunchConfig` contract).  Adding
    # the offsets to the velocity as well would pin alpha to zero at release and
    # leave any airframe trimmed at a positive alpha with the full residual
    # ``cm(0) = -cm_alpha * alpha_trim``; because the pitch mode is fast
    # (omega_sp ~ 200 rad/s on a 4 g paper airframe) that residual pitches the
    # nose over the top and loops the glider inside 0.2 s.
    flight_pitch_deg = _finite(cfg.launch_angle_deg)
    flight_yaw_deg = _finite(cfg.launch_yaw_deg)
    release_pitch_deg = _finite(cfg.release_pitch_deg)
    release_yaw_deg = _finite(cfg.release_yaw_deg)
    roll_total = _finite(cfg.launch_roll_deg) + _finite(cfg.release_roll_deg)
    pitch_total = flight_pitch_deg + release_pitch_deg
    yaw_total = flight_yaw_deg + release_yaw_deg
    quaternion = quaternion_from_euler(roll_total, pitch_total, yaw_total)

    # --- release speed and direction -------------------------------------
    if active:
        speed = impulse_ns / mass
        if not math.isfinite(speed) or speed > MAX_RELEASE_SPEED_MPS:
            speed = min(speed if math.isfinite(speed) else MAX_RELEASE_SPEED_MPS, MAX_RELEASE_SPEED_MPS)
            warnings.append(
                f"release speed clamped to {MAX_RELEASE_SPEED_MPS:g} m/s (hand impulse too large)"
            )
        # The impulsive hand pushes along the force direction in body frame:
        # Transform direction_body to world frame via the airframe attitude quaternion
        velocity = speed * body_to_world(quaternion, direction_body)
        v_h = math.hypot(velocity[0], velocity[1])
        velocity_pitch_deg = rad2deg(math.atan2(-velocity[2], max(v_h, 1e-9)))
        velocity_yaw_deg = rad2deg(math.atan2(velocity[1], velocity[0]))
    else:
        speed = abs(_finite(cfg.launch_speed))
        velocity_pitch_deg = flight_pitch_deg
        velocity_yaw_deg = flight_yaw_deg
        velocity = _velocity_from_elevation_azimuth(speed, velocity_pitch_deg, velocity_yaw_deg)

    # --- release rotation ------------------------------------------------
    grip_z_val = getattr(cfg, "throw_grip_z", getattr(cfg, "grip_z", None))
    grip_z = 0.015 if grip_z_val is None else float(grip_z_val)
    grip = np.array(
        [
            _finite(cfg.throw_grip_x) * mac + _finite(air.cg_offset_x),
            _finite(cfg.throw_grip_y) * semispan,
            grip_z,
        ],
        dtype=np.float64,
    )
    moment = np.cross(grip, force_body)
    efficiency = clamp(_finite(cfg.throw_torque_efficiency, 0.15), 0.0, 1.0)
    inertia[0, 1] = inertia[1, 0] = derived.get("inertia_xy_kgm2", 0.0)
    inertia[0, 2] = inertia[2, 0] = derived.get("inertia_xz_kgm2", 0.0)
    inertia[1, 2] = inertia[2, 1] = derived.get("inertia_yz_kgm2", 0.0)
    if active:
        try:
            torque_rate = np.linalg.solve(inertia, moment * duration * efficiency)
        except np.linalg.LinAlgError:
            torque_rate = np.zeros(3, dtype=np.float64)
        rates = np.array(
            [
                clamp(
                    _finite(torque_rate[0] + _finite(cfg.throw_wrist_spin_radps)),
                    -MAX_RELEASE_ROLL_RATE_RADPS,
                    MAX_RELEASE_ROLL_RATE_RADPS,
                ),
                clamp(
                    _finite(torque_rate[1] + _finite(cfg.throw_pitch_rate_radps)),
                    -MAX_RELEASE_PITCH_RATE_RADPS,
                    MAX_RELEASE_PITCH_RATE_RADPS,
                ),
                clamp(
                    _finite(torque_rate[2] + _finite(cfg.throw_yaw_rate_radps)),
                    -MAX_RELEASE_YAW_RATE_RADPS,
                    MAX_RELEASE_YAW_RATE_RADPS,
                ),
            ],
            dtype=np.float64,
        )
    else:
        # The neutral entry point keeps the historical behaviour exactly:
        # no force, no grip, no wrist rotation.
        grip = np.zeros(3, dtype=np.float64)
        moment = np.zeros(3, dtype=np.float64)
        torque_rate = np.zeros(3, dtype=np.float64)
        if any(abs(_finite(v)) > 0.0 for v in (cfg.throw_wrist_spin_radps, cfg.throw_pitch_rate_radps, cfg.throw_yaw_rate_radps)):
            rates = np.array(
                [
                    clamp(_finite(cfg.throw_wrist_spin_radps), -MAX_RELEASE_ROLL_RATE_RADPS, MAX_RELEASE_ROLL_RATE_RADPS),
                    clamp(_finite(cfg.throw_pitch_rate_radps), -MAX_RELEASE_PITCH_RATE_RADPS, MAX_RELEASE_PITCH_RATE_RADPS),
                    clamp(_finite(cfg.throw_yaw_rate_radps), -MAX_RELEASE_YAW_RATE_RADPS, MAX_RELEASE_YAW_RATE_RADPS),
                ],
                dtype=np.float64,
            )
            warnings.append(
                "explicit release rates are applied even though 'throw_speed_source' is 'direct'"
            )
        else:
            rates = np.zeros(3, dtype=np.float64)

    height = _finite(cfg.launch_height_m)
    ground_level = _finite(getattr(env, "ground_level_m", 0.0))
    raw_pos = getattr(cfg, "launch_position_m", (0.0, 0.0, 0.0))
    if raw_pos is None or not isinstance(raw_pos, (list, tuple, np.ndarray)):
        raw_pos = (0.0, 0.0, 0.0)
    pos_x = _finite(raw_pos[0]) if len(raw_pos) > 0 else 0.0
    pos_y = _finite(raw_pos[1]) if len(raw_pos) > 1 else 0.0
    position = np.array(
        [
            pos_x,
            pos_y,
            -(ground_level + height),
        ],
        dtype=np.float64,
    )
    gravity = abs(_finite(env.gravity, 9.80665))
    kinetic_energy = 0.5 * mass * speed * speed

    return ThrowSolution(
        style=key,
        style_label=style.label,
        impulse_active=active,
        speed_source=str(cfg.throw_speed_source),
        force_n=float(force),
        duration_s=float(duration),
        impulse_ns=float(impulse_ns),
        mass_kg=float(mass),
        peak_acceleration_mps2=float(safe_div(force, mass, 0.0)),
        force_direction_body=tuple(float(v) for v in direction_body),  # type: ignore[arg-type]
        grip_point_m=tuple(float(v) for v in grip),  # type: ignore[arg-type]
        moment_nm=tuple(float(v) for v in moment),  # type: ignore[arg-type]
        torque_rate_radps=tuple(float(v) for v in torque_rate),  # type: ignore[arg-type]
        angular_rate_radps=tuple(float(v) for v in rates),  # type: ignore[arg-type]
        speed_mps=float(speed),
        velocity_mps=tuple(float(v) for v in velocity),  # type: ignore[arg-type]
        flight_path_deg=(float(velocity_pitch_deg), float(velocity_yaw_deg)),
        # Nose-up attitude relative to flight path gives positive angle of attack:
        # alpha = pitch_attitude - flight_path_pitch.
        release_alpha_deg=float(pitch_total - velocity_pitch_deg),
        release_euler_deg=(float(roll_total), float(pitch_total), float(yaw_total)),
        quaternion=tuple(float(v) for v in quaternion),  # type: ignore[arg-type]
        release_height_m=float(height),
        position_m=tuple(float(v) for v in position),  # type: ignore[arg-type]
        kinetic_energy_j=float(kinetic_energy),
        impulse_to_weight=float(safe_div(impulse_ns, mass * gravity, 0.0)),
        runup_m=float(clamp(_finite(cfg.throw_runup_m), 0.0, 3.04)),
        warnings=warnings,
    )



def initial_state(
    launch: LaunchConfig,
    environment: EnvironmentConfig,
    aircraft: Optional[AircraftConfig] = None,
) -> StateVector:
    """Build the launch state from a launch condition.

    The release condition is produced by :func:`resolve_throw`, which
    reconstructs the hand impulse ("力の入れ方") that creates it.  With the
    neutral ``direct`` style the velocity points along the body x axis raised
    by ``launch_angle_deg`` above the horizon (a positive angle climbs) and
    turned by ``launch_yaw_deg``, ``launch_roll_deg`` tilts the attitude about
    the velocity vector, and the angular rate is zero -- exactly the original
    convention.  The position is ``(x, y, -launch_height_m)`` because +z points
    **down**, so the initial altitude above the environment ground level is
    exactly ``launch_height_m``.
    """
    throw = resolve_throw(launch, aircraft, environment)
    env_cfg = _config_from(EnvironmentConfig, environment)
    derived = derived_properties(aircraft) if aircraft is not None else None
    stations = compute_ground_stations(throw.position_m, throw.quaternion, env_cfg, derived)
    alt_cg = stations["altitude_cg"]
    min_alt = stations["min_alt"]

    # Underground / extreme ground penetration guardrail:
    # If launch_height_m < 0.0, or CG is underground (alt_cg < 0.0), or any key
    # station penetrates past the numerical tolerance (min_alt < -0.05),
    # immediately latch crashed=True and zero out initial velocities.
    launch_height = _finite(getattr(launch, "launch_height_m", 0.0))
    is_underground = launch_height < 0.0 or alt_cg < 0.0 or min_alt < -0.05
    crashed = bool(is_underground)

    vel = (0.0, 0.0, 0.0) if crashed else tuple(float(v) for v in throw.velocity_mps)
    omega = (0.0, 0.0, 0.0) if crashed else tuple(float(v) for v in throw.angular_rate_radps)
    accel = (0.0, 0.0, 0.0) if crashed else (0.0, 0.0, _finite(env_cfg.gravity))

    return StateVector(
        time_s=0.0,
        position_m=tuple(float(v) for v in throw.position_m),  # type: ignore[arg-type]
        velocity_mps=vel,  # type: ignore[arg-type]
        quaternion=tuple(float(v) for v in throw.quaternion),  # type: ignore[arg-type]
        angular_velocity_radps=omega,  # type: ignore[arg-type]
        acceleration_mps2=accel,
        angular_accel_radps2=(0.0, 0.0, 0.0),
        landed=False,
        crashed=crashed,
    )

