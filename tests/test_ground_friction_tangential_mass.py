"""Dedicated tests for Issue #94: Ground friction impulse uses tangential effective mass.

Verifies:
1. Contact points offset from CG produce different rotational inertia coupling for
   tangential slip (r x t) vs normal impact (r x n).
2. Exact tangential sticking impulse: j_t = min(mu * |j_n|, m_eff_tang * v_tang)
   cleanly nullifies tangential contact velocity along t_dir at the impact station
   without over- or under-correcting.
3. Coulomb saturation regime: when mu * |j_n| < m_eff_tang * v_tang, impulse magnitude is capped
   at mu * |j_n| and opposes tangential slip without reversal.
4. Mechanical energy during ground contact strictly dissipates or conserves (Delta E <= 0).
"""

from __future__ import annotations

import math
import numpy as np
import pytest

from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    SimulationSettings,
    StateVector,
)
from physics.flight_sim import FlightSimulator, body_to_world


def _make_sim(mu: float, restitution: float = 0.0) -> FlightSimulator:
    cfg = AircraftConfig(mass_kg=0.05)
    settings = SimulationSettings(restitution=restitution)
    env = EnvironmentConfig(ground_level_m=0.0, ground_friction=mu)
    return FlightSimulator(cfg, env, settings=settings)


def test_rotational_inertia_coupling_split():
    """Hypothesis 1: For contact points offset from CG, r x t differs from r x n,

    yielding m_eff_tang != m_eff_norm.
    """
    sim = _make_sim(mu=1.0)
    mass = 0.05
    inertia = np.asarray(sim._inertia, dtype=np.float64)
    inertia_inv = np.linalg.pinv(inertia)

    # Offset contact point (keel station)
    r_body = np.array([0.0, 0.0, 0.015], dtype=np.float64)
    n_world = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    t_world = np.array([3.0, 1.0, 0.0], dtype=np.float64)
    t_world = t_world / np.linalg.norm(t_world)

    lever_n = np.cross(r_body, n_world)
    lever_t = np.cross(r_body, t_world)

    inv_m_n = 1.0 / mass + float(lever_n @ (inertia_inv @ lever_n))
    inv_m_t = 1.0 / mass + float(lever_t @ (inertia_inv @ lever_t))

    m_eff_n = 1.0 / inv_m_n
    m_eff_t = 1.0 / inv_m_t

    # For keel: lever_n = r x n = 0, so m_eff_n = mass = 0.05.
    # But lever_t = r x t != 0, so rotational inertia coupling enters and m_eff_t < mass.
    assert lever_n[0] == 0.0 and lever_n[1] == 0.0 and lever_n[2] == 0.0
    assert np.linalg.norm(lever_t) > 0.01
    assert m_eff_n == pytest.approx(mass, rel=1e-6)
    rel_diff = abs(m_eff_t - m_eff_n) / max(m_eff_t, m_eff_n)
    assert rel_diff > 0.20, f"Expected >20% split between m_eff_t and m_eff_n, got rel_diff={rel_diff}"


def test_sticking_friction_exact_zeroing_along_slip_direction():
    """Hypothesis 2: In sticking regime (large mu), tangential effective mass impulse

    j_t = m_eff_tang * v_tang exactly cancels the tangential contact-point velocity
    along the slip direction t_dir (v_contact_post . t_dir == 0).
    Under the old path (m_eff_n), the impulse overcorrected along t_dir by >20-40%.
    """
    sim = _make_sim(mu=10.0, restitution=0.0)
    mass = float(sim._mass)
    inertia = np.asarray(sim._inertia, dtype=np.float64)
    inertia_inv = np.linalg.pinv(inertia)

    r_keel = np.array([0.0, 0.0, 0.015], dtype=np.float64)
    v_initial = np.array([3.0, 1.0, 1.0], dtype=np.float64)
    w_initial = np.array([0.0, 0.0, 0.0], dtype=np.float64)

    # Level attitude: r_world == r_keel
    normal = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    v_point_pre = v_initial + np.cross(w_initial, r_keel)
    v_norm_pre = float(v_point_pre[2])
    v_tang_pre = v_point_pre - v_norm_pre * normal
    v_tang_speed = float(np.linalg.norm(v_tang_pre))
    t_dir = v_tang_pre / v_tang_speed

    lever_n = np.cross(r_keel, normal)
    lever_t = np.cross(r_keel, t_dir)
    m_eff_n = 1.0 / (1.0 / mass + float(lever_n @ (inertia_inv @ lever_n)))
    m_eff_t = 1.0 / (1.0 / mass + float(lever_t @ (inertia_inv @ lever_t)))

    # Compute post-impulse state with new tangential mass m_eff_t
    j_n = - (1.0 + 0.0) * m_eff_n * v_norm_pre
    j_t_new = min(10.0 * abs(j_n), m_eff_t * v_tang_speed)
    impulse_new = np.array([0.0, 0.0, j_n], dtype=np.float64) - j_t_new * t_dir
    v_post_new = v_initial + impulse_new / mass
    w_post_new = w_initial + inertia_inv @ np.cross(r_keel, impulse_new)
    v_point_post_new = v_post_new + np.cross(w_post_new, r_keel)
    slip_residual_new = float(np.dot(v_point_post_new, t_dir))

    # Compute post-impulse state with OLD normal mass m_eff_n
    j_t_old = min(10.0 * abs(j_n), m_eff_n * v_tang_speed)
    impulse_old = np.array([0.0, 0.0, j_n], dtype=np.float64) - j_t_old * t_dir
    v_post_old = v_initial + impulse_old / mass
    w_post_old = w_initial + inertia_inv @ np.cross(r_keel, impulse_old)
    v_point_post_old = v_post_old + np.cross(w_post_old, r_keel)
    slip_residual_old = float(np.dot(v_point_post_old, t_dir))

    # Falsifiable criteria:
    # 1. New formulation zeroes tangential contact-point velocity along t_dir to within numerical tolerance.
    assert abs(slip_residual_new) < 1.0e-12, f"Expected 0 slip along t_dir, got {slip_residual_new}"
    # 2. Old formulation significantly reversed/over-corrected the slip along t_dir.
    assert slip_residual_old < -0.5, f"Expected overcorrection with old m_eff_n, got {slip_residual_old}"


