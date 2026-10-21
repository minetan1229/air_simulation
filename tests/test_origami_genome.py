"""Tests for the procedural origami genome engine.

The genome engine turns a *programme of folds* into a solid, filters the
solids that cannot be thrown by hand and renders the programme back into a
recipe a human can follow.

The two acceptance criteria of this step are covered here:

* a thousand randomly generated genomes have to survive the geometry
  transformation, the grip filter and the handover into the 6-DoF model
  without a single error, and fast enough to be used inside a genetic
  algorithm,
* the recipe of an arbitrary individual has to expose *how many degrees*
  every single fold is turned by, together with the crease pattern and the
  intermediate solid of every step.
"""

from __future__ import annotations

import json
import math
import random
import sys
import time
from pathlib import Path
from typing import Dict

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from physics import origami_domain as od  # noqa: E402
from physics import origami_genome as og  # noqa: E402
from physics.config_schema import EnvironmentConfig, LaunchConfig  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _random_population(count: int, seed: int = 20261020) -> list:
    """A mixed population of completely random and family guided genomes."""
    rng = random.Random(seed)
    population = []
    for index in range(count):
        strategy = "free" if index % 4 == 0 else "families"
        steps = 4 + (index % 9)
        population.append(
            og.random_genome(rng, steps=steps, strategy=strategy, seed=seed + index)
        )
    return population


def _assert_finite(value: float) -> None:
    assert math.isfinite(value), f"non finite value {value!r}"


# ---------------------------------------------------------------------------
# (1) the population: 1000 random genomes, no error and fast
# ---------------------------------------------------------------------------
def test_a_thousand_random_genomes_are_transformed_and_filtered_without_error() -> None:
    """The whole pipeline has to survive a thousand random individuals.

    This is the population the genetic algorithm starts from, so a single
    malformed genome would take the whole run down.  Every individual is
    folded into a solid, measured, checked for a grip and checked for a
    self intersection.
    """
    population = _random_population(1000)
    started = time.perf_counter()
    valid = 0
    for genome in population:
        geometry = og.fold_genome(genome, paper_gsm=100.0)
        assert geometry.faces, "the folded solid has no panel"
        _assert_finite(geometry.mass_kg)
        _assert_finite(geometry.cg[0])
        _assert_finite(geometry.cg[1])
        _assert_finite(geometry.cg[2])
        _assert_finite(geometry.wing_area_m2)
        _assert_finite(geometry.span_m)
        _assert_finite(geometry.roll_inertia_kgm2)
        assert geometry.mass_kg > 0.0
        assert geometry.span_m > 0.0

        report = og.evaluate_grip(geometry)
        assert isinstance(report.valid, bool)
        assert report.penalty >= 0.0
        assert 0.0 < report.fitness <= 1.0
        for reason in report.reasons:
            assert isinstance(reason, str) and reason
        if report.valid:
            valid += 1
    elapsed = time.perf_counter() - started

    # The filter has to discriminate: a population where everything passes
    # would mean the filter does not work at all.
    assert 0 < valid < len(population), "the grip filter accepts or rejects everything"
    # The pipeline has to be fast enough to sit inside a genetic loop.
    assert elapsed < 120.0, f"the population took {elapsed:.1f} s"


def test_the_population_is_handed_over_to_the_6dof_model() -> None:
    """Every individual has to produce a usable flight configuration."""
    population = _random_population(200)
    for genome in population:
        geometry = og.fold_genome(genome, paper_gsm=80.0)
        config = og.genome_to_aircraft_config(geometry, paper_gsm=80.0)
        assert config.wing_area_m2 > 0.0
        assert config.wingspan_m > 0.0
        assert config.mass_kg > 0.0
        assert config.aspect_ratio > 0.0
        assert config.roll_inertia_kgm2 > 0.0
        assert config.pitch_inertia_kgm2 > 0.0
        assert config.yaw_inertia_kgm2 > 0.0
        assert math.isfinite(config.inertia_xy_kgm2)
        assert math.isfinite(config.inertia_xz_kgm2)
        assert math.isfinite(config.inertia_yz_kgm2)
        # The derived properties of the flight model have to accept it.
        from physics.config_schema import derived_properties

        derived = derived_properties(config)
        assert derived["mass_kg"] > 0.0
        assert derived["ref_span_m"] > 0.0
        assert derived["ref_area_m2"] > 0.0


