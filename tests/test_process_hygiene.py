"""Tests for the process-hygiene criteria (``tools/process_hygiene.py``).

判定基準の正本は ``.agents/skills/process-hygiene.md``。4軸（所有 / 進行 / 冗長 / 接続）
のうち 進行・冗長・接続 の3軸が機械的に実測され、理由文字列と判定へ反映されることを
固定化する。実行環境（実プロセス）への依存は最小限にし、判断ロジックは合成プロセスで検証する。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import process_hygiene as ph  # noqa: E402
from tools.process_hygiene import (  # noqa: E402
    POLICY_IN_USE_STOP,
    POLICY_KEEP,
    Evidence,
    Proc,
    classify,
    collect_evidence,
    command_signature,
)


def _proc(pid: int, cmd: str, ppid: int = 1, name: str = "python.exe") -> Proc:
    return Proc(
        pid=pid,
        ppid=ppid,
        name=name,
        cmd=cmd,
        started="2026-10-21T04:00:00+00:00",
        exe=r"C:\Python311\python.exe",
    )


PY = r'"C:\Users\theor\AppData\Local\Programs\Python\Python311\python.exe"'


# --- 進行軸 -----------------------------------------------------------------


def test_command_signature_normalizes_interpreter_path() -> None:
    a = command_signature(f'{PY} -m pytest tests -q')
    b = command_signature('"C:\\Other\\Python\\python.exe" -m pytest tests -q')
    c = command_signature(f'{PY} -m pytest tests -x -q')
    assert a == b
    assert a != c


def test_evidence_flags_progressing_process() -> None:
    ev = Evidence(progress_cpu_s=1.5, sampled=True)
    assert ev.is_progressing()
    assert not Evidence(progress_cpu_s=1.5, sampled=False).is_progressing()
    assert not Evidence(progress_cpu_s=0.0, sampled=True).is_progressing()


def test_evidence_flags_idle_resident_and_redundancy() -> None:
    assert Evidence(listen=1, established=0).is_idle_resident()
    assert not Evidence(listen=1, established=2).is_idle_resident()
    assert not Evidence(listen=0, established=0).is_idle_resident()
    assert Evidence(duplicates=3).is_redundant()
    assert not Evidence(duplicates=1).is_redundant()


def test_collect_evidence_measures_progress_on_live_processes() -> None:
    """実プロセスで 進行あり と 停滞 を区別できる."""
    busy = subprocess.Popen(
        [sys.executable, "-c", "x=0\nwhile True:\n    x+=1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    idle = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        procs = [
            _proc(busy.pid, f'{PY} -c busy', name="python.exe"),
            _proc(idle.pid, f'{PY} -c idle', name="python.exe"),
        ]
        evidence = collect_evidence(procs, window_s=1.0)
        assert evidence[busy.pid].is_progressing()
        assert not evidence[idle.pid].is_progressing()
    finally:
        for proc in (busy, idle):
            proc.kill()
            proc.wait(timeout=30)


# --- 判定とポリシー ---------------------------------------------------------


def _classify(procs: list[Proc], evidence=None, policy: str = POLICY_IN_USE_STOP):
    live = {p.pid for p in procs}
    return {v.proc.pid: v for v in classify(procs, live, set(), None, evidence, policy)}


def test_host_and_mcp_pool_are_kept() -> None:
    procs = [
        _proc(100, "kilo.exe serve --port 5133", name="kilo.exe"),
        _proc(101, "node .kilo/mcp/launch.mjs playwright", ppid=100, name="node.exe"),
    ]
    verdicts = _classify(procs)
    assert verdicts[100].decision == "KEEP"
    assert verdicts[101].decision == "KEEP"


def test_residual_artifact_and_orphan_browser_are_stopped() -> None:
    procs = [
        _proc(200, f"{PY} app.py --port 5099 --host 127.0.0.1"),
        _proc(
            201,
            r"C:\Users\theor\AppData\Local\ms-playwright\chromium-1248\chrome-win64\chrome.exe --headless",
            name="chrome.exe",
        ),
    ]
    verdicts = _classify(procs)
    assert verdicts[200].decision == "STOP"
    assert verdicts[201].decision == "STOP"


def test_windows_style_mcp_path_is_recognized_as_tool_pool() -> None:
    """`@playwright\\mcp\\cli.js`（Windows の npm パス）も MCP プールと判定する."""
    proc = _proc(
        210,
        r'"C:\Program Files\nodejs\node.exe" .kilo\mcp\node_modules\@playwright\mcp\cli.js --headless',
        name="node.exe",
    )
    assert proc.is_mcp


def test_browser_owned_by_live_mcp_pool_is_kept() -> None:
    """MCP プールが所有するブラウザは孤児扱いにせず保護する."""
    procs = [
        _proc(220, "kilo.exe serve --port 5133", name="kilo.exe"),
        _proc(221, "node .kilo/mcp/launch.mjs playwright", ppid=220, name="node.exe"),
        _proc(
            222,
            r'"C:\Program Files\nodejs\node.exe" .kilo\mcp\node_modules\@playwright\mcp\cli.js --headless',
            ppid=221,
            name="node.exe",
        ),
        _proc(
            223,
            r"C:\Users\theor\AppData\Local\ms-playwright\chromium-1248\chrome-win64\chrome.exe --headless",
            ppid=222,
            name="chrome.exe",
        ),
    ]
    verdicts = _classify(procs)
    assert verdicts[222].decision == "KEEP"
    assert verdicts[223].decision == "KEEP"


def test_default_policy_stops_in_use_processes() -> None:
    """ユーザー指示「使用しているタスクも停止」に対応する既定ポリシー."""
    procs = [_proc(300, f"{PY} -m pytest tests -q")]
    evidence = {300: Evidence(progress_cpu_s=8.0, sampled=True)}
    verdicts = _classify(procs, evidence, POLICY_IN_USE_STOP)
    assert verdicts[300].decision == "STOP"
    assert "使用中" in verdicts[300].reason


def test_keep_policy_protects_in_use_processes() -> None:
    procs = [_proc(301, f"{PY} -m pytest tests -q")]
    evidence = {301: Evidence(progress_cpu_s=8.0, sampled=True)}
    verdicts = _classify(procs, evidence, POLICY_KEEP)
    assert verdicts[301].decision == "KEEP"


def test_keep_policy_still_stops_orchestrator_and_worker() -> None:
    procs = [
        _proc(302, "kilo.exe run --auto --task x", name="kilo.exe"),
        _proc(303, r"C:\orch\supervisor.ps1 -Watch", name="powershell.exe"),
    ]
    evidence = {
        302: Evidence(progress_cpu_s=5.0, sampled=True),
        303: Evidence(progress_cpu_s=5.0, sampled=True),
    }
    verdicts = _classify(procs, evidence, POLICY_KEEP)
    assert verdicts[302].decision == "STOP"
    assert verdicts[303].decision == "STOP"


def test_reason_carries_measured_axes() -> None:
    procs = [_proc(400, f"{PY} -m pytest tests -q")]
    evidence = {
        400: Evidence(
            progress_cpu_s=0.0,
            listen=1,
            established=0,
            duplicates=2,
            sampled=True,
        )
    }
    reason = _classify(procs, evidence)[400].reason
    assert "進行=" in reason
    assert "LISTEN1/EST0" in reason
    assert "冗長=2件" in reason


def test_keep_pid_protects_descendants() -> None:
    """ラッパーを --keep-pid で保護すると子孫も保護される（K3 の継承）."""
    procs = [
        _proc(500, "powershell.exe -NoLogo -NoProfile", name="powershell.exe"),
        _proc(501, f"{PY} -m pytest tests -q", ppid=500),
    ]
    live = {p.pid for p in procs}
    verdicts = {v.proc.pid: v for v in classify(procs, live, {500}, None)}
    assert verdicts[500].decision == "KEEP"
    assert verdicts[501].decision == "KEEP"


def test_self_and_ancestors_are_never_stopped() -> None:
    """自分自身と祖先プロセスは停止対象から除外される."""
    procs = [
        _proc(os.getpid(), f"{PY} tools/process_hygiene.py --apply"),
        _proc(999, f"{PY} -m pytest tests -q"),
    ]
    live = {p.pid for p in procs}
    verdicts = classify(procs, live, set(), None)
    stops = [v for v in verdicts if v.decision == "STOP"]
    protected = ph._ancestor_pids(procs, os.getpid())
    assert all(v.proc.pid not in protected for v in stops)


# --- CLI --------------------------------------------------------------------


def test_cli_exposes_policy_and_progress_window() -> None:
    out = subprocess.run(
        [sys.executable, "tools/process_hygiene.py", "--help"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert out.returncode == 0
    assert "--policy" in out.stdout
    assert "--progress-window" in out.stdout
    assert "in-use-stop" in out.stdout


def test_cli_list_runs_and_reports_stop_count() -> None:
    out = subprocess.run(
        [sys.executable, "tools/process_hygiene.py", "--list", "--progress-window", "0"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    assert out.returncode == 0
    assert "判定: STOP=" in out.stdout


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
