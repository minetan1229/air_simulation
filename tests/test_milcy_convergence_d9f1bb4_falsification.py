"""Falsification test suite for MILCY convergence audit (d9f1bb4): C-01 through C-16.

Every test in this file is designed to FAIL before the corresponding fix is
applied and PASS once the minimal fix is in place.
"""

from __future__ import annotations

import math
from dataclasses import replace
from unittest.mock import patch

import numpy as np
import pytest

from core.interfaces import (
    AircraftCandidate,
    AirframeGeometry,
    BehaviorDescriptor,
    EvaluationMetric,
    FlightBenchmarkResult,
    ParametricOrigamiConfig,
    ParametricWingSpec,
)


# ---------------------------------------------------------------------------
# C-01 [S1]: v3/physics/properties.py parity & _build_aircraft_config <=0 fallback
# ---------------------------------------------------------------------------
def test_c01_v3_properties_parity_and_nonpositive_planform_fallback():
    from geometry.origami_genome import fold_genome
    from geometry.v3_origami.adapter import OrigamiAdapter
    from physics.benchmark.benchmark import _build_aircraft_config as canon_build_ac
    from physics.benchmark.properties import extract_airframe_geometry as canon_extract
    from v3.physics.benchmark import _build_aircraft_config as v3_build_ac
    from v3.physics.properties import extract_airframe_geometry as v3_extract

    adapter = OrigamiAdapter()
    cand = adapter.compile_candidate(ParametricOrigamiConfig(), candidate_id="c01-test")
    folded = fold_genome(cand.raw_genome, paper_gsm=cand.config.paper_gsm)

    g_canon = canon_extract(folded, step_count=cand.step_count)
    g_v3 = v3_extract(folded, step_count=cand.step_count)

    assert math.isclose(g_v3.planform_area_m2, g_canon.planform_area_m2, rel_tol=1e-9)
    assert math.isclose(g_v3.planform_aspect_ratio, g_canon.planform_aspect_ratio, rel_tol=1e-9)
    assert g_v3.penetrations == g_canon.penetrations
    assert math.isclose(g_v3.nose_tip_x_m, g_canon.nose_tip_x_m, rel_tol=1e-9)

    # Non-positive planform_area_m2 / planform_aspect_ratio must fall back to wing_area_m2 / aspect_ratio
    zero_planform_geom = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.018,
        wingspan_m=0.18,
        chord_m=0.12,
        aspect_ratio=1.80,
        cg_m=(0.08, 0.0, 0.005),
        keel_height_m=0.015,
        keel_length_m=0.08,
        keel_layers=4,
        step_count=16,
        planform_area_m2=0.0,
        planform_aspect_ratio=0.0,
        fold_valid=True,
    )
    cand_zero = AircraftCandidate(
        candidate_id="c01-zero",
        config=ParametricOrigamiConfig(),
        step_count=16,
        geometry=zero_planform_geom,
    )
    ac_canon = canon_build_ac(cand_zero)
    ac_v3 = v3_build_ac(cand_zero)
    assert math.isclose(ac_canon.wing_area_m2, 0.018, rel_tol=1e-9)
    assert math.isclose(ac_canon.aspect_ratio, 1.80, rel_tol=1e-9)
    assert math.isclose(ac_v3.wing_area_m2, 0.018, rel_tol=1e-9)
    assert math.isclose(ac_v3.aspect_ratio, 1.80, rel_tol=1e-9)


