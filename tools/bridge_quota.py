"""Quota / rate-limit tracking for the Discord agent bridge.

Antigravity publishes the remaining quota through read-only slash commands:

    agy -p "/quota"    -> "<group>\t<metric>\t<percent>%\t<reset-iso8601>" (one per line)
    agy -p "/credits"  -> "Remaining credits\t<n>"

The Kilo Code CLI exposes no quota endpoint, so its free tier is reported as an
``unmetered`` snapshot and the bridge shows the resolved model instead.

Besides the raw percentage the tracker derives an *available request count*
from the measured relation between issued requests and the observed quota
drop.  No constant total is assumed: the slope is measured from the traffic
that the bridge itself produced, so the estimate degrades to ``None`` until
enough samples exist.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

QUOTA_TTL_SECONDS = 120.0
WEEKLY_METRIC = "weekly"
FIVE_HOUR_METRIC = "five_hour"
EXHAUSTED_THRESHOLD_PERCENT = 1.0

_PERCENT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")
_MAX_OBSERVATIONS = 64


def _parse_reset(value: str) -> Optional[datetime]:
    """Parse the reset timestamp emitted by the Antigravity quota command."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@dataclass(frozen=True)
class QuotaEntry:
    """A single quota line, e.g. ``Gemini Models / Weekly Limit Remaining / 1%``."""

    group: str
    metric: str
    remaining_percent: float
    reset_at: Optional[datetime] = None

    @property
    def kind(self) -> str:
        """Normalised metric family (``weekly`` / ``five_hour`` / other)."""
        lowered = self.metric.lower()
        if "weekly" in lowered or "week" in lowered:
            return WEEKLY_METRIC
        if "five hour" in lowered or "five-hour" in lowered or "5 hour" in lowered or "5-hour" in lowered:
            return FIVE_HOUR_METRIC
        return lowered.replace(" ", "_").replace("-", "_")

    def reset_text(self) -> str:
        if self.reset_at is None:
            return "—"
        return self.reset_at.astimezone().strftime("%m/%d %H:%M")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "group": self.group,
            "metric": self.metric,
            "remaining_percent": self.remaining_percent,
            "reset_at": self.reset_at.isoformat() if self.reset_at else None,
        }


@dataclass
class QuotaSnapshot:
    """Immutable view of the quota state of one backend at one point in time."""

    backend: str
    entries: List[QuotaEntry] = field(default_factory=list)
    credits_remaining: Optional[int] = None
    fetched_at: float = field(default_factory=time.time)
    error: Optional[str] = None
    unmetered: bool = False

    def by_kind(self, kind: str) -> List[QuotaEntry]:
        return [entry for entry in self.entries if entry.kind == kind]

    @property
    def weekly(self) -> List[QuotaEntry]:
        return self.by_kind(WEEKLY_METRIC)

    @property
    def five_hour(self) -> List[QuotaEntry]:
        return self.by_kind(FIVE_HOUR_METRIC)

    def worst(self, kind: str) -> Optional[float]:
        values = [entry.remaining_percent for entry in self.by_kind(kind)]
        return min(values) if values else None

    @property
    def worst_weekly_percent(self) -> Optional[float]:
        return self.worst(WEEKLY_METRIC)

    @property
    def worst_five_hour_percent(self) -> Optional[float]:
        return self.worst(FIVE_HOUR_METRIC)

    @property
    def is_exhausted(self) -> bool:
        """True when the weekly quota of any group is effectively used up."""
        if self.error or self.unmetered:
            return False
        weekly = self.worst_weekly_percent
        return weekly is not None and weekly <= EXHAUSTED_THRESHOLD_PERCENT

    def summary_lines(self) -> List[str]:
        """Human readable lines used by the Discord status embeds."""
        if self.error:
            return [f"❌ クォータ取得失敗: {self.error}"]
        if self.unmetered:
            return ["♾️ 無料枠（クォータ制限なし）"]
        lines: List[str] = []
        for entry in self.entries:
            lines.append(
                f"{entry.group} / {entry.metric}: **{entry.remaining_percent:g}%** 残 (リセット: {entry.reset_text()})"
            )
        if self.credits_remaining is not None:
            lines.append(f"残りクレジット: **{self.credits_remaining}**")
        if not lines:
            lines.append("ℹ️ クォータ情報が取得できませんでした")
        return lines

    def to_dict(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "entries": [entry.to_dict() for entry in self.entries],
            "credits_remaining": self.credits_remaining,
            "fetched_at": self.fetched_at,
            "error": self.error,
            "unmetered": self.unmetered,
            "worst_weekly_percent": self.worst_weekly_percent,
            "worst_five_hour_percent": self.worst_five_hour_percent,
            "is_exhausted": self.is_exhausted,
        }


def parse_quota_output(text: str) -> List[QuotaEntry]:
    """Parse the tab separated output of ``agy -p "/quota"``."""
    entries: List[QuotaEntry] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split("\t") if part.strip()]
        if len(parts) < 3:
            parts = [part.strip() for part in re.split(r"\t+|\s{2,}", line) if part.strip()]
        if len(parts) < 3:
            continue
        match = _PERCENT_RE.search(parts[2])
        if match is None:
            continue
        reset_at = _parse_reset(parts[3]) if len(parts) > 3 else None
        entries.append(
            QuotaEntry(
                group=parts[0],
                metric=parts[1],
                remaining_percent=float(match.group(1)),
                reset_at=reset_at,
            )
        )
    return entries


