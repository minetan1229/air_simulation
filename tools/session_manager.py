"""Concurrent Antigravity session manager (1 session = 1 isolated Git worktree).

Provides cross-platform (Windows / Linux / macOS) session isolation:
  - Creates dedicated `mission/<slug>[-<issue>]` branch and Git worktree
  - Registers active session metadata atomically under `~/.cache/air_simulation/active_sessions/`
  - Wraps `pytest` with an OS-appropriate isolated `--basetemp` directory
  - Tracks heartbeats and garbage-collects dead session records

Usage:
    python tools/session_manager.py start --slug aero-fix --issue 221
    python tools/session_manager.py pytest -- tests/test_aero.py -q
    python tools/session_manager.py list
    python tools/session_manager.py heartbeat --session <id>
    python tools/session_manager.py finish --session <id> [--remove-worktree]
    python tools/session_manager.py gc
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

WORKSPACE_DIR = Path(__file__).resolve().parent.parent


def default_cache_dir() -> Path:
    override = os.getenv("AIR_SIM_CACHE_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".cache" / "air_simulation"


def active_sessions_dir(cache_dir: Optional[Path] = None) -> Path:
    return (cache_dir or default_cache_dir()) / "active_sessions"


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip().lower()).strip("-")
    return cleaned[:40] or "mission"


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_active_sessions(cache_dir: Optional[Path] = None, alive_only: bool = True) -> List[Dict[str, Any]]:
    sdir = active_sessions_dir(cache_dir)
    if not sdir.is_dir():
        return []
    records: List[Dict[str, Any]] = []
    for jf in sorted(sdir.glob("*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        pid = int(data.get("pid", 0) or 0)
        alive = is_pid_alive(pid)
        data["alive"] = alive
        if alive_only and not alive:
            continue
        records.append(data)
    return records


def register_session(
    session_id: str,
    worktree_path: Path,
    branch: str,
    pid: Optional[int] = None,
    cache_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    now = _utc_now_iso()
    basetemp = Path(tempfile.gettempdir()) / f"pytest_airsim_{session_id}"
    record: Dict[str, Any] = {
        "session_id": session_id,
        "pid": pid if pid is not None else os.getpid(),
        "ppid": os.getppid(),
        "worktree_path": str(worktree_path.resolve()),
        "branch": branch,
        "started_at": now,
        "last_heartbeat": now,
        "pytest_basetemp": str(basetemp),
    }
    _atomic_write_json(active_sessions_dir(cache_dir) / f"{session_id}.json", record)
    return record


def update_heartbeat(session_id: str, cache_dir: Optional[Path] = None) -> bool:
    path = active_sessions_dir(cache_dir) / f"{session_id}.json"
    if not path.is_file():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    data["last_heartbeat"] = _utc_now_iso()
    _atomic_write_json(path, data)
    return True


def unregister_session(session_id: str, cache_dir: Optional[Path] = None) -> bool:
    path = active_sessions_dir(cache_dir) / f"{session_id}.json"
    if path.is_file():
        path.unlink()
        return True
    return False


def gc_sessions(cache_dir: Optional[Path] = None) -> List[str]:
    sdir = active_sessions_dir(cache_dir)
    if not sdir.is_dir():
        return []
    removed: List[str] = []
    for jf in sorted(sdir.glob("*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0) or 0)
        except (OSError, ValueError, json.JSONDecodeError):
            jf.unlink(missing_ok=True)
            removed.append(jf.stem)
            continue
        if not is_pid_alive(pid):
            jf.unlink(missing_ok=True)
            removed.append(str(data.get("session_id", jf.stem)))
    return removed


def _init_session_markdown(worktree: Path, session_id: str, branch: str) -> Path:
    sess_file = worktree / ".agents" / "sessions" / f"{session_id}.md"
    sess_file.parent.mkdir(parents=True, exist_ok=True)
    if not sess_file.exists():
        sess_file.write_text(
            f"# Session State: {session_id}\n\n"
            f"- **Branch**: `{branch}`\n"
            f"- **Worktree**: `{worktree}`\n"
            f"- **Started**: {_utc_now_iso()}\n"
            f"- **Current Phase**: Phase 1 [Sync & Plan]\n"
            f"- **Non-Goals (P2 Frozen)**: (明記すること)\n\n"
            f"## WBS / Todo\n- [ ] (タスク分解を記載)\n\n"
            f"## Rejected Hypotheses / Consultations\n- (なし)\n",
            encoding="utf-8",
        )
    return sess_file


def cmd_start(args: argparse.Namespace) -> int:
    slug = _slugify(args.slug)
    suffix = f"-{args.issue}" if args.issue else ""
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    session_id = f"{slug}{suffix}_{ts}"
    branch = f"mission/{slug}{suffix}"
    wt_root = Path(args.worktree_root) if args.worktree_root else WORKSPACE_DIR.parent / "air_sim_wt"
    worktree_path = (wt_root / session_id).resolve()

    if not args.no_worktree:
        wt_root.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["git", "worktree", "add", "-b", branch, str(worktree_path), args.base],
            cwd=str(WORKSPACE_DIR),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            print(f"[session_manager] git worktree add failed: {proc.stderr.strip()}", file=sys.stderr)
            return proc.returncode
    else:
        worktree_path = WORKSPACE_DIR

    record = register_session(session_id, worktree_path, branch, pid=args.pid)
    sess_md = _init_session_markdown(worktree_path, session_id, branch)

    if args.json:
        print(json.dumps({**record, "session_md": str(sess_md)}, ensure_ascii=False, indent=2))
    else:
        print(f"[session_manager] Started session `{session_id}`")
        print(f"  branch       : {branch}")
        print(f"  worktree     : {worktree_path}")
        print(f"  session_md   : {sess_md}")
        print(f"  basetemp     : {record['pytest_basetemp']}")
        print(f"  run pytest   : python tools/session_manager.py pytest --session {session_id} -- tests -q")
    return 0


def cmd_pytest(args: argparse.Namespace) -> int:
    session_id = args.session or os.getenv("AIR_SIM_SESSION_ID", "").strip() or f"pid_{os.getpid()}"
    basetemp = Path(tempfile.gettempdir()) / f"pytest_airsim_{_slugify(session_id)}"
    pytest_args = list(args.pytest_args)
    if pytest_args and pytest_args[0] == "--":
        pytest_args = pytest_args[1:]
    cmd = [sys.executable, "-m", "pytest", f"--basetemp={basetemp}", *pytest_args]
    return subprocess.run(cmd, check=False).returncode


def cmd_list(args: argparse.Namespace) -> int:
    records = load_active_sessions(alive_only=not args.all)
    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return 0
    if not records:
        print("[session_manager] No active sessions.")
        return 0
    for r in records:
        status = "ALIVE" if r.get("alive") else "DEAD"
        print(f"  [{status}] {r.get('session_id')} pid={r.get('pid')} branch={r.get('branch')} wt={r.get('worktree_path')}")
    return 0


def cmd_finish(args: argparse.Namespace) -> int:
    sdir = active_sessions_dir()
    path = sdir / f"{args.session}.json"
    wt_path: Optional[Path] = None
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("worktree_path"):
                wt_path = Path(data["worktree_path"])
        except (OSError, json.JSONDecodeError):
            pass
    removed = unregister_session(args.session)
    if args.remove_worktree and wt_path and wt_path.exists() and wt_path != WORKSPACE_DIR:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt_path)], cwd=str(WORKSPACE_DIR), check=False)
    print(f"[session_manager] Finished `{args.session}` (unregistered={removed})")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Manage isolated concurrent Antigravity sessions.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="Create isolated worktree & register session")
    p_start.add_argument("--slug", required=True)
    p_start.add_argument("--issue", default="")
    p_start.add_argument("--base", default="origin/master")
    p_start.add_argument("--worktree-root", default="")
    p_start.add_argument("--pid", type=int, default=None)
    p_start.add_argument("--no-worktree", action="store_true", help="Register current workspace without git worktree add")
    p_start.add_argument("--json", action="store_true")

    p_pytest = sub.add_parser("pytest", help="Run pytest with OS-safe isolated --basetemp")
    p_pytest.add_argument("--session", default="")
    p_pytest.add_argument("pytest_args", nargs=argparse.REMAINDER)

    p_list = sub.add_parser("list", help="List active sessions")
    p_list.add_argument("--all", action="store_true", help="Include dead session records")
    p_list.add_argument("--json", action="store_true")

    p_hb = sub.add_parser("heartbeat", help="Update session heartbeat timestamp")
    p_hb.add_argument("--session", required=True)

    p_fin = sub.add_parser("finish", help="Unregister session and optionally remove worktree")
    p_fin.add_argument("--session", required=True)
    p_fin.add_argument("--remove-worktree", action="store_true")

    sub.add_parser("gc", help="Remove stale session records whose PID is no longer alive")

    args = parser.parse_args(argv)
    if args.command == "start":
        return cmd_start(args)
    if args.command == "pytest":
        return cmd_pytest(args)
    if args.command == "list":
        return cmd_list(args)
    if args.command == "heartbeat":
        ok = update_heartbeat(args.session)
        print(f"[session_manager] heartbeat `{args.session}` updated={ok}")
        return 0 if ok else 1
    if args.command == "finish":
        return cmd_finish(args)
    if args.command == "gc":
        removed = gc_sessions()
        print(f"[session_manager] gc removed {len(removed)} stale record(s): {removed}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
