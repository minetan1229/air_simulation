"""Shared cross-session 429 rate-limit cooldown guard (anti-Thundering Herd).

When any concurrent Antigravity session hits HTTP 429 / RESOURCE_EXHAUSTED,
it records a shared cooldown window under `~/.cache/air_simulation/rate_limit.json`.
Peer sessions inspect this file before calling the same backend/model and either
wait with randomized jitter or fall back immediately.
"""

from __future__ import annotations

import json
import os
import sys
import random
import time
from pathlib import Path
from typing import Any, Dict, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.session_manager import default_cache_dir


def default_rate_limit_path(cache_dir: Optional[Path] = None) -> Path:
    return (cache_dir or default_cache_dir()) / "rate_limit.json"


def _read_state(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def record_rate_limit(
    key: str,
    base_cooldown_s: float = 4.0,
    max_cooldown_s: float = 120.0,
    now: Optional[float] = None,
    state_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Record a 429 event for `key` and return updated cooldown entry."""
    path = state_path or default_rate_limit_path()
    ts = time.time() if now is None else now
    state = _read_state(path)
    prev = state.get(key, {}) if isinstance(state.get(key), dict) else {}
    consecutive = int(prev.get("consecutive_429", 0)) + 1
    cooldown = min(max_cooldown_s, base_cooldown_s * (2 ** (consecutive - 1)))
    entry = {
        "consecutive_429": consecutive,
        "last_429_epoch": ts,
        "cooldown_until_epoch": ts + cooldown,
    }
    state[key] = entry
    _write_state(path, state)
    return entry


def clear_rate_limit(key: str, state_path: Optional[Path] = None) -> None:
    path = state_path or default_rate_limit_path()
    state = _read_state(path)
    if key in state:
        del state[key]
        _write_state(path, state)


def remaining_cooldown(
    key: str,
    now: Optional[float] = None,
    jitter_ratio: float = 0.25,
    state_path: Optional[Path] = None,
    rng: Optional[random.Random] = None,
) -> float:
    """Return remaining cooldown seconds (with positive jitter) or 0.0 if clear."""
    path = state_path or default_rate_limit_path()
    ts = time.time() if now is None else now
    state = _read_state(path)
    entry = state.get(key)
    if not isinstance(entry, dict):
        return 0.0
    until = float(entry.get("cooldown_until_epoch", 0.0))
    rem = until - ts
    if rem <= 0.0:
        return 0.0
    r = rng if rng is not None else random
    jitter = rem * max(0.0, jitter_ratio) * r.random()
    return rem + jitter
