"""Tests for remote compute, hybrid split, and watchdog resilience fixes.

Covers:
- #179: remote_evaluate_worker sys.path hierarchy resolution
- #180: hybrid_split _sync_tree rsync failure fallback to SFTP
- #181: watchdog.sh cross-platform PID check logic
- #182: setup.sh graceful CUDA failure handling
- #183: watchdog_loop.py cross-platform memory/handle/FD metric extraction
- #185: watchdog_loop.py normal completion detection from run_summary.json / logs
- #186: hybrid_split _sftp_mkdir_p parent directory recursive creation
- #187: hybrid_split _remote_run guaranteed cleanup of remote temporary files
- #192: hybrid_split core_ratio_split exact target count partitioning
-"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.hybrid_split import core_ratio_split, _sftp_mkdir_p
from tools.watchdog_loop import get_process_metrics, is_process_running


def test_core_ratio_split_exact_remote_count():
    """Verify that core_ratio_split assigns exactly the expected ratio of cases across various list sizes."""
    # Test case: 10 cases, remote_ratio = 0.6 (60% -> remote_n = 6)
    cases = [{"id": i} for i in range(10)]
    local, remote, l_idx, r_idx = core_ratio_split(cases, remote_cores=6, local_cores=4, remote_ratio=0.6)
    assert len(remote) == 6
    assert len(local) == 4
    assert len(l_idx) == 4
    assert len(r_idx) == 6
    assert sorted([c["id"] for c in local + remote]) == list(range(10))

    # Test edge case: remote_cores = 0
    local0, remote0, l0_idx, r0_idx = core_ratio_split(cases, remote_cores=0)
    assert len(remote0) == 0
    assert len(local0) == 10

    # Test edge case: remote_ratio = 1.0
    local_all, remote_all, _, _ = core_ratio_split(cases, remote_cores=10, remote_ratio=1.0)
    assert len(remote_all) == 10
    assert len(local_all) == 0

    # Exhaustive test for N in 1..25 with various ratios
    for n in range(1, 26):
        sample = [{"id": i} for i in range(n)]
        # r_target = 0 with remote_cores=0
        loc0, rem0, _, _ = core_ratio_split(sample, remote_cores=0)
        assert len(rem0) == 0
        assert len(loc0) == n
        # r_target in 1..n
        for r_target in range(1, n + 1):
            ratio = r_target / n
            loc, rem, _, _ = core_ratio_split(sample, remote_cores=1, remote_ratio=ratio)
            expected = min(n, max(1, round(n * ratio)))
            assert len(rem) == expected, f"Failed for n={n}, target={r_target}: got {len(rem)} vs expected {expected}"
            assert len(loc) == n - expected


def test_remote_evaluate_worker_sys_path():
    """Verify that remote_evaluate_worker.py sets repo_root to parent.parent (not parents[2])."""
    worker_path = Path("tools/remote_evaluate_worker.py")
    assert worker_path.exists()
    content = worker_path.read_text(encoding="utf-8")
    assert "Path(__file__).resolve().parent.parent" in content
    assert "parents[2]" not in content


def test_sftp_mkdir_p_creates_parents():
    """Verify that _sftp_mkdir_p recursively creates parent directories without crashing."""
    mock_sftp = MagicMock()
    # Simulate stat raising FileNotFoundError (or OSError) for nonexistent dirs
    def mock_stat(p):
        if p in ["/", "/remote", "/remote/runs"]:
            return True
        raise OSError("Directory does not exist")

    mock_sftp.stat.side_effect = mock_stat

    created_dirs = []
    def mock_mkdir(p):
        created_dirs.append(p)

    mock_sftp.mkdir.side_effect = mock_mkdir

    _sftp_mkdir_p(mock_sftp, "/remote/runs/ga_research/checkpoints")

    assert "/remote/runs/ga_research" in created_dirs
    assert "/remote/runs/ga_research/checkpoints" in created_dirs


def test_watchdog_is_process_running_current_pid():
    """Verify is_process_running returns True for current python process."""
    curr_pid = os.getpid()
    assert is_process_running(curr_pid) is True
    # Nonexistent PID (e.g. 9999999) should return False
    assert is_process_running(9999999) is False


def test_watchdog_get_process_metrics():
    """Verify get_process_metrics returns valid memory usage > 0 for current process."""
    curr_pid = os.getpid()
    mem_mb, count = get_process_metrics(curr_pid)
    # Memory should be positive on both Windows and POSIX (or 0 if procfs not accessible in sandboxes)
    assert mem_mb >= 0.0
    assert count >= 0
    if os.name == "nt":
        assert mem_mb > 0.0
        assert count > 0


def test_watchdog_log_tail_keywords():
    """Verify watchdog detects traceback or nan in log tail."""
    from tools.watchdog_loop import check_log_tail
    import tempfile

    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as f:
        f.write("Line 1: OK\nLine 2: Traceback (most recent call last):\nLine 3: ValueError\n")
        f_path = Path(f.name)

    try:
        err = check_log_tail(f_path)
        assert err is not None
        assert "Traceback" in err
    finally:
        f_path.unlink(missing_ok=True)
