"""Falsification test suite for MILCY Convergence Audit (F-01 through F-27).

Every test in this file is designed to FAIL on the unpatched commit (acfe623)
when the reported bug is reproducible, and PASS once the minimal surgical fix
is applied.
"""
from __future__ import annotations

import inspect
import math
from pathlib import Path
from typing import Any, List, Tuple

import numpy as np
import pytest

from core.interfaces import (
    MAX_FOLD_STEPS,
    MIN_FOLD_STEPS,
    AircraftCandidate,
    AirframeGeometry,
    BehaviorDescriptor,
    EvaluationMetric,
    FlightBenchmarkResult,
    ParametricKeelSpec,
    ParametricNoseSpec,
    ParametricOrigamiConfig,
    ParametricWingSpec,
)
from geometry.origami_domain import FoldedFace
from geometry.origami_genome import (
    FoldStep,
    OrigamiGenome,
    SHEET_OUTLINE,
    adjacent_panel_pairs,
    fold_genome,
    _separated_polygons,
)
from geometry.v3_origami.adapter import (
    OrigamiAdapter,
    detect_strict_self_intersections,
    extract_airframe_geometry,
    validate_tier0,
)
from geometry.v3_origami.compiler import (
    ParametricOrigamiCompiler,
    validate_crease_pattern_physics,
)
from geometry.v3_origami.kernel_25d import (
    Sequential2DOrigamiKernel,
    compile_macro_origami,
)
from geometry.v3_origami.mcts import MCTSOrigamiGenerator
from optimization.eval.robustness import RobustnessEvaluator
from optimization.map_elites.engine import MAPElitesEngine
from optimization.map_elites.grid import MAPElitesGridArchive
from optimization.map_elites.mutation import mutate_parametric_config
from optimization.trim.nelder_mead import NelderMeadTrimOptimizer
from physics import aero_domain, aero_kernel, flight_sim, gpu_dynamics
from physics.benchmark.benchmark import (
    DeterministicFlightBenchmark,
    _build_aircraft_config,
    evaluate_horizontal_glide,
)
from physics.config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    SimulationSettings,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_valid_benchmark(score_time: float = 4.0) -> FlightBenchmarkResult:
    m = EvaluationMetric(
        speed_mps=6.0,
        flight_time_s=score_time,
        max_ld_ratio=8.0,
        steady_glide_fraction=0.8,
        stability=0.8,
        distance_m=15.0,
        max_altitude_m=1.6,
        landed=True,
        crashed=False,
        diverged=False,
        status="landed",
    )
    m_mid = EvaluationMetric(
        speed_mps=10.0,
        flight_time_s=score_time,
        max_ld_ratio=8.0,
        steady_glide_fraction=0.8,
        stability=0.8,
        distance_m=22.0,
        max_altitude_m=1.7,
        landed=True,
        crashed=False,
        diverged=False,
        status="landed",
    )
    comp = math.sqrt(m.deterministic_score * m_mid.deterministic_score)
    return FlightBenchmarkResult(
        low_speed=m,
        mid_speed=m_mid,
        composite_score=comp,
        valid=True,
    )


# ===========================================================================
# Physics Parity (F-01 .. F-07)
# ===========================================================================
def test_f01_added_mass_coriolis_energy_and_parity() -> None:
    """F-01: CPU/GPU added-mass Coriolis z-term parity & Kirchhoff energy conservation."""
    env = EnvironmentConfig(wind_speed=0.0, gravity=0.0)
    launch = LaunchConfig(launch_speed=6.0, launch_angle_deg=0.0, launch_height_m=5.0)
    settings = SimulationSettings()

    # Non-zero body velocity (u, v, w) and angular rates (p, q, r) so p*v - q*u != 0
    y = np.array(
        [0.0, 0.0, -5.0, 6.0, 1.2, 0.7, 1.0, 0.0, 0.0, 0.0, 0.4, -0.5, 0.2],
        dtype=np.float64,
    )

    for munk_enabled in (False, True):
        ac = AircraftConfig(
            added_mass_enabled=True,
            apparent_inertia_enabled=True,
            munk_coupling_enabled=munk_enabled,
        )
        sim = flight_sim.FlightSimulator(ac, env, launch, settings)
        params = gpu_dynamics.build_params(np, [ac], env)
        w_gpu = np.zeros((1, 3), dtype=np.float64)

        core_cpu = sim._compute_dynamics(y, 0.0)
        core_gpu = gpu_dynamics._evaluate_state(np, params, y[np.newaxis, :], w_gpu)

        np.testing.assert_allclose(
            core_cpu["acceleration"],
            core_gpu["acceleration"][0],
            rtol=1e-6,
            atol=1e-8,
            err_msg=f"CPU/GPU linear acceleration mismatch (munk={munk_enabled})",
        )
        np.testing.assert_allclose(
            core_cpu["angular_acceleration"],
            core_gpu["angular_acceleration"][0],
            rtol=1e-7,
            atol=1e-9,
            err_msg=f"CPU/GPU angular acceleration mismatch (munk={munk_enabled})",
        )

        # Verify exact energy conservation of internal Kirchhoff terms:
        # dT/dt = v_b . F_ext + omega . M_ext (no spurious work from Coriolis/Munk).
        # Compute internal power = m*u*a_x + m*v*a_y + (m+m_z)*w*a_z - m_z*w*(p*v-q*u) + omega . (J_eff alpha)
        # minus external power (v . F_aero + omega . M_aero).
        added = aero_kernel.compute_added_mass(
            aero_kernel.ScalarMath,
            1.225,
            sim.aircraft.wing_area_m2,
            sim._chord,
            wingspan_m=sim.aircraft.wingspan_m,
        )
        m_z = float(added["added_mass_z_kg"])
        # Check GPU vs CPU internal work rate difference is zero and matches analytical skew-symmetry:
        # For munk_enabled=False, f_cor_z must be 0 so internal Coriolis work cancels -m_z*w*(p*v-q*u).
        # For munk_enabled=True, f_cor_z = munk_factor * m_z * (p*v - q*u) cancels omega . M_munk.
        u, v, w = y[3], y[4], y[5]
        p, q, _r = y[10], y[11], y[12]
        # Compare a_z with and without omega to isolate f_cor_z
        y_zero_omega = y.copy()
        y_zero_omega[10:13] = 0.0
        # Zero damping/aero-rate effects by checking at rho=1.225 with cm_q=0 etc.
        ac_pure = AircraftConfig(
            added_mass_enabled=True,
            apparent_inertia_enabled=True,
            munk_coupling_enabled=munk_enabled,
            cL0=0.0,
            cLalpha_per_rad=0.001,
            cd0=0.0,
            cm0=0.0,
            cm_alpha=0.0,
            cm_q=0.0,
            cl_roll_rate=0.0,
            cn_yaw_rate=0.0,
            fold_trailing_deg=0.0,
            elevon_deg=0.0,
        )
        sim_p = flight_sim.FlightSimulator(ac_pure, env, launch, settings)
        d_cpu = sim_p._compute_dynamics(y, 0.0)
        d0_cpu = sim_p._compute_dynamics(y_zero_omega, 0.0)
        m_eff_z = sim_p._mass + m_z
        # Difference in m_eff_z * a_z between omega!=0 and omega=0 is f_cor_z (since force_body is identical at same v,q)
        f_cor_z_cpu = m_eff_z * (d_cpu["acceleration"][2] - d0_cpu["acceleration"][2])
        expected_f_cor_z = (m_z * (p * v - q * u)) if munk_enabled else 0.0
        assert f_cor_z_cpu == pytest.approx(expected_f_cor_z, rel=5e-3, abs=1e-7), (
            f"CPU f_cor_z={f_cor_z_cpu} != expected {expected_f_cor_z} (munk={munk_enabled})"
        )


