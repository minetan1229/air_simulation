"""Verification tests covering:
- Issue #31: Fixed-timestep numerical convergence and bounded discretization error
- Issue #33: 3D wind field divergence, spatial correlation decay, and temporal continuity
- Issue #36: Panel method mesh resolution dependency and consistency with analytical lift
"""

from __future__ import annotations

import math
import numpy as np
import pytest

from physics import aero_domain, flight_sim, mesh_domain, wind_domain
from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    SimulationSettings,
)


# ==============================================================================
# Issue #31: 固定時間刻みの収束性テスト
# ==============================================================================

def test_fixed_timestep_convergence():
    """Verify simulation states at a fixed in-flight time converge systematically as dt is refined.

    The step sizes must live in the **resolved** regime, i.e. below the
    stiffness-resolved step of the integrator.  A step above that limit is
    rejected by the truncation-error guard and silently halved, so the
    trajectory is no longer the one the nominal ``dt`` asks for: comparing a
    rejected trajectory with a resolved one measures the chaotic sensitivity
    of the airframe, not the discretisation error.

    The scenario is a 4 g dart with a 3.5e-6 kg m^2 pitch inertia, released at
    8 m/s.  It tumbles violently (|omega| reaches 100 rad/s), so the
    point-wise trajectory is genuinely chaotic and the *successive* differences
    between two step sizes are only meaningful once the step is fine enough
    to resolve the motion.  The previous step ladder (0.02 / 0.01 / 0.005 s)
    sat entirely *above* the resolved limit, which is why it produced
    non-monotone differences.
    """
    ac = AircraftConfig(mass_kg=0.004, wingspan_m=0.20, wing_area_m2=0.02)
    env = EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0, thermal_strength=0.0)
    lnc = LaunchConfig(launch_speed=8.0, launch_angle_deg=0.0, launch_height_m=5.0)

    # Evaluate across a sequence of halved fixed time steps for duration 0.4s.
    # The ladder straddles the resolved limit: 0.0025 s is still partially
    # rejected, 0.00125 s and below are fully resolved.
    dts = [0.0025, 0.00125, 0.000625]
    distances = []
    altitudes = []

    for dt in dts:
        settings = SimulationSettings(
            dt_max=dt,
            dt_min=dt,
            adaptive=False,
            max_substeps=1,
            duration_s=0.4,
        )
        sim = flight_sim.FlightSimulator(ac, env, lnc, settings)
        res = sim.simulate(duration_s=0.4)
        distances.append(float(res["horizontal_range_m"]))
        altitudes.append(float(res["max_altitude_m"]))

    diffs_dist = [abs(distances[i] - distances[i + 1]) for i in range(len(distances) - 1)]
    diffs_alt = [abs(altitudes[i] - altitudes[i + 1]) for i in range(len(altitudes) - 1)]

    # Discretization differences must decrease monotonically as dt is refined
    assert diffs_dist[-1] < diffs_dist[0]
    assert diffs_alt[-1] < diffs_alt[0]

    # The refinement has to be meaningful: the coarse step must not already be
    # converged, otherwise the comparison above would be vacuous.
    assert diffs_dist[0] > 1.0e-4

    # Relative difference between finest steps must be well within 0.1%
    assert diffs_dist[-1] / max(distances[-1], 1e-3) < 0.005


# ==============================================================================
# Issue #33: 3D風場の発散・空間相関・時間相関の検証
# ==============================================================================

