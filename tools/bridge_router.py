"""Execution routing between the Antigravity and the Kilo Code backends.

Routing rules implemented here:

1. The requested backend is executed first (Antigravity is the primary agent).
2. A rate limited answer (HTTP 429 / ``TooManyRequests`` / ``RESOURCE_EXHAUSTED``)
   is never treated as a failure of the turn.  The router waits with an
   exponential backoff and then re-dispatches the very same work with the
   ``continue`` flag of the CLI, so the conversation resumes where it stopped.
3. When the primary backend cannot serve the request at all (CLI error,
   timeout, exhausted quota or rate limits that survived every retry) the
   router transparently continues the work on the Kilo Code backend using its
   free model, and reports the fallback to the monitor agent.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from tools.bridge_backends import (
        ANTIGRAVITY_BACKEND,
        KILO_BACKEND,
        KILO_DEFAULT_MODEL,
        WORKSPACE_DIR,
        AgentBackend,
        StreamEvent,
    )
except ImportError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tools.bridge_backends import (
        ANTIGRAVITY_BACKEND,
        KILO_BACKEND,
        KILO_DEFAULT_MODEL,
        WORKSPACE_DIR,
        AgentBackend,
        StreamEvent,
    )

STRONG_RATE_LIMIT_PATTERNS = (
    r"too\s*many\s*requests",
    r"toomanyrequests",
    r"resource[\s_-]*exhausted",
    r"(?:http|status|code|error)\W{0,6}429",
    r"429\W{0,4}(?:too\ many|error|rate)",
    r"quota[\s_-]*(?:exceeded|exhausted|depleted)",
    r"exhausted your quota",
    r"out of quota",
    r"individual quota reached",
    r"capacity exhausted",
    r"prefill_queue_overloaded",
)

WEAK_RATE_LIMIT_PATTERNS = (
    r"rate[\s_-]*limits?",
    r"\b429\b",
)

_STRONG_RATE_LIMIT_RE = re.compile("|".join(STRONG_RATE_LIMIT_PATTERNS), re.IGNORECASE)
_WEAK_RATE_LIMIT_RE = re.compile("|".join(WEAK_RATE_LIMIT_PATTERNS), re.IGNORECASE)

DEFAULT_MAX_RATE_LIMIT_RETRIES = 3
DEFAULT_BACKOFF_BASE_SECONDS = 2.0
DEFAULT_BACKOFF_CAP_SECONDS = 60.0
DEFAULT_TASK_TIMEOUT_SECONDS = 1800.0

EventCallback = Callable[[StreamEvent], Any]


def is_rate_limited(text: str, include_weak: bool = False) -> bool:
    """True when the given output carries a rate limit / quota exhaustion marker.

    The weak markers (``rate limit`` / a bare ``429``) are only trusted on the
    error streams, because a normal agent answer may legitimately contain the
    word "rate limit".
    """
    if not text:
        return False
    if _STRONG_RATE_LIMIT_RE.search(text) is not None:
        return True
    if include_weak and _WEAK_RATE_LIMIT_RE.search(text) is not None:
        return True
    return False


def strip_rate_limit_noise(text: str) -> str:
    """Remove the rate limit notices from the partial answer of a failed turn."""
    if not text:
        return ""
    kept = [line for line in text.splitlines() if not is_rate_limited(line, include_weak=True)]
    return "\n".join(kept).strip()


def backoff_delay(attempt: int, base: float = DEFAULT_BACKOFF_BASE_SECONDS, cap: float = DEFAULT_BACKOFF_CAP_SECONDS) -> float:
    """Exponential backoff delay for the given 1-based retry attempt."""
    if attempt < 1:
        attempt = 1
    return float(min(cap, base * (2 ** (attempt - 1))))


@dataclass
class BridgeRequest:
    """A single unit of work handed to the router."""

    prompt: str
    backend: str = ANTIGRAVITY_BACKEND
    model: str = ""
    session_id: Optional[str] = None
    continue_session: bool = False
    timeout: Optional[float] = None
    on_process: Optional[Callable[[Optional[Any]], None]] = None


@dataclass
class BridgeOutcome:
    """Result of a routed execution."""

    backend: str
    model: str
    text: str = ""
    session_id: Optional[str] = None
    status: str = "ok"
    error: Optional[str] = None
    fallback_used: bool = False
    fallback_reason: Optional[str] = None
    attempts: int = 0
    rate_limit_retries: int = 0
    duration_s: float = 0.0
    usage: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "model": self.model,
            "session_id": self.session_id,
            "status": self.status,
            "error": self.error,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "attempts": self.attempts,
            "rate_limit_retries": self.rate_limit_retries,
            "duration_s": self.duration_s,
            "usage": self.usage,
        }


class BridgeRouter:
    """Routes a request to a backend, adding retry, backoff and fallback."""

    def __init__(
        self,
        backends: Dict[str, AgentBackend],
        monitor: Any = None,
        fallback_enabled: bool = True,
        max_rate_limit_retries: int = DEFAULT_MAX_RATE_LIMIT_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE_SECONDS,
        backoff_cap: float = DEFAULT_BACKOFF_CAP_SECONDS,
        task_timeout: float = DEFAULT_TASK_TIMEOUT_SECONDS,
        quota_tracker: Any = None,
    ) -> None:
        self.backends = backends
        self.monitor = monitor
        self.fallback_enabled = fallback_enabled
        self.max_rate_limit_retries = max(0, int(max_rate_limit_retries))
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.task_timeout = task_timeout
        self.quota_tracker = quota_tracker

    def backend_status(self) -> Dict[str, bool]:
        return {name: backend.is_available() for name, backend in self.backends.items()}

    async def execute(self, request: BridgeRequest, on_event: Optional[EventCallback] = None) -> BridgeOutcome:
        """Execute the request, falling back to Kilo Code when the primary fails."""
        started = time.time()
        outcome = await self._run_backend(request, on_event)

        if outcome.ok or not self.fallback_enabled or request.backend != ANTIGRAVITY_BACKEND:
            outcome.duration_s = time.time() - started
            self._notify_monitor(outcome)
            return outcome

        reason = outcome.error or f"status={outcome.status}"
        await self._emit(
            on_event,
            StreamEvent(
                kind="fallback",
                text=(
                    f"🔀 **Kilo Code へフォールバック** (Antigravity: {reason})"
                ),
            ),
        )
        if self.monitor is not None:
            self.monitor.record_fallback(ANTIGRAVITY_BACKEND, KILO_BACKEND, reason)

        fallback_request = BridgeRequest(
            prompt=request.prompt,
            backend=KILO_BACKEND,
            model=KILO_DEFAULT_MODEL,
            session_id=None,
            continue_session=False,
            timeout=request.timeout,
            on_process=request.on_process,
        )
        fallback_outcome = await self._run_backend(fallback_request, on_event)
        fallback_outcome.fallback_used = True
        fallback_outcome.fallback_reason = reason
        fallback_outcome.duration_s = time.time() - started
        self._notify_monitor(fallback_outcome)
        return fallback_outcome

    async def _run_backend(self, request: BridgeRequest, on_event: Optional[EventCallback]) -> BridgeOutcome:
        backend = self.backends.get(request.backend)
        if backend is None:
            return BridgeOutcome(
                backend=request.backend,
                model=request.model,
                status="error",
                error=f"バックエンド '{request.backend}' が登録されていません",
            )
        if not backend.is_available():
            return BridgeOutcome(
                backend=request.backend,
                model=request.model,
                status="error",
                error=f"バックエンド '{request.backend}' の実行ファイルが見つかりません",
            )

        carried_text = ""
        session_id = request.session_id
        retries = 0
        attempts = 0
        outcome: BridgeOutcome

        while True:
            attempts += 1
            use_session = session_id
            use_continue = request.continue_session if attempts == 1 else True
            if use_session:
                use_continue = False
            attempt_request = BridgeRequest(
                prompt=request.prompt,
                backend=request.backend,
                model=request.model,
                session_id=use_session,
                continue_session=use_continue,
                timeout=request.timeout,
            )
            outcome = await self._invoke(backend, attempt_request, on_event, carried_text)
            carried_text = outcome.text
            if outcome.session_id:
                session_id = outcome.session_id
            if outcome.status != "rate_limited":
                break
            if retries >= self.max_rate_limit_retries:
                outcome.error = (
                    f"レート制限 (HTTP 429) が {retries} 回続けて発生しました: {outcome.error}"
                )
                break
            retries += 1
            delay = backoff_delay(retries, self.backoff_base, self.backoff_cap)
            await self._emit(
                on_event,
                StreamEvent(
                    kind="status",
                    text=(
                        f"⏳ レート制限 (HTTP 429) 検出 — {delay:.1f} 秒待機後に "
                        f"`continue` で再開します (再試行 {retries}/{self.max_rate_limit_retries})"
                    ),
                ),
            )
            await asyncio.sleep(delay)

        outcome.attempts = attempts
        outcome.rate_limit_retries = retries
        if session_id:
            outcome.session_id = session_id
        return outcome

    async def _invoke(
        self,
        backend: AgentBackend,
        request: BridgeRequest,
        on_event: Optional[EventCallback],
        carried_text: str,
    ) -> BridgeOutcome:
        cmd = backend.build_command(
            request.prompt,
            request.model,
            session_id=request.session_id,
            continue_session=request.continue_session,
        )
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        accumulated = carried_text
        stdout_dump = ""
        session_id = request.session_id
        usage: Dict[str, Any] = {}
        error_text = ""
        resolved_model: Optional[str] = None
        stderr_chunks: List[str] = []

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(WORKSPACE_DIR),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except (OSError, ValueError) as exc:
            return BridgeOutcome(
                backend=backend.name,
                model=request.model,
                text=accumulated,
                session_id=session_id,
                status="error",
                error=f"プロセス起動失敗: {exc}",
            )

        stderr_task = asyncio.create_task(_drain_stderr(proc, stderr_chunks))
        if request.on_process is not None:
            request.on_process(proc)

        async def _read_stdout() -> None:
            nonlocal accumulated, stdout_dump, session_id, error_text, resolved_model, usage
            if proc.stdout is None:
                return
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                stdout_dump += text + "\n"
                event = backend.parse_line(text)
                if event is None:
                    continue
                if event.session_id:
                    session_id = event.session_id
                if event.model:
                    resolved_model = event.model
                if event.usage:
                    usage.update(event.usage)
                if event.kind == "text":
                    accumulated += event.text
                elif event.kind == "raw":
                    accumulated += text + "\n"
                elif event.kind == "final":
                    if event.text:
                        accumulated = event.text
                elif event.kind == "error":
                    error_text = event.text or error_text
                await self._emit(on_event, event)

        timeout = request.timeout if request.timeout is not None else self.task_timeout
        try:
            await asyncio.wait_for(_read_stdout(), timeout=timeout)
        except asyncio.TimeoutError:
            await _terminate(proc)
            _cancel_task(stderr_task)
            if request.on_process is not None:
                request.on_process(None)
            return BridgeOutcome(
                backend=backend.name,
                model=request.model,
                text=accumulated,
                session_id=session_id,
                status="timeout",
                error=f"timeout: {timeout:.0f} 秒のタイムアウト",
                usage=usage,
            )
        except asyncio.CancelledError:
            await _terminate(proc)
            _cancel_task(stderr_task)
            if request.on_process is not None:
                request.on_process(None)
            raise

        if request.on_process is not None:
            request.on_process(None)
        await stderr_task
        returncode = await proc.wait()
        stderr_text = "".join(stderr_chunks).strip()
        error_stream = "\n".join(part for part in (error_text, stderr_text) if part)

        if is_rate_limited(stdout_dump + "\n" + error_stream) or is_rate_limited(error_stream, include_weak=True):
            return BridgeOutcome(
                backend=backend.name,
                model=resolved_model or request.model,
                text=strip_rate_limit_noise(accumulated),
                session_id=session_id,
                status="rate_limited",
                error=error_text or stderr_text or "レート制限 (HTTP 429) を検出しました",
                usage=usage,
            )
        if returncode != 0 and not accumulated.strip():
            return BridgeOutcome(
                backend=backend.name,
                model=resolved_model or request.model,
                text=accumulated,
                session_id=session_id,
                status="error",
                error=error_text or stderr_text or f"終了コード {returncode}",
                usage=usage,
            )
        if error_text and not accumulated.strip():
            return BridgeOutcome(
                backend=backend.name,
                model=resolved_model or request.model,
                text=accumulated,
                session_id=session_id,
                status="error",
                error=error_text,
                usage=usage,
            )
        return BridgeOutcome(
            backend=backend.name,
            model=resolved_model or request.model,
            text=accumulated,
            session_id=session_id,
            status="ok",
            usage=usage,
        )

    async def _emit(self, on_event: Optional[EventCallback], event: StreamEvent) -> None:
        if on_event is None:
            return
        result = on_event(event)
        if asyncio.iscoroutine(result):
            await result

    def _notify_monitor(self, outcome: BridgeOutcome) -> None:
        if self.monitor is None:
            return
        if outcome.ok:
            self.monitor.record_success(outcome.backend, outcome.duration_s)
        else:
            self.monitor.record_error(
                outcome.backend,
                outcome.error or outcome.status,
                rate_limited=(outcome.status == "rate_limited"),
            )
        if self.quota_tracker is not None:
            self.quota_tracker.record_request(outcome.backend)


async def _drain_stderr(proc: asyncio.subprocess.Process, sink: List[str]) -> None:
    if proc.stderr is None:
        return
    try:
        data = await proc.stderr.read()
    except (asyncio.CancelledError, ValueError):
        return
    except Exception:
        return
    if data:
        sink.append(data.decode("utf-8", errors="replace"))


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        proc.kill()
    except ProcessLookupError:
        return
    except Exception:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        return


def _cancel_task(task: asyncio.Task) -> None:
    """Cancel a task and swallow its eventual exception."""
    if task.done():
        return
    task.cancel()

    def _swallow(finished: asyncio.Task) -> None:
        if finished.cancelled():
            return
        finished.exception()

    task.add_done_callback(_swallow)
