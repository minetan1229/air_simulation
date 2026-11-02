"""Tests of the autonomous genetic research engine.

The tests exercise the *logic* of the engine -- the budget, the promotion
between the fidelity tiers, the statistics, the covariance adaptation, the
linkage crossover, the island migration, the stagnation detection, the
checkpoint/resume cycle and the artifacts of the best individual.

The physics is **never** mocked: every flight the engine evaluates goes
through the real reference 6-DoF model
(:func:`physics.parallel_engine.evaluate_case`).  The runs are made cheap by
shortening the flight durations and by shrinking the tier sizes, never by
replacing the physics.
"""

from __future__ import annotations

import json
import math
import os
import random
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from physics.ga_cma import CMAES  # noqa: E402
from physics.ga_config import (  # noqa: E402
    DEFAULT_MAX_TRIALS,
    DEFAULT_TIER1_TRIALS,
    DEFAULT_TIER2_TRIALS,
    DEFAULT_TIER3_TRIALS,
    DEFAULT_TOP_K,
    GAConfig,
    OBJECTIVES,
    STAT_MODES,
    load_config_file,
)
from physics.ga_domain import (  # noqa: E402
    GENE_NAMES,
    GENE_SPECS,
    Individual,
    LINKAGE_BLOCKS,
    ResearchRun,
    ThrowParams,
    apply_genes_to_genome,
    block_crossover,
    compute_population_statistics,
    default_genes,
    gene_bounds,
    mutate_genome,
    objective_spec,
    run_research,
    select_statistic,
)
from physics.origami_genome import genome_from_archetype  # noqa: E402


CONFIG_PATH = ROOT / "config" / "genetic_research.yaml"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _overrides(tmp_path: Path, **extra: object) -> dict:
    """A cheap but real research configuration."""
    base = {
        "max_trials": 12,
        "top_k": 3,
        "tier1_trials": 4,
        "tier2_trials": 3,
        "tier3_trials": 3,
        "generations": 2,
        "islands": 2,
        "population_size": 6,
        "tier2_duration_s": 0.12,
        "tier3_duration_s": 0.15,
        # The turbulent tier keeps its 3-D wind field (gusts and thermals);
        # only the expensive multi octave noise is switched off, so the test
        # suite stays quick without touching the physics path.
        "tier3_turbulence_scale": 0.0,
        "out_dir": str(tmp_path / "run"),
        "processes": 1,
        "seed": 11,
        "archetype_seed_count": 2,
        "random_seed_count": 2,
        "cma_lambda": 6,
    }
    base.update(extra)
    return base


def _run(tmp_path: Path, **extra: object) -> dict:
    """Run a small but real research programme."""
    return run_research(_overrides(tmp_path, **extra))


def _archive_member(index: int, fitness: float, *, status: str = "landed") -> Individual:
    """A candidate with a known fitness, for the hall of fame tests.

    The individual carries the flight payload of a **feasible** flight
    (``status`` ``landed``, no stress above the allowable) and the
    provenance of the 6-DoF reference path, so the engine accepts it into
    the archive.  A different status (``crashed``) makes it a structural
    failure, which never enters the archive.
    """
    genes = default_genes()
    result: dict = {
        "status": status,
        "max_stress_pa": 0.0,
        "model": "rigid_body_6dof",
        "approximate": False,
        "physics_verification_eligible": True,
    }
    if status == "crashed":
        result["crashed"] = True
    return Individual(
        index=index,
        genome=genome_from_archetype("dart"),
        genes=genes,
        throw=ThrowParams.from_genes(genes),
        tier=3,
        metric=float(fitness),
        fitness=float(fitness),
        feasible=True,
        result=result,
    )



# ---------------------------------------------------------------------------
# the configuration contract
# ---------------------------------------------------------------------------
def test_the_configuration_file_mirrors_the_dataclass_defaults() -> None:
    """The YAML file and the dataclass must not drift apart."""
    from physics.ga_config import load_yaml_mapping

    mapping = load_yaml_mapping(str(CONFIG_PATH))
    file_config = GAConfig.from_dict(mapping)
    base = GAConfig()
    for name, value in base.to_dict().items():
        if name == "grip_thresholds":
            continue
        assert file_config.to_dict()[name] == value, name

    from physics.origami_genome import GripThresholds

    thresholds = GripThresholds()
    for name, value in (file_config.grip_thresholds or {}).items():
        assert getattr(thresholds, name) == pytest.approx(float(value)), name


def test_the_default_trial_counts_are_defined_once() -> None:
    """The defaults live in one place only."""
    assert DEFAULT_MAX_TRIALS == 100_000
    assert DEFAULT_TOP_K == 1_000
    assert DEFAULT_TIER1_TRIALS == 20
    assert DEFAULT_TIER2_TRIALS == 1_000
    assert DEFAULT_TIER3_TRIALS == 100_000

    source = (ROOT / "physics" / "ga_domain.py").read_text(encoding="utf-8")
    for literal in ("100000", "100_000", "1000", "1_000"):
        assert literal not in source, f"{literal} must not appear in the search logic"

    config_source = (ROOT / "physics" / "ga_config.py").read_text(encoding="utf-8")
    assert config_source.count("100_000") == 2  # max trials and tier 3 trials
    assert config_source.count("1_000") == 2  # top k and tier 2 trials


# ---------------------------------------------------------------------------
# the budget: every count is overridable
# ---------------------------------------------------------------------------
def test_the_runtime_api_overrides_the_trial_counts(tmp_path: Path) -> None:
    """``run_research`` honours ``max_trials`` and ``top_k``."""
    summary = _run(tmp_path, max_trials=5, top_k=1)
    assert summary["evaluations"] <= 5
    assert summary["config"]["max_trials"] == 5
    assert summary["config"]["top_k"] == 1
    assert len((summary["result"].get("genome") or {}).get("steps", [])) >= 0
    hall_of_fame = summary.get("artifacts", {}).get("individual")
    assert hall_of_fame and os.path.exists(hall_of_fame)


def test_the_budget_is_binding_and_never_exceeded(tmp_path: Path) -> None:
    """A run stops exactly at its evaluation budget."""
    summary = _run(
        tmp_path,
        max_trials=6,
        tier1_trials=50,
        tier2_trials=50,
        tier3_trials=50,
        generations=5,
    )
    assert summary["evaluations"] == 6
    assert summary["status"] == "budget_exhausted"


def test_the_configuration_file_overrides_the_trial_counts(tmp_path: Path) -> None:
    """The YAML file is a first class source of the trial counts."""
    path = tmp_path / "custom.yaml"
    path.write_text(
        "objective: airtime\n"
        "max_trials: 7\n"
        "top_k: 2\n"
        "tier1_trials: 3\n"
        "tier2_trials: 3\n"
        "tier3_trials: 3\n"
        "generations: 1\n"
        "islands: 1\n"
        "seed: 5\n"
        f"out_dir: {json.dumps(str(tmp_path / 'yaml_run'))}\n",
        encoding="utf-8",
    )
    config = load_config_file(str(path))
    assert config.max_trials == 7
    assert config.top_k == 2
    assert config.objective == "airtime"

    summary = run_research(
        {
            "max_trials": config.max_trials,
            "top_k": config.top_k,
            "objective": config.objective,
            "tier1_trials": config.tier1_trials,
            "tier2_trials": config.tier2_trials,
            "tier3_trials": config.tier3_trials,
            "generations": config.generations,
            "islands": config.islands,
            "seed": config.seed,
            "tier2_duration_s": 0.12,
            "tier3_duration_s": 0.15,
            "out_dir": config.out_dir,
            "processes": 1,
        }
    )
    assert summary["evaluations"] <= 7
    assert summary["config"]["top_k"] == 2
    assert summary["objective"] == "airtime"


def test_the_command_line_applies_the_trial_count_overrides(tmp_path: Path, capsys) -> None:
    """``--max-trials 100 --top-k 10`` really caps the run at 100 evaluations."""
    from tools.run_ga_research import main

    out_dir = tmp_path / "cli"
    code = main(
        [
            "--objective",
            "distance",
            "--max-trials",
            "100",
            "--top-k",
            "10",
            "--tier1-trials",
            "40",
            "--tier2-trials",
            "2",
            "--tier3-trials",
            "2",
            "--generations",
            "6",
            "--islands",
            "1",
            "--population-size",
            "8",
            "--seed",
            "5",
            "--out",
            str(out_dir),
            "--tier2-duration",
            "0.12",
            "--tier3-duration",
            "0.15",
            "--tier3-turbulence-scale",
            "0.0",
            "--processes",
            "1",
        ]
    )
    assert code == 0
    payload = capsys.readouterr().out
    summary = json.loads(payload[payload.index("{") :])
    assert summary["config"]["max_trials"] == 100
    assert summary["config"]["top_k"] == 10
    assert summary["evaluations"] <= 100
    assert summary["evaluations"] >= 10
    assert summary["objective"] == "distance"

    hall_of_fame = json.loads(
        Path(out_dir, "checkpoints", "hall_of_fame.json").read_text(encoding="utf-8")
    )
    assert len(hall_of_fame) <= 10


def test_the_command_line_rejects_an_unknown_objective() -> None:
    from tools.run_ga_research import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--objective", "altitude"])


