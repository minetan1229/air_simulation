"""Stage1 wind-model scope and rank-risk quantification (Issue #81).

Stage1 (``physics.ga2.stage1.BatchScreener``) is an intentional
**mean-wind-only screener**: ``params.use_mean_wind_only=True`` makes every
RK4 substep reuse the per-individual drawn mean wind without sampling the
spatial / time-varying wind field of :mod:`physics.wind_domain`.  Shear,
gusts and position-dependent turbulence are therefore *not* represented at
screen time.

This module quantifies -- rather than assumes away -- the consequence of
that simplification by re-evaluating the same population under a flat
constant-wind baseline and under variable-wind fields, and reporting rank
agreement (Spearman correlation) and top-k miss rate.

Where Stage1 IS authoritative
    * Fast comparative screening of airframes under near-calm or steady-mean
      wind, for surfacing candidates worth deeper analysis.
    * Ranking *within* the mean-wind approximation: survivors that win
      under the mean wind are a reasonable shortlist.

Where Stage1 is NOT authoritative
    * Absolute flight distance / flight time under shear, gust or
      turbulence fields (the field can add or remove several m/s of relative
      wind across an altitude band).
    * Cross-condition ranking under strongly unsteady or spatially
      heterogeneous wind: the mean-wind rank can invert relative to the
      variable-wind truth.  The rank swap is measured by
      :func:`measure_wind_rank_risk`, not assumed to be zero.
    * Any design decision that hinges on gust response, thermal updrafts,
      or turbulence-induced load peaks -- those must be re-evaluated with
      the full field (reference 6-DoF ``wind_vector`` per stage, stage 2/3).

The ``MEAN_WIND_SCOPE`` mapping below is the machine-readable form of this
statement.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Sequence

import numpy as np

MEAN_WIND_SCOPE: Dict[str, Any] = {
    "screener_mean_wind_only": True,
    "authoritative_for": [
        "fast comparative screening under steady mean wind",
        "ranking within the mean-wind approximation",
        "shortlisting candidates for downstream full-field re-evaluation",
    ],
    "not_authoritative_for": [
        "absolute distance/time under shear, gust or turbulence",
        "cross-condition ranking under unsteady or heterogeneous wind",
        "gust response, thermal updraft or turbulence load-peak claims",
    ],
    "full_field_re_evaluation": "reference 6-DoF wind_vector per RK4 stage (FlightSimulator / evaluate_flight), stage 2/3",
    "quantification": "measure_wind_rank_risk in physics.ga2.wind_model_scope",
}


def _rankdata(values: np.ndarray) -> np.ndarray:
    """Average ranks (1-based) with ties averaged, numpy only."""
    order = np.argsort(np.asarray(values, dtype=float), kind="mergesort")
    ranks = np.empty(len(order), dtype=float)
    sorted_vals = np.asarray(values, dtype=float)[order]
    i = 0
    n = len(order)
    while i < n:
        j = i
        while j + 1 < n and sorted_vals[j + 1] == sorted_vals[i]:
            j += 1
        avg = 0.5 * (i + j) + 1.0
        ranks[order[i : j + 1]] = avg
        i = j + 1
    return ranks


def _spearman(a: Sequence[float], b: Sequence[float]) -> float:
    """Spearman rank correlation; 0.0 when either side has zero variance."""
    if len(a) < 2 or len(a) != len(b):
        return 0.0
    ra = _rankdata(np.asarray(a, dtype=float))
    rb = _rankdata(np.asarray(b, dtype=float))
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    denom = float(np.linalg.norm(ra) * np.linalg.norm(rb))
    if denom == 0.0:
        return 0.0
    return float(np.dot(ra, rb) / denom)


def _top_k_miss_rate(baseline: Sequence[float], other: Sequence[float], k: int) -> float:
    """Fraction of baseline top-k absent from other's top-k (higher is better)."""
    if not baseline or not other or len(baseline) != len(other):
        return 0.0
    k = max(1, min(int(k), len(baseline)))
    base_top = set(np.argsort(np.asarray(baseline, dtype=float))[-k:].tolist())
    other_top = set(np.argsort(np.asarray(other, dtype=float))[-k:].tolist())
    return float(len(base_top - other_top)) / float(k)


def _env_dict(**overrides: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "wind_speed": 2.0,
        "wind_direction_deg": 0.0,
        "gust_intensity": 0.0,
        "thermal_strength": 0.0,
        "turbulence_scale": 0.0,
        "boundary_layer_enabled": False,
        "turbulence_seed": 12345,
        "reference_height_m": 1.0,
    }
    base.update(overrides)
    return base


