"""Tests for energy conservation and physical constraints during ground contact,
specifically focusing on wingtip strike scenarios to prevent perpetual bouncing
and non-physical energy creation.
"""

from __future__ import annotations

import numpy as np
import pytest

from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    SimulationSettings,
    StateVector,
)
from physics.flight_sim import FlightSimulator, compute_ground_stations, GROUND_KEEL_DEPTH_M
from physics.geometry_utils import body_to_world, quaternion_from_euler, quaternion_to_matrix


def _total_kinetic_energy(sim: FlightSimulator, state: StateVector) -> float:
    """Calculate total kinetic energy (translational + rotational) of the aircraft."""
    m = float(sim.aircraft.mass_kg)
    v = np.asarray(state.velocity_mps, dtype=np.float64)
    w = np.asarray(state.angular_velocity_radps, dtype=np.float64)
    e_trans = 0.5 * m * float(np.dot(v, v))
    e_rot = 0.5 * float(w @ sim._inertia @ w)
    return e_trans + e_rot


def test_wingtip_strike_energy_conservation():
    """Ensure that a wingtip strike does not generate energy out of nowhere (E_post <= E_pre).

    In the unfixed implementation, an asymmetric wingtip contact applies fixed
    angular velocity increments (e.g. +2.0 rad/s roll, -1.5 rad/s yaw) regardless
    of impact kinetic energy, causing E_post > E_pre for lightweight/high-inertia
    aircraft at low vertical impact speed (e.g. vz = 0.5 m/s).
    """
    cfg = AircraftConfig(
        mass_kg=0.001,
        roll_inertia_kgm2=5.0e-4,
        yaw_inertia_kgm2=5.0e-4,
    )
    settings = SimulationSettings(restitution=0.8)
    sim = FlightSimulator(cfg, EnvironmentConfig(ground_level_m=0.0), settings=settings)

    # Roll aircraft by 30 deg so right wingtip strikes ground before CG or other stations
    q = quaternion_from_euler(30.0, -5.0, 0.0)
    z_cg = -0.04
    stations = compute_ground_stations((0.0, 0.0, z_cg), tuple(q), sim.environment, sim._derived)
    assert stations["altitude_cg"] > 0.0, "CG must be above ground"
    assert stations["alt_rtip"] <= 0.0, "Right wingtip must be in ground contact"
    assert stations["alt_ltip"] > 0.0, "Left wingtip must be in air"

    vz = 0.5
    state = StateVector(
        time_s=0.0,
        position_m=(0.0, 0.0, z_cg),
        velocity_mps=(0.0, 0.0, vz),
        quaternion=tuple(q),
        angular_velocity_radps=(0.0, 0.0, 0.0),
        acceleration_mps2=(0.0, 0.0, 0.0),
        angular_accel_radps2=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )

    e_pre = _total_kinetic_energy(sim, state)
    assert e_pre > 0.0

    next_state = sim._apply_ground_contact(state, None)
    e_post = _total_kinetic_energy(sim, next_state)

    # Physical law: Inelastic collision cannot create energy. E_post <= E_pre must hold.
    assert e_post <= e_pre + 1.0e-12, (
        f"Energy non-conservation detected on wingtip strike: "
        f"E_post ({e_post:.6e} J) > E_pre ({e_pre:.6e} J), ratio={e_post / e_pre:.2f}"
    )