# ---------------------------------------------------------------------------
# the two objectives
# ---------------------------------------------------------------------------
def test_the_two_objectives_are_separate_code_paths() -> None:
    """``distance`` and ``airtime`` measure different things."""
    distance = objective_spec("distance")
    airtime = objective_spec("airtime")
    assert distance.metric_key == "distance_m"
    assert airtime.metric_key == "flight_time_s"
    assert distance.reference_target == pytest.approx(88.31)
    assert airtime.reference_target == pytest.approx(29.2)
    assert set(OBJECTIVES) == {"distance", "airtime"}

    payload = {"distance_m": 30.0, "flight_time_s": 2.0}
    assert distance.extract(payload) == 30.0
    assert airtime.extract(payload) == 2.0

    # The two objectives rank the same population differently.
    population = [
        {"distance_m": 10.0, "flight_time_s": 5.0},
        {"distance_m": 20.0, "flight_time_s": 1.0},
    ]
    by_distance = sorted(population, key=distance.extract, reverse=True)
    by_airtime = sorted(population, key=airtime.extract, reverse=True)
    assert by_distance[0] is not by_airtime[0]


def test_a_real_run_reports_the_metric_of_its_objective(tmp_path: Path) -> None:
    """The objective threads through the configuration into the statistics."""
    summary = _run(tmp_path, objective="airtime")
    assert summary["objective"] == "airtime"
    assert summary["objective_detail"]["metric_key"] == "flight_time_s"
    assert summary["stats"]["objective"] == "airtime"
    assert summary["stats"]["reference_target"] == pytest.approx(29.2)


# ---------------------------------------------------------------------------
# the multi fidelity pipeline
# ---------------------------------------------------------------------------
def test_the_tier_promotion_respects_the_configured_sizes(tmp_path: Path) -> None:
    """The size of every tier is the configured one.

    The tier sizes are the limits of **one evaluation round**, and a run has
    one round more than it has generations: the seeded population is
    evaluated before the first generation breeds from it, so the budget of
    the run pays for those flights as well.
    """
    generations = 3
    summary = _run(
        tmp_path,
        tier1_trials=3,
        tier2_trials=2,
        tier3_trials=1,
        max_trials=200,
        generations=generations,
        islands=1,
    )
    rounds = generations + 1
    counts = summary["tier_counts"]
    assert counts["tier1"] <= 3 * rounds
    assert counts["tier2"] <= 2 * rounds
    assert counts["tier3"] <= 1 * rounds
    assert counts["tier3"] >= 1
    assert counts["tier2"] >= counts["tier3"]
    promotion = summary["promotion"]
    assert promotion["promotion_ratio_tier1"] >= 0.0
    assert promotion["promotion_ratio_tier1"] <= 1.0
    assert promotion["promotion_ratio_tier3"] <= 1.0



def test_the_geometry_filter_eliminates_the_invalid_individuals(tmp_path: Path) -> None:
    """The grip filter runs before any 6-DoF cost is paid."""
    summary = _run(tmp_path, generations=1, islands=1)
    assert summary["tier_counts"]["tier0"] > summary["tier_counts"]["tier1"]
    assert 0.0 <= summary["generation0_feasible_fraction"] <= 1.0


# ---------------------------------------------------------------------------
# the statistics
# ---------------------------------------------------------------------------
def test_the_statistics_of_a_known_population() -> None:
    """Mean, median, overall median and failure rate on synthetic data."""
    statistics = compute_population_statistics(
        [1.0, 2.0, 3.0, 100.0],
        [1.0, 2.0, 3.0, 100.0, 2.0],
        [False, False, False, True],
    )
    assert statistics["mean"] == pytest.approx(26.5)
    assert statistics["median"] == pytest.approx(2.5)
    assert statistics["overall_median"] == pytest.approx(2.0)
    assert statistics["failure_rate"] == pytest.approx(0.25)
    assert statistics["best"] == pytest.approx(100.0)
    assert statistics["count"] == pytest.approx(4.0)


def test_every_statistic_mode_selects_its_own_value() -> None:
    """The statistic mode is a documented, selectable combination."""
    statistics = {"mean": 10.0, "median": 4.0, "best": 20.0, "overall_median": 4.0}
    assert select_statistic(statistics, "mean") == pytest.approx(10.0)
    assert select_statistic(statistics, "median") == pytest.approx(4.0)
    assert select_statistic(statistics, "best") == pytest.approx(20.0)
    # hybrid_median_mean = (0.5 * mean + 0.5 * median) / (0.5 + 0.5)
    assert select_statistic(statistics, "hybrid_median_mean") == pytest.approx(7.0)
    assert select_statistic(
        statistics, "hybrid_median_mean", {"mean": 0.25, "median": 0.75}
    ) == pytest.approx(5.5)
    assert set(STAT_MODES) == {"mean", "median", "hybrid_median_mean", "best"}


# ---------------------------------------------------------------------------
# the covariance adaptation
# ---------------------------------------------------------------------------
def test_the_cma_searches_the_normalised_box_of_the_real_genes() -> None:
    """The CMA-ES explores every gene with the same relative step.

    The engine samples in the raw gene units, and the ranges of the genes
    differ by three orders of magnitude: the centre-of-gravity offset spans
    0.1 m, the main wing fold angle spans 360 deg.  A single step size in
    the raw units therefore pins the narrow genes at their bounds (the
    centre-of-gravity offset was clipped in *100 %* of the samples) and
    barely moves the wide ones.  The strategy works in the normalised unit
    box, so every gene is explored with the same relative step.
    """
    bounds = gene_bounds()
    names = [spec.name for spec in GENE_SPECS]
    cma = CMAES(
        x0=[spec.default for spec in GENE_SPECS],
        sigma0=0.15,
        bounds=bounds,
        seed=5,
        lam=32,
        mu=16,
    )

    clipped = {name: 0 for name in names}
    total = 0
    for _ in range(20):
        for sample in cma.ask():
            total += 1
            for position, name in enumerate(names):
                low, high = bounds[position]
                if sample[position] <= low + 1.0e-12 or sample[position] >= high - 1.0e-12:
                    clipped[name] += 1

    assert total == 20 * 32
    for name in names:
        fraction = clipped[name] / float(total)
        assert fraction < 0.9, f"{name} is pinned at a bound in {fraction:.1%} of the samples"
    # The genes whose default lies inside their range must not be clipped at
    # all: the raw-unit sampling pinned the narrow ones at 100 % and the
    # wide ones at 0.1 % of their range.
    for name in ("cg_offset_x_m", "roll_deg", "yaw_deg", "throw_speed_mps"):
        fraction = clipped[name] / float(total)
        assert fraction < 0.05, f"{name} is clipped in {fraction:.1%} of the samples"


def test_the_cma_discovers_the_linkage_of_the_real_genes() -> None:
    """The covariance matrix develops a correlation between two genes.

    The test uses the **real** gene bounds of the engine (the main wing
    fold angle and the elevon angle), so it fails when the strategy samples
    in the raw units: the narrow genes are pinned at their bounds, the
    wide ones barely move and the linkage is never learned.
    """
    bounds = gene_bounds()
    names = [spec.name for spec in GENE_SPECS]
    wing = names.index("main_wing_fold_angle_deg")
    elevon = names.index("elevon_angle_deg")
    spans = [high - low for low, high in bounds]

    cma = CMAES(
        x0=[spec.default for spec in GENE_SPECS],
        sigma0=0.15,
        bounds=bounds,
        seed=5,
        lam=24,
        mu=12,
    )
    assert cma.off_diagonal_mass() == pytest.approx(0.0, abs=1.0e-9)

    generations = 40
    for _ in range(generations):
        samples = cma.ask()
        # The wing fold angle and the elevon angle are rewarded when they
        # cancel each other, so the strategy has to learn that the two
        # genes are linked.
        scores = []
        for sample in samples:
            wing_value = (sample[wing] - bounds[wing][0]) / spans[wing]
            elevon_value = (sample[elevon] - bounds[elevon][0]) / spans[elevon]
            scores.append(-((wing_value - elevon_value) ** 2))
        cma.tell(scores)

    correlation = cma.correlation_matrix()
    measured = abs(float(correlation[wing, elevon]))
    assert measured > 0.2, f"the wing/elevon correlation is only {measured:.3f}"
    assert cma.off_diagonal_mass() > 0.0
    assert math.isfinite(cma.sigma) and cma.sigma > 0.0


def test_the_cma_state_survives_a_checkpoint_round_trip() -> None:
    """The covariance matrix and the evolution paths are restored."""
    cma = CMAES(
        x0=[0.0, 0.0, 0.0],
        sigma0=0.4,
        bounds=[(-1.0, 1.0)] * 3,
        seed=9,
        lam=8,
        mu=4,
    )
    for _ in range(4):
        samples = cma.ask()
        cma.tell([float(index) for index in range(len(samples))])

    payload = cma.to_dict()
    restored = CMAES.from_dict(payload)
    assert np.allclose(restored.covariance, cma.covariance)
    assert np.allclose(restored.path_sigma, cma.path_sigma)
    assert np.allclose(restored.path_c, cma.path_c)
    assert restored.sigma == pytest.approx(cma.sigma)
    assert np.allclose(restored.ask()[0], cma.ask()[0])


# ---------------------------------------------------------------------------
# the linkage crossover
# ---------------------------------------------------------------------------
def test_the_block_crossover_keeps_the_blocks_intact() -> None:
    """Every linkage block is inherited as a unit from one parent."""
    parent_a = {name: 1.0 for name in GENE_NAMES}
    parent_b = {name: 2.0 for name in GENE_NAMES}
    for seed in range(12):
        child = block_crossover(parent_a, parent_b, random.Random(seed))
        for _name, members in LINKAGE_BLOCKS:
            values = {child[member] for member in members}
            assert len(values) == 1, "a block must not be split between the parents"
            assert values.pop() in (1.0, 2.0)
        assert set(child) == set(GENE_NAMES)