# ---------------------------------------------------------------------------
# C-02 [S1]: GPU ground impact speed missing omega x r rotational lever arm
# ---------------------------------------------------------------------------
def test_c02_gpu_impact_speed_includes_rotational_lever_arm():
    from physics import gpu_dynamics
    from physics.config_schema import (
        AircraftConfig,
        EnvironmentConfig,
        LaunchConfig,
        SimulationSettings,
    )
    from physics.flight_sim import FlightSimulator

    # High roll rate near ground: CG vertical velocity is near zero,
    # but wingtip downward velocity v + omega x r exceeds crash threshold (> 5 m/s).
    ac = AircraftConfig(
        mass_kg=0.006,
        wingspan_m=0.30,
        wing_area_m2=0.024,
        aspect_ratio=3.75,
        keel_height_m=0.015,
    )
    env = EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0)
    lnc = LaunchConfig(
        launch_speed=2.0,
        launch_angle_deg=0.0,
        launch_roll_deg=0.0,
        launch_yaw_deg=0.0,
        launch_height_m=0.05,
        throw_wrist_spin_radps=60.0,  # omega_x * semispan = 60 * 0.15 = 9.0 m/s
        throw_pitch_rate_radps=0.0,
        throw_yaw_rate_radps=0.0,
    )
    sim = FlightSimulator(
        aircraft=ac,
        environment=env,
        launch=lnc,
        settings=SimulationSettings(dt_max=0.005, duration_s=0.5, adaptive=True),
    )
    sim.simulate(duration_s=0.5)
    cpu_metrics = sim.metrics()

    params = gpu_dynamics.build_params(np, [ac], env)
    y0 = np.stack([gpu_dynamics.launch_state_vector(lnc, env, ac)], axis=0)
    batch = gpu_dynamics.simulate_batch(
        np,
        params,
        y0,
        np.asarray([0.5], dtype=np.float64),
        dt_max=0.005,
        max_steps=500,
        max_steps_per_case=500,
        use_graph=False,
    )
    cpu_crashed = bool(cpu_metrics.get("crashed", False))
    gpu_crashed = bool(batch["crashed"][0])
    assert cpu_crashed is True, f"Expected CPU crash from high roll-rate wingtip strike, got {cpu_metrics}"
    assert gpu_crashed is True, (
        f"GPU missed rotational lever-arm omega x r impact velocity: "
        f"cpu_crashed={cpu_crashed}, gpu_crashed={gpu_crashed}, gpu_landed={bool(batch['landed'][0])}"
    )


# ---------------------------------------------------------------------------
# C-03 [S1]: parallel_engine GPU path silently ignores unsupported flags
# ---------------------------------------------------------------------------
def test_c03_parallel_engine_does_not_silently_ignore_unsupported_gpu_flags():
    from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig
    from physics.parallel_engine import evaluate_cases_on_device

    cases = [
        {
            "aircraft": AircraftConfig(aeroelastic_compliance=0.5),
            "environment": EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0),
            "launch": LaunchConfig(launch_speed=8.0, launch_height_m=1.5),
            "duration_s": 0.5,
        },
        {
            "aircraft": AircraftConfig(unsteady_aero_enabled=True),
            "environment": EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0),
            "launch": LaunchConfig(launch_speed=8.0, launch_height_m=1.5),
            "duration_s": 0.5,
        },
        {
            "aircraft": AircraftConfig(dynamic_damping_enabled=True),
            "environment": EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0),
            "launch": LaunchConfig(launch_speed=8.0, launch_height_m=1.5),
            "duration_s": 0.5,
        },
        {
            "aircraft": AircraftConfig(fidelity_mode="improved_medium"),
            "environment": EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0),
            "launch": LaunchConfig(launch_speed=8.0, launch_height_m=1.5),
            "duration_s": 0.5,
        },
    ]
    results = evaluate_cases_on_device(cases, np, backend="numpy")
    for idx, res in enumerate(results):
        assert res["approximate"] is True, (
            f"Case {idx} with GPU-unsupported physics flag returned approximate=False silently"
        )
        assert res["physics_verification_eligible"] is False
        assert len(res.get("limitations", [])) > 0


