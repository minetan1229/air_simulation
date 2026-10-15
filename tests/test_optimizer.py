"""Verification suite for :mod:`physics.parallel_engine` and :mod:`physics.optimizer_domain`.

The suite is deliberately split into two evaluation paths:

* the **analytical surrogate** of :func:`physics.parallel_engine.evaluate_flight_surrogate`
  (forced with the :func:`surrogate_path` fixture) is used wherever a numeric
  assertion about the *optimiser machinery* is needed -- it is deterministic and
  microseconds fast, so five algorithms fit comfortably in the time budget;
* the **real integrator** is exercised by
  :func:`test_real_integrator_path_returns_the_flight_contract`, which only
  asserts the payload contract, so the suite stays green whether or not
  ``physics/flight_sim.py`` is importable yet.
"""

from __future__ import annotations

import math
import os
import pickle
from typing import Any, Dict, List

import numpy as np
import pytest

from physics import parallel_engine
from physics.config_schema import (
    OBJECTIVE_DIRECTIONS,
    AircraftConfig,
    DesignVariable,
    EnvironmentConfig,
    LaunchConfig,
    OptimizerConfig,
    as_dict,
)
from physics.optimizer_domain import (
    ALGORITHM_DISPATCH,
    OBJECTIVE_KEYS,
    OBJECTIVE_SOURCES,
    RESULT_KEYS,
    SurrogateModel,
    aggregate_score,
    build_surrogate,
    config_to_vector,
    crowding_distance,
    dominates,
    evaluate_vector,
    grid_points,
    latin_hypercube,
    non_dominated_sort,
    objective_direction,
    objective_value,
    pareto_front,
    polynomial_mutation,
    run_optimizer,
    sbx_crossover,
    vector_to_config,
)
from physics.parallel_engine import backend_name

ALGORITHMS = ("grid", "nelder-mead", "differential-evolution", "nsga2", "surrogate")
FAST_VARIABLES = ["wingspan_m", "wing_area_m2", "aspect_ratio", "paper_gsm", "launch_speed"]


def _report_worker_pid(_ignored: Any) -> int:
    """Module level probe: reports the pid of whichever process runs it."""
    return os.getpid()


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------
@pytest.fixture()
def surrogate_path(monkeypatch: pytest.MonkeyPatch) -> bool:
    """Force the analytical surrogate, bypassing the (optional) 6-DoF integrator."""
    monkeypatch.setattr(parallel_engine, "_flight_module", lambda: None)
    return True


def make_cfg(algorithm: str = "grid", **overrides: Any) -> OptimizerConfig:
    """Small, fast optimiser configuration used across the suite."""
    params: Dict[str, Any] = {
        "objectives": ["max_distance", "min_max_stress"],
        "weights": {"max_distance": 1.0, "min_max_stress": 0.5},
        "design_variables": list(FAST_VARIABLES),
        "max_evaluations": 18,
        "population_size": 9,
        "seed": 7,
        "processes": 1,
        "sim_duration_s": 1.0,
        "dt_max": 0.05,
    }
    params.update(overrides)
    config = OptimizerConfig(**params)
    config.algorithm = algorithm
    return config


def make_case(speed: float = 12.0, duration_s: float = 1.0, dt_max: float = 0.05) -> Dict[str, Any]:
    return {
        "aircraft": as_dict(AircraftConfig()),
        "environment": as_dict(EnvironmentConfig()),
        "launch": as_dict(LaunchConfig(launch_speed=speed)),
        "duration_s": duration_s,
        "dt_max": dt_max,
    }


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


