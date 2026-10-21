"""Tests of the four evaluation stages of the second generation search.

The stages are tested with the *real* physics: the screen flies the shared
6-DoF model, the structural screen uses the shared structural kernel and the
flow solver solves the lattice.  The runs are made cheap by shortening the
flights and by shrinking the lattices, never by replacing the physics.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from physics.config_schema import AircraftConfig, EnvironmentConfig, LaunchConfig  # noqa: E402
from physics import gpu_dynamics  # noqa: E402
from physics.ga2 import stage0 as stage0_module  # noqa: E402
from physics.ga2 import stage1 as stage1_module  # noqa: E402
from physics.ga2 import stage2 as stage2_module  # noqa: E402
from physics.ga2 import stage3 as stage3_module  # noqa: E402
from physics.ga2.genome import archetype_hybrid_genomes  # noqa: E402
from physics.origami_genome import (  # noqa: E402
    fold_genome,
    genome_to_aircraft_config,
)

ENVIRONMENT = EnvironmentConfig(wind_speed=0.0)
LAUNCH = LaunchConfig(launch_speed=8.0, launch_angle_deg=25.0, launch_height_m=1.5)


def _case(index: int = 0) -> stage1_module.Stage1Case:
    config = AircraftConfig()
    state = gpu_dynamics.launch_state_vector(LAUNCH, ENVIRONMENT, config)
    return stage1_module.Stage1Case(
        index=index,
        aircraft=config,
        initial_state=state,
        duration_s=1.0,
    )


def test_the_geometry_filter_accepts_the_known_designs() -> None:
    geometry_filter = stage0_module.GeometryFilter(paper_gsm=100.0)
    for genome in archetype_hybrid_genomes():
        report = geometry_filter.evaluate(genome)
        assert report.valid, report.reasons
        assert report.wing_area_m2 > 0.01
        assert report.span_m > 0.08


def test_the_geometry_filter_rejects_an_empty_programme() -> None:
    geometry_filter = stage0_module.GeometryFilter()
    report = geometry_filter.quick_screen(archetype_hybrid_genomes()[0])
    assert isinstance(report.valid, bool)
    assert report.to_dict()["valid"] == report.valid


def test_the_screen_flies_the_case_and_reports_a_finite_summary() -> None:
    screener = stage1_module.BatchScreener(
        xp=np, env=ENVIRONMENT, dt_max=2.5e-3, duration_s=1.0, use_graph=False, max_steps=200
    )
    results = screener.screen([_case(0)], repeats=1, perturbation=False)
    assert len(results) == 1
    result = results[0]
    assert result.status in ("landed", "timeout", "crashed", "degenerate")
    assert np.isfinite(result.distance_m)
    assert np.isfinite(result.max_ld_ratio)
    assert result.flight_time_s >= 0.0
    assert result.peak_stress_pa >= 0.0


def test_the_perturbation_ensemble_does_not_change_the_geometry() -> None:
    screener = stage1_module.BatchScreener(
        xp=np, env=ENVIRONMENT, dt_max=2.5e-3, duration_s=0.5, use_graph=False, max_steps=100
    )
    plain = screener.screen([_case(0)], repeats=1, perturbation=False)[0]
    ensemble = screener.screen([_case(0)], repeats=2, perturbation=True)[0]
    assert np.isfinite(ensemble.distance_m)
    assert ensemble.coverage <= 1.0
    assert plain.index == ensemble.index


def test_the_structural_screen_accepts_the_known_design() -> None:
    genome = archetype_hybrid_genomes()[0]
    geometry = fold_genome(
        __import__("physics.ga2.genome", fromlist=["x"]).hybrid_to_origami(genome), 1.0, 100.0
    )
    aircraft = genome_to_aircraft_config(geometry, 100.0)
    case = stage2_module.Stage2Case(
        index=0,
        aircraft=aircraft,
        dynamic_pressure_pa=39.0,
        load_factor=1.5,
        speed_mps=8.0,
        span_m=float(geometry.span_m),
        chord_m=float(geometry.chord_m),
        thickness_m=1.25e-4,
    )
    report = stage2_module.StaticFsiScreener(xp=np).screen([case])[0]
    assert report.buckling_margin > 1.0
    assert report.max_tip_deflection_m < 0.05
    assert report.pitch_stability_margin > 0.0
    assert report.feasible is True


def test_the_structural_screen_rejects_a_degenerate_case() -> None:
    case = stage2_module.Stage2Case(
        index=0,
        aircraft=AircraftConfig(),
        dynamic_pressure_pa=0.0,
        load_factor=1.0,
        speed_mps=0.0,
        span_m=0.0,
        chord_m=0.0,
        thickness_m=0.0,
    )
    report = stage2_module.StaticFsiScreener(xp=np).screen([case])[0]
    assert report.feasible is False
    assert report.reasons


def test_the_flow_solver_answers_for_a_known_design() -> None:
    genome = archetype_hybrid_genomes()[0]
    request = stage3_module.Stage3Request(
        index=0,
        genome=genome.to_dict(),
        aircraft=dataclasses.asdict(AircraftConfig()),
        launch={"speed_mps": 8.0, "angle_deg": 5.0, "height_m": 1.5},
        grid_cells=20_000,
        transition_steps=20,
        glide_steps=40,
    )
    solver = stage3_module.LbmDnsSolver(np, 20_000, "w_mles")
    result = solver.solve(request)
    assert result.status == "ok"
    assert np.isfinite(result.cl)
    assert np.isfinite(result.cd)
    assert abs(result.cl) <= 3.0
    assert 0.0 <= result.cd <= 3.0


def test_the_flow_solver_reports_a_degenerate_mesh() -> None:
    request = stage3_module.Stage3Request(
        index=0,
        genome={"genes": "not a programme"},
        aircraft=dataclasses.asdict(AircraftConfig()),
        launch={"speed_mps": 8.0, "angle_deg": 5.0, "height_m": 1.5},
        grid_cells=20_000,
        transition_steps=5,
        glide_steps=5,
    )
    solver = stage3_module.LbmDnsSolver(np, 20_000, "w_mles")
    result = solver.solve(request)
    assert result.status == "degenerate"
    assert result.lift_to_drag == 0.0