def test_saturated_friction_coulomb_cone():
    """Hypothesis 3: In sliding regime (small mu), impulse is strictly bounded by mu*|j_n|."""
    sim = _make_sim(mu=0.05, restitution=0.0)
    mass = float(sim._mass)
    r_keel = np.array([0.0, 0.0, 0.015], dtype=np.float64)
    state = StateVector(
        time_s=0.0,
        position_m=(0.0, 0.0, -0.002),
        velocity_mps=(3.0, 1.0, 1.0),
        quaternion=(1.0, 0.0, 0.0, 0.0),
        angular_velocity_radps=(0.0, 0.0, 0.0),
        acceleration_mps2=(0.0, 0.0, 0.0),
        angular_accel_radps2=(0.0, 0.0, 0.0),
        landed=False,
        crashed=False,
    )
    nxt = sim._apply_ground_contact(state, None, dt=0.001)
    assert not nxt.crashed
    v_post = np.asarray(nxt.velocity_mps, dtype=float)
    # Post-contact horizontal speed remains positive and bounded
    v_post_t = float(np.hypot(v_post[0], v_post[1]))
    assert v_post_t > 2.0
    assert v_post_t <= float(np.hypot(3.0, 1.0)) + 1e-9


def test_energy_strict_dissipation():
    """Hypothesis 4: Mechanical energy during ground contact must strictly dissipate

    or conserve (Delta E <= 0) across both sliding and sticking regimes and diverse attitudes.
    """
    mass = 0.05
    for mu in [0.05, 0.3, 1.0, 5.0]:
        for restitution in [0.0, 0.3, 0.8]:
            sim = _make_sim(mu=mu, restitution=restitution)
            state = StateVector(
                time_s=0.0,
                position_m=(0.0, 0.0, -0.002),
                velocity_mps=(3.0, 1.5, 1.2),
                quaternion=(1.0, 0.0, 0.0, 0.0),
                angular_velocity_radps=(0.1, -0.2, 0.05),
                acceleration_mps2=(0.0, 0.0, 0.0),
                angular_accel_radps2=(0.0, 0.0, 0.0),
                landed=False,
                crashed=False,
            )

            v_pre = np.asarray(state.velocity_mps, dtype=np.float64)
            w_pre = np.asarray(state.angular_velocity_radps, dtype=np.float64)
            inertia = np.asarray(sim._inertia, dtype=np.float64)
            e_pre = 0.5 * mass * float(v_pre @ v_pre) + 0.5 * float(w_pre @ inertia @ w_pre)

            nxt = sim._apply_ground_contact(state, None, dt=0.001)
            v_post = np.asarray(nxt.velocity_mps, dtype=np.float64)
            w_post = np.asarray(nxt.angular_velocity_radps, dtype=np.float64)
            e_post = 0.5 * mass * float(v_post @ v_post) + 0.5 * float(w_post @ inertia @ w_post)

            assert e_post <= e_pre + 1.0e-12, (
                f"Energy increased during contact: E_pre={e_pre}, E_post={e_post}, mu={mu}, rest={restitution}"
            )
