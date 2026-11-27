"""Tests for concurrent session isolation, process_hygiene protection, GPU lock, distillation, and 429 guard."""

from __future__ import annotations

import os
import random
from pathlib import Path

import pytest

from tools.distill_agents_state import distill_knowledge
from tools.gpu_lock import acquire_gpu_lock
from tools.process_hygiene import POLICY_IN_USE_STOP, Proc, classify
from tools.rate_limit_guard import clear_rate_limit, record_rate_limit, remaining_cooldown
from tools.session_manager import gc_sessions, load_active_sessions, register_session


def _proc(pid: int, cmd: str, ppid: int = 1, name: str = "python.exe") -> Proc:
    return Proc(
        pid=pid,
        ppid=ppid,
        name=name,
        cmd=cmd,
        started="2026-11-26T00:00:00+00:00",
        exe=r"C:\Python311\python.exe",
    )


def test_process_hygiene_protects_active_session_pids_and_worktrees(tmp_path: Path) -> None:
    wt_other = tmp_path / "air_sim_wt" / "other-session"
    wt_other.mkdir(parents=True)
    # Register current live PID as owning `wt_other`
    session_rec = register_session(
        session_id="other-session",
        worktree_path=wt_other,
        branch="mission/other",
        pid=os.getpid(),
        cache_dir=tmp_path,
    )
    active = load_active_sessions(cache_dir=tmp_path, alive_only=True)
    assert len(active) == 1

    procs = [
        _proc(os.getpid(), "python session_host.py", ppid=1),
        _proc(90001, "python -m pytest tests/test_aero.py", ppid=os.getpid()),
        _proc(90002, f'python -m pytest "{wt_other}/tests/test_flight.py"', ppid=1),
        _proc(90003, "python -m pytest /unrelated/orphan/test_x.py", ppid=1),
    ]
    live = {p.pid for p in procs}
    verdicts = {
        v.proc.pid: v
        for v in classify(
            procs,
            live,
            set(),
            None,
            policy=POLICY_IN_USE_STOP,
            active_sessions=[session_rec],
        )
    }

    assert verdicts[90001].decision == "KEEP"
    assert "アクティブセッション" in verdicts[90001].reason
    assert verdicts[90002].decision == "KEEP"
    assert "アクティブセッション" in verdicts[90002].reason
    assert verdicts[90003].decision == "STOP"


def test_session_gc_removes_dead_pids(tmp_path: Path) -> None:
    wt = tmp_path / "wt_dead"
    wt.mkdir()
    register_session("dead-sess", wt, "mission/dead", pid=99999999, cache_dir=tmp_path)
    register_session("live-sess", wt, "mission/live", pid=os.getpid(), cache_dir=tmp_path)

    removed = gc_sessions(cache_dir=tmp_path)
    assert "dead-sess" in removed
    remaining = load_active_sessions(cache_dir=tmp_path, alive_only=False)
    assert [r["session_id"] for r in remaining] == ["live-sess"]


def test_gpu_lock_exclusion_timeout_and_stale_recovery(tmp_path: Path) -> None:
    lock_file = tmp_path / "gpu.lock"

    # 1. Exclusion & timeout while held by live PID
    with acquire_gpu_lock(timeout=1.0, poll_interval=0.02, lock_path=lock_file):
        assert lock_file.exists()
        with pytest.raises(TimeoutError):
            with acquire_gpu_lock(timeout=0.05, poll_interval=0.02, lock_path=lock_file):
                pass
    assert not lock_file.exists()

    # 2. Stale lock owned by dead PID is automatically recovered
    lock_file.write_text('{"pid": 99999999, "acquired_at": 1.0}', encoding="utf-8")
    with acquire_gpu_lock(timeout=1.0, poll_interval=0.02, lock_path=lock_file):
        assert lock_file.exists()
    assert not lock_file.exists()


def test_distill_knowledge_deduplicates_and_archives(tmp_path: Path) -> None:
    kb = tmp_path / ".agents" / "Knowledge.md"
    frag_dir = tmp_path / ".agents" / "knowledge.d"
    frag_dir.mkdir(parents=True)
    kb.write_text("# Knowledge\n- existing fact\n", encoding="utf-8")

    (frag_dir / "20261126_a.md").write_text("- existing fact\n- new fact A\n", encoding="utf-8")
    (frag_dir / "20261126_b.md").write_text("- new fact A\n- new fact B\n", encoding="utf-8")

    dry = distill_knowledge(tmp_path, apply=False)
    assert dry["new_bullets_count"] == 2
    assert not dry["applied"]
    assert len(list(frag_dir.glob("*.md"))) == 2

    applied = distill_knowledge(tmp_path, apply=True)
    assert applied["applied"]
    assert applied["new_bullets_count"] == 2
    assert len(list(frag_dir.glob("*.md"))) == 0
    kb_text = kb.read_text(encoding="utf-8")
    assert kb_text.count("- new fact A") == 1
    assert "- new fact B" in kb_text


def test_rate_limit_guard_records_and_applies_jitter(tmp_path: Path) -> None:
    state_file = tmp_path / "rate_limit.json"
    entry1 = record_rate_limit("opus4.6", base_cooldown_s=4.0, now=1000.0, state_path=state_file)
    assert entry1["consecutive_429"] == 1
    assert entry1["cooldown_until_epoch"] == 1004.0

    rem = remaining_cooldown("opus4.6", now=1001.0, jitter_ratio=0.25, state_path=state_file, rng=random.Random(42))
    assert 3.0 <= rem <= 3.75

    entry2 = record_rate_limit("opus4.6", base_cooldown_s=4.0, now=1002.0, state_path=state_file)
    assert entry2["consecutive_429"] == 2
    assert entry2["cooldown_until_epoch"] == 1010.0

    assert remaining_cooldown("opus4.6", now=1020.0, state_path=state_file) == 0.0
    clear_rate_limit("opus4.6", state_path=state_file)
    assert remaining_cooldown("opus4.6", now=1003.0, state_path=state_file) == 0.0
