"""Job manager -- ETA management for the non-blocking optimiser jobs.

Implements requirement D of prompt.md 3.4 / AGENTS.md 8.5:

* every optimiser / mesh / simulation batch job is represented by a
  :class:`physics.config_schema.JobRecord` (the contract dataclass -- it is
  populated here, never redefined);
* the *estimated time of arrival* uses exactly the closed form demanded by the
  specification::

      t_bar              = elapsed_seconds / max(evaluated_count, 1)
      eta_seconds        = (total_evaluations - evaluated_count) * t_bar
      evals_per_second   = 1 / t_bar                     (0 when t_bar <= 0)
      estimated_completion = now + eta_seconds

* ``evals_per_second`` is smoothed with an exponential moving average
  (``ETA_ALPHA = 0.3``) so that the huge over-estimate produced by the very
  first evaluation cannot be shown to the user;
* the ETA triplet is recomputed at most every ``ETA_UPDATE_INTERVAL = 0.5``
  seconds while ``evaluated_count`` / ``progress`` / ``updated_at`` are
  refreshed on *every* ``update()`` call;
* elapsed time is measured with ``time.monotonic()`` (never the wall clock) so
  an NTP step cannot make ``eta_seconds`` negative;
* ``POST /api/v1/optimizer/run`` is served by :meth:`JobManager.run_tracked`
  which returns the ``job_id`` **immediately** while the callable runs on a
  daemon thread.

Thread safety: every public method takes ``self._lock`` (an ``RLock``).
``get`` / ``find`` / ``list_jobs`` / ``jobs`` / ``snapshot`` / ``to_dict``
return *copies*, so a caller can never corrupt the manager's internal state;
``is_cancel_requested`` / ``is_cancelled`` deliberately read the live internal
flags.

Path-traversal / security are out of scope here; see
:mod:`physics.storage_domain` for the artefact naming policy.
"""

from __future__ import annotations

import contextlib
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from .config_schema import JobRecord, as_dict, from_dict, make_uuid, utc_now_iso

__all__ = [
    "ETA_ALPHA",
    "ETA_UPDATE_INTERVAL",
    "JOB_STATUSES",
    "ACTIVE_STATUSES",
    "TERMINAL_STATUSES",
    "JobCancelled",
    "mean_eval_time",
    "remaining_evaluations",
    "estimate_remaining",
    "eta_seconds",
    "eta_from_rate",
    "evals_per_second",
    "ema_recurrence",
    "ema_update",
    "estimated_completion_iso",
    "parse_iso8601",
    "format_duration",
    "clamp_progress",
    "JobManager",
    "TrackedJob",
    "JOB_MANAGER",
]

#: Exponential moving average weight for ``evals_per_second`` (prompt.md 3.4).
ETA_ALPHA: float = 0.3

#: Minimum wall-clock spacing between two ETA recomputations, in seconds.
ETA_UPDATE_INTERVAL: float = 0.5

#: The complete status vocabulary (AGENTS.md 8.5).
JOB_STATUSES = ("queued", "running", "completed", "failed", "cancelled")

#: Statuses that still occupy a worker / progress slot.
ACTIVE_STATUSES = ("queued", "running")

#: Statuses that can no longer change.
TERMINAL_STATUSES = ("completed", "failed", "cancelled")


class JobCancelled(Exception):
    """Raised inside a tracked callable once a cancel has been requested.

    :meth:`JobManager.run_tracked` raises it from the ``progress_cb`` it hands
    to the wrapped callable, which gives the optimiser a clean cooperative
    stop point (and lets the caller of :meth:`TrackedJob.result` observe the
    cancellation instead of a real result).
    """


# --------------------------------------------------------------------------
# ETA mathematics (pure functions -- directly testable, no state)
# --------------------------------------------------------------------------
def mean_eval_time(elapsed_seconds: float, evaluated_count: int) -> float:
    """``t_bar = elapsed_seconds / max(evaluated_count, 1)``.

    The ``max(..., 1)`` denominator is what makes the very first update (zero
    evaluations so far) well defined; a negative ``elapsed_seconds`` (clock
    going backwards is impossible with :func:`time.monotonic`, but the pure
    function is public) is clamped to zero so the result is never negative.
    """
    denominator = max(int(evaluated_count), 1)
    return max(float(elapsed_seconds), 0.0) / denominator