# ---------------------------------------------------------------------------
# C-04 [S1]: _vector_drag_coefficient post-stall drag double counting
# ---------------------------------------------------------------------------
def test_c04_vector_drag_coefficient_no_post_stall_double_counting():
    from physics.aero_kernel import drag_coefficient as kernel_drag_coefficient
    from physics.parallel_engine import _vector_drag_coefficient

    alpha_deg = 35.0
    stall_alpha_deg = 14.0
    cd0 = 0.03
    oswald_e = 0.75
    ar = 2.0
    polar_constant = math.pi * oswald_e * ar
    k_induced = 1.0 / polar_constant
    cl = 0.55
    ge = 1.0

    from physics.aero_kernel import lift_coefficient as kernel_lift_coefficient

    cl_val = float(
        kernel_lift_coefficient(
            np,
            alpha_deg=np.asarray([alpha_deg]),
            cL0=0.0,
            slope_per_rad=2.5,
            stall_alpha_deg=stall_alpha_deg,
            sharpness=1.4,
            cl_max=0.9,
        )[0]
    )
    cd_vec = float(
        _vector_drag_coefficient(
            np,
            np.asarray([math.radians(alpha_deg)]),
            cd0=cd0,
            k_induced=k_induced,
            cl=np.asarray([cl_val]),
            ge_factor=np.asarray([ge]),
            stall_alpha_rad=math.radians(stall_alpha_deg),
        )[0]
    )
    cd_kernel = float(
        kernel_drag_coefficient(
            np,
            alpha_deg=np.asarray([alpha_deg]),
            cL0=0.0,
            slope_per_rad=2.5,
            stall_alpha_deg=stall_alpha_deg,
            sharpness=1.4,
            cl_max=0.9,
            cd0=cd0,
            reynolds=np.asarray([1.0e5]),
            re_exponent=0.0,
            polar_constant=polar_constant,
            ground_ratio=np.asarray([ge]),
            mach=np.asarray([0.0]),
            compressible=False,
        )[0]
    )
    assert math.isclose(cd_vec, cd_kernel, rel_tol=1e-6), (
        f"_vector_drag_coefficient={cd_vec:.6f} != aero_kernel={cd_kernel:.6f} "
        f"(rel_err={(cd_vec - cd_kernel) / cd_kernel:+.2%})"
    )


# ---------------------------------------------------------------------------
# C-05 [S1]: drag_polar missing _guard_fold_increment & trim_alpha_from_elevon sea-level hardcode
# ---------------------------------------------------------------------------
def test_c05_drag_polar_fold_guard_and_trim_alpha_atmosphere():
    from physics.aero_domain import drag_polar, evaluate_aero, trim_alpha_from_elevon
    from physics.config_schema import AircraftConfig, EnvironmentConfig

    # 1. drag_polar must apply _guard_fold_increment consistent with evaluate_aero
    cfg_fold = AircraftConfig(
        fold_trailing_deg=45.0,
        cl_max=0.20,  # small cl_max so raw te_fold dcl exceeds cl_max
        low_re_enabled=False,
    )
    env_std = EnvironmentConfig(ground_effect_enabled=False, compressibility_enabled=False)
    polar = drag_polar(cfg_fold, alpha_min_deg=0.0, alpha_max_deg=10.0, points=11, reynolds=1.0e5)
    # Both should agree on guarded cl at alpha=0 (modulo Re/mach=0; use exact Re match)
    from physics.aero_kernel import _guard_fold_increment
    from physics.aero_domain import trailing_edge_fold_effects, elevon_effects, lift_coefficient

    raw_te = trailing_edge_fold_effects(cfg_fold)
    guarded_te = _guard_fold_increment(np, raw_te, cfg_fold.cl_max)
    assert abs(float(raw_te["dcl"])) > cfg_fold.cl_max, "Precondition: raw te_fold dcl must exceed cl_max"
    expected_cl0 = lift_coefficient(
        0.0,
        cfg_fold,
        compressible=False,
        mach=0.0,
        cl_offset=elevon_effects(cfg_fold.elevon_deg, cfg_fold)["dcl"] + float(guarded_te["dcl"]),
        reynolds=1.0e5,
    )
    assert math.isclose(polar["cl"][0], expected_cl0, rel_tol=1e-6), (
        f"drag_polar cl[0]={polar['cl'][0]:.6f} != guarded expected_cl0={expected_cl0:.6f}"
    )

    # 2. trim_alpha_from_elevon must respect env atmosphere (density, kinematic_viscosity, speed_of_sound)
    cfg_low_re = AircraftConfig(
        low_re_enabled=True,
        low_re_cl_alpha_gain=0.20,
        low_re_stall_gain=0.20,
        elevon_deg=5.0,
        cm0=0.01,
        cm_alpha=-0.35,
    )
    env_sea = EnvironmentConfig(isa_mode=True, altitude_m=0.0, temperature_K=288.15, pressure_Pa=101325.0)
    env_high = EnvironmentConfig(isa_mode=True, altitude_m=8000.0, temperature_K=236.15, pressure_Pa=35600.0)
    a_sea = trim_alpha_from_elevon(cfg_low_re, 5.0, speed_mps=4.0, env=env_sea)
    a_high = trim_alpha_from_elevon(cfg_low_re, 5.0, speed_mps=4.0, env=env_high)
    assert not math.isclose(a_sea, a_high, abs_tol=1e-5), (
        f"trim_alpha_from_elevon ignored env atmosphere: sea={a_sea}, high={a_high}"
    )


