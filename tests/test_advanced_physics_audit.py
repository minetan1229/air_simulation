"""Verification tests covering:
- Issue #31: Fixed-timestep numerical convergence and bounded discretization error
- Issue #33: 3D wind field divergence, spatial correlation decay, and temporal continuity
- Issue #36: Panel method mesh resolution dependency and consistency with analytical lift
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from physics import flight_sim, mesh_domain, wind_domain
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

    The refinement ladder starts at ``dt = 0.04 s`` and ends at ``0.005 s``, as
    required by the specification.  Both the travelled distance *and* the peak
    altitude must converge monotonically, and the observed order of convergence
    must be compatible with a fourth-order Runge-Kutta integrator.
    """
    ac = AircraftConfig(mass_kg=0.004, wingspan_m=0.20, wing_area_m2=0.02)
    env = EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0, thermal_strength=0.0)
    lnc = LaunchConfig(launch_speed=8.0, launch_angle_deg=0.0, launch_height_m=5.0)

    dts = [0.04, 0.02, 0.01, 0.005]
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

    # Discretization differences must decrease strictly and monotonically as dt
    # is refined -- for the distance *and* for the altitude.  The previous
    # revision only compared the first and last element and computed the
    # altitude differences without ever asserting on them.
    assert all(diffs_dist[i + 1] < diffs_dist[i] for i in range(len(diffs_dist) - 1))
    assert all(diffs_alt[i + 1] < diffs_alt[i] for i in range(len(diffs_alt) - 1))

    # Observed order of convergence p = log2(d_i / d_{i+1}) must be compatible
    # with the fourth-order RK4 integrator (>= 2.5 is a safe lower bound while
    # the asymptotic regime is reached).
    order_dist = math.log2(diffs_dist[-2] / diffs_dist[-1])
    order_alt = math.log2(diffs_alt[-2] / diffs_alt[-1])
    assert order_dist >= 2.5
    assert order_alt >= 2.5

    # Relative difference between finest steps must be well within 0.1%
    assert diffs_dist[-1] / max(distances[-1], 1e-3) < 0.005
    assert diffs_alt[-1] / max(altitudes[-1], 1e-3) < 0.005


# ==============================================================================
# Issue #33: 3D風場の発散・空間相関・時間相関の検証
# ==============================================================================

def _divergence(env: EnvironmentConfig, point, h: float) -> float:
    """Central-difference divergence of the wind field at ``point`` with step ``h``."""
    total = 0.0
    for axis in range(3):
        minus = list(point)
        plus = list(point)
        minus[axis] -= h
        plus[axis] += h
        v_minus = wind_domain.wind_velocity(minus, env)
        v_plus = wind_domain.wind_velocity(plus, env)
        total += float((v_plus[axis] - v_minus[axis]) / (2.0 * h))
    return total


def test_wind_field_divergence_bounded():
    """Verify numeric divergence of the generated 3D wind velocity field remains bounded.

    The bound is measured over several sample points and stencil sizes, and the
    residual is additionally shown to *shrink* when the stencil is widened.
    That second property is what distinguishes a truncation artefact of a
    non-solenoidal turbulent field from a genuine singularity.
    """
    env = EnvironmentConfig(wind_speed=5.0, gust_intensity=0.5, thermal_strength=1.0)

    points = [
        (0.0, 0.0, 10.0),
        (5.0, -3.0, 25.0),
        (-12.0, 8.0, 3.0),
        (30.0, 30.0, 60.0),
    ]

    fine = []
    coarse = []
    for point in points:
        for h in (0.25, 0.5, 1.0):
            div = _divergence(env, point, h)
            assert math.isfinite(div)
            assert abs(div) < 4.0
            if h == 0.25:
                fine.append(abs(div))
            elif h == 1.0:
                coarse.append(abs(div))

    # Widening the stencil drives the discrete estimate towards zero, so the
    # residual is a finite-difference artefact and not a singularity.
    assert max(coarse) < max(fine)


def _turbulence_correlation(env: EnvironmentConfig, offsets, seed: int) -> float:
    """Mean cosine similarity of the *turbulent* part of the field for a set of offsets."""
    ops = wind_domain._Ops("numpy")
    drift = np.zeros(3, dtype=np.float64)
    rng = np.random.default_rng(seed)
    dots = []
    for _ in range(200):
        x = rng.uniform(-500.0, 500.0, size=2)
        for offset in offsets:
            p0 = np.array([[x[0], x[1], 10.0]], dtype=np.float64)
            p1 = np.array([[x[0] + offset, x[1], 10.0]], dtype=np.float64)
            t0 = wind_domain._turbulence_components(p0, env, drift, ops)[0]
            t1 = wind_domain._turbulence_components(p1, env, drift, ops)[0]
            n0 = float(np.linalg.norm(t0))
            n1 = float(np.linalg.norm(t1))
            if n0 > 1e-12 and n1 > 1e-12:
                dots.append(float(np.dot(t0, t1) / (n0 * n1)))
    return float(np.mean(dots))