def remaining_evaluations(evaluated_count: int, total_evaluations: int) -> int:
    """Number of evaluations still outstanding, never negative."""
    return max(int(total_evaluations) - int(evaluated_count), 0)


def estimate_remaining(
    elapsed_seconds: float, evaluated_count: int, total_evaluations: int
) -> float:
    """``eta = (total - evaluated) * t_bar`` -- the canonical ETA formula."""
    outstanding = remaining_evaluations(evaluated_count, total_evaluations)
    return float(outstanding) * mean_eval_time(elapsed_seconds, evaluated_count)


def eta_seconds(
    elapsed_seconds: float, evaluated_count: int, total_evaluations: int
) -> float:
    """``(total_evaluations - evaluated_count) * mean_eval_time(...)``.

    Clamps at ``0.0`` when the job over-shot its budget (``evaluated > total``)
    or when ``total == evaluated``; an unknown budget (``total == 0``) also
    yields ``0.0`` because nothing is outstanding.
    """
    return estimate_remaining(elapsed_seconds, evaluated_count, total_evaluations)


def evals_per_second(elapsed_seconds: float, evaluated_count: int) -> float:
    """Raw (unsmoothed) throughput ``1 / t_bar``; ``0.0`` when ``t_bar <= 0``."""
    t_bar = mean_eval_time(elapsed_seconds, evaluated_count)
    if t_bar <= 0.0:
        return 0.0
    return 1.0 / t_bar


def eta_from_rate(
    evaluated_count: int, total_evaluations: int, evals_per_second_ema: float
) -> float:
    """ETA derived from the **EMA-smoothed** throughput.

    ``eta = outstanding / smoothed_rate``.  Smoothing the rate (instead of the
    remaining time) is what keeps the estimate stable while the job is still
    warming up: the raw closed form ``(total - n) * elapsed / n`` is dominated
    by the first, very slow evaluation and therefore *over*-estimates the
    remaining time, sometimes by a large factor.

    Returns ``0.0`` when nothing is outstanding or when no throughput has been
    measured yet.
    """
    outstanding = remaining_evaluations(evaluated_count, total_evaluations)
    rate = float(evals_per_second_ema)
    if outstanding <= 0 or rate <= 0.0:
        return 0.0
    return float(outstanding) / rate


def ema_recurrence(previous: float, sample: float, alpha: float = ETA_ALPHA) -> float:
    """Textbook EMA step ``alpha * sample + (1 - alpha) * previous``."""
    weight = float(alpha)
    return weight * float(sample) + (1.0 - weight) * float(previous)


def ema_update(previous: float, sample: float, alpha: float = ETA_ALPHA) -> float:
    """EMA accumulator update **with first-sample seeding**.

    ``previous == 0`` means "no history yet", so the very first measurement is
    adopted verbatim (``alpha * sample`` would throw away 70 % of it and hide
    the start-up transient).  Every later step follows
    :func:`ema_recurrence`, i.e. ``alpha = 0.3`` by default::

        ema_n = 0.3 * sample_n + 0.7 * ema_{n-1}
    """
    previous_value = float(previous)
    sample_value = float(sample)
    if previous_value == 0.0:
        return sample_value
    return ema_recurrence(previous_value, sample_value, alpha)