# ==========================================================================
# 1. Objective contract
# ==========================================================================
def test_objective_value_maps_all_seven_names_to_the_right_key() -> None:
    flight = {
        "distance_m": 11.5,
        "flight_time_s": 4.25,
        "max_ld_ratio": 6.5,
        "max_stress_index": 0.375,
        "stall_margin": 0.8125,
        "launch_speed_mps": 9.75,
        "stability": 0.9375,
    }
    expected = {
        "max_distance": ("distance_m", 11.5),
        "max_flight_time": ("flight_time_s", 4.25),
        "max_ld_ratio": ("max_ld_ratio", 6.5),
        "min_max_stress": ("max_stress_index", 0.375),
        "max_stall_margin": ("stall_margin", 0.8125),
        "min_launch_speed": ("launch_speed_mps", 9.75),
        "max_stability": ("stability", 0.9375),
    }
    assert tuple(expected) == OBJECTIVE_KEYS
    for name, (key, value) in expected.items():
        assert OBJECTIVE_SOURCES[name] == key
        # raw, physically signed value taken verbatim from the flight payload
        assert objective_value(name, flight) == pytest.approx(value, rel=1e-12, abs=1e-12)
        # the sign convention lives in the direction table, not in the value
        assert objective_direction(name) == OBJECTIVE_DIRECTIONS[name]
    with pytest.raises(ValueError):
        objective_value("not_an_objective", flight)


def test_aggregate_score_respects_direction_and_weights() -> None:
    objectives = ["max_distance", "min_max_stress"]
    low_stress = {"max_distance": 10.0, "min_max_stress": 0.20}
    high_stress = {"max_distance": 10.0, "min_max_stress": 0.80}

    weights = {"max_distance": 1.0, "min_max_stress": 1.0}
    assert aggregate_score(low_stress, weights, objectives) > aggregate_score(high_stress, weights, objectives)

    near = {"max_distance": 12.0, "min_max_stress": 0.20}
    far = {"max_distance": 6.0, "min_max_stress": 0.20}
    assert aggregate_score(near, weights, objectives) > aggregate_score(far, weights, objectives)

    base = aggregate_score(low_stress, weights, objectives)
    doubled = aggregate_score(low_stress, {"max_distance": 2.0, "min_max_stress": 1.0}, objectives)
    assert doubled == pytest.approx(base + low_stress["max_distance"] / 25.0, rel=1e-12)

    zero_weight = aggregate_score(low_stress, {"max_distance": 0.0, "min_max_stress": 0.0}, objectives)
    assert zero_weight == pytest.approx(0.0, abs=1e-12)


# ==========================================================================
# 2. Vector <-> configuration
# ==========================================================================
def test_vector_to_config_deep_copies_and_roundtrips() -> None:
    cfg = make_cfg()
    variables = cfg.resolved_variables()
    vector = [0.21, 0.019, 2.4, 130.0, 17.5]

    clone = vector_to_config(vector, variables, cfg)

    assert clone is not cfg
    assert clone.aircraft is not cfg.aircraft
    assert clone.launch is not cfg.launch
    assert clone.aircraft.wingspan_m == pytest.approx(0.21)
    assert clone.aircraft.wing_area_m2 == pytest.approx(0.019)
    assert clone.aircraft.aspect_ratio == pytest.approx(2.4)
    assert clone.aircraft.paper_gsm == pytest.approx(130.0)
    assert clone.launch.launch_speed == pytest.approx(17.5)

    # ``base`` must be untouched.
    assert cfg.aircraft.wingspan_m == AircraftConfig().wingspan_m
    assert cfg.aircraft.paper_gsm == AircraftConfig().paper_gsm
    assert cfg.launch.launch_speed == LaunchConfig().launch_speed

    recovered = config_to_vector(clone, variables)
    for got, want in zip(recovered, vector):
        assert got == pytest.approx(want, rel=1e-12, abs=1e-12)

    # out-of-range components are clipped into the declared bounds
    clipped = vector_to_config([99.0, -5.0, 0.0, 0.0, 0.0], variables, cfg)
    assert clipped.aircraft.wingspan_m == pytest.approx(variables[0].high)
    assert clipped.aircraft.wing_area_m2 == pytest.approx(variables[1].low)


# ==========================================================================
# 3. Pareto machinery against a hand-made, known answer
# ==========================================================================
KNOWN_ROWS: List[Dict[str, float]] = [
    {"max_distance": 10.0, "min_max_stress": 0.50},  # 0 A  non-dominated (long but heavy)
    {"max_distance": 8.0, "min_max_stress": 0.20},   # 1 B  non-dominated (short but light)
    {"max_distance": 6.0, "min_max_stress": 0.30},   # 2 C  dominated by B
    {"max_distance": 4.0, "min_max_stress": 0.40},   # 3 D  dominated by B and C
    {"max_distance": 2.0, "min_max_stress": 0.60},   # 4 E  dominated by everybody
    {"max_distance": 10.0, "min_max_stress": 0.50},  # 5 F  identical to A -> non-dominated
]
KNOWN_FRONTS = [[0, 1, 5], [2], [3], [4]]