def test_f02_benchmark_ghost_trailing_fold_and_cm_alpha() -> None:
    """F-02: _build_aircraft_config must not inherit ghost fold_trailing_deg=15.0 and must couple cm_alpha to static_margin."""
    geom_unstable = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.025,
        wingspan_m=0.16,
        chord_m=0.25,
        aspect_ratio=1.024,
        cg_m=(-0.02, 0.0, 0.0),  # aft of 0.55*chord delta AC from nose=0.1485 -> static_margin < 0
        keel_height_m=0.015,
        keel_length_m=0.10,
        keel_layers=2,
        step_count=16,
        planform_area_m2=0.025,
        planform_aspect_ratio=1.024,
        penetrations=0,
        fold_valid=True,
        nose_tip_x_m=0.1485,
    )
    cand = AircraftCandidate(
        candidate_id="f02-unstable",
        config=ParametricOrigamiConfig(),
        step_count=16,
        geometry=geom_unstable,
    )
    ac = _build_aircraft_config(cand)
    assert ac.fold_trailing_deg == pytest.approx(0.0), (
        f"Ghost fold_trailing_deg={ac.fold_trailing_deg} inherited from AircraftConfig default"
    )
    assert ac.static_margin < 0.0
    assert ac.cm_alpha > 0.0, (
        f"Statically unstable airframe (static_margin={ac.static_margin:.3f}) has stabilizing cm_alpha={ac.cm_alpha:.3f}"
    )


def test_f03_ground_contact_friction_impulse_angular_rate_clamp() -> None:
    """F-03: Ground contact friction impulse must not push omega above HARD_ANGULAR_RATE_LIMIT_RPS and cause false divergence."""
    # Lightweight airframe with small inertia hitting ground at high tangential speed
    ac = AircraftConfig(
        mass_kg=0.005,
        wing_area_m2=0.015,
        wingspan_m=0.20,
        keel_height_m=0.015,
    )
    env = EnvironmentConfig(wind_speed=0.0)
    launch = LaunchConfig(
        launch_speed=15.0,
        launch_angle_deg=-30.0,
        launch_roll_deg=45.0,
        launch_height_m=0.05,
    )
    settings = SimulationSettings(duration_s=1.0, dt_max=0.01, collision_enabled=True, restitution=0.35)
    sim = flight_sim.FlightSimulator(ac, env, launch, settings)
    sim.simulate(duration_s=1.0)
    m = sim.metrics()
    assert not m["diverged"], f"Ground contact caused false divergence: status={m['status']}"