def _iso_from_datetime(moment: datetime) -> str:
    """Format a datetime exactly like :func:`config_schema.utc_now_iso`."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    stamp = moment.astimezone(timezone.utc).replace(microsecond=0)
    return stamp.isoformat().replace("+00:00", "Z")


def estimated_completion_iso(eta_seconds_value: float, now: Any = None) -> str:
    """``now + eta_seconds`` rendered as an ISO-8601 UTC timestamp.

    ``now`` may be ``None`` (current UTC time), a :class:`datetime.datetime`
    (naive values are interpreted as UTC) or a POSIX timestamp in seconds.
    The output has one-second resolution, exactly like
    :func:`config_schema.utc_now_iso`, so the UI can slice ``[11:19]`` for the
    mandated ``HH:MM:SS`` widget.
    """
    delta = float(eta_seconds_value or 0.0)
    if not math.isfinite(delta) or delta < 0.0:
        delta = 0.0
    if now is None:
        base = datetime.now(timezone.utc)
    elif isinstance(now, datetime):
        base = now
    elif isinstance(now, (int, float)) and not isinstance(now, bool):
        base = datetime.fromtimestamp(float(now), tz=timezone.utc)
    else:
        raise TypeError(f"unsupported 'now' type for estimated_completion_iso: {type(now)!r}")
    return _iso_from_datetime(base + timedelta(seconds=delta))


def parse_iso8601(text: str) -> datetime:
    """Parse a timestamp produced by this module (or by ``utc_now_iso``)."""
    raw = str(text).strip()
    if not raw:
        raise ValueError("empty ISO-8601 timestamp")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    moment = datetime.fromisoformat(raw)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def format_duration(seconds: float) -> str:
    """Render a duration as ``mm:ss`` (below one hour) or ``HH:MM:SS``.

    Negative values clamp to ``"00:00"`` and the value is truncated (never
    rounded up) so a countdown never promises time it cannot deliver.
    """
    total = int(max(float(seconds or 0.0), 0.0))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def clamp_progress(value: float) -> float:
    """Clamp a progress ratio into ``[0, 1]``; NaN maps to ``0.0``."""
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(ratio):
        return 0.0
    if ratio < 0.0:
        return 0.0
    if ratio > 1.0:
        return 1.0
    return ratio


def _copy_record(record: JobRecord) -> JobRecord:
    """Deep copy of a :class:`JobRecord` (lists are not shared with the manager)."""
    return from_dict(JobRecord, as_dict(record))


# --------------------------------------------------------------------------
# Internal per-job bookkeeping (never exposed; guarded by the manager lock)
# --------------------------------------------------------------------------
@dataclass
class _JobState:
    record: JobRecord
    cancel_requested: bool = False
    stop_requested: bool = False
    started_monotonic: float = 0.0
    started: bool = False
    last_eta_monotonic: float = 0.0
    evals_per_second_ema: float = 0.0
    thread: Optional["TrackedJob"] = None
    value: Any = None
    exception: Optional[BaseException] = None
    done: threading.Event = field(default_factory=threading.Event)


class TrackedJob(threading.Thread):
    """Daemon thread that drives one :meth:`JobManager.run_tracked` callable.

    It *is* a :class:`threading.Thread` (so ``is_alive()`` and
    ``threading.enumerate()`` behave normally) and additionally exposes the
    value/exception of the wrapped callable once it has terminated::

        job_id, runner = JOB_MANAGER.run_tracked(fn, "Differential Evolution", ...)
        runner.result(timeout=30.0)      # value, or re-raises the exception
        runner.result_or_exception(30.0) # value, or the Exception instance
    """

    def __init__(
        self,
        manager: "JobManager",
        job_id: str,
        fn: Callable[[Callable[..., Any]], Any],
        on_update: Optional[Callable[[JobRecord], Any]] = None,
    ) -> None:
        super().__init__(name=f"job-{job_id}", daemon=True)
        self._manager = manager
        self._job_id = job_id
        self._fn = fn
        self._on_update = on_update
        self._state: Optional[_JobState] = manager._state_for(job_id)

    @property
    def done(self) -> threading.Event:
        """Set once the wrapped callable has terminated (value stored)."""
        return self._state.done if self._state is not None else threading.Event()

    # -- thread body -------------------------------------------------------
    def run(self) -> None:
        manager = self._manager
        job_id = self._job_id
        try:
            manager.start(job_id)
            result = self._fn(self._make_progress_cb())
            if self._state is not None and (
                self._state.stop_requested or self._state.cancel_requested
            ):
                raise JobCancelled(f"job {job_id} cancelled")
            manager.finish(job_id)
            self._store(result, None)
        except JobCancelled as exc:
            manager.cancel(job_id)
            self._store(None, exc)
        except BaseException as exc:  # noqa: BLE001 - recorded on the job record
            manager.fail(job_id, f"{type(exc).__name__}: {exc}")
            self._store(None, exc)

    def _make_progress_cb(self) -> Callable[..., Any]:
        manager = self._manager
        job_id = self._job_id
        callback = self._on_update
        state = self._state

        def progress_cb(
            evaluated_count: int,
            total_evaluations: Optional[int] = None,
            backend: Optional[str] = None,
            message: str = "",
        ) -> bool:
            """Report progress from inside the wrapped callable.

            Returns ``True`` (keep running); the ``progress_cb.stop`` variant
            returns ``False`` to request a cooperative stop, which terminates
            the job with the ``cancelled`` status.  Raises
            :class:`JobCancelled` immediately if a cancel was requested through
            :meth:`JobManager.request_cancel`.
            """
            if state is not None and state.cancel_requested:
                raise JobCancelled(f"job {job_id} cancel requested")
            record = manager.update(
                job_id, evaluated_count, backend=backend, total_evaluations=total_evaluations
            )
            if message:
                with manager._lock:
                    live = manager._state_for(job_id)
                    if live is not None:
                        live.record.message = str(message)
                        manager._touch(live)
            if callback is not None:
                with contextlib.suppress(Exception):
                    callback(record)  # observers must never kill a job
            return True

        def stop_cb(
            evaluated_count: int,
            total_evaluations: Optional[int] = None,
            backend: Optional[str] = None,
            message: str = "",
            keep_going: bool = True,
        ) -> bool:
            """Report progress and return ``keep_going``.

            Passing ``keep_going=False`` requests a cooperative stop: the
            callable should return, and the job terminates as ``cancelled``.
            """
            progress_cb(
                evaluated_count,
                total_evaluations=total_evaluations,
                backend=backend,
                message=message,
            )
            if state is not None:
                state.stop_requested = not bool(keep_going)
            return bool(keep_going)

        progress_cb.stop = stop_cb  # type: ignore[attr-defined]
        return progress_cb

    def _store(self, value: Any, exception: Optional[BaseException]) -> None:
        if self._state is None:  # pragma: no cover - job removed mid flight
            return
        self._state.value = value
        self._state.exception = exception
        self._state.done.set()

    # -- result accessors --------------------------------------------------
    def result(self, timeout: Optional[float] = None) -> Any:
        """Join, then return the wrapped callable's value or re-raise its error."""
        if not self.done.wait(timeout):
            raise TimeoutError(f"job {self._job_id} did not finish within {timeout}s")
        state = self._state
        if state is not None and state.exception is not None:
            raise state.exception
        return state.value if state is not None else None

    def result_or_exception(self, timeout: Optional[float] = None) -> Any:
        """Like :meth:`result` but returns the exception instance instead of raising."""
        if not self.done.wait(timeout):
            raise TimeoutError(f"job {self._job_id} did not finish within {timeout}s")
        state = self._state
        if state is None:
            return None
        return state.exception if state.exception is not None else state.value

    def exception(self, timeout: Optional[float] = None) -> Optional[BaseException]:
        """The recorded exception (``None`` on success)."""
        if not self.done.wait(timeout):
            raise TimeoutError(f"job {self._job_id} did not finish within {timeout}s")
        return self._state.exception if self._state is not None else None

    def join(self, timeout: Optional[float] = None) -> bool:  # type: ignore[override]
        """Like :meth:`threading.Thread.join` but returns ``True`` if it finished."""
        super().join(timeout)
        return not self.is_alive()