def test_non_dominated_sort_recovers_the_known_fronts() -> None:
    fronts = non_dominated_sort(["max_distance", "min_max_stress"], KNOWN_ROWS)
    assert fronts == KNOWN_FRONTS
    assert pareto_front(["max_distance", "min_max_stress"], KNOWN_ROWS) == [0, 1, 5]
    # the same answer from a dense matrix, columns in objective order
    matrix = [[row["max_distance"], row["min_max_stress"]] for row in KNOWN_ROWS]
    assert non_dominated_sort(["max_distance", "min_max_stress"], np.asarray(matrix)) == KNOWN_FRONTS


def test_dominates_respects_both_directions() -> None:
    objectives = ["max_distance", "min_max_stress"]
    a, b = KNOWN_ROWS[1], KNOWN_ROWS[2]  # B vs C
    assert dominates(a, b, objectives) is True
    assert dominates(b, a, objectives) is False
    assert dominates(KNOWN_ROWS[0], KNOWN_ROWS[1], objectives) is False  # trade-off
    assert dominates(KNOWN_ROWS[1], KNOWN_ROWS[0], objectives) is False
    assert dominates(KNOWN_ROWS[0], KNOWN_ROWS[5], objectives) is False  # identical -> False
    assert dominates(KNOWN_ROWS[2], KNOWN_ROWS[3], objectives) is True


def test_crowding_distance_hand_checked() -> None:
    objectives = ["max_distance", "min_max_stress"]
    front = [0, 1, 5]
    distances = crowding_distance(objectives, KNOWN_ROWS, front)
    assert len(distances) == 3
    # rows 1 and 5 are the boundary of both objective ranges
    assert math.isinf(distances[1])
    assert math.isinf(distances[2])
    # row 0 sits strictly inside both ranges: (10-8)/(10-8) + (0.5-0.2)/(0.5-0.2)
    assert math.isfinite(distances[0])
    assert distances[0] == pytest.approx(2.0, rel=1e-12)
    assert crowding_distance(objectives, KNOWN_ROWS, [0]) == [math.inf]
    assert crowding_distance(objectives, KNOWN_ROWS, [0, 1]) == [math.inf, math.inf]


def test_crowding_distance_identical_scores_no_bias() -> None:
    objectives = ["max_distance"]
    identical_rows = [
        {"distance_m": 10.0},
        {"distance_m": 10.0},
        {"distance_m": 10.0},
        {"distance_m": 10.0},
    ]
    distances = crowding_distance(objectives, identical_rows, [0, 1, 2, 3])
    # When spread is 0, no individual should falsely receive inf
    assert all(d == 0.0 for d in distances)



# ==========================================================================
# 4. Variation operators
# ==========================================================================
def test_sbx_crossover_respects_bounds_and_probability() -> None:
    variables = [
        DesignVariable("a", 0.0, 1.0, 0.0),
        DesignVariable("b", -10.0, 5.0, 0.0),
        DesignVariable("c", 2.0, 3.0, 0.0),
    ]
    rng = np.random.default_rng(1234)
    parent_a = [0.2, -4.0, 2.1]
    parent_b = [0.8, 3.0, 2.9]

    child_a, child_b = sbx_crossover(parent_a, parent_b, variables, rng, prob=1.0)
    assert child_a != parent_a or child_b != parent_b
    for child in (child_a, child_b):
        for value, variable in zip(child, variables):
            assert variable.low - 1e-12 <= value <= variable.high + 1e-12

    untouched_a, untouched_b = sbx_crossover(parent_a, parent_b, variables, rng, prob=0.0)
    assert untouched_a == parent_a
    assert untouched_b == parent_b
    # the parents themselves are never mutated in place
    assert parent_a == [0.2, -4.0, 2.1]
    assert parent_b == [0.8, 3.0, 2.9]