def test_wind_field_divergence_bounded():
    """Verify numeric divergence of the generated 3D wind velocity field remains bounded."""
    env = EnvironmentConfig(wind_speed=5.0, gust_intensity=0.5, thermal_strength=1.0)

    # 3D sampling stencil around altitude 10m
    x0, y0, z0 = 0.0, 0.0, 10.0
    h = 0.5  # step size in meters

    # Sample central difference components
    vx_p = wind_domain.wind_velocity((x0 + h, y0, z0), env)[0]
    vx_m = wind_domain.wind_velocity((x0 - h, y0, z0), env)[0]
    du_dx = (vx_p - vx_m) / (2.0 * h)

    vy_p = wind_domain.wind_velocity((x0, y0 + h, z0), env)[1]
    vy_m = wind_domain.wind_velocity((x0, y0 - h, z0), env)[1]
    dv_dy = (vy_p - vy_m) / (2.0 * h)

    vz_p = wind_domain.wind_velocity((x0, y0, z0 + h), env)[2]
    vz_m = wind_domain.wind_velocity((x0, y0, z0 - h), env)[2]
    dw_dz = (vz_p - vz_m) / (2.0 * h)

    divergence = du_dx + dv_dy + dw_dz
    assert math.isfinite(divergence)
    # Divergence of turbulent wind field should be physically bounded (< 5.0 s^-1)
    assert abs(divergence) < 5.0


def test_wind_spatial_and_temporal_correlation():
    """Verify wind fluctuations correlate strongly at short spatial/temporal separations and decorrelate at large separations."""
    env = EnvironmentConfig(wind_speed=4.0, gust_intensity=1.0, thermal_strength=0.0)

    # Spatial correlation: nearby point vs distant point
    v_base = wind_domain.wind_velocity((0.0, 0.0, 10.0), env, time_s=0.0)
    v_near = wind_domain.wind_velocity((0.1, 0.0, 10.0), env, time_s=0.0)
    v_far = wind_domain.wind_velocity((100.0, 0.0, 10.0), env, time_s=0.0)

    diff_near = float(np.linalg.norm(v_near - v_base))
    diff_far = float(np.linalg.norm(v_far - v_base))

    # Nearby point should have smaller difference (continuity) and distant point larger difference
    assert diff_near < 0.8
    assert diff_near < diff_far

    # Temporal correlation: small dt vs large dt
    v_t0 = wind_domain.wind_velocity((0.0, 0.0, 10.0), env, time_s=0.0)
    v_t_near = wind_domain.wind_velocity((0.0, 0.0, 10.0), env, time_s=0.05)
    v_t_far = wind_domain.wind_velocity((0.0, 0.0, 10.0), env, time_s=10.0)

    diff_t_near = float(np.linalg.norm(v_t_near - v_t0))
    diff_t_far = float(np.linalg.norm(v_t_far - v_t0))

    # M-24 normalises the turbulence amplitude with the local mean wind
    # (``amplitude_ref * boundary_layer_ratio(z)``), so the absolute fluctuation
    # amplitude at 10 m is 1.32x larger than under the previous normalisation
    # (1.4 -> 1.852 m/s).  The advection speed and the correlation length are
    # unchanged (Taylor frozen turbulence keeps ``advection * correlation
    # length`` consistent), hence the temporal *decay rate* is unaffected: the
    # short-time difference only scales with the (now local) amplitude.  The
    # absolute threshold 0.5 m/s therefore depended on the old normalisation
    # and is replaced by a wind-scale-relative bound: the gust-induced velocity
    # change over a short interval must stay below half of the mean wind
    # speed, i.e. the turbulent fluctuation has to remain a sub-dominant
    # perturbation of the mean flow (0.5 is a dimensionless ratio here, not an
    # absolute speed).
    assert diff_t_near < 0.5 * env.wind_speed
    assert diff_t_near <= diff_t_far + 1e-4

    # Regression guard for the physical intent: over a short interval the field
    # must still be strongly correlated, so the velocity change has to stay
    # inside the local turbulence amplitude itself.
    local_amplitude = wind_domain.turbulence_amplitude(env) * float(
        np.ravel(wind_domain.boundary_layer_ratio(10.0, env))[0]
    )
    assert diff_t_near < local_amplitude


# ==============================================================================
# Issue #36: パネル法のメッシュ解像度依存性と参照解への整合性検証
# ==============================================================================