SCENARIOS: Dict[str, Dict[str, Any]] = {
    "zero": _env_dict(wind_speed=0.0),
    "constant": _env_dict(),
    "shear": _env_dict(boundary_layer_enabled=True),
    "gust": _env_dict(gust_intensity=1.0),
    "turbulence": _env_dict(turbulence_scale=0.35, gust_intensity=0.5),
}

DEFAULT_LAUNCH: Dict[str, Any] = {
    "launch_speed": 8.0,
    "launch_angle_deg": 0.0,
    "launch_height_m": 1.2,
}


def _as_aircraft_dict(item: Any) -> Dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    config = getattr(item, "__dict__", None)
    if isinstance(config, dict):
        return dict(config)
    raise TypeError(f"individual must be a dict or config object, got {type(item)!r}")


def _default_evaluator(aircraft_dict: Dict[str, Any], env_dict: Dict[str, Any]) -> float:
    from physics.flight_sim import evaluate_flight

    result = evaluate_flight(
        dict(aircraft_dict),
        dict(env_dict),
        dict(DEFAULT_LAUNCH),
        duration_s=3.0,
        dt_max=2.0e-2,
    )
    distance = result.get("distance_m", 0.0)
    try:
        return float(distance)
    except (TypeError, ValueError):
        return 0.0


def measure_wind_rank_risk(
    individuals_or_evaluator: Any,
    evaluator: Callable[[Dict[str, Any], Dict[str, Any]], float] | None = None,
    top_k: int = 3,
    seed: int = 12345,
) -> Dict[str, Any]:
    """Evaluate one population under mean and variable wind; report rank risk.

    Args:
        individuals_or_evaluator: either a sequence of aircraft configs/dicts
            (evaluated with :func:`physics.flight_sim.evaluate_flight`) or a
            callable ``(aircraft_dict, env_dict) -> float`` score (higher is
            better).  When a callable is given, ``individuals_or_evaluator``
            is treated as the callable and scores are drawn from ``evaluator``
            default individuals -- callers passing a callable should instead
            pass the population as the second interpretation: the callable
            itself is used as ``evaluator`` and the population must be
            supplied via ``individuals_or_evaluator`` as a sequence.  In
            practice: pass a sequence here and optionally a custom
            ``evaluator``.
        evaluator: optional scoring callable ``(aircraft_dict, env_dict) ->
            float`` (higher is better).  Defaults to 6-DoF ``evaluate_flight``
            distance.
        top_k: size of the top-k set for the miss-rate statistic.
        seed: deterministic seed recorded for reproducibility (the wind field
            itself is seeded via ``turbulence_seed``).

    Returns:
        dict with per-scenario ``correlation`` and ``miss_rate`` against the
        constant (mean-wind) baseline, per-scenario score lists, counts, and
        ``mean_wind_only`` flag.  Empty-input yields zeroed statistics.
    """
    if callable(individuals_or_evaluator) and evaluator is None:
        raise TypeError(
            "measure_wind_rank_risk requires a population sequence as the "
            "first argument; pass the callable via evaluator="
        )
    individuals = list(individuals_or_evaluator)
    n = len(individuals)
    score_fn: Callable[[Dict[str, Any], Dict[str, Any]], float] = (
        evaluator if evaluator is not None else _default_evaluator
    )
    scenarios = {name: dict(env) for name, env in SCENARIOS.items()}
    for env in scenarios.values():
        env["turbulence_seed"] = int(seed)

    scores: Dict[str, List[float]] = {}
    for name, env in scenarios.items():
        scores[name] = [
            float(score_fn(_as_aircraft_dict(ind), dict(env))) for ind in individuals
        ]

    baseline = scores.get("constant", [])
    correlation: Dict[str, float] = {}
    miss_rate: Dict[str, float] = {}
    for name, vals in scores.items():
        if name == "constant":
            correlation[name] = 1.0
            miss_rate[name] = 0.0
        else:
            correlation[name] = _spearman(baseline, vals)
            miss_rate[name] = _top_k_miss_rate(baseline, vals, top_k)

    return {
        "mean_wind_only": True,
        "seed": int(seed),
        "n_individuals": n,
        "n_scenarios": len(scenarios),
        "scenarios": sorted(scenarios.keys()),
        "top_k": int(top_k),
        "baseline": "constant",
        "scores": scores,
        "correlation": correlation,
        "miss_rate": miss_rate,
        "scope": dict(MEAN_WIND_SCOPE),
    }