# ---------------------------------------------------------------------------
# C-06 [S0]: _collar_crease inverted bisector breaks all parametric airframes
# ---------------------------------------------------------------------------
def test_c06_collar_crease_direction_and_parametric_chord_bounds():
    from geometry.origami_domain import SHEET_LENGTH_M, _collar_crease, _corner_crease
    from geometry.origami_genome import fold_genome
    from geometry.v3_origami.adapter import OrigamiAdapter

    corner_l = _corner_crease(0.75)
    collar_l = _collar_crease(corner_l[0], corner_l[1])
    # Collar crease on the left half must stay in u <= 0.5 and extend rearward (v < 1.0)
    assert collar_l[0][0] <= 0.5 + 1e-9 and collar_l[1][0] <= 0.5 + 1e-9
    assert min(collar_l[0][1], collar_l[1][1]) < 0.99, (
        f"_collar_crease did not extend rearward into sheet: {collar_l}"
    )

    adapter = OrigamiAdapter()
    cand = adapter.compile_candidate(ParametricOrigamiConfig(), candidate_id="c06-test")
    assert cand.geometry is not None
    assert cand.geometry.chord_m <= SHEET_LENGTH_M + 1e-6, (
        f"Folded chord_m={cand.geometry.chord_m:.4f}m exceeds A4 sheet length {SHEET_LENGTH_M:.4f}m"
    )
    folded = fold_genome(cand.raw_genome, paper_gsm=cand.config.paper_gsm)
    collar_l_faces = [f for f in folded.faces if f.group == "collar_l"]
    assert collar_l_faces, "collar_l faces missing"
    assert max(pt[2] for f in collar_l_faces for pt in f.poly3d) <= 1e-6, (
        "collar_l crossed centerline z=0"
    )
    assert cand.geometry.nose_tip_x_m <= SHEET_LENGTH_M * 0.5 + 1e-6


# ---------------------------------------------------------------------------
# C-07 [S0]: detect_penetrations X-crossing (edge-piercing) false negative
# ---------------------------------------------------------------------------
def test_c07_detect_penetrations_catches_x_crossing():
    from geometry.origami_genome import FoldedFace, detect_penetrations

    # Two perpendicular rectangles crossing in an X: no vertex of A is inside B and vice versa.
    face_a = FoldedFace(
        fid=1,
        poly3d=[
            (-0.05, 0.0, -0.01),
            (0.05, 0.0, -0.01),
            (0.05, 0.0, 0.01),
            (-0.05, 0.0, 0.01),
        ],
        normal=(0.0, 1.0, 0.0),
        front=(1.0, 0.0, 0.0),
        level=0,
        group="a",
        role="wing",
        parent=None,
        centroid3d=(0.0, 0.0, 0.0),
        area_m2=0.004,
    )
    face_b = FoldedFace(
        fid=2,
        poly3d=[
            (0.0, -0.05, -0.02),
            (0.0, 0.05, -0.02),
            (0.0, 0.05, 0.02),
            (0.0, -0.05, 0.02),
        ],
        normal=(1.0, 0.0, 0.0),
        front=(0.0, 1.0, 0.0),
        level=0,
        group="b",
        role="wing",
        parent=None,
        centroid3d=(0.0, 0.0, 0.0),
        area_m2=0.004,
    )
    hits = detect_penetrations([face_a, face_b], adjacency=())
    assert len(hits) >= 1, "detect_penetrations missed X-crossing edge penetration"


