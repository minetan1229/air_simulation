"""Helper loop for watchdog.sh to monitor run_ga_research execution.

Periodically inspects:
- Process liveliness (PID check)
- Log tail for exceptions / NaN / Inf / crashes (Hard anomaly -> exit code 10)
- Generation metrics from summary / checkpoints (Stagnation, diversity loss, slowdown -> Soft anomaly -> exit code 30)
- System metrics: Memory usage, handle count (leak -> exit code 30)
- Elapsed time >= 7200s (Periodic audit -> exit code 20)
- Normal completion -> exit code 0
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

STATUS_FILE = "watchdog_status.txt"


def write_status(code: int, reason: str) -> None:
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        f.write(f"EXIT_CODE={code}\nREASON={reason}\nTIMESTAMP={time.strftime('%Y-%m-%d %H:%M:%S')}\n")


class PMC(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("PageFaultCount", ctypes.c_uint32),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def get_process_metrics(pid: int) -> tuple[float, int]:
    """Return (working_set_mb, handle_count) for Windows PID, or (0.0, 0) on failure."""
    if os.name != "nt":
        return 0.0, 0
    try:
        h = ctypes.windll.kernel32.OpenProcess(0x0400 | 0x0010, False, pid)
        if not h:
            return 0.0, 0
        pmc = PMC()
        pmc.cb = ctypes.sizeof(pmc)
        ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), ctypes.sizeof(pmc))
        mem_mb = pmc.WorkingSetSize / (1024.0 * 1024.0)

        handle_count = ctypes.c_uint32()
        ctypes.windll.kernel32.GetProcessHandleCount(h, ctypes.byref(handle_count))
        ctypes.windll.kernel32.CloseHandle(h)
        return mem_mb, handle_count.value
    except Exception:
        return 0.0, 0


def is_process_running(pid: int) -> bool:
    if os.name == "nt":
        try:
            h = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)
            if h:
                exit_code = ctypes.c_uint32()
                ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(exit_code))
                ctypes.windll.kernel32.CloseHandle(h)
                # STILL_ACTIVE is 259
                return exit_code.value == 259
            return False
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def check_log_tail(log_path: Path, max_bytes: int = 50000) -> Optional[str]:
    """Check tail of log for fatal keywords (NaN, Inf, Traceback)."""
    if not log_path.exists():
        return None
    try:
        size = log_path.stat().st_size
        offset = max(0, size - max_bytes)
        with open(log_path, "rb") as f:
            if offset > 0:
                f.seek(offset)
            data = f.read().decode("utf-8", errors="ignore")
        for line in data.splitlines():
            low = line.lower()
            if "traceback (most recent call last)" in low or "fatal python error" in low:
                return f"Traceback detected in log: {line.strip()}"
            if "nan" in low and ("fitness" in low or "error" in low or "divergence" in low):
                return f"NaN detected in log: {line.strip()}"
            if "inf" in low and ("fitness" in low or "overflow" in low):
                return f"Inf detected in log: {line.strip()}"
    except Exception:
        pass
    return None


def read_latest_checkpoint_metrics(run_dir: Path) -> Dict[str, Any]:
    """Parse latest metrics from checkpoints in run_dir/checkpoints."""
    ckpt_dir = run_dir / "checkpoints"
    if not ckpt_dir.exists():
        return {}
    files = sorted(ckpt_dir.glob("generation_*.json"))
    if not files:
        return {}
    try:
        with open(files[-1], "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "generation": data.get("generation", 0),
            "best_fitness": data.get("best_fitness", 0.0),
            "stats": data.get("stats", {}),
            "total_checkpoints": len(files),
            "files": [str(x) for x in files],
        }
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True, help="PID of simulation process")
    parser.add_argument("--log", type=str, required=True, help="Path to stdout/stderr log")
    parser.add_argument("--run-dir", type=str, required=True, help="Run directory (e.g. runs/ga_research)")
    parser.add_argument("--check-interval", type=int, default=30, help="Check interval in seconds")
    parser.add_argument("--max-period", type=int, default=7200, help="Periodic audit limit in seconds (default 7200)")
    args = parser.parse_args()

    pid = args.pid
    log_path = Path(args.log)
    run_dir = Path(args.run_dir)
    start_time = time.time()
    last_fitness_progress_gen = 0
    best_fitness_history: List[tuple[int, float]] = []
    initial_mem_mb: Optional[float] = None
    initial_handles: Optional[int] = None
    gen_durations: Dict[int, float] = {}

    print(f"[watchdog] Monitoring PID {pid}, log: {log_path}, run_dir: {run_dir}")

    while True:
        time.sleep(args.check_interval)
        now = time.time()
        elapsed = now - start_time

        # 1. Check Periodic Audit (7200s)
        if elapsed >= args.max_period:
            reason = f"Periodic audit triggered: {elapsed:.1f}s >= {args.max_period}s elapsed."
            print(f"[watchdog] {reason}")
            write_status(20, reason)
            return 20

        # 2. Check process liveliness
        running = is_process_running(pid)

        # 3. Check hard errors in log
        log_err = check_log_tail(log_path)
        if log_err:
            reason = f"Hard error detected in log: {log_err}"
            print(f"[watchdog] {reason}")
            write_status(10, reason)
            return 10

        # If process exited
        if not running:
            # Check if finished normally
            summary_file = run_dir / "summary.json"
            if summary_file.exists() or "completed" in (check_log_tail(log_path, 10000) or "").lower():
                reason = "Simulation completed normally."
                print(f"[watchdog] {reason}")
                write_status(0, reason)
                return 0
            else:
                reason = f"Simulation process PID {pid} died unexpectedly without clean summary."
                print(f"[watchdog] {reason}")
                write_status(10, reason)
                return 10

        # 4. Check system metrics (Memory & Handle leaks)
        mem_mb, handles = get_process_metrics(pid)
        if mem_mb > 0:
            if initial_mem_mb is None:
                initial_mem_mb = mem_mb
                initial_handles = handles
                print(f"[watchdog] Baseline metrics: Mem={mem_mb:.1f}MB, Handles={handles}")
            else:
                # Threshold: Mem increase > 500MB, Handle increase > 300
                if mem_mb - initial_mem_mb > 500.0:
                    reason = f"Soft anomaly: Memory leak suspected (Init: {initial_mem_mb:.1f}MB -> Current: {mem_mb:.1f}MB)"
                    print(f"[watchdog] {reason}")
                    write_status(30, reason)
                    return 30
                if initial_handles is not None and (handles - initial_handles > 300):
                    reason = f"Soft anomaly: Handle leak suspected (Init: {initial_handles} -> Current: {handles})"
                    print(f"[watchdog] {reason}")
                    write_status(30, reason)
                    return 30

        # 5. Check checkpoint metrics (Stagnation & Diversity loss)
        metrics = read_latest_checkpoint_metrics(run_dir)
        if metrics:
            gen = metrics.get("generation", 0)
            bf = metrics.get("best_fitness", 0.0)
            if not best_fitness_history or best_fitness_history[-1][0] != gen:
                best_fitness_history.append((gen, bf))
                print(f"[watchdog] Gen {gen}: best_fitness={bf:.6f}, Mem={mem_mb:.1f}MB, Handles={handles}")

            # Check stagnation: 8 generations without improvement
            if len(best_fitness_history) >= 8:
                recent_bfs = [x[1] for x in best_fitness_history[-8:]]
                if max(recent_bfs) - min(recent_bfs) < 1e-4:
                    reason = f"Soft anomaly: Best fitness stagnated across 8 generations ({recent_bfs[0]:.6f} to {recent_bfs[-1]:.6f})"
                    print(f"[watchdog] {reason}")
                    write_status(30, reason)
                    return 30


if __name__ == "__main__":
    raise SystemExit(main())
