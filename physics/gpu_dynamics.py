"""Vectorised 6-DoF rigid-body flight dynamics (NumPy / CuPy).

This module is the GPU counterpart of :mod:`physics.flight_sim`.  It
integrates the **same 13-state rigid body** -- position, velocity,
quaternion and body rates -- with the same RK4 scheme, the same step-control
limits, the same aerodynamic coefficients and the same structural model, but
for a whole batch of airframes at once.

Why it exists
-------------
The GPU optimisation batch used to integrate a *3-DoF point mass* with an
explicit Euler step and ten artificial clamps, while the reference path
integrated the 13-state rigid body with RK4.  The two paths therefore
evaluated different airplanes: the audit measured a 1261x disagreement in the
reported wing-root stress and even a different flight status for the same
launch.  Sharing the aerodynamic kernel
(:mod:`physics.aero_kernel`), the wind convention
(:func:`physics.wind_domain.wind_flow_components`) and the structural model
(:func:`physics.stress_domain.peak_root_stress_pa`) removes the *model*
difference; this module removes the *integrator* difference.

Everything is written against an explicit array module ``xp`` so the same
code runs on NumPy (CPU) and CuPy (GPU).  All per-case quantities are arrays
of length ``N`` that broadcast against each other.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np

from . import aero_kernel
from .config_schema import (
    GAMMA_AIR,
    MU0_ISA,
    MU_SUTHERLAND,
    R_AIR,
    R_VAPOR,
    SUTHERLAND_T0,
    AircraftConfig,
    EnvironmentConfig,
    clamp,
    derived_properties,
)
from .stress_domain import (
    KAPPA_SIGMA,
    Z_SCALE_GAIN,
    Z_SCALE_OFFSET,
    effective_paper_stiffness,
    paper_allowable_stress,
    peak_root_stress_pa,
    root_chord_batch,
    te_fold_chord_scale,
)
from .wind_domain import velocity_components, wind_flow_components

__all__ = [
    "FlightParams",
    "STATE_SIZE",
    "atmosphere_batch",
    "build_params",
    "quaternion_to_matrix_batch",
    "body_to_world_batch",
    "world_to_body_batch",
    "quaternion_derivative_batch",
    "quaternion_normalize_batch",
    "launch_state_vector",
    "simulate_batch",
]

#: Number of state variables of the rigid body: position, velocity,
#: quaternion and body rates.
STATE_SIZE: int = 13

# --- step control (mirrors physics.flight_sim) ---------------------------
MAX_ATTITUDE_STEP_RAD: float = 0.35
TRAVEL_FRACTION_PER_STEP: float = 0.5
MAX_RATE_CHANGE_RADPS: float = 2.0
RK4_STIFFNESS_SAFETY: float = 2.0
DT_MIN: float = 1.0e-5

# --- keel damping (mirrors physics.flight_sim) ---------------------------
KEEL_ROLL_DAMPING: float = -0.20
KEEL_YAW_DAMPING: float = -0.26
KEEL_DAMPING_CUTOFF_MPS: float = 0.8

#: Impact speed above which the ground contact is a structural failure.
CRASH_IMPACT_SPEED_MPS: float = 60.0

#: Depth of the keel below the centre of gravity (metres).
GROUND_KEEL_DEPTH_M: float = 0.015

#: Minimum airspeed for a sample to enter the L/D statistics.
SPEED_STAT_FLOOR_MPS: float = 1.0

_EPS: float = 1.0e-12
_ZERO_CELSIUS_K: float = 273.15


# --------------------------------------------------------------------------
# Array-module agnostic helpers
# --------------------------------------------------------------------------
def _norm(xp: Any, vector: Any) -> Any:
    """Euclidean norm along the last axis."""
    return xp.sqrt(xp.sum(vector * vector, axis=-1))


def _cross(xp: Any, a: Any, b: Any) -> Any:
    """Cross product of two ``(N, 3)`` arrays."""
    return xp.stack(
        (
            a[:, 1] * b[:, 2] - a[:, 2] * b[:, 1],
            a[:, 2] * b[:, 0] - a[:, 0] * b[:, 2],
            a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0],
        ),
        axis=-1,
    )


def _rad(xp: Any, value: Any) -> Any:
    return xp.asarray(value, dtype=xp.float64) * (math.pi / 180.0)


def _deg(xp: Any, value: Any) -> Any:
    return xp.asarray(value, dtype=xp.float64) * (180.0 / math.pi)


# --------------------------------------------------------------------------
# Quaternion algebra, vectorised
# --------------------------------------------------------------------------
def quaternion_normalize_batch(xp: Any, quat: Any) -> Any:
    """Normalise ``(N, 4)`` quaternions to unit length."""
    norm = _norm(xp, quat)
    safe = xp.where(norm > _EPS, norm, xp.ones_like(norm))
    out = quat / safe[:, xp.newaxis]
    return xp.where((norm > _EPS)[:, xp.newaxis], out, xp.tile(xp.array([1.0, 0.0, 0.0, 0.0]), (quat.shape[0], 1)))


def quaternion_to_matrix_batch(xp: Any, quat: Any) -> Any:
    """Body-to-world direction cosine matrices of ``(N, 4)`` quaternions."""
    w, x, y, z = quat[:, 0], quat[:, 1], quat[:, 2], quat[:, 3]
    xx, yy, zz = x * x, y * y, z * z
    wx, wy, wz = w * x, w * y, w * z
    xy, xz, yz = x * y, x * z, y * z
    r00 = 1.0 - 2.0 * (yy + zz)
    r01 = 2.0 * (xy - wz)
    r02 = 2.0 * (xz + wy)
    r10 = 2.0 * (xy + wz)
    r11 = 1.0 - 2.0 * (xx + zz)
    r12 = 2.0 * (yz - wx)
    r20 = 2.0 * (xz - wy)
    r21 = 2.0 * (yz + wx)
    r22 = 1.0 - 2.0 * (xx + yy)
    return xp.stack(
        (
            xp.stack((r00, r01, r02), axis=-1),
            xp.stack((r10, r11, r12), axis=-1),
            xp.stack((r20, r21, r22), axis=-1),
        ),
        axis=1,
    )


def body_to_world_batch(xp: Any, rotation: Any, vector_body: Any) -> Any:
    """Rotate ``(N, 3)`` body vectors into the world frame."""
    return xp.einsum("nij,nj->ni", rotation, vector_body)


def world_to_body_batch(xp: Any, rotation: Any, vector_world: Any) -> Any:
    """Rotate ``(N, 3)`` world vectors into the body frame."""
    return xp.einsum("nji,nj->ni", rotation, vector_world)


def quaternion_derivative_batch(xp: Any, quat: Any, omega_body: Any) -> Any:
    """``q_dot = 0.5 * q (x) [0, omega_body]`` for body-frame angular rate."""
    w, x, y, z = quat[:, 0], quat[:, 1], quat[:, 2], quat[:, 3]
    p, q_pitch, r_yaw = omega_body[:, 0], omega_body[:, 1], omega_body[:, 2]
    return 0.5 * xp.stack(
        (
            -(x * p + y * q_pitch + z * r_yaw),
            w * p + y * r_yaw - z * q_pitch,
            w * q_pitch - x * r_yaw + z * p,
            w * r_yaw + x * q_pitch - y * p,
        ),
        axis=-1,
    )


# --------------------------------------------------------------------------
# Atmosphere, vectorised
# --------------------------------------------------------------------------
def atmosphere_batch(
    xp: Any, env: EnvironmentConfig, altitude_m: Any, chord_m: Any
) -> Dict[str, Any]:
    """Vectorised ISA / moist-air atmosphere of a batch of altitudes.

    Reproduces the closed forms of :mod:`physics.atmosphere_domain`:
    the ISA temperature and pressure profile, the moist-air density
    correction and Sutherland's viscosity.  The batch path needs the density
    at every altitude it visits, and it must be the *same* atmosphere as the
    6-DoF path -- a constant-density approximation would bias the dynamic
    pressure of every case by a few percent over a 200 m glide.
    """
    gravity = abs(float(env.gravity))
    altitude = xp.asarray(altitude_m, dtype=xp.float64)
    temperature = xp.full_like(altitude, float(env.temperature_K))
    pressure = xp.full_like(altitude, float(env.pressure_Pa))

    if bool(env.isa_mode):
        t0 = max(float(env.sea_level_temperature_K), 1.0)
        p0 = max(float(env.sea_level_pressure_Pa), 1.0)
        lapse = float(env.lapse_rate_K_per_m)
        height = xp.maximum(altitude, 0.0)
        isa_t = t0 - lapse * height
        isa_t = xp.maximum(isa_t, 50.0)
        ratio = isa_t / t0
        exponent = gravity / (lapse * R_AIR) if lapse > 0.0 else 0.0
        troposphere = p0 * xp.power(
            xp.maximum(ratio, 1.0e-6), exponent
        )
        isothermal = p0 * xp.exp(-gravity * height / (R_AIR * t0))
        # ``xp.where`` rejects a Python bool condition on CuPy ("'bool'
        # object has no attribute 'astype'"); the lapse rate is a scalar, so
        # the branch is selected in Python.
        isa_p = troposphere if lapse > 0.0 else isothermal
        temperature = xp.where(isa_t > 0.0, isa_t, t0)
        pressure = xp.maximum(isa_p, 1.0)

    temperature = xp.maximum(temperature, 100.0)
    pressure = xp.maximum(pressure, 1.0)
    humidity = clamp(float(env.humidity_rel), 0.0, 1.0)

    celsius = temperature - _ZERO_CELSIUS_K
    exponent_psat = xp.clip(17.625 * celsius / (celsius + 243.04), -60.0, 60.0)
    p_sat = 610.94 * xp.exp(exponent_psat)
    p_vapour = xp.clip(humidity * p_sat, 0.0, 0.999999 * pressure)
    r_moist = pressure / (((pressure - p_vapour) / R_AIR) + (p_vapour / R_VAPOR))
    density = pressure / (r_moist * temperature)

    ratio_t = temperature / SUTHERLAND_T0
    viscosity = (
        MU0_ISA
        * xp.power(ratio_t, 1.5)
        * ((SUTHERLAND_T0 + MU_SUTHERLAND) / (temperature + MU_SUTHERLAND))
    )
    kinematic = viscosity / xp.maximum(density, 1.0e-12)
    sound = xp.sqrt(GAMMA_AIR * R_AIR * temperature)
    return {
        "density_kgm3": density,
        "kinematic_viscosity_m2s": kinematic,
        "sound_speed_mps": sound,
    }


# --------------------------------------------------------------------------
# Per-case parameter bundle
# --------------------------------------------------------------------------
@dataclass
class FlightParams:
    """Per-case parameters of the batched rigid-body simulation.

    Every field is an array of length ``N`` (one entry per airframe), so the
    whole batch is evaluated with a single set of vectorised expressions.
    """

    env: EnvironmentConfig
    mass: Any
    inertia: Any                     # (N, 3) principal moments
    area: Any
    chord: Any
    span: Any
    dihedral_rad: Any
    gravity: float
    ground_level_m: float
    wind: Any                        # (N, 3) world-frame wind vector
    aero: aero_kernel.AeroParams
    thickness_m: Any
    z_gain: Any
    root_chord_m: Any
    taper_ratio: Any
    chord_scale: Any
    tip_factor: Any
    allowable_pa: Any
    wind_backend: str = "numpy"
    mean_wind: Any = None
    use_mean_wind_only: bool = False


def wind_batch(
    xp: Any,
    params: FlightParams,
    positions: Any,
    time_s: float,
) -> Any:
    """Wind velocity of the whole batch in the **z-down world frame**.

    The field is the one of :mod:`physics.wind_domain` -- the log-law
    boundary layer, the thermal plume and the three decorrelated turbulence
    channels -- evaluated for every case at once.  The 6-DoF path samples the
    same field, so the two paths see the same air mass.

    The upstream solver works on a *z-up* height axis; the query points are
    converted the same way :func:`physics.flight_sim.wind_vector` converts
    them, and the returned vertical component is negated back into the z-down
    world frame.
    """
    if params.use_mean_wind_only:
        return xp.broadcast_to(params.mean_wind, (positions.shape[0], 3))
    # The wind field is evaluated on the host, so the query points have to be
    # brought back explicitly.  ``np.asarray`` on a CuPy array raises
    # "Implicit conversion to a NumPy array is not allowed".
    host_positions = positions.get() if hasattr(positions, "get") else np.asarray(positions)
    points = np.stack(
        (
            np.asarray(host_positions[:, 0], dtype=np.float64),
            np.asarray(host_positions[:, 1], dtype=np.float64),
            params.ground_level_m - np.asarray(host_positions[:, 2], dtype=np.float64),
        ),
        axis=-1,
    )
    values = velocity_components(points, params.env, float(time_s), params.wind_backend)
    values = np.asarray(values, dtype=np.float64)
    return xp.asarray(
        np.stack((values[:, 0], values[:, 1], -values[:, 2]), axis=-1),
        dtype=xp.float64,
    )


def build_params(
    xp: Any,
    aircraft: Any,
    env: EnvironmentConfig,
    wind_override: Optional[Any] = None,
) -> FlightParams:
    """Build the per-case parameter bundle.

    ``aircraft`` is a sequence of :class:`~physics.config_schema.AircraftConfig`
    objects (one per case) or a single config repeated ``N`` times.
    ``wind_override`` is an optional ``(N, 3)`` world-frame wind vector; when
    omitted the shared meteorological convention of
    :func:`physics.wind_domain.wind_flow_components` is used.
    """
    configs = list(aircraft) if isinstance(aircraft, (list, tuple)) else [aircraft]
    count = len(configs)
    zeros = xp.zeros(count)
    ones = xp.ones(count)
    derived = [derived_properties(cfg) for cfg in configs]

    def column(name: str, default: float = 0.0) -> Any:
        return xp.asarray([float(getattr(cfg, name, default)) for cfg in configs])

    mass = xp.asarray([max(float(item["mass_kg"]), 1.0e-9) for item in derived])
    inertia = xp.stack(
        (
            xp.asarray([max(float(item["roll_inertia_kgm2"]), 1.0e-12) for item in derived]),
            xp.asarray([max(float(item["pitch_inertia_kgm2"]), 1.0e-12) for item in derived]),
            xp.asarray([max(float(item["yaw_inertia_kgm2"]), 1.0e-12) for item in derived]),
        ),
        axis=-1,
    )
    areas = xp.asarray([max(float(item["ref_area_m2"]), 1.0e-9) for item in derived])
    chords = xp.asarray([max(float(item["ref_length_m"]), 1.0e-9) for item in derived])
    spans = xp.asarray([max(float(item["ref_span_m"]), 1.0e-6) for item in derived])

    aero = aero_kernel.AeroParams(
        area=areas,
        chord=chords,
        span=spans,
        aspect=xp.asarray([float(item["aspect_ratio"]) for item in derived]),
        oswald=column("oswald_e", 0.75),
        cL0=column("cL0"),
        cl_alpha=xp.asarray(
            [aero_kernel.params_from_aircraft(cfg).cl_alpha for cfg in configs]
        ),
        cl_max=column("cl_max", 0.90),
        stall_alpha_deg=xp.asarray(
            [aero_kernel.effective_stall_alpha_deg(cfg) for cfg in configs]
        ),
        stall_sharpness=column("stall_sharpness", 1.4),
        polhamus_kp=column("polhamus_kp"),
        polhamus_kv=column("polhamus_kv"),
        cd0=column("cd0", 0.02),
        re_cd0_exponent=column("re_cd0_exponent"),
        cm0=column("cm0"),
        cm_alpha=column("cm_alpha"),
        cm_q=column("cm_q"),
        cm_roll_rate=column("cm_roll_rate"),
        cm_pitch_rate=column("cm_pitch_rate"),
        cm_yaw_rate=column("cm_yaw_rate"),
        cl_beta=column("cl_beta"),
        cl_roll_rate=column("cl_roll_rate"),
        cl_pitch_rate=column("cl_pitch_rate"),
        cl_yaw_rate=column("cl_yaw_rate"),
        cn_beta=column("cn_beta"),
        cn_roll_rate=column("cn_roll_rate"),
        cn_pitch_rate=column("cn_pitch_rate"),
        cn_yaw_rate=column("cn_yaw_rate"),
        cy_beta=column("cy_beta"),
        aileron_deg=column("aileron_deg"),
        asymmetry_cl=column("asymmetry_cl"),
        rudder_deg=column("rudder_deg"),
        elevon_deg=column("elevon_deg"),
        elevon_authority=column("elevon_chord_ratio", 0.22),
        cg_offset_x=column("cg_offset_x"),
        cp_shift_gain=column("cp_shift_gain"),
        ground_cushion_lift=column("ground_cushion_lift"),
        ground_pitch_coupling=column("ground_pitch_coupling"),
        lift_scale=ones * max(float(env.lift_scale), 0.0),
        drag_scale=ones * max(float(env.drag_scale), 0.0),
        compressible=bool(env.compressibility_enabled),
        ground_effect_enabled=bool(env.ground_effect_enabled),
    )

    if wind_override is not None:
        wind = xp.asarray(wind_override, dtype=xp.float64).reshape(count, 3)
        use_mean_wind = True
    else:
        flow_x, flow_y = wind_flow_components(float(env.wind_direction_deg))
        speed = float(env.wind_speed)
        wind = xp.stack(
            (
                ones * (speed * flow_x),
                ones * (speed * flow_y),
                zeros,
            ),
            axis=-1,
        )
        use_mean_wind = False

    thickness = xp.asarray([max(float(cfg.paper_gsm) * 1.0e-3 / 800.0, 1.0e-6) for cfg in configs])
    stiffness = xp.asarray([max(effective_paper_stiffness(cfg), 0.0) for cfg in configs])
    z_gain = xp.maximum(Z_SCALE_OFFSET + Z_SCALE_GAIN * stiffness, 1.0e-12)
    taper = xp.clip(xp.asarray([float(item["taper_ratio"]) for item in derived]), 0.05, 1.0)
    root_chord = root_chord_batch(xp, areas, spans, taper)
    chord_scale = te_fold_chord_scale(xp, column("fold_trailing_deg"))
    tip_factor = xp.abs(xp.sin(_rad(xp, column("fold_tip_deg"))))
    allowable = xp.asarray(
        [max(paper_allowable_stress(cfg.paper_gsm, effective_paper_stiffness(cfg)), 1.0e-9) for cfg in configs]
    )

    return FlightParams(
        env=env,
        mass=mass,
        inertia=inertia,
        area=areas,
        chord=chords,
        span=spans,
        dihedral_rad=xp.sin(_rad(xp, column("dihedral_deg"))),
        gravity=abs(float(env.gravity)),
        ground_level_m=float(env.ground_level_m),
        wind=wind,
        aero=aero,
        thickness_m=thickness,
        z_gain=z_gain,
        root_chord_m=root_chord,
        taper_ratio=taper,
        chord_scale=chord_scale,
        tip_factor=tip_factor,
        allowable_pa=allowable,
        wind_backend="numpy",
        mean_wind=wind,
        use_mean_wind_only=use_mean_wind,
    )


# --------------------------------------------------------------------------
# Dynamics
# --------------------------------------------------------------------------
def _wind_force_to_body_batch(
    xp: Any, relative_body: Any, drag: Any, side: Any, lift: Any
) -> Any:
    """Resolve the wind-axis forces along the actual flow, in the body frame."""
    speed = _norm(xp, relative_body)
    safe = xp.where(speed > 1.0e-12, speed, xp.ones_like(speed))
    drag_axis = relative_body / safe[:, xp.newaxis]
    horizontal = xp.sqrt(relative_body[:, 0] ** 2 + relative_body[:, 2] ** 2)
    has_horizontal = horizontal > 1.0e-12
    safe_h = xp.where(has_horizontal, horizontal, xp.ones_like(horizontal))
    lift_axis = xp.stack(
        (
            xp.where(has_horizontal, relative_body[:, 2] / safe_h, 0.0),
            xp.zeros_like(relative_body[:, 1]),
            xp.where(has_horizontal, -relative_body[:, 0] / safe_h, -1.0),
        ),
        axis=-1,
    )
    side_axis = _cross(xp, drag_axis, lift_axis)
    side_norm = _norm(xp, side_axis)
    safe_side = xp.where(side_norm > 1.0e-12, side_norm, xp.ones_like(side_norm))
    side_axis = side_axis / safe_side[:, xp.newaxis]
    return (
        -drag[:, xp.newaxis] * drag_axis
        + side[:, xp.newaxis] * side_axis
        + lift[:, xp.newaxis] * lift_axis
    )


def _restoring_moment_batch(
    xp: Any,
    params: FlightParams,
    omega: Any,
    dynamic_pressure: Any,
    speed: Any,
    beta_rad: Any,
) -> Any:
    """Flow-dependent keel damping and the dihedral/sideslip stability term."""
    q_dyn = xp.maximum(dynamic_pressure, 0.0)
    airspeed = xp.maximum(speed, 0.0)
    active = (q_dyn > 0.0) & (airspeed > 1.0e-9)
    scale = airspeed / (airspeed + KEEL_DAMPING_CUTOFF_MPS)
    rate_hat = params.span / (2.0 * xp.maximum(airspeed, 1.0e-9))
    keel = q_dyn * params.area * params.span * rate_hat * scale
    roll = keel * KEEL_ROLL_DAMPING * omega[:, 0]
    yaw = keel * KEEL_YAW_DAMPING * omega[:, 2]
    dihedral = (
        q_dyn
        * params.area
        * params.span
        * 0.04
        * params.dihedral_rad
        * xp.sin(beta_rad)
        * scale
    )
    moment = xp.stack((roll - dihedral, xp.zeros_like(roll), yaw), axis=-1)
    return xp.where(active[:, xp.newaxis], moment, xp.zeros_like(moment))


def _ld_ratio(xp: Any, cl: Any, cd: Any) -> Any:
    """Lift-to-drag ratio, zero where the drag vanishes."""
    safe = xp.where(xp.abs(cd) > 1.0e-12, cd, xp.ones_like(cd))
    return xp.where(xp.abs(cd) > 1.0e-12, cl / safe, 0.0)


def _evaluate_state(
    xp: Any, params: FlightParams, y: Any, wind: Any
) -> Dict[str, Any]:
    """Rigid-body dynamics at a batch of 13-states."""
    position = y[:, 0:3]
    velocity = y[:, 3:6]
    quat = quaternion_normalize_batch(xp, y[:, 6:10])
    omega = y[:, 10:13]

    gravity_world = xp.stack(
        (
            xp.zeros_like(params.mass),
            xp.zeros_like(params.mass),
            xp.full_like(params.mass, params.gravity),
        ),
        axis=-1,
    )
    altitude = -position[:, 2] - params.ground_level_m
    air = atmosphere_batch(xp, params.env, xp.maximum(altitude, 0.0), params.chord)

    relative = velocity - wind
    speed = _norm(xp, relative)
    rotation = quaternion_to_matrix_batch(xp, quat)
    body_relative = world_to_body_batch(xp, rotation, relative)

    safe_speed = xp.where(speed > 1.0e-9, speed, xp.ones_like(speed))
    alpha = _deg(xp, xp.arctan2(body_relative[:, 2], body_relative[:, 0]))
    beta = _deg(
        xp, xp.arcsin(xp.clip(body_relative[:, 1] / safe_speed, -1.0, 1.0))
    )
    mach = speed / xp.maximum(air["sound_speed_mps"], 1.0)
    reynolds = speed * params.chord / xp.maximum(air["kinematic_viscosity_m2s"], 1.0e-12)

    coefficients = aero_kernel.evaluate_coefficients(
        xp,
        params.aero,
        alpha_deg=alpha,
        beta_deg=beta,
        p_rate=omega[:, 0],
        q_rate=omega[:, 1],
        r_rate=omega[:, 2],
        speed=speed,
        reynolds=reynolds,
        mach=mach,
        ground_height_m=xp.maximum(altitude, 0.0),
        density=air["density_kgm3"],
    )

    force_body = _wind_force_to_body_batch(
        xp,
        body_relative,
        coefficients["drag_n"],
        coefficients["side_n"],
        coefficients["lift_n"],
    )
    force_world = body_to_world_batch(xp, rotation, force_body) + params.mass[:, xp.newaxis] * gravity_world
    acceleration = force_world / params.mass[:, xp.newaxis]

    beta_rad = _rad(xp, beta)
    moment_body = xp.stack(
        (
            coefficients["rolling_nm"],
            coefficients["pitching_nm"],
            coefficients["yawing_nm"],
        ),
        axis=-1,
    ) + _restoring_moment_batch(
        xp, params, omega, coefficients["q_dyn"], speed, beta_rad
    )

    angular_momentum = params.inertia * omega
    angular_acceleration = (
        moment_body - _cross(xp, omega, angular_momentum)
    ) / params.inertia

    specific_force = world_to_body_batch(
        xp, rotation, acceleration - gravity_world
    )
    gravity_ref = abs(params.gravity) if abs(params.gravity) > 1.0e-9 else 1.0
    g_load = _norm(xp, specific_force) / gravity_ref

    return {
        "acceleration": acceleration,
        "angular_acceleration": angular_acceleration,
        "speed": speed,
        "alpha": alpha,
        "beta": beta,
        "g_load": g_load,
        "specific_force": specific_force,
        "dynamic_pressure": coefficients["q_dyn"],
        "lift_n": coefficients["lift_n"],
        "drag_n": coefficients["drag_n"],
        "cl": coefficients["cl"],
        "cd": coefficients["cd"],
        "cm": coefficients["cm"],
        "ld_ratio": _ld_ratio(xp, coefficients["cl"], coefficients["cd"]),
        "stalled": coefficients["stalled"],
        "stall_margin": coefficients["stall_margin"],
        "altitude": altitude,
        "density": air["density_kgm3"],
    }


def _derivative(xp: Any, params: FlightParams, y: Any, wind: Any) -> Any:
    """Right-hand side of the 13-state ODE."""
    core = _evaluate_state(xp, params, y, wind)
    quat = quaternion_normalize_batch(xp, y[:, 6:10])
    return xp.concatenate(
        (
            y[:, 3:6],
            core["acceleration"],
            quaternion_derivative_batch(xp, quat, y[:, 10:13]),
            core["angular_acceleration"],
        ),
        axis=1,
    )


def _rk4(xp: Any, params: FlightParams, y0: Any, h: Any, wind: Any) -> Any:
    """One classical RK4 sweep, renormalising the quaternions afterwards.

    The wind field is sampled **once per step** and shared by the four
    stages: the field varies over seconds, not over the millisecond of one
    step, and re-evaluating the (expensive) turbulence model four times per
    step for every case would make the batch far slower than the path it
    replaces.
    """
    k1 = _derivative(xp, params, y0, wind)
    k2 = _derivative(xp, params, y0 + 0.5 * h[:, xp.newaxis] * k1, wind)
    k3 = _derivative(xp, params, y0 + 0.5 * h[:, xp.newaxis] * k2, wind)
    k4 = _derivative(xp, params, y0 + h[:, xp.newaxis] * k3, wind)
    y1 = y0 + (h[:, xp.newaxis] / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)
    quat = quaternion_normalize_batch(xp, y1[:, 6:10])
    y1 = xp.concatenate((y1[:, 0:6], quat, y1[:, 10:13]), axis=1)
    return y1


def _ground_stations(xp: Any, params: FlightParams, y: Any) -> Dict[str, Any]:
    """Ground clearance of the key airframe stations of every case.

    Mirrors :func:`physics.flight_sim.compute_ground_stations`: the nose,
    the two wing tips, the tail and the keel are tracked, and the lowest of
    them decides whether the airframe is in contact with the floor.
    """
    mac = xp.maximum(params.chord, 1.0e-3)
    semispan = params.span * 0.5
    rotation = quaternion_to_matrix_batch(xp, quaternion_normalize_batch(xp, y[:, 6:10]))
    nose = xp.stack((0.5 * mac, xp.zeros_like(mac), xp.zeros_like(mac)), axis=-1)
    left = xp.stack((-0.3 * mac, -semispan, xp.zeros_like(mac)), axis=-1)
    right = xp.stack((-0.3 * mac, semispan, xp.zeros_like(mac)), axis=-1)
    tail = xp.stack(
        (-0.7 * mac, xp.zeros_like(mac), xp.full_like(mac, GROUND_KEEL_DEPTH_M)), axis=-1
    )
    keel = xp.stack(
        (xp.zeros_like(mac), xp.zeros_like(mac), xp.full_like(mac, GROUND_KEEL_DEPTH_M)),
        axis=-1,
    )
    z_cg = y[:, 2]
    altitudes = []
    for station in (nose, left, right, tail, keel):
        offset = body_to_world_batch(xp, rotation, station)[:, 2]
        altitudes.append(-(z_cg + offset) - params.ground_level_m)
    stacked = xp.stack(altitudes, axis=-1)
    return {
        "min_alt": xp.min(stacked, axis=-1),
        "alt_cg": -z_cg - params.ground_level_m,
    }


def _step_size(xp: Any, params: FlightParams, y: Any, core: Dict[str, Any], dt_max: float) -> Any:
    """Combine the CFL-style limits into one clamped step, per case."""
    omega = y[:, 10:13]
    rate = _norm(xp, omega)
    speed = core["speed"]
    limits = xp.full_like(rate, float(dt_max))
    positive_rate = rate > 1.0e-9
    safe_rate = xp.where(positive_rate, rate, xp.ones_like(rate))
    limits = xp.minimum(
        limits,
        xp.where(positive_rate, MAX_ATTITUDE_STEP_RAD / safe_rate, limits),
    )
    positive_speed = speed > 1.0e-9
    safe_speed = xp.where(positive_speed, speed, xp.ones_like(speed))
    limits = xp.minimum(
        limits,
        xp.where(
            positive_speed,
            TRAVEL_FRACTION_PER_STEP * params.chord / safe_speed,
            limits,
        ),
    )
    rate_change = _norm(xp, core["angular_acceleration"])
    positive_change = rate_change > 1.0e-9
    safe_change = xp.where(positive_change, rate_change, xp.ones_like(rate_change))
    limits = xp.minimum(
        limits,
        xp.where(positive_change, MAX_RATE_CHANGE_RADPS / safe_change, limits),
    )
    return xp.clip(limits, DT_MIN, float(dt_max))


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def launch_state_vector(
    launch: Any, env: EnvironmentConfig, aircraft: AircraftConfig
) -> Any:
    """The 13-state launch condition of one airframe.

    The release condition is produced by the *reference* throw model of
    :func:`physics.flight_sim.initial_state`, so the batch starts from
    exactly the state the 6-DoF path starts from -- same position, same
    velocity, same attitude, same body rates.
    """
    from . import flight_sim

    state = flight_sim.initial_state(launch, env, aircraft)
    return np.concatenate(
        (
            np.asarray(state.position_m, dtype=np.float64),
            np.asarray(state.velocity_mps, dtype=np.float64),
            np.asarray(state.quaternion, dtype=np.float64),
            np.asarray(state.angular_velocity_radps, dtype=np.float64),
        )
    )


def simulate_batch(
    xp: Any,
    params: FlightParams,
    initial_state: Any,
    duration_s: float,
    dt_max: float = 0.01,
    max_steps: int = 20000,
) -> Dict[str, Any]:
    """Integrate a batch of airframes with the 6-DoF rigid-body model.

    ``initial_state`` is an ``(N, 13)`` array of
    ``(position, velocity, quaternion, omega)`` rows.  ``duration_s`` may be a
    scalar or one duration per case.  The result carries the trajectory
    summary of every case: the peak dynamic pressure, the peak load factor,
    the peak wing-root stress, the flight status and the distance flown.

    The stress is evaluated with the *shared* structural model
    (:func:`physics.stress_domain.peak_root_stress_pa`) and normalised with
    the *shared* allowable (:func:`physics.stress_domain.paper_allowable_stress`),
    so the number this path reports is the same number the 6-DoF path
    reports for the same flight.
    """
    count = initial_state.shape[0]
    y = xp.array(initial_state, dtype=xp.float64, copy=True)
    duration = xp.broadcast_to(
        xp.asarray(duration_s, dtype=xp.float64), (count,)
    ).copy()
    dt_max = max(float(dt_max), DT_MIN)

    peak_q = xp.zeros(count)
    peak_g = xp.zeros(count)
    peak_stress = xp.zeros(count)
    peak_stress_time = xp.zeros(count)
    landed = xp.zeros(count, dtype=bool)
    crashed = xp.zeros(count, dtype=bool)
    flight_time = xp.zeros(count)
    distance = xp.zeros(count)
    stall_events = xp.zeros(count)
    min_stall_margin = xp.ones(count)
    max_ld_ratio = xp.zeros(count)
    trim_cm = xp.zeros(count)
    trim_alpha = xp.zeros(count)
    trim_speed = xp.zeros(count)
    trim_altitude = xp.zeros(count)

    # Every case keeps its own clock: the step size is a per-case quantity
    # (it depends on the local rate, speed and stiffness), so a shared
    # clock would silently truncate the cases that take smaller steps.
    case_time = xp.zeros(count)

    def stress_of(core: Dict[str, Any]) -> Any:
        """Wing-root bending stress of every case at the given state."""
        return peak_root_stress_pa(
            xp,
            load_factor=core["g_load"],
            mass_kg=params.mass,
            gravity=params.gravity,
            span_m=params.span,
            taper_ratio=params.taper_ratio,
            root_chord_m=params.root_chord_m,
            thickness_m=params.thickness_m,
            z_gain=params.z_gain,
            chord_scale=params.chord_scale,
            tip_factor=params.tip_factor,
        )

    def horizontal_distance(state: Any) -> Any:
        """Straight-line horizontal range from the launch point."""
        return xp.sqrt(state[:, 0] ** 2 + state[:, 1] ** 2)

    # The launch state is part of the flight: the 6-DoF path records a
    # sample at ``t = 0`` and the peak load factor of a hard launch happens
    # exactly there.  The summary therefore starts from the initial state,
    # not from the first integrated step.
    launch_wind = wind_batch(xp, params, y[:, 0:3], 0.0)
    launch_core = _evaluate_state(xp, params, y, launch_wind)
    peak_q = xp.maximum(peak_q, launch_core["dynamic_pressure"])
    peak_g = xp.maximum(peak_g, launch_core["g_load"])
    peak_stress = xp.maximum(peak_stress, stress_of(launch_core))
    stall_events = xp.where(launch_core["stalled"], 1.0, 0.0)
    if float(launch_core["speed"][0]) >= SPEED_STAT_FLOOR_MPS:
        max_ld_ratio = xp.maximum(max_ld_ratio, xp.abs(launch_core["ld_ratio"]))
        trim_cm = xp.where(
            xp.abs(launch_core["ld_ratio"]) >= max_ld_ratio, launch_core["cm"], trim_cm
        )
        trim_alpha = xp.where(
            xp.abs(launch_core["ld_ratio"]) >= max_ld_ratio, launch_core["alpha"], trim_alpha
        )
        trim_speed = xp.where(
            xp.abs(launch_core["ld_ratio"]) >= max_ld_ratio, launch_core["speed"], trim_speed
        )
        trim_altitude = xp.where(
            xp.abs(launch_core["ld_ratio"]) >= max_ld_ratio, launch_core["altitude"], trim_altitude
        )

    for _ in range(int(max_steps)):
        active = (~landed) & (~crashed) & (case_time < duration)
        if not bool(xp.any(active)):
            break
        # The wind field is sampled once per step for the whole batch, at
        # the mean time of the active cases.
        mean_time = float(xp.mean(case_time[active]))
        wind = wind_batch(xp, params, y[:, 0:3], mean_time)
        core = _evaluate_state(xp, params, y, wind)
        step = _step_size(xp, params, y, core, dt_max)
        step = xp.minimum(step, xp.maximum(duration - case_time, 0.0))
        step = xp.where(active, step, xp.zeros_like(step))

        previous = y
        y = _rk4(xp, params, y, step, wind)
        # Cases that already landed or crashed are frozen: the batch is
        # integrated as one array, so an inactive case must be restored to
        # its own last valid state instead of drifting underground.
        y = xp.where(active[:, xp.newaxis], y, previous)
        # The reported distance is the accumulated **horizontal path
        # length**, exactly as the 6-DoF path sums it over its samples: in a
        # crosswind the track is longer than the straight line to the
        # touchdown point, and the two must not be confused.
        travelled = xp.sqrt((y[:, 0] - previous[:, 0]) ** 2 + (y[:, 1] - previous[:, 1]) ** 2)
        distance = distance + xp.where(active, travelled, xp.zeros_like(travelled))
        case_time = case_time + step

        # --- accumulate the flight summary --------------------------------
        # The summary is evaluated on the **post-step** state: that is the
        # state the airframe actually reached, and it is the state the 6-DoF
        # path records its samples at.
        core = _evaluate_state(xp, params, y, wind)
        stress = stress_of(core)
        index = stress / params.allowable_pa
        peak_q = xp.where(active, xp.maximum(peak_q, core["dynamic_pressure"]), peak_q)
        peak_g = xp.where(active, xp.maximum(peak_g, core["g_load"]), peak_g)
        worse = active & (stress > peak_stress)
        peak_stress = xp.where(worse, stress, peak_stress)
        peak_stress_time = xp.where(worse, case_time, peak_stress_time)
        stall_events = stall_events + xp.where(active & core["stalled"], 1.0, 0.0)
        min_stall_margin = xp.where(
            active, xp.minimum(min_stall_margin, core["stall_margin"]), min_stall_margin
        )
        # The best-glide point defines the trim: the reference path evaluates
        # the stability metric ``1 / (1 + |C_m|)`` at the sample with the
        # largest L/D, so the batch tracks the same point.
        fast = core["speed"] >= SPEED_STAT_FLOOR_MPS
        candidate = xp.where(fast, xp.abs(core["ld_ratio"]), 0.0)
        better = active & (candidate > max_ld_ratio)
        max_ld_ratio = xp.where(better, candidate, max_ld_ratio)
        trim_cm = xp.where(better, core["cm"], trim_cm)
        trim_alpha = xp.where(better, core["alpha"], trim_alpha)
        trim_speed = xp.where(better, core["speed"], trim_speed)
        trim_altitude = xp.where(better, core["altitude"], trim_altitude)
        flight_time = xp.where(active, case_time, flight_time)

        # --- ground contact ------------------------------------------------
        # The contact is detected on the *stations* of the airframe, not on
        # the centre of gravity: the keel and the wing tips reach the ground
        # before the CG does, and the reference path resolves the contact on
        # the deepest station.  Using the CG alone would let the batch
        # report "still flying" for an airframe that is already on the floor.
        stations = _ground_stations(xp, params, y)
        touching = stations["min_alt"] <= 0.0
        # The impact load factor follows the reference model: the structural
        # stop distance converts the impact speed into the load the root
        # section has to carry, ``n = 1 + v^2 / (2 g d)``.
        impact_speed = xp.maximum(y[:, 2], 0.0)
        impact_g = 1.0 + impact_speed * impact_speed / (2.0 * params.gravity * 0.031)
        impact_stress = peak_root_stress_pa(
            xp,
            load_factor=impact_g,
            mass_kg=params.mass,
            gravity=params.gravity,
            span_m=params.span,
            taper_ratio=params.taper_ratio,
            root_chord_m=params.root_chord_m,
            thickness_m=params.thickness_m,
            z_gain=params.z_gain,
            chord_scale=params.chord_scale,
            tip_factor=params.tip_factor,
        )
        impact_index = impact_stress / params.allowable_pa
        peak_stress = xp.where(
            active & touching, xp.maximum(peak_stress, impact_stress), peak_stress
        )
        failed = touching & (
            (impact_index >= 1.0) | (impact_speed > CRASH_IMPACT_SPEED_MPS)
        )
        landed = xp.where(touching & ~failed, xp.ones(count, dtype=bool), landed)
        crashed = xp.where(failed, xp.ones(count, dtype=bool), crashed)
        del previous
    # The stability metric of the reference path is the **static** trim
    # residual at the best-glide point: the pitching moment coefficient of
    # the airframe trimmed at that angle of attack, with no sideslip and no
    # body rates.  It is evaluated here with the same shared kernel.
    trim_air = atmosphere_batch(
        xp, params.env, xp.maximum(trim_altitude, 0.0), params.chord
    )
    trim_reynolds = trim_speed * params.chord / xp.maximum(
        trim_air["kinematic_viscosity_m2s"], 1.0e-12
    )
    trim_mach = trim_speed / xp.maximum(trim_air["sound_speed_mps"], 1.0)
    trim_coefficients = aero_kernel.evaluate_coefficients(
        xp,
        params.aero,
        alpha_deg=trim_alpha,
        beta_deg=0.0,
        p_rate=0.0,
        q_rate=0.0,
        r_rate=0.0,
        speed=trim_speed,
        reynolds=trim_reynolds,
        mach=trim_mach,
        ground_height_m=xp.maximum(trim_altitude, 0.0),
        density=trim_air["density_kgm3"],
    )
    trim_cm = trim_coefficients["cm"]

    return {
        "state": y,
        "peak_dynamic_pressure_pa": peak_q,
        "peak_load_factor": peak_g,
        "peak_stress_pa": peak_stress,
        "peak_stress_time_s": peak_stress_time,
        "peak_stress_index": peak_stress / params.allowable_pa,
        "allowable_stress_pa": params.allowable_pa,
        "landed": landed,
        "crashed": crashed,
        "flight_time_s": flight_time,
        "distance_m": distance,
        "stall_events": stall_events,
        "stall_margin": min_stall_margin,
        "max_ld_ratio": max_ld_ratio,
        "stability": 1.0 / (1.0 + xp.abs(trim_cm)),
    }