# ---------------------------------------------------------------------------
# C-08 [S0]: detect_strict_self_intersections ignores polygons argument
# ---------------------------------------------------------------------------
def test_c08_detect_strict_self_intersections_respects_polygons_arg():
    from geometry.origami_genome import FoldedFace
    from geometry.v3_origami.adapter import detect_strict_self_intersections

    # Raw poly3d are separated (no intersection), but supplied polygons have an X-crossing
    face_a = FoldedFace(
        fid=10,
        poly3d=[
            (-0.05, 0.0, 0.01),
            (0.05, 0.0, 0.01),
            (0.05, 0.0, 0.03),
            (-0.05, 0.0, 0.03),
        ],
        normal=(0.0, 1.0, 0.0),
        front=(1.0, 0.0, 0.0),
        level=0,
        group="a",
        role="wing",
        parent=None,
        centroid3d=(0.0, 0.0, 0.02),
        area_m2=0.004,
    )
    face_b = FoldedFace(
        fid=20,
        poly3d=[
            (0.0, 0.20, 0.0),
            (0.0, 0.30, 0.0),
            (0.0, 0.30, 0.04),
            (0.0, 0.20, 0.04),
        ],
        normal=(1.0, 0.0, 0.0),
        front=(0.0, 1.0, 0.0),
        level=0,
        group="b",
        role="wing",
        parent=None,
        centroid3d=(0.0, 0.25, 0.02),
        area_m2=0.004,
    )
    # Pass separated polygons that DO cross in an X, while raw poly3d do NOT
    crossing_polys = {
        10: list(face_a.poly3d),
        20: [
            (0.0, -0.05, 0.0),
            (0.0, 0.05, 0.0),
            (0.0, 0.05, 0.04),
            (0.0, -0.05, 0.04),
        ],
    }
    hits_crossing = detect_strict_self_intersections(
        [face_a, face_b], adjacency=(), polygons=crossing_polys
    )
    assert len(hits_crossing) >= 1, "detect_strict_self_intersections ignored polygons arg (missed crossing)"

    # Also test reverse: raw poly3d cross, but separated polygons do NOT cross
    face_b_raw_cross = FoldedFace(
        fid=20,
        poly3d=list(crossing_polys[20]),
        normal=face_b.normal,
        front=(0.0, 1.0, 0.0),
        level=0,
        group="b",
        role="wing",
        parent=None,
        centroid3d=(0.0, 0.0, 0.02),
        area_m2=0.004,
    )
    separated_polys = {
        10: list(face_a.poly3d),
        20: list(face_b.poly3d),
    }
    hits_sep = detect_strict_self_intersections(
        [face_a, face_b_raw_cross], adjacency=(), polygons=separated_polys
    )
    assert len(hits_sep) == 0, "detect_strict_self_intersections ignored polygons arg (false positive)"


# ---------------------------------------------------------------------------
# C-09 [S1]: FoldStep.to_dict crease_line_mm missing * 1000.0
# ---------------------------------------------------------------------------
def test_c09_fold_step_to_dict_crease_line_mm_units():
    from geometry.origami_domain import SHEET_LENGTH_M, SHEET_WIDTH_M
    from geometry.origami_genome import FoldStep

    step = FoldStep(
        step_index=0,
        crease_line=((0.5, 0.0), (0.5, 1.0)),
        fold_angle_deg=90.0,
    )
    d = step.to_dict()
    assert math.isclose(d["crease_line_mm"][0][0], 0.5 * SHEET_WIDTH_M * 1000.0, rel_tol=1e-6)
    assert math.isclose(d["crease_line_mm"][1][1], 1.0 * SHEET_LENGTH_M * 1000.0, rel_tol=1e-6)


# ---------------------------------------------------------------------------
# C-10 [S1]: A4 pattern (compiler.py) vs folding instructions (kernel_25d.py) mismatch
# ---------------------------------------------------------------------------
def test_c10_compiler_and_kernel_25d_consistency():
    from geometry.v3_origami.compiler import ParametricOrigamiCompiler
    from geometry.v3_origami.kernel_25d import compile_macro_origami

    # 1. has_winglet=False must NOT emit winglet_folds macro step
    cfg_no_wl = ParametricOrigamiConfig(
        wing=ParametricWingSpec(has_winglet=False, elevon_left_deg=-15.0, elevon_right_deg=-15.0)
    )
    macro_steps, _ = compile_macro_origami(cfg_no_wl)
    macro_names = [s.name for s in macro_steps]
    assert "winglet_folds" not in macro_names, "kernel_25d emitted winglet_folds when has_winglet=False"

    # 2. elevon clamp in kernel_25d must match compiler [-15, 15]
    elevon_step = next(s for s in macro_steps if s.name == "elevon_reflex_trim")
    assert math.isclose(elevon_step.angle_deg, 15.0, rel_tol=1e-6), (
        f"kernel_25d clamped elevon=-15 deg to {elevon_step.angle_deg} instead of 15.0"
    )

    # 3. compiler.py elevon_l / elevon_r creases must be split to left/right halves
    cfg_asym = ParametricOrigamiConfig(
        wing=ParametricWingSpec(elevon_left_deg=6.0, elevon_right_deg=-4.0)
    )
    steps = ParametricOrigamiCompiler().compile_steps(cfg_asym)
    elevon_l = next(s for s in steps if s.label == "elevon_l")
    elevon_r = next(s for s in steps if s.label == "elevon_r")
    assert elevon_l.crease_line[0][0] <= 0.5 and elevon_l.crease_line[1][0] <= 0.5, (
        f"elevon_l spans full width: {elevon_l.crease_line}"
    )
    assert elevon_r.crease_line[0][0] >= 0.5 and elevon_r.crease_line[1][0] >= 0.5, (
        f"elevon_r spans full width: {elevon_r.crease_line}"
    )


