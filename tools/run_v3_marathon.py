"""Continuous Marathon Runner and Watchdog for v3 MAP-Elites Research.

Runs long-duration or continuous batch iterations, handles exceptions gracefully,
records telemetry to JSONL, and auto-restarts upon unrecoverable crashes.
"""

from __future__ import annotations

import argparse
import datetime
import os
import subprocess
import sys
import time
from pathlib import Path


def log_event(log_file: Path, message: str) -> None:
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now_str}] {message}\n"
    sys.stdout.write(line)
    sys.stdout.flush()
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def run_marathon(
    total_iterations: int = 10000,
    batch_size: int = 500,
    base_seed: int = 42,
    output_dir: str = "output/v3_marathon",
    max_restarts: int = 50,
) -> None:
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    watchdog_log = out_path / "marathon_watchdog.log"

    log_event(watchdog_log, "=== STARTING V3 MARATHON SEARCH WATCHDOG ===")
    log_event(watchdog_log, f"Target Iterations: {total_iterations} (Batch Size: {batch_size})")
    log_event(watchdog_log, f"Output Directory : {out_path.resolve()}")

    completed_batches = 0
    restarts = 0
    current_seed = base_seed

    while completed_batches * batch_size < total_iterations:
        batch_num = completed_batches + 1
        current_iters = min(batch_size, total_iterations - completed_batches * batch_size)
        batch_out_dir = out_path / f"batch_{batch_num:03d}"

        cmd = [
            sys.executable,
            "-m",
            "v3.cli",
            "--iterations",
            str(current_iters),
            "--seed",
            str(current_seed),
            "--output-dir",
            str(batch_out_dir),
            "--top-k",
            "5",
        ]

        log_event(
            watchdog_log,
            f"Launching Batch #{batch_num} ({current_iters} iters, seed={current_seed}) -> {batch_out_dir}",
        )

        t_start = time.time()
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            # Stream output
            last_line = ""
            if proc.stdout:
                for line in proc.stdout:
                    clean = line.strip()
                    if clean:
                        last_line = clean
                        if "Iteration" in clean or "Stage" in clean or "Elite #" in clean:
                            log_event(watchdog_log, f"  [B#{batch_num}] {clean}")

            ret_code = proc.wait()
            elapsed = time.time() - t_start

            if ret_code == 0:
                log_event(
                    watchdog_log,
                    f"Batch #{batch_num} COMPLETED successfully in {elapsed:.1f}s. (Last: {last_line})",
                )
                completed_batches += 1
                current_seed += 1
                restarts = 0  # reset consecutive restart count
            else:
                log_event(
                    watchdog_log,
                    f"WARNING: Batch #{batch_num} failed with return code {ret_code}. Output snippet: {last_line}",
                )
                restarts += 1
                current_seed += 100  # Shift seed to avoid persistent defect state
                if restarts > max_restarts:
                    log_event(watchdog_log, f"ERROR: Exceeded max consecutive restarts ({max_restarts}). Halting.")
                    break
                log_event(watchdog_log, f"Auto-restarting in 3 seconds... (Restart attempt {restarts})")
                time.sleep(3)

        except Exception as exc:
            log_event(watchdog_log, f"EXCEPTION during Batch #{batch_num}: {exc}")
            restarts += 1
            current_seed += 100
            if restarts > max_restarts:
                log_event(watchdog_log, "ERROR: Exceeded max restarts after exception. Halting.")
                break
            time.sleep(3)

    log_event(
        watchdog_log,
        f"=== MARATHON COMPLETE: Finished {completed_batches} batches ({completed_batches * batch_size} iters) ===",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="v3 Marathon Search Watchdog")
    parser.add_argument("--total-iterations", type=int, default=5000, help="Total iterations to run across batches")
    parser.add_argument("--batch-size", type=int, default=250, help="Iterations per batch run")
    parser.add_argument("--seed", type=int, default=42, help="Base seed")
    parser.add_argument("--output-dir", type=str, default="output/v3_marathon", help="Output directory")
    args = parser.parse_args()

    run_marathon(
        total_iterations=args.total_iterations,
        batch_size=args.batch_size,
        base_seed=args.seed,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