# ---------------------------------------------------------------------------
# the island model
# ---------------------------------------------------------------------------
def test_the_island_migration_preserves_the_elites_and_the_islands_diverge(
    tmp_path: Path,
) -> None:
    """The elites travel between the islands and the islands stay apart."""
    config = GAConfig().merged(
        _overrides(tmp_path, islands=2, migration_elites=2, migration_interval=1)
    ).validate()
    run = ResearchRun(config, {})
    run._build_islands()

    for index, island in enumerate(run.islands):
        island.population = [
            _synthetic_individual(run, index * 10 + slot, fitness=100.0 - index * 10 - slot)
            for slot in range(4)
        ]

    source_population = list(run.islands[0].population)
    run.generation = 0
    migration = run._migrate()
    assert migration["migrated"] == 4
    assert len(migration["edges"]) == 2

    receiver = run.islands[1]
    assert any(item in receiver.population for item in source_population)
    # the receiver keeps its own size
    assert len(receiver.population) == 4
    # the islands have diverged
    means = []
    for island in run.islands:
        vectors = [
            [float(ind.genes.get(name, 0.0)) for name in GENE_NAMES]
            for ind in island.population
        ]
        means.append([sum(row[i] for row in vectors) / len(vectors) for i in range(len(GENE_NAMES))])
    assert means[0] != means[1]


def _synthetic_individual(run: ResearchRun, index: int, fitness: float):
    """An individual with deterministic genes (no physics involved)."""
    from physics.ga_domain import Individual, ThrowParams, default_genes

    from physics.origami_genome import genome_from_archetype

    genes = default_genes()
    for position, name in enumerate(GENE_NAMES):
        genes[name] = float(index) + 0.1 * position
    genome = apply_genes_to_genome(genome_from_archetype("dart"), genes)
    individual = Individual(
        index=index,
        genome=genome,
        genes=genes,
        throw=ThrowParams.from_genes(genes),
    )
    individual.fitness = float(fitness)
    individual.metric = float(fitness)
    return individual


