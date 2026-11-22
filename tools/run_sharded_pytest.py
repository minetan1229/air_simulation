#!/usr/bin/env python3
"""
Dynamic Work-Queue Pytest Runner with Massive Kilo Code Parallel Workers

This script executes pytest test files using a dynamic work queue (work-stealing)
with LPT (Longest Processing Time first) scheduling to maximize CPU efficiency,
eliminating worker idle time. Afterwards, parallel Kilo Code CLI workers
(Nemotron 3 Ultra high) analyze logs, bottlenecks, and summarize test health.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.kilo_subagent import DEFAULT_WORKER_MODEL, execute_kilo_worker, get_kilo_bin


@dataclass
class SingleTestResult:
    test_file: str
    return_code: int
    duration_sec: float
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    warnings: int = 0
    stdout: str = ""


@dataclass
class WorkerSummary:
    worker_id: int
    executed_files: List[str] = field(default_factory=list)
    total_duration_sec: float = 0.0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    warnings: int = 0


def collect_test_files() -> List[str]:
    """Collect all test_*.py files in tests directory."""
    tests_dir = PROJECT_ROOT / "tests"
    test_files = sorted([
        str(p.relative_to(PROJECT_ROOT)).replace("\\", "/")
        for p in tests_dir.rglob("test_*.py")
        if "__pycache__" not in p.parts
    ])
    return test_files


# Known heavy test files or patterns based on execution profile
HEAVY_PATTERNS = [
    "test_cfd_",
    "test_ga2_",
    "test_ga_",
    "test_issue12_",
    "test_ensemble",
    "test_nonlinear_aero",
    "test_long_horizon",
    "test_investigate",
    "test_deformation",
    "test_gpu_kernel",
    "test_optimizer",
    "test_search",
    "test_structural",
    "test_wind_axis",
]


def estimate_test_weight(test_file: str) -> int:
    """
    Estimate computation weight for LPT (Longest Processing Time) sorting.
    Higher weight files run first to prevent late-tail stragglers.
    """
    weight = 10
    for pat in HEAVY_PATTERNS:
        if pat in test_file:
            weight += 100
    try:
        # File size as secondary heuristic
        size = (PROJECT_ROOT / test_file).stat().st_size
        weight += int(size / 500)
    except Exception:
        pass
    return weight


def parse_pytest_summary(output: str) -> Dict[str, int]:
    """Parse pass/fail/skip counts from pytest terminal output."""
    counts = {"passed": 0, "failed": 0, "skipped": 0, "warnings": 0}
    m_pass = re.search(r"(\d+)\s+passed", output)
    if m_pass:
        counts["passed"] = int(m_pass.group(1))
    m_fail = re.search(r"(\d+)\s+failed", output)
    if m_fail:
        counts["failed"] = int(m_fail.group(1))
    m_skip = re.search(r"(\d+)\s+skipped", output)
    if m_skip:
        counts["skipped"] = int(m_skip.group(1))
    m_warn = re.search(r"(\d+)\s+warning", output)
    if m_warn:
        counts["warnings"] = int(m_warn.group(1))
    return counts


def run_test_file(test_file: str) -> SingleTestResult:
    """Execute pytest on a single test file."""
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--tb=short",
        test_file,
    ]

    start_time = time.time()
    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout, _ = proc.communicate()
    duration = time.time() - start_time
    ret = proc.returncode

    counts = parse_pytest_summary(stdout)
    return SingleTestResult(
        test_file=test_file,
        return_code=ret,
        duration_sec=duration,
        passed=counts["passed"],
        failed=counts["failed"],
        skipped=counts["skipped"],
        warnings=counts["warnings"],
        stdout=stdout,
    )


def dynamic_worker(
    worker_id: int,
    work_queue: queue.Queue[Optional[str]],
    results_list: List[SingleTestResult],
    worker_summary: WorkerSummary,
    lock: threading.Lock,
    total_files: int,
    counter_ref: List[int],
    verbose: bool = False,
) -> None:
    """Worker loop that dynamically consumes tests from queue until None."""
    while True:
        try:
            test_file = work_queue.get(block=True, timeout=1.0)
        except queue.Empty:
            break

        if test_file is None:
            work_queue.task_done()
            break

        res = run_test_file(test_file)

        with lock:
            counter_ref[0] += 1
            completed_count = counter_ref[0]
            results_list.append(res)
            worker_summary.executed_files.append(test_file)
            worker_summary.total_duration_sec += res.duration_sec
            worker_summary.passed += res.passed
            worker_summary.failed += res.failed
            worker_summary.skipped += res.skipped
            worker_summary.warnings += res.warnings

            status_icon = "✅" if res.return_code == 0 else "❌"
            progress_pct = (completed_count / total_files) * 100
            print(
                f"[{completed_count:03d}/{total_files:03d} {progress_pct:4.1f}%] "
                f"[W{worker_id:02d} {status_icon}] {test_file:<45} "
                f"({res.duration_sec:5.1f}s | P:{res.passed} F:{res.failed} S:{res.skipped})",
                flush=True,
            )

        work_queue.task_done()


def analyze_diagnostics_with_kilo(
    top_slow: List[SingleTestResult],
    failed_tests: List[SingleTestResult],
    worker_summaries: List[WorkerSummary],
    total_duration: float,
    model: str = DEFAULT_WORKER_MODEL,
    verbose: bool = False,
) -> str:
    """Dispatch Kilo Code CLI worker to analyze performance and failure diagnostics."""
    slow_details = "\n".join([
        f"- `{r.test_file}`: {r.duration_sec:.1f}s (P:{r.passed}, F:{r.failed}, S:{r.skipped})"
        for r in top_slow[:10]
    ])

    fail_details = "None (All tests passed)"
    if failed_tests:
        fail_details = "\n".join([
            f"- `{r.test_file}`: Code {r.return_code}\n  Tail log:\n"
            + "\n".join(r.stdout.strip().splitlines()[-10:])
            for r in failed_tests[:5]
        ])

    worker_balance = "\n".join([
        f"- Worker {w.worker_id:02d}: {len(w.executed_files)} files, {w.total_duration_sec:.1f}s, Pass={w.passed}, Fail={w.failed}"
        for w in worker_summaries
    ])

    prompt = (
        f"あなたはテスト最適化・CI並列化の専門AIワーカーです。以下の動的キューpytest実行結果を分析し、\n"
        f"1. 実行効率とワーカー負荷分散の評価（LPT動的キューの効果）\n"
        f"2. 低速テスト上位（ボトルネック）の分析と今後の高速化提案\n"
        f"3. テスト全体の健全性総合評価\n"
        f"を簡潔かつ論理的なMarkdown（10〜15行程度）で報告してください。\n\n"
        f"【総テスト実行実時間】: {total_duration:.1f}秒\n"
        f"【ワーカー負荷分布】:\n{worker_balance}\n\n"
        f"【最長所要時間テスト Top 10】:\n{slow_details}\n\n"
        f"【失敗テスト状況】:\n{fail_details}"
    )

    try:
        worker_res = execute_kilo_worker(
            prompt=prompt,
            model=model,
            auto_approve=True,
            timeout=120,
            verbose=verbose,
        )
        if worker_res.get("success"):
            return worker_res.get("text", "").strip()
        else:
            return f"Kilo Worker Error: {worker_res.get('error')}"
    except Exception as exc:
        return f"Kilo Worker Exception: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run pytest using dynamic work-queue with Kilo Code audit.")
    parser.add_argument(
        "-w", "--workers",
        type=int,
        default=4,
        help="Number of concurrent test worker processes (default: 4)",
    )
    parser.add_argument(
        "-m", "--model",
        default=DEFAULT_WORKER_MODEL,
        help=f"Kilo Code model (default: {DEFAULT_WORKER_MODEL})",
    )
    parser.add_argument(
        "--no-kilo",
        action="store_true",
        help="Skip Kilo Code analysis",
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default="docs/test_shards_report.md",
        help="Output markdown report file",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose logging",
    )
    args = parser.parse_args()

    files = collect_test_files()
    total_files = len(files)
    print(f"[*] Collected {total_files} test files from tests/")

    # LPT (Longest Processing Time first) Sort:
    # Heavier tests are queued first so no stragglers remain at the end!
    files.sort(key=estimate_test_weight, reverse=True)
    print("[*] Sorted tests by estimated weight (LPT scheduling).")
    print(f"[*] Starting Dynamic Work-Queue with {args.workers} concurrent workers...\n")

    work_queue: queue.Queue[Optional[str]] = queue.Queue()
    for f in files:
        work_queue.put(f)

    # Put sentinel None for each worker
    for _ in range(args.workers):
        work_queue.put(None)

    results_list: List[SingleTestResult] = []
    worker_summaries: List[WorkerSummary] = [WorkerSummary(worker_id=i + 1) for i in range(args.workers)]
    lock = threading.Lock()
    counter_ref = [0]

    start_total = time.time()
    threads: List[threading.Thread] = []

    for i in range(args.workers):
        t = threading.Thread(
            target=dynamic_worker,
            args=(
                i + 1,
                work_queue,
                results_list,
                worker_summaries[i],
                lock,
                total_files,
                counter_ref,
                args.verbose,
            ),
        )
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    total_duration = time.time() - start_total

    total_pass = sum(r.passed for r in results_list)
    total_fail = sum(r.failed for r in results_list)
    total_skip = sum(r.skipped for r in results_list)
    total_warn = sum(r.warnings for r in results_list)
    failed_tests = [r for r in results_list if r.return_code != 0]
    any_failed = len(failed_tests) > 0

    print("\n" + "=" * 65)
    print(f"[*] Dynamic Work-Queue Test Run Completed in {total_duration:.1f}s")
    print(f"[*] Totals: Passed={total_pass}, Failed={total_fail}, Skipped={total_skip}, Warnings={total_warn}")
    print("=" * 65 + "\n")

    # Top slow tests
    results_list.sort(key=lambda r: r.duration_sec, reverse=True)
    top_slow = results_list[:10]
    print("[*] Top 10 Slowest Test Files:")
    for rank, r in enumerate(top_slow, 1):
        print(f"  {rank:2d}. {r.test_file:<45} : {r.duration_sec:5.1f}s (P:{r.passed})")
    print()

    # Kilo Code Analysis
    kilo_analysis = ""
    if not args.no_kilo:
        print(f"[*] Dispatching Kilo Code Worker to analyze efficiency & diagnostics (Model: {args.model})...")
        kilo_start = time.time()
        kilo_analysis = analyze_diagnostics_with_kilo(
            top_slow=top_slow,
            failed_tests=failed_tests,
            worker_summaries=worker_summaries,
            total_duration=total_duration,
            model=args.model,
            verbose=args.verbose,
        )
        print(f"[*] Kilo Code Worker analysis completed in {time.time() - kilo_start:.1f}s\n")
        print("--- Kilo Code Diagnosis ---")
        print(kilo_analysis)
        print("---------------------------\n")

    # Generate Markdown Report
    report_lines = [
        "# Dynamic Work-Queue Pytest & Kilo Code Parallel Audit Report",
        "",
        f"- **Date / Time**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "- **Scheduling Strategy**: Dynamic Work-Queue with LPT (Longest Processing Time first)",
        f"- **Total Test Files**: {total_files}",
        f"- **Active Workers**: {args.workers}",
        f"- **Total Wall-Clock Duration**: {total_duration:.2f}s",
        f"- **Overall Status**: {'FAILED ❌' if any_failed else 'PASSED ✅'}",
        "",
        "## Summary Metrics",
        "| Metric | Count |",
        "|---|---|",
        f"| **Total Passed** | {total_pass} |",
        f"| **Total Failed** | {total_fail} |",
        f"| **Total Skipped** | {total_skip} |",
        f"| **Total Warnings** | {total_warn} |",
        "",
        "## Worker Load Distribution (Work-Stealing Efficiency)",
        "| Worker | Files Done | Active Time (s) | Passed | Failed | Skipped |",
        "|---|---|---|---|---|---|",
    ]

    for w in worker_summaries:
        report_lines.append(
            f"| Worker {w.worker_id:02d} | {len(w.executed_files)} | {w.total_duration_sec:.1f}s | {w.passed} | {w.failed} | {w.skipped} |"
        )

    report_lines.extend([
        "",
        "## Top 10 Longest Running Test Files",
        "| Rank | Test File | Duration | Passed | Failed | Skipped |",
        "|---|---|---|---|---|---|",
    ])
    for rank, r in enumerate(top_slow, 1):
        report_lines.append(
            f"| {rank} | `{r.test_file}` | {r.duration_sec:.1f}s | {r.passed} | {r.failed} | {r.skipped} |"
        )

    if kilo_analysis:
        report_lines.extend([
            "",
            "## Kilo Code Worker Optimization & Health Diagnosis",
            "",
            kilo_analysis,
            "",
        ])

    report_content = "\n".join(report_lines)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report_content, encoding="utf-8")
    print(f"[*] Report saved to {out_path.resolve()}")

    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