def test_polynomial_mutation_respects_bounds_and_constant_box() -> None:
    variables = [
        DesignVariable("a", 0.12, 0.36, 0.02),
        DesignVariable("b", 60.0, 220.0, 20.0),
    ]
    rng = np.random.default_rng(99)
    start = [0.20, 120.0]
    for _ in range(50):
        mutated = polynomial_mutation(start, variables, rng, prob=1.0)
        for value, variable in zip(mutated, variables):
            assert variable.low - 1e-12 <= value <= variable.high + 1e-12

    constant = polynomial_mutation([0.1, 0.9, 0.4], None, rng, prob=1.0, lower=2.5, upper=2.5)
    assert constant == [2.5, 2.5, 2.5]

    unit = polynomial_mutation([0.5, 0.5], None, rng, prob=1.0, lower=0.0, upper=1.0)
    for value in unit:
        assert 0.0 <= value <= 1.0


def test_grid_points_enumerate_the_declared_axes() -> None:
    variables = [
        DesignVariable("a", 0.0, 1.0, 0.5),   # 0.0 0.5 1.0
        DesignVariable("b", 0.0, 2.0, 0.0),   # continuous -> low/mid/high
    ]
    points = grid_points(variables)
    assert points == [
        [0.0, 0.0],
        [0.0, 1.0],
        [0.0, 2.0],
        [0.5, 0.0],
        [0.5, 1.0],
        [0.5, 2.0],
        [1.0, 0.0],
        [1.0, 1.0],
        [1.0, 2.0],
    ]


# ==========================================================================
# 5. Every algorithm runs and honours the budget
# ==========================================================================
@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_algorithm_payload_is_complete_and_finite(surrogate_path: bool, algorithm: str) -> None:
    cfg = make_cfg(algorithm)
    seen: List[int] = []
    result = run_optimizer(cfg, lambda done, total, _best: seen.append(done))

    assert set(result) == set(RESULT_KEYS)
    assert result["algorithm"] == algorithm
    assert result["status"] in ("completed", "cancelled")
    assert result["status"] == "completed"
    assert result["objectives"] == cfg.objectives
    assert result["design_variables"] == list(FAST_VARIABLES)
    assert result["seed"] == cfg.seed
    assert result["backend"] == "serial"
    assert 1 <= result["evaluations"] <= cfg.max_evaluations
    assert result["elapsed_s"] >= 0.0 and math.isfinite(result["elapsed_s"])
    assert finite(result["best_score"])
    assert len(result["best_vector"]) == len(FAST_VARIABLES)
    assert all(finite(value) for value in result["best_vector"])
    assert set(result["best_params"]) == set(FAST_VARIABLES)
    assert set(result["best_objectives"]) == set(cfg.objectives)
    assert all(finite(value) for value in result["best_objectives"].values())

    assert len(result["history"]) == result["evaluations"]
    assert [row["evaluation"] for row in result["history"]] == list(range(1, result["evaluations"] + 1))
    assert all(finite(row["score"]) for row in result["history"])

    assert result["pareto_front"]
    for member in result["pareto_front"]:
        assert set(member) == {"objectives", "vector", "params", "physics"}
        assert set(member["objectives"]) == set(cfg.objectives)
        assert len(member["vector"]) == len(FAST_VARIABLES)
        assert all(finite(value) for value in member["vector"])
    # the incumbent is always a member of the returned front
    assert result["best_vector"] in [member["vector"] for member in result["pareto_front"]]

    assert seen == sorted(seen)
    assert seen[-1] == result["evaluations"]
    assert result["best_score"] == max(row["score"] for row in result["history"])


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_algorithm_stops_immediately_when_cancelled(surrogate_path: bool, algorithm: str) -> None:
    cfg = make_cfg(algorithm)
    calls: List[bool] = []

    def cancel_cb() -> bool:
        calls.append(True)
        return True

    result = run_optimizer(cfg, None, cancel_cb)
    assert result["status"] == "cancelled"
    assert result["evaluations"] == 0
    assert calls, "cancel_cb must be polled at least once before the first evaluation"


def test_progress_callback_is_monotonic_and_reaches_total(surrogate_path: bool) -> None:
    cfg = make_cfg("nsga2", max_evaluations=24, population_size=8)
    totals: List[int] = []
    done: List[int] = []
    run_optimizer(cfg, lambda evaluated, total, best: (done.append(evaluated), totals.append(total), best))
    assert done == sorted(done)
    assert all(total > 0 for total in totals)
    assert done[-1] == cfg.max_evaluations


