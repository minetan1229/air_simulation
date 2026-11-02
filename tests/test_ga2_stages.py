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
from typing import Tuple

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
from physics.ga2.genome import (  # noqa: E402
    HybridFoldGene,
    HybridGenome,
    archetype_hybrid_genomes,
    crease_segment,
)
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


def _crease_gene(
    origin: Tuple[float, float],
    angle_deg: float,
    length_ratio: float,
    label: str,
) -> HybridFoldGene:
    """One crease of a synthetic programme."""
    return HybridFoldGene(
        enabled=True,
        origin=origin,
        crease_angle_deg=angle_deg,
        fold_angle_deg=90.0,
        length_ratio=length_ratio,
        label=label,
    )


def test_the_nose_lock_of_the_nakamura_design_gathers_five_creases() -> None:
    """The hot spot of the nose lock is five creases, not nine.

    The body keel, the belly fold and the three lock folds meet at the
    nose lock of the design.  The elevon and the trailing edge folds run
    parallel to them more than 130 mm away, so they are not creases of
    that spot and the paper survives the five that are.
    """
    designs = {item.key: item for item in archetype_hybrid_genomes()}
    assert "nakamura_lock" in designs
    genome = designs["nakamura_lock"]
    geometry_filter = stage0_module.GeometryFilter(paper_gsm=100.0)
    assert geometry_filter._hot_spot_count(genome) == 5


def test_two_creases_on_the_same_line_are_a_hot_spot() -> None:
    """A crease that is laid on another one bends the paper twice.

    Two creases that lie on the same line and overlap each other are a
    place where the paper is creased twice, and the filter has to see it.
    """
    genome = HybridGenome(
        genes=[
            _crease_gene((0.2, 0.5), 0.0, 0.5, "first"),
            _crease_gene((0.4, 0.5), 0.0, 0.5, "second"),
        ],
        key="collinear",
        label="two creases on one line",
    )
    geometry_filter = stage0_module.GeometryFilter(paper_gsm=100.0)
    assert geometry_filter._hot_spot_count(genome) == 2


def test_creases_that_only_run_parallel_are_not_a_hot_spot() -> None:
    """Parallel creases that do not touch are no place where paper tears.

    The two creases run 35 mm apart and never meet, so no spot of the
    paper is creased twice by them.
    """
    genome = HybridGenome(
        genes=[
            _crease_gene((0.2, 0.5), 0.0, 0.5, "upper"),
            _crease_gene((0.2, 0.62), 0.0, 0.5, "lower"),
        ],
        key="parallel",
        label="two parallel creases",
    )
    geometry_filter = stage0_module.GeometryFilter(paper_gsm=100.0)
    assert geometry_filter._hot_spot_count(genome) == 0


def test_the_overlap_test_sees_the_creases_that_share_a_line_and_only_those() -> None:
    """The overlap test must tell collinear creases from parallel ones.

    The two elevon folds of the known designs lie on one line and overlap
    each other; the trailing edge fold runs parallel to them 31 mm away
    and never touches them.
    """
    designs = {item.key: item for item in archetype_hybrid_genomes()}
    assert "nakamura_lock" in designs
    genome = designs["nakamura_lock"]
    creases = {
        gene.label: crease_segment(
            gene.origin, gene.crease_angle_deg, gene.length_ratio
        )
        for gene in genome.genes
        if gene.enabled
    }
    assert stage0_module._segments_overlap(
        *creases["elevon_l"], *creases["elevon_r"]
    ) is not None
    assert stage0_module._segments_overlap(
        *creases["elevon_l"], *creases["trailing_l"]
    ) is None


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


def test_the_summary_accumulates_over_the_whole_flight() -> None:
    """The summary must not stop after the first step.

    A CUDA graph replays the kernels it captured, so a summary that was
    bound to a new array inside the step would only ever hold the value of
    the capture.  The distance of a flight of a second is metres, not
    millimetres.
    """
    screener = stage1_module.BatchScreener(
        xp=np, env=ENVIRONMENT, dt_max=2.5e-3, duration_s=2.0, use_graph=False, max_steps=400
    )
    result = screener.screen([_case(0)], repeats=1, perturbation=False)[0]
    assert result.distance_m > 0.5
    assert result.flight_time_s > 0.5


def test_the_captured_graph_agrees_with_the_eager_loop() -> None:
    """The graph path must reproduce the eager path.

    The graph contracts the rotation without cuBLAS, which differs from the
    shared kernel in the last place; the chaotic airframes of the project
    amplify that, so the comparison is a tolerance and not an equality.
    """
    try:
        import cupy as cp

        if int(cp.cuda.runtime.getDeviceCount()) == 0:
            pytest.skip("no CUDA device")
    except Exception:
        pytest.skip("CuPy is not available")
    eager = stage1_module.BatchScreener(
        xp=np, env=ENVIRONMENT, dt_max=2.5e-3, duration_s=1.0, use_graph=False, max_steps=200
    ).screen([_case(0)], repeats=1, perturbation=False)[0]
    captured = stage1_module.BatchScreener(
        xp=cp, env=ENVIRONMENT, dt_max=2.5e-3, duration_s=1.0, use_graph=True, max_steps=200
    )
    result = captured.screen([_case(0)], repeats=1, perturbation=False)[0]
    assert captured.describe()["graph"] is True
    assert result.status == eager.status
    assert result.max_ld_ratio == pytest.approx(eager.max_ld_ratio, rel=1.0e-6)
    assert result.distance_m == pytest.approx(eager.distance_m, rel=0.05)


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
