"""Cross-platform GPU exclusion lock for concurrent sessions (Windows / POSIX).

Prevents VRAM OOM crashes when multiple Antigravity sessions run GPU batch
simulations, GA2, or milcy concurrently, while allowing CPU unit tests to run
fully in parallel. Includes stale-PID lock recovery.

Usage:
    python tools/gpu_lock.py --timeout 600 -- python scripts/smoke_test.py
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator, List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.session_manager import default_cache_dir, is_pid_alive


def default_gpu_lock_path(cache_dir: Optional[Path] = None) -> Path:
    return (cache_dir or default_cache_dir()) / "gpu.lock"


def _try_acquire_file(lock_path: Path) -> Optional[int]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(lock_path), flags)
    except FileExistsError:
        return None
    payload = json.dumps({"pid": os.getpid(), "acquired_at": time.time()}).encode("utf-8")
    os.write(fd, payload)
    os.close(fd)
    return os.getpid()


def _recover_stale_lock(lock_path: Path) -> bool:
    if not lock_path.exists():
        return False
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
        owner_pid = int(data.get("pid", 0) or 0)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if owner_pid > 0 and not is_pid_alive(owner_pid):
        try:
            lock_path.unlink()
            return True
        except OSError:
            return False
    return False


@contextlib.contextmanager
def acquire_gpu_lock(
    timeout: float = 600.0,
    poll_interval: float = 0.2,
    lock_path: Optional[Path] = None,
) -> Iterator[Path]:
    """Acquire exclusive GPU execution lock with stale-PID recovery."""
    path = lock_path or default_gpu_lock_path()
    deadline = time.monotonic() + max(0.0, timeout)

    while True:
        if _try_acquire_file(path) is not None:
            break
        if _recover_stale_lock(path):
            continue
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out after {timeout}s waiting for GPU lock: {path}")
        time.sleep(poll_interval)

    try:
        yield path
    finally:
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if int(data.get("pid", -1)) == os.getpid():
                    path.unlink(missing_ok=True)
        except (OSError, ValueError, json.JSONDecodeError):
            path.unlink(missing_ok=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run a command under an exclusive GPU lock.")
    parser.add_argument("--timeout", type=float, default=600.0, help="Seconds to wait for GPU lock")
    parser.add_argument("--lock-file", default="", help="Optional custom lock file path")
    parser.add_argument("cmd", nargs=argparse.REMAINDER, help="Command to run (prefix with --)")
    args = parser.parse_args(argv)

    cmd = list(args.cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("[gpu_lock] ERROR: No command specified. Example: python tools/gpu_lock.py -- python scripts/smoke_test.py", file=sys.stderr)
        return 2

    lpath = Path(args.lock_file) if args.lock_file else None
    try:
        with acquire_gpu_lock(timeout=args.timeout, lock_path=lpath):
            return subprocess.run(cmd, check=False).returncode
    except TimeoutError as exc:
        print(f"[gpu_lock] {exc}", file=sys.stderr)
        return 124


if __name__ == "__main__":
    raise SystemExit(main())