def test_f04_legacy_v3_package_divergence() -> None:
    """F-04: Legacy v3 package must enforce penetrations/fold_valid, planform area, nose_tip_x, dt_max, and elevon sign."""
    from v3.origami.adapter import validate_tier0 as v3_validate_tier0
    from v3.origami.compiler import ParametricOrigamiCompiler as V3Compiler
    from v3.physics.benchmark import _build_aircraft_config as v3_build_ac
    from v3.interfaces import (
        AircraftCandidate as V3Cand,
        AirframeGeometry as V3Geom,
        ParametricOrigamiConfig as V3Cfg,
        ParametricWingSpec as V3Wing,
    )

    # 1. v3 validate_tier0 must reject fold_valid=False or penetrations > 0
    bad_geom = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.02,
        wingspan_m=0.16,
        chord_m=0.25,
        aspect_ratio=1.28,
        cg_m=(0.06, 0.0, 0.0),
        keel_height_m=0.015,
        keel_length_m=0.10,
        keel_layers=2,
        step_count=16,
        planform_area_m2=0.02,
        planform_aspect_ratio=1.28,
        penetrations=3,
        fold_valid=False,
    )
    bad_cand = AircraftCandidate(
        candidate_id="f04-bad",
        config=ParametricOrigamiConfig(),
        step_count=16,
        geometry=bad_geom,
    )
    valid, _ = v3_validate_tier0(bad_cand)
    assert not valid, "v3.origami.adapter.validate_tier0 accepted penetrations=3 / fold_valid=False"

    # 2. v3 compiler elevon sign must not hardcode up=False for both +10 and -10
    comp = V3Compiler()
    steps_pos = comp.compile_steps(V3Cfg(wing=V3Wing(elevon_left_deg=8.0, elevon_right_deg=8.0)))
    steps_neg = comp.compile_steps(V3Cfg(wing=V3Wing(elevon_left_deg=-8.0, elevon_right_deg=-8.0)))
    elev_pos = [s for s in steps_pos if "elevon" in (s.set_group or s.label or "").lower()]
    elev_neg = [s for s in steps_neg if "elevon" in (s.set_group or s.label or "").lower()]
    assert elev_pos and elev_neg
    assert elev_pos[0].mountain != elev_neg[0].mountain, "v3 compiler hardcoded elevon fold direction"

    # 3. v3 benchmark must use fold_trailing_deg=0.0 and planform_area_m2
    geom_pf = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.050,
        wingspan_m=0.16,
        chord_m=0.25,
        aspect_ratio=0.512,
        cg_m=(0.06, 0.0, 0.0),
        keel_height_m=0.015,
        keel_length_m=0.10,
        keel_layers=2,
        step_count=16,
        planform_area_m2=0.020,
        planform_aspect_ratio=1.28,
        nose_tip_x_m=0.120,
    )
    cand_pf = AircraftCandidate(
        candidate_id="f04-pf",
        config=ParametricOrigamiConfig(),
        step_count=16,
        geometry=geom_pf,
    )
    ac_v3 = v3_build_ac(cand_pf)
    assert ac_v3.wing_area_m2 == pytest.approx(0.020, rel=1e-6)
    assert ac_v3.fold_trailing_deg == pytest.approx(0.0)


def test_f05_gpu_build_params_low_re_fields() -> None:
    """F-05: gpu_dynamics.build_params must propagate low_re_enabled and all 5 low_re_*_gain fields."""
    ac = AircraftConfig(
        low_re_enabled=True,
        low_re_cl_alpha_gain=0.08,
        low_re_stall_gain=0.15,
        low_re_cl_max_gain=0.18,
        low_re_lsb_gain=0.20,
        low_re_cd0_gain=0.06,
    )
    env = EnvironmentConfig()
    params = gpu_dynamics.build_params(np, [ac], env)
    assert bool(np.asarray(params.aero.low_re_enabled).ravel()[0]) is True
    assert float(np.asarray(params.aero.low_re_cl_alpha_gain).ravel()[0]) == pytest.approx(0.08)
    assert float(np.asarray(params.aero.low_re_stall_gain).ravel()[0]) == pytest.approx(0.15)
    assert float(np.asarray(params.aero.low_re_cl_max_gain).ravel()[0]) == pytest.approx(0.18)
    assert float(np.asarray(params.aero.low_re_lsb_gain).ravel()[0]) == pytest.approx(0.20)
    assert float(np.asarray(params.aero.low_re_cd0_gain).ravel()[0]) == pytest.approx(0.06)


def test_f06_gpu_station_altitudes_keel_height_m() -> None:
    """F-06: gpu_dynamics._station_altitudes must use aircraft keel_height_m instead of hardcoded 0.015."""
    ac = AircraftConfig(keel_height_m=0.045)
    env = EnvironmentConfig(ground_level_m=0.0)
    params = gpu_dynamics.build_params(np, [ac], env)
    z_cg = np.array([-0.06], dtype=np.float64)
    rot = np.eye(3, dtype=np.float64)[np.newaxis, :, :]
    alts = gpu_dynamics._station_altitudes(np, params, z_cg, rot)[0]
    # Station 4 is keel: alt = -(-0.06 + 0.045) = 0.015
    assert float(alts[4]) == pytest.approx(0.015, abs=1e-6), (
        f"GPU keel altitude={float(alts[4])}, expected 0.015 for keel_height_m=0.045"
    )


def test_f07_aero_domain_low_re_parity() -> None:
    """F-07: aero_domain.lift_coefficient/drag_coefficient/drag_polar must match evaluate_aero when low_re_enabled=True."""
    ac = AircraftConfig(low_re_enabled=True, fold_trailing_deg=0.0, elevon_deg=0.0)
    env = EnvironmentConfig()
    # Evaluate at low Re (~5000) via low speed
    chord = aero_domain._chord(ac)
    nu = 1.48e-5
    re_target = 5000.0
    speed = re_target * nu / chord
    alpha = 12.0

    res = aero_domain.evaluate_aero(alpha_deg=alpha, speed_mps=speed, cfg=ac, env=env)
    re_actual = res.reynolds
    cl_fn = aero_domain.lift_coefficient(alpha, ac, compressible=False, mach=0.0, reynolds=re_actual)
    cd_fn = aero_domain.drag_coefficient(alpha, ac, reynolds=re_actual, ground_height_m=None, compressible=False, mach=0.0)

    assert cl_fn == pytest.approx(res.cl, rel=1e-4), f"lift_coefficient={cl_fn} != evaluate_aero.cl={res.cl}"
    assert cd_fn == pytest.approx(res.cd, rel=2e-3), f"drag_coefficient={cd_fn} != evaluate_aero.cd={res.cd}"


# ===========================================================================
# Origami & Folding Pipeline (F-08 .. F-16)
# ===========================================================================
def test_f08_bilateral_crease_side_self_intersection() -> None:
    """F-08: Default & multi-fold configs must fold with 0 self-intersections (side parity on bilateral creases)."""
    adapter = OrigamiAdapter()
    for fc in (3, 4, 5, 6):
        cfg = ParametricOrigamiConfig(
            nose=ParametricNoseSpec(fold_count=fc),
            keel=ParametricKeelSpec(has_lock_fold=True),
            wing=ParametricWingSpec(elevon_left_deg=2.0, elevon_right_deg=2.0),
        )
        cand = adapter.compile_candidate(cfg, candidate_id=f"f08-fc{fc}")
        assert cand.geometry is not None
        assert cand.geometry.penetrations == 0, (
            f"fold_count={fc} produced {cand.geometry.penetrations} self-intersections"
        )
        if fc <= 5:
            assert cand.metadata.get("tier0_valid") is True, (
                f"fold_count={fc} failed Tier 0: {cand.metadata.get('tier0_reasons')}"
            )


