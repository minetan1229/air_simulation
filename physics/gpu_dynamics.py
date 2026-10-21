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
from .flight_sim import MODEL_REYNOLDS_MAX, MODEL_REYNOLDS_MIN
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
    # The identity quaternion is built on the device: a host side
    # ``xp.array([1, 0, 0, 0])`` would be an implicit host to device copy, and
    # a copy is forbidden while a CUDA graph is being captured.
    fallback = xp.zeros((quat.shape[0], 4), dtype=xp.float64)
    fallback[:, 0] = 1.0
    return xp.where((norm > _EPS)[:, xp.newaxis], out, fallback)


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


def _contract3(xp: Any, rotation: Any, vector: Any) -> Any:
    """Contract an ``(N, 3, 3)`` rotation with an ``(N, 3)`` vector.

    The contraction is written out element by element instead of with
    ``einsum``: CuPy lowers ``einsum`` to cuBLAS, and a cuBLAS call cannot be
    captured into a CUDA graph, so the ``einsum`` form would make the whole
    integration step uncapturable.  The two forms are the same sum in the same
    order -- ``(r00 * v0 + r01 * v1) + r02 * v2`` -- so they differ by at most
    one unit in the last place, and the CPU (NumPy) and the device (CuPy) path
    evaluate exactly the same expression.
    """
    v0 = vector[:, 0]
    v1 = vector[:, 1]
    v2 = vector[:, 2]
    return xp.stack(
        (
            rotation[:, 0, 0] * v0 + rotation[:, 0, 1] * v1 + rotation[:, 0, 2] * v2,
            rotation[:, 1, 0] * v0 + rotation[:, 1, 1] * v1 + rotation[:, 1, 2] * v2,
            rotation[:, 2, 0] * v0 + rotation[:, 2, 1] * v1 + rotation[:, 2, 2] * v2,
        ),
        axis=-1,
    )


def body_to_world_batch(xp: Any, rotation: Any, vector_body: Any) -> Any:
    """Rotate ``(N, 3)`` body vectors into the world frame."""
    return _contract3(xp, rotation, vector_body)