# ---------------------------------------------------------------------------
# C-11 [S0]: v3/ submodule split-brain must be unified with canonical modules
# ---------------------------------------------------------------------------
def test_c11_v3_submodules_unified_with_canonical():
    import geometry.v3_origami.adapter as canon_adapter
    import geometry.v3_origami.compiler as canon_compiler
    import geometry.v3_origami.mcts as canon_mcts
    import optimization.eval.robustness as canon_robustness
    import optimization.map_elites.engine as canon_engine
    import optimization.map_elites.grid as canon_grid
    import optimization.trim.nelder_mead as canon_nm
    import physics.benchmark.benchmark as canon_bench
    import v3.eval.robustness as v3_robustness
    import v3.origami.adapter as v3_adapter
    import v3.origami.compiler as v3_compiler
    import v3.origami.mcts as v3_mcts
    import v3.physics.benchmark as v3_bench
    import v3.search.engine as v3_engine
    import v3.search.grid as v3_grid
    import v3.trim.nelder_mead as v3_nm

    assert v3_grid.MAPElitesGridArchive is canon_grid.MAPElitesGridArchive
    assert v3_engine.MAPElitesEngine is canon_engine.MAPElitesEngine
    assert v3_mcts.MCTSOrigamiGenerator is canon_mcts.MCTSOrigamiGenerator
    assert v3_compiler.ParametricOrigamiCompiler is canon_compiler.ParametricOrigamiCompiler
    assert v3_adapter.OrigamiAdapter is canon_adapter.OrigamiAdapter
    assert v3_adapter.validate_tier0 is canon_adapter.validate_tier0
    assert v3_nm.NelderMeadTrimOptimizer is canon_nm.NelderMeadTrimOptimizer
    assert v3_robustness.RobustnessEvaluator is canon_robustness.RobustnessEvaluator
    assert v3_bench.DeterministicFlightBenchmark is canon_bench.DeterministicFlightBenchmark


# ---------------------------------------------------------------------------
# C-12 [S0]: NaN / out-of-range config params bypass Tier 0 & physics clamp
# ---------------------------------------------------------------------------
def test_c12_nan_and_out_of_range_config_rejected_and_clamped():
    from geometry.v3_origami.adapter import OrigamiAdapter, validate_tier0
    from geometry.v3_origami.compiler import _clamp
    from physics.benchmark.benchmark import _build_aircraft_config

    # 1. _clamp must never return NaN/Inf
    clamped_nan = _clamp(float("nan"), -15.0, 15.0)
    assert math.isfinite(clamped_nan) and -15.0 <= clamped_nan <= 15.0

    # 2. NaN elevon / NaN dihedral / ultra-low paper_gsm must fail Tier 0
    adapter = OrigamiAdapter()
    for bad_cfg in [
        ParametricOrigamiConfig(wing=ParametricWingSpec(elevon_left_deg=float("nan"))),
        ParametricOrigamiConfig(wing=ParametricWingSpec(dihedral_deg=float("nan"))),
        ParametricOrigamiConfig(paper_gsm=0.01),
        ParametricOrigamiConfig(wing=ParametricWingSpec(elevon_left_deg=90.0)),
    ]:
        cand = adapter.compile_candidate(bad_cfg, candidate_id="c12-bad")
        ok, reasons = validate_tier0(cand)
        assert ok is False, f"Tier 0 accepted invalid config {bad_cfg}: reasons={reasons}"

    # 3. _build_aircraft_config must clamp elevon_deg and dihedral_deg & reject NaN
    good_cand = adapter.compile_candidate(ParametricOrigamiConfig(), candidate_id="c12-good")
    extreme_cand = AircraftCandidate(
        candidate_id="c12-extreme",
        step_count=16,
        config=ParametricOrigamiConfig(
            wing=ParametricWingSpec(elevon_left_deg=80.0, elevon_right_deg=80.0, dihedral_deg=60.0)
        ),
        geometry=good_cand.geometry,
    )
    ac = _build_aircraft_config(extreme_cand)
    assert -15.0 <= ac.elevon_deg <= 15.0, f"Unclamped elevon_deg={ac.elevon_deg}"
    assert -5.0 <= ac.dihedral_deg <= 15.0, f"Unclamped dihedral_deg={ac.dihedral_deg}"


