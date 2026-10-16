"""Optimisation domain: objectives, five real algorithms, Pareto machinery.

Scope
-----
Everything here is a pure function of :class:`~physics.config_schema.OptimizerConfig`
and evaluates a design vector through
:func:`physics.parallel_engine.evaluate_case`, so the optimiser automatically
picks up the real 6-DoF integrator when :mod:`physics.flight_sim` exists and
the analytical surrogate otherwise.

Chain
-----
``vector -> vector_to_config -> case dict -> evaluate_case -> flight dict ->
objective_value -> aggregate_score``.

Algorithms
----------
``grid``, ``nelder-mead`` and ``differential-evolution`` delegate the local
search to :mod:`scipy.optimize` (negated aggregate score, hard evaluation
budget, polling cancellation callback).  ``nsga2`` and ``surrogate`` are
implemented from scratch in this file.

The payload every ``run_*`` function returns has exactly these keys::

    algorithm, best_vector, best_params, best_objectives, best_score,
    pareto_front, evaluations, history, backend, population_size, seed,
    objectives, design_variables, elapsed_s, status

with ``status`` in ``{"completed", "cancelled"}``.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
try:
    from scipy.optimize import differential_evolution, minimize
except ImportError:
    differential_evolution = None
    minimize = None

from .config_schema import (
    OBJECTIVE_DIRECTIONS,
    AircraftConfig,
    DesignVariable,
    LaunchConfig,
    OptimizerConfig,
    as_dict,
    clamp,
    from_dict,
)
from .parallel_engine import backend_name, evaluate_case, parallel_evaluate

__all__ = [
    "OBJECTIVE_KEYS",
    "OBJECTIVE_SOURCES",
    "OBJECTIVE_SCALES",
    "RESULT_KEYS",
    "AIRCRAFT_VARIABLES",
    "LAUNCH_VARIABLES",
    "objective_value",
    "objective_direction",
    "aggregate_score",
    "vector_to_config",
    "config_to_vector",
    "evaluate_vector",
    "case_for_vector",
    "grid_points",
    "run_grid_search",
    "run_nelder_mead",
    "run_differential_evolution",
    "run_nsga2",
    "run_surrogate",
    "run_optimizer",
    "pareto_front",
    "non_dominated_sort",
    "crowding_distance",
    "dominates",
    "sbx_crossover",
    "polynomial_mutation",
    "SurrogateModel",
    "build_surrogate",
    "ALGORITHM_DISPATCH",
]


# --------------------------------------------------------------------------
# Objective contract
# --------------------------------------------------------------------------
#: The seven supported objective names, in canonical order.
OBJECTIVE_KEYS: Tuple[str, ...] = (
    "max_distance",
    "max_flight_time",
    "max_ld_ratio",
    "min_max_stress",
    "max_stall_margin",
    "min_launch_speed",
    "max_stability",
)

#: Objective name -> key of the flight payload it reads.
OBJECTIVE_SOURCES: Dict[str, str] = {
    "max_distance": "distance_m",
    "max_flight_time": "flight_time_s",
    "max_ld_ratio": "max_ld_ratio",
    "min_max_stress": "max_stress_index",
    "max_stall_margin": "stall_margin",
    "min_launch_speed": "launch_speed_mps",
    "max_stability": "stability",
}

#: Characteristic magnitude of each objective, used to make the weighted
#: aggregate dimensionless.  Two runs that differ by a factor of scale still
#: contribute comparably to :func:`aggregate_score`.
OBJECTIVE_SCALES: Dict[str, float] = {
    "max_distance": 25.0,       # m, typical indoor range
    "max_flight_time": 5.0,     # s
    "max_ld_ratio": 8.0,        # -
    "min_max_stress": 0.5,      # normalised stress index (0..1)
    "max_stall_margin": 5.0,    # fraction of the stall AoA
    "min_launch_speed": 12.0,   # m/s
    "max_stability": 1.0,       # -
}

#: Exact key set of every ``run_*`` payload.
RESULT_KEYS: Tuple[str, ...] = (
    "algorithm",
    "best_vector",
    "best_params",
    "best_objectives",
    "best_score",
    "pareto_front",
    "evaluations",
    "history",
    "backend",
    "population_size",
    "seed",
    "objectives",
    "design_variables",
    "elapsed_s",
    "status",
    "physics_models",
    "approximate",
    "physics_verification_eligible",
    "limitations",
    "best_physics",
)

#: Design variables that land on :class:`~physics.config_schema.LaunchConfig`.
LAUNCH_VARIABLES: Tuple[str, ...] = (
    "launch_angle_deg",
    "launch_roll_deg",
    "launch_speed",
    "throw_force_n",
    "throw_duration_s",
    "throw_force_pitch_deg",
    "throw_grip_x",
    "throw_grip_y",
    "throw_wrist_spin_radps",
)

#: Every other catalogue variable lands on :class:`AircraftConfig`.
AIRCRAFT_VARIABLES: Tuple[str, ...] = (
    "wingspan_m",
    "wing_area_m2",
    "taper_ratio",
    "aspect_ratio",
    "dihedral_deg",
    "fold_leading_deg",
    "fold_trailing_deg",
    "fold_tip_deg",
    "fold_center_deg",
    "elevon_deg",
    "paper_gsm",
    "paper_stiffness",
    "mass_kg",
)

_VALID_STATUS = ("completed", "cancelled")


def objective_value(name: str, flight: Dict[str, Any]) -> float:
    """Return the raw (physically signed) objective value of a flight payload.

    The *sign convention* of an objective -- whether larger or smaller is
    better -- is carried by :func:`objective_direction`, not by this function;
    here the value is reported exactly as the flight payload defines it.
    """
    if name not in OBJECTIVE_SOURCES:
        raise ValueError(f"unknown objective '{name}'; expected one of {OBJECTIVE_KEYS}")
    source = OBJECTIVE_SOURCES[name]
    try:
        value = float(flight[source])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"flight payload is missing numeric key '{source}'") from exc
    if not math.isfinite(value):
        raise ValueError(f"flight payload key '{source}' is not finite: {value!r}")
    return value


def objective_direction(name: str) -> int:
    """``+1`` when a larger value is better, ``-1`` when smaller is better."""
    try:
        return int(OBJECTIVE_DIRECTIONS[name])
    except KeyError as exc:
        raise ValueError(f"unknown objective '{name}'; expected one of {OBJECTIVE_KEYS}") from exc


def aggregate_score(values: Dict[str, float], weights: Dict[str, float], objectives: Sequence[str]) -> float:
    """Weighted, direction aware, dimensionless score -- **higher is better**.

    ``score = sum_i w_i * direction_i * value_i / scale_i``

    Because the objective directions are applied inside, a lower
    ``min_max_stress`` raises the score while a higher ``max_distance`` raises
    it too, and the contribution of each objective scales linearly with its
    weight.
    """
    total = 0.0
    for name in objectives:
        if name not in OBJECTIVE_SOURCES:
            raise ValueError(f"unknown objective '{name}'; expected one of {OBJECTIVE_KEYS}")
        direction = objective_direction(name)
        weight = float(weights.get(name, 1.0)) if weights else 1.0
        value = float(values.get(name, 0.0))
        if not math.isfinite(weight) or not math.isfinite(value):
            continue
        total += weight * direction * value / OBJECTIVE_SCALES[name]
    return float(total)


# --------------------------------------------------------------------------
# Vector <-> configuration
# --------------------------------------------------------------------------
def _container_for(name: str) -> str:
    """``"launch"`` for launch conditions, ``"aircraft"`` for everything else."""
    return "launch" if name in LAUNCH_VARIABLES else "aircraft"


def vector_to_config(
    vector: Sequence[float],
    variables: Sequence[DesignVariable],
    base: OptimizerConfig,
) -> OptimizerConfig:
    """Return a **deep copy** of ``base`` with ``vector`` written into it.

    ``base`` is never mutated, which lets an optimiser evaluate many candidate
    vectors that all share one immutable parent configuration.
    """
    from copy import deepcopy

    if len(vector) != len(variables):
        raise ValueError(
            f"vector length {len(vector)} does not match {len(variables)} design variable(s)"
        )
    clone = deepcopy(base)
    aircraft: AircraftConfig = clone.aircraft
    launch: LaunchConfig = clone.launch
    for raw, variable in zip(vector, variables):
        value = variable.clip(float(raw))
        if _container_for(variable.name) == "launch":
            setattr(launch, variable.name, value)
        else:
            setattr(aircraft, variable.name, value)
    bounds = dict(clone.variable_bounds or {})
    for variable in variables:
        bounds[variable.name] = (float(variable.low), float(variable.high))
    clone.variable_bounds = bounds
    return clone


def config_to_vector(config: OptimizerConfig, variables: Sequence[DesignVariable]) -> List[float]:
    """Inverse of :func:`vector_to_config` (exact round trip to 1e-12)."""
    out: List[float] = []
    for variable in variables:
        if _container_for(variable.name) == "launch":
            out.append(float(getattr(config.launch, variable.name)))
        else:
            out.append(float(getattr(config.aircraft, variable.name)))
    return out


def case_for_vector(vector: Sequence[float], cfg: OptimizerConfig) -> Dict[str, Any]:
    """Build the plain flight-case dictionary consumed by ``evaluate_case``."""
    variables = cfg.resolved_variables()
    run_cfg = vector_to_config(vector, variables, cfg)
    return {
        "aircraft": as_dict(run_cfg.aircraft),
        "environment": as_dict(run_cfg.environment),
        "launch": as_dict(run_cfg.launch),
        "duration_s": float(run_cfg.sim_duration_s),
        "dt_max": float(run_cfg.dt_max),
    }


def params_for_vector(variables: Sequence[DesignVariable], vector: Sequence[float]) -> Dict[str, float]:
    """``{variable name: value}`` -- the JSON friendly parameter echo."""
    return {variable.name: float(value) for variable, value in zip(variables, vector)}


def _objectives_from_flight(names: Sequence[str], flight: Dict[str, Any]) -> Dict[str, float]:
    return {name: objective_value(name, flight) for name in names}


def evaluate_vector(vector: Sequence[float], cfg: OptimizerConfig) -> Dict[str, Any]:
    """Evaluate one design vector and return objectives, score and parameters.

    Returned keys: ``objectives``, ``score``, ``vector``, ``params``, ``status``
    plus the untouched ``flight`` payload for diagnostics.
    """
    names = cfg.active_objectives() or list(OBJECTIVE_KEYS)
    variables = cfg.resolved_variables()
    case = case_for_vector(vector, cfg)
    flight = evaluate_case(case)
    objectives = _objectives_from_flight(names, flight)
    score = aggregate_score(objectives, cfg.weights, names)
    return {
        "objectives": objectives,
        "score": float(score),
        "vector": [float(value) for value in vector],
        "params": params_for_vector(variables, vector),
        "status": str(flight.get("status", "unknown")),
        "flight": flight,
    }


# --------------------------------------------------------------------------
# Shared run scaffolding
# --------------------------------------------------------------------------
class _Run:
    """Book-keeping shared by all five algorithms.

    Owns the evaluation budget, the history log, the incumbent best point and
    the ``progress_cb`` / ``cancel_cb`` protocol.  ``progress_cb`` is always
    invoked with ``total > 0``.
    """

    def __init__(
        self,
        algorithm: str,
        cfg: OptimizerConfig,
        variables: Sequence[DesignVariable],
        total: int,
        progress_cb: Optional[Callable[[int, int, float], None]],
        cancel_cb: Optional[Callable[[], bool]],
    ) -> None:
        self.algorithm = algorithm
        self.cfg = cfg
        self.variables = list(variables)
        self.objectives = cfg.active_objectives() or list(OBJECTIVE_KEYS)
        self.weights = dict(cfg.weights or {})
        self.total = max(1, int(total))
        self.evaluations = 0
        self.history: List[Dict[str, Any]] = []
        self.best_score = -math.inf
        self.best_vector: List[float] = []
        self.best_objectives: Dict[str, float] = {}
        self.archive: List[Tuple[List[float], Dict[str, float]]] = []
        self.cancelled = False
        self.stop_reason = ""
        self.started = time.perf_counter()
        self._progress_cb = progress_cb
        self._cancel_cb = cancel_cb
        self._used_gpu = False
        self.physics_trace: List[Dict[str, Any]] = []
        self.best_physics: Dict[str, Any] = {}

    # -- protocol ----------------------------------------------------------
    def poll_cancel(self) -> bool:
        """Poll the cancellation callback (cheap, no side effects)."""
        if self.cancelled:
            return True
        if self._cancel_cb is not None and bool(self._cancel_cb()):
            self.cancelled = True
            self.stop_reason = "cancel"
        return self.cancelled

    def budget_left(self) -> int:
        return max(0, self.total - self.evaluations)

    def notify(self) -> None:
        if self._progress_cb is not None:
            self._progress_cb(int(self.evaluations), int(self.total), float(self.best_score))

    # -- evaluation --------------------------------------------------------
    def evaluate_batch(self, vectors: Sequence[Sequence[float]]) -> List[Dict[str, Any]]:
        """Evaluate up to ``budget_left`` vectors, in parallel when allowed."""
        room = self.budget_left()
        if room <= 0 or not vectors:
            return []
        batch = [list(map(float, vector)) for vector in vectors[:room]]
        if self.poll_cancel():
            return []

        if len(batch) == 1:
            outcome = self._single(batch[0])
            self.evaluations += 1
            self._record(outcome["vector"], outcome["objectives"], outcome["flight"])
            self.notify()
            return [outcome]

        cases = [case_for_vector(vector, self.cfg) for vector in batch]
        before = int(self.evaluations)
        done = 0

        def relay(evaluated: int, _total: int, _best: float) -> None:
            self.evaluations = before + int(evaluated)
            self.notify()

        from .parallel_engine import get_gpu_info, gpu_batch_evaluate_cases
        gpu_info = get_gpu_info()
        use_gpu = (self.cfg.processes != 1) and gpu_info.get("available") and (gpu_info.get("backend") == "cupy")
        if use_gpu:
            flights = gpu_batch_evaluate_cases(cases, progress_cb=relay)
            self._used_gpu = True
        else:
            flights = parallel_evaluate(
                cases,
                processes=self.cfg.processes,
                progress_cb=relay,
                cancel_cb=self._cancel_cb,
            )
        results: List[Dict[str, Any]] = []
        for offset, (vector, flight) in enumerate(zip(batch, flights), start=1):
            self.evaluations = before + offset
            objectives = _objectives_from_flight(self.objectives, flight)
            self._record(vector, objectives, flight)
            results.append(
                {
                    "objectives": objectives,
                    "score": aggregate_score(objectives, self.weights, self.objectives),
                    "vector": list(vector),
                    "params": params_for_vector(self.variables, vector),
                    "status": str(flight.get("status", "unknown")),
                    "flight": flight,
                }
            )
        self.evaluations = before + len(flights)
        self.notify()
        return results

    def evaluate_pairs(self, vectors: Sequence[Sequence[float]]) -> List[Tuple[List[float], Dict[str, float]]]:
        """Evaluate a batch and return the ``(vector, objectives)`` actually run.

        The returned vectors are exactly the ones that consumed budget, so a
        caller never has to re-derive which of its candidates were evaluated.
        """
        outcomes = self.evaluate_batch(vectors)
        return [([float(value) for value in outcome["vector"]], outcome["objectives"]) for outcome in outcomes]

    def _single(self, vector: Sequence[float]) -> Dict[str, Any]:
        case = case_for_vector(vector, self.cfg)
        flight = evaluate_case(case)
        objectives = _objectives_from_flight(self.objectives, flight)
        score = aggregate_score(objectives, self.weights, self.objectives)
        return {
            "objectives": objectives,
            "score": float(score),
            "vector": [float(value) for value in vector],
            "params": params_for_vector(self.variables, vector),
            "status": str(flight.get("status", "unknown")),
            "flight": flight,
        }

    def _record(self, vector: Sequence[float], objectives: Dict[str, float], flight: Dict[str, Any]) -> None:
        vector = [float(value) for value in vector]
        score = aggregate_score(objectives, self.weights, self.objectives)
        self.history.append(
            {"evaluation": int(self.evaluations), "score": float(score), "objectives": dict(objectives)}
        )
        self.archive.append((list(vector), dict(objectives)))
        physics = {"model": flight.get("model", "unknown"), "approximate": flight.get("approximate", True),
                   "physics_verification_eligible": flight.get("physics_verification_eligible", False),
                   "limitations": list(flight.get("limitations", ["Evaluation model not identified."]))}
        self.physics_trace.append(physics)
        if score > self.best_score or not self.best_vector:
            self.best_score = float(score)
            self.best_vector = list(vector)
            self.best_objectives = dict(objectives)
            self.best_physics = dict(physics)

    # -- payload -----------------------------------------------------------
    def result(self, population_size: int) -> Dict[str, Any]:
        status = "cancelled" if self.cancelled else "completed"
        if not self.best_vector:
            self.best_vector = [float(variable.low) for variable in self.variables]
            self.best_objectives = {name: 0.0 for name in self.objectives}
            if not math.isfinite(self.best_score):
                self.best_score = 0.0
        front_rows = [value for _vector, value in self.archive]
        front_indices = pareto_front(self.objectives, front_rows)
        front: List[Dict[str, Any]] = []
        for index in front_indices:
            vector, objectives = self.archive[index]
            front.append(
                {
                    "objectives": dict(objectives),
                    "vector": list(vector),
                    "params": params_for_vector(self.variables, vector),
                    "physics": dict(self.physics_trace[index]),
                }
            )
        if not front:
            front = [
                {
                    "objectives": dict(self.best_objectives),
                    "vector": list(self.best_vector),
                    "params": params_for_vector(self.variables, self.best_vector),
                    "physics": dict(self.best_physics),
                }
            ]
        from .parallel_engine import get_gpu_info
        gpu_info = get_gpu_info()
        models = sorted({str(row["model"]) for row in self.physics_trace})
        approximate = any(row["approximate"] for row in self.physics_trace)
        limitations = sorted({str(value) for row in self.physics_trace for value in row["limitations"]})
        if len(models) > 1:
            limitations.append("Candidates use different physics models; their objective values are not validated for direct comparison.")
        return {
            "algorithm": self.algorithm,
            "physics_models": models,
            "approximate": approximate,
            "physics_verification_eligible": bool(self.physics_trace) and not approximate and len(models) == 1 and all(row["physics_verification_eligible"] for row in self.physics_trace),
            "limitations": limitations,
            "best_physics": dict(self.best_physics),
            "best_vector": list(self.best_vector),
            "best_params": params_for_vector(self.variables, self.best_vector),
            "best_objectives": dict(self.best_objectives),
            "best_score": float(self.best_score),
            "pareto_front": front,
            "evaluations": int(self.evaluations),
            "history": list(self.history),
            "backend": f"cupy ({gpu_info['device_name']})" if (self._used_gpu and gpu_info.get("device_name")) else backend_name(self.cfg.processes),
            "population_size": int(population_size),
            "seed": int(self.cfg.seed),
            "objectives": list(self.objectives),
            "design_variables": [variable.name for variable in self.variables],
            "elapsed_s": float(time.perf_counter() - self.started),
            "status": status,
        }


def _prepare(cfg: OptimizerConfig) -> Tuple[List[DesignVariable], List[float], List[float]]:
    variables = cfg.resolved_variables()
    if not variables:
        raise ValueError("at least one design variable must be selected")
    lows = [float(variable.low) for variable in variables]
    highs = [float(variable.high) for variable in variables]
    for low, high in zip(lows, highs):
        if not high > low:
            raise ValueError(f"invalid bound low={low} high={high}")
    return variables, lows, highs


# --------------------------------------------------------------------------
# Pareto machinery
# --------------------------------------------------------------------------
def _as_rows(objectives: Sequence[str], value_matrix: Any) -> np.ndarray:
    """Normalise ``value_matrix`` into a ``(n, len(objectives))`` float array."""
    names = list(objectives)
    if isinstance(value_matrix, np.ndarray):
        matrix = np.asarray(value_matrix, dtype=float)
        if matrix.size == 0:
            return np.zeros((0, len(names)), dtype=float)
        if matrix.ndim == 1:
            matrix = matrix.reshape(-1, 1) if len(names) == 1 else matrix.reshape(1, -1)
        if matrix.ndim != 2:
            raise ValueError("value_matrix must be two dimensional")
        if matrix.shape[1] != len(names):
            raise ValueError(
                f"value_matrix has {matrix.shape[1]} column(s) but {len(names)} objective(s) were named"
            )
        return matrix
    rows = list(value_matrix)
    out: List[List[float]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append([float(row.get(name, 0.0)) for name in names])
        elif isinstance(row, (list, tuple, np.ndarray)):
            values = [float(value) for value in row]
            if len(values) != len(names):
                raise ValueError(
                    f"value_matrix row has {len(values)} entries but {len(names)} objective(s) were named"
                )
            out.append(values)
        else:
            raise TypeError(f"unsupported value_matrix row type: {type(row)!r}")
    if not out:
        return np.zeros((0, len(names)), dtype=float)
    return np.asarray(out, dtype=float)


def _directions(objectives: Sequence[str]) -> np.ndarray:
    return np.asarray([objective_direction(name) for name in objectives], dtype=float)


def dominates(a: Dict[str, float], b: Dict[str, float], objectives: Sequence[str]) -> bool:
    """True when ``a`` is at least as good as ``b`` everywhere and strictly better once."""
    better_somewhere = False
    for name in objectives:
        direction = objective_direction(name)
        delta = direction * (float(a.get(name, 0.0)) - float(b.get(name, 0.0)))
        if delta < 0.0:
            return False
        if delta > 0.0:
            better_somewhere = True
    return better_somewhere


def non_dominated_sort(objectives: Sequence[str], value_matrix: Any) -> List[List[int]]:
    """Fast non-dominated sort (Deb et al.) returning the ordered fronts."""
    matrix = _as_rows(objectives, value_matrix)
    size = matrix.shape[0]
    if size == 0:
        return []
    directions = _directions(objectives)
    dominated = np.zeros(size, dtype=bool)
    domination_count = np.zeros(size, dtype=int)
    front: List[int] = []
    for dominator in range(size):
        for other in range(size):
            if other == dominator:
                continue
            delta = directions * (matrix[dominator] - matrix[other])
            if np.all(delta >= 0.0) and np.any(delta > 0.0):
                dominated[other] = True
                domination_count[other] += 1
    for index in range(size):
        if not dominated[index]:
            front.append(index)

    fronts: List[List[int]] = []
    while front:
        fronts.append(list(front))
        nxt: List[int] = []
        for index in front:
            for other in range(size):
                if other == index:
                    continue
                delta = directions * (matrix[index] - matrix[other])
                if np.all(delta >= 0.0) and np.any(delta > 0.0):
                    domination_count[other] -= 1
                    if domination_count[other] == 0:
                        nxt.append(other)
        front = nxt
    return fronts


def pareto_front(objectives: Sequence[str], value_matrix: Any) -> List[int]:
    """Indices of the first (fully non-dominated) front, ascending."""
    fronts = non_dominated_sort(objectives, value_matrix)
    return list(fronts[0]) if fronts else []


def crowding_distance(objectives: Sequence[str], value_matrix: Any, front: Sequence[int]) -> List[float]:
    """NSGA-II crowding distance; boundary members get ``math.inf``."""
    matrix = _as_rows(objectives, value_matrix)
    members = [int(index) for index in front]
    distances = [0.0] * len(members)
    if len(members) <= 2:
        return [math.inf] * len(members)
    block = matrix[members]
    for column in range(block.shape[1]):
        order = np.argsort(block[:, column], kind="stable")
        values = block[order, column]
        spread = float(values[-1] - values[0])
        if spread <= 1.0e-12:
            continue
        distances[order[0]] = math.inf
        distances[order[-1]] = math.inf
        for position in range(1, len(members) - 1):
            member = int(order[position])
            if math.isinf(distances[member]):
                continue
            distances[member] += float(values[position + 1] - values[position - 1]) / spread
    return distances


# --------------------------------------------------------------------------
# Variation operators
# --------------------------------------------------------------------------
def sbx_crossover(
    parent_a: Sequence[float],
    parent_b: Sequence[float],
    variables: Sequence[DesignVariable],
    rng: Any,
    eta: float = 15.0,
    prob: float = 0.9,
) -> Tuple[List[float], List[float]]:
    """Simulated binary crossover bounded by each design variable's range.

    ``prob <= 0`` returns copies of both parents untouched; otherwise every
    variable is crossed independently with probability ``prob`` and the children
    are clamped into ``[low, high]``.
    """
    first = [float(value) for value in parent_a]
    second = [float(value) for value in parent_b]
    if rng is None or float(prob) <= 0.0 or len(first) != len(variables):
        return first, second
    spread = max(float(eta), 1.0e-6)
    for index, variable in enumerate(variables):
        if index >= len(first) or index >= len(second):
            break
        if float(rng.random()) > float(prob):
            continue
        low = float(variable.low)
        high = float(variable.high)
        x1 = clamp(first[index], low, high)
        x2 = clamp(second[index], low, high)
        if abs(x1 - x2) <= 1.0e-14:
            continue
        draw = float(rng.random())
        if draw <= 0.5:
            beta = (2.0 * draw) ** (1.0 / (spread + 1.0))
        else:
            beta = (1.0 / (2.0 * (1.0 - draw))) ** (1.0 / (spread + 1.0))
        low_child = 0.5 * ((x1 + x2) - beta * abs(x2 - x1))
        high_child = 0.5 * ((x1 + x2) + beta * abs(x2 - x1))
        if draw > 0.5:
            low_child, high_child = high_child, low_child
        first[index] = clamp(low_child, low, high)
        second[index] = clamp(high_child, low, high)
    return first, second


def polynomial_mutation(
    individual: Sequence[float],
    variables: Optional[Sequence[DesignVariable]],
    rng: Any,
    eta: float = 20.0,
    prob: Optional[float] = None,
    lower: float = 0.0,
    upper: float = 1.0,
) -> List[float]:
    """NSGA-II polynomial mutation with distribution index ``eta``.

    Each gene is mutated with probability ``prob`` (``1/n`` by default) inside
    the per-gene box taken from ``variables``.  When ``variables`` is ``None``
    the scalar box ``[lower, upper]`` is used for every gene, and a degenerate
    box (``lower == upper``) returns that constant for every gene.
    """
    child = [float(value) for value in individual]
    size = len(child)
    if size == 0:
        return child
    if variables is None:
        boxes = [(float(lower), float(upper))] * size
    else:
        boxes = [(float(variable.low), float(variable.high)) for variable in variables][:size]
        boxes += [(float(lower), float(upper))] * (size - len(boxes))
    if rng is None:
        return child

    rate = (1.0 / size) if prob is None else float(prob)
    rate = clamp(rate, 0.0, 1.0)
    power = max(float(eta), 1.0e-6) + 1.0

    for index, (low, high) in enumerate(boxes):
        if float(rng.random()) > rate:
            continue
        if high - low <= 1.0e-15:
            child[index] = float(low)
            continue
        normalised = clamp((child[index] - low) / (high - low), 0.0, 1.0)
        draw = float(rng.random())
        if draw <= 0.5:
            delta = (
                (2.0 * draw + (1.0 - 2.0 * draw) * (1.0 - draw) ** power) ** (1.0 / power)
                - 1.0
            )
        else:
            delta = 1.0 - (
                (2.0 * (1.0 - draw) + 2.0 * (draw - 0.5) * (1.0 - draw) ** power)
                ** (1.0 / power)
            )
        child[index] = clamp(normalised + delta, 0.0, 1.0) * (high - low) + low
        child[index] = clamp(child[index], low, high)
    return child


# --------------------------------------------------------------------------
# Surrogate model
# --------------------------------------------------------------------------
def _erf_array(values: np.ndarray) -> np.ndarray:
    """Element-wise ``erf`` built on :func:`math.erf` (no SciPy dependency)."""
    flat = np.ravel(values)
    out = np.asarray([math.erf(float(value)) for value in flat], dtype=float)
    return out.reshape(np.shape(values))


class SurrogateModel:
    """Normalised k-NN blended with a Gaussian RBF interpolation.

    * the RBF part is a regularised *interpolant* ``w = (K + ridge I)^-1 y`` so
      ``predict`` reproduces the training targets to machine precision;
    * the k-NN part supplies local robustness far from the sample set;
    * the blend weight vanishes at the sample points (distance 0), therefore the
      exactness of :meth:`predict` on the training set is preserved while the
      k-NN contribution grows with distance from the data.
    """

    def __init__(self, k_nn: int = 4, rbf_gamma: float = 0.5, knn_weight: float = 0.35, ridge: float = 1.0e-10) -> None:
        self.k_nn = max(1, int(k_nn))
        self.rbf_gamma = max(1.0e-6, float(rbf_gamma))
        self.knn_weight = clamp(float(knn_weight), 0.0, 1.0)
        self.ridge = max(float(ridge), 1.0e-12)
        self.X: np.ndarray = np.zeros((0, 0), dtype=float)
        self.X_raw: np.ndarray = np.zeros((0, 0), dtype=float)
        self.y: np.ndarray = np.zeros(0, dtype=float)
        self.low: np.ndarray = np.zeros(0, dtype=float)
        self.span: np.ndarray = np.zeros(0, dtype=float)
        self.weights: np.ndarray = np.zeros(0, dtype=float)
        self.fitted = False

    # -- internals ---------------------------------------------------------
    def _normalise(self, X: Any) -> np.ndarray:
        matrix = np.atleast_2d(np.asarray(X, dtype=float))
        span = np.where(np.abs(self.span) < 1.0e-15, 1.0, self.span)
        return (matrix - self.low) / span

    def _distances(self, Xn: np.ndarray) -> np.ndarray:
        diff = Xn[:, None, :] - self.X[None, :, :]
        return np.einsum("ijk,ijk->ij", diff, diff)

    def _knn(self, Xn: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        distances = self._distances(Xn)
        order = np.argsort(distances, axis=1, kind="stable")
        take = min(self.k_nn, self.X.shape[0])
        nearest = order[:, :take]
        values = self.y[nearest]
        mean = np.mean(values, axis=1)
        variance = np.var(values, axis=1) if take > 1 else np.zeros_like(mean)
        return mean, variance, np.min(distances, axis=1)

    def _interpolate(self, Xn: np.ndarray) -> np.ndarray:
        diff = Xn[:, None, :] - self.X[None, :, :]
        kernel = np.exp(-self.rbf_gamma * np.einsum("ijk,ijk->ij", diff, diff))
        return kernel @ self.weights

    def _blend(self, Xn: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return ``(mean, variance, blend weight of the interpolant)``."""
        knn_mean, knn_var, min_sq = self._knn(Xn)
        blend = 1.0 - self.knn_weight * (min_sq / (min_sq + 1.0e-12))
        mean = blend * self._interpolate(Xn) + (1.0 - blend) * knn_mean
        variance = blend**2 * self._knn_var(Xn) + (1.0 - blend) ** 2 * knn_var
        return mean, variance, blend

    def _knn_var(self, Xn: np.ndarray) -> np.ndarray:
        distances = self._distances(Xn)
        take = min(self.k_nn, self.X.shape[0])
        order = np.argsort(distances, axis=1, kind="stable")[:, :take]
        values = self.y[order]
        weights = np.exp(-self.rbf_gamma * np.take_along_axis(distances, order, axis=1))
        total = np.sum(weights, axis=1)
        safe = np.where(total > 1.0e-12, total, 1.0)
        mean = np.sum(weights * values, axis=1) / safe
        spread = np.sqrt(np.sum(weights * (values - mean[:, None]) ** 2, axis=1) / safe)
        return spread**2

    # -- public API --------------------------------------------------------
    def fit(self, X: Any, y: Any) -> "SurrogateModel":
        """Fit the model on ``X`` (n samples x d features) and targets ``y``."""
        matrix = np.atleast_2d(np.asarray(X, dtype=float))
        targets = np.asarray(y, dtype=float).reshape(-1)
        if matrix.shape[0] != targets.shape[0]:
            raise ValueError("X and y must have the same number of rows")
        if matrix.shape[0] == 0:
            raise ValueError("cannot fit a surrogate on an empty sample")
        self.low = np.min(matrix, axis=0)
        high = np.max(matrix, axis=0)
        self.span = high - self.low
        self.X = self._normalise(matrix)
        self.X_raw = matrix
        self.y = targets
        if self.X.shape[0] == 1:
            self.weights = np.array([float(targets[0])])
        else:
            diff = self.X[:, None, :] - self.X[None, :, :]
            kernel = np.exp(-self.rbf_gamma * np.einsum("ijk,ijk->ij", diff, diff))
            system = kernel + self.ridge * np.eye(self.X.shape[0])
            try:
                self.weights = np.linalg.solve(system, targets)
            except np.linalg.LinAlgError:
                self.weights = np.linalg.lstsq(system, targets, rcond=None)[0]
        self.fitted = True
        return self

    def predict(self, X: Any) -> np.ndarray:
        """Predicted targets for ``X`` (exact on the training set)."""
        if not self.fitted:
            raise RuntimeError("SurrogateModel.predict called before fit")
        Xn = self._normalise(X)
        mean, _variance, _blend = self._blend(Xn)
        return mean

    def expected_improvement(
        self, X: Any, best_y: float, maximise: bool = True, xi: float = 0.01
    ) -> np.ndarray:
        """Expected improvement of ``X`` over the incumbent ``best_y``.

        ``EI = (mu - best - xi) Phi(z) + sigma phi(z)`` for maximisation with
        ``z = (mu - best - xi) / sigma``; minimisation mirrors the sign.  The
        result is clipped at zero, so it is never negative.
        """
        if not self.fitted:
            raise RuntimeError("SurrogateModel.expected_improvement called before fit")
        Xn = self._normalise(X)
        mean, variance, _blend = self._blend(Xn)
        sigma = np.sqrt(np.clip(variance, 0.0, None) + self.ridge)
        sigma = np.where(sigma < 1.0e-12, 1.0e-12, sigma)
        target = float(best_y)
        if not maximise:
            mean = -mean
            target = -target
        improvement = mean - target - abs(float(xi))
        z = improvement / sigma
        cdf = 0.5 * (1.0 + _erf_array(z / math.sqrt(2.0)))
        pdf = np.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
        ei = improvement * cdf + sigma * pdf
        return np.clip(np.nan_to_num(ei, nan=0.0, posinf=0.0, neginf=0.0), 0.0, None)

    def suggest(
        self, n: int, rng: Any, bounds: Sequence[Sequence[float]], maximise: bool = True
    ) -> List[List[float]]:
        """Propose ``n`` in-bounds candidates that maximise the EI."""
        count = max(0, int(n))
        if count == 0:
            return []
        if not self.fitted:
            raise RuntimeError("SurrogateModel.suggest called before fit")
        lows = np.asarray([float(pair[0]) for pair in bounds], dtype=float)
        highs = np.asarray([float(pair[1]) for pair in bounds], dtype=float)
        generator = np.random.default_rng(rng) if isinstance(rng, (int, np.integer)) else rng
        pool_size = max(8 * count, 32)
        pool = np.asarray(generator.random((pool_size, lows.shape[0])), dtype=float)
        pool = lows + pool * (highs - lows)
        incumbent = float(np.max(self.y) if maximise else np.min(self.y))
        ei = self.expected_improvement(pool, incumbent, maximise=maximise)
        order = np.argsort(-ei if maximise else ei, kind="stable")[:count]
        return [[float(value) for value in pool[row]] for row in order]