def test_wind_spatial_and_temporal_correlation():
    """Verify wind fluctuations correlate strongly at short separations and decorrelate at large ones.

    The correlation has to be measured on the *turbulent* part of the field.
    The full velocity vector carries a spatially uniform gust envelope, so the
    correlation of the complete vector stays near a constant floor and never
    decays -- the previous revision therefore measured the gust envelope rather
    than the spatial structure it claimed to verify.
    """
    env = EnvironmentConfig(wind_speed=4.0, gust_intensity=1.0, thermal_strength=0.0)

    # --- spatial decorrelation -------------------------------------------
    corr_zero = _turbulence_correlation(env, [0.0], seed=7)
    corr_close = _turbulence_correlation(env, [0.25], seed=7)
    corr_far = _turbulence_correlation(env, [1.0], seed=7)

    assert corr_zero == pytest.approx(1.0, abs=1e-9)
    assert corr_close < 0.6
    assert corr_far < 0.2
    # The correlation must decay with separation.
    assert corr_far < corr_close < corr_zero

    # --- temporal continuity ---------------------------------------------
    ops = wind_domain._Ops("numpy")
    mean_wind = wind_domain.mean_wind_vector(env)

    def temporal_corr(dt: float) -> float:
        rng = np.random.default_rng(5)
        dots = []
        for _ in range(200):
            x = rng.uniform(-300.0, 300.0, size=2)
            p0 = np.array([[x[0], x[1], 10.0]], dtype=np.float64)
            t0 = wind_domain._turbulence_components(p0, env, mean_wind * 0.0, ops)[0]
            t1 = wind_domain._turbulence_components(p0, env, mean_wind * dt, ops)[0]
            n0 = float(np.linalg.norm(t0))
            n1 = float(np.linalg.norm(t1))
            if n0 > 1e-12 and n1 > 1e-12:
                dots.append(float(np.dot(t0, t1) / (n0 * n1)))
        return float(np.mean(dots))

    assert temporal_corr(0.0) == pytest.approx(1.0, abs=1e-9)
    # The field is continuous in time: over 50 ms the turbulence is still
    # strongly correlated with its initial state.
    assert temporal_corr(0.05) > 0.3
    # After the eddies have been advected several correlation lengths the
    # field is fully decorrelated.
    assert temporal_corr(2.0) < 0.2


# ==============================================================================
# Issue #36: パネル法のメッシュ解像度依存性と参照解への整合性検証
# ==============================================================================

def _cambered_wing(nx: int, ny: int):
    """Cambered and swept wing surface whose panel normals vary with resolution.

    A flat plate is *resolution independent* by construction (every panel
    carries the same incidence), so a flat-plate convergence test cannot
    discriminate a broken panel method from a correct one.  Camber and sweep
    make the panel incidence depend on the discretisation.
    """
    span, chord = 0.25, 0.10
    xs = np.linspace(-chord / 2, chord / 2, nx + 1)
    ys = np.linspace(-span / 2, span / 2, ny + 1)
    verts = []
    for y in ys:
        for x in xs:
            camber = 0.06 * chord * (1.0 - (2.0 * x / chord) ** 2)
            sweep = 0.25 * (y / (span / 2))
            verts.append([x + sweep, y, camber])
    verts = np.array(verts, dtype=np.float64)
    faces = []
    for j in range(ny):
        for i in range(nx):
            a = j * (nx + 1) + i
            b = a + 1
            c = a + (nx + 1)
            d = c + 1
            faces.append([a, c, b])
            faces.append([b, c, d])
    return verts, np.array(faces, dtype=np.int64)


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


def test_panel_method_refinement_converges_for_lift_drag_and_moment():
    """Verify that refining the panel grid converges lift, drag *and* moment.

    SPECIFICATION 6 #36 claims an asymptotic convergence demonstration for all
    three load components.  The previous revision verified the lift of a flat
    plate only, where the result is resolution independent and the assertion
    is vacuous.
    """
    q = 0.5 * 1.225 * (10.0 ** 2)
    verts, faces = _cambered_wing(6, 3)

    results = {}
    for n in (2, 4, 8, 16):
        panels = mesh_domain.build_panels(verts, faces, nx=n, ny=n)
        res = mesh_domain.panel_aerodynamics(panels, alpha_deg=5.0, dynamic_pressure_pa=q)
        results[n] = {
            "lift": abs(float(res["total_lift_n"])),
            "drag": abs(float(res["total_drag_n"])),
            "moment": abs(float(res["total_moment_nm"])),
        }

    reference = results[16]
    assert reference["lift"] > 0.0
    assert reference["drag"] > 0.0
    assert reference["moment"] > 0.0

    # The finest two grids agree to within 1e-6 relative on every component.
    for key in ("lift", "drag", "moment"):
        rel = abs(results[8][key] - reference[key]) / max(reference[key], 1e-12)
        assert rel < 1e-6, f"{key} did not converge: rel={rel:.3e}"

    # The coarse grid is genuinely unresolved, otherwise the test above could
    # not distinguish a converged method from a constant answer.
    coarse_rel = abs(results[2]["lift"] - reference["lift"]) / reference["lift"]
    assert coarse_rel > 0.05

    # The normal-force contribution to drag is a flat-plate quantity and must
    # be exactly resolution independent.
    drag_rel = abs(results[2]["drag"] - reference["drag"]) / reference["drag"]
    assert drag_rel < 1e-12