# ---------------------------------------------------------------------------
# C-13 [S0]: geometry=None must NOT fall back to default aircraft
# ---------------------------------------------------------------------------
def test_c13_none_geometry_disqualified_immediately():
    from optimization.eval.robustness import RobustnessEvaluator
    from physics.benchmark.benchmark import DeterministicFlightBenchmark

    cand = AircraftCandidate(
        candidate_id="c13-none-geom",
        config=ParametricOrigamiConfig(),
        step_count=0,
        geometry=None,
    )
    bench = DeterministicFlightBenchmark(duration_s=2.0)
    res = bench.evaluate_candidate(cand)
    assert res.valid is False
    assert res.composite_score == 0.0

    rob = RobustnessEvaluator(duration_s=1.0, n_samples=2)
    rep = rob.evaluate_candidate(cand)
    assert rep.overall_robustness_score == 0.0


# ---------------------------------------------------------------------------
# C-14 [S0]: NaN penalty bypass in EvaluationMetric & benchmark.py
# ---------------------------------------------------------------------------
def test_c14_nan_metrics_fail_closed():
    from physics.benchmark.benchmark import evaluate_horizontal_glide
    from physics.config_schema import AircraftConfig

    base_kwargs = dict(
        speed_mps=6.0,
        flight_time_s=2.5,
        max_ld_ratio=5.0,
        steady_glide_fraction=0.8,
        stability=0.7,
        distance_m=12.0,
        max_altitude_m=1.5,
        landed=True,
        crashed=False,
        diverged=False,
        status="landed",
        bounce_count=0,
        max_abs_roll_deg=5.0,
        ground_skim_fraction=0.05,
    )
    for field_name in ("distance_m", "max_abs_roll_deg", "ground_skim_fraction"):
        kw = dict(base_kwargs)
        kw[field_name] = float("nan")
        m = EvaluationMetric(**kw)
        assert m.deterministic_score == 0.0, (
            f"EvaluationMetric with {field_name}=NaN returned {m.deterministic_score}"
        )

    # benchmark.py evaluate_horizontal_glide must mark diverged=True on NaN roll/skim
    fake_metrics = {
        "flight_time_s": 2.5,
        "distance_m": 12.0,
        "max_altitude_m": 1.5,
        "max_ld_ratio": 5.0,
        "crashed": False,
        "diverged": False,
        "landed": True,
        "status": "landed",
        "max_abs_roll_deg": float("nan"),
        "ground_skim_fraction": 0.05,
    }
    with patch("physics.benchmark.benchmark.FlightSimulator") as mock_sim_cls:
        inst = mock_sim_cls.return_value
        inst.simulate.return_value = None
        inst.metrics.return_value = fake_metrics
        inst.history.return_value = []
        em = evaluate_horizontal_glide(AircraftConfig(), speed_mps=6.0, duration_s=1.0)
        assert em.diverged is True
        assert em.deterministic_score == 0.0