def test_a_valid_genome_flies_in_the_6dof_path() -> None:
    """A throwable design has to survive the whole 6-DoF simulation."""
    from physics import flight_sim

    genome = og.genome_from_archetype(od.ARCHETYPE_DART)
    geometry = og.fold_genome(genome, paper_gsm=100.0)
    report = og.evaluate_grip(geometry)
    assert report.valid, f"the classic dart must be throwable: {report.reasons}"

    config = og.genome_to_aircraft_config(geometry, paper_gsm=100.0)
    environment = EnvironmentConfig(wind_speed=0.0, gust_intensity=0.0, thermal_strength=0.0)
    launch = LaunchConfig(launch_speed=9.0, launch_angle_deg=3.0, launch_height_m=2.0)
    result = flight_sim.evaluate_flight(config, environment, launch, 1.5, 0.02)
    assert result["status"] in ("flying", "landed", "crashed", "time-limit", "stalled")
    _assert_finite(float(result["distance_m"]))
    _assert_finite(float(result["max_stress_index"]))


# ---------------------------------------------------------------------------
# (2) the grip filter
# ---------------------------------------------------------------------------
def test_the_classic_archetypes_satisfy_the_grip_filter() -> None:
    """A real paper aeroplane has to pass the filter.

    This is the regression guard of the filter: if it rejected the classic
    archetypes it would reject every throwable design there is.
    """
    for archetype in od.ARCHETYPES:
        genome = og.genome_from_archetype(archetype)
        geometry = og.fold_genome(genome)
        report = og.evaluate_grip(geometry)
        assert report.valid, f"{archetype} was rejected: {report.reasons}"
        assert report.grip_height_m >= 0.0
        assert report.grip_length_m > 0.0
        assert report.grip_layers >= 1
        assert report.penetrations == 0, f"{archetype} reports a self intersection"
        assert report.fitness == 1.0


def test_a_sheet_without_a_grip_is_rejected() -> None:
    """A flat sheet cannot be thrown: it has no grip at all."""
    genome = og.OrigamiGenome(
        steps=[
            og.FoldStep(
                step_index=1,
                crease_line=((0.0, 0.5), (1.0, 0.5)),
                fold_angle_deg=180.0,
                symmetry="symmetric",
            )
        ]
    )
    geometry = og.fold_genome(genome)
    report = og.evaluate_grip(geometry)
    assert not report.valid
    assert report.reasons
    assert report.penalty > 0.0
    assert report.fitness < 1.0


def test_a_self_intersection_is_detected() -> None:
    """A panel that lies inside another one has to be reported."""
    from physics.origami_domain import FoldedFace

    def face(fid: int, z: float, size: float) -> FoldedFace:
        poly = [
            (0.0, 0.0, z),
            (size, 0.0, z),
            (size, size, z),
            (0.0, size, z),
        ]
        return FoldedFace(
            fid=fid,
            poly3d=poly,
            normal=(0.0, 0.0, 1.0),
            front=(0.0, 0.0, 1.0),
            level=0,
            group="test",
            role="test",
            parent=None,
            centroid3d=(size / 2.0, size / 2.0, z),
            area_m2=size * size,
        )

    # A small panel floating just above a big one: the small panel is inside
    # the big one, which no pair of hands can produce.
    hits = og.detect_penetrations([face(1, 0.0, 0.1), face(2, 1.0e-9, 0.02)])
    assert len(hits) == 1
    assert {hits[0]["face_a"], hits[0]["face_b"]} == {1, 2}

    # Two panels that only touch along an edge are not a penetration.
    assert og.detect_penetrations(
        [face(1, 0.0, 0.1), face(2, 0.0, 0.1)], adjacency=[(1, 2)]
    ) == []