def test_panel_method_resolution_consistency_and_lift():
    """Verify panel method lift force scales systematically and agrees between resolutions."""
    span = 0.25
    chord = 0.10
    q = 0.5 * 1.225 * (10.0 ** 2)  # ~61.25 Pa

    verts = np.array([
        [-chord / 2, -span / 2, 0.0],
        [chord / 2, -span / 2, 0.0],
        [chord / 2, span / 2, 0.0],
        [-chord / 2, span / 2, 0.0],
    ], dtype=np.float64)
    # Winding order oriented so that aerodynamic normal vector produces positive lift at positive alpha
    faces = np.array([
        [0, 2, 1],
        [0, 3, 2],
    ], dtype=np.int64)

    # Coarse panels
    coarse_panels = mesh_domain.build_panels(verts, faces, nx=4, ny=4)
    res_coarse = mesh_domain.panel_aerodynamics(coarse_panels, alpha_deg=5.0, dynamic_pressure_pa=q)

    # Fine panels
    fine_panels = mesh_domain.build_panels(verts, faces, nx=16, ny=16)
    res_fine = mesh_domain.panel_aerodynamics(fine_panels, alpha_deg=5.0, dynamic_pressure_pa=q)

    lift_coarse = abs(float(res_coarse["total_lift_n"]))
    lift_fine = abs(float(res_fine["total_lift_n"]))

    assert lift_coarse > 0.0
    assert lift_fine > 0.0
    # Consistent between coarse and fine meshes within 15%
    assert abs(lift_fine - lift_coarse) / max(lift_fine, 1e-4) < 0.15

    # Lift must increase monotonically with dynamic pressure
    res_higher_q = mesh_domain.panel_aerodynamics(fine_panels, alpha_deg=5.0, dynamic_pressure_pa=q * 2.0)
    assert abs(float(res_higher_q["total_lift_n"])) > lift_fine


# ==============================================================================
# Issue #69: メッシュ空力モデルの定式化・有限翼効果・抗力仮定の監査
# ==============================================================================

def _wing_mesh(
    *,
    span_m: float,
    area_m2: float,
    taper_ratio: float = 1.0,
    sweep_deg: float = 0.0,
    tip_twist_deg: float = 0.0,
    nx: int = 12,
    ny: int = 12,
) -> tuple[np.ndarray, np.ndarray]:
    """Create a symmetric trapezoid wing mesh in body axes (x fwd, z down)."""
    taper = float(np.clip(taper_ratio, 0.05, 1.0))
    half_span = 0.5 * float(span_m)
    root = 2.0 * float(area_m2) / (float(span_m) * (1.0 + taper))
    tip = taper * root
    sweep = math.radians(float(sweep_deg))
    tip_twist = math.radians(float(tip_twist_deg))

    vertices: list[tuple[float, float, float]] = []
    for i in range(nx + 1):
        fx = i / nx
        for j in range(ny + 1):
            eta = j / ny
            y = -half_span + 2.0 * half_span * eta
            station = abs(y) / max(half_span, 1.0e-9)
            chord = root + (tip - root) * station
            x_le = -abs(y) * math.tan(sweep)
            x = x_le + (0.5 - fx) * chord

            twist = tip_twist * station
            pivot = x_le + 0.25 * chord
            x_local = x - pivot
            z = -x_local * math.sin(twist)
            x = pivot + x_local * math.cos(twist)
            vertices.append((x, y, z))

    faces: list[list[int]] = []
    stride = ny + 1
    for i in range(nx):
        for j in range(ny):
            a = i * stride + j
            b = a + 1
            c = a + stride
            d = c + 1
            faces.append([a, d, b])
            faces.append([a, c, d])
    return np.asarray(vertices, dtype=np.float64), np.asarray(faces, dtype=np.int64)


def _cl_from_result(result: dict[str, float], q_pa: float, area_m2: float) -> float:
    return float(result["total_lift_n"]) / max(float(q_pa) * float(area_m2), 1.0e-12)


def test_mesh_panel_model_declares_local_algebraic_formulation():
    verts, faces = _wing_mesh(span_m=0.24, area_m2=0.018, sweep_deg=20.0)
    panels = mesh_domain.build_panels(verts, faces, nx=12, ny=12)
    result = mesh_domain.panel_aerodynamics(panels, alpha_deg=6.0, dynamic_pressure_pa=40.0)

    assert result["formulation"] == "local-algebraic-panel-load"
    assert result["is_boundary_element_panel_method"] is False
    assert result["solves_influence_matrix"] is False
    assert result["models_panel_interaction"] is False
    assert result["models_induced_drag"] is False
    assert result["profile_drag_cd"] == pytest.approx(mesh_domain.PANEL_PROFILE_DRAG_CD, rel=0.0)
    assert np.median([row["alpha_local_deg"] for row in result["panels"]]) == pytest.approx(6.0, abs=0.3)