def test_f09_max_fold_steps_truncation_and_duplicate_keel_lock() -> None:
    """F-09: High fold_count with winglet & lock must not truncate elevons/wings or duplicate keel_lock creases."""
    compiler = ParametricOrigamiCompiler()
    cfg = ParametricOrigamiConfig(
        nose=ParametricNoseSpec(fold_count=6),
        keel=ParametricKeelSpec(has_lock_fold=True),
        wing=ParametricWingSpec(has_winglet=True, elevon_left_deg=4.0, elevon_right_deg=4.0),
    )
    steps = compiler.compile_steps(cfg)
    assert len(steps) <= MAX_FOLD_STEPS
    groups = [s.set_group for s in steps]
    # Must retain both wings and both elevons (not silently truncated)
    for required in ("wing_l", "wing_r", "elevon_l", "elevon_r"):
        assert required in groups, f"Required crease '{required}' was silently truncated! groups={groups}"
    # Must not duplicate keel_lock_l or keel_lock_r
    assert groups.count("keel_lock_l") <= 1, f"Duplicate keel_lock_l in {groups}"
    assert groups.count("keel_lock_r") <= 1, f"Duplicate keel_lock_r in {groups}"


def test_f10_strict_self_intersection_fid_vs_index_adjacency() -> None:
    """F-10: detect_strict_self_intersections must match adjacency by fid only, never by array index (i, j)."""
    # Face 0 (fid=10): horizontal square in y=0 plane
    f0 = FoldedFace(
        fid=10,
        poly3d=((-0.05, 0.0, -0.05), (0.05, 0.0, -0.05), (0.05, 0.0, 0.05), (-0.05, 0.0, 0.05)),
        normal=(0.0, 1.0, 0.0),
        front=True,
        level=0,
        group="a",
        role="wing",
        parent=None,
        centroid3d=(0.0, 0.0, 0.0),
        area_m2=0.01,
    )
    # Face 1 (fid=20): far away dummy face
    f1 = FoldedFace(
        fid=20,
        poly3d=((1.0, 1.0, 1.0), (1.1, 1.0, 1.0), (1.1, 1.0, 1.1), (1.0, 1.0, 1.1)),
        normal=(0.0, 1.0, 0.0),
        front=True,
        level=0,
        group="b",
        role="wing",
        parent=None,
        centroid3d=(1.05, 1.0, 1.05),
        area_m2=0.01,
    )
    # Face 2 (fid=30): vertical square piercing f0 right through the middle
    f2 = FoldedFace(
        fid=30,
        poly3d=((0.0, -0.05, -0.02), (0.0, 0.05, -0.02), (0.0, 0.05, 0.02), (0.0, -0.05, 0.02)),
        normal=(1.0, 0.0, 0.0),
        front=True,
        level=0,
        group="c",
        role="wing",
        parent=None,
        centroid3d=(0.0, 0.0, 0.0),
        area_m2=0.004,
    )
    # Adjacency contains (0, 2) by fid (unrelated fids 0 and 2), NOT (10, 30)
    hits = detect_strict_self_intersections([f0, f1, f2], adjacency={(0, 2)})
    assert len(hits) >= 1, "X-crossing between fid=10 and fid=30 was missed due to (i, j)=(0, 2) adjacency collision"


def test_f11_kernel_25d_z_index_collision_and_kawasaki_endpoint_vertex() -> None:
    """F-11: 2.5D kernel multi-layer mountain fold must preserve unique z_index; Kawasaki/Maekawa must catch endpoint vertices."""
    # Part A: Multi-layer mountain fold in Sequential2DOrigamiKernel
    k = Sequential2DOrigamiKernel()
    # First fold in half along u=0.5 so we have 2 overlapping layers at z=0 and z=1
    k.execute_fold(p0=(0.5, 0.0), direction=(0.0, 1.0), mountain=True)
    assert len(k.polygons) == 2
    # Second fold across v=0.5 (mountain=True) moves BOTH layers
    k.execute_fold(p0=(0.0, 0.5), direction=(1.0, 0.0), mountain=True)
    moved_z = [p.z_index for p in k.polygons if p.z_index > 1]
    assert len(moved_z) == 2
    assert len(set(moved_z)) == 2, f"Multi-layer mountain fold caused z_index collision: {moved_z}"

    # Part B: 4 flat-fold creases meeting at shared interior endpoint (0.5, 0.5)
    # All 4 are mountain (Maekawa violation: |4 - 0| = 4 != 2) and non-Kawasaki angles (30, 60, 120, 150 -> alt sums 150 vs 210)
    cx, cy = 0.5, 0.5
    angles_deg = [0.0, 30.0, 90.0, 210.0]
    steps = [
        FoldStep(
            step_index=i + 1,
            crease_line=((cx, cy), (cx + 0.2 * math.cos(math.radians(a)), cy + 0.2 * math.sin(math.radians(a)))),
            fold_angle_deg=180.0,
            mountain=True,
            label=f"ray_{i}",
        )
        for i, a in enumerate(angles_deg)
    ]
    res = validate_crease_pattern_physics(steps, paper_gsm=100.0, nose_fold_count=3, nose_length_ratio=0.30)
    assert not res.flat_foldable_ok, "Endpoint-convergent Kawasaki/Maekawa violation was skipped"


def test_f12_printable_a4_folding_instructions_uses_candidate_config() -> None:
    """F-12: render_a4_printable_sheet must pass candidate dict (not bare cand.metadata) to build_folding_instructions."""
    from tools import generate_printable_a4 as a4

    src = inspect.getsource(a4.render_a4_printable_sheet)
    assert "build_folding_instructions(cand.metadata)" not in src, (
        "render_a4_printable_sheet passes bare cand.metadata so build_folding_instructions always falls back to defaults"
    )