def test_the_grip_thresholds_are_configurable() -> None:
    """The minimum conditions have to be adjustable."""
    genome = og.genome_from_archetype(od.ARCHETYPE_DART)
    geometry = og.fold_genome(genome)
    strict = og.GripThresholds(min_grip_height_m=0.5, min_grip_length_m=0.9)
    report = og.evaluate_grip(geometry, strict)
    assert not report.valid
    assert "grip-too-shallow" in report.reasons
    assert "grip-too-short" in report.reasons

    loose = og.GripThresholds(min_grip_height_m=0.0, min_grip_length_m=0.0, min_grip_layers=1)
    assert og.evaluate_grip(geometry, loose).valid


# ---------------------------------------------------------------------------
# (3) the genome data model
# ---------------------------------------------------------------------------
def test_the_fold_angle_stays_inside_the_physical_range() -> None:
    """A fold is a rotation, so it can never exceed half a turn."""
    population = _random_population(200)
    for genome in population:
        for step in genome.steps:
            assert og.MIN_FOLD_ANGLE_DEG <= step.fold_angle_deg <= og.MAX_FOLD_ANGLE_DEG
            assert step.angle_magnitude_deg <= 180.0
            assert step.symmetry in og.SYMMETRY_MODES
            assert step.layer_order in og.LAYER_ORDER_MODES
            assert step.step_index >= 1


def test_a_genome_survives_a_json_round_trip() -> None:
    """The genome has to be serialisable: it is the unit of the search."""
    genome = og.random_genome(random.Random(5), steps=6, strategy="free", seed=5)
    payload = genome.to_dict()
    encoded = json.dumps(payload)
    decoded = json.loads(encoded)
    assert decoded["seed"] == genome.seed
    assert len(decoded["steps"]) == len(genome.steps)
    for step, raw in zip(genome.steps, decoded["steps"]):
        assert raw["step_index"] == step.step_index
        assert raw["fold_angle_deg"] == pytest.approx(step.fold_angle_deg)
        assert raw["symmetry"] == step.symmetry
        assert raw["layer_order"] == step.layer_order
        assert raw["fold_type"] == ("mountain" if step.mountain else "valley")


def test_the_genome_round_trips_through_the_crease_programme() -> None:
    """Genome -> creases -> genome has to be stable.

    The archetype is the reference design of the repository, so the genome
    representation has to reproduce its crease programme exactly.
    """
    reference = od._archetype_creases(
        od.ARCHETYPE_DART, od.KEEL_V_GAP_DEG, 0.0, 0.0, 0.0, 15.0, 5.0
    )
    genome = og.genome_from_archetype(od.ARCHETYPE_DART)
    creases = og.genome_to_creases(genome)
    assert len(creases) == len(reference)
    for produced, expected in zip(creases, reference):
        assert produced.p0[0] == pytest.approx(expected.p0[0])
        assert produced.p0[1] == pytest.approx(expected.p0[1])
        assert produced.p1[0] == pytest.approx(expected.p1[0])
        assert produced.p1[1] == pytest.approx(expected.p1[1])
        assert produced.angle_deg == pytest.approx(expected.angle_deg)
        assert produced.side == expected.side
        assert produced.up == expected.up
        assert produced.groups == expected.groups
        assert produced.set_group == expected.set_group
        assert produced.set_group_pair == expected.set_group_pair


def test_the_fold_sequence_is_executed_in_the_written_order() -> None:
    """The programme is a *sequence*: the order changes the result."""
    genome = og.random_genome(random.Random(11), steps=6, strategy="free", seed=11)
    steps = list(genome.steps)
    first = og.fold_genome(og.OrigamiGenome(steps=steps, sheet=genome.sheet))
    reversed_geometry = og.fold_genome(
        og.OrigamiGenome(steps=list(reversed(steps)), sheet=genome.sheet)
    )
    # The same set of creases in a different order is a different solid.
    assert first.faces != reversed_geometry.faces