# ---------------------------------------------------------------------------
# C-15 [S1]: MAPElitesGridArchive.add_candidate descriptor cache spoofing
# ---------------------------------------------------------------------------
def test_c15_map_elites_recomputes_descriptor_against_spoofing():
    from optimization.map_elites.grid import MAPElitesGridArchive

    archive = MAPElitesGridArchive(
        bins=(5, 5, 5),
        ranges=((0.10, 0.30), (0.20, 0.90), (0.50, 1.50)),
        reject_out_of_bounds=True,
        use_planform_bd=True,
    )
    # Real geometry has planform_aspect_ratio = 5.0 (way out of bounds [0.50, 1.50])
    oob_geom = AirframeGeometry(
        mass_kg=0.005,
        wing_area_m2=0.020,
        wingspan_m=0.20,
        chord_m=0.10,
        aspect_ratio=2.0,
        cg_m=(0.08, 0.0, 0.005),
        keel_height_m=0.015,
        keel_length_m=0.08,
        keel_layers=4,
        step_count=16,
        planform_area_m2=0.008,
        planform_aspect_ratio=5.0,
        penetrations=0,
        fold_valid=True,
    )
    metric = EvaluationMetric(
        speed_mps=6.0,
        flight_time_s=2.0,
        max_ld_ratio=5.0,
        steady_glide_fraction=0.8,
        stability=0.6,
        distance_m=10.0,
        max_altitude_m=1.5,
        landed=True,
        crashed=False,
        diverged=False,
        status="landed",
    )
    bench = FlightBenchmarkResult(
        low_speed=metric,
        mid_speed=metric,
        composite_score=metric.deterministic_score,
        valid=True,
    )
    # Spoofed in-bounds descriptor
    spoofed_desc = BehaviorDescriptor(
        wing_loading_kg_m2=0.20,
        static_stability=0.60,
        aspect_ratio=1.00,
    )
    cand = AircraftCandidate(
        candidate_id="c15-spoof",
        config=ParametricOrigamiConfig(),
        step_count=16,
        geometry=oob_geom,
        benchmark=bench,
        descriptor=spoofed_desc,
        metadata={"tier0_valid": True},
    )
    accepted = archive.add_candidate(cand)
    assert accepted is False, "add_candidate accepted out-of-bounds geometry via spoofed descriptor cache"


# ---------------------------------------------------------------------------
# C-16 [S1]: NelderMeadTrimOptimizer score regression on asymmetric elevon candidate
# ---------------------------------------------------------------------------
def test_c16_nelder_mead_no_regression_on_asymmetric_elevon_candidate():
    from geometry.v3_origami.adapter import OrigamiAdapter
    from optimization.trim.nelder_mead import NelderMeadTrimOptimizer
    from physics.benchmark.benchmark import DeterministicFlightBenchmark

    adapter = OrigamiAdapter()
    cfg_asym = ParametricOrigamiConfig(
        wing=ParametricWingSpec(elevon_left_deg=4.0, elevon_right_deg=-2.0, dihedral_deg=5.0)
    )
    cand = adapter.compile_candidate(cfg_asym, candidate_id="c16-asym")

    # Give candidate an existing high valid benchmark score (higher than symmetric trim can achieve)
    high_metric = EvaluationMetric(
        speed_mps=6.0,
        flight_time_s=5.0,
        max_ld_ratio=12.0,
        steady_glide_fraction=0.95,
        stability=0.9,
        distance_m=30.0,
        max_altitude_m=1.5,
        landed=True,
        crashed=False,
        diverged=False,
        status="landed",
    )
    original_score = 99.0
    cand.benchmark = FlightBenchmarkResult(
        low_speed=high_metric,
        mid_speed=high_metric,
        composite_score=original_score,
        valid=True,
    )

    class _SyntheticBench:
        def evaluate_candidate(self, c):
            # Score increases slightly with elevon so NM improves over simplex[0] (10.0 -> 15.0),
            # yet remains far below original_score=99.0.
            sc = 10.0 + abs(c.config.wing.elevon_left_deg - 1.0)
            m = EvaluationMetric(
                speed_mps=6.0, flight_time_s=2.0, max_ld_ratio=5.0,
                steady_glide_fraction=0.8, stability=0.7, distance_m=10.0,
                max_altitude_m=1.5, landed=True, crashed=False, diverged=False, status="landed",
            )
            res = FlightBenchmarkResult(low_speed=m, mid_speed=m, composite_score=sc, valid=True)
            c.benchmark = res
            return res

    opt = NelderMeadTrimOptimizer(
        benchmark=_SyntheticBench(),
        adapter=adapter,
        max_iter=2,
        max_restarts=0,
    )
    opt.optimize_trim(cand)
    assert cand.benchmark.composite_score >= original_score, (
        f"NelderMead regressed candidate score from {original_score} to {cand.benchmark.composite_score}"
    )
    assert math.isclose(cand.config.wing.elevon_left_deg, 4.0)
    assert math.isclose(cand.config.wing.elevon_right_deg, -2.0)