def test_wingtip_strike_physical_dissipation_bound():
    """Ensure energy after wingtip collision does not exceed the physical dissipation limit:
    E_post <= E_pre * max(restitution^2, 0.5^2).
    """
    cfg = AircraftConfig(
        mass_kg=0.003,
        roll_inertia_kgm2=2.0e-4,
        yaw_inertia_kgm2=2.0e-4,
    )
    restitution = 0.4
    settings = SimulationSettings(restitution=restitution)
    sim = FlightSimulator(cfg, EnvironmentConfig(ground_level_m=0.0), settings=settings)

    q = quaternion_from_euler(30.0, -5.0, 0.0)
    z_cg = -0.04
    state = StateVector(
        time_s=0.0,
        position_m=(0.0, 0.0, z_cg),
        velocity_mps=(0.0, 0.0, 0.5),
        quaternion=tuple(q),
        angular_velocity_radps=(0.0, 0.0, 0.0),
        acceleration_mps2=(0.0, 0.0, 0.0),
        angular_accel_radps2=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )

    e_pre = _total_kinetic_energy(sim, state)
    next_state = sim._apply_ground_contact(state, None)
    e_post = _total_kinetic_energy(sim, next_state)

    allowed_ratio = max(restitution**2, 0.5**2)
    assert e_post <= e_pre * allowed_ratio + 1.0e-12, (
        f"Collision dissipation violated: E_post={e_post:.6e} J exceeded allowed limit "
        f"{e_pre * allowed_ratio:.6e} J (ratio={e_post / e_pre:.2f}, max_allowed={allowed_ratio:.2f})"
    )


def test_no_double_rebound_on_wingtip_strike():
    """Ensure that wingtip strike rebound speed is not overwritten by main body rebound.

    The contact is resolved as an impulse applied at the *contact point*, so the
    restitution law applies to the velocity of that point and not to the centre
    of mass.  A wingtip strike is therefore mostly a rotation (the airframe
    pivots about the tip) and the centre of mass keeps descending.  The main
    body rebound must not be applied on top of it: the body branch reverses the
    centre-of-mass velocity to ``-restitution * vz``, which is a different and
    much larger result.
    """
    restitution = 0.8
    settings = SimulationSettings(restitution=restitution)
    sim = FlightSimulator(AircraftConfig(), EnvironmentConfig(ground_level_m=0.0), settings=settings)

    q = quaternion_from_euler(30.0, -5.0, 0.0)
    z_cg = -0.04
    vz = 0.6
    state = StateVector(
        time_s=0.0,
        position_m=(0.0, 0.0, z_cg),
        velocity_mps=(0.0, 0.0, vz),
        quaternion=tuple(q),
        angular_velocity_radps=(0.0, 0.0, 0.0),
        acceleration_mps2=(0.0, 0.0, 0.0),
        angular_accel_radps2=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )

    next_state = sim._apply_ground_contact(state, None)

    # The restitution law holds at the contact point (the right wingtip).
    mac = float(sim._derived["mac_m"])
    semispan = float(sim._derived["wingspan_m"]) * 0.5
    r_body = np.array([-0.3 * mac, semispan, 0.0])
    r_world = body_to_world(q, r_body)
    R_b2w = quaternion_to_matrix(q)
    world_omega_pre = R_b2w @ np.asarray(state.angular_velocity_radps)
    v_point_pre = np.asarray(state.velocity_mps) + np.cross(world_omega_pre, r_world)
    v_pre_normal = float(v_point_pre[2])
    world_omega_post = R_b2w @ np.asarray(next_state.angular_velocity_radps)
    v_point_post = np.asarray(next_state.velocity_mps) + np.cross(world_omega_post, r_world)
    v_post_normal = float(v_point_post[2])
    assert v_post_normal == pytest.approx(-restitution * v_pre_normal, rel=1e-6, abs=1e-9), (
        f"Restitution law violated at the contact point: "
        f"v_post={v_point_post[2]} vs -e*v_pre={-restitution * v_point_pre[2]}"
    )

    # The main body rebound must NOT have been applied on top of the tip
    # impulse: it would have reversed the centre-of-mass velocity exactly to
    # -restitution * vz.
    assert next_state.velocity_mps[2] != pytest.approx(-restitution * vz, rel=1e-6), (
        "Main body rebound overwrote the wingtip strike result."
    )