def build_surrogate(cfg: OptimizerConfig) -> SurrogateModel:
    """Warm-start a :class:`SurrogateModel` from a seeded space filling design.

    ``min(population_size, max_evaluations // 3)`` points (at least 2) are drawn
    with a Latin-hypercube, scored with the *aggregate* objective and fitted.
    """
    variables, lows, highs = _prepare(cfg)
    cap = max(1, int(cfg.max_evaluations))
    size = max(2, min(int(cfg.population_size), max(2, cap // 3)))
    rng = np.random.default_rng(int(cfg.seed))
    samples = latin_hypercube(size, lows, highs, rng)
    values = []
    for vector in samples:
        case = case_for_vector(vector, cfg)
        flight = evaluate_case(case)
        names = cfg.active_objectives() or list(OBJECTIVE_KEYS)
        values.append(aggregate_score(_objectives_from_flight(names, flight), cfg.weights, names))
    return SurrogateModel(k_nn=cfg.k_nn, rbf_gamma=cfg.rbf_gamma).fit(samples, np.asarray(values, dtype=float))


def latin_hypercube(size: int, lows: Sequence[float], highs: Sequence[float], rng: Any) -> List[List[float]]:
    """Deterministic Latin-hypercube sample of ``size`` points in the box."""
    dims = len(lows)
    columns = []
    for dim in range(dims):
        cuts = (np.arange(size, dtype=float) + rng.random(size)) / size
        rng.shuffle(cuts)
        columns.append(cuts)
    lattice = np.column_stack(columns)
    low_arr = np.asarray(lows, dtype=float)
    high_arr = np.asarray(highs, dtype=float)
    points = low_arr + lattice * (high_arr - low_arr)
    return [[float(value) for value in row] for row in points]


# --------------------------------------------------------------------------
# Algorithm 1: coarse grid sweep
# --------------------------------------------------------------------------
def _grid_axes(variables: Sequence[DesignVariable]) -> List[List[float]]:
    axes: List[List[float]] = []
    MAX_POINTS_PER_AXIS = 200
    for variable in variables:
        low = float(variable.low)
        high = float(variable.high)
        step = float(variable.step)
        if step > 0.0:
            count = int(math.floor((high - low) / step + 1.0e-9)) + 1
            if count > MAX_POINTS_PER_AXIS:
                # Clamp step so we don't blow up memory with tiny step sizes
                step = (high - low) / (MAX_POINTS_PER_AXIS - 1)
                count = MAX_POINTS_PER_AXIS
            values = [low + index * step for index in range(max(1, count))]
            if values[-1] < high - 1.0e-12:
                values.append(high)
            values = sorted({clamp(value, low, high) for value in values})
        else:
            values = [low, 0.5 * (low + high), high]
        axes.append(values)
    return axes


def _point_from_flat_index(index: int, axes: Sequence[Sequence[float]], strides: Sequence[int]) -> List[float]:
    point = []
    rem = index
    for axis, stride in zip(axes, strides):
        idx = (rem // stride) % len(axis)
        point.append(float(axis[idx]))
        rem = rem % stride
    return point


def grid_points(variables: Sequence[DesignVariable], max_points: int = 100000) -> List[List[float]]:
    """Cartesian product of every variable's grid."""
    axes = _grid_axes(variables)
    if not axes:
        return [[]]
    total = 1
    for axis in axes:
        total *= len(axis)
    strides = [1] * len(axes)
    for i in range(len(axes) - 2, -1, -1):
        strides[i] = strides[i + 1] * len(axes[i + 1])
    count = min(total, max_points)
    return [_point_from_flat_index(i, axes, strides) for i in range(count)]


def _evenly_spaced_indices(count: int, cap: int) -> List[int]:
    """Deterministic, evenly spread subset of ``range(count)`` of size ``cap``."""
    if count <= cap:
        return list(range(count))
    if cap <= 1:
        return [0]
    step = (count - 1) / (cap - 1)
    return [min(count - 1, int(round(index * step))) for index in range(cap)]


def run_grid_search(
    cfg: OptimizerConfig,
    progress_cb: Optional[Callable[[int, int, float], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Coarse cartesian sweep, deterministically capped at ``max_evaluations``."""
    variables, _lows, _highs = _prepare(cfg)
    cap = max(1, int(cfg.max_evaluations))
    axes = _grid_axes(variables)
    chosen: List[List[float]]
    if not axes:
        chosen = [[]]
        total = 1
    else:
        total = 1
        for axis in axes:
            total *= len(axis)
        strides = [1] * len(axes)
        for i in range(len(axes) - 2, -1, -1):
            strides[i] = strides[i + 1] * len(axes[i + 1])
        chosen_indices = _evenly_spaced_indices(total, cap)
        chosen = [_point_from_flat_index(idx, axes, strides) for idx in chosen_indices]

    eval_cap = min(cap, total)
    run = _Run("grid", cfg, variables, eval_cap, progress_cb, cancel_cb)
    if run.poll_cancel():
        return run.result(eval_cap)
    run.evaluate_batch(chosen)
    run.notify()
    return run.result(eval_cap)


# --------------------------------------------------------------------------
# Algorithm 2: Nelder-Mead (scipy)
# --------------------------------------------------------------------------
class _StopSearch(Exception):
    """Internal control-flow exception used to leave SciPy early and safely."""


def run_nelder_mead(
    cfg: OptimizerConfig,
    progress_cb: Optional[Callable[[int, int, float], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Downhill simplex on the negated aggregate score with hard budget control."""
    variables, lows, highs = _prepare(cfg)
    cap = max(1, int(cfg.max_evaluations))
    if minimize is None:
        res = run_surrogate(cfg, progress_cb=progress_cb, cancel_cb=cancel_cb)
        res["algorithm"] = "nelder-mead"
        res["backend"] = f"{res.get('backend', 'serial')}:surrogate_fallback"
        return res
    run = _Run("nelder-mead", cfg, variables, cap, progress_cb, cancel_cb)
    start = [clamp(value, low, high) for value, low, high in zip(config_to_vector(cfg, variables), lows, highs)]
    state = {"score": -math.inf, "seen": 0}

    def objective(x: np.ndarray) -> float:
        if run.poll_cancel() or run.budget_left() <= 0:
            raise _StopSearch
        vector = [clamp(float(value), low, high) for value, low, high in zip(x, lows, highs)]
        outcome = run._single(vector)
        run.evaluations += 1
        run._record(vector, outcome["objectives"], outcome["flight"])
        run.notify()
        state["seen"] += 1
        state["score"] = run.best_score
        return -float(outcome["score"])

    def callback(_xk: np.ndarray) -> bool:
        return bool(run.poll_cancel()) or run.budget_left() <= 0

    try:
        minimize(
            objective,
            np.asarray(start, dtype=float),
            method="Nelder-Mead",
            bounds=list(zip(lows, highs)),
            options={"maxfev": cap, "xatol": 1.0e-8, "fatol": 1.0e-10, "adaptive": False},
            callback=callback,
        )
    except _StopSearch:
        run.stop_reason = "cancel" if run.cancelled else "budget"
    run.notify()
    return run.result(cap)


# --------------------------------------------------------------------------
# Algorithm 3: Differential evolution (scipy)
# --------------------------------------------------------------------------
def run_differential_evolution(
    cfg: OptimizerConfig,
    progress_cb: Optional[Callable[[int, int, float], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Self-adaptive DE/rand/1/bin with deferred update and a strict budget."""
    variables, lows, highs = _prepare(cfg)
    cap = max(1, int(cfg.max_evaluations))
    dims = len(variables)
    if differential_evolution is None or dims == 0 or cap < dims * 2:
        res = run_surrogate(cfg, progress_cb=progress_cb, cancel_cb=cancel_cb)
        res["algorithm"] = "differential-evolution"
        res["backend"] = f"{res.get('backend', 'serial')}:surrogate_fallback"
        return res
    run = _Run("differential-evolution", cfg, variables, cap, progress_cb, cancel_cb)

    popsize = max(1, min(int(cfg.population_size), cap // max(1, dims)))
    population = popsize * dims
    generations = max(0, (cap - population) // max(1, population))

    def objective(x: np.ndarray) -> float:
        if run.poll_cancel() or run.budget_left() <= 0:
            raise _StopSearch
        vector = [clamp(float(value), low, high) for value, low, high in zip(x, lows, highs)]
        outcome = run._single(vector)
        run.evaluations += 1
        run._record(vector, outcome["objectives"], outcome["flight"])
        run.notify()
        return -float(outcome["score"])

    def callback(intermediate_result: Any = None, **_kwargs: Any) -> bool:
        return bool(run.poll_cancel()) or run.budget_left() <= 0

    try:
        differential_evolution(
            objective,
            list(zip(lows, highs)),
            strategy="best1bin",
            maxiter=generations,
            popsize=popsize,
            mutation=(0.5, 1.0),
            recombination=0.7,
            seed=int(cfg.seed),
            init="latinhypercube",
            polish=False,
            updating="deferred",
            tol=0.0,
            atol=0.0,
            callback=callback,
        )
    except _StopSearch:
        run.stop_reason = "cancel" if run.cancelled else "budget"
    run.notify()
    return run.result(popsize)


# --------------------------------------------------------------------------
# Algorithm 4: NSGA-II from scratch
# --------------------------------------------------------------------------
def _split_pairs(
    pairs: Sequence[Tuple[List[float], Dict[str, float]]]
) -> Tuple[List[List[float]], List[Dict[str, float]]]:
    """Split ``[(vector, objectives), ...]`` into the two parallel lists."""
    vectors = [[float(value) for value in vector] for vector, _objectives in pairs]
    values = [dict(objectives) for _vector, objectives in pairs]
    return vectors, values


def _rank_and_crowding(objectives: Sequence[str], rows: np.ndarray) -> Tuple[List[int], List[float]]:
    fronts = non_dominated_sort(objectives, rows)
    ranks = [0] * rows.shape[0]
    crowding = [0.0] * rows.shape[0]
    for rank, front in enumerate(fronts):
        distances = crowding_distance(objectives, rows, front)
        for position, index in enumerate(front):
            ranks[index] = rank
            crowding[index] = distances[position]
    return ranks, crowding


def _tournament(
    rng: Any,
    ranks: Sequence[int],
    crowding: Sequence[float],
    size: int,
) -> int:
    """Binary tournament on ``(rank, crowding)``; lower rank then wider spread."""
    first = int(rng.integers(0, size))
    second = int(rng.integers(0, size))
    if first == second:
        return first
    if ranks[first] != ranks[second]:
        return first if ranks[first] < ranks[second] else second
    if crowding[first] != crowding[second]:
        return first if crowding[first] > crowding[second] else second
    return first if rng.random() < 0.5 else second


def run_nsga2(
    cfg: OptimizerConfig,
    progress_cb: Optional[Callable[[int, int, float], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Non-dominated sorting genetic algorithm II, written from scratch.

    Fast non-dominated sort -> crowding distance -> (rank, crowding) binary
    tournament -> simulated binary crossover -> polynomial mutation ->
    ``(mu + lambda)`` elitist replacement, repeated until the evaluation budget
    or the cancellation callback stops the run.  The returned ``pareto_front``
    is front 1 of the surviving population.
    """
    variables, lows, highs = _prepare(cfg)
    cap = max(1, int(cfg.max_evaluations))
    dims = len(variables)
    size = int(clamp(int(cfg.population_size), 4, cap))
    run = _Run("nsga2", cfg, variables, cap, progress_cb, cancel_cb)
    rng = np.random.default_rng(int(cfg.seed))

    population = latin_hypercube(size, lows, highs, rng)
    population, values = _split_pairs(run.evaluate_pairs(population))
    run.notify()

    while not run.poll_cancel() and run.budget_left() > 0:
        rows = np.asarray(
            [[float(v[name]) for name in run.objectives] for v in values], dtype=float
        )
        ranks, crowding = _rank_and_crowding(run.objectives, rows)

        parents: List[int] = []
        for _ in range(2 * size):
            parents.append(_tournament(rng, ranks, crowding, size))
        children: List[List[float]] = []
        for first in range(0, 2 * size - 1, 2):
            left, right = sbx_crossover(
                population[parents[first]],
                population[parents[first + 1]],
                variables,
                rng,
                eta=15.0,
                prob=0.9,
            )
            children.append(polynomial_mutation(left, variables, rng, eta=20.0))
            children.append(polynomial_mutation(right, variables, rng, eta=20.0))
        if not children:
            break

        child_population, child_values = _split_pairs(run.evaluate_pairs(children))
        if not child_values:
            break

        merged_pop = list(population) + list(child_population)
        merged_values = list(values) + list(child_values)
        merged_rows = np.asarray(
            [[float(v[name]) for name in run.objectives] for v in merged_values], dtype=float
        )
        merged_ranks, merged_crowding = _rank_and_crowding(run.objectives, merged_rows)
        order = sorted(
            range(len(merged_pop)),
            key=lambda index: (merged_ranks[index], -merged_crowding[index], index),
        )[:size]
        population = [merged_pop[index] for index in order]
        values = [merged_values[index] for index in order]

    final_rows = np.asarray(
        [[float(v[name]) for name in run.objectives] for v in values], dtype=float
    )
    front_indices = pareto_front(run.objectives, final_rows)
    front_points = [(population[index], values[index]) for index in front_indices]
    result = _result_from_front(run, front_points, size)
    return {key: value for key, value in result.items() if key in RESULT_KEYS}


def _result_from_front(
    run: "_Run", front_points: Sequence[Tuple[List[float], Dict[str, float]]], population_size: int
) -> Dict[str, Any]:
    """Assemble the canonical payload with an explicit front."""
    payload = run.result(population_size)
    front = [
        {
            "objectives": dict(objectives),
            "vector": [float(value) for value in vector],
            "params": params_for_vector(run.variables, vector),
            "physics": next((dict(run.physics_trace[index]) for index in range(len(run.archive)-1, -1, -1)
                             if run.archive[index][0] == list(vector) and run.archive[index][1] == objectives),
                            {"model": "unknown", "approximate": True, "physics_verification_eligible": False}),
        }
        for vector, objectives in front_points
    ]
    payload["pareto_front"] = front or payload["pareto_front"]
    return payload


# --------------------------------------------------------------------------
# Algorithm 5: surrogate assisted search
# --------------------------------------------------------------------------
def run_surrogate(
    cfg: OptimizerConfig,
    progress_cb: Optional[Callable[[int, int, float], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Expected-improvement driven search around a fitted surrogate.

    Warm start: ``min(population_size, max_evaluations // 3)`` Latin-hypercube
    points, then batches of candidates maximising the expected improvement over
    a random pool drawn from the bounds.  The search stops early when
    ``SURROGATE_PATIENCE`` consecutive batches fail to improve the incumbent,
    which is what makes it cheaper than plain differential evolution for a
    comparable score.
    """
    variables, lows, highs = _prepare(cfg)
    cap = max(1, int(cfg.max_evaluations))
    run = _Run("surrogate", cfg, variables, cap, progress_cb, cancel_cb)
    rng = np.random.default_rng(int(cfg.seed))
    bounds = list(zip(lows, highs))

    warm = max(2, min(int(cfg.population_size), max(2, cap // 3)))
    warm = min(warm, cap)
    sample = latin_hypercube(warm, lows, highs, rng)
    sample, values = _split_pairs(run.evaluate_pairs(sample))
    run.notify()
    if not values:
        return run.result(warm)

    scores = [aggregate_score(v, run.weights, run.objectives) for v in values]
    model = SurrogateModel(k_nn=cfg.k_nn, rbf_gamma=cfg.rbf_gamma).fit(sample, np.asarray(scores, dtype=float))

    batch_size = max(1, min(run.budget_left(), max(1, int(cfg.population_size) // 4)))
    known: set = {tuple(round(float(value), 12) for value in row) for row in sample}
    stall = 0
    while not run.poll_cancel() and run.budget_left() > 0:
        wanted = min(batch_size, run.budget_left())
        pool_size = max(8 * wanted, 48)
        low_arr = np.asarray(lows, dtype=float)
        high_arr = np.asarray(highs, dtype=float)
        pool = low_arr + np.asarray(rng.random((pool_size, len(lows))), dtype=float) * (high_arr - low_arr)
        incumbent = float(np.max(model.y))
        ei = model.expected_improvement(pool, incumbent, maximise=True)
        chosen = pool[np.argsort(-ei, kind="stable")[:wanted]]
        unique: List[List[float]] = []
        for row in chosen:
            key = tuple(round(float(value), 12) for value in row)
            if key in known:
                continue
            known.add(key)
            unique.append([float(value) for value in row])
        if not unique:
            unique = latin_hypercube(wanted, lows, highs, rng)
        unique, new_values = _split_pairs(run.evaluate_pairs(unique))
        if not new_values:
            break
        run.notify()
        new_scores = [aggregate_score(v, run.weights, run.objectives) for v in new_values]
        improved = any(score > incumbent for score in new_scores)
        merged_x = np.vstack(
            [np.asarray(model.X_raw, dtype=float), np.asarray(unique, dtype=float)]
        )
        merged_y = np.append(np.asarray(model.y, dtype=float), new_scores)
        model = SurrogateModel(k_nn=cfg.k_nn, rbf_gamma=cfg.rbf_gamma).fit(merged_x, merged_y)
        stall = 0 if improved else stall + 1
        if stall >= SURROGATE_PATIENCE:
            break

    run.notify()
    return run.result(max(2, min(int(cfg.population_size), max(2, cap // 3))))


#: Consecutive non-improving surrogate batches that stop :func:`run_surrogate`.
SURROGATE_PATIENCE: int = 2


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------
#: Algorithm name -> runner.  Every runner takes ``(cfg, progress_cb, cancel_cb)``.
ALGORITHM_DISPATCH: Dict[str, Callable[..., Dict[str, Any]]] = {
    "grid": run_grid_search,
    "nelder-mead": run_nelder_mead,
    "differential-evolution": run_differential_evolution,
    "nsga2": run_nsga2,
    "surrogate": run_surrogate,
}


def run_optimizer(
    cfg: OptimizerConfig,
    progress_cb: Optional[Callable[[int, int, float], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Run the algorithm named by ``cfg.algorithm`` and return its payload."""
    algorithm = str(cfg.algorithm)
    runner = ALGORITHM_DISPATCH.get(algorithm)
    if runner is None:
        raise ValueError(
            f"unknown algorithm '{algorithm}'; expected one of {tuple(sorted(ALGORITHM_DISPATCH))}"
        )
    if not isinstance(cfg, OptimizerConfig):
        cfg = from_dict(OptimizerConfig, as_dict(cfg))
    return runner(cfg, progress_cb, cancel_cb)