def test_grid_search_caps_deterministically(surrogate_path: bool) -> None:
    cfg = make_cfg("grid", max_evaluations=12, population_size=8)
    first = run_optimizer(cfg)
    second = run_optimizer(cfg)
    assert first["evaluations"] == 12
    assert first["best_vector"] == second["best_vector"]
    assert first["best_score"] == pytest.approx(second["best_score"])
    assert first["history"] == second["history"]


# ==========================================================================
# 6. NSGA-II specifics
# ==========================================================================
def test_nsga2_front_is_non_dominated_and_never_worse(surrogate_path: bool) -> None:
    cfg = make_cfg("nsga2", max_evaluations=30, population_size=12, seed=5)
    objectives = cfg.objectives
    result = run_optimizer(cfg)
    front = result["pareto_front"]

    assert len(front) >= 2
    assert result["status"] == "completed"
    assert result["evaluations"] <= cfg.max_evaluations

    rows = [member["objectives"] for member in front]
    assert pareto_front(objectives, rows) == list(range(len(rows)))
    for first in range(len(rows)):
        for second in range(len(rows)):
            if first == second:
                continue
            assert dominates(rows[first], rows[second], objectives) is False
    assert len({tuple(member["vector"]) for member in front}) == len(front)

    # reconstruct the initial population (same seed, same sampler) and check the
    # elitist (mu + lambda) replacement never lost ground
    variables = cfg.resolved_variables()
    lows = [variable.low for variable in variables]
    highs = [variable.high for variable in variables]
    initial = latin_hypercube(12, lows, highs, np.random.default_rng(cfg.seed))
    initial_scores = [evaluate_vector(vector, cfg)["score"] for vector in initial]
    assert result["best_score"] >= sum(initial_scores) / len(initial_scores)


# ==========================================================================
# 7. Surrogate model + surrogate assisted search
# ==========================================================================
def _quadratic_sample(count: int = 9, seed: int = 4) -> Any:
    rng = np.random.default_rng(seed)
    X = rng.random((count, 2))
    y = -((X[:, 0] - 0.80) ** 2 + (X[:, 1] - 0.65) ** 2)
    return X, y


def test_surrogate_model_reproduces_training_points() -> None:
    X, y = _quadratic_sample()
    model = SurrogateModel(k_nn=3, rbf_gamma=0.6).fit(X, y)
    predicted = model.predict(X)
    for got, want in zip(predicted, y):
        assert abs(got - want) <= 1.0e-6 * max(1.0, abs(want))


def test_expected_improvement_is_positive_and_peaks_near_the_incumbent() -> None:
    X, y = _quadratic_sample()
    model = SurrogateModel(k_nn=3, rbf_gamma=0.6).fit(X, y)
    best = float(np.max(y))
    incumbent = int(np.argmax(y))

    grid = np.linspace(0.0, 1.0, 21)
    mesh_x, mesh_y = np.meshgrid(grid, grid)
    candidates = np.column_stack([mesh_x.ravel(), mesh_y.ravel()])
    ei = model.expected_improvement(candidates, best, maximise=True, xi=0.001)

    assert np.all(ei >= 0.0)
    assert np.all(np.isfinite(ei))

    near = np.array([[X[incumbent, 0] + 0.02, X[incumbent, 1] - 0.01]])
    far = np.array([[0.0, 0.0]])
    assert model.expected_improvement(near, best, xi=0.001)[0] > model.expected_improvement(
        far, best, xi=0.001
    )[0]
    # minimisation mirrors the problem
    assert np.all(model.expected_improvement(candidates, float(np.min(y)), maximise=False) >= 0.0)


def test_surrogate_suggest_stays_in_bounds() -> None:
    X, y = _quadratic_sample()
    model = SurrogateModel(k_nn=3, rbf_gamma=0.5).fit(X, y)
    bounds = [(0.2, 0.9), (-1.0, 4.0)]
    candidates = model.suggest(6, np.random.default_rng(8), bounds, maximise=True)
    assert len(candidates) == 6
    for candidate in candidates:
        assert len(candidate) == 2
        for value, (low, high) in zip(candidate, bounds):
            assert low - 1e-12 <= value <= high + 1e-12
    ei = model.expected_improvement(np.asarray(candidates), float(np.max(y)), maximise=True)
    assert np.all(ei >= 0.0)