def test_rotating_contact_zero_cg_velocity_detects_impact():
    """重心速度ゼロでも回転による接触点速度が非ゼロなら接触を検出する（Issue #96）。

    機首を下げて回転させ、重心の鉛直速度はゼロのまま機首接触点が地面に
    入り込む状態を作る。接触点速度が非ゼロならインパルスが適用され、
    接触後の接触点法線速度は restitution 係数で反転する。
    """
    restitution = 0.7
    settings = SimulationSettings(restitution=restitution)
    sim = FlightSimulator(AircraftConfig(), EnvironmentConfig(ground_level_m=0.0), settings=settings)

    # 機首を下げて回転させ、重心の鉛直速度はゼロのまま機首接触点が地面に
    # 入り込む状態を作る。接触点速度が非ゼロならインパルスが適用され、
    # 接触後の接触点法線速度は反転する（摩擦ありのため係数厳密一致は見做さず符号反転を確認）。
    q = quaternion_from_euler(0.0, -30.0, 0.0)  # ピッチ下げ：鼻先が下向き
    z_cg = -0.005  # 重心は地面ぎりぎり上（鼻先が食い込む）
    state = StateVector(
        time_s=0.0,
        position_m=(0.0, 0.0, z_cg),
        velocity_mps=(0.0, 0.0, 0.0),  # 重心速度ゼロ
        quaternion=tuple(q),
        angular_velocity_radps=(0.0, -30.0, 0.0),  # ピッチ回転のみ（機首を下向きへ）
        acceleration_mps2=(0.0, 0.0, 0.0),
        angular_accel_radps2=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )

    # 接触前：機首点の法線速度が非ゼロであること（回転由来、重心速度ゼロ）
    mac = float(sim._derived["mac_m"])
    r_nose = np.array([0.5 * mac, 0.0, 0.0], dtype=np.float64)
    r_world = body_to_world(q, r_nose)
    R_b2w = quaternion_to_matrix(q)
    world_omega_pre = R_b2w @ np.asarray(state.angular_velocity_radps)
    v_nose_pre = np.asarray(state.velocity_mps) + np.cross(world_omega_pre, r_world)
    v_nose_pre_normal = float(v_nose_pre[2])
    assert v_nose_pre_normal > 0.0, (
        f"機首接触点の降下速度が非ゼロであるべき: v={v_nose_pre_normal}"
    )

    next_state = sim._apply_ground_contact(state, None)

    # 接触後：機首点の法線速度は反転（上向き）
    world_omega_post = R_b2w @ np.asarray(next_state.angular_velocity_radps)
    v_nose_post = np.asarray(next_state.velocity_mps) + np.cross(world_omega_post, r_world)
    v_nose_post_normal = float(v_nose_post[2])
    assert v_nose_post_normal < 0.0, (
        f"機首接触点の速度が反転すべき: v_post={v_nose_post_normal}"
    )


def test_rotating_wingtip_zero_cg_velocity_detects_impact():
    """重心速度ゼロ・横転による翼端接地で接触を検出する（Issue #96）。

    機体を横転させ、重心速度はゼロのまま右翼端が地面に食い込む状態を作る。
    回転由来で翼端接触点の法線速度が非ゼロならインパルスが適用され、
    接触後速度が反転する。
    """
    settings = SimulationSettings(restitution=0.7)
    sim = FlightSimulator(AircraftConfig(), EnvironmentConfig(ground_level_m=0.0), settings=settings)

    # 右翼を下げる横転（+roll で右翼端が下向きに移動）
    q = quaternion_from_euler(20.0, 0.0, 0.0)
    z_cg = -0.005
    state = StateVector(
        time_s=0.0,
        position_m=(0.0, 0.0, z_cg),
        velocity_mps=(0.0, 0.0, 0.0),  # 重心速度ゼロ
        quaternion=tuple(q),
        angular_velocity_radps=(25.0, 0.0, 0.0),  # 右翼をさらに下げる横転
        acceleration_mps2=(0.0, 0.0, 0.0),
        angular_accel_radps2=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )

    stations = compute_ground_stations((0.0, 0.0, z_cg), tuple(q), sim.environment, sim._derived)
    assert stations["alt_rtip"] <= 0.0, f"右翼端が地面に食い込むべき: alt_rtip={stations['alt_rtip']}"
    assert stations["alt_ltip"] > 0.0, "左翼端は空中であるべき"

    mac = float(sim._derived["mac_m"])
    semispan = float(sim._derived["wingspan_m"]) * 0.5
    r_rtip = np.array([-0.3 * mac, semispan, 0.0], dtype=np.float64)
    r_world = body_to_world(q, r_rtip)
    R_b2w = quaternion_to_matrix(q)
    world_omega_pre = R_b2w @ np.asarray(state.angular_velocity_radps)
    v_rtip_pre = np.asarray(state.velocity_mps) + np.cross(world_omega_pre, r_world)
    v_rtip_pre_normal = float(v_rtip_pre[2])
    assert v_rtip_pre_normal > 0.0, (
        f"右翼端接触点の降下速度が非ゼロであるべき: v={v_rtip_pre_normal}"
    )

    next_state = sim._apply_ground_contact(state, None)

    world_omega_post = R_b2w @ np.asarray(next_state.angular_velocity_radps)
    v_rtip_post = np.asarray(next_state.velocity_mps) + np.cross(world_omega_post, r_world)
    v_rtip_post_normal = float(v_rtip_post[2])
    assert v_rtip_post_normal < 0.0, (
        f"右翼端接触点の速度が反転すべき: v_post={v_rtip_post_normal}"
    )