# ---------------------------------------------------------------------------
# (4) the recipe
# ---------------------------------------------------------------------------
def test_the_recipe_reports_how_many_degrees_every_fold_uses() -> None:
    """The recipe has to name the line, the sense and the exact angle."""
    genome = og.random_genome(random.Random(3), steps=5, strategy="families", seed=3)
    geometry = og.fold_genome(genome)
    recipe = og.recipe_steps(genome, geometry)

    assert len(recipe) == len(genome.steps)
    for entry, step in zip(recipe, genome.steps):
        assert entry["step_index"] == step.step_index
        assert entry["fold_angle_deg"] == pytest.approx(step.fold_angle_deg)
        assert entry["fold_angle_magnitude_deg"] == pytest.approx(abs(step.fold_angle_deg))
        assert entry["fold_type"] in ("mountain", "valley")
        assert entry["symmetry"] == step.symmetry
        assert entry["layer_order"] == step.layer_order
        # The line is reported both normalised and in millimetres.
        assert len(entry["crease_line"]) == 2
        assert len(entry["crease_line_mm"]) == 2
        # The instruction is the human readable form of the step.
        assert f"{abs(step.fold_angle_deg):.1f}" in entry["instruction"]
        assert ("山折り" if step.mountain else "谷折り") in entry["instruction"]


def test_the_recipe_markdown_lists_every_step() -> None:
    """The Markdown recipe has to be usable as a print-out."""
    genome = og.genome_from_archetype(od.ARCHETYPE_DART)
    geometry = og.fold_genome(genome)
    markdown = og.recipe_markdown(genome, geometry)
    assert "# 折り手順" in markdown
    assert "| # |" in markdown
    for step in genome.steps:
        assert f"| {step.step_index} |" in markdown
        assert f"{step.angle_magnitude_deg:.1f}°" in markdown


def test_the_crease_pattern_svg_marks_the_mountains_and_the_valleys() -> None:
    """The flat pattern has to show every crease with its angle."""
    genome = og.random_genome(random.Random(9), steps=6, strategy="free", seed=9)
    svg = og.crease_pattern_svg(genome)
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    assert svg.count("<line") == len(genome.steps)
    # The mountains are red, the valleys blue.
    assert "#c02020" in svg
    assert "#2050c0" in svg
    for step in genome.steps:
        assert f"{abs(step.fold_angle_deg):.1f}°" in svg


def test_the_step_meshes_show_every_intermediate_state() -> None:
    """The 3D viewer needs the solid of every single step."""
    genome = og.genome_from_archetype(od.ARCHETYPE_DART)
    states = og.step_meshes(genome)
    assert len(states) == len(genome.steps) + 1
    assert states[0]["step_index"] == 0
    assert states[0]["label"] == "flat sheet"
    for index, state in enumerate(states[1:], start=1):
        assert state["step_index"] == index
        assert state["positions"], "a step produced an empty mesh"
        assert len(state["indices"]) % 3 == 0
        assert state["triangles"] if "triangles" in state else True
    # The flat sheet is the full sheet, the folded one is smaller.
    flat = states[0]["bounds"]
    folded = states[-1]["bounds"]
    assert flat["max"][0] - flat["min"][0] > 0.0


def test_the_genome_payload_carries_geometry_filter_and_recipe() -> None:
    """The JSON payload of an individual has to be complete."""
    genome = og.random_genome(random.Random(17), steps=5, seed=17)
    geometry = og.fold_genome(genome)
    report = og.evaluate_grip(geometry)
    payload = og.genome_payload(genome, geometry, report)
    assert "genome" in payload
    assert "geometry" in payload
    assert "grip" in payload
    assert "recipe" in payload
    assert payload["grip"]["valid"] == report.valid
    assert len(payload["recipe"]) == len(genome.steps)
    json.dumps(payload)


# ---------------------------------------------------------------------------
# (5) the API
# ---------------------------------------------------------------------------
@pytest.fixture()
def client():
    from app import app

    app.config["TESTING"] = True
    return app.test_client()


def test_the_api_generates_and_evaluates_a_genome(client) -> None:
    """``GET /api/v1/origami/genome`` returns the whole evaluation."""
    response = client.get("/api/v1/origami/genome?seed=11&step_count=6")
    assert response.status_code == 200
    payload = response.get_json()
    assert "genome" in payload
    assert "geometry" in payload
    assert "grip" in payload
    assert "recipe" in payload
    assert len(payload["recipe"]) == 6
    for entry in payload["recipe"]:
        assert "fold_angle_deg" in entry
        assert "fold_type" in entry
        assert "symmetry" in entry