def test_build_surrogate_is_warm_started_from_real_evaluations(surrogate_path: bool) -> None:
    cfg = make_cfg("surrogate", max_evaluations=18, population_size=6)
    model = build_surrogate(cfg)
    assert model.fitted is True
    assert model.X_raw.shape == (6, len(FAST_VARIABLES))
    assert model.y.shape == (6,)
    assert np.all(np.isfinite(model.y))


def test_surrogate_needs_fewer_evaluations_than_differential_evolution(surrogate_path: bool) -> None:
    """Observed on the analytical surrogate, 5 variables, budget 40, seed 23:

    * ``surrogate``              -> 23 evaluations, best score 2.37226
    * ``differential-evolution`` -> 40 evaluations, best score 2.34744
    """
    cfg = make_cfg(algorithm="surrogate", max_evaluations=40, population_size=20, seed=23)
    surrogate_result = run_optimizer(cfg)
    de_result = run_optimizer(
        make_cfg(algorithm="differential-evolution", max_evaluations=40, population_size=20, seed=23)
    )

    assert surrogate_result["evaluations"] < de_result["evaluations"]
    assert de_result["evaluations"] == 40
    assert surrogate_result["best_score"] >= 0.99 * de_result["best_score"]


# ==========================================================================
# 8. Parallel engine
# ==========================================================================
def test_worker_count_clamps_and_backend_labels() -> None:
    cpu = os.cpu_count() or 1
    assert backend_name(1) == "serial"
    assert backend_name(0) == "multiprocessing"
    assert backend_name(2) == "multiprocessing"
    assert parallel_engine.get_worker_count(1) == 1
    assert parallel_engine.get_worker_count(-5) == max(1, min(cpu, parallel_engine.MAX_AUTO_WORKERS))
    assert parallel_engine.get_worker_count(0) >= 1
    assert parallel_engine.get_worker_count(999) == cpu
    assert parallel_engine.get_worker_count(999) >= 1


def test_evaluate_case_and_pool_worker_are_spawn_safe() -> None:
    assert parallel_engine.evaluate_case.__module__ == "physics.parallel_engine"
    assert parallel_engine._pool_worker.__module__ == "physics.parallel_engine"
    assert pickle.loads(pickle.dumps(parallel_engine.evaluate_case)) is parallel_engine.evaluate_case
    assert pickle.loads(pickle.dumps(parallel_engine._pool_worker)) is parallel_engine._pool_worker

    case = make_case()
    assert pickle.loads(pickle.dumps(case)) == case
    rebuilt = pickle.loads(pickle.dumps(parallel_engine._pool_worker((parallel_engine.evaluate_case, [case]))))
    assert rebuilt and set(parallel_engine.FLIGHT_RESULT_KEYS).issubset(rebuilt[0])


def assert_same_flight(left: Dict[str, Any], right: Dict[str, Any]) -> None:
    """Cross-process payloads must agree to floating point noise, not bit-exactly."""
    for key in parallel_engine.FLIGHT_RESULT_KEYS:
        if key == "status":
            assert left[key] == right[key]
        else:
            assert float(left[key]) == pytest.approx(float(right[key]), rel=1e-6, abs=1e-8), key


def test_map_evaluate_is_order_preserving() -> None:
    cases = [make_case(speed=speed, duration_s=0.4, dt_max=0.1) for speed in (5.0, 14.0, 20.0, 26.0)]
    serial = parallel_engine.map_evaluate(parallel_engine.evaluate_case, cases, processes=1)
    spawned = parallel_engine.map_evaluate(parallel_engine.evaluate_case, cases, processes=2)
    assert len(serial) == len(cases) == len(spawned)
    assert [float(item["launch_speed_mps"]) for item in spawned] == [5.0, 14.0, 20.0, 26.0]
    for left, right in zip(serial, spawned):
        assert_same_flight(left, right)
    assert parallel_engine.map_evaluate(parallel_engine.evaluate_case, [], processes=2) == []
    again = parallel_engine.parallel_map(parallel_engine.evaluate_case, cases, processes=2)
    assert len(again) == len(cases)
    for left, right in zip(spawned, again):
        assert_same_flight(left, right)


