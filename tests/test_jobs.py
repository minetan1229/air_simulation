"""Analytic tests for the job/ETA manager and the JSON+CSV storage domain.

Covers prompt.md 3.4 (job fields, ETA closed form, ``alpha = 0.3`` EMA,
0.5 s throttle, non-blocking ``optimizer/run``) and AGENTS.md 2.3 / 7.5 /
8.5 (export, persistence, ETA display).  Every ETA assertion is checked against
a hand computed value; nothing is asserted against a value the implementation
produced in the same run.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from physics.config_schema import (
    DESIGN_VARIABLE_CATALOGUE,
    FLIGHT_LOG_COLUMNS,
    AircraftConfig,
    EnvironmentConfig,
    FlightLogSample,
    JobRecord,
    LaunchConfig,
    OBJECTIVE_DIRECTIONS,
    SimulationSettings,
    as_dict,
    default_config_bundle,
    utc_now_iso,
)
from physics.job_manager import (
    ACTIVE_STATUSES,
    ETA_ALPHA,
    ETA_UPDATE_INTERVAL,
    JOB_STATUSES,
    TERMINAL_STATUSES,
    JobCancelled,
    JobManager,
    TrackedJob,
    clamp_progress,
    ema_recurrence,
    ema_update,
    estimate_remaining,
    estimated_completion_iso,
    eta_seconds,
    evals_per_second,
    format_duration,
    mean_eval_time,
    parse_iso8601,
    remaining_evaluations,
)
from physics.storage_domain import (
    CSV_MIMETYPE,
    JSON_MIMETYPE,
    StorageManager,
    config_bundle_to_rows,
    export_config,
    export_flight_log,
    export_optimizer_result,
    flight_log_to_rows,
    normalise_bundle,
    objective_directions,
    optimizer_result_columns,
    parse_csv,
    parse_json,
    rows_to_csv,
    rows_to_json,
    sample_from_row,
    sanitise_artefact_name,
    unknown_design_variables,
    unknown_objectives,
    validate_config_bundle,
)

# ==========================================================================
# 1. ETA mathematics -- pure functions, hand computed expectations
# ==========================================================================

#: (elapsed, evaluated, total, expected eta) -- every row is 6*... arithmetic
#: a reviewer can redo on paper.
ETA_TRIPLES = [
    (12.0, 4, 10, 18.0),   # t_bar = 12/4 = 3.0, remaining 6  -> 6*3.0
    (10.0, 5, 5, 0.0),     # total == evaluated             -> 0.0
    (10.0, 9, 5, 0.0),     # evaluated > total (over-shoot) -> clamped 0.0
    (2.0, 2, 9, 7.0),      # t_bar = 1.0,   remaining 7     -> 7.0
    (3.6, 3, 8, 6.0),      # t_bar = 1.2,   remaining 5     -> 6.0
    (1.0, 10, 100, 9.0),   # t_bar = 0.1,   remaining 90    -> 9.0
    (0.0, 0, 100, 0.0),    # nothing measured yet           -> 0.0
    (6.0, 3, 0, 0.0),      # unknown budget                 -> 0.0
    (0.5, 0, 4, 2.0),      # t_bar = 0.5/max(0,1) = 0.5     -> 4*0.5
]


def test_eta_constants_match_the_specification():
    assert ETA_ALPHA == 0.3
    assert ETA_UPDATE_INTERVAL == 0.5
    assert JOB_STATUSES == ("queued", "running", "completed", "failed", "cancelled")
    assert ACTIVE_STATUSES == ("queued", "running")
    assert TERMINAL_STATUSES == ("completed", "failed", "cancelled")


def test_mean_eval_time_is_elapsed_over_max_evaluated_one():
    assert mean_eval_time(12.0, 4) == pytest.approx(3.0, rel=1e-15)
    assert mean_eval_time(1.0, 1) == pytest.approx(1.0, rel=1e-15)
    assert mean_eval_time(7.5, 15) == pytest.approx(0.5, rel=1e-15)
    assert mean_eval_time(0.0, 0) == 0.0
    # evaluated == 0 must never raise ZeroDivisionError -> denominator clamped to 1
    assert mean_eval_time(5.0, 0) == pytest.approx(5.0, rel=1e-15)
    assert mean_eval_time(2.0, -3) == pytest.approx(2.0, rel=1e-15)
    # a negative elapsed clock reading is clamped, so the rate stays sane
    assert mean_eval_time(-4.0, 2) == 0.0


def test_remaining_evaluations_never_goes_negative():
    assert remaining_evaluations(0, 10) == 10
    assert remaining_evaluations(10, 10) == 0
    assert remaining_evaluations(11, 10) == 0
    assert remaining_evaluations(0, 0) == 0


@pytest.mark.parametrize("elapsed,evaluated,total,expected", ETA_TRIPLES)
def test_eta_seconds_matches_the_closed_form(elapsed, evaluated, total, expected):
    assert eta_seconds(elapsed, evaluated, total) == pytest.approx(expected, abs=1e-12)
    # estimate_remaining is the same closed form and must agree bit for bit
    assert estimate_remaining(elapsed, evaluated, total) == pytest.approx(expected, abs=1e-12)
    # and it is exactly (total - evaluated)_+ * t_bar
    expected_formula = max(total - evaluated, 0) * mean_eval_time(elapsed, evaluated)
    assert eta_seconds(elapsed, evaluated, total) == pytest.approx(expected_formula, abs=1e-12)
    assert eta_seconds(elapsed, evaluated, total) >= 0.0


def test_eta_seconds_never_raises_for_degenerate_budgets():
    for elapsed in (0.0, 1.0, -3.0):
        for evaluated in (0, 1, -5):
            for total in (0, 1, -2):
                assert eta_seconds(elapsed, evaluated, total) >= 0.0


def test_evals_per_second_is_the_reciprocal_of_t_bar():
    assert evals_per_second(12.0, 4) == pytest.approx(1.0 / 3.0, rel=1e-15)
    assert evals_per_second(0.25, 2) == pytest.approx(8.0, rel=1e-15)
    assert evals_per_second(5.0, 1) == pytest.approx(0.2, rel=1e-15)  # 5 s / evaluation
    assert evals_per_second(1.0, 100) == pytest.approx(100.0, rel=1e-15)
    # t_bar <= 0 -> no rate at all (never ZeroDivisionError)
    assert evals_per_second(0.0, 7) == 0.0
    assert evals_per_second(0.0, 0) == 0.0
    assert evals_per_second(-2.0, 3) == 0.0
    for elapsed, evaluated, total, _expected in ETA_TRIPLES:
        t_bar = mean_eval_time(elapsed, evaluated)
        expected = 0.0 if t_bar <= 0.0 else 1.0 / t_bar
        assert evals_per_second(elapsed, evaluated) == pytest.approx(expected, rel=1e-15)


# ==========================================================================
# 2. Exponential moving average (alpha = 0.3) with first-sample seeding
# ==========================================================================

#: Hand computed ema_update(0, 10) = 10 (seed), then 0.3*s + 0.7*previous.
EMA_SAMPLES = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
EMA_EXPECTED = [
    10.0,
    13.0,
    18.1,
    24.67,
    32.269,
    40.5883,
    49.41181,
    58.588267,
    68.0117869,
    77.60825083,
]


def test_ema_update_follows_the_recurrence_over_ten_steps():
    previous = 0.0
    assert len(EMA_SAMPLES) == len(EMA_EXPECTED) == 10
    for index, (sample, expected) in enumerate(zip(EMA_SAMPLES, EMA_EXPECTED)):
        if index > 0:
            # explicit recurrence identity for every step after the seed
            assert ema_update(previous, sample) == pytest.approx(
                0.3 * sample + 0.7 * previous, rel=1e-12
            )
        updated = ema_update(previous, sample)
        assert updated == pytest.approx(expected, rel=1e-12), f"step {index}"
        previous = updated
    assert previous == pytest.approx(EMA_EXPECTED[-1], rel=1e-12)


def test_ema_update_seeds_with_the_first_sample():
    # previous == 0 means "no history": the sample is adopted verbatim so the
    # start-up transient is not attenuated by 0.7.
    assert ema_update(0.0, 5.0) == 5.0
    assert ema_update(0, 123.4) == 123.4
    assert ema_update(0.0, 123.4, alpha=0.05) == 123.4
    assert ema_update(0.0, 0.0) == 0.0
    # the unseeded textbook step, exposed separately
    assert ema_recurrence(10.0, 20.0, alpha=0.5) == pytest.approx(15.0)
    assert ema_recurrence(10.0, 20.0) == pytest.approx(13.0)
    assert ema_update(10.0, 20.0, alpha=0.5) == pytest.approx(15.0)


def test_ema_suppresses_the_startup_estimate_in_both_directions():
    # (a) slow first evaluation (5 s per eval -> 0.2 evals/s) then a steady
    #     100 evals/s sample: the smoothed value stays BELOW the raw sample.
    spike = evals_per_second(5.0, 1)
    steady = evals_per_second(1.0, 100)
    assert spike == pytest.approx(0.2)
    assert steady == pytest.approx(100.0)
    assert spike < steady
    first = ema_update(0.0, spike)
    second = ema_update(first, steady)
    assert first == pytest.approx(0.2)                       # seeded
    assert second == pytest.approx(0.3 * 100.0 + 0.7 * 0.2)  # = 30.14
    assert second == pytest.approx(30.14, rel=1e-12)
    assert second < steady, "the EMA must not jump to the raw sample"
    assert spike < second < steady, "and it must be a proper low pass"
    # the raw (unsmoothed) estimate would have shown 100 evals/s straight away
    assert evals_per_second(1.0, 100) == pytest.approx(100.0)

    # (b) a fast first evaluation (0.01 s -> 100 evals/s) then a steady
    #     5 s/eval sample: the over-estimate is damped from 100 down to 70.06.
    fast = evals_per_second(0.01, 1)
    slow = evals_per_second(5.0, 1)
    assert fast == pytest.approx(100.0)
    smoothed = ema_update(ema_update(0.0, fast), slow)
    assert smoothed == pytest.approx(0.3 * slow + 0.7 * fast)
    assert smoothed == pytest.approx(70.06, rel=1e-12)
    assert slow < smoothed < fast

    # (c) the EMA is a low pass: it converges and never overshoots the target
    value = 0.0
    gaps = []
    for _ in range(60):
        value = ema_update(value, 42.0)
        gaps.append(abs(value - 42.0))
    assert value == pytest.approx(42.0, abs=1e-6)
    assert gaps == sorted(gaps, reverse=True)


# ==========================================================================
# 3. Small pure helpers
# ==========================================================================

def test_format_duration_renders_mm_ss_or_hh_mm_ss():
    assert format_duration(0) == "00:00"
    assert format_duration(9.9) == "00:09"      # truncated, never rounded up
    assert format_duration(59) == "00:59"
    assert format_duration(60) == "01:00"
    assert format_duration(61.75) == "01:01"
    assert format_duration(3599) == "59:59"
    assert format_duration(3600) == "01:00:00"
    assert format_duration(3661) == "01:01:01"
    assert format_duration(86399) == "23:59:59"
    assert format_duration(-12) == "00:00"
    assert format_duration(None) == "00:00"
    for seconds in (0.0, 1.0, 59.0, 61.0, 3600.0, 7325.0):
        text = format_duration(seconds)
        assert len(text) in (5, 8)
        assert len(text) - text.count(":") in (4, 6)    # mm:ss or hh:mm:ss
        assert all(len(part) == 2 for part in text.split(":"))


def test_clamp_progress_bounds_the_ratio():
    assert clamp_progress(-0.5) == 0.0
    assert clamp_progress(0.0) == 0.0
    assert clamp_progress(0.25) == 0.25
    assert clamp_progress(1.0) == 1.0
    assert clamp_progress(17.0) == 1.0
    assert clamp_progress(float("nan")) == 0.0
    assert clamp_progress(float("inf")) == 1.0
    assert clamp_progress("nonsense") == 0.0


def test_estimated_completion_iso_accepts_several_clock_inputs():
    base = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert estimated_completion_iso(90.0, base) == "2026-09-01T12:01:30Z"
    assert estimated_completion_iso(0.0, base) == "2026-09-01T12:00:00Z"
    assert estimated_completion_iso(-5.0, base) == "2026-09-01T12:00:00Z"
    assert estimated_completion_iso(3661.0, base.timestamp()) == "2026-09-01T13:01:01Z"
    assert estimated_completion_iso(10.0, datetime(2026, 9, 1, 12, 0, 0)) == (
        "2026-09-01T12:00:10Z"
    )
    ahead = parse_iso8601(estimated_completion_iso(10.0))
    assert ahead > datetime.now(timezone.utc)
    with pytest.raises(TypeError):
        estimated_completion_iso(10.0, "now please")


def test_parse_iso8601_reads_the_contract_timestamps():
    stamp = utc_now_iso()
    parsed = parse_iso8601(stamp)
    assert parsed.tzinfo is not None
    assert stamp.endswith("Z")
    assert abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 5.0
    # the +00:00 spelling and the Z spelling denote the same instant
    assert parse_iso8601("2026-09-01T12:00:00+00:00") == datetime(
        2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc
    )
    assert parse_iso8601("2026-09-01T12:00:00Z") == parse_iso8601("2026-09-01T12:00:00")
    # naive input is interpreted as UTC, never as local time
    assert parse_iso8601("2026-09-01T12:00:00") == datetime(
        2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc
    )
    with pytest.raises(ValueError):
        parse_iso8601("")
    with pytest.raises(ValueError):
        parse_iso8601("not a timestamp")


# ==========================================================================
# 4. JobManager lifecycle
# ==========================================================================

@pytest.fixture()
def manager():
    return JobManager()


def test_create_populates_every_contract_field(manager):
    record = manager.create(
        name="Differential Evolution",
        total_evaluations=400,
        backend="multiprocessing",
        objectives=["max_distance", "min_max_stress"],
        algorithm="differential-evolution",
        model_id="mesh-42",
    )
    assert record.job_id and len(record.job_id) == 36
    assert record.status == "queued"
    assert record.name == "Differential Evolution"
    assert record.algorithm == "differential-evolution"
    assert record.backend == "multiprocessing"
    assert record.model_id == "mesh-42"
    assert record.objectives == ["max_distance", "min_max_stress"]
    assert record.total_evaluations == 400
    assert record.evaluated_count == 0
    assert record.progress == 0.0
    assert record.eta_seconds == 0.0
    assert record.evals_per_second == 0.0
    assert record.elapsed_seconds == 0.0
    assert record.started_at == ""
    assert record.finished_at == ""
    assert record.estimated_completion == ""
    assert record.updated_at != ""
    # every declared field exists on the record we were handed
    assert set(as_dict(record)) == set(as_dict(JobRecord()))
    # weighted objectives given as a mapping degrade to their names
    weighted = manager.create(objectives={"max_ld_ratio": 0.5})
    assert weighted.objectives == ["max_ld_ratio"]
    assert weighted.backend == "serial" and weighted.name == "optimizer"


def test_full_lifecycle_queued_running_completed(manager):
    created = manager.create(total_evaluations=40)
    job_id = created.job_id
    assert manager.get(job_id).status == "queued"

    running = manager.start(job_id)
    assert running.status == "running"
    assert running.started_at != "" and running.finished_at == ""
    parse_iso8601(running.started_at)

    for count in (10, 20, 30):
        updated = manager.update(job_id, count, backend="threading")
        assert updated.evaluated_count == count
        assert updated.progress == pytest.approx(count / 40)
        assert updated.backend == "threading"
        assert updated.updated_at != ""
    # time.monotonic() on Windows has a ~15.6 ms tick, so give the job some
    # measurable runtime before freezing it
    time.sleep(0.05)

    done = manager.finish(job_id, message="optimal", result_path="runs/opt.result.json")
    assert done.status == "completed"
    assert done.progress == 1.0
    assert done.eta_seconds == 0.0
    assert done.finished_at != "" and done.estimated_completion == done.finished_at
    assert done.message == "optimal"
    assert done.result_path == "runs/opt.result.json"
    assert done.elapsed_seconds > 0.0
    assert done.evals_per_second > 0.0        # achieved throughput, not a forecast
    assert manager.active_count() == 0
    assert manager.is_cancelled(job_id) is False


def test_fail_and_cancel_transitions(manager):
    first = manager.create(total_evaluations=10)
    manager.start(first.job_id)
    manager.update(first.job_id, 4)
    failed = manager.fail(first.job_id, "RuntimeError: pool died")
    assert failed.status == "failed"
    assert failed.message == "RuntimeError: pool died"
    assert failed.progress == pytest.approx(0.4)   # frozen where it stopped
    assert failed.eta_seconds == 0.0
    assert failed.finished_at != ""

    second = manager.create(total_evaluations=10)
    manager.start(second.job_id)
    manager.update(second.job_id, 3)
    cancelled = manager.cancel(second.job_id)
    assert cancelled.status == "cancelled"
    assert cancelled.progress == pytest.approx(0.3)  # frozen, never 1.0
    assert cancelled.eta_seconds == 0.0
    assert cancelled.finished_at != ""
    assert manager.active_count() == 0


def test_terminal_statuses_are_written_exactly_once(manager):
    completed = manager.create(total_evaluations=5)
    manager.start(completed.job_id)
    manager.finish(completed.job_id)
    assert manager.finish(completed.job_id, message="late").status == "completed"
    assert manager.finish(completed.job_id).message == ""      # no-op
    assert manager.fail(completed.job_id, "late").status == "completed"
    assert manager.cancel(completed.job_id).status == "completed"
    assert manager.cancel(completed.job_id).finished_at == manager.get(completed.job_id).finished_at
    # a straggling worker cannot resurrect a finished job
    assert manager.update(completed.job_id, 4).evaluated_count == 0

    cancelled = manager.create(total_evaluations=5)
    manager.start(cancelled.job_id)
    manager.update(cancelled.job_id, 2)
    manager.cancel(cancelled.job_id)
    assert manager.finish(cancelled.job_id, message="too late").status == "cancelled"
    assert manager.finish(cancelled.job_id).message == "cancelled by request"
    assert manager.fail(cancelled.job_id, "too late").status == "cancelled"


def test_start_is_idempotent(manager):
    record = manager.create(total_evaluations=6)
    first = manager.start(record.job_id)
    again = manager.start(record.job_id)
    assert again.status == "running"
    assert again.started_at == first.started_at      # the clock is armed once
    assert manager.get(record.job_id).status == "running"


def test_unknown_job_ids_behave_documented(manager):
    with pytest.raises(KeyError):
        manager.get("does-not-exist")
    with pytest.raises(KeyError):
        manager.update("does-not-exist", 1)
    with pytest.raises(KeyError):
        manager.finish("does-not-exist")
    with pytest.raises(KeyError):
        manager.fail("does-not-exist", "x")
    with pytest.raises(KeyError):
        manager.cancel("does-not-exist")
    with pytest.raises(KeyError):
        manager.request_cancel("does-not-exist")
    with pytest.raises(KeyError):
        manager.is_cancel_requested("does-not-exist")
    with pytest.raises(KeyError):
        manager.is_cancelled("does-not-exist")
    with pytest.raises(KeyError):
        manager.snapshot("does-not-exist")
    with pytest.raises(KeyError):
        manager.to_dict("does-not-exist")
    with pytest.raises(KeyError):
        manager.join("does-not-exist", 0.0)
    assert manager.find("does-not-exist") is None
    assert manager.remove("does-not-exist") is False
    assert manager.list_jobs() == []
    assert manager.active_count() == 0
    assert manager.jobs == {}


def test_list_jobs_filters_and_active_count(manager):
    ids = {}
    for name in ("grid", "nsga2", "surrogate"):
        ids[name] = manager.create(name=name, total_evaluations=4).job_id
    manager.start(ids["grid"])
    manager.update(ids["grid"], 2)
    manager.finish(ids["grid"])
    manager.start(ids["nsga2"])
    manager.cancel(ids["nsga2"])
    assert [job.name for job in manager.list_jobs()] == ["grid", "nsga2", "surrogate"]
    assert [job.job_id for job in manager.list_jobs(status="completed")] == [ids["grid"]]
    assert [job.job_id for job in manager.list_jobs(status="cancelled")] == [ids["nsga2"]]
    assert [job.job_id for job in manager.list_jobs(status="queued")] == [ids["surrogate"]]
    assert manager.list_jobs(status="failed") == []
    assert manager.list_jobs(status="running") == []
    assert manager.active_count() == 1
    assert set(manager.jobs) == set(ids.values())
    assert manager.remove(ids["surrogate"]) is True
    assert manager.remove(ids["surrogate"]) is False
    manager.clear()
    assert manager.list_jobs() == [] and manager.jobs == {}
    assert manager.active_count() == 0


def test_cancel_flag_round_trip(manager):
    record = manager.create(total_evaluations=20)
    assert manager.is_cancel_requested(record.job_id) is False
    assert manager.is_cancelled(record.job_id) is False
    manager.start(record.job_id)
    assert manager.request_cancel(record.job_id) is True
    assert manager.request_cancel(record.job_id) is False   # idempotent
    assert manager.is_cancel_requested(record.job_id) is True
    # the flag alone does not change the status ...
    assert manager.is_cancelled(record.job_id) is False
    assert manager.get(record.job_id).status == "running"
    assert manager.snapshot(record.job_id)["cancel_requested"] is True
    manager.cancel(record.job_id)
    assert manager.is_cancelled(record.job_id) is True
    assert manager.get(record.job_id).status == "cancelled"

    fresh = manager.create(total_evaluations=20)
    assert manager.is_cancelled(fresh.job_id) is False
    # cancel() itself raises the flag so the worker notices
    third = manager.create(total_evaluations=20)
    manager.start(third.job_id)
    manager.cancel(third.job_id)
    assert manager.is_cancel_requested(third.job_id) is True


def test_get_returns_a_copy(manager):
    record = manager.create(total_evaluations=10)
    manager.start(record.job_id)
    first = manager.get(record.job_id)
    first.status = "failed"
    first.evaluated_count = 999
    first.progress = 1.0
    first.objectives.append("injected")
    second = manager.get(record.job_id)
    assert second.status == "running"
    assert second.evaluated_count == 0
    assert second.progress == 0.0
    assert second.objectives == []
    manager.update(record.job_id, 5)
    third = manager.get(record.job_id)
    assert third.evaluated_count == 5
    assert third.progress == pytest.approx(0.5)
    # the property and the list are copies too
    assert manager.jobs[record.job_id].evaluated_count == 5
    for job in manager.list_jobs():
        job.evaluated_count = -1
    assert manager.get(record.job_id).evaluated_count == 5
    assert manager.find(record.job_id) is not record


def test_snapshot_and_to_dict_are_json_serialisable(manager):
    record = manager.create(
        name="Grid Sweep",
        total_evaluations=8,
        backend="threading",
        objectives={"max_distance": 1.0, "min_max_stress": 0.25},
        algorithm="grid",
        model_id="mesh-7",
    )
    manager.start(record.job_id)
    time.sleep(ETA_UPDATE_INTERVAL + 0.05)
    manager.update(record.job_id, 2, backend="threading", total_evaluations=8)
    snapshot = manager.snapshot(record.job_id)
    assert json.loads(json.dumps(snapshot)) == snapshot      # pure JSON types
    for key in (
        "eta_seconds",
        "estimated_completion",
        "eta_display",
        "elapsed_display",
        "completion_clock",
        "remaining_evaluations",
        "cancel_requested",
        "active",
        "progress",
        "status",
    ):
        assert key in snapshot, key
    assert snapshot["eta_seconds"] > 0.0
    assert snapshot["eta_display"] == format_duration(snapshot["eta_seconds"])
    assert snapshot["remaining_evaluations"] == 6
    assert snapshot["active"] is True and snapshot["status"] == "running"
    assert snapshot["completion_clock"] == snapshot["estimated_completion"][11:19]
    assert parse_iso8601(snapshot["estimated_completion"]) > datetime.now(timezone.utc)
    assert snapshot["objectives"] == ["max_distance", "min_max_stress"]

    plain = manager.to_dict(record.job_id)
    assert set(plain) == set(as_dict(JobRecord()))
    assert json.dumps(plain)
    assert plain["eta_seconds"] > 0.0
    assert plain["estimated_completion"].endswith("Z")

    manager.finish(record.job_id)
    frozen = manager.snapshot(record.job_id)
    assert frozen["active"] is False
    assert frozen["eta_seconds"] == 0.0
    assert frozen["estimated_completion"] == frozen["finished_at"]
    assert json.loads(json.dumps(frozen)) == frozen


# ==========================================================================
# 5. Progress / ETA / throttle behaviour against the live clock
# ==========================================================================

def test_progress_and_eta_stay_consistent(manager):
    total = 25
    record = manager.create(total_evaluations=total)
    manager.start(record.job_id)
    for count in range(1, 11):
        time.sleep(0.06)
        manager.update(record.job_id, count)
        current = manager.get(record.job_id)
        assert 0.0 <= current.progress <= 1.0
        assert current.progress == pytest.approx(count / total)
        assert current.eta_seconds >= 0.0
    # after the interval the ETA is exactly (total - n) * elapsed / max(n, 1)
    time.sleep(ETA_UPDATE_INTERVAL + 0.05)
    manager.update(record.job_id, 12)
    settled = manager.get(record.job_id)
    expected = (total - 12) * (settled.elapsed_seconds / max(12, 1))
    assert settled.eta_seconds == pytest.approx(expected, rel=1e-12)
    assert settled.progress == pytest.approx(12 / total)
    assert settled.evals_per_second > 0.0
    assert settled.estimated_completion == estimated_completion_iso(settled.eta_seconds)


def test_over_shooting_the_budget_clamps_progress_and_eta(manager):
    record = manager.create(total_evaluations=5)
    manager.start(record.job_id)
    time.sleep(ETA_UPDATE_INTERVAL + 0.05)
    manager.update(record.job_id, 9)
    current = manager.get(record.job_id)
    assert current.progress == 1.0                     # clamped
    assert current.eta_seconds == 0.0                  # nothing outstanding
    assert current.evals_per_second > 0.0


def test_eta_recomputation_is_throttled_to_half_a_second(manager):
    total = 200
    record = manager.create(total_evaluations=total)
    manager.start(record.job_id)

    # 20 back-to-back updates: the counters must always advance, the ETA never
    # recompute (no sleep -> the monotonic clock cannot reach the 0.5 s window)
    for count in range(1, 21):
        manager.update(record.job_id, count)
    rapid = manager.get(record.job_id)
    assert rapid.evaluated_count == 20                 # every call recorded
    assert rapid.progress == pytest.approx(20 / total)  # every call recorded
    assert rapid.updated_at != ""
    assert rapid.elapsed_seconds < ETA_UPDATE_INTERVAL
    assert rapid.evals_per_second == 0.0               # 0.5 s not reached
    assert rapid.eta_seconds == 0.0

    # first update past the interval performs the very first ETA computation
    time.sleep(ETA_UPDATE_INTERVAL + 0.05)
    manager.update(record.job_id, 21)
    first = manager.get(record.job_id)
    raw_first = evals_per_second(first.elapsed_seconds, 21)
    assert first.evals_per_second == pytest.approx(raw_first)   # EMA seeded
    assert first.eta_seconds > 0.0
    assert first.estimated_completion.endswith("Z")

    # further updates inside the next interval must not touch the ETA triple
    for count in range(22, 31):
        manager.update(record.job_id, count)
    throttled = manager.get(record.job_id)
    assert throttled.evaluated_count == 30
    assert throttled.progress == pytest.approx(30 / total)
    assert throttled.evals_per_second == first.evals_per_second
    assert throttled.eta_seconds == first.eta_seconds
    assert throttled.estimated_completion == first.estimated_completion
    # the monotonic clock keeps running (it may not tick between two calls on
    # Windows, where time.monotonic() has a ~15.6 ms resolution)
    assert throttled.elapsed_seconds >= first.elapsed_seconds > 0.0

    # ... and the next interval recomputes with the 0.3 / 0.7 recurrence
    time.sleep(ETA_UPDATE_INTERVAL + 0.05)
    manager.update(record.job_id, 31)
    second = manager.get(record.job_id)
    raw_second = evals_per_second(second.elapsed_seconds, 31)
    assert second.evals_per_second == pytest.approx(
        ETA_ALPHA * raw_second + (1.0 - ETA_ALPHA) * first.evals_per_second
    )
    assert second.eta_seconds != first.eta_seconds
    assert second.eta_seconds > 0.0
    assert parse_iso8601(second.estimated_completion) > datetime.now(timezone.utc)


def test_finish_forces_a_final_eta_update(manager):
    record = manager.create(total_evaluations=10)
    manager.start(record.job_id)
    manager.update(record.job_id, 2)              # inside the throttle window
    assert manager.get(record.job_id).evals_per_second == 0.0
    time.sleep(0.05)                              # measurable monotonic elapsed
    done = manager.finish(record.job_id)
    assert done.status == "completed"
    assert done.eta_seconds == 0.0
    assert done.elapsed_seconds > 0.0
    assert done.evals_per_second == pytest.approx(2.0 / done.elapsed_seconds)
    assert done.progress == 1.0


def test_estimated_completion_is_a_future_timestamp_while_running(manager):
    record = manager.create(total_evaluations=50)
    manager.start(record.job_id)
    time.sleep(ETA_UPDATE_INTERVAL + 0.05)
    manager.update(record.job_id, 1)
    running = manager.get(record.job_id)
    completion = parse_iso8601(running.estimated_completion)
    now = datetime.now(timezone.utc)
    assert completion > now
    assert (completion - now).total_seconds() == pytest.approx(running.eta_seconds, abs=1.5)
    # 49 evaluations outstanding at t_bar = elapsed -> eta ~= 49 * elapsed
    assert running.eta_seconds == pytest.approx(49.0 * running.elapsed_seconds, rel=1e-12)
    finished = manager.finish(record.job_id)
    assert finished.estimated_completion == finished.finished_at
    assert parse_iso8601(finished.estimated_completion) <= now + timedelta(seconds=2)


def test_concurrent_updates_are_serialised(manager):
    record = manager.create(total_evaluations=400)
    manager.start(record.job_id)
    failures = []

    def worker(offset: int) -> None:
        try:
            for index in range(25):
                manager.update(record.job_id, offset * 25 + index + 1)
                manager.snapshot(record.job_id)
        except Exception as exc:  # pragma: no cover - failure path
            failures.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20.0)
        assert not thread.is_alive()
    assert not failures
    final = manager.get(record.job_id)
    assert 1 <= final.evaluated_count <= 200
    assert final.progress == pytest.approx(final.evaluated_count / 400)
    assert final.status == "running"
    assert manager.snapshot(record.job_id)["active"] is True


# ==========================================================================
# 6. Non blocking run_tracked driver
# ==========================================================================

def test_run_tracked_returns_the_job_id_immediately(manager):
    progress_calls = []
    observations = []

    def work(progress_cb):
        for count in range(1, 5):
            progress_cb(count)
            progress_calls.append(count)
            time.sleep(0.25)          # ~1.0 s of real work
        return {"algorithm": "unit-test", "best_score": 0.75}

    started = time.perf_counter()
    job_id, runner = manager.run_tracked(
        work,
        name="Differential Evolution",
        total_evaluations=4,
        backend="threading",
        objectives=["max_distance", "min_max_stress"],
        algorithm="differential-evolution",
        model_id="mesh-1",
        on_update=observations.append,
    )
    returned_after = time.perf_counter() - started
    assert isinstance(job_id, str) and len(job_id) == 36
    assert isinstance(runner, TrackedJob)
    assert runner.daemon is True
    # the callable alone needs ~1.0 s; returning must cost microseconds
    assert returned_after < 0.25, f"run_tracked blocked for {returned_after:.3f}s"
    assert manager.get(job_id).status in ("queued", "running")

    assert manager.join(job_id, timeout=20.0) is True
    finished = manager.get(job_id)
    assert finished.status == "completed"
    assert finished.progress == 1.0
    assert finished.eta_seconds == 0.0
    assert finished.evaluated_count == 4
    assert finished.backend == "threading"
    assert finished.name == "Differential Evolution"
    assert finished.algorithm == "differential-evolution"
    assert finished.objectives == ["max_distance", "min_max_stress"]
    assert finished.finished_at != ""
    assert progress_calls == [1, 2, 3, 4]
    assert len(observations) == 4
    assert observations[-1].evaluated_count == 4
    assert observations[-1].status == "running"      # observers see pre-finish state

    expected = {"algorithm": "unit-test", "best_score": 0.75}
    assert runner.result(timeout=1.0) == expected
    assert runner.result_or_exception(1.0) == expected
    assert manager.result(job_id) == expected
    assert manager.exception(job_id) is None
    assert runner.exception(1.0) is None
    assert manager.join(job_id) is True             # already finished, returns at once
    assert manager.tracked(job_id) is runner


def test_run_tracked_records_failures(manager):
    def boom(progress_cb):
        progress_cb(1)
        raise RuntimeError("exploded in the worker pool")

    job_id, runner = manager.run_tracked(boom, total_evaluations=10)
    assert manager.join(job_id, timeout=10.0) is True
    record = manager.get(job_id)
    assert record.status == "failed"
    assert "exploded in the worker pool" in record.message
    assert "RuntimeError" in record.message
    assert record.progress == pytest.approx(0.1)     # frozen where it stopped
    assert record.eta_seconds == 0.0
    assert record.finished_at != ""
    assert isinstance(manager.exception(job_id), RuntimeError)
    assert isinstance(runner.result_or_exception(1.0), RuntimeError)
    with pytest.raises(RuntimeError):
        runner.result(1.0)
    with pytest.raises(RuntimeError):
        manager.result(job_id)


def test_run_tracked_honours_a_requested_cancel(manager):
    def endless(progress_cb):
        for count in range(1, 500):
            progress_cb(count)
            time.sleep(0.005)
        return "finished anyway"

    job_id, runner = manager.run_tracked(endless, total_evaluations=500)
    time.sleep(0.08)
    assert manager.request_cancel(job_id) is True
    assert manager.join(job_id, timeout=15.0) is True
    record = manager.get(job_id)
    assert record.status == "cancelled"
    assert 0.0 < record.progress < 1.0
    assert record.eta_seconds == 0.0
    assert manager.is_cancelled(job_id) is True
    assert isinstance(runner.result_or_exception(1.0), JobCancelled)


def test_run_tracked_stop_callback_cancels_without_a_flag(manager):
    def stop_early(progress_cb):
        for count in range(1, 50):
            if not progress_cb.stop(count, keep_going=False):
                break
        return "should not count as success"

    job_id, runner = manager.run_tracked(stop_early, total_evaluations=50)
    assert manager.join(job_id, timeout=10.0) is True
    record = manager.get(job_id)
    assert record.status == "cancelled"
    assert record.evaluated_count == 1
    assert isinstance(runner.result_or_exception(1.0), JobCancelled)


def test_run_tracked_survives_a_broken_observer(manager):
    def broken_observer(record):
        raise RuntimeError("the UI went away")

    def work(progress_cb):
        progress_cb(1)
        return 42

    job_id, runner = manager.run_tracked(work, total_evaluations=1, on_update=broken_observer)
    assert manager.join(job_id, timeout=10.0) is True
    assert manager.get(job_id).status == "completed"
    assert runner.result(1.0) == 42


def test_join_returns_false_for_manually_driven_jobs(manager):
    record = manager.create(total_evaluations=4)
    manager.start(record.job_id)
    assert manager.tracked(record.job_id) is None
    assert manager.join(record.job_id, timeout=0.01) is False   # still running
    manager.finish(record.job_id)
    assert manager.join(record.job_id, timeout=0.01) is True
    assert manager.result_or_exception(record.job_id) is None


def test_message_from_progress_cb_is_recorded(manager):
    def work(progress_cb):
        progress_cb(1, total_evaluations=5, backend="multiprocessing", message="evaluating 1/5")
        return "ok"

    job_id, _ = manager.run_tracked(work, total_evaluations=5, backend="serial")
    assert manager.join(job_id, timeout=10.0) is True
    record = manager.get(job_id)
    assert record.status == "completed"
    assert record.message == "evaluating 1/5"
    assert record.backend == "multiprocessing"
    assert record.total_evaluations == 5


# ==========================================================================
# 7. CSV / JSON primitives
# ==========================================================================

def _tricky_rows():
    return [
        {"time_s": 0.0, "note": "plain", "value": 1.5, "flag": True},
        {"time_s": 1.0, "note": "comma, inside", "value": -2.25, "flag": False},
        {"time_s": 2.0, "note": 'quotes " and doubled "" inside', "value": 0.0, "flag": True},
        {"time_s": 3.0, "note": "line1\r\nline2", "value": 1e-9, "flag": False},
        {"time_s": 4.0, "note": "\u65e5\u672c\u8a9e\u306e\u30c6\u30ad\u30b9\u30c8", "value": 123456789.125, "flag": True},
    ]


TRICKY_COLUMNS = ["time_s", "note", "value", "flag"]


def test_rows_to_csv_round_trips_commas_quotes_and_crlf():
    rows = _tricky_rows()
    text = rows_to_csv(rows, columns=TRICKY_COLUMNS)
    assert text.endswith("\r\n")
    # one terminator per record plus the one embedded inside the quoted field
    assert text.count("\r\n") == len(rows) + 2
    assert text.split("\r\n")[0] == "time_s,note,value,flag"
    assert '"comma, inside"' in text
    assert '"line1\r\nline2"' in text
    assert '""' in text                       # the embedded quote is doubled
    assert parse_csv(text) == rows


def test_rows_to_csv_handles_empty_and_single_row_payloads():
    assert rows_to_csv([]) == ""
    assert parse_csv("") == []
    assert parse_csv(rows_to_csv([])) == []
    single = [{"time_s": 1.0, "x_m": 2.0}]
    assert rows_to_csv(single, columns=["time_s", "x_m"]) == "time_s,x_m\r\n1.0,2.0\r\n"
    assert parse_csv(rows_to_csv(single, columns=["time_s", "x_m"])) == single
    # a header-only CSV (no rows at all) still round-trips to an empty list
    header_only = rows_to_csv([], columns=list(FLIGHT_LOG_COLUMNS))
    assert header_only == ",".join(FLIGHT_LOG_COLUMNS) + "\r\n"
    assert parse_csv(header_only) == []
    with pytest.raises(ValueError):
        rows_to_csv([], columns=["a", "a"])


def test_csv_keeps_unicode_verbatim_and_is_utf8_clean():
    rows = [{"name": "\u65e5\u672c\u8a9e", "value": "\u00e9\u00fc\u00f1", "mixed": "\u3053\u3093\u306b\u3061\u306f"}]
    text = rows_to_csv(rows, columns=["name", "value", "mixed"])
    assert "\u65e5\u672c\u8a9e" in text
    assert text.encode("utf-8").decode("utf-8") == text
    assert parse_csv(text) == rows


def test_parse_csv_decodes_the_scalar_types_the_encoder_wrote():
    rows = [
        {"b": True, "f": False, "i": 7, "n": -12, "x": 1.25, "e": 1.2e-9, "s": "text", "blank": None}
    ]
    columns = ["b", "f", "i", "n", "x", "e", "s", "blank"]
    decoded = parse_csv(rows_to_csv(rows, columns=columns))
    assert decoded[0]["b"] is True and decoded[0]["f"] is False
    assert decoded[0]["i"] == 7 and isinstance(decoded[0]["i"], int)
    assert decoded[0]["n"] == -12
    assert decoded[0]["x"] == 1.25 and isinstance(decoded[0]["x"], float)
    assert decoded[0]["e"] == pytest.approx(1.2e-9, rel=1e-15)
    assert decoded[0]["s"] == "text"
    assert decoded[0]["blank"] == ""
    with pytest.raises(ValueError):
        parse_json("{not json}")
    with pytest.raises(ValueError):
        parse_json("")
    with pytest.raises(ValueError):
        parse_json("[1, 2]")


def test_rows_to_json_is_valid_json_and_utf8():
    rows = [{"name": "\u65e5\u672c\u8a9e", "value": 1.5}]
    text = rows_to_json(rows)
    assert "\u65e5\u672c\u8a9e" in text
    assert json.loads(text) == rows
    assert "\n" in text                                  # indent=2 by default
    assert json.loads(rows_to_json(rows, indent=None)) == rows


# ==========================================================================
# 8. Flight log export / import
# ==========================================================================

def test_flight_log_rows_use_the_contract_header():
    samples = [FlightLogSample(time_s=0.01, x_m=1.0, stalled=True)]
    rows = flight_log_to_rows(samples)
    assert set(rows[0]) == set(FLIGHT_LOG_COLUMNS)
    assert len(FLIGHT_LOG_COLUMNS) == 30
    text = rows_to_csv(rows)
    assert text.split("\r\n")[0].split(",") == list(FLIGHT_LOG_COLUMNS)
    assert parse_csv(text)[0]["stalled"] is True


def test_flight_log_to_rows_accepts_mappings_and_fills_defaults():
    rows = flight_log_to_rows([{"time_s": 0.5, "x_m": 2.0}, FlightLogSample(time_s=1.0)])
    assert len(rows) == 2
    for row in rows:
        assert set(row) == set(FLIGHT_LOG_COLUMNS)
        assert row["y_m"] == 0.0
        assert row["g_load"] == 1.0
        assert row["stalled"] is False
    assert rows[0]["time_s"] == 0.5 and rows[1]["time_s"] == 1.0
    assert flight_log_to_rows([]) == []
    with pytest.raises(TypeError):
        flight_log_to_rows([object()])


def test_sample_from_row_restores_the_dataclass_types():
    sample = sample_from_row(
        {
            "time_s": "0.5",
            "x_m": "-1.25",
            "reynolds": "1.2e5",
            "stalled": "true",
            "landed": "false",
            "crashed": "",
            "unknown_extra_column": "ignored",
        }
    )
    assert isinstance(sample, FlightLogSample)
    assert sample.time_s == pytest.approx(0.5)
    assert sample.x_m == pytest.approx(-1.25)
    assert sample.reynolds == pytest.approx(1.2e5)
    assert sample.stalled is True
    assert sample.landed is False
    assert sample.crashed is False          # empty cell -> dataclass default
    assert sample.max_stress_pa == 0.0      # missing column -> dataclass default
    with pytest.raises(ValueError, match="not a number"):
        sample_from_row({"time_s": "half a second"})


def test_export_flight_log_mimetypes_and_unknown_format():
    payload, mime, name = export_flight_log([FlightLogSample(time_s=0.25, x_m=3.5)], "csv")
    assert mime == "text/csv" and name.endswith(".csv")
    assert payload.startswith(",".join(FLIGHT_LOG_COLUMNS) + "\r\n")
    assert "0.25,3.5" in payload
    payload, mime, name = export_flight_log([FlightLogSample(time_s=0.25)], "JSON")
    assert mime == "application/json" and name.endswith(".json")
    assert json.loads(payload)[0]["time_s"] == 0.25
    empty = export_flight_log([], "csv")
    assert empty[0] == ",".join(FLIGHT_LOG_COLUMNS) + "\r\n"
    assert parse_csv(empty[0]) == []
    for bad in ("xml", "", "yaml", "parquet", None, 7, "  ", "text/csv"):
        with pytest.raises(ValueError):
            export_flight_log([], bad)
    # the format string is case/whitespace tolerant
    assert export_flight_log([], " CSV ")[1] == "text/csv"
    assert export_flight_log([], "Json")[1] == "application/json"


# ==========================================================================
# 9. Optimiser result export
# ==========================================================================

def _optimizer_result():
    return {
        "algorithm": "nsga2",
        "backend": "multiprocessing",
        "objectives": ["max_distance", "min_max_stress"],
        "design_variables": ["wingspan_m", "paper_gsm", "elevon_deg"],
        "best_vector": [0.22, 80.0, 10.0],
        "best_params": {"wingspan_m": 0.22, "paper_gsm": 80.0, "elevon_deg": 10.0},
        "best_objectives": {"max_distance": 12.5, "min_max_stress": 0.11},
        "best_score": 0.83,
        "pareto_front": [[0.2, 0.2], [0.22, 0.11]],
        "evaluations": 3,
        "history": [
            {
                "evaluation": 0,
                "score": 0.1,
                "objectives": {"max_distance": 8.0, "min_max_stress": 0.3},
                "params": {"wingspan_m": 0.18, "paper_gsm": 120.0, "elevon_deg": 5.0},
            },
            {
                "evaluation": 1,
                "score": 0.55,
                "objectives": {"max_distance": 11.0, "min_max_stress": 0.2},
                "params": {"wingspan_m": 0.2, "paper_gsm": 100.0, "elevon_deg": 7.5},
            },
            {
                "evaluation": 2,
                "score": 0.83,
                "objectives": {"max_distance": 12.5, "min_max_stress": 0.11},
                "params": {"wingspan_m": 0.22, "paper_gsm": 80.0, "elevon_deg": 10.0},
            },
        ],
    }


def test_export_optimizer_result_flattens_every_history_entry():
    payload, mime, name = export_optimizer_result(_optimizer_result(), "csv")
    assert mime == "text/csv" and name.endswith(".csv")
    expected_header = (
        "evaluation,score,max_distance,min_max_stress,wingspan_m,paper_gsm,elevon_deg"
    )
    assert payload.split("\r\n")[0] == expected_header
    rows = parse_csv(payload)
    assert len(rows) == 3
    assert rows[0]["max_distance"] == pytest.approx(8.0)
    assert rows[0]["min_max_stress"] == pytest.approx(0.3)
    assert rows[0]["paper_gsm"] == pytest.approx(120.0)
    assert rows[2]["score"] == pytest.approx(0.83)
    assert rows[2]["max_distance"] == pytest.approx(12.5)
    assert rows[2]["wingspan_m"] == pytest.approx(0.22)
    assert rows[2]["elevon_deg"] == pytest.approx(10.0)
    assert rows[2]["evaluation"] == 2
    assert optimizer_result_columns(_optimizer_result()) == expected_header.split(",")


def test_export_optimizer_result_supports_positional_vectors():
    positional = {
        "objectives": ["max_distance"],
        "design_variables": [],
        "best_vector": [0.25],
        "history": [
            {"score": 1.0, "objectives": [3.5], "params": [0.2]},
            {"score": 2.0, "objectives": [4.5], "params": [0.25]},
        ],
    }
    payload, _, _ = export_optimizer_result(positional, "csv")
    assert payload.split("\r\n")[0] == "evaluation,score,max_distance,var_0"
    rows = parse_csv(payload)
    assert rows[1]["max_distance"] == pytest.approx(4.5)
    assert rows[1]["var_0"] == pytest.approx(0.25)
    assert rows[1]["evaluation"] == 1        # falls back to the history index


def test_export_optimizer_result_handles_the_optimizer_domain_history_shape():
    # the history entries recorded by optimizer_domain carry only
    # evaluation / score / objectives -- the design-variable cells must then be
    # left empty instead of fabricating the best values.
    result = {
        "algorithm": "grid",
        "backend": "multiprocessing",
        "status": "completed",
        "objectives": ["max_distance", "min_max_stress"],
        "design_variables": ["wingspan_m", "paper_gsm", "elevon_deg"],
        "best_vector": [0.22, 80.0, 10.0],
        "best_params": {"wingspan_m": 0.22, "paper_gsm": 80.0, "elevon_deg": 10.0},
        "best_objectives": {"max_distance": 12.5, "min_max_stress": 0.11},
        "best_score": 0.0003206352154190295,
        "evaluations": 3,
        "history": [
            {
                "evaluation": 1,
                "score": 0.0003206352154190295,
                "objectives": {"max_distance": 0.23695026582753353, "min_max_stress": 0.009157375417682311},
            },
            {"evaluation": 2, "score": 0.0004, "objectives": {"max_distance": 0.3, "min_max_stress": 0.008}},
            {"evaluation": 3, "score": 0.0005, "objectives": {"max_distance": 0.4, "min_max_stress": 0.007}},
        ],
        "pareto_front": [
            {"objectives": {"max_distance": 12.5, "min_max_stress": 0.11}, "vector": [0.22, 80.0, 10.0]}
        ],
    }
    payload, _, _ = export_optimizer_result(result, "csv")
    header = payload.split("\r\n")[0]
    assert header == (
        "evaluation,score,max_distance,min_max_stress,wingspan_m,paper_gsm,elevon_deg"
    )
    rows = parse_csv(payload)
    assert len(rows) == 3
    # the explicit evaluation number wins over the row index
    assert [row["evaluation"] for row in rows] == [1, 2, 3]
    assert rows[0]["score"] == pytest.approx(0.0003206352154190295, rel=1e-15)
    assert rows[0]["max_distance"] == pytest.approx(0.23695026582753353, rel=1e-15)
    assert rows[2]["min_max_stress"] == pytest.approx(0.007)
    for row in rows:
        assert row["wingspan_m"] == "" and row["paper_gsm"] == ""
    assert objective_directions(result["objectives"]) == {
        "max_distance": OBJECTIVE_DIRECTIONS["max_distance"],
        "min_max_stress": OBJECTIVE_DIRECTIONS["min_max_stress"],
    }


def test_export_optimizer_result_with_empty_history_still_has_a_header():
    empty = {
        "algorithm": "grid",
        "objectives": ["max_distance", "min_max_stress"],
        "design_variables": ["wingspan_m"],
        "history": [],
    }
    payload, mime, _ = export_optimizer_result(empty, "csv")
    assert mime == "text/csv"
    assert payload == "evaluation,score,max_distance,min_max_stress,wingspan_m\r\n"
    assert parse_csv(payload) == []
    # a bare result (no history, no metadata) still exports a usable header
    assert export_optimizer_result({}, "csv")[0] == "evaluation,score\r\n"
    assert export_optimizer_result(None, "csv")[0] == "evaluation,score\r\n"


def test_export_optimizer_result_json_and_errors():
    payload, mime, name = export_optimizer_result(_optimizer_result(), "json")
    assert mime == "application/json" and name.endswith(".json")
    restored = parse_json(payload)
    assert restored["algorithm"] == "nsga2"
    assert restored["best_score"] == pytest.approx(0.83)
    assert restored["pareto_front"] == [[0.2, 0.2], [0.22, 0.11]]
    for bad in ("yaml", "", "parquet", None):
        with pytest.raises(ValueError):
            export_optimizer_result(_optimizer_result(), bad)


def test_objective_names_are_known_to_the_contract():
    assert set(_optimizer_result()["objectives"]) <= set(OBJECTIVE_DIRECTIONS)


# ==========================================================================
# 10. Configuration bundles
# ==========================================================================

def test_validate_config_bundle_accepts_and_rejects():
    bundle = default_config_bundle()
    validated = validate_config_bundle(bundle)
    assert set(validated) >= {"aircraft", "environment", "launch", "simulation"}
    for section in ("aircraft", "environment", "launch", "simulation"):
        assert validated[section] == bundle[section]
    # a partial bundle is completed from the contract defaults
    partial = validate_config_bundle({"aircraft": {"wingspan_m": 0.3}})
    assert partial["aircraft"]["wingspan_m"] == pytest.approx(0.3)
    assert partial["aircraft"]["name"] == AircraftConfig().name
    assert set(partial["simulation"]) == set(SimulationSettings().__dataclass_fields__)
    assert set(partial["launch"]) == set(LaunchConfig().__dataclass_fields__)
    assert set(partial["environment"]) == set(EnvironmentConfig().__dataclass_fields__)

    with pytest.raises(ValueError, match="must be a mapping"):
        validate_config_bundle({"aircraft": [1, 2]})
    with pytest.raises(ValueError, match="must be a mapping"):
        validate_config_bundle([1, 2, 3])
    with pytest.raises(ValueError, match="must be a mapping"):
        validate_config_bundle("nope")
    with pytest.raises(ValueError, match="unknown field"):
        validate_config_bundle({"aircraft": {"not_a_declared_field": 1.0}})
    with pytest.raises(ValueError, match="finite"):
        validate_config_bundle({"aircraft": {"wingspan_m": float("nan")}})
    with pytest.raises(ValueError, match="finite"):
        validate_config_bundle({"aircraft": {"wingspan_m": float("inf")}})
    with pytest.raises(ValueError, match="JSON compatible"):
        validate_config_bundle({"aircraft": {"wingspan_m": object()}})


def test_normalise_bundle_fills_missing_sections_only():
    partial = normalise_bundle({"aircraft": {"wingspan_m": 0.3}})
    assert partial["aircraft"] == {"wingspan_m": 0.3}
    assert "simulation" in partial and "environment" in partial
    assert partial["simulation"] == as_dict(SimulationSettings())
    # a present but invalid section is preserved so validate can report it
    broken = normalise_bundle({"aircraft": "oops"})
    assert broken["aircraft"] == "oops"
    assert "simulation" in broken
    # the caller's dict is never mutated
    source = {"aircraft": {"wingspan_m": 0.3}}
    normalise_bundle(source)
    assert source == {"aircraft": {"wingspan_m": 0.3}}
    with pytest.raises(ValueError):
        normalise_bundle(["not", "a", "bundle"])


def test_objective_catalogue_helpers():
    names = ["max_distance", "min_max_stress", "max_thrust"]
    assert unknown_objectives(names) == ["max_thrust"]
    assert objective_directions(names) == {
        "max_distance": 1,
        "min_max_stress": -1,
    }
    assert objective_directions("max_ld_ratio") == {"max_ld_ratio": 1}
    assert objective_directions(None) == {}
    assert unknown_design_variables(["wingspan_m", "warp_core"]) == ["warp_core"]
    assert unknown_design_variables([variable.name for variable in DESIGN_VARIABLE_CATALOGUE]) == []


def test_validate_config_bundle_checks_the_optional_optimizer_section():
    good = default_config_bundle()
    good["optimizer"] = {
        "objectives": ["max_distance", "min_max_stress"],
        "design_variables": ["wingspan_m", "paper_gsm"],
        "max_evaluations": 200,
    }
    validated = validate_config_bundle(good)
    assert validated["optimizer"]["objectives"] == ["max_distance", "min_max_stress"]
    with pytest.raises(ValueError, match="unknown objective"):
        validate_config_bundle(
            {"optimizer": {"objectives": ["max_distance", "max_thrust"]}}
        )
    with pytest.raises(ValueError, match="unknown design variable name"):
        validate_config_bundle(
            {"optimizer": {"design_variables": ["wingspan_m", "warp_core"]}}
        )
    with pytest.raises(ValueError, match="must be a list of names"):
        validate_config_bundle({"optimizer": {"objectives": "max_distance"}})
    # a section that is not a mapping is rejected like any other bad section
    with pytest.raises(ValueError, match="finite"):
        validate_config_bundle({"optimizer": {"max_evaluations": float("nan")}})


def test_export_config_formats():
    payload, mime, name = export_config(default_config_bundle(), "json")
    assert mime == "application/json" and name.endswith(".json")
    restored = parse_json(payload)
    assert set(restored) >= {"aircraft", "environment", "launch", "simulation"}
    assert restored["aircraft"]["wingspan_m"] == pytest.approx(0.2)
    payload, mime, name = export_config(default_config_bundle(), "csv")
    assert mime == "text/csv" and name.endswith(".csv")
    rows = parse_csv(payload)
    assert rows[0]["section"] == "aircraft"
    assert any(row["parameter"] == "wingspan_m" for row in rows)
    assert any(row["section"] == "simulation" for row in rows)
    field_count = (
        len(AircraftConfig.__dataclass_fields__)
        + len(EnvironmentConfig.__dataclass_fields__)
        + len(LaunchConfig.__dataclass_fields__)
        + len(SimulationSettings.__dataclass_fields__)
    )
    assert len(rows) == field_count                   # every field is exported
    with pytest.raises(ValueError):
        export_config(default_config_bundle(), "toml")
    with pytest.raises(ValueError):
        export_config({"aircraft": "nope"}, "json")
    assert isinstance(config_bundle_to_rows(default_config_bundle()), list)


# ==========================================================================
# 11. StorageManager
# ==========================================================================

def test_storage_config_round_trip(tmp_path):
    store = StorageManager(str(tmp_path / "runs"))
    assert os.path.isdir(store.root)
    bundle = default_config_bundle()
    bundle["aircraft"]["wingspan_m"] = 0.31
    bundle["aircraft"]["dihedral_deg"] = 7.5
    bundle["environment"]["wind_speed"] = 3.25
    path = store.save_config(bundle, name="preset-a")
    assert os.path.isfile(path)
    assert path.endswith("preset-a.config.json")
    expected = validate_config_bundle(bundle)
    loaded = store.load_config("preset-a")
    for section in ("aircraft", "environment", "launch", "simulation"):
        assert loaded[section] == expected[section]
    assert loaded["aircraft"]["wingspan_m"] == pytest.approx(0.31)
    assert loaded["aircraft"]["dihedral_deg"] == pytest.approx(7.5)
    assert loaded["environment"]["wind_speed"] == pytest.approx(3.25)

    configs = store.list_configs()
    assert [entry["name"] for entry in configs] == ["preset-a.config.json"]
    assert configs[0]["kind"] == "config"
    assert configs[0]["stem"] == "preset-a"
    assert configs[0]["size_bytes"] > 0
    assert configs[0]["modified_at"].endswith("Z")

    # an explicit suffix is accepted as well
    store.save_config(bundle, name="preset-a.config.json")
    assert store.load_config("preset-a.config.json") == loaded
    assert store.delete_config("preset-a") is True
    assert store.delete_config("preset-a") is False
    with pytest.raises(KeyError):
        store.load_config("preset-a")
    with pytest.raises(ValueError):
        store.save_config({"aircraft": [1, 2]}, name="broken")


def test_storage_rejects_path_traversal(tmp_path):
    store = StorageManager(str(tmp_path / "runs"))
    hostile = (
        "../evil",
        "..\\evil",
        "sub/dir",
        "sub\\dir",
        "C:\\Windows\\evil",
        "/etc/passwd",
        "",
        "   ",
        ".hidden",
        "CON",
        "nul.config.json",
        "name.",
        "with\x00null",
        "wild*card",
        "pipe|name",
    )
    for name in hostile:
        with pytest.raises(ValueError):
            store.save_config(default_config_bundle(), name=name)
        with pytest.raises(ValueError):
            sanitise_artefact_name(name)
    # nothing was written, and nothing escaped the root
    assert sorted(os.listdir(store.root)) == []
    assert not (tmp_path / "evil.config.json").exists()
    assert not (tmp_path.parent / "evil.config.json").exists()
    # untidy but harmless names are normalised rather than rejected
    assert sanitise_artefact_name("  my preset  ") == "my_preset"
    assert sanitise_artefact_name("\u65e5\u672c\u8a9e\u306e\u30d7\u30ec\u30bb\u30c3\u30c8") == (
        "\u65e5\u672c\u8a9e\u306e\u30d7\u30ec\u30bb\u30c3\u30c8"
    )
    assert len(sanitise_artefact_name("x" * 400)) == 96
    path = store.save_config(default_config_bundle(), name="  my preset  ")
    assert os.path.isfile(path)
    assert path.endswith("my_preset.config.json")
    assert store.load_config("my_preset")["aircraft"]["wingspan_m"] == pytest.approx(0.2)
    # a flight log name cannot traverse either
    with pytest.raises(ValueError):
        store.save_flight_log([FlightLogSample()], name="../escape")
    with pytest.raises(ValueError):
        store.save_optimizer_result({}, name="../../escape")


def test_storage_flight_log_round_trips_fifty_samples(tmp_path):
    store = StorageManager(str(tmp_path / "runs"))
    samples = []
    for index in range(50):
        samples.append(
            FlightLogSample(
                time_s=index * 0.01,
                x_m=math.sin(index * 0.37) * 12.5,
                y_m=math.cos(index * 0.21) * 0.75,
                z_m=-1.2 - index * 0.031,
                altitude_m=1.2 + index * 0.031,
                speed_mps=math.sqrt(max(144.0 - index * 0.7, 1.0)),
                vx_mps=11.9 - index * 0.01,
                vy_mps=0.13 * index,
                vz_mps=-0.27,
                alpha_deg=4.5 + index * 0.11,
                beta_deg=-0.8 + index * 0.03,
                roll_deg=1.2 * index,
                pitch_deg=-2.5 - index * 0.07,
                yaw_deg=0.9 * index,
                ld_ratio=5.5 + index * 0.02,
                g_load=1.0 + index * 0.01,
                max_stress_index=min(1.0, index / 60.0),
                max_stress_pa=41.0 * index,
                reynolds=1.0e5 + index * 17.0,
                mach=0.035 + index * 1e-4,
                dynamic_pressure_pa=72.0 + index * 0.5,
                stalled=index % 17 == 0,
                landed=index == 49,
                crashed=index == 50,
            )
        )
    path = store.save_flight_log(samples, name="run-01")
    assert os.path.isfile(path) and path.endswith("run-01.csv")
    restored = store.load_flight_log("run-01")
    assert len(restored) == 50
    for original, copy in zip(samples, restored):
        assert type(copy) is FlightLogSample
        for column in FLIGHT_LOG_COLUMNS:
            left = getattr(original, column)
            right = getattr(copy, column)
            if isinstance(left, bool):
                assert right is left, column
            elif isinstance(left, str):
                assert right == left, column
            else:
                assert float(right) == pytest.approx(float(left), abs=1e-9, rel=0.0), column
    with open(path, "r", encoding="utf-8", newline="") as handle:
        text = handle.read()
    assert text.split("\r\n")[0].split(",") == list(FLIGHT_LOG_COLUMNS)
    assert len(text.split("\r\n")) == 52          # header + 50 rows + trailing empty
    assert text.count("\r\n") == 51
    # an empty log round-trips to an empty list
    empty_path = store.save_flight_log([], name="empty")
    assert os.path.isfile(empty_path)
    assert store.load_flight_log("empty") == []
    with pytest.raises(KeyError):
        store.load_flight_log("missing-log")


def test_storage_optimizer_result_round_trip(tmp_path):
    store = StorageManager(str(tmp_path / "runs"))
    result = _optimizer_result()
    path = store.save_optimizer_result(result, name="nsga2-run")
    assert os.path.isfile(path) and path.endswith("nsga2-run.result.json")
    loaded = store.load_optimizer_result("nsga2-run")
    assert loaded["algorithm"] == "nsga2"
    assert loaded["best_score"] == pytest.approx(0.83)
    assert loaded["history"] == result["history"]
    assert loaded["pareto_front"] == [[0.2, 0.2], [0.22, 0.11]]
    assert loaded["best_params"] == result["best_params"]
    entries = store.list_artefacts()
    assert [entry["name"] for entry in entries] == ["nsga2-run.result.json"]
    assert entries[0]["kind"] == "optimizer_result"
    assert entries[0]["size_bytes"] > 0
    assert store.delete("nsga2-run") is True
    assert store.delete("nsga2-run") is False
    with pytest.raises(KeyError):
        store.load_optimizer_result("nsga2-run")
    with pytest.raises(ValueError):
        store.save_optimizer_result(["not", "a", "mapping"], name="bad")
    # an empty result is still persisted
    store.save_optimizer_result({}, name="empty-result")
    assert store.load_optimizer_result("empty-result") == {}


def test_storage_lists_every_artefact_kind(tmp_path):
    store = StorageManager(str(tmp_path / "runs"))
    store.save_config(default_config_bundle(), name="preset")
    store.save_flight_log([FlightLogSample(time_s=0.0)], name="flight")
    store.save_optimizer_result(_optimizer_result(), name="opt")
    kinds = sorted(entry["kind"] for entry in store.list_artefacts())
    assert kinds == ["config", "flight_log", "optimizer_result"]
    assert [entry["stem"] for entry in store.list_configs()] == ["preset"]
    artefacts = store.list_artefacts()
    assert len(artefacts) == 3
    assert all(entry["modified_at"].endswith("Z") for entry in artefacts)
    assert all(os.path.isfile(entry["path"]) for entry in artefacts)
    with pytest.raises(KeyError):
        store.load_optimizer_result("preset")     # right stem, wrong kind


def test_storage_is_thread_safe(tmp_path):
    store = StorageManager(str(tmp_path / "runs"))
    failures = []

    def worker(index: int) -> None:
        try:
            for step in range(10):
                store.save_flight_log(
                    [FlightLogSample(time_s=index * 10 + step)], name=f"flight-{index}-{step}"
                )
                store.list_artefacts()
                store.save_config(default_config_bundle(), name=f"preset-{index}")
        except Exception as exc:  # pragma: no cover - failure path
            failures.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60.0)
        assert not thread.is_alive()
    assert not failures
    assert len(store.list_artefacts()) == 60 + 6
    assert len(store.list_configs()) == 6
    assert store.load_flight_log("flight-3-7")[0].time_s == pytest.approx(37.0)