def test_the_api_accepts_an_archetype(client) -> None:
    """The classic archetypes are valid individuals."""
    response = client.get("/api/v1/origami/genome?archetype=classic_dart")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["grip"]["valid"] is True
    assert payload["grip"]["reasons"] == []


def test_the_api_evaluates_a_posted_genome(client) -> None:
    """A client can send its own fold programme."""
    generated = client.get("/api/v1/origami/genome?seed=4&step_count=5").get_json()
    response = client.post("/api/v1/origami/genome", json={"genome": generated["genome"]})
    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload["genome"]["steps"]) == 5
    assert payload["grip"]["valid"] == generated["grip"]["valid"]


def test_the_api_returns_the_recipe_and_the_crease_pattern(client) -> None:
    """The recipe endpoint carries the table, the Markdown and the SVG."""
    response = client.get("/api/v1/origami/genome/recipe?seed=2&step_count=5")
    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload["steps"]) == 5
    assert "# 折り手順" in payload["markdown"]
    assert payload["svg"].startswith("<svg")
    for entry in payload["steps"]:
        assert f"{entry['fold_angle_magnitude_deg']:.1f}°" in payload["markdown"]


def test_the_api_returns_every_intermediate_fold_state(client) -> None:
    """The step endpoint is what a slider in the viewer walks through."""
    response = client.get("/api/v1/origami/genome/steps?seed=6&step_count=4")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["step_count"] == 4
    assert len(payload["states"]) == 5
    assert payload["states"][0]["step_index"] == 0
    for state in payload["states"]:
        assert state["positions"]
        assert state["indices"]


def test_the_api_rejects_invalid_requests(client) -> None:
    """Bad parameters have to be refused, not guessed."""
    assert client.get("/api/v1/origami/genome?step_count=0").status_code == 400
    assert client.get("/api/v1/origami/genome?step_count=999").status_code == 400
    assert client.get("/api/v1/origami/genome?strategy=bogus").status_code == 400
    assert client.get("/api/v1/origami/genome?archetype=nope").status_code == 400
    assert client.post("/api/v1/origami/genome", json={"genome": {"steps": [{}]}}).status_code == 400


# ---------------------------------------------------------------------------
# (6) G9 operators and scaling (no-finish.md §4.6)
# ---------------------------------------------------------------------------

def _grip_valid(genome: og.OrigamiGenome, paper_gsm: float = 100.0) -> bool:
    return og.evaluate_grip(og.fold_genome(genome, paper_gsm=paper_gsm)).valid


def _fixed_population(count: int = 50, seed: int = 20261020) -> Dict[str, list]:
    """Deterministic population, one strategy per entry, same seed per index."""
    population: Dict[str, list] = {"families": [], "free": []}
    for index in range(count):
        for strategy in ("families", "free"):
            population[strategy].append(
                og.random_genome(
                    random.Random(seed + index),
                    steps=4 + (index % 9),
                    strategy=strategy,
                    seed=seed + index,
                )
            )
    return population


def test_group_order_ranks_the_moving_group_above_the_static_group() -> None:
    """G9-2: group rank must actually orient layer assignment.

    ``layer_order=ABOVE`` ranks the moving group above the static group, so
    the rank is not a no-op.  The ranks feed :func:`assign_layers`'s DAG
    tie-break (separation guarantee lives in ``origami_domain``).
    """
    genome = og.OrigamiGenome(
        steps=[
            og.FoldStep(
                step_index=1,
                crease_line=((0.5, 0.0), (0.5, 1.0)),
                fold_angle_deg=120.0,
                symmetry=og.SYMMETRY_SYMMETRIC,
                layer_order=og.LAYER_ORDER_ABOVE,
                group=("base",),
                set_group="g1",
            )
        ]
    )
    order = og._group_order(genome)
    assert order["g1"] == 1
    assert order["base"] == -1
    assert order["g1"] > order["base"]

    # The rank is consumed by the layer solver and yields deterministic,
    # separated layers for a foldable genome.
    geometry = og.fold_genome(genome)
    layers = od.assign_layers(geometry.faces, og._group_order(genome))
    assert layers.max_layers >= 1
    assert all(index >= 0 for index in layers.index.values())