def test_rotating_tail_zero_cg_velocity_detects_impact():
    """重心速度ゼロ・ピッチ上げによる尾部接地で接触を検出する（Issue #96）。

    機首を上げて重心速度ゼロのまま尾部が地面に食い込む状態を作る。
    回転由来で尾部接触点の法線速度が非ゼロならインパルスが適用され、
    接触後速度が反転する。
    """
    settings = SimulationSettings(restitution=0.7)
    sim = FlightSimulator(AircraftConfig(), EnvironmentConfig(ground_level_m=0.0), settings=settings)

    # 機首を上げて尾部を下向きに
    q = quaternion_from_euler(0.0, 20.0, 0.0)
    z_cg = -0.005
    state = StateVector(
        time_s=0.0,
        position_m=(0.0, 0.0, z_cg),
        velocity_mps=(0.0, 0.0, 0.0),  # 重心速度ゼロ
        quaternion=tuple(q),
        angular_velocity_radps=(0.0, 25.0, 0.0),  # 尾部をさらに下げるピッチ
        acceleration_mps2=(0.0, 0.0, 0.0),
        angular_accel_radps2=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )

    stations = compute_ground_stations((0.0, 0.0, z_cg), tuple(q), sim.environment, sim._derived)
    assert stations["alt_tail"] <= 0.0, f"尾部が地面に食い込むべき: alt_tail={stations['alt_tail']}"
    assert stations["alt_nose"] > 0.0, "鼻先は空中であるべき"

    mac = float(sim._derived["mac_m"])
    r_tail = np.array([-0.7 * mac, 0.0, GROUND_KEEL_DEPTH_M], dtype=np.float64)
    r_world = body_to_world(q, r_tail)
    R_b2w = quaternion_to_matrix(q)
    world_omega_pre = R_b2w @ np.asarray(state.angular_velocity_radps)
    v_tail_pre = np.asarray(state.velocity_mps) + np.cross(world_omega_pre, r_world)
    v_tail_pre_normal = float(v_tail_pre[2])
    assert v_tail_pre_normal > 0.0, (
        f"尾部接触点の降下速度が非ゼロであるべき: v={v_tail_pre_normal}"
    )

    next_state = sim._apply_ground_contact(state, None)

    world_omega_post = R_b2w @ np.asarray(next_state.angular_velocity_radps)
    v_tail_post = np.asarray(next_state.velocity_mps) + np.cross(world_omega_post, r_world)
    v_tail_post_normal = float(v_tail_post[2])
    assert v_tail_post_normal < 0.0, (
        f"尾部接触点の速度が反転すべき: v_post={v_tail_post_normal}"
    )