# ---------------------------------------------------------------------------
# the stagnation detection
# ---------------------------------------------------------------------------
def test_the_stagnation_detection_writes_a_decision_record(tmp_path: Path) -> None:
    """A stagnating island triggers the structure search shift and journals it.

    The structure belongs to the island that stagnated: the shift changes
    the search of that island only and never the shared configuration of the
    run.
    """
    config = GAConfig().merged(
        _overrides(
            tmp_path,
            stagnation_generations=1,
            max_fold_steps=4,
            max_fold_steps_limit=6,
            generations=1,
        )
    ).validate()
    run = ResearchRun(config, {})
    run._build_islands()
    island = run.islands[0]
    island.improved = False
    island.stagnation_counter = 0

    decision = run._check_stagnation(island)
    assert decision is not None
    assert "stagnation" in decision["trigger"]
    assert island.max_fold_steps == 5
    assert island.micro_flaps is True
    # The shared configuration is not touched by the shift of one island.
    assert config.max_fold_steps == 4
    assert config.micro_flaps is False
    assert run.config.max_fold_steps == 4
    assert run.config.micro_flaps is False

    journal_path = Path(config.resolved_journal_path())
    assert journal_path.exists()
    records = [
        json.loads(line)
        for line in journal_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert records
    shifts = [record for record in records if record["decision"] == "structure_search_shift"]
    assert shifts, "the structure search shift must be journalled"
    for record in shifts:
        for key in ("timestamp", "generation", "island", "decision", "trigger", "changes"):
            assert key in record, key
        assert record["island"] == island.id
        assert "stagnation" in record["trigger"]
        assert isinstance(record["changes"], dict)


def test_a_real_run_journals_its_decisions(tmp_path: Path) -> None:
    """Every autonomous decision of a real run lands in the journal file."""
    summary = _run(tmp_path, generations=2, islands=1, stagnation_generations=1)
    out_dir = Path(summary["config"]["out_dir"])
    journal_path = out_dir / "research_journal.jsonl"
    records = [
        json.loads(line)
        for line in journal_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert records
    decisions = {record["decision"] for record in records}
    assert "run_start" in decisions
    for record in records:
        for key in ("timestamp", "generation", "island", "decision", "trigger", "changes"):
            assert key in record, key


# ---------------------------------------------------------------------------
# the checkpoint / resume cycle
# ---------------------------------------------------------------------------
def test_the_checkpoint_restores_the_populations_the_seeds_and_the_covariance(
    tmp_path: Path,
) -> None:
    """A resumed run continues where the checkpoint stopped."""
    config = GAConfig().merged(
        _overrides(tmp_path, max_trials=8, generations=1, islands=2, out_dir=str(tmp_path / "run"))
    ).validate()
    run = ResearchRun(config, {})
    summary = run.run()
    checkpoint = summary["checkpoint"]
    assert os.path.exists(checkpoint)

    saved_population = [ind.to_dict() for ind in run.islands[0].population]
    saved_seed = run.islands[0].seed
    saved_covariance = run.islands[0].cma.covariance.copy()
    saved_evaluations = run.evaluation_count

    resumed_config = GAConfig().merged(
        _overrides(
            tmp_path,
            max_trials=20,
            top_k=7,
            generations=2,
            islands=2,
            out_dir=str(tmp_path / "resumed"),
            resume_from=checkpoint,
        )
    ).validate()
    resumed = ResearchRun(resumed_config, {"max_trials": 20, "top_k": 7})
    resumed._load_checkpoint(checkpoint)
    assert len(resumed.islands) == 2
    assert resumed.islands[0].seed == saved_seed
    assert len(resumed.islands[0].population) == len(saved_population)
    assert np.allclose(resumed.islands[0].cma.covariance, saved_covariance)
    assert resumed.islands[0].cma.generation == run.islands[0].cma.generation
    assert resumed.evaluation_count == saved_evaluations

    resumed_summary = resumed.run()

    assert resumed_summary["resumed"] is True
    assert resumed.evaluation_count >= saved_evaluations
    assert len(resumed.islands) == 2
    assert resumed.islands[0].seed == saved_seed

    # the overridden trial counts are honoured and recorded
    assert resumed_summary["config"]["max_trials"] == 20
    assert resumed_summary["config"]["top_k"] == 7
    records = [
        json.loads(line)
        for line in Path(resumed_config.resolved_journal_path())
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    resumes = [record for record in records if record["decision"] == "resume"]
    assert resumes
    assert resumes[0]["changes"]["max_trials"] == 20
    assert resumes[0]["changes"]["top_k"] == 7


# ---------------------------------------------------------------------------
# the artifacts of the best individual
# ---------------------------------------------------------------------------
def test_the_best_individual_yields_a_recipe_an_svg_and_the_step_meshes(
    tmp_path: Path,
) -> None:
    """The result of a run carries the whole human facing description."""
    summary = _run(tmp_path, generations=1, islands=1)
    result = summary["result"]
    assert result["recipe"]["steps"], "the recipe must list the folds"
    assert result["recipe"]["markdown"].startswith("#")
    assert result["crease_pattern_svg"].lstrip().startswith("<svg")
    assert isinstance(result["step_meshes"], list) and result["step_meshes"]
    assert result["throw_params"]["speed_mps"] > 0.0
    for key in ("mean", "median", "overall_median", "failure_rate"):
        assert key in result["stats"]

    artifacts = summary["artifacts"]
    for key in (
        "recipe",
        "recipe_markdown",
        "crease_pattern_svg",
        "step_meshes",
        "throw_params",
        "statistics",
        "individual",
    ):
        assert os.path.exists(artifacts[key]), key


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------
def test_the_same_seed_produces_the_same_result(tmp_path: Path) -> None:
    """The search is reproducible."""
    first = _run(tmp_path / "a", max_trials=8, generations=2, islands=1)
    second = _run(tmp_path / "b", max_trials=8, generations=2, islands=1)
    assert first["evaluations"] == second["evaluations"]
    assert first["best_metric"] == pytest.approx(second["best_metric"])
    assert first["best_fitness"] == pytest.approx(second["best_fitness"])
    assert first["stats"]["mean"] == pytest.approx(second["stats"]["mean"])
    assert first["stats"]["median"] == pytest.approx(second["stats"]["median"])


# ---------------------------------------------------------------------------
# the API
# ---------------------------------------------------------------------------
def test_the_api_exposes_the_research_contract(tmp_path: Path) -> None:
    """The blueprint answers with the documented payload."""
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        response = client.post(
            "/api/ga/research/run",
            json={
                "objective": "distance",
                "max_trials": 3,
                "top_k": 2,
                "tier1_trials": 2,
                "tier2_trials": 1,
                "tier3_trials": 1,
                "stat_mode": "hybrid_median_mean",
                "seed": 3,
                "generations": 1,
                "islands": 1,
                "output_dir": str(tmp_path / "api"),
            },
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["run_id"]
        assert payload["config"]["max_trials"] == 3
        assert payload["config"]["top_k"] == 2
        assert payload["status"] in ("running", "completed", "budget_exhausted")

        status = client.get(f"/api/ga/research/status/{payload['run_id']}")
        assert status.status_code == 200
        status_payload = status.get_json()
        for key in (
            "run_id",
            "status",
            "objective",
            "generation",
            "generations",
            "evaluations",
            "best_fitness",
            "islands",
            "tier",
            "elapsed_s",
        ):
            assert key in status_payload, key

        missing = client.get("/api/ga/research/status/does-not-exist")
        assert missing.status_code == 404


# ---------------------------------------------------------------------------
# the live progress of a run
# ---------------------------------------------------------------------------
def _live_overrides(tmp_path: Path, **extra: object) -> dict:
    """A cheap run whose budget cannot be exhausted by the generations."""
    base = {
        "max_trials": 40,
        "top_k": 3,
        "tier1_trials": 2,
        "tier2_trials": 2,
        "tier3_trials": 2,
        "generations": 3,
        "islands": 2,
    }
    base.update(extra)
    return _overrides(tmp_path, **base)


def test_the_progress_callback_is_called_once_per_generation(tmp_path: Path) -> None:
    """The listener of a run sees one snapshot per generation."""
    events: list = []
    run_research(_live_overrides(tmp_path), progress_callback=events.append)

    assert len(events) == 3, "one snapshot per generation"
    generations = [int(event["generation"]) for event in events]
    assert generations == sorted(generations), "the generation never goes back"
    assert generations == [0, 1, 2]
    evaluations = [int(event["evaluations"]) for event in events]
    assert all(
        later > earlier for earlier, later in zip(evaluations, evaluations[1:])
    ), "every generation evaluates more than the one before"
    for event in events:
        for key in (
            "generation",
            "generations",
            "evaluations",
            "best_fitness",
            "islands",
            "tier",
            "elapsed_s",
            "history",
        ):
            assert key in event, key
        assert int(event["generations"]) == 3


def test_the_progress_snapshot_carries_the_state_of_every_island(tmp_path: Path) -> None:
    """The snapshot describes the island model of the run."""
    events: list = []
    run_research(_live_overrides(tmp_path), progress_callback=events.append)

    for event in events:
        islands = event["islands"]
        assert len(islands) == 2, "one entry per island"
        assert sorted(int(island["id"]) for island in islands) == [0, 1]
        for island in islands:
            for key in ("id", "size", "best", "diversity"):
                assert key in island, key
            assert int(island["size"]) > 0
            assert float(island["diversity"]) >= 0.0
        assert int(event["tier"]) >= 1, "the tiers were reached"


def test_the_progress_history_is_a_monotone_best_so_far_curve(tmp_path: Path) -> None:
    """The published curve is the best fitness up to every generation."""
    events: list = []
    run_research(_live_overrides(tmp_path), progress_callback=events.append)

    last = events[-1]
    history = last["history"]
    assert len(history) == 3, "one entry per generation"
    values = []
    for entry in history:
        assert "generation" in entry
        assert "best_fitness" in entry
        values.append(entry["best_fitness"])
    assert values == sorted(values), "the best so far never decreases"
    assert values[-1] == pytest.approx(last["best_fitness"])
    assert [int(entry["generation"]) for entry in history] == [0, 1, 2]
    # The curve grows while the run goes on.
    assert len(events[0]["history"]) == 1
    assert len(events[1]["history"]) == 2


def test_a_broken_progress_listener_does_not_abort_the_run(tmp_path: Path) -> None:
    """A listener that raises must not take the research run down."""

    def broken(_payload: dict) -> None:
        raise RuntimeError("the listener is broken")

    summary = run_research(_live_overrides(tmp_path, generations=2), progress_callback=broken)
    assert summary["status"] in ("completed", "budget_exhausted")
    assert int(summary["generations"]) == 1
    assert int(summary["evaluations"]) > 0
    assert summary["result"]["recipe"]["steps"]


def test_the_run_works_without_a_progress_listener(tmp_path: Path) -> None:
    """The entry point stays backward compatible."""
    summary = run_research(_live_overrides(tmp_path, generations=2))
    assert summary["status"] in ("completed", "budget_exhausted")
    assert int(summary["generations"]) == 1
    assert int(summary["evaluations"]) > 0


def test_the_api_publishes_the_live_progress_of_a_running_run(tmp_path: Path) -> None:
    """The status of a running run carries the progress of the generations."""
    import time

    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        response = client.post(
            "/api/ga/research/run",
            json={
                "objective": "distance",
                "max_trials": 40,
                "top_k": 3,
                "tier1_trials": 2,
                "tier2_trials": 2,
                "tier3_trials": 2,
                "tier2_duration_s": 0.12,
                "tier3_duration_s": 0.15,
                "seed": 11,
                "generations": 3,
                "islands": 2,
                "output_dir": str(tmp_path / "live"),
            },
        )
        assert response.status_code == 200
        run_id = response.get_json()["run_id"]

        live = []
        final = None
        # The seeded population is evaluated before the first generation, so
        # the first snapshot of the curve appears after those flights: the
        # deadline has to cover them as well.
        deadline = time.time() + 240.0

        while time.time() < deadline:
            payload = client.get(f"/api/ga/research/status/{run_id}").get_json()
            for key in (
                "run_id",
                "status",
                "objective",
                "generation",
                "generations",
                "evaluations",
                "best_fitness",
                "islands",
                "tier",
                "elapsed_s",
                "error",
                "history",
            ):
                assert key in payload, key
            assert int(payload["generations"]) == 3
            if payload["status"] == "running" and int(payload["evaluations"]) > 0:
                live.append(payload)
            else:
                final = payload
                if payload["status"] != "running":
                    break
            time.sleep(0.05)

        assert live, "the status of a running run must publish its progress"
        assert len(live[0]["islands"]) == 2
        assert live[0]["history"], "the curve of the generations must be published"
        assert final is not None
        assert int(final["evaluations"]) > 0
        assert final["history"], "the curve survives the end of the run"


# ---------------------------------------------------------------------------
# D1: the configuration file must reach the run
# ---------------------------------------------------------------------------
def test_the_configuration_file_reaches_the_run(tmp_path: Path) -> None:
    """The values of a configuration file steer the run.

    The command line tool read ``config/genetic_research.yaml``, validated
    it and then threw it away: only the command line overrides reached the
    run, so every value of the file was silently dropped.  The file is the
    *base* of the run now and the command line is merged on top of it.
    """
    import contextlib
    import io
    import json as json_module

    from tools.run_ga_research import main as cli_main

    config_path = tmp_path / "custom_research.yaml"
    config_path.write_text(
        "\n".join(
            [
                "objective: airtime",
                "stat_mode: median",
                "generations: 7",
                "islands: 3",
                "population_size: 4",
                "max_trials: 40",
                "top_k: 5",
                "tier1_trials: 4",
                "tier2_trials: 8",
                "tier3_trials: 40",
                "seed: 3",
                "tier2_duration_s: 0.12",
                "tier3_duration_s: 0.15",
                "tier3_turbulence_scale: 0.0",
                "processes: 1",
                f"out_dir: {tmp_path / 'from_yaml'}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        code = cli_main(
            [
                "--config",
                str(config_path),
                "--out",
                str(tmp_path / "from_cli"),
            ]
        )
    assert code == 0
    summary = json_module.loads(stream.getvalue())

    config = summary["config"]
    assert config["objective"] == "airtime"
    assert config["stat_mode"] == "median"
    assert int(config["generations"]) == 7
    assert int(config["islands"]) == 3
    assert int(config["population_size"]) == 4
    assert int(config["seed"]) == 3
    assert int(config["top_k"]) == 5
    assert int(config["tier1_trials"]) == 4
    assert int(config["tier2_trials"]) == 8
    assert int(config["tier3_trials"]) == 40
    # The run really used them: three islands and the airtime objective.
    assert len(summary["islands"]) == 3
    assert summary["objective"] == "airtime"
    assert summary["stats"]["stat_mode"] == "median"


def test_the_command_line_wins_over_the_configuration_file(tmp_path: Path) -> None:
    """The precedence is command line > file > dataclass default."""
    import contextlib
    import io
    import json as json_module

    from tools.run_ga_research import main as cli_main

    config_path = tmp_path / "custom_research.yaml"
    config_path.write_text(
        "\n".join(
            [
                "objective: airtime",
                "stat_mode: median",
                "generations: 2",
                "islands: 1",
                "population_size: 4",
                "max_trials: 30",
                "top_k: 4",
                "tier1_trials: 3",
                "tier2_trials: 4",
                "tier3_trials: 4",
                "seed: 3",
                "tier2_duration_s: 0.12",
                "tier3_duration_s: 0.15",
                "tier3_turbulence_scale: 0.0",
                "processes: 1",
                f"out_dir: {tmp_path / 'from_yaml'}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        code = cli_main(
            [
                "--config",
                str(config_path),
                "--objective",
                "distance",
                "--out",
                str(tmp_path / "from_cli"),
            ]
        )
    assert code == 0
    summary = json_module.loads(stream.getvalue())
    assert summary["objective"] == "distance"
    assert summary["config"]["objective"] == "distance"
    # The values that the command line did not mention still come from the
    # file.
    assert summary["config"]["stat_mode"] == "median"
    assert int(summary["config"]["islands"]) == 1
    assert int(summary["config"]["seed"]) == 3


# ---------------------------------------------------------------------------
# D3: a failed evaluation is infeasible
# ---------------------------------------------------------------------------
def test_a_failed_evaluation_is_infeasible_and_never_published(tmp_path: Path) -> None:
    """A broken evaluation must not look like a legitimate flight.

    The metric of a flight that could not be evaluated is ``0.0`` -- the
    metric of an aeroplane that flew nowhere.  Scoring it like a legitimate
    flight lets a broken evaluation win every comparison and end up as the
    published best individual.

    Both candidates carry the full fidelity tier and a reference path
    payload, so the *only* difference between them is the failed
    evaluation: the archive publishes the legitimate flight and refuses the
    broken one.
    """
    from physics.ga_domain import INFEASIBLE_SCORE, is_failed_evaluation
    from physics.optimizer_domain import INFEASIBLE_SCORE as SHARED_INFEASIBLE_SCORE

    assert float(INFEASIBLE_SCORE) == float(SHARED_INFEASIBLE_SCORE)

    run = ResearchRun(GAConfig().merged(_overrides(tmp_path)).validate(), {})
    genome = genome_from_archetype("dart")
    genes = default_genes()

    broken = Individual(
        index=1,
        genome=genome,
        genes=genes,
        throw=ThrowParams.from_genes(genes),
        tier=2,
        feasible=True,
        result={
            "model": "rigid_body_6dof",
            "status": "error",
            "distance_m": 0.0,
            "flight_time_s": 0.0,
        },
    )
    legitimate = Individual(
        index=2,
        genome=genome,
        genes=genes,
        throw=ThrowParams.from_genes(genes),
        tier=2,
        feasible=True,
        result={
            "model": "rigid_body_6dof",
            "status": "landed",
            "distance_m": 0.0,
            "flight_time_s": 0.0,
        },
    )

    assert is_failed_evaluation(broken.result, run._allowable_pa()) is True
    assert is_failed_evaluation(legitimate.result, run._allowable_pa()) is False

    broken_fitness = run._individual_fitness(broken)
    legitimate_fitness = run._individual_fitness(legitimate)
    assert broken_fitness == pytest.approx(float(INFEASIBLE_SCORE))
    assert broken_fitness < legitimate_fitness, (
        "a failed evaluation must score strictly worse than a legitimate 0 m flight"
    )
    assert legitimate_fitness == pytest.approx(0.0)

    # The archive of the best individuals of the run refuses it.
    run._update_hall_of_fame([broken, legitimate])
    assert [item.index for item in run.hall_of_fame] == [legitimate.index]

    # The structural failure rate counts it.
    failures = [
        run._is_infeasible(broken),
        run._is_infeasible(legitimate),
    ]
    assert failures == [True, False]
    statistics = compute_population_statistics([0.0, 0.0], [0.0, 0.0], failures)
    assert statistics["failure_rate"] == pytest.approx(0.5)


def test_a_failed_evaluation_never_becomes_the_best_of_a_real_run(tmp_path: Path) -> None:
    """A run never publishes an individual whose evaluation failed."""
    from physics.ga_domain import INFEASIBLE_SCORE

    summary = _run(tmp_path, generations=2, islands=1)
    best_fitness = summary["best_fitness"]
    assert best_fitness is not None
    # The published best is a real flight, not an infeasible score.
    assert best_fitness > float(INFEASIBLE_SCORE)
    assert summary["result"]["fitness"] == pytest.approx(best_fitness)


# ---------------------------------------------------------------------------
# D4: the statistic mode steers the search
# ---------------------------------------------------------------------------
def test_the_statistic_mode_drives_the_selection(tmp_path: Path) -> None:
    """``--stat-mode`` changes the search, not only the report.

    The contract (D4) is a **population level** one: the selected
    population statistic decides which candidates become the *parents* of
    the next population.  The engine implements it in
    :meth:`physics.ga_domain.ResearchRun._elite_count`: the elite block is
    the group of candidates that are at least as good as the selected
    aggregate, so the mean keeps a broadly good population, the median a
    robust one and the peak a population built around the best individual.
    The next population is that elite block plus the best of the new
    offspring, so the size of the block is also the room the island has
    left for new blood.

    The test therefore asserts the **search level** effect and not the
    identity of the published best.  The best individual of a run is a
    single point of the search: the same individual may well win in two
    different modes - the generation-0 elite wins in both when the
    population is still close to the seeds - and that coincidence says
    nothing about the population the next generation is built from.  The
    published genes were therefore the wrong proxy for the contract.

    The proof has two halves:

    * a **real run** in each mode: the statistic selects the aggregate the
      selection is built on, so the two runs build their next populations
      from different aggregates.  The *size* of the elite block is a
      property of the data (the mean and the median of a small population
      can select the same number of candidates), so the size is not the
      contract -- the selection is, and the controlled population proves
      it;
    * a **controlled population** whose fitness spread is known: with the
      engine's own methods the elite block of every mode is exactly the
      group of candidates that carries the selected aggregate, so the
      three modes keep three different sets of parents.  This half is
      deterministic - it does not depend on a lucky run.
    """
    from physics.ga_domain import INFEASIBLE_SCORE
    from physics.ga_domain import select_statistic as select

    outcomes = {}
    for mode in ("mean", "median"):
        out_dir = tmp_path / mode
        events: list = []
        summary = run_research(
            _overrides(
                tmp_path,
                stat_mode=mode,
                generations=3,
                islands=1,
                out_dir=str(out_dir),
                max_trials=60,
                cma_lambda=8,
            ),
            progress_callback=events.append,
        )
        assert summary["config"]["stat_mode"] == mode
        assert summary["stats"]["stat_mode"] == mode
        # The reported aggregate is the one the mode selects.
        assert summary["stats"]["selected"] == pytest.approx(
            select(summary["stats"], mode)
        )
        checkpoint = json.loads(
            (Path(summary["checkpoint"])).read_text(encoding="utf-8")
        )
        reports = [
            report
            for entry in checkpoint["history"]
            for report in entry["reports"]
        ]
        elite_counts = [int(report["elite_count"]) for report in reports]
        aggregates = [float(report["aggregate"]) for report in reports]
        assert elite_counts, "the run must report the size of its elite block"
        outcomes[mode] = {
            "elite_counts": elite_counts,
            "aggregates": aggregates,
            "genes": summary["result"]["genes"],
            "best": summary["best_fitness"],
        }

    # The elite block of a real generation is never empty, and the block is
    # the room the island has left for new blood.
    for mode, outcome in outcomes.items():
        assert all(count >= 1 for count in outcome["elite_counts"]), mode
        assert outcome["best"] is not None, mode
        assert outcome["best"] > float(INFEASIBLE_SCORE), mode
    # The aggregate the selection is built on is the statistic itself, so a
    # different aggregate is a different selection input, not a different
    # report.
    assert outcomes["mean"]["aggregates"] != outcomes["median"]["aggregates"], (
        "the selected statistic must change the aggregate the selection uses"
    )

    # -- the controlled population -------------------------------------
    # A population with a known spread: a dense upper half and two poor
    # candidates, so the mean, the median and the peak select three
    # different groups.
    fitness_values = [
        0.10,
        0.20,
        0.30,
        0.40,
        0.50,
        0.60,
        0.65,
        0.70,
        0.80,
        0.90,
        0.95,
        1.00,
    ]
    run = ResearchRun(
        GAConfig()
        .merged(
            _overrides(
                tmp_path,
                population_size=len(fitness_values),
                stat_elite_max_fraction=0.9,
            )
        )
        .validate(),
        {},
    )
    genes = default_genes()
    genome = genome_from_archetype("dart")
    candidates = [
        Individual(
            index=index,
            genome=genome,
            genes=genes,
            throw=ThrowParams.from_genes(genes),
            metric=value,
            fitness=value,
        )
        for index, value in enumerate(fitness_values)
    ]
    statistics = compute_population_statistics(
        fitness_values, fitness_values, [False] * len(fitness_values)
    )

    blocks = {}
    for mode in ("mean", "median", "best"):
        aggregate = select(statistics, mode)
        count = int(run._elite_count(candidates, aggregate))
        ranked = sorted(
            candidates, key=lambda item: (item.fitness, item.index), reverse=True
        )
        block = tuple(sorted(item.index for item in ranked[:count]))
        blocks[mode] = {"aggregate": float(aggregate), "count": count, "block": block}
        # The block is exactly the group that carries the selected
        # aggregate: every member is at least as good as the aggregate.
        assert all(
            fitness_values[index] >= aggregate for index in block
        ), (mode, block, aggregate)
        # The island keeps the block and fills the rest of the population
        # with new blood, so the block size is the room for new blood.
        assert count <= len(fitness_values) - 1, (mode, count)

    # The three statistics select three different groups of parents.
    assert blocks["mean"]["block"] != blocks["median"]["block"], blocks
    assert blocks["median"]["block"] != blocks["best"]["block"], blocks
    # The peak keeps the best individual alone: the population is rebuilt
    # around it.
    assert blocks["best"]["count"] == 1, blocks
    assert blocks["best"]["block"] == (
        max(range(len(fitness_values)), key=lambda index: fitness_values[index]),
    ), blocks
    # The mean keeps the broadly good population, the median the robust
    # one: the median block is a subset of the mean block.
    assert set(blocks["median"]["block"]) <= set(blocks["mean"]["block"]), blocks
    # ... and the number of newcomers differs, so the *composition* of the
    # next population differs as well.
    assert blocks["mean"]["count"] != blocks["median"]["count"], blocks


# ---------------------------------------------------------------------------
# D5: the cheap tier promotes by merit
# ---------------------------------------------------------------------------
def test_the_cheap_tier_promotes_by_merit_and_not_by_birth_order(tmp_path: Path) -> None:
    """The promotion into the surrogate tier is decided by the merit.

    The tier-0 filter is the source of the merit: it folds the real genomes
    and writes the grip penalty and the grip quality of every candidate.
    The survivors are ranked by that merit before the cheap tier slices the
    first ones off, so the cheapest tier does not screen by birth order.
    """
    run = ResearchRun(
        GAConfig().merged(
            _overrides(tmp_path, tier1_trials=1, population_size=8)
        ).validate(),
        {},
    )
    rng = random.Random(4)
    candidates = []
    for index, name in enumerate(("dart", "nakamura", "sky_king")):
        genome = mutate_genome(genome_from_archetype(name), rng, 8)
        genes = default_genes()
        candidates.append(
            Individual(
                index=index,
                genome=genome,
                genes=genes,
                throw=ThrowParams.from_genes(genes),
            )
        )

    survivors = run._tier0_geometry(candidates)
    assert len(survivors) >= 2, "the test needs at least two feasible designs"
    qualities = [item.grip_quality for item in survivors]
    assert max(qualities) - min(qualities) > 0.05, (
        "the feasible designs must differ in their merit"
    )

    # The best ranked design is put *last* in the birth order.
    ranked = sorted(
        survivors,
        key=lambda item: (item.grip_penalty, -item.grip_quality, item.index),
    )
    best = ranked[0]
    ordered = ranked[1:] + [best]

    promoted = run._tier1_surrogate(ordered)
    assert len(promoted) == 1
    assert promoted[0].index == best.index, (
        "the cheap tier must promote the best ranked design, not the first one"
    )


# ---------------------------------------------------------------------------
# D6: the structure shift belongs to its island
# ---------------------------------------------------------------------------
def test_the_structure_shift_stays_inside_its_island(tmp_path: Path) -> None:
    """A structure shift of one island never touches another island."""
    config = GAConfig().merged(
        _overrides(
            tmp_path,
            islands=2,
            max_fold_steps=4,
            max_fold_steps_limit=6,
            stagnation_generations=1,
        )
    ).validate()
    run = ResearchRun(config, {})
    run._build_islands()
    first, second = run.islands[0], run.islands[1]
    assert first.max_fold_steps == 4
    assert second.max_fold_steps == 4

    first.improved = False
    first.stagnation_counter = 0
    decision = run._check_stagnation(first)
    assert decision is not None
    assert first.max_fold_steps == 5
    assert first.micro_flaps is True

    # The other island is untouched ...
    assert second.max_fold_steps == 4
    assert second.micro_flaps is False
    # ... and the shared configuration is untouched as well.
    assert config.max_fold_steps == 4
    assert config.micro_flaps is False

    journal_path = Path(config.resolved_journal_path())
    records = [
        json.loads(line)
        for line in journal_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    shifts = [record for record in records if record["decision"] == "structure_search_shift"]
    assert shifts, "the shift must be journalled"
    for record in shifts:
        assert int(record["island"]) == first.id, (
            "the journal must name the island the shift was applied to"
        )


# ---------------------------------------------------------------------------
# D7: the resume restores the meta state
# ---------------------------------------------------------------------------
def test_the_resume_restores_the_structure_of_every_island(tmp_path: Path) -> None:
    """A resumed run keeps the structure its islands had decided on.

    The checkpoint used to restore the islands, the hall of fame and the
    history, but not the meta state of the islands, so a resumed run lost
    the structure shift it had already decided on.
    """
    overrides = _overrides(
        tmp_path,
        islands=2,
        max_fold_steps=4,
        max_fold_steps_limit=6,
        stagnation_generations=1,
    )
    run = ResearchRun(GAConfig().merged(overrides).validate(), {})
    run._build_islands()
    first, second = run.islands[0], run.islands[1]

    # The first island stagnates and shifts its own structure.
    first.improved = False
    first.stagnation_counter = 0
    assert run._check_stagnation(first) is not None
    assert first.max_fold_steps == 5
    assert first.micro_flaps is True
    assert second.max_fold_steps == 4
    assert second.micro_flaps is False

    checkpoint_path = Path(run._save_checkpoint())
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert int(payload["islands"][0]["max_fold_steps"]) == 5
    assert payload["islands"][0]["micro_flaps"] is True
    assert int(payload["islands"][1]["max_fold_steps"]) == 4
    assert payload["islands"][1]["micro_flaps"] is False

    resumed = ResearchRun(GAConfig().merged(overrides).validate(), {})
    resumed._load_checkpoint(str(checkpoint_path))
    assert len(resumed.islands) == 2
    assert int(resumed.islands[0].max_fold_steps) == 5
    assert bool(resumed.islands[0].micro_flaps) is True
    assert int(resumed.islands[1].max_fold_steps) == 4
    assert bool(resumed.islands[1].micro_flaps) is False


# ---------------------------------------------------------------------------
# D8: the hall of fame holds the best of the whole run
# ---------------------------------------------------------------------------
def test_the_hall_of_fame_keeps_the_best_of_the_whole_run(tmp_path: Path) -> None:
    """The published best is the best the run ever found.

    The archive of the best individuals used to be filled once and then
    frozen: as soon as it held ``top_k`` entries the loop broke on its
    first iteration, so a run that found a much better design afterwards
    published the worse one it had found in its first generation.

    The archive is a **publication surface**, so it only holds the designs
    that were evaluated on the 6-DoF reference path and that flew without a
    structural failure.  A surrogate result and a candidate that was never
    evaluated are never archived, and the archive may therefore hold fewer
    than ``top_k`` entries: it holds the *verified* designs, not the number
    of candidates the run happened to produce.

    The claim is checked against the **state of the run**, never against the
    outcome of one generation: a run whose best design was found in its
    first generation and was never beaten is a legitimate run, so the test
    does not demand that the best archive member is newer than the oldest
    one.  What it demands is that the archive is the best of the *whole*
    run: it holds the designs of the whole run, it holds individuals the
    seeded population did not contain, and it was updated after the first
    generation.
    """
    overrides = _overrides(tmp_path, generations=4, islands=1, top_k=5, max_trials=30)
    config = GAConfig().merged(overrides).validate()
    events: list = []
    run = ResearchRun(config, overrides, progress_callback=events.append)
    summary = run.run()

    history = events[-1]["history"]
    assert history, "the run must publish its curve"
    best_so_far = [entry["best_fitness"] for entry in history]
    assert max(best_so_far) == pytest.approx(summary["best_fitness"]), (
        "the published best must never be worse than the live best so far"
    )
    assert best_so_far[-1] == pytest.approx(summary["best_fitness"])

    checkpoint = json.loads(Path(summary["checkpoint"]).read_text(encoding="utf-8"))
    hall = json.loads(
        (Path(summary["checkpoint"]).parent / "hall_of_fame.json").read_text(encoding="utf-8")
    )
    assert hall, "the archive must not be empty"
    assert max(float(entry["fitness"]) for entry in hall) == pytest.approx(
        summary["best_fitness"]
    )

    # Every archived design is a **verified** one: it was evaluated on the
    # 6-DoF reference path, it is not an approximation and it did not fail.
    for entry in hall:
        result = entry.get("result") or {}
        assert int(entry["tier"]) >= 2, "an archived design must carry a 6-DoF result"
        assert result.get("model") == "rigid_body_6dof", result.get("model")
        assert result.get("approximate") is False
        assert result.get("status") not in ("crashed", "diverged", "failed", "error")

    # The published best is at least as good as the best any island ever
    # found.  The island reports carry the best fitness of the population of
    # every generation, and the archive covers the whole run, so the
    # published best can never be worse than one of them.
    island_best = [
        float(report["best_fitness"])
        for entry in checkpoint["history"]
        for report in entry["reports"]
        if report.get("best_fitness") is not None
    ]
    assert island_best, "the run must report the best fitness of every generation"
    assert float(summary["best_fitness"]) >= max(island_best) - 1.0e-12, (
        "the published best must be the best the run ever found"
    )

    # The archive is the best of the **whole** run, so it is not limited to
    # the individuals of the seeded population.  The initial population is
    # rebuilt with the production bootstrap code, so its index set is the
    # one of the run itself and not an assumption about it.
    probe = ResearchRun(config, overrides)
    probe._build_islands()
    initial_indices = {
        int(individual.index)
        for island in probe.islands
        for individual in island.population
    }
    assert initial_indices, "the probe must rebuild the initial population"
    archive_indices = {int(entry["index"]) for entry in hall}

    assert len(hall) == int(config.top_k), "the archive must be full"
    assert len(hall) > len(initial_indices), (
        "the archive must hold more individuals than the initial population had"
    )
    assert archive_indices - initial_indices, (
        "the archive must hold individuals that the initial population did not "
        "contain: the hall of fame must not be frozen at the first generation"
    )

    # The archive was updated **after** the first generation: the run
    # created the individuals of its first generation first (the seeded
    # population plus the offspring of the first generation, which the
    # report counts as ``candidates``), so an archive member with a larger
    # index was found in a later generation.  The run uses a single island,
    # so the candidate count of the first generation is the number of
    # individuals the run had created when that generation ended.
    first_generation_candidates = int(
        checkpoint["history"][0]["reports"][0]["candidates"]
    )
    assert max(archive_indices) > first_generation_candidates, (
        "the archive must have been updated after the first generation: the "
        "hall of fame must not be frozen at generation 0"
    )


def test_the_hall_of_fame_admits_a_better_design_when_it_is_already_full(
    tmp_path: Path,
) -> None:
    """A full hall of fame still takes a strictly better design.

    This is the deterministic proof of the D8 fix.  The archive used to
    stop filling as soon as it held ``top_k`` entries -- the loop broke on
    its first iteration -- so a design that was better than everything in
    the archive could never enter it, and the run published the worse
    design of its first generation.

    The archive is the best of the whole run: a strictly better individual
    must enter it, a strictly worse one must not, and an individual whose
    flight failed must never enter it, no matter how full the archive is.
    """
    overrides = _overrides(tmp_path, top_k=2, islands=1)
    config = GAConfig().merged(overrides).validate()
    run = ResearchRun(config, overrides)

    run.hall_of_fame = [_archive_member(1, 1.0), _archive_member(2, 0.5)]
    assert len(run.hall_of_fame) == int(config.top_k), "the archive must start out full"

    better = _archive_member(3, 2.0)
    worse = _archive_member(4, 0.1)
    run._update_hall_of_fame([better, worse])

    indices = [int(item.index) for item in run.hall_of_fame]
    assert 3 in indices, "a strictly better design must enter a full hall of fame"
    assert 4 not in indices, "a strictly worse design must not enter the hall of fame"
    assert len(run.hall_of_fame) == int(config.top_k), "the archive must stay at its size"
    assert int(run.hall_of_fame[0].index) == 3, "the best design must be the first entry"
    assert 1 in indices, "the second best design of the archive must be kept"
    assert [float(item.fitness) for item in run.hall_of_fame] == [2.0, 1.0], (
        "the archive must be sorted by fitness"
    )

    # A design whose flight failed never enters the archive, not even when
    # it claims the best fitness of all.
    broken = _archive_member(5, 9.0, status="crashed")
    run._update_hall_of_fame([broken])
    assert 5 not in [int(item.index) for item in run.hall_of_fame], (
        "a failed evaluation must never enter the hall of fame"
    )


# ---------------------------------------------------------------------------
# D9: the full fidelity tier is a Monte-Carlo ensemble
# ---------------------------------------------------------------------------
def test_the_full_fidelity_tier_is_a_monte_carlo_ensemble(tmp_path: Path) -> None:
    """Every promoted individual is evaluated once per Monte-Carlo sample.

    The knob ``tier3_monte_carlo_samples`` used to be validated and then
    read nowhere: the full fidelity tier evaluated exactly one jittered
    case per individual, so the tier only *looked* configurable.
    """
    samples = 3
    run = ResearchRun(
        GAConfig().merged(
            _overrides(
                tmp_path,
                tier3_monte_carlo_samples=samples,
                tier3_trials=2,
                tier2_trials=4,
                tier1_trials=4,
                population_size=4,
            )
        ).validate(),
        {},
    )
    run._build_islands()
    individuals = list(run.islands[0].population)[:2]
    before = int(run.evaluation_count)

    promoted = run._tier3_full(individuals)
    assert len(promoted) == 2
    assert int(run.evaluation_count) == before + samples * len(promoted)
    assert int(run.tier_counts["tier3"]) == samples * len(promoted)

    for individual in promoted:
        assert len(individual.sample_failures) == samples
        # The metric of the individual is the aggregate of its samples: the
        # samples are reproducible, so they can be evaluated again.
        cases = []
        for sample in range(samples):
            throw_rng = random.Random(run._jitter_seed(individual, sample))
            throw = run._jittered_throw(individual.throw, throw_rng)
            cases.append(
                {
                    "aircraft": run._aircraft_payload(individual),
                    "environment": run._environment(
                        3, seed=run._jitter_seed(individual, sample)
                    ),
                    "launch": run._launch_payload(throw, run.config.launch_height_m),
                    "duration_s": run._tier3_duration(),
                    "dt_max": float(run.config.dt_max),
                }
            )
        again = run._evaluate_batch(cases)
        metrics = [run.objective.extract(result) for result in again]
        assert individual.metric == pytest.approx(sum(metrics) / len(metrics))


def test_the_monte_carlo_ensemble_stays_inside_the_budget(tmp_path: Path) -> None:
    """The extra flights of the ensemble are counted and never overrun."""
    summary = _run(
        tmp_path,
        generations=2,
        islands=1,
        tier3_monte_carlo_samples=3,
        tier3_trials=2,
        max_trials=40,
    )
    assert int(summary["evaluations"]) <= 40
    promoted = sum(
        int(report["tier3"])
        for entry in json.loads(Path(summary["checkpoint"]).read_text(encoding="utf-8"))[
            "history"
        ]
        for report in entry["reports"]
    )
    # The seeded population is evaluated before the first generation, so the
    # flights of that round belong to the count as well; the journal says
    # how many they were.
    initial = [
        record
        for record in (
            json.loads(line)
            for line in Path(
                str(summary["config"]["out_dir"]), "research_journal.jsonl"
            )
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        )
        if record["decision"] == "initial_evaluation"
    ]
    assert initial, "the evaluation of the seeded population must be journalled"
    initial_flights = int(initial[0]["changes"]["tier3_flights"])
    assert int(summary["tier_counts"]["tier3"]) == 3 * promoted + initial_flights



# ---------------------------------------------------------------------------
# P0 (Y-02): the fitness carries no free coefficient
# ---------------------------------------------------------------------------
def test_the_fitness_carries_no_free_coefficient(tmp_path: Path) -> None:
    """The fitness of the engine is dimensionless and free of tuned weights.

    The contract (``AGENTS.md`` §1.5 and the fitness design of the research
    programme) is that the fitness carries **no** weight without a physical
    source.  The old formula mixed a weighted sum of secondary terms
    (``0.25``), a reference value without a source (``8.0``) and an
    additive grip penalty (``0.01``): every one of them was a free
    coefficient that moved the search without a physical meaning.

    The test is therefore **structural**: it reads the source of the
    fitness and checks that the only numeric literals it uses are the two
    constants of the grace factors (``1`` and ``0``), and it checks that
    the configuration and the objective carry no weight at all.
    """
    import ast
    import inspect
    import textwrap

    from physics.ga_domain import ResearchRun

    source = textwrap.dedent(inspect.getsource(ResearchRun._individual_fitness))
    tree = ast.parse(source)
    literals = {
        float(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    }
    assert literals <= {0.0, 1.0}, (
        f"the fitness must not carry a free coefficient: {sorted(literals)}"
    )

    config = GAConfig()
    for name in ("grip_penalty_weight", "max_secondary_weight", "sensitivity_threshold"):
        assert not hasattr(config, name), (
            f"the configuration must not carry a fitness weight: {name}"
        )

    spec = objective_spec("distance")
    assert not hasattr(spec, "secondary_references"), (
        "the objective must not carry a reference value without a source"
    )
    assert not hasattr(spec, "secondary_metric_keys")
    # The only reference the fitness uses is the world record of the
    # objective, and that one has a source.
    assert spec.reference_target == pytest.approx(88.31)


def test_the_grip_penalty_enters_the_fitness_as_a_grace_factor(
    tmp_path: Path,
) -> None:
    """The grip penalty scales the merit, it is not a penalty added to it.

    The design of the fitness multiplies the merit with the grace factors
    of the constraints, so a design that is hard to hold is worth *less in
    proportion*.  The old formula subtracted ``1 + 0.01 * P_grip`` from the
    merit, which made a small penalty invisible next to the constant and
    let a bad design outrank a good one.
    """
    from physics.ga_domain import INFEASIBLE_SCORE

    run = ResearchRun(GAConfig().merged(_overrides(tmp_path)).validate(), {})

    def design(metric: float, grip_penalty: float = 0.0) -> Individual:
        genes = default_genes()
        return Individual(
            index=1,
            genome=genome_from_archetype("dart"),
            genes=genes,
            throw=ThrowParams.from_genes(genes),
            tier=3,
            metric=metric,
            feasible=True,
            grip_penalty=grip_penalty,
            result={
                "status": "landed",
                "max_stress_pa": 0.0,
                "model": "rigid_body_6dof",
                "approximate": False,
            },
        )

    reference = float(objective_spec("distance").reference_target)
    # a feasible design is worth exactly its merit: no additive constant
    assert run._individual_fitness(design(reference)) == pytest.approx(1.0)
    # the grip penalty is a grace factor: 1/(1 + P_grip)
    assert run._individual_fitness(design(reference, grip_penalty=1.0)) == pytest.approx(0.5)
    assert run._individual_fitness(design(reference, grip_penalty=3.0)) == pytest.approx(0.25)
    # a design that was never evaluated (or whose flight failed) is infeasible
    unevaluated = design(reference)
    unevaluated.result = {}
    assert run._individual_fitness(unevaluated) == pytest.approx(float(INFEASIBLE_SCORE))


# ---------------------------------------------------------------------------
# P0 (Y-06): the published result carries its provenance
# ---------------------------------------------------------------------------
def test_the_published_result_carries_its_provenance(tmp_path: Path) -> None:
    """A published flight says which model produced it.

    The contract of ``docs/SPECIFICATION.md`` §2.1 is that a reader can
    always tell *which model* produced a number, whether that model is an
    approximation, whether the number may be used for a physics
    verification and what the result does not know.  The published payload
    used to carry none of it, and the scalar filter of the flight result
    dropped the limitations on the way.
    """
    summary = _run(tmp_path, generations=1, islands=1)
    result = summary["result"]
    for key in ("model", "approximate", "physics_verification_eligible", "limitations"):
        assert key in result, key
    assert result["model"] == "rigid_body_6dof"
    assert result["approximate"] is False
    assert result["physics_verification_eligible"] is True
    assert isinstance(result["limitations"], list)

    flight = result["flight_result"]
    assert flight["model"] == result["model"]
    assert flight["approximate"] is False
    assert flight["physics_verification_eligible"] is True
    # the limitations are a list of sentences: the scalar filter of the
    # flight result must not drop them
    assert isinstance(flight["limitations"], list)
    assert flight["limitations"] == result["limitations"]


def test_the_api_publishes_the_provenance_of_the_result(tmp_path: Path) -> None:
    """The HTTP answer carries the four provenance keys as well."""
    import time

    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        response = client.post(
            "/api/ga/research/run",
            json={
                "objective": "distance",
                "max_trials": 6,
                "top_k": 2,
                "tier1_trials": 2,
                "tier2_trials": 1,
                "tier3_trials": 1,
                "tier2_duration_s": 0.12,
                "tier3_duration_s": 0.15,
                "seed": 5,
                "generations": 1,
                "islands": 1,
                "output_dir": str(tmp_path / "api"),
            },
        )
        assert response.status_code == 200
        run_id = response.get_json()["run_id"]

        # the run executes in the background: the answer of a running job
        # already carries the provenance keys ...
        payload = client.get(f"/api/ga/research/result/{run_id}").get_json()
        for key in (
            "model",
            "approximate",
            "physics_verification_eligible",
            "limitations",
        ):
            assert key in payload, key
        assert isinstance(payload["approximate"], bool)
        assert isinstance(payload["physics_verification_eligible"], bool)
        assert isinstance(payload["limitations"], list)

        # ... and so does the answer of the finished run
        deadline = time.time() + 300.0
        while time.time() < deadline:
            status = client.get(f"/api/ga/research/status/{run_id}").get_json()
            if status.get("status") in ("completed", "failed", "budget_exhausted"):
                break
            time.sleep(0.5)
        payload = client.get(f"/api/ga/research/result/{run_id}").get_json()
        for key in (
            "model",
            "approximate",
            "physics_verification_eligible",
            "limitations",
        ):
            assert key in payload, key
        assert isinstance(payload["approximate"], bool)
        assert isinstance(payload["physics_verification_eligible"], bool)
        assert isinstance(payload["limitations"], list)
        if payload.get("model") == "rigid_body_6dof":
            # a verified flight is not an approximation and may be used for
            # a physics verification
            assert payload["approximate"] is False
            assert payload["physics_verification_eligible"] is True
        else:
            # anything else is an approximation and never verification
            # eligible
            assert payload["approximate"] is True
            assert payload["physics_verification_eligible"] is False



# ---------------------------------------------------------------------------
# P0 (Y-21): the published optimum is never a surrogate result
# ---------------------------------------------------------------------------
def test_the_published_optimum_is_never_a_surrogate_result(tmp_path: Path) -> None:
    """A design that was only flown on the reduced path is never published.

    The reduced (surrogate) paths are closed-form approximations without an
    attitude integration (``docs/SPECIFICATION.md`` §2.1).  They are the
    cheap screen of the search, but a design that was never flown on the
    6-DoF reference path must never be published as the optimum of the
    research programme.
    """
    summary = _run(tmp_path, generations=2, islands=1, tier2_trials=0, tier3_trials=0)
    result = summary["result"]
    assert int(result["tier"]) != 1, "a surrogate result must never be published"
    assert result["model"] != "reduced_surrogate"
    # nothing was published, and the payload says so honestly
    assert result["model"] == "unknown"
    assert result["approximate"] is True
    assert result["physics_verification_eligible"] is False
    assert result["fitness"] is None

    # the archive of the best individuals is empty as well
    hall = json.loads(
        Path(
            str(summary["config"]["out_dir"]), "checkpoints", "hall_of_fame.json"
        ).read_text(encoding="utf-8")
    )
    assert hall == [], "a surrogate result must never enter the archive"

    # with the 6-DoF tiers enabled the run publishes a verified design
    verified = _run(tmp_path / "verified", generations=2, islands=1)
    published = verified["result"]
    assert published["model"] == "rigid_body_6dof"
    assert published["approximate"] is False
    assert int(published["tier"]) >= 2


# ---------------------------------------------------------------------------
# P0 (Y-23): the seeded population is evaluated, not assumed
# ---------------------------------------------------------------------------
def test_the_seeded_population_is_evaluated_before_it_is_ranked(
    tmp_path: Path,
) -> None:
    """The initial population of generation 0 is flown before it is ranked.

    The engine used to breed the first generation from a population that
    had never been evaluated: the seeded designs carried an empty flight
    result, and the engine could not tell that apart from a legitimate
    flight.  They were ranked as if they had flown (with a fitness of
    ``-1.0``, far above the infeasible score of a failed evaluation) and
    they entered the archive of the best designs.
    """
    from physics.ga_domain import INFEASIBLE_SCORE

    config = GAConfig().merged(_overrides(tmp_path)).validate()
    run = ResearchRun(config, {})
    run._build_islands()
    seeded = list(run.islands[0].population)
    assert seeded, "the run must seed a population"
    for individual in seeded:
        # a design that was never flown carries no result at all ...
        assert not individual.result
        # ... so it is infeasible and never ranks above a failed evaluation
        assert run._is_infeasible(individual)
        assert run._individual_fitness(individual) == pytest.approx(float(INFEASIBLE_SCORE))

    # the run evaluates the seeded population before the first generation
    # breeds from it, and the decision is journalled
    summary = _run(tmp_path, generations=1, islands=1)
    journal = [
        json.loads(line)
        for line in Path(str(summary["config"]["out_dir"]), "research_journal.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    initial = [record for record in journal if record["decision"] == "initial_evaluation"]
    assert initial, "the evaluation of the seeded population must be journalled"
    assert int(initial[0]["changes"]["screened"]) >= 4
    assert int(initial[0]["changes"]["evaluated"]) >= 1

    # the seeded designs are screened in the run as well
    assert int(summary["tier_counts"]["tier0"]) > int(
        summary["config"]["population_size"]
    ), "the seeded population must be screened before the first generation"

    # and the archive holds no design that was never evaluated
    hall = json.loads(
        Path(
            str(summary["config"]["out_dir"]), "checkpoints", "hall_of_fame.json"
        ).read_text(encoding="utf-8")
    )
    for entry in hall:
        assert entry.get("result"), "an archived design must carry a flight result"
        assert int(entry["tier"]) >= 2

# ---------------------------------------------------------------------------
# P1 (Y-03 / Y-05 / Y-12): the determinism conventions of the engine
# ---------------------------------------------------------------------------
def test_the_environment_seed_depends_on_the_generation(tmp_path: Path) -> None:
    """A design is never flown in the same air twice.

    The seed of the environment is derived from the individual **and the
    generation**, so the turbulence of a design in generation 3 differs
    from the turbulence of the same design in generation 4.  A seed that
    ignored the generation would fly every generation in the same gusts.
    """
    config = GAConfig().merged(_overrides(tmp_path)).validate()
    run = ResearchRun(config, {})
    run._build_islands()
    individual = run.islands[0].population[0]
    individual.metric = 1.0

    captured: list = []

    def capture(cases):
        captured.append([dict(case) for case in cases])
        return [{} for _ in cases]

    run._evaluate_batch = capture  # type: ignore[method-assign]
    run.generation = 0
    run._tier2_reference([individual])
    run.generation = 1
    run._tier2_reference([individual])

    first = int(captured[0][0]["environment"]["turbulence_seed"])
    second = int(captured[1][0]["environment"]["turbulence_seed"])
    assert first != second, "the seed of the air must depend on the generation"


def test_a_short_batch_answer_is_an_error_and_not_a_silent_drop(
    tmp_path: Path,
) -> None:
    """A batch that answers short is refused, not truncated.

    The engine used to zip the cases with the results, so a batch that
    returned fewer answers than cases dropped the tail of the batch
    without a word: the design was published as if it had flown.
    """
    config = GAConfig().merged(_overrides(tmp_path)).validate()
    run = ResearchRun(config, {})
    run._build_islands()
    individual = run.islands[0].population[0]
    individual.metric = 1.0

    def short_batch(cases):
        return [{} for _ in cases[:-1]]

    run._evaluate_batch = short_batch  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        run._tier2_reference([individual])

    config = GAConfig().merged(_overrides(tmp_path)).validate()
    run = ResearchRun(config, {})
    run._build_islands()
    individual = run.islands[0].population[0]
    individual.metric = 1.0
    run._evaluate_batch = short_batch  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        run._tier3_full([individual])


def test_a_tie_is_broken_by_the_identity_of_the_candidate(tmp_path: Path) -> None:
    """The order of the candidates is total and stable.

    The design orders the candidates by the fitness first and by the
    identity of the candidate second, and it uses that one order
    everywhere.  The archive used to break a tie the other way round, so
    two places of the engine ranked the same two candidates differently.
    """
    from physics.ga_domain import _rank_by_fitness

    genes = default_genes()
    genome = genome_from_archetype("dart")
    candidates = [
        Individual(
            index=index,
            genome=genome,
            genes=genes,
            throw=ThrowParams.from_genes(genes),
            tier=3,
            fitness=fitness,
        )
        for index, fitness in ((3, 0.5), (1, 0.5), (2, 0.9))
    ]
    ranked = _rank_by_fitness(candidates)
    assert [item.index for item in ranked] == [2, 1, 3], (
        "the fitness decides, the identity of the candidate breaks the tie"
    )
    # the order does not depend on the order in which the candidates were
    # created
    assert [item.index for item in _rank_by_fitness(list(reversed(candidates)))] == [
        2,
        1,
        3,
    ]
