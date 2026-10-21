"""REST endpoints of the autonomous genetic research engine.

The endpoints start a research run in the background, report its progress,
publish the result of the best individual (the folding recipe, the crease
pattern, the per step solids, the optimal throw and the statistics of the
full fidelity population), list the checkpoints, resume a run from a
checkpoint and expose the decision journal of the meta controller.

Every trial count of a run is part of the request body, so the size of a run
is never decided by the server.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify

from api.core import BadRequest, NotFound, as_int, body

ga_research_bp = Blueprint("ga_research", __name__)

#: Prefix of every endpoint of the blueprint.
API_PREFIX = "/api/ga/research"

#: The registry of the research runs of this process.
RUNS: Dict[str, "GARunState"] = {}

#: Lock that protects the registry.
RUNS_LOCK = threading.RLock()


def _highest_tier(tier_counts: Dict[str, Any]) -> int:
    """The highest tier the run has reached.

    A tier counts as reached as soon as one individual was evaluated in it,
    so the value is the index of the highest tier with a non zero count.
    """
    tier = 0
    for name in ("tier0", "tier1", "tier2", "tier3"):
        if int(tier_counts.get(name, 0) or 0) > 0:
            tier = int(name[-1])
    return tier


@dataclass
class GARunState:
    """The server side state of one research run."""

    run_id: str
    objective: str
    out_dir: str
    checkpoint_dir: str
    journal_path: str
    overrides: Dict[str, Any] = field(default_factory=dict)
    status: str = "running"
    error: Optional[str] = None
    summary: Optional[Dict[str, Any]] = None
    progress: Optional[Dict[str, Any]] = None
    generations_total: int = 0
    thread: Optional[threading.Thread] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    lock: threading.RLock = field(default_factory=threading.RLock)

    def elapsed_s(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.time()
        return float(end - self.started_at)

    def to_status(self) -> Dict[str, Any]:
        """The progress payload of the run.

        While the run is in flight the live snapshot published by the
        research thread is served; when the run has finished, the final
        summary takes over.  The generation curve of the live snapshot is
        kept in both cases, so the monitor can draw the whole run.
        """
        with self.lock:
            progress = dict(self.progress) if self.progress else {}
            summary = self.summary
        if summary:
            islands = summary.get("islands") or []
            best_fitness = summary.get("best_fitness")
            generation = int(summary.get("generations", 0))
            generations = int(summary.get("config", {}).get("generations", 0))
            evaluations = int(summary.get("evaluations", 0))
            tier = _highest_tier(summary.get("tier_counts") or {})
        else:
            islands = progress.get("islands") or []
            best_fitness = progress.get("best_fitness")
            generation = int(progress.get("generation", 0))
            generations = int(progress.get("generations", 0)) or int(self.generations_total)
            evaluations = int(progress.get("evaluations", 0))
            tier = int(progress.get("tier", 0))
        return {
            "run_id": self.run_id,
            "status": self.status,
            "objective": self.objective,
            "generation": generation,
            "generations": generations,
            "evaluations": evaluations,
            "best_fitness": best_fitness,
            "islands": islands,
            "tier": tier,
            "elapsed_s": self.elapsed_s(),
            "error": self.error,
            "history": progress.get("history") or [],
        }


def _state(run_id: str) -> GARunState:
    """The registered run of an id (raises ``NotFound`` when unknown)."""
    with RUNS_LOCK:
        state = RUNS.get(str(run_id))
    if state is None:
        raise NotFound(f"unknown research run '{run_id}'")
    return state


def _overrides_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """The configuration overrides a request body asks for."""
    overrides: Dict[str, Any] = {}

    def take(key: str, target: str) -> None:
        if payload.get(key) is not None:
            overrides[target] = payload[key]

    take("objective", "objective")
    take("stat_mode", "stat_mode")
    take("seed", "seed")
    take("generations", "generations")
    take("islands", "islands")
    take("population_size", "population_size")
    take("max_trials", "max_trials")
    take("top_k", "top_k")
    take("tier1_trials", "tier1_trials")
    take("tier2_trials", "tier2_trials")
    take("tier3_trials", "tier3_trials")
    take("tier2_duration_s", "tier2_duration_s")
    take("tier3_duration_s", "tier3_duration_s")
    take("paper_gsm", "paper_gsm")
    take("processes", "processes")
    take("checkpoint_dir", "checkpoint_dir")
    take("journal_path", "journal_path")
    take("resume_from", "resume_from")

    out_dir = payload.get("output_dir") or payload.get("out_dir")
    if out_dir:
        overrides["out_dir"] = str(out_dir)
    # The batch evaluator spawns worker processes; inside a server thread
    # that is both slow and fragile, so a run started through the API stays
    # in the process unless the request asks for workers explicitly.
    overrides.setdefault("processes", 1)
    return overrides


def _base_config_from_payload(payload: Dict[str, Any]) -> Any:
    """The configuration a request reads from a configuration file.

    A request may name a YAML file (the format of
    ``config/genetic_research.yaml``) with ``config_path``.  The file
    becomes the *base* of the run and the fields of the request body are
    merged on top of it, so the precedence is the same as on the command
    line: ``request > file > dataclass default``.  ``None`` means "no file",
    i.e. the dataclass defaults are the base.
    """
    path = payload.get("config_path")
    if not path:
        return None
    from physics.ga_config import load_config_file

    try:
        return load_config_file(str(path), {})
    except (OSError, ValueError) as exc:
        raise BadRequest(f"invalid research configuration file: {exc}") from exc


def _worker(state: GARunState, overrides: Dict[str, Any], base_config: Any = None) -> None:
    """The background thread that executes the research run."""
    from physics.ga_domain import run_research

    def on_progress(snapshot: Dict[str, Any]) -> None:
        """Store the live snapshot of the research thread (thread safe)."""
        with state.lock:
            state.progress = dict(snapshot)

    try:
        summary = run_research(
            overrides, base_config=base_config, progress_callback=on_progress
        )
    except Exception as exc:  # the failure is reported through the status
        with state.lock:
            state.status = "failed"
            state.error = f"{type(exc).__name__}: {exc}"
            state.finished_at = time.time()
        return
    with state.lock:
        state.summary = summary
        state.status = str(summary.get("status", "completed"))
        state.finished_at = time.time()


def _start(overrides: Dict[str, Any], base_config: Any = None) -> GARunState:
    """Start a research run in the background and register it."""
    from physics.ga_config import GAConfig

    try:
        base = base_config if base_config is not None else GAConfig()
        config = base.merged(overrides).validate()
    except ValueError as exc:
        raise BadRequest(f"invalid research configuration: {exc}") from exc

    run_id = uuid.uuid4().hex[:16]
    state = GARunState(
        run_id=run_id,
        objective=config.objective,
        out_dir=os.path.abspath(config.out_dir),
        checkpoint_dir=os.path.abspath(config.resolved_checkpoint_dir()),
        journal_path=os.path.abspath(config.resolved_journal_path()),
        overrides=dict(overrides),
        generations_total=int(config.generations),
    )
    thread = threading.Thread(
        target=_worker,
        args=(state, dict(overrides), base_config),
        name=f"ga-research-{run_id}",
        daemon=True,
    )
    state.thread = thread
    with RUNS_LOCK:
        RUNS[run_id] = state
    thread.start()
    return state


@ga_research_bp.post(f"{API_PREFIX}/run")
def ga_research_run():
    """Start a research run.

    The body carries the objective, the trial counts, the tier sizes, the
    statistic mode, the seed, the number of generations and islands and the
    output directory.  An optional ``config_path`` names a configuration
    file whose values are the base of the run; the body overrides them.  The
    run itself executes in the background.
    """
    payload = body()
    overrides = _overrides_from_payload(payload)
    base_config = _base_config_from_payload(payload)
    state = _start(overrides, base_config)
    from physics.ga_config import GAConfig

    base = base_config if base_config is not None else GAConfig()
    config = base.merged(overrides).validate()
    return jsonify(
        {
            "run_id": state.run_id,
            "config": config.to_dict(),
            "status": state.status,
            "objective": config.objective,
            "output_dir": state.out_dir,
        }
    )


@ga_research_bp.get(f"{API_PREFIX}/status/<run_id>")
def ga_research_status(run_id: str):
    """The progress of a research run."""
    state = _state(run_id)
    return jsonify(state.to_status())


@ga_research_bp.get(f"{API_PREFIX}/result/<run_id>")
def ga_research_result(run_id: str):
    """The result of the best individual of a research run."""
    state = _state(run_id)
    with state.lock:
        summary = state.summary
    if not summary:
        return jsonify(
            {
                "run_id": state.run_id,
                "status": state.status,
                "genome": None,
                "recipe": {"steps": [], "markdown": ""},
                "crease_pattern_svg": "",
                "step_meshes": [],
                "throw_params": None,
                "stats": {
                    "mean": 0.0,
                    "median": 0.0,
                    "overall_median": 0.0,
                    "failure_rate": 0.0,
                },
                "fitness": None,
            }
        )
    result = dict(summary.get("result") or {})
    stats = dict(result.get("stats") or summary.get("stats") or {})
    return jsonify(
        {
            "run_id": state.run_id,
            "status": state.status,
            "objective": summary.get("objective"),
            "genome": result.get("genome"),
            "genes": result.get("genes"),
            "recipe": result.get("recipe") or {"steps": [], "markdown": ""},
            "crease_pattern_svg": result.get("crease_pattern_svg", ""),
            "step_meshes": result.get("step_meshes") or [],
            "throw_params": result.get("throw_params"),
            "stats": {
                "mean": stats.get("mean", 0.0),
                "median": stats.get("median", 0.0),
                "overall_median": stats.get("overall_median", 0.0),
                "failure_rate": stats.get("failure_rate", 0.0),
                "selected": stats.get("selected"),
                "stat_mode": stats.get("stat_mode"),
            },
            "fitness": result.get("fitness"),
            "metric": result.get("metric"),
            "artifacts": summary.get("artifacts") or {},
        }
    )


@ga_research_bp.get(f"{API_PREFIX}/checkpoints/<run_id>")
def ga_research_checkpoints(run_id: str):
    """The checkpoints a research run has written."""
    state = _state(run_id)
    directory = state.checkpoint_dir
    entries: List[Dict[str, Any]] = []
    if os.path.isdir(directory):
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(directory, name)
            entry: Dict[str, Any] = {
                "name": name,
                "path": path,
                "size_bytes": os.path.getsize(path),
                "created_at": os.path.getmtime(path),
            }
            if name.startswith("checkpoint_"):
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        payload = json.load(handle)
                    entry["generation"] = int(payload.get("generation", 0))
                    entry["evaluations"] = int(payload.get("evaluation_count", 0))
                    entry["objective"] = (payload.get("config") or {}).get("objective")
                except (OSError, ValueError):
                    entry["generation"] = None
            entries.append(entry)
    return jsonify({"run_id": state.run_id, "checkpoints": entries})


@ga_research_bp.post(f"{API_PREFIX}/resume")
def ga_research_resume():
    """Resume a research run from its latest checkpoint.

    The trial counts of the request override the counts stored in the
    checkpoint, and the override is recorded in the journal of the resumed
    run.
    """
    payload = body()
    run_id = payload.get("run_id")
    if not run_id:
        raise BadRequest("missing required field 'run_id'")
    state = _state(run_id)
    checkpoint = payload.get("checkpoint")
    if not checkpoint:
        latest = os.path.join(state.checkpoint_dir, "latest.json")
        if not os.path.exists(latest):
            raise NotFound(f"research run '{run_id}' has no checkpoint to resume from")
        checkpoint = latest

    overrides: Dict[str, Any] = {"resume_from": str(checkpoint)}
    if payload.get("max_trials") is not None:
        overrides["max_trials"] = as_int(payload["max_trials"], "max_trials")
    if payload.get("top_k") is not None:
        overrides["top_k"] = as_int(payload["top_k"], "top_k")
    if payload.get("generations") is not None:
        overrides["generations"] = as_int(payload["generations"], "generations")
    if payload.get("output_dir") or payload.get("out_dir"):
        overrides["out_dir"] = str(payload.get("output_dir") or payload.get("out_dir"))

    from physics.ga_config import GAConfig

    try:
        config = GAConfig().merged(overrides).validate()
    except ValueError as exc:
        raise BadRequest(f"invalid research configuration: {exc}") from exc

    new_state = _start(overrides)
    return jsonify(
        {
            "run_id": new_state.run_id,
            "previous_run_id": state.run_id,
            "config": config.to_dict(),
            "status": new_state.status,
            "resumed_from": str(checkpoint),
        }
    )


@ga_research_bp.get(f"{API_PREFIX}/journal/<run_id>")
def ga_research_journal(run_id: str):
    """The decision journal of a research run."""
    state = _state(run_id)
    entries: List[Dict[str, Any]] = []
    path = state.journal_path
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return jsonify({"run_id": state.run_id, "entries": entries, "path": path})
