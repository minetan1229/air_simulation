"""Monitor agent for the Discord bridge.

The monitor is the always-on supervisor of the agent stack.  It samples

* the health of the host (CPU / RAM / GPU),
* the availability of every agent backend and the latency of its API answers,
* the accumulated error and rate limit counters,
* the remaining quota of the primary backend,

and raises alerts as soon as something degrades.  Alerts are delivered to the
callback registered by the bridge, which posts them into the Discord response
channel and notifies the main agent through the Discord webhook.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from tools.bridge_quota import QuotaSnapshot
except ImportError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tools.bridge_quota import QuotaSnapshot

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:  # pragma: no cover - optional dependency
    HAS_PSUTIL = False
    psutil = None  # type: ignore[assignment]

LEVEL_INFO = "info"
LEVEL_WARNING = "warning"
LEVEL_CRITICAL = "critical"

BACKEND_LABELS: Dict[str, str] = {
    "antigravity": "Antigravity",
    "kilo": "Kilo Code",
}

DEFAULT_MONITOR_INTERVAL = 60.0
DEFAULT_ERROR_THRESHOLD = 3
MAX_ALERT_HISTORY = 50


def backend_label(name: str) -> str:
    """Human readable name of a backend."""
    return BACKEND_LABELS.get(name, name)


def get_system_metrics() -> Dict[str, Any]:
    """Gather CPU, RAM, and GPU status."""
    metrics: Dict[str, Any] = {
        "cpu": "N/A",
        "cpu_percent": 0.0,
        "memory": "N/A",
        "memory_percent": 0.0,
        "gpu": "GPU: 未検出 / 利用不可",
    }
    if HAS_PSUTIL and psutil:
        try:
            cpu_pct = psutil.cpu_percent(interval=0.1)
            cpu_count = psutil.cpu_count(logical=True)
            metrics["cpu_percent"] = cpu_pct
            metrics["cpu"] = f"{cpu_pct:.1f}% ({cpu_count} Threads)"

            mem = psutil.virtual_memory()
            mem_used_gb = mem.used / (1024 ** 3)
            mem_tot_gb = mem.total / (1024 ** 3)
            metrics["memory_percent"] = mem.percent
            metrics["memory"] = f"{mem_used_gb:.1f}GB / {mem_tot_gb:.1f}GB ({mem.percent}%)"
        except Exception as exc:  # pragma: no cover - depends on the host
            # Intentional guard: a host without a readable /proc or psutil must
            # not abort the health report, the metrics are simply incomplete.
            # The cause (class + message) is recorded instead of being
            # swallowed.
            metrics["cpu_error"] = f"{type(exc).__name__}: {exc}"

    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            res = subprocess.run(
                [
                    smi,
                    "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
            )
            if res.returncode == 0 and res.stdout.strip():
                gpus: List[str] = []
                for line in res.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 4:
                        name, util, m_used, m_tot = parts[0], parts[1], parts[2], parts[3]
                        try:
                            m_used_f = float(m_used) / 1024.0
                            m_tot_f = float(m_tot) / 1024.0
                            gpus.append(
                                f"{name} | 負荷: {util}% | VRAM: {m_used_f:.1f}GB / {m_tot_f:.1f}GB"
                            )
                        except ValueError:
                            gpus.append(f"{name} | 負荷: {util}% | VRAM: {m_used}MB / {m_tot}MB")
                if gpus:
                    metrics["gpu"] = "\n".join(gpus)
        except Exception as exc:  # pragma: no cover - depends on the host
            # Intentional guard: a missing or failing nvidia-smi must not
            # abort the health report, the GPU line is simply replaced by the
            # error.  The cause (class + message) is recorded instead of being
            # swallowed.
            metrics["gpu"] = f"GPU取得エラー: {type(exc).__name__}: {exc}"

    return metrics


@dataclass
class MonitorAlert:
    """A single alert raised by the monitor agent."""

    level: str
    kind: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "kind": self.kind,
            "message": self.message,
            "details": self.details,
            "created_at": self.created_at,
            "created_at_text": datetime.fromtimestamp(self.created_at).strftime("%Y-%m-%d %H:%M:%S"),
        }

    def to_embed_lines(self) -> List[str]:
        icon = {LEVEL_INFO: "ℹ️", LEVEL_WARNING: "⚠️", LEVEL_CRITICAL: "🚨"}.get(self.level, "ℹ️")
        return [f"{icon} **[{self.kind}]** {self.message}"]


class MonitorAgent:
    """Always-on supervisor of the agent backends."""

    def __init__(
        self,
        interval: float = DEFAULT_MONITOR_INTERVAL,
        error_threshold: int = DEFAULT_ERROR_THRESHOLD,
        on_alert: Optional[Callable[[MonitorAlert], Any]] = None,
    ) -> None:
        self.interval = float(interval)
        self.error_threshold = max(1, int(error_threshold))
        self.on_alert = on_alert
        self.started_at = time.time()
        self.alerts: List[MonitorAlert] = []
        self.last_check_at: Optional[float] = None
        self.last_quota: Optional[QuotaSnapshot] = None
        self.fallback_count = 0
        self.rate_limit_count = 0
        self.error_count = 0
        self.success_count = 0
        self._backend_stats: Dict[str, Dict[str, Any]] = {}
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    # -- recording ---------------------------------------------------------
    def record_success(self, backend: str, duration_s: float = 0.0) -> None:
        """Register a successful API answer of a backend."""
        self.success_count += 1
        stats = self._backend_stats.setdefault(backend, self._empty_backend_stats())
        stats["successes"] += 1
        stats["consecutive_errors"] = 0
        stats["last_success_at"] = time.time()
        stats["last_latency_s"] = duration_s

    def record_error(self, backend: str, error: str, rate_limited: bool = False) -> None:
        """Register a failed API answer of a backend."""
        self.error_count += 1
        stats = self._backend_stats.setdefault(backend, self._empty_backend_stats())
        stats["errors"] += 1
        stats["consecutive_errors"] += 1
        stats["last_error_at"] = time.time()
        stats["last_error"] = error
        if rate_limited:
            self.rate_limit_count += 1
            stats["rate_limits"] += 1

    def record_fallback(self, from_backend: str, to_backend: str, reason: str) -> MonitorAlert:
        """Register a backend fallback and raise the corresponding alert."""
        self.fallback_count += 1
        stats = self._backend_stats.setdefault(from_backend, self._empty_backend_stats())
        stats["fallbacks"] += 1
        alert = MonitorAlert(
            level=LEVEL_WARNING,
            kind="fallback",
            message=(
                f"{backend_label(from_backend)} が利用できないため "
                f"**{backend_label(to_backend)}** へフォールバックしました "
                f"(理由: {reason})"
            ),
            details={
                "from_backend": from_backend,
                "to_backend": to_backend,
                "reason": reason,
            },
        )
        self._push(alert)
        return alert

    def record_quota(self, snapshot: QuotaSnapshot) -> None:
        """Store the latest quota snapshot of a backend."""
        self.last_quota = snapshot

    # -- inspection --------------------------------------------------------
    @staticmethod
    def _empty_backend_stats() -> Dict[str, Any]:
        return {
            "successes": 0,
            "errors": 0,
            "rate_limits": 0,
            "fallbacks": 0,
            "consecutive_errors": 0,
            "last_success_at": None,
            "last_error_at": None,
            "last_error": None,
            "last_latency_s": 0.0,
        }

    def backend_stats(self) -> Dict[str, Dict[str, Any]]:
        return {name: dict(stats) for name, stats in self._backend_stats.items()}

    def status(self) -> Dict[str, Any]:
        """Return the current monitor status as a plain dictionary."""
        return {
            "uptime_s": time.time() - self.started_at,
            "interval_s": self.interval,
            "error_threshold": self.error_threshold,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "rate_limit_count": self.rate_limit_count,
            "fallback_count": self.fallback_count,
            "backends": self.backend_stats(),
            "last_check_at": self.last_check_at,
            "quota": self.last_quota.to_dict() if self.last_quota else None,
            "recent_alerts": [alert.to_dict() for alert in self.alerts[-5:]],
            "running": self._task is not None and not self._task.done(),
        }

    def status_lines(self) -> List[str]:
        """Human readable monitor status used by the Discord status embed."""
        status = self.status()
        lines = [
            f"稼働時間: **{status['uptime_s'] / 60:.1f} 分** / 監視間隔: **{self.interval:.0f} 秒**",
            f"成功: **{self.success_count}** 件 / エラー: **{self.error_count}** 件 / "
            f"レート制限: **{self.rate_limit_count}** 件 / フォールバック: **{self.fallback_count}** 件",
        ]
        for name, stats in sorted(status["backends"].items()):
            lines.append(
                f"`{name}`: 成功 {stats['successes']} / エラー {stats['errors']} / "
                f"連続エラー {stats['consecutive_errors']} / フォールバック {stats['fallbacks']}"
            )
        if self.last_quota is not None:
            lines.append("クォータ: " + " | ".join(self.last_quota.summary_lines()))
        else:
            lines.append("クォータ: 未取得")
        if self.alerts:
            lines.append(f"最新アラート: {self.alerts[-1].message}")
        return lines

    async def check(self, backends: Optional[Dict[str, Any]] = None) -> List[MonitorAlert]:
        """Evaluate the current state and raise the alerts that apply."""
        self.last_check_at = time.time()
        raised: List[MonitorAlert] = []

        for name, backend in (backends or {}).items():
            try:
                available = bool(backend.is_available())
            except Exception as exc:  # pragma: no cover - defensive
                # Intentional guard: a probe that raises must not abort the
                # whole health check, the backend is simply reported as
                # unavailable.  The cause is recorded instead of being
                # swallowed.
                print(
                    f"[MonitorAgent] availability probe of `{name}` failed "
                    f"({type(exc).__name__}: {exc})",
                    file=sys.stderr,
                )
                available = False
            stats = self._backend_stats.setdefault(name, self._empty_backend_stats())
            stats["available"] = available
            if not available:
                alert = MonitorAlert(
                    level=LEVEL_CRITICAL,
                    kind="backend_unavailable",
                    message=f"バックエンド `{backend_label(name)}` の実行ファイルが見つかりません",
                    details={"backend": name},
                )
                raised.append(self._push(alert))

        for name, stats in self._backend_stats.items():
            if stats["consecutive_errors"] >= self.error_threshold:
                alert = MonitorAlert(
                    level=LEVEL_CRITICAL,
                    kind="consecutive_errors",
                    message=(
                        f"バックエンド `{backend_label(name)}` が {stats['consecutive_errors']} 回連続で失敗しました: "
                        f"{stats['last_error']}"
                    ),
                    details={"backend": name, "errors": stats["consecutive_errors"]},
                )
                raised.append(self._push(alert))

        if self.last_quota is not None and self.last_quota.is_exhausted:
            alert = MonitorAlert(
                level=LEVEL_WARNING,
                kind="quota_exhausted",
                message=(
                    f"`{self.last_quota.backend}` のクォータが枯渇しています "
                    f"(週次残量 {self.last_quota.worst_weekly_percent}%) — "
                    "Kilo Code へのフォールバックを推奨します"
                ),
                details={"backend": self.last_quota.backend},
            )
            raised.append(self._push(alert))

        return raised

    def _push(self, alert: MonitorAlert) -> MonitorAlert:
        self.alerts.append(alert)
        if len(self.alerts) > MAX_ALERT_HISTORY:
            del self.alerts[: len(self.alerts) - MAX_ALERT_HISTORY]
        return alert

    async def notify(self, alert: MonitorAlert) -> None:
        """Deliver an alert through the registered callback."""
        if self.on_alert is None:
            return
        try:
            result = self.on_alert(alert)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:  # pragma: no cover - alerting must not crash
            print(f"[MonitorAgent] alert delivery failed: {exc}", file=sys.stderr)

    # -- lifecycle ---------------------------------------------------------
    def start(self, backends: Optional[Dict[str, Any]] = None) -> None:
        """Start the background monitoring loop."""
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self.run_forever(backends))

    async def stop(self) -> None:
        """Stop the background monitoring loop."""
        self._stop.set()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError as exc:
            # Intentional guard: ``stop()`` cancels the loop task on purpose,
            # so the CancelledError raised by ``await task`` is the expected
            # outcome and must not propagate to the caller (it would abort the
            # shutdown).  The reason is recorded instead of being swallowed.
            print(
                f"[MonitorAgent] monitor loop cancelled ({type(exc).__name__})",
                file=sys.stderr,
            )
        except Exception as exc:
            # Intentional guard: a failing monitor loop must not break the
            # shutdown of the bridge.  The cause is recorded instead of being
            # swallowed.
            print(
                f"[MonitorAgent] monitor loop failed ({type(exc).__name__}: {exc})",
                file=sys.stderr,
            )

    async def run_forever(self, backends: Optional[Dict[str, Any]] = None) -> None:
        """Continuously evaluate the state and deliver the resulting alerts."""
        while not self._stop.is_set():
            try:
                for alert in await self.check(backends):
                    await self.notify(alert)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[MonitorAgent] check failed: {exc}", file=sys.stderr)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                continue