def world_to_body_batch(xp: Any, rotation: Any, vector_world: Any) -> Any:
    """Rotate ``(N, 3)`` world vectors into the body frame."""
    return _contract3(xp, xp.swapaxes(rotation, 1, 2), vector_world)


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
    dihedral_rad: Any                # (N,) dihedral angle in radians
    dihedral_sin: Any                # (N,) sin(dihedral angle)
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
    out: Any = None,
) -> Any:
    """Wind velocity of the whole batch in the **z-down world frame**.

    The field is the one of :mod:`physics.wind_domain` -- the log-law
    boundary layer, the thermal plume and the three decorrelated turbulence
    channels -- evaluated for every case at once.  The 6-DoF path samples the
    same field, so the two paths see the same air mass.

    The upstream solver works on a *z-up* height axis; the query points are
    converted the same way :func:`physics.flight_sim.wind_vector` converts
    them -- with ``z_up = -z_ned``, the z-up coordinate of the point itself,
    from which the solver forms the height above the ground plane
    (``z_up - ground_level_m``).  Adding the ground level here would sample
    the boundary layer at the altitude above the world origin instead of at
    the height above the ground plane, and the two paths would fly in
    different air masses.  The returned vertical component is negated back
    into the z-down world frame.

    ``out`` is an optional ``(N, 3)`` buffer the wind is written into.  The
    captured integration step reads the wind from a *fixed* address, so the
    wind of every step has to be copied into the same buffer.
    """
    if params.use_mean_wind_only:
        mean = xp.broadcast_to(params.mean_wind, (positions.shape[0], 3))
        if out is None:
            return mean
        out[...] = mean
        return out
    # The wind field is evaluated on the host, so the query points have to be
    # brought back explicitly.  ``np.asarray`` on a CuPy array raises
    # "Implicit conversion to a NumPy array is not allowed".
    host_positions = positions.get() if hasattr(positions, "get") else np.asarray(positions)
    points = np.stack(
        (
            np.asarray(host_positions[:, 0], dtype=np.float64),
            np.asarray(host_positions[:, 1], dtype=np.float64),
            -np.asarray(host_positions[:, 2], dtype=np.float64),
        ),
        axis=-1,
    )
    values = velocity_components(points, params.env, float(time_s), params.wind_backend)
    values = np.asarray(values, dtype=np.float64)
    wind = xp.asarray(
        np.stack((values[:, 0], values[:, 1], -values[:, 2]), axis=-1),
        dtype=xp.float64,
    )
    if out is None:
        return wind
    out[...] = wind
    return out


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

    mass_values = [max(float(item["mass_kg"]), 1.0e-9) for item in derived]
    area_values = [max(float(item["ref_area_m2"]), 1.0e-9) for item in derived]
    chord_values = [max(float(item["ref_length_m"]), 1.0e-9) for item in derived]
    span_values = [max(float(item["ref_span_m"]), 1.0e-6) for item in derived]
    mass = xp.asarray(mass_values)
    inertia = xp.stack(
        (
            xp.asarray([max(float(item["roll_inertia_kgm2"]), 1.0e-12) for item in derived]),
            xp.asarray([max(float(item["pitch_inertia_kgm2"]), 1.0e-12) for item in derived]),
            xp.asarray([max(float(item["yaw_inertia_kgm2"]), 1.0e-12) for item in derived]),
        ),
        axis=-1,
    )
    areas = xp.asarray(area_values)
    chords = xp.asarray(chord_values)
    spans = xp.asarray(span_values)

    # Every per-case parameter bundle is built by the shared builder, which
    # sanitises it: the kernel can then use the values directly instead of
    # re-checking each of them for finiteness on every evaluation.
    case_params = [aero_kernel.params_from_aircraft(cfg) for cfg in configs]

    def stacked(field: str) -> Any:
        return xp.asarray([getattr(item, field) for item in case_params])

    aero = aero_kernel.AeroParams(
        **{
            name: stacked(name)
            for name in (
                "area", "chord", "span", "aspect", "oswald", "cL0", "cl_alpha",
                "cl_max", "stall_alpha_deg", "stall_sharpness", "polhamus_kp",
                "polhamus_kv", "cd0", "re_cd0_exponent", "cm0", "cm_alpha",
                "cm_q", "cm_roll_rate", "cm_pitch_rate", "cm_yaw_rate",
                "cl_beta", "cl_roll_rate", "cl_pitch_rate", "cl_yaw_rate",
                "cn_beta", "cn_roll_rate", "cn_pitch_rate", "cn_yaw_rate",
                "cy_beta", "aileron_deg", "asymmetry_cl", "rudder_deg",
                "elevon_deg", "elevon_authority", "fold_trailing_deg",
                "cg_offset_x",
                "cp_shift_gain", "ground_cushion_lift", "ground_pitch_coupling",
            )
        }
    )
    lift_scale, drag_scale = aero_kernel.environment_scales(env)
    aero.lift_scale = ones * lift_scale
    aero.drag_scale = ones * drag_scale
    aero.compressible = xp.asarray(bool(env.compressibility_enabled))
    aero.ground_effect_enabled = xp.asarray(bool(env.ground_effect_enabled))

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
    # The canonical structural model is reached through two doors: the 6-DoF
    # reference path reports the peak of the stress field
    # (:func:`physics.stress_domain.stress_field`, reached through
    # ``stress_domain.analyse``), while the batch evaluates the *scalar
    # reduction* of that field
    # (:func:`physics.stress_domain.peak_root_stress_pa`).  The two are the
    # same quantity because the reduction carries every factor the field
    # applies to the wing-root moment -- the projection of that moment on the
    # body vertical by ``cos(dihedral)`` among them.  No extra calibration
    # factor is needed here: the reduction is the field, evaluated at its
    # peak station.

    return FlightParams(
        env=env,
        mass=mass,
        inertia=inertia,
        area=areas,
        chord=chords,
        span=spans,
        dihedral_rad=_rad(xp, column("dihedral_deg")),
        dihedral_sin=xp.sin(_rad(xp, column("dihedral_deg"))),
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
        * params.dihedral_sin
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
    # The canonical 6-DoF path evaluates the aerodynamic model only inside its
    # validated Reynolds envelope: ``flight_sim.aero_forces`` clamps the
    # Reynolds number to ``[MODEL_REYNOLDS_MIN, MODEL_REYNOLDS_MAX]`` before it
    # reaches the shared kernel.  The batch path used the raw Reynolds number,
    # so for an airframe whose chord is short enough to run above the envelope
    # (a GA airframe reaches Re ~ 1.7e5 at the release speed) the two paths
    # evaluated the *same* kernel with a *different* parasite drag: the
    # Reynolds correction ``cd0 (1e5/Re) ** exponent`` gave the batch 1.5 %
    # less drag than the canonical path, which moved the load factor by
    # 1.1e-3 and the peak wing-root stress by the same factor.  The envelope is
    # a property of the model, so the batch has to apply it as well.
    reynolds = xp.clip(
        reynolds, float(MODEL_REYNOLDS_MIN), float(MODEL_REYNOLDS_MAX)
    )

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


def _derivative_from_core(
    xp: Any, params: FlightParams, y: Any, core: Dict[str, Any]
) -> Any:
    """Right-hand side of the 13-state ODE, from an evaluated state.

    The state evaluation is the expensive part of the right-hand side (it
    runs the shared aerodynamic kernel), so it is evaluated once and reused:
    the RK4 sweep needs the derivative of the *pre-step* state for its first
    stage, and the step controller needs the same evaluation for the step
    size.  Computing it once instead of twice removes one sixth of the
    aerodynamic evaluations of a step -- and every one of them is several
    hundred kernel launches.
    """
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


def _derivative(xp: Any, params: FlightParams, y: Any, wind: Any) -> Any:
    """Right-hand side of the 13-state ODE."""
    return _derivative_from_core(xp, params, y, _evaluate_state(xp, params, y, wind))


def _rk4(
    xp: Any,
    params: FlightParams,
    y0: Any,
    h: Any,
    wind: Any,
    k1: Any = None,
) -> Any:
    """One classical RK4 sweep, renormalising the quaternions afterwards.

    The wind field is sampled **once per step** and shared by the four
    stages: the field varies over seconds, not over the millisecond of one
    step, and re-evaluating the (expensive) turbulence model four times per
    step for every case would make the batch far slower than the path it
    replaces.

    ``k1`` may be supplied by the caller when the derivative of ``y0`` has
    already been evaluated; it is then reused instead of being computed a
    second time.
    """
    if k1 is None:
        k1 = _derivative(xp, params, y0, wind)
    k2 = _derivative(xp, params, y0 + 0.5 * h[:, xp.newaxis] * k1, wind)
    k3 = _derivative(xp, params, y0 + 0.5 * h[:, xp.newaxis] * k2, wind)
    k4 = _derivative(xp, params, y0 + h[:, xp.newaxis] * k3, wind)
    y1 = y0 + (h[:, xp.newaxis] / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)
    quat = quaternion_normalize_batch(xp, y1[:, 6:10])
    y1 = xp.concatenate((y1[:, 0:6], quat, y1[:, 10:13]), axis=1)
    return y1


def _station_altitudes(
    xp: Any, params: FlightParams, z_cg: Any, rotation: Any
) -> Any:
    """Altitude of the five key stations of every case.

    The stations are the nose, the two wing tips, the tail and the keel, in
    the order the reference path tracks them.  The altitude of a station is
    the height of its point above the ground plane, so the lowest of the five
    decides whether the airframe is in contact with the floor.
    """
    mac = xp.maximum(params.chord, 1.0e-3)
    semispan = params.span * 0.5
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
    altitudes = []
    for station in (nose, left, right, tail, keel):
        offset = body_to_world_batch(xp, rotation, station)[:, 2]
        altitudes.append(-(z_cg + offset) - params.ground_level_m)
    return xp.stack(altitudes, axis=-1)


def _ground_stations(xp: Any, params: FlightParams, y: Any) -> Dict[str, Any]:
    """Ground clearance of the key airframe stations of every case.

    Mirrors :func:`physics.flight_sim.compute_ground_stations`: the nose,
    the two wing tips, the tail and the keel are tracked, and the lowest of
    them decides whether the airframe is in contact with the floor.
    """
    rotation = quaternion_to_matrix_batch(xp, quaternion_normalize_batch(xp, y[:, 6:10]))
    stacked = _station_altitudes(xp, params, y[:, 2], rotation)
    return {
        "min_alt": xp.min(stacked, axis=-1),
        "alt_cg": -y[:, 2] - params.ground_level_m,
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


#: The capture mode that tolerates the asynchronous copies CuPy emits.
CAPTURE_MODE_RELAXED: int = 2


def _is_device_module(xp: Any) -> bool:
    """True when the array module is the CUDA one."""
    return getattr(xp, "__name__", "") == "cupy"


def _capture_step(
    xp: Any, step: Any, snapshot: Any, restore: Any
) -> Tuple[Any, Any, Any]:
    """Capture one integration step into a CUDA graph.

    The step issues several thousand kernel launches and the launch overhead
    dominates the arithmetic on a consumer GPU, so the whole step -- the RK4
    sweep, the contact resolution and the summary accumulation -- is captured
    once and replayed with a single launch.

    The captured step performs exactly the same operations in the same order
    as the eager step, so the numbers are the numbers of the eager path.

    Two device-memory rules make that true:

    * The step allocates its temporaries on the device.  They are released
      when the captured call returns, and the default allocator would hand
      their addresses to the next allocation -- the graph would then write
      into memory that belongs to somebody else, and the flight would
      depend on the allocator's state.  The capture therefore runs in a
      **private memory pool** whose free blocks are never handed out while
      the graph is alive (the pool is returned to the caller and outlives
      the graph).
    * ``cudaMalloc`` is not a stream-ordered operation, so it must not
      happen during the capture.  The step is therefore executed once
      *before* the capture, in the private pool, so that the pool already
      holds every block the capture needs.  The warm-up advances the flight
      by one step, so the state is saved before it and restored afterwards
      (``snapshot`` / ``restore``); the capture itself only *records* the
      kernels, it does not run them.

    Returns ``(graph, stream, pool)``, or ``(None, None, None)`` when the
    array module cannot capture: the CPU path, a host without a CUDA device,
    or a driver that refuses the capture.  The caller then runs the eager
    step.
    """
    if not _is_device_module(xp):
        return None, None, None
    try:
        import cupy as cp
    except Exception:  # pragma: no cover - depends on the host
        return None, None, None
    try:
        stream = cp.cuda.Stream(non_blocking=True)
        pool = cp.cuda.MemoryPool()
        saved = snapshot()
        with cp.cuda.using_allocator(pool.malloc):
            # The warm-up: the step runs once, outside the capture, so that
            # the private pool holds every block the capture allocates.  The
            # state is restored in any case: the warm-up advances the flight
            # by one step.
            try:
                step()
            finally:
                restore(saved)
            with stream:
                stream.begin_capture(mode=CAPTURE_MODE_RELAXED)
                step()
                graph = stream.end_capture()
        # Upload the graph to the device so the first replay does not pay
        # for the upload.
        graph.upload(stream)
        stream.synchronize()
    except Exception:  # pragma: no cover - depends on the driver
        return None, None, None
    return graph, stream, pool


def graph_kernel_launches(graph: Any) -> int:
    """Number of kernel launches captured in a CUDA graph.

    The CUDA graph dump lists one node per captured kernel, so the node count
    is the exact number of kernel launches the captured region issues.  It is
    the measurement the launch-count comparison of the parity harness is
    based on.
    """
    if graph is None:
        return 0
    try:
        return int(graph.debug_dot_str().count('shape="octagon"'))
    except Exception:  # pragma: no cover - depends on the driver
        return 0


def simulate_batch(
    xp: Any,
    params: FlightParams,
    initial_state: Any,
    duration_s: float,
    dt_max: float = 0.01,
    max_steps: int = 20000,
    use_graph: Optional[bool] = None,
    record_interval_s: Optional[float] = None,
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

    The flight summary is accumulated on the **reporting grid** of the
    canonical 6-DoF path: a sample is recorded when the flight clock has
    advanced by at least ``record_interval_s`` since the previous recorded
    sample, and the statistics (``distance_m``, ``max_ld_ratio``,
    ``peak_load_factor``, ``peak_stress_pa``, ``stall_margin`` and the trim
    point) are taken over the *recorded* samples only.  This is the same
    convention as :meth:`physics.flight_sim.FlightSimulator._record_sample`,
    and it is what makes the two paths report the *same statistic* instead
    of two different statistics of the same trajectory: the L/D maximum of
    this dart sits in the first milliseconds of the flight, where the 20 ms
    grid of the reference path has a single sample (the launch one) while
    the integration steps of the batch have dozens.  ``record_interval_s``
    defaults to ``dt_max``, which is the interval the reference path uses
    (``flight_sim.evaluate_flight`` records with ``record_interval_s =
    dt_max``).

    On a CUDA device the integration step is captured into a **CUDA graph**
    and replayed with a single launch (:func:`_capture_step`): the step
    issues several thousand kernel launches, and the launch overhead -- not
    the arithmetic -- dominates the wall clock.  The captured step performs
    exactly the same operations in the same order, so the numbers are the
    numbers of the eager path.  ``use_graph=False`` runs the eager path.
    """
    count = initial_state.shape[0]
    y = xp.array(initial_state, dtype=xp.float64, copy=True)
    duration = xp.broadcast_to(
        xp.asarray(duration_s, dtype=xp.float64), (count,)
    ).copy()
    dt_max = max(float(dt_max), DT_MIN)
    # The reporting grid of the flight summary.  It is the interval the
    # canonical 6-DoF path records its samples with, so both paths take their
    # statistics over the same kind of sample set.
    record_interval = max(
        float(dt_max if record_interval_s is None else record_interval_s), DT_MIN
    )

    # Every accumulator is a device array that the step updates **in place**:
    # a captured step replays the kernels it captured, so an accumulator that
    # was bound to a new array inside the step would only ever hold the
    # value of the capture.
    acc: Dict[str, Any] = {
        "peak_q": xp.zeros(count),
        "peak_g": xp.zeros(count),
        "peak_stress": xp.zeros(count),
        "peak_stress_time": xp.zeros(count),
        "landed": xp.zeros(count, dtype=bool),
        "crashed": xp.zeros(count, dtype=bool),
        "flight_time": xp.zeros(count),
        "distance": xp.zeros(count),
        "stall_events": xp.zeros(count),
        "stall_margin": xp.ones(count),
        "max_ld_ratio": xp.zeros(count),
        "trim_cm": xp.zeros(count),
        "trim_alpha": xp.zeros(count),
        "trim_speed": xp.zeros(count),
        "trim_altitude": xp.zeros(count),
        # Every case keeps its own clock: the step size is a per-case
        # quantity (it depends on the local rate, speed and stiffness), so a
        # shared clock would silently truncate the cases that take smaller
        # steps.
        "time": xp.zeros(count),
        # The reporting grid: the time of the last recorded sample and the
        # position of that sample, from which the horizontal path length of
        # the next recorded sample is measured.
        "record_time": xp.zeros(count),
        "record_x": xp.zeros(count),
        "record_y": xp.zeros(count),
        # The number of recorded samples per case: the size of the sample
        # set the summary was taken over.
        "samples": xp.zeros(count),
    }

    # The wind of a step is written into this buffer.  The captured step
    # reads the wind from a fixed address, so the wind of every step has to
    # be copied into the same array.
    wind = xp.zeros((count, 3), dtype=xp.float64)

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
            dihedral_rad=params.dihedral_rad,
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
    acc["peak_q"][...] = xp.maximum(acc["peak_q"], launch_core["dynamic_pressure"])
    acc["peak_g"][...] = xp.maximum(acc["peak_g"], launch_core["g_load"])
    acc["peak_stress"][...] = xp.maximum(acc["peak_stress"], stress_of(launch_core))
    acc["stall_events"][...] = xp.where(launch_core["stalled"], 1.0, 0.0)
    # The launch sample is the first sample of the reporting grid.
    acc["samples"][...] = 1.0
    # The launch sample is the first sample of the reporting grid, so the
    # path length is measured from it.
    acc["record_x"][...] = y[:, 0]
    acc["record_y"][...] = y[:, 1]
    # The glide statistics ignore the samples whose airspeed is below the
    # floor, the same way the reference path does.  The test is per case: a
    # batch holds airframes with different launch speeds.
    launch_fast = launch_core["speed"] >= SPEED_STAT_FLOOR_MPS
    acc["max_ld_ratio"][...] = xp.where(
        launch_fast, xp.maximum(acc["max_ld_ratio"], xp.abs(launch_core["ld_ratio"])), acc["max_ld_ratio"]
    )
    best = launch_fast & (xp.abs(launch_core["ld_ratio"]) >= acc["max_ld_ratio"])
    acc["trim_cm"][...] = xp.where(best, launch_core["cm"], acc["trim_cm"])
    acc["trim_alpha"][...] = xp.where(best, launch_core["alpha"], acc["trim_alpha"])
    acc["trim_speed"][...] = xp.where(best, launch_core["speed"], acc["trim_speed"])
    acc["trim_altitude"][...] = xp.where(
        best, launch_core["altitude"], acc["trim_altitude"]
    )

    # The clearance of the deepest station of the pre-step state.  It is
    # carried across the steps so that the contact can be resolved *inside*
    # the step that crossed the ground.  It is an accumulator of the step,
    # so the warm-up of the capture can save and restore it.
    acc["min_alt_prev"] = _ground_stations(xp, params, y)["min_alt"]
    min_alt_prev = acc["min_alt_prev"]

    def step() -> None:
        """Advance the whole batch by one integration step, in place."""
        active = (~acc["landed"]) & (~acc["crashed"]) & (acc["time"] < duration)
        core = _evaluate_state(xp, params, y, wind)
        size = _step_size(xp, params, y, core, dt_max)
        size = xp.minimum(size, xp.maximum(duration - acc["time"], 0.0))
        size = xp.where(active, size, xp.zeros_like(size))
        # The derivative of the pre-step state is the first RK4 stage, and
        # the state evaluation above is the same evaluation of the same
        # state.  It is evaluated once and shared instead of twice.
        k1 = _derivative_from_core(xp, params, y, core)
        y_next = _rk4(xp, params, y, size, wind, k1=k1)

        # --- ground contact ------------------------------------------------
        # The contact is detected on the *stations* of the airframe, not on
        # the centre of gravity: the keel and the wing tips reach the ground
        # before the CG does, and the reference path resolves the contact on
        # the deepest station.  Using the CG alone would let the batch
        # report "still flying" for an airframe that is already on the floor.
        stations = _ground_stations(xp, params, y_next)
        min_alt_next = stations["min_alt"]
        touching = min_alt_next <= 0.0
        # The contact is resolved with the continuous collision detection of
        # the reference path: the instant of the contact is interpolated
        # inside the step that crossed the ground, so the reported flight
        # time and distance are the ones of the touchdown and not the ones of
        # the end of the step.  Without the interpolation the contact time is
        # quantised by the step size, which is a visible difference between
        # the two paths (and between the device and the host execution of
        # this one).
        crossing = touching & (min_alt_prev > 0.0)
        span = min_alt_prev - min_alt_next
        safe_span = xp.where(span > 1.0e-12, span, xp.ones_like(span))
        tau = xp.where(
            crossing, xp.clip(min_alt_prev / safe_span, 0.0, 1.0), xp.zeros_like(span)
        )
        impact_time = acc["time"] + tau * size
        # The state of the touchdown: the position, the velocity, the
        # attitude and the body rates are interpolated, and the airframe is
        # placed exactly on the floor (the deepest station touches it).
        quat_interp = quaternion_normalize_batch(
            xp,
            (1.0 - tau)[:, xp.newaxis] * y[:, 6:10]
            + tau[:, xp.newaxis] * y_next[:, 6:10],
        )
        position_interp = y[:, 0:3] + tau[:, xp.newaxis] * (y_next[:, 0:3] - y[:, 0:3])
        velocity_interp = y[:, 3:6] + tau[:, xp.newaxis] * (y_next[:, 3:6] - y[:, 3:6])
        rates_interp = y[:, 10:13] + tau[:, xp.newaxis] * (y_next[:, 10:13] - y[:, 10:13])
        rotation_interp = quaternion_to_matrix_batch(xp, quat_interp)
        min_alt_interp = xp.min(
            _station_altitudes(xp, params, position_interp[:, 2], rotation_interp), axis=-1
        )
        position_interp[:, 2] = position_interp[:, 2] + min_alt_interp
        y_interp = xp.concatenate(
            (position_interp, velocity_interp, quat_interp, rates_interp), axis=1
        )
        # A contact step ends on the state of the touchdown, not on the state
        # at the end of the step (which is below the floor): that is the
        # state the reference path records for the contact sample, and the
        # state the flight summary of the step has to be evaluated at.
        y_next = xp.where(crossing[:, xp.newaxis], y_interp, y_next)

        # The reported distance is the accumulated **horizontal path
        # length**, exactly as the 6-DoF path sums it over its samples: in a
        # crosswind the track is longer than the straight line to the
        # touchdown point, and the two must not be confused.  The last
        # segment ends at the touchdown, not at the end of the step.
        #
        # The path length is summed over the **recorded samples** of the
        # reporting grid, like the reference path does: the two paths then
        # measure the same polyline of the same trajectory.  A step that is
        # not a recording step moves the polyline's last vertex forward
        # without adding to the length.
        end_x = xp.where(crossing, position_interp[:, 0], y_next[:, 0])
        end_y = xp.where(crossing, position_interp[:, 1], y_next[:, 1])
        # The time of the sample the step ends with: the instant of the
        # touchdown for a contact step, the end of the step otherwise.
        sample_time = xp.where(crossing, impact_time, acc["time"] + size)
        recorded = active & (sample_time >= acc["record_time"] + record_interval)
        travelled = xp.sqrt(
            (end_x - acc["record_x"]) ** 2 + (end_y - acc["record_y"]) ** 2
        )
        acc["distance"] += xp.where(recorded, travelled, xp.zeros_like(travelled))
        acc["record_x"][...] = xp.where(recorded, end_x, acc["record_x"])
        acc["record_y"][...] = xp.where(recorded, end_y, acc["record_y"])
        acc["record_time"][...] = xp.where(recorded, sample_time, acc["record_time"])
        acc["samples"] += xp.where(recorded, 1.0, 0.0)
        acc["time"] += size

        # --- accumulate the flight summary --------------------------------
        # The summary is evaluated on the **post-step** state: that is the
        # state the airframe actually reached, and it is the state the 6-DoF
        # path records its samples at.  A contact step ends on the state of
        # the touchdown, which is the state the reference path records.
        core_next = _evaluate_state(xp, params, y_next, wind)
        stress = stress_of(core_next)
        acc["peak_q"][...] = xp.where(
            recorded, xp.maximum(acc["peak_q"], core_next["dynamic_pressure"]), acc["peak_q"]
        )
        acc["peak_g"][...] = xp.where(
            recorded, xp.maximum(acc["peak_g"], core_next["g_load"]), acc["peak_g"]
        )
        worse = recorded & (stress > acc["peak_stress"])
        acc["peak_stress"][...] = xp.where(worse, stress, acc["peak_stress"])
        acc["peak_stress_time"][...] = xp.where(
            worse, acc["time"], acc["peak_stress_time"]
        )
        acc["stall_events"] += xp.where(recorded & core_next["stalled"], 1.0, 0.0)
        acc["stall_margin"][...] = xp.where(
            recorded,
            xp.minimum(acc["stall_margin"], core_next["stall_margin"]),
            acc["stall_margin"],
        )
        # The best-glide point defines the trim: the reference path evaluates
        # the stability metric ``1 / (1 + |C_m|)`` at the sample with the
        # largest L/D, so the batch tracks the same point.  The sample that
        # ends the flight in a contact is the *contact sample*: its L/D is
        # the transient of the bounce and it is excluded from the glide
        # statistics, exactly as the reference path excludes it.
        fast = core_next["speed"] >= SPEED_STAT_FLOOR_MPS
        candidate = xp.where(fast & ~touching, xp.abs(core_next["ld_ratio"]), 0.0)
        better = recorded & (candidate > acc["max_ld_ratio"])
        acc["max_ld_ratio"][...] = xp.where(better, candidate, acc["max_ld_ratio"])
        acc["trim_cm"][...] = xp.where(better, core_next["cm"], acc["trim_cm"])
        acc["trim_alpha"][...] = xp.where(better, core_next["alpha"], acc["trim_alpha"])
        acc["trim_speed"][...] = xp.where(better, core_next["speed"], acc["trim_speed"])
        acc["trim_altitude"][...] = xp.where(
            better, core_next["altitude"], acc["trim_altitude"]
        )
        # The flight time of a case that touched the floor is the instant of
        # the touchdown, not the end of the step that found it.
        end_time = xp.where(crossing, impact_time, acc["time"])
        acc["flight_time"][...] = xp.where(active, end_time, acc["flight_time"])

        # The impact load factor follows the reference model: the structural
        # stop distance converts the impact speed into the load the root
        # section has to carry, ``n = 1 + v^2 / (2 g d)``.
        #
        # The impact speed is the **vertical velocity** of the 13-state vector
        # (``y[:, 3:6]`` is the velocity, so the NED vertical component is
        # ``y[:, 5]``), not the altitude: ``y[:, 2]`` is the NED z *position*,
        # which is negative while the airframe is airborne and therefore
        # reported an impact speed of 0 m/s for every flight.  The crash test
        # was consequently never armed in the batch path and a steep impact
        # was reported as a landing while the 6-DoF path reported a crash.
        impact_speed = xp.maximum(
            xp.where(crossing, velocity_interp[:, 2], y_next[:, 5]), 0.0
        )
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
            dihedral_rad=params.dihedral_rad,
            chord_scale=params.chord_scale,
            tip_factor=params.tip_factor,
        )
        impact_index = impact_stress / params.allowable_pa
        # The impact stress decides the crash, it is **not** the reported peak
        # stress: the 6-DoF path reports the maximum stress of the *flight*
        # samples and uses the impact stress only for the crash criterion.
        # Folding the impact into the peak made the batch report a stress the
        # reference path never reports.
        failed = touching & (
            (impact_index >= 1.0) | (impact_speed > CRASH_IMPACT_SPEED_MPS)
        )
        acc["landed"] |= touching & ~failed
        acc["crashed"] |= failed

        # Cases that already landed or crashed are frozen: the batch is
        # integrated as one array, so an inactive case must be restored to
        # its own last valid state instead of drifting underground.
        y[...] = xp.where(active[:, xp.newaxis], y_next, y)
        min_alt_prev[...] = min_alt_next

    def snapshot() -> Dict[str, Any]:
        """Copy the mutable state of the batch, for the capture warm-up."""
        return {
            "state": xp.array(y, copy=True),
            "acc": {key: xp.array(value, copy=True) for key, value in acc.items()},
            "wind": xp.array(wind, copy=True),
        }

    def restore(saved: Dict[str, Any]) -> None:
        """Put the copied state back after the capture warm-up."""
        y[...] = saved["state"]
        for key, value in saved["acc"].items():
            acc[key][...] = value
        wind[...] = saved["wind"]

    if use_graph is None:
        use_graph = _is_device_module(xp)
    graph = None
    stream = None
    pool = None
    if use_graph:
        graph, stream, pool = _capture_step(xp, step, snapshot, restore)
    launches_per_step = graph_kernel_launches(graph)

    steps_executed = 0
    for _ in range(int(max_steps)):
        active = (~acc["landed"]) & (~acc["crashed"]) & (acc["time"] < duration)
        if not bool(xp.any(active)):
            break
        # The wind field is sampled once per step for the whole batch, at
        # the mean time of the active cases.
        #
        # The captured graph is replayed on the **current** stream, the same
        # stream the wind is written on and the same stream the host reads
        # of the batch state are issued on.  A graph replayed on a second,
        # non-blocking stream would not be ordered against the host reads of
        # this loop: the mean time and the wind would be taken from a state
        # the previous step had not written yet, and the flight would
        # depend on the order in which the streams happened to drain.
        mean_time = float(xp.mean(acc["time"][active]))
        wind_batch(xp, params, y[:, 0:3], mean_time, out=wind)
        if graph is not None:
            graph.launch()
        else:
            step()
        steps_executed += 1
    if graph is not None and stream is not None:
        stream.synchronize()
        # The graph keeps the memory of the temporaries it captured alive.
        # Releasing it before the caller allocates device memory of its own
        # keeps both of them sound.  The private memory pool of the capture
        # is released with it: its blocks are the ones the graph writes to.
        del graph
        graph = None
        del pool
        pool = None

    # The stability metric of the reference path is the **static** trim
    # residual at the best-glide point: the pitching moment coefficient of
    # the airframe trimmed at that angle of attack, with no sideslip and no
    # body rates.  It is evaluated here with the same shared kernel.
    trim_air = atmosphere_batch(
        xp, params.env, xp.maximum(acc["trim_altitude"], 0.0), params.chord
    )
    trim_reynolds = acc["trim_speed"] * params.chord / xp.maximum(
        trim_air["kinematic_viscosity_m2s"], 1.0e-12
    )
    # The same Reynolds envelope the canonical path applies to its trim
    # residual (``flight_sim._trim_residual_cm`` goes through
    # ``aero_forces``), so the trim sees the same model validity.
    trim_reynolds = xp.clip(
        trim_reynolds, float(MODEL_REYNOLDS_MIN), float(MODEL_REYNOLDS_MAX)
    )
    trim_mach = acc["trim_speed"] / xp.maximum(trim_air["sound_speed_mps"], 1.0)
    trim_coefficients = aero_kernel.evaluate_coefficients(
        xp,
        params.aero,
        alpha_deg=acc["trim_alpha"],
        beta_deg=0.0,
        p_rate=0.0,
        q_rate=0.0,
        r_rate=0.0,
        speed=acc["trim_speed"],
        reynolds=trim_reynolds,
        mach=trim_mach,
        ground_height_m=xp.maximum(acc["trim_altitude"], 0.0),
        density=trim_air["density_kgm3"],
    )
    trim_cm = trim_coefficients["cm"]

    return {
        "state": y,
        "peak_dynamic_pressure_pa": acc["peak_q"],
        "peak_load_factor": acc["peak_g"],
        "peak_stress_pa": acc["peak_stress"],
        "peak_stress_time_s": acc["peak_stress_time"],
        "peak_stress_index": acc["peak_stress"] / params.allowable_pa,
        "allowable_stress_pa": params.allowable_pa,
        "landed": acc["landed"],
        "crashed": acc["crashed"],
        "flight_time_s": acc["flight_time"],
        "distance_m": acc["distance"],
        "stall_events": acc["stall_events"],
        "stall_margin": acc["stall_margin"],
        "max_ld_ratio": acc["max_ld_ratio"],
        "stability": 1.0 / (1.0 + xp.abs(trim_cm)),
        "recorded_samples": acc["samples"],
        "last_recorded_time_s": acc["record_time"],
        # The position of the last recorded sample: the endpoint of the
        # path length the summary measures.  The canonical path reports its
        # straight-line range to the same point ("the final recorded
        # position", ``flight_sim.evaluate_flight``), so the two fields of
        # the payload refer to the same endpoints.
        "last_recorded_x": acc["record_x"],
        "last_recorded_y": acc["record_y"],
        "record_interval_s": float(record_interval),
        "integration_steps": int(steps_executed),
        "kernel_launches_per_step": int(launches_per_step),
        "kernel_launches_total": int(launches_per_step * steps_executed),
        "graph_captured": bool(launches_per_step > 0),
    }