def parse_credits_output(text: str) -> Optional[int]:
    """Parse the output of ``agy -p "/credits"`` into the remaining credit count."""
    for raw_line in (text or "").splitlines():
        parts = [part.strip() for part in raw_line.split("\t") if part.strip()]
        if len(parts) >= 2 and "credit" in parts[0].lower():
            try:
                return int(float(parts[1]))
            except ValueError:
                continue
    return None


class QuotaTracker:
    """Caches quota snapshots and derives the available request count."""

    def __init__(self, ttl: float = QUOTA_TTL_SECONDS) -> None:
        self.ttl = ttl
        self._snapshots: Dict[str, QuotaSnapshot] = {}
        self._request_counts: Dict[str, int] = {}
        self._observations: Dict[str, List[Tuple[float, float, int]]] = {}
        self._lock = asyncio.Lock()

    def cached(self, backend: str) -> Optional[QuotaSnapshot]:
        """Return the cached snapshot when it is still inside the TTL window."""
        snapshot = self._snapshots.get(backend)
        if snapshot is None:
            return None
        if time.time() - snapshot.fetched_at > self.ttl:
            return None
        return snapshot

    def store(self, snapshot: QuotaSnapshot) -> QuotaSnapshot:
        """Store a snapshot and feed its percentage into the request estimator."""
        self._snapshots[snapshot.backend] = snapshot
        worst = snapshot.worst_weekly_percent
        if worst is None:
            worst = snapshot.worst_five_hour_percent
        if worst is not None:
            self.observe_percent(snapshot.backend, worst)
        return snapshot

    def invalidate(self, backend: str) -> None:
        self._snapshots.pop(backend, None)

    def record_request(self, backend: str) -> int:
        """Count one dispatched request for the backend."""
        count = self._request_counts.get(backend, 0) + 1
        self._request_counts[backend] = count
        return count

    def request_count(self, backend: str) -> int:
        return self._request_counts.get(backend, 0)

    def observe_percent(self, backend: str, percent: float) -> None:
        """Record a quota percentage observation together with the request counter."""
        observations = self._observations.setdefault(backend, [])
        observations.append((time.time(), percent, self._request_counts.get(backend, 0)))
        if len(observations) > _MAX_OBSERVATIONS:
            del observations[: len(observations) - _MAX_OBSERVATIONS]

    def estimated_remaining_requests(self, backend: str) -> Optional[float]:
        """Estimate how many requests the remaining quota still allows.

        The estimate uses the steepest measured drop between two observations:
        ``requests_per_percent = used_requests / percent_drop``.  It stays
        ``None`` while no drop has been observed yet.
        """
        observations = self._observations.get(backend, [])
        if len(observations) < 2:
            return None
        best: Optional[Tuple[float, int]] = None
        for index in range(len(observations) - 1):
            _, percent_before, requests_before = observations[index]
            _, percent_after, requests_after = observations[index + 1]
            drop = percent_before - percent_after
            used = requests_after - requests_before
            if drop <= 0 or used <= 0:
                continue
            if best is None or drop > best[0]:
                best = (drop, used)
        if best is None:
            return None
        drop, used = best
        snapshot = self.cached(backend)
        if snapshot is None:
            return None
        remaining = snapshot.worst_weekly_percent
        if remaining is None:
            remaining = snapshot.worst_five_hour_percent
        if remaining is None:
            return None
        return (used / drop) * remaining

    async def fetch_antigravity(self, cwd: Path, timeout: float = 30.0) -> QuotaSnapshot:
        """Read the live Antigravity quota by running the read-only slash commands."""

        def _run() -> QuotaSnapshot:
            executable = shutil.which("agy")
            if not executable:
                return QuotaSnapshot(backend="antigravity", error="agy CLI が見つかりません")
            entries: List[QuotaEntry] = []
            credits: Optional[int] = None
            errors: List[str] = []
            for args, kind in ((["-p", "/quota"], "quota"), (["-p", "/credits"], "credits")):
                try:
                    proc = subprocess.run(
                        [executable, *args],
                        cwd=str(cwd),
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=timeout,
                    )
                except subprocess.TimeoutExpired:
                    errors.append(f"/{kind}: タイムアウト")
                    continue
                except OSError as exc:
                    errors.append(f"/{kind}: {exc}")
                    continue
                if proc.returncode != 0:
                    errors.append(f"/{kind}: 終了コード {proc.returncode}")
                    continue
                if kind == "quota":
                    entries = parse_quota_output(proc.stdout)
                    if not entries:
                        errors.append("/quota: 解析可能な行なし")
                else:
                    credits = parse_credits_output(proc.stdout)
            return QuotaSnapshot(
                backend="antigravity",
                entries=entries,
                credits_remaining=credits,
                error="; ".join(errors) if errors else None,
            )

        snapshot = await asyncio.to_thread(_run)
        return self.store(snapshot)

    async def get(self, backend: str, cwd: Path, timeout: float = 30.0) -> QuotaSnapshot:
        """Return a fresh snapshot for the backend, using the cache when possible."""
        async with self._lock:
            cached = self.cached(backend)
            if cached is not None:
                return cached
        if backend == "kilo":
            snapshot = QuotaSnapshot(backend="kilo", unmetered=True)
            return self.store(snapshot)
        return await self.fetch_antigravity(cwd, timeout=timeout)