class JobManager:
    """Thread-safe registry of every background job with live ETA bookkeeping."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: Dict[str, _JobState] = {}
        self._max_retained_jobs: int = 256

    # -- internal helpers (call with the lock held) ------------------------
    def _state_for(self, job_id: str) -> Optional[_JobState]:
        return self._jobs.get(str(job_id))

    def _require_state(self, job_id: str) -> _JobState:
        state = self._jobs.get(str(job_id))
        if state is None:
            raise KeyError(f"unknown job_id '{job_id}'")
        return state

    def _touch(self, state: _JobState) -> JobRecord:
        record = state.record
        record.updated_at = utc_now_iso()
        return record

    def _elapsed_live(self, state: _JobState) -> float:
        if not state.started:
            return 0.0
        return max(time.monotonic() - state.started_monotonic, 0.0)

    def _refresh_eta(self, state: _JobState) -> None:
        """Recompute elapsed / ETA / throughput, honouring the 0.5 s throttle.

        ``elapsed_seconds`` is refreshed on every call (it is the live clock
        reading); the ETA triplet is only recomputed once
        :data:`ETA_UPDATE_INTERVAL` has passed since the previous
        recomputation, which is what keeps a fast optimiser from rewriting the
        ETA on every single evaluation.
        """
        record = state.record
        if not state.started:
            return
        moment = time.monotonic()
        elapsed = max(moment - state.started_monotonic, 0.0)
        record.elapsed_seconds = elapsed
        if (moment - state.last_eta_monotonic) < ETA_UPDATE_INTERVAL:
            return
        state.last_eta_monotonic = moment
        evaluated = max(record.evaluated_count, 0)
        raw_rate = evals_per_second(elapsed, evaluated)
        state.evals_per_second_ema = ema_update(state.evals_per_second_ema, raw_rate, ETA_ALPHA)
        record.evals_per_second = state.evals_per_second_ema
        # The ETA is derived from the smoothed rate, so the estimate does not
        # jump upwards when the first (slow) evaluation inflates the raw
        # mean evaluation time.
        record.eta_seconds = eta_from_rate(
            evaluated, record.total_evaluations, state.evals_per_second_ema
        )
        record.estimated_completion = estimated_completion_iso(record.eta_seconds)

    def _finalise(
        self,
        state: _JobState,
        status: str,
        message: str = "",
        result_path: str = "",
    ) -> JobRecord:
        record = state.record
        stamp = utc_now_iso()
        record.status = status
        record.finished_at = stamp
        record.updated_at = stamp
        record.elapsed_seconds = self._elapsed_live(state)
        evaluated = max(record.evaluated_count, 0)
        if record.elapsed_seconds > 0.0 and evaluated > 0:
            # Achieved (not predicted) throughput -- useful in the job list.
            record.evals_per_second = float(evaluated) / record.elapsed_seconds
        record.eta_seconds = 0.0
        record.estimated_completion = stamp
        if status == "completed":
            record.progress = 1.0
        if message:
            record.message = message
        if result_path:
            record.result_path = result_path
        if state.thread is None:
            # Manually driven job (no TrackedJob owns the completion event).
            state.value = None
            state.done.set()
        return record

    # -- lifecycle ---------------------------------------------------------
    def create(
        self,
        name: str = "optimizer",
        total_evaluations: int = 0,
        backend: str = "serial",
        objectives: Any = None,
        algorithm: str = "",
        model_id: str = "",
    ) -> JobRecord:
        """Register a new ``queued`` job and return a copy of the record."""
        with self._lock:
            objective_names: List[str]
            if objectives is None:
                objective_names = []
            elif isinstance(objectives, dict):
                objective_names = [str(key) for key in objectives]
            elif isinstance(objectives, (list, tuple, set)):
                objective_names = [str(item) for item in objectives]
            else:
                objective_names = [str(objectives)]
            record = JobRecord(
                job_id=make_uuid(),
                name=str(name or "optimizer"),
                algorithm=str(algorithm or ""),
                status="queued",
                progress=0.0,
                evaluated_count=0,
                total_evaluations=max(int(total_evaluations or 0), 0),
                started_at="",
                updated_at=utc_now_iso(),
                finished_at="",
                eta_seconds=0.0,
                estimated_completion="",
                backend=str(backend or "serial"),
                elapsed_seconds=0.0,
                evals_per_second=0.0,
                message="",
                objectives=objective_names,
                model_id=str(model_id or ""),
                result_path="",
            )
            # Evict oldest terminal jobs if capacity exceeded
            if len(self._jobs) >= self._max_retained_jobs:
                terminal_ids = [
                    jid for jid, st in self._jobs.items() if st.record.status in TERMINAL_STATUSES
                ]
                to_remove = max(0, len(terminal_ids) - self._max_retained_jobs // 2)
                for old_id in terminal_ids[:to_remove]:
                    self._jobs.pop(old_id, None)
            self._jobs[record.job_id] = _JobState(record=record)
            return _copy_record(record)

    def start(self, job_id: str) -> JobRecord:
        """``queued -> running``; stamps ``started_at`` and ``updated_at``.

        Idempotent: starting an already running job (or a terminal one) returns
        the record unchanged.  The monotonic ETA clock is armed here, so the
        first ``update()`` within :data:`ETA_UPDATE_INTERVAL` seconds is
        deliberately throttled.
        """
        with self._lock:
            state = self._require_state(job_id)
            record = state.record
            if record.status != "queued":
                return _copy_record(record)
            stamp = utc_now_iso()
            state.started_monotonic = time.monotonic()
            state.started = True
            state.last_eta_monotonic = state.started_monotonic
            state.evals_per_second_ema = 0.0
            record.status = "running"
            record.started_at = stamp
            record.updated_at = stamp
            record.message = ""
            return _copy_record(record)

    def update(
        self,
        job_id: str,
        evaluated_count: int,
        backend: Optional[str] = None,
        total_evaluations: Optional[int] = None,
        message: Optional[str] = None,
    ) -> JobRecord:
        """Record progress for ``job_id`` (absolute ``evaluated_count``).

        ``evaluated_count``, ``progress`` and ``updated_at`` are written on
        every call; ``eta_seconds`` / ``evals_per_second`` /
        ``estimated_completion`` are only recomputed once
        :data:`ETA_UPDATE_INTERVAL` has elapsed (see :meth:`_refresh_eta`).
        Jobs in a terminal status are left untouched, so a straggling worker
        cannot resurrect a finished job.
        """
        with self._lock:
            state = self._require_state(job_id)
            record = state.record
            if record.status in TERMINAL_STATUSES:
                return _copy_record(record)
            new_count = max(int(evaluated_count), 0)
            record.evaluated_count = max(record.evaluated_count, new_count)
            if backend is not None:
                record.backend = str(backend)
            if total_evaluations is not None:
                record.total_evaluations = max(int(total_evaluations), 0)
            if message is not None:
                record.message = str(message)
            record.progress = clamp_progress(
                float(record.evaluated_count) / max(record.total_evaluations, 1)
            )
            self._touch(state)
            self._refresh_eta(state)
            return _copy_record(record)

    def finish(
        self, job_id: str, message: str = "", result_path: str = ""
    ) -> JobRecord:
        """``-> completed`` with ``progress == 1.0``, ``eta_seconds == 0``.

        A job that was cancelled stays cancelled (and a finished/failed job is
        left alone), which guarantees the terminal status is written exactly
        once.
        """
        with self._lock:
            state = self._require_state(job_id)
            record = state.record
            if record.status in TERMINAL_STATUSES:
                return _copy_record(record)
            return _copy_record(self._finalise(state, "completed", message, result_path))

    def fail(self, job_id: str, message: str) -> JobRecord:
        """``-> failed`` with the diagnostic ``message`` (progress frozen)."""
        with self._lock:
            state = self._require_state(job_id)
            record = state.record
            if record.status in TERMINAL_STATUSES:
                return _copy_record(record)
            if state.cancel_requested:
                return _copy_record(self._finalise(state, "cancelled", "cancelled by request"))
            return _copy_record(self._finalise(state, "failed", str(message)))

    def cancel(self, job_id: str) -> JobRecord:
        """``-> cancelled`` for a queued/running job (progress frozen).

        Also raises the cooperative ``cancel_requested`` flag so the worker
        thread notices through :meth:`is_cancel_requested`.  Cancelling a
        terminal job is a no-op that returns the record unchanged.
        """
        with self._lock:
            state = self._require_state(job_id)
            record = state.record
            state.cancel_requested = True
            if record.status in TERMINAL_STATUSES:
                return _copy_record(record)
            message = record.message or "cancelled by request"
            return _copy_record(self._finalise(state, "cancelled", message))

    def register_thread(self, job_id: str, thread: Any) -> None:
        """Associate a running thread or TrackedJob runner with the job state."""
        with self._lock:
            state = self._require_state(job_id)
            state.thread = thread

    # -- cooperative cancellation -----------------------------------------
    def request_cancel(self, job_id: str) -> bool:
        """Raise the cooperative stop flag; ``True`` when newly requested.

        Raises :class:`KeyError` for an unknown job (so the REST layer can turn
        it into a 404) and returns ``False`` when the flag was already set,
        which makes the endpoint idempotent under repeated clicks.
        """
        with self._lock:
            state = self._require_state(job_id)
            if state.cancel_requested:
                return False
            state.cancel_requested = True
            return True

    def is_cancel_requested(self, job_id: str) -> bool:
        """Live internal stop flag polled by the worker (not a copy)."""
        with self._lock:
            return self._require_state(job_id).cancel_requested

    def is_cancelled(self, job_id: str) -> bool:
        """``True`` once the record's status is ``cancelled``."""
        with self._lock:
            return self._require_state(job_id).record.status == "cancelled"

    # -- reads -------------------------------------------------------------
    def get(self, job_id: str) -> JobRecord:
        """Copy of the record; :class:`KeyError` when the id is unknown."""
        with self._lock:
            return _copy_record(self._require_state(job_id).record)

    def find(self, job_id: str) -> Optional[JobRecord]:
        """Copy of the record, or ``None`` when the id is unknown."""
        with self._lock:
            state = self._jobs.get(str(job_id))
            return None if state is None else _copy_record(state.record)

    def list_jobs(self, status: Optional[str] = None) -> List[JobRecord]:
        """Copies of every job in creation order, optionally filtered."""
        with self._lock:
            states = list(self._jobs.values())
            if status is not None:
                wanted = str(status)
                states = [state for state in states if state.record.status == wanted]
            return [_copy_record(state.record) for state in states]

    @property
    def jobs(self) -> Dict[str, JobRecord]:
        """``{job_id: record-copy}`` snapshot -- mutating it is harmless."""
        with self._lock:
            return {
                job_id: _copy_record(state.record) for job_id, state in self._jobs.items()
            }

    def active_count(self) -> int:
        """Number of jobs that are still ``queued`` or ``running``."""
        with self._lock:
            return sum(
                1
                for state in self._jobs.values()
                if state.record.status in ACTIVE_STATUSES
            )

    def to_dict(self, job_id: str) -> Dict[str, Any]:
        """Plain JSON-serialisable dict of the record with live ETA fields."""
        with self._lock:
            state = self._require_state(job_id)
            record = state.record
            data = as_dict(_copy_record(record))
            if state.started and record.status not in TERMINAL_STATUSES:
                elapsed = self._elapsed_live(state)
                data["elapsed_seconds"] = elapsed
                # The stored ETA is the EMA-smoothed estimate, refreshed at most
                # every :data:`ETA_UPDATE_INTERVAL` seconds by
                # :meth:`_refresh_eta`.  Recomputing the raw closed form on
                # every read would bypass the smoothing entirely.
                eta = float(record.eta_seconds)
                data["eta_seconds"] = eta
                data["estimated_completion"] = estimated_completion_iso(eta)
            return data

    def snapshot(self, job_id: str) -> Dict[str, Any]:
        """:meth:`to_dict` plus the live/derived fields the UI widget needs."""
        with self._lock:
            state = self._require_state(job_id)
            record = state.record
            data = as_dict(_copy_record(record))
            active = record.status not in TERMINAL_STATUSES
            elapsed = self._elapsed_live(state) if (state.started and active) else record.elapsed_seconds
            evaluated = max(record.evaluated_count, 0)
            if state.started and active:
                # EMA-smoothed estimate, refreshed at most every
                # :data:`ETA_UPDATE_INTERVAL` seconds (see :meth:`_refresh_eta`).
                eta = float(record.eta_seconds)
                completion = estimated_completion_iso(eta)
            else:
                eta = float(record.eta_seconds)
                completion = str(record.estimated_completion or record.finished_at)
            data["elapsed_seconds"] = elapsed
            data["eta_seconds"] = eta
            data["estimated_completion"] = completion
            data["progress"] = clamp_progress(record.progress)
            data["remaining_evaluations"] = remaining_evaluations(
                evaluated, record.total_evaluations
            )
            data["eta_display"] = format_duration(eta)
            data["elapsed_display"] = format_duration(elapsed)
            data["completion_clock"] = completion[11:19] if len(completion) >= 19 else ""
            data["cancel_requested"] = state.cancel_requested
            data["active"] = record.status in ACTIVE_STATUSES
            data["status"] = record.status
            return data

    # -- removal -----------------------------------------------------------
    def remove(self, job_id: str) -> bool:
        """Forget a job; ``False`` when the id is unknown."""
        with self._lock:
            return self._jobs.pop(str(job_id), None) is not None

    def clear(self) -> None:
        """Forget every job (running worker threads are *not* interrupted)."""
        with self._lock:
            self._jobs.clear()

    # -- non blocking driver ----------------------------------------------
    def run_tracked(
        self,
        fn: Callable[[Callable[..., Any]], Any],
        name: str = "optimizer",
        total_evaluations: int = 0,
        backend: str = "serial",
        objectives: Any = None,
        algorithm: str = "",
        model_id: str = "",
        on_update: Optional[Callable[[JobRecord], Any]] = None,
    ):
        """Start ``fn(progress_cb)`` on a daemon thread and return immediately.

        Returns ``(job_id, runner)`` where ``runner`` is a :class:`TrackedJob`
        (a daemon :class:`threading.Thread` with ``.result()``,
        ``.result_or_exception()``, ``.exception()`` and a boolean ``.join()``)
        -- this is the non-blocking ``POST /api/v1/optimizer/run`` path.

        Exceptions raised by ``fn`` are recorded on the job
        (``status == "failed"``) and re-raised by ``runner.result()``; a
        requested cancel ends the job as ``cancelled``.  ``on_update`` is an
        optional observer invoked with a fresh record copy after every
        progress report; an exception raised by the observer is swallowed so it
        can never kill the job.
        """
        record = self.create(
            name=name,
            total_evaluations=total_evaluations,
            backend=backend,
            objectives=objectives,
            algorithm=algorithm,
            model_id=model_id,
        )
        job_id = record.job_id
        runner = TrackedJob(self, job_id, fn, on_update=on_update)
        with self._lock:
            state = self._require_state(job_id)
            state.thread = runner
        runner.start()
        return job_id, runner

    def tracked(self, job_id: str) -> Optional[TrackedJob]:
        """The :class:`TrackedJob` driving ``job_id``, or ``None``."""
        with self._lock:
            state = self._jobs.get(str(job_id))
            return None if state is None else state.thread

    def join(self, job_id: str, timeout: Optional[float] = None) -> bool:
        """Wait for the tracked worker of ``job_id``; ``True`` if it finished.

        For a job that is driven manually (through :meth:`update` without
        :meth:`run_tracked`) there is no thread, so the terminal status is
        reported instead; ``False`` means "still active".  Raises
        :class:`KeyError` for an unknown job.
        """
        with self._lock:
            state = self._require_state(job_id)
            runner = state.thread
            if runner is None:
                return state.record.status in TERMINAL_STATUSES
        if hasattr(runner, "join"):
            res = runner.join(timeout)
            if res is not None:
                return bool(res)
            # Standard threading.Thread returns None on join()
            return not getattr(runner, "is_alive", lambda: False)()
        if hasattr(runner, "result"):
            try:
                runner.result(timeout=timeout)
                return True
            except TimeoutError:
                return False
            except Exception:
                return True
        return state.record.status in TERMINAL_STATUSES

    def result(self, job_id: str, timeout: Optional[float] = None) -> Any:
        """Wait for the tracked worker and return its value (re-raising errors)."""
        with self._lock:
            state = self._require_state(job_id)
            runner = state.thread
            done_event = state.done
        if runner is not None and hasattr(runner, "result") and callable(runner.result):
            return runner.result(timeout)
        if not done_event.wait(timeout):
            raise TimeoutError(f"job {job_id} did not finish within {timeout}s")
        if state.exception is not None:
            raise state.exception
        return state.value

    def result_or_exception(self, job_id: str, timeout: Optional[float] = None) -> Any:
        """:meth:`result` without raising: returns the value or the exception."""
        with self._lock:
            state = self._require_state(job_id)
            runner = state.thread
            done_event = state.done
        if runner is not None and hasattr(runner, "result_or_exception") and callable(runner.result_or_exception):
            return runner.result_or_exception(timeout)
        if not done_event.wait(timeout):
            raise TimeoutError(f"job {job_id} did not finish within {timeout}s")
        return state.exception if state.exception is not None else state.value

    def exception(self, job_id: str) -> Optional[BaseException]:
        """Recorded exception of a finished job (``None`` on success)."""
        with self._lock:
            return self._require_state(job_id).exception


#: Process wide singleton used by the Flask layer.
JOB_MANAGER = JobManager()
