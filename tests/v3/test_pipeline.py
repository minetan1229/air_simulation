"""Comprehensive end-to-end integration and V&V tests for v3 architecture."""

import glob
from pathlib import Path
import pytest
from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig
from physics.flight_sim import FlightSimulator
from v3.interfaces import (
    AircraftCandidate,
    LAUNCH_HEIGHT_M,
    LAUNCH_PITCH_DEG,
    MIN_FOLD_STEPS,
    ParametricOrigamiConfig,
)
from v3.origami.adapter import OrigamiAdapter, validate_tier0
from v3.origami.compiler import ParametricOrigamiCompiler
from v3.physics.benchmark import (
    DeterministicFlightBenchmark,
    evaluate_horizontal_glide,
)


def test_determinism_exact_match():
    """Verify that evaluating the exact same candidate twice yields identical scores."""
    adapter = OrigamiAdapter()
    candidate = adapter.compile_candidate(ParametricOrigamiConfig())

    benchmark1 = DeterministicFlightBenchmark(duration_s=0.2)
    res1 = benchmark1.evaluate_candidate(candidate)

    benchmark2 = DeterministicFlightBenchmark(duration_s=0.2)
    res2 = benchmark2.evaluate_candidate(candidate)

    assert res1.low_speed.flight_time_s == pytest.approx(res2.low_speed.flight_time_s, abs=1.0e-7)
    assert res1.mid_speed.flight_time_s == pytest.approx(res2.mid_speed.flight_time_s, abs=1.0e-7)
    assert res1.composite_score == pytest.approx(res2.composite_score, abs=1.0e-7)


def test_strict_min_16_steps_enforcement():
    """Verify that candidates with fewer than 16 steps are strictly rejected by Tier 0."""
    compiler = ParametricOrigamiCompiler()
    cfg = ParametricOrigamiConfig()
    steps = compiler.compile_steps(cfg)
    assert len(steps) >= MIN_FOLD_STEPS

    adapter = OrigamiAdapter()
    cand = adapter.compile_candidate(cfg)

    # Force invalid step count
    cand.step_count = 15
    valid, reasons = validate_tier0(cand)
    assert valid is False
    assert any("below minimum requirement 16" in r for r in reasons)


def test_zoom_climb_stall_suppression():
    """Verify that zoom-climb trajectories are heavily penalized compared to steady glides."""
    # Steady glider
    steady_cfg = AircraftConfig(
        mass_kg=0.005,
        wing_area_m2=0.015,
        wingspan_m=0.18,
        aspect_ratio=2.16,
        dihedral_deg=3.0,
        elevon_deg=1.0,
    )
    metric_steady = evaluate_horizontal_glide(steady_cfg, speed_mps=6.0, duration_s=0.3)

    # High-pitch stall configuration
    stall_cfg = AircraftConfig(
        mass_kg=0.005,
        wing_area_m2=0.015,
        wingspan_m=0.18,
        aspect_ratio=2.16,
        dihedral_deg=0.0,
        elevon_deg=15.0,  # Excessive upward elevator causing zoom stall
    )
    metric_stall = evaluate_horizontal_glide(stall_cfg, speed_mps=6.0, duration_s=0.3)

    # Steady glide should achieve a higher deterministic score
    assert metric_steady.deterministic_score >= metric_stall.deterministic_score


def test_headless_gui_free_enforcement():
    """Verify no GUI libraries (tkinter, pyvista, OpenGL, pyplot.show) are imported in v3/."""
    v3_dir = Path("v3")
    py_files = list(v3_dir.rglob("*.py"))
    assert len(py_files) > 0

    prohibited_tokens = [
        "tkinter",
        "pyvista",
        "OpenGL",
        "matplotlib.pyplot",
        "plt.show",
    ]

    for py_file in py_files:
        content = py_file.read_text(encoding="utf-8")
        for token in prohibited_tokens:
            assert token not in content, f"Prohibited GUI token '{token}' found in {py_file}"


def test_ci_isolated_energy_conservation():
    """Verify total mechanical energy in quiescent air does not arbitrarily increase."""
    aircraft = AircraftConfig(
        mass_kg=0.005,
        wing_area_m2=0.015,
        wingspan_m=0.18,
        aspect_ratio=2.16,
        dihedral_deg=3.0,
        elevon_deg=1.0,
    )
    env = EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0)
    launch = LaunchConfig(launch_speed=8.0, launch_angle_deg=0.0, launch_height_m=1.5)

    sim = FlightSimulator(aircraft=aircraft, environment=env, launch=launch)
    sim.simulate(duration_s=0.4)
    samples = sim.history()

    assert len(samples) > 5

    # Total energy: E = 0.5 * m * v^2 + m * g * z
    m = aircraft.mass_kg
    g = 9.80665

    energies = [
        0.5 * m * (s.speed_mps ** 2) + m * g * s.altitude_m
        for s in samples
    ]

    # Without thrust or updraft, final energy must be lower than initial launch energy
    e_initial = energies[0]
    e_final = energies[-1]
    assert e_final <= e_initial * 1.05  # within numerical tolerance