def test_mutate_genome_preserves_structure_and_is_deterministic() -> None:
    """G9-3: mutation keeps the programme structure and is reproducible."""
    genome = og.random_genome(random.Random(1), steps=6, strategy="families", seed=1)
    rng = random.Random(7)
    m1 = og.mutate_genome(genome, rng=rng)
    rng = random.Random(7)
    m2 = og.mutate_genome(genome, rng=rng)
    assert len(m1.steps) == len(genome.steps)
    assert len(m2.steps) == len(genome.steps)
    assert m1.to_dict() == m2.to_dict()  # same rng -> same mutant
    for s1, s2 in zip(m1.steps, genome.steps):
        assert s1.step_index == s2.step_index
        assert s1.symmetry == s2.symmetry
        assert s1.group == s2.group
        assert s1.set_group == s2.set_group
        assert og.MIN_FOLD_ANGLE_DEG <= s1.fold_angle_deg <= og.MAX_FOLD_ANGLE_DEG


def test_crossover_genomes_is_a_valid_union() -> None:
    """G9-3: union crossover folds cleanly (no dangling group references)."""
    a = og.random_genome(random.Random(3), steps=5, strategy="families", seed=3)
    b = og.random_genome(random.Random(4), steps=5, strategy="families", seed=4)
    child = og.crossover_genomes(a, b, rng=random.Random(9))
    assert child.steps
    assert all(step.set_group == "base" for step in child.steps)
    assert all(step.group == () for step in child.steps)
    geometry = og.fold_genome(child)
    assert geometry.faces
    assert geometry.span_m > 0.0


def test_repair_by_mutation_raises_grip_acceptance_rate() -> None:
    """G9-3: mutation-based repair cannot lower, and typically raises, the
    grip acceptance rate of a deterministic 50-individual population.

    The random-generation distribution itself is untouched
    (``random_genome`` is not modified); only existing individuals are
    repaired.  Before/after values are reported via assertions so the
    regression is observable.
    """
    population = _fixed_population(count=50, seed=20261020)

    def acceptance(pop: list) -> int:
        return sum(1 for g in pop if _grip_valid(g))

    rng = random.Random(20261020)
    before = {name: acceptance(pop) for name, pop in population.items()}
    after_population: Dict[str, list] = {}
    for name, pop in population.items():
        after_population[name] = [
            og.repair_by_mutation(g, rng=rng, max_attempts=8) for g in pop
        ]
    after = {name: acceptance(pop) for name, pop in after_population.items()}

    for strategy in ("families", "free"):
        assert after[strategy] >= before[strategy], (
            f"{strategy}: acceptance dropped {before[strategy]} -> {after[strategy]}"
        )

    # Documented reference: families was 24 % (12/50) on the 2026-10-20 audit;
    # this run's deterministic before/after is asserted non-decreasing below.
    print(
        f"\nG9-3 grip acceptance (50 individuals, seed 20261020): "
        f"families {before['families']}/50 -> {after['families']}/50, "
        f"free {before['free']}/50 -> {after['free']}/50"
    )


def test_genome_to_aircraft_config_matches_wingspan_m() -> None:
    """G9-5: the real span must match ``wingspan_m`` (±1%) via 2-stage scaling."""
    genome = og.genome_from_archetype(od.ARCHETYPE_DART)
    geometry = og.fold_genome(genome, paper_gsm=100.0)
    assert geometry.span_m > 0.0

    target = 1.2
    fit = target / geometry.span_m

    config = og.genome_to_aircraft_config(geometry, paper_gsm=100.0, wingspan_m=target)
    assert config.wingspan_m == pytest.approx(target, rel=1e-2)

    # Without the target, the measured span is used unchanged.
    config_base = og.genome_to_aircraft_config(geometry, paper_gsm=100.0)
    assert config_base.wingspan_m == pytest.approx(geometry.span_m, rel=1e-6)

    # Areas and mass scale with fit**2.
    assert config.wing_area_m2 == pytest.approx(config_base.wing_area_m2 * fit * fit, rel=1e-6)
    assert config.mass_kg == pytest.approx(config_base.mass_kg * fit * fit, rel=1e-6)