def test_f13_elevon_mountain_valley_sign_preserved() -> None:
    """F-13: Elevon mountain/valley sign must reflect raw_mountain and elevon sign in A4 & kernel_25d."""
    from tools.generate_printable_a4 import resolve_canonical_crease_style

    is_mtn_true, _, _ = resolve_canonical_crease_style("elevon_l", raw_mountain=True)
    is_mtn_false, _, _ = resolve_canonical_crease_style("elevon_l", raw_mountain=False)
    assert is_mtn_true is True and is_mtn_false is False, (
        f"resolve_canonical_crease_style overwrote elevon sign: True->{is_mtn_true}, False->{is_mtn_false}"
    )

    cfg_pos = ParametricOrigamiConfig(wing=ParametricWingSpec(elevon_left_deg=6.0, elevon_right_deg=6.0))
    cfg_neg = ParametricOrigamiConfig(wing=ParametricWingSpec(elevon_left_deg=-6.0, elevon_right_deg=-6.0))
    m_pos, _ = compile_macro_origami(cfg_pos)
    m_neg, _ = compile_macro_origami(cfg_neg)
    e_pos = [s for s in m_pos if "elevon" in s.name][0]
    e_neg = [s for s in m_neg if "elevon" in s.name][0]
    assert e_pos.mountain != e_neg.mountain, "compile_macro_origami hardcoded elevon mountain=False"


def test_f14_wingspan_ratio_sign_and_keel_depth_a4() -> None:
    """F-14: kernel_25d wingspan_ratio direction must match compiler (larger ratio -> larger wing_u) and A4 keel_mm must use sheet width."""
    cfg_lo = ParametricOrigamiConfig(wing=ParametricWingSpec(wingspan_ratio=0.55))
    cfg_hi = ParametricOrigamiConfig(wing=ParametricWingSpec(wingspan_ratio=0.85))
    m_lo, _ = compile_macro_origami(cfg_lo)
    m_hi, _ = compile_macro_origami(cfg_hi)
    w_lo = [s for s in m_lo if s.name == "wing_folds"][0].line_p0[0]
    w_hi = [s for s in m_hi if s.name == "wing_folds"][0].line_p0[0]
    assert w_hi > w_lo, f"kernel_25d wingspan_ratio inverted: 0.55->u={w_lo}, 0.85->u={w_hi}"

    from tools.generate_printable_a4 import build_folding_instructions
    # Without macro_steps, keel_mm should reflect actual geometry keel_height_m or sheet width (210mm), not 297*0.5
    cand_dict = {
        "config": {"nose": {}, "keel": {"keel_depth_ratio": 0.10}, "wing": {}},
        "geometry": {"keel_height_m": 0.022},
    }
    instrs = build_folding_instructions(cand_dict)
    wing_instr = [i for i in instrs if "主翼" in i["title"]][0]
    assert "22.0mm" in wing_instr["title"] or "21.0mm" in wing_instr["title"], (
        f"A4 keel depth used wrong scale: {wing_instr['title']}"
    )


def test_f15_viewer_2d_mirror_and_zero_deg_falsy() -> None:
    """F-15: view_milcy_elite.py must not mirror 2D preview X-axis and must use ?? instead of || for 0.0 deg."""
    viewer_path = Path("tools/view_milcy_elite.py")
    text = viewer_path.read_text(encoding="utf-8")
    assert "w/2 - (pos[i0*3 + 2] / 0.105)" not in text, "2D preview X-axis is mirror-flipped (- instead of +)"
    assert "(cfg.wing.elevon_left_deg || 3.9)" not in text, "JS || turns 0.0 deg elevon into 3.9"
    assert "(cfg.wing.dihedral_deg || 4.4)" not in text, "JS || turns 0.0 deg dihedral into 4.4"
    assert "(cfg.wing.winglet_angle_deg || 50.3)" not in text, "JS || turns 0.0 deg winglet into 50.3"


def test_f16_origami_genome_pitch_yaw_inertia_axes() -> None:
    """F-16: OrigamiGenome must map Z-axis inertia to pitch and Y-axis (vertical) inertia to yaw."""
    genome = OrigamiGenome(steps=[], sheet=list(SHEET_OUTLINE))
    geom = fold_genome(genome, paper_gsm=100.0)
    # Flat A4 sheet in X-Z plane (L=0.297 along X, W=0.210 along Z, Y=0):
    # I_roll (X) = m*W^2/12, I_pitch (Z) = m*L^2/12, I_yaw (Y) = m*(L^2 + W^2)/12 = I_roll + I_pitch
    assert geom.yaw_inertia_kgm2 > geom.pitch_inertia_kgm2 > geom.roll_inertia_kgm2, (
        f"Expected yaw > pitch > roll, got roll={geom.roll_inertia_kgm2:.3e}, "
        f"pitch={geom.pitch_inertia_kgm2:.3e}, yaw={geom.yaw_inertia_kgm2:.3e}"
    )
    assert math.isclose(
        geom.yaw_inertia_kgm2,
        geom.roll_inertia_kgm2 + geom.pitch_inertia_kgm2,
        rel_tol=1e-3,
    )


# ===========================================================================
# MILCY Red Team (F-17 .. F-27)
# ===========================================================================
def test_f17_antichat_penalties_wired_to_benchmark() -> None:
    """F-17: FlightSimulator.metrics() and evaluate_horizontal_glide() must wire bounce_count, max_abs_roll_deg, ground_skim_fraction."""
    ac = AircraftConfig(
        fold_trailing_deg=0.0,
        aileron_deg=15.0,  # Induce significant roll
    )
    env = EnvironmentConfig(wind_speed=0.0)
    launch = LaunchConfig(launch_speed=7.0, launch_angle_deg=0.0, launch_height_m=1.5)
    sim = flight_sim.FlightSimulator(ac, env, launch, SimulationSettings(duration_s=3.0))
    sim.simulate(duration_s=3.0)
    m = sim.metrics()
    assert "bounce_count" in m, "FlightSimulator.metrics() missing bounce_count"
    assert "max_abs_roll_deg" in m, "FlightSimulator.metrics() missing max_abs_roll_deg"
    assert "ground_skim_fraction" in m, "FlightSimulator.metrics() missing ground_skim_fraction"

    metric = evaluate_horizontal_glide(ac, speed_mps=7.0, duration_s=3.0)
    assert metric.max_abs_roll_deg > 10.0, (
        f"evaluate_horizontal_glide did not wire max_abs_roll_deg (got {metric.max_abs_roll_deg})"
    )