def test_a_real_process_pool_is_used() -> None:
    assert parallel_engine.worker_pid() == os.getpid()
    assert parallel_engine.worker_id() == -1
    pids = parallel_engine.map_evaluate(_report_worker_pid, [0, 1, 2, 3], processes=2)
    assert len(pids) == 4
    assert all(isinstance(pid, int) for pid in pids)
    assert all(pid != os.getpid() for pid in pids), "work must happen in child processes"
    serial = parallel_engine.map_evaluate(_report_worker_pid, [0, 1, 2, 3], processes=1)
    assert serial == [os.getpid()] * 4


def test_non_picklable_callable_falls_back_to_threads() -> None:
    multiplier = 3  # a local cell: unpicklable by reference

    def local(value: int) -> int:
        return value * multiplier

    results = parallel_engine.map_evaluate(local, [1, 2, 3], processes=2)
    assert results == [3, 6, 9]
    assert backend_name(2) == "multiprocessing"


def test_parallel_evaluate_matches_serial_and_reports_progress() -> None:
    cases = [make_case(speed=speed, duration_s=0.4, dt_max=0.1) for speed in (7.0, 16.0, 6.0, 22.0)]
    seen: List[int] = []
    totals: List[int] = []

    serial = parallel_engine.parallel_evaluate(
        cases, processes=1, progress_cb=lambda done, total, _b: (seen.append(done), totals.append(total))
    )
    seen.clear()
    totals.clear()
    spawned = parallel_engine.parallel_evaluate(
        cases, processes=2, progress_cb=lambda done, total, _b: (seen.append(done), totals.append(total))
    )

    assert len(serial) == len(spawned) == len(cases)
    for left, right in zip(serial, spawned):
        assert_same_flight(left, right)
    assert seen == sorted(seen)
    assert seen[-1] == len(cases)
    assert all(total == len(cases) for total in totals)
    assert parallel_engine.parallel_evaluate([], processes=2) == []


def test_parallel_evaluate_honours_cancellation(surrogate_path: bool) -> None:
    cases = [make_case(speed=10.0 + index) for index in range(8)]
    assert parallel_engine.parallel_evaluate(cases, processes=1, cancel_cb=lambda: True) == []
    assert parallel_engine.parallel_evaluate(cases, processes=2, cancel_cb=lambda: True) == []


# ==========================================================================
# 9. Integration with the real integrator (whatever is available)
# ==========================================================================
def test_real_integrator_path_returns_the_flight_contract() -> None:
    case = make_case(speed=12.0, duration_s=0.6, dt_max=0.05)
    flight = parallel_engine.evaluate_case(case)
    assert set(parallel_engine.FLIGHT_RESULT_KEYS).issubset(flight)
    for key in parallel_engine.FLIGHT_RESULT_KEYS:
        if key == "status":
            assert isinstance(flight[key], str) and flight[key]
        else:
            assert finite(flight[key]), key
    assert parallel_engine._flight_module() is not None, (
        "flight_sim disappeared again -- the guard path was exercised instead"
    )


def test_grid_search_runs_end_to_end_on_the_live_path() -> None:
    cfg = make_cfg("grid", max_evaluations=6, design_variables=["wingspan_m", "launch_speed"])
    cfg.sim_duration_s = 0.6
    cfg.dt_max = 0.05
    result = run_optimizer(cfg)
    assert set(result) == set(RESULT_KEYS)
    assert result["status"] == "completed"
    assert 1 <= result["evaluations"] <= 6
    assert finite(result["best_score"])


def test_evaluate_vector_reports_objectives_score_and_params(surrogate_path: bool) -> None:
    cfg = make_cfg()
    variables = cfg.resolved_variables()
    vector = config_to_vector(cfg, variables)
    payload = evaluate_vector(vector, cfg)
    assert {"objectives", "score", "vector", "params", "status"} <= set(payload)
    assert set(payload["objectives"]) == set(cfg.objectives)
    assert all(finite(value) for value in payload["objectives"].values())
    assert finite(payload["score"])
    assert payload["vector"] == pytest.approx(vector, rel=1e-12, abs=1e-12)
    assert set(payload["params"]) == set(FAST_VARIABLES)
    assert isinstance(payload["status"], str)