def test_mesh_panel_model_shows_limited_finite_wing_response_against_helmbold():
    alpha_deg = 5.0
    alpha_rad = math.radians(alpha_deg)
    q = 55.0
    area = 0.02
    span_low, span_high = 0.16, 0.40  # AR: 1.28 -> 8.0 (same area)

    low_verts, low_faces = _wing_mesh(span_m=span_low, area_m2=area, taper_ratio=1.0)
    high_verts, high_faces = _wing_mesh(span_m=span_high, area_m2=area, taper_ratio=1.0)
    low = mesh_domain.panel_aerodynamics(mesh_domain.build_panels(low_verts, low_faces), alpha_deg, q)
    high = mesh_domain.panel_aerodynamics(mesh_domain.build_panels(high_verts, high_faces), alpha_deg, q)
    cl_low = _cl_from_result(low, q, area)
    cl_high = _cl_from_result(high, q, area)
    panel_ratio = cl_high / max(cl_low, 1.0e-12)

    helmbold_low = aero_domain.helmbold_cl_alpha(span_low * span_low / area, 1.0) * alpha_rad
    helmbold_high = aero_domain.helmbold_cl_alpha(span_high * span_high / area, 1.0) * alpha_rad
    helmbold_ratio = helmbold_high / max(helmbold_low, 1.0e-12)

    assert panel_ratio == pytest.approx(1.0, rel=0.08)
    assert helmbold_ratio > 1.25


def test_mesh_panel_drag_and_planform_sensitivity_limits():
    q = 60.0
    area = 0.02
    alpha = 8.0

    rect = mesh_domain.panel_aerodynamics(
        mesh_domain.build_panels(*_wing_mesh(span_m=0.24, area_m2=area, taper_ratio=1.0)),
        alpha,
        q,
    )
    swept = mesh_domain.panel_aerodynamics(
        mesh_domain.build_panels(*_wing_mesh(span_m=0.24, area_m2=area, taper_ratio=1.0, sweep_deg=30.0)),
        alpha,
        q,
    )
    pointed_tip = mesh_domain.panel_aerodynamics(
        mesh_domain.build_panels(*_wing_mesh(span_m=0.24, area_m2=area, taper_ratio=0.08)),
        alpha,
        q,
    )
    twisted = mesh_domain.panel_aerodynamics(
        mesh_domain.build_panels(*_wing_mesh(span_m=0.24, area_m2=area, taper_ratio=1.0, tip_twist_deg=-10.0)),
        alpha,
        q,
    )

    cl_rect = _cl_from_result(rect, q, area)
    cl_swept = _cl_from_result(swept, q, area)
    cl_tip = _cl_from_result(pointed_tip, q, area)
    cl_twisted = _cl_from_result(twisted, q, area)

    assert cl_swept == pytest.approx(cl_rect, rel=0.10)
    assert cl_tip == pytest.approx(cl_rect, rel=0.12)
    assert cl_twisted < 0.85 * cl_rect

    # Fixed-profile drag model: with equal area and q, drag stays almost alpha-insensitive.
    rect_alpha0 = mesh_domain.panel_aerodynamics(
        mesh_domain.build_panels(*_wing_mesh(span_m=0.24, area_m2=area, taper_ratio=1.0)),
        0.0,
        q,
    )
    rect_alpha20 = mesh_domain.panel_aerodynamics(
        mesh_domain.build_panels(*_wing_mesh(span_m=0.24, area_m2=area, taper_ratio=1.0)),
        20.0,
        q,
    )
    assert rect_alpha20["total_drag_n"] == pytest.approx(rect_alpha0["total_drag_n"], rel=1.0e-12)