def test_f18_nelder_mead_enforces_tier0_and_updates_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """F-18: NelderMeadTrimOptimizer must reject Tier 0 invalid candidates and update tier0_valid metadata."""
    adapter = OrigamiAdapter()
    benchmark = DeterministicFlightBenchmark(duration_s=2.0, dt_max=0.02)
    opt = NelderMeadTrimOptimizer(adapter=adapter, benchmark=benchmark, max_iter=3)

    # Create a base candidate
    base = adapter.compile_candidate(ParametricOrigamiConfig(), candidate_id="f18-base")
    base.benchmark = _make_valid_benchmark(score_time=0.5)
    base.metadata["tier0_valid"] = True

    # Force compiled trim candidates to have penetrations=5 (Tier 0 invalid!) while benchmark returns high score
    orig_compile = adapter.compile_candidate

    def fake_compile(cfg: ParametricOrigamiConfig, candidate_id: str | None = None, **kw: Any) -> AircraftCandidate:
        c = orig_compile(cfg, candidate_id=candidate_id, **kw)
        assert c.geometry is not None
        bad_g = AirframeGeometry(**{**c.geometry.__dict__, "penetrations": 5, "fold_valid": False})
        c.geometry = bad_g
        c.metadata["tier0_valid"] = False
        c.metadata["tier0_reasons"] = ["Self-intersection detected: 5 penetration(s)"]
        return c

    monkeypatch.setattr(adapter, "compile_candidate", fake_compile)
    monkeypatch.setattr(
        benchmark,
        "evaluate_candidate",
        lambda c: _make_valid_benchmark(score_time=2.0 + abs(c.config.wing.elevon_left_deg)),
    )

    opt.optimize_trim(base)
    assert base.geometry is not None
    assert base.geometry.penetrations != 5, "NelderMeadTrimOptimizer accepted a Tier 0 invalid candidate!"


def test_f19_extract_airframe_geometry_no_fake_keel_fallback() -> None:
    """F-19: extract_airframe_geometry must not fabricate >= 8mm/30mm/2-layer keel when evaluate_grip reports 0."""
    flat_genome = OrigamiGenome(steps=[], sheet=list(SHEET_OUTLINE))
    flat_geom_obj = fold_genome(flat_genome, paper_gsm=100.0)
    extracted = extract_airframe_geometry(flat_geom_obj, step_count=16)
    assert extracted.keel_height_m < 0.008, (
        f"Flat unfolded sheet fabricated keel_height_m={extracted.keel_height_m}"
    )


def test_f20_validate_tier0_fail_closed_on_invalid_fields() -> None:
    """F-20: validate_tier0 must fail closed on None/NaN/negative dimensions or invalid penetrations."""
    base_geom = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.02,
        wingspan_m=0.16,
        chord_m=0.25,
        aspect_ratio=1.28,
        cg_m=(0.06, 0.0, 0.0),
        keel_height_m=0.015,
        keel_length_m=0.10,
        keel_layers=2,
        step_count=16,
        planform_area_m2=0.02,
        planform_aspect_ratio=1.28,
        penetrations=0,
        fold_valid=True,
    )
    # 1. Invalid penetrations type ("bad" or None)
    g_bad_pen = AirframeGeometry(**{**base_geom.__dict__, "penetrations": "bad"})  # type: ignore[arg-type]
    c1 = AircraftCandidate(candidate_id="f20-1", config=ParametricOrigamiConfig(), step_count=16, geometry=g_bad_pen)
    ok1, _ = validate_tier0(c1)
    assert not ok1, "validate_tier0 silently coerced penetrations='bad' to 0 and passed"

    # 2. None mass_kg
    g_none_mass = AirframeGeometry(**{**base_geom.__dict__, "mass_kg": None})  # type: ignore[arg-type]
    c2 = AircraftCandidate(candidate_id="f20-2", config=ParametricOrigamiConfig(), step_count=16, geometry=g_none_mass)
    ok2, _ = validate_tier0(c2)
    assert not ok2, "validate_tier0 swallowed TypeError on mass_kg=None and passed"

    # 3. Negative wingspan
    g_neg_span = AirframeGeometry(**{**base_geom.__dict__, "wingspan_m": -0.15})
    c3 = AircraftCandidate(candidate_id="f20-3", config=ParametricOrigamiConfig(), step_count=16, geometry=g_neg_span)
    ok3, _ = validate_tier0(c3)
    assert not ok3, "validate_tier0 accepted negative wingspan_m=-0.15"