def test_surrogate_is_monotone_in_aspect_ratio(surrogate_path: bool) -> None:
    """The analytical model must reward a higher aspect ratio with more range."""
    cfg = make_cfg("grid", design_variables=["aspect_ratio"], objectives=["max_distance"])
    cfg.weights = {"max_distance": 1.0}
    variables = cfg.resolved_variables()
    variable = variables[0]
    low = evaluate_vector([variable.low], cfg)["objectives"]["max_distance"]
    high = evaluate_vector([variable.high], cfg)["objectives"]["max_distance"]
    assert high > low > 0.0


def test_optimiser_module_has_no_placeholder_tokens() -> None:
    import physics.optimizer_domain as optimizer_domain

    source = open(optimizer_domain.__file__, encoding="utf-8").read()
    for token in ("TODO", "FIXME", "NotImplementedError", "XXX"):
        assert token not in source

    engine_source = open(parallel_engine.__file__, encoding="utf-8").read()
    for token in ("TODO", "FIXME", "NotImplementedError", "XXX"):
        assert token not in engine_source


def test_flight_evaluator_exposes_model_fidelity(surrogate_path):
    result = parallel_engine.evaluate_case({"aircraft": {}, "environment": {}, "launch": {}, "duration_s": .1})
    assert result["model"] == "analytical_glide_surrogate"
    assert result["approximate"] is True
    assert result["physics_verification_eligible"] is False


def test_gpu_optimizer_batch_identifies_approximate_model():
    info = parallel_engine.get_gpu_info()
    if not (info["available"] and info["backend"] == "cupy"):
        pytest.skip("CuPy CUDA device unavailable")
    rows = parallel_engine.gpu_batch_evaluate_cases([{"aircraft": {}, "environment": {}, "launch": {}, "duration_s": .1}]*2)
    assert len(rows) == 2
    assert all(row["backend"] == "cupy" and row["model"] == "translational_ensemble" and row["approximate"] and not row["physics_verification_eligible"] for row in rows)


def test_real_evaluator_metadata_does_not_claim_reduced_model(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(parallel_engine, "_flight_module", lambda: SimpleNamespace(evaluate_flight=lambda *args: {"status": "landed", "backend": "numpy"}))
    result = parallel_engine.evaluate_case({})
    assert result["model"] == "rigid_body_6dof"
    assert result["approximate"] is False
    assert result["backend"] == "numpy"
    assert "calibration" in result["limitations"][0]


def test_optimizer_result_preserves_mixed_physics_warning(monkeypatch):
    import physics.optimizer_domain as domain
    cfg = make_cfg("grid", objectives=["max_distance"], weights={"max_distance":1.0})
    run = domain._Run("grid", cfg, cfg.resolved_variables(), 2, None, None)
    for model, approximate, score in [("rigid_body_6dof", False, 1), ("translational_ensemble", True, 2)]:
        run.evaluations += 1
        run._record([variable.low for variable in run.variables], {name:score for name in run.objectives}, {"model":model, "approximate":approximate, "physics_verification_eligible":not approximate, "limitations":[]})
    result = run.result(2)
    assert result["physics_models"] == ["rigid_body_6dof", "translational_ensemble"]
    assert result["approximate"] is True
    assert result["physics_verification_eligible"] is False
    assert any("direct comparison" in value for value in result["limitations"])
    assert result["best_physics"]["model"] == "translational_ensemble"


def test_differential_evolution_small_budget_falls_back_to_surrogate():
    from physics.optimizer_domain import run_differential_evolution
    # 3 variables with max_evaluations=4 (< 3*2 = 6)
    cfg = make_cfg(
        "differential-evolution",
        design_variables=["elevon_deg", "paper_gsm", "launch_angle_deg"],
        objectives=["max_distance"],
        max_evaluations=4,
    )
    result = run_differential_evolution(cfg)
    assert result["algorithm"] == "surrogate"
    assert result["evaluations"] <= 4

    assert all("physics" in row for row in result["pareto_front"])