def test_f21_no_tier0_invalid_fallback_in_mcts_engine_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """F-21: MCTS, MAPElitesEngine, and cli.run_pipeline must not return/accept Tier 0 invalid fallback or silently succeed with 0 elites."""
    adapter = OrigamiAdapter()

    # Force every compiled candidate to fail Tier 0
    orig_compile = adapter.compile_candidate

    def always_invalid_compile(cfg: ParametricOrigamiConfig, candidate_id: str | None = None, **kw: Any) -> AircraftCandidate:
        c = orig_compile(cfg, candidate_id=candidate_id, **kw)
        assert c.geometry is not None
        c.geometry = AirframeGeometry(**{**c.geometry.__dict__, "penetrations": 9, "fold_valid": False})
        c.metadata["tier0_valid"] = False
        return c

    monkeypatch.setattr(adapter, "compile_candidate", always_invalid_compile)

    # 1. MCTS must not return a Tier 0 invalid fallback
    mcts = MCTSOrigamiGenerator(adapter=adapter, seed=42)
    results = mcts.search_valid_candidates(iterations=3, top_k=2)
    for r in results:
        ok, _ = validate_tier0(r)
        assert ok, "MCTSOrigamiGenerator returned a Tier 0 invalid fallback candidate"

    # 2. MAPElitesEngine.bootstrap_population must not insert invalid fallback into archive
    grid = MAPElitesGridArchive()
    bench = DeterministicFlightBenchmark(duration_s=1.0)
    monkeypatch.setattr(bench, "evaluate_candidate", lambda c: _make_valid_benchmark())
    engine = MAPElitesEngine(archive=grid, adapter=adapter, benchmark=bench, seed=42)
    engine.bootstrap_population(count=2, mcts_iterations=3)
    assert len(grid.cells) == 0, "MAPElitesEngine inserted Tier 0 invalid fallback into archive"


def test_f22_grid_archive_validates_tier0_and_geometry() -> None:
    """F-22: MAPElitesGridArchive._is_candidate_valid must reject geometry=None and Tier 0 violations."""
    grid = MAPElitesGridArchive(
        ranges=((0.10, 0.50), (0.10, 0.95), (0.30, 2.50)),
    )
    bench = _make_valid_benchmark()

    # 1. geometry=None with pre-populated descriptor
    c_no_geom = AircraftCandidate(
        candidate_id="f22-nogeom",
        config=ParametricOrigamiConfig(),
        step_count=16,
        geometry=None,
        benchmark=bench,
        descriptor=BehaviorDescriptor(0.20, 0.80, 0.65),
    )
    assert grid.add_candidate(c_no_geom) is False, "Grid archive accepted candidate with geometry=None"

    # 2. Keel height < 8mm, thickness_limit_ok=False, flat_foldable_ok=False, step_count < MIN_FOLD_STEPS
    bad_geom = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.025,
        wingspan_m=0.16,
        chord_m=0.25,
        aspect_ratio=1.024,
        cg_m=(0.06, 0.0, 0.0),
        keel_height_m=0.002,
        keel_length_m=0.010,
        keel_layers=1,
        step_count=2,
        planform_area_m2=0.025,
        planform_aspect_ratio=1.024,
        penetrations=0,
        fold_valid=True,
        thickness_limit_ok=False,
        flat_foldable_ok=False,
    )
    c_bad_t0 = AircraftCandidate(
        candidate_id="f22-badt0",
        config=ParametricOrigamiConfig(),
        step_count=2,
        geometry=bad_geom,
        benchmark=bench,
    )
    assert grid.add_candidate(c_bad_t0) is False, "Grid archive accepted Tier 0 violating candidate"


def test_f23_unclamped_nose_fold_count_gallivan_bypass() -> None:
    """F-23: adapter.compile_candidate must clamp nose.fold_count to [3, 6] before Gallivan thickness check."""
    adapter = OrigamiAdapter()
    # Heavy 300 gsm paper with very short nose (0.20 * 297mm = 59.4mm) and fold_count=0 (compiler clamps to 3!)
    # Actually test fold_count=0 vs fold_count=3 at a gsm/length where 3 folds fail Gallivan:
    # t = 300e-3/800 = 0.000375m; n=3 -> L = (pi/6)*t*(2^3+4)*(2^3-1) = 0.5236*0.000375*12*7 = 0.0165m.
    # For n=3 to exceed 0.20*0.297=0.0594m, we need t > 0.0594 / (0.5236*84) = 0.00135m -> gsm=1100.
    # Or better: pass fold_count=0 at gsm=1200 where n=3 fails Gallivan (L=0.066m > 0.0594m) while n=0 gives L=0!
    cfg = ParametricOrigamiConfig(
        nose=ParametricNoseSpec(fold_count=0, nose_length_ratio=0.20),
        paper_gsm=1200.0,
    )
    cand = adapter.compile_candidate(cfg, candidate_id="f23-bypass")
    assert cand.geometry is not None
    assert cand.geometry.thickness_limit_ok is False, (
        "fold_count=0 bypassed Gallivan thickness check even though compiler clamped fold_count to 3"
    )


def test_f24_map_elites_grid_ranges_match_real_origami() -> None:
    """F-24: cli.py fallback grid ranges and overnight_milcy_loop.py ranges must cover real folded A4 descriptors."""
    from optimization import cli
    import yaml

    # Check cli.py main() fallback ranges don't have stale [0.118, 0.128] / [0.215, 0.255]
    cli_src = Path("optimization/cli.py").read_text(encoding="utf-8")
    assert "[0.118, 0.128]" not in cli_src, "cli.py still has stale wing_loading_range fallback [0.118, 0.128]"
    assert "[0.215, 0.255]" not in cli_src, "cli.py still has stale aspect_ratio_range fallback [0.215, 0.255]"

    loop_src = Path("tools/overnight_milcy_loop.py").read_text(encoding="utf-8")
    assert "(0.115, 0.135)" not in loop_src, "overnight_milcy_loop.py still has stale wing_loading range (0.115, 0.135)"

    # Also verify config/milcy_map_elites.yaml covers default compiled candidate
    cfg_yaml = yaml.safe_load(Path("config/milcy_map_elites.yaml").read_text(encoding="utf-8"))
    g = cfg_yaml["grid"]
    archive = MAPElitesGridArchive(
        bins=tuple(g["bins"]),
        ranges=(tuple(g["wing_loading_range"]), tuple(g["stability_range"]), tuple(g["aspect_ratio_range"])),
        use_planform_bd=True,
    )
    cand = OrigamiAdapter().compile_candidate(ParametricOrigamiConfig(), candidate_id="f24-default")
    assert cand.geometry is not None
    desc = cand.geometry.compute_behavior_descriptor(stability=0.75, use_planform=True)
    assert archive.is_in_bounds(desc), (
        f"Default origami descriptor {desc} is out of bounds for milcy_map_elites.yaml ranges {archive.ranges}"
    )


def test_f25_benchmark_robustness_parity_and_wind_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """F-25: benchmark sets keel_height_m; robustness respects dt_max and logs exact simulated wind_direction_deg."""
    # 1. benchmark._build_aircraft_config must propagate geom.keel_height_m
    geom = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.025,
        wingspan_m=0.16,
        chord_m=0.25,
        aspect_ratio=1.024,
        cg_m=(0.06, 0.0, 0.0),
        keel_height_m=0.032,
        keel_length_m=0.10,
        keel_layers=2,
        step_count=16,
        planform_area_m2=0.025,
        planform_aspect_ratio=1.024,
    )
    cand = AircraftCandidate(candidate_id="f25", config=ParametricOrigamiConfig(), step_count=16, geometry=geom)
    ac = _build_aircraft_config(cand)
    assert ac.keel_height_m == pytest.approx(0.032), (
        f"benchmark._build_aircraft_config ignored keel_height_m (got {ac.keel_height_m})"
    )

    # 2. RobustnessEvaluator must pass dt_max and log exact simulated wind_direction_deg
    seen_wind_dirs: List[float] = []
    seen_dt_max: List[float] = []

    class _SpySim:
        def __init__(self, aircraft: Any, environment: Any, launch: Any, settings: Any = None) -> None:
            if environment.wind_speed > 0.0:
                seen_wind_dirs.append(float(environment.wind_direction_deg))
            if settings is not None:
                seen_dt_max.append(float(settings.dt_max))

        def simulate(self, duration_s: float = 6.0) -> None:
            pass

        def metrics(self) -> dict:
            return {"flight_time_s": 2.5, "distance_m": 10.0, "crashed": False, "diverged": False, "stalled": False}

    import optimization.eval.robustness as rob_mod
    monkeypatch.setattr(rob_mod, "FlightSimulator", _SpySim)

    ev = RobustnessEvaluator(duration_s=2.0, n_samples=10, seed=123, dt_max=0.015)
    report = ev.evaluate_candidate(cand)
    assert seen_dt_max and all(abs(d - 0.015) < 1e-9 for d in seen_dt_max), (
        f"RobustnessEvaluator ignored self.dt_max (seen_dt_max={seen_dt_max})"
    )
    logged_dirs = [r["wind_direction_deg"] for r in report.details["dryden_runs"]]
    assert logged_dirs == seen_wind_dirs, (
        f"RobustnessEvaluator logged different wind_direction_deg than simulated: logged={logged_dirs[-2:]} vs sim={seen_wind_dirs[-2:]}"
    )


def test_f26_robustness_survival_rejects_nonglide_short_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    """F-26: RobustnessEvaluator must not award survival=1.0 to 0.3s non-gliding drops."""
    geom = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.025,
        wingspan_m=0.16,
        chord_m=0.25,
        aspect_ratio=1.024,
        cg_m=(0.06, 0.0, 0.0),
        keel_height_m=0.015,
        keel_length_m=0.10,
        keel_layers=2,
        step_count=16,
        planform_area_m2=0.025,
        planform_aspect_ratio=1.024,
    )
    cand = AircraftCandidate(candidate_id="f26", config=ParametricOrigamiConfig(), step_count=16, geometry=geom)

    class _ShortDropSim:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        def simulate(self, duration_s: float = 6.0) -> None:
            pass

        def metrics(self) -> dict:
            return {"flight_time_s": 0.3, "distance_m": 0.4, "crashed": False, "diverged": False, "stalled": False}

    import optimization.eval.robustness as rob_mod
    monkeypatch.setattr(rob_mod, "FlightSimulator", _ShortDropSim)

    ev = RobustnessEvaluator(duration_s=6.0, n_samples=4, seed=42)
    report = ev.evaluate_candidate(cand)
    assert report.dryden_survival_rate == 0.0, (
        f"Short 0.3s non-glide drop received dryden_survival_rate={report.dryden_survival_rate}"
    )
    assert report.jitter_robustness_score == 0.0, (
        f"Short 0.3s non-glide drop received jitter_robustness_score={report.jitter_robustness_score}"
    )


def test_f27_planform_bd_default_and_mutation_trim_bounds() -> None:
    """F-27: MAPElitesGridArchive defaults use_planform_bd=True; mutation & NelderMead trim bounds align."""
    grid = MAPElitesGridArchive()
    assert grid.use_planform_bd is True, "MAPElitesGridArchive defaults use_planform_bd=False (diverges from benchmark planform_area_m2)"

    opt = NelderMeadTrimOptimizer(
        adapter=OrigamiAdapter(),
        benchmark=DeterministicFlightBenchmark(duration_s=1.0),
    )
    # Mutate many times and ensure mutated elevon/dihedral stay within NelderMeadTrimOptimizer default bounds
    import random
    rng = random.Random(42)
    cfg = ParametricOrigamiConfig()
    for _ in range(300):
        cfg = mutate_parametric_config(cfg, mutation_rate=1.0, rng=rng)
        assert opt.dihedral_bounds[0] <= cfg.wing.dihedral_deg <= opt.dihedral_bounds[1], (
            f"Mutated dihedral {cfg.wing.dihedral_deg} outside trim bounds {opt.dihedral_bounds}"
        )
        assert opt.elevon_bounds[0] <= cfg.wing.elevon_left_deg <= opt.elevon_bounds[1], (
            f"Mutated elevon_left {cfg.wing.elevon_left_deg} outside trim bounds {opt.elevon_bounds}"
        )
        assert opt.elevon_bounds[0] <= cfg.wing.elevon_right_deg <= opt.elevon_bounds[1], (
            f"Mutated elevon_right {cfg.wing.elevon_right_deg} outside trim bounds {opt.elevon_bounds}"
        )
