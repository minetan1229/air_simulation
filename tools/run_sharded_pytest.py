#!/usr/bin/env python3
"""
Sharded Pytest Runner with Massive Kilo Code Parallel Workers

This script divides the pytest test suite into N shards, executes them
in parallel, and dispatches Kilo Code CLI workers (Nemotron 3 Ultra high)
in parallel to analyze logs, detect bottlenecks, and summarize results.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.kilo_subagent import DEFAULT_WORKER_MODEL, execute_kilo_worker, get_kilo_bin


@dataclass
class ShardResult:
    shard_id: int
    files: List[str]
    return_code: int
    duration_sec: float
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    warnings: int = 0
    stdout: str = ""
    analysis: str = ""


def collect_test_files() -> List[str]:
    """Collect all test_*.py files in tests directory."""
    tests_dir = PROJECT_ROOT / "tests"
    test_files = sorted([
        str(p.relative_to(PROJECT_ROOT)).replace("\\", "/")
        for p in tests_dir.rglob("test_*.py")
        if "__pycache__" not in p.parts
    ])
    return test_files


def partition_into_shards(items: List[str], num_shards: int) -> List[List[str]]:
    """Partition items across num_shards evenly (round-robin)."""
    shards: List[List[str]] = [[] for _ in range(num_shards)]
    for i, item in enumerate(items):
        shards[i % num_shards].append(item)
    return [s for s in shards if s]


def parse_pytest_summary(output: str) -> Dict[str, int]:
    """Parse pass/fail/skip counts from pytest terminal output."""
    import re
    counts = {"passed": 0, "failed": 0, "skipped": 0, "warnings": 0}
    # Pattern e.g. "15 passed, 2 skipped, 1 warning in 3.45s"
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


def run_single_shard(shard_id: int, files: List[str], verbose: bool = False) -> ShardResult:
    """Execute pytest for a given shard."""
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--tb=short",
    ] + files

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
    if verbose:
        status_sym = "✅" if ret == 0 else "❌"
        print(f"[{status_sym} Shard {shard_id:02d}] Finished in {duration:.1f}s: "
              f"{counts['passed']} passed, {counts['failed']} failed, {counts['skipped']} skipped "
              f"({len(files)} files)")

    return ShardResult(
        shard_id=shard_id,
        files=files,
        return_code=ret,
        duration_sec=duration,
        passed=counts["passed"],
        failed=counts["failed"],
        skipped=counts["skipped"],
        warnings=counts["warnings"],
        stdout=stdout,
    )


def analyze_shard_with_kilo(
    result: ShardResult,
    model: str = DEFAULT_WORKER_MODEL,
    verbose: bool = False,
) -> str:
    """Dispatch Kilo Code CLI worker to analyze shard output."""
    tail_output = "\n".join(result.stdout.strip().splitlines()[-40:])
    file_list = ", ".join(result.files[:5])
    if len(result.files) > 5:
        file_list += f" ... (+{len(result.files)-5} files)"

    prompt = (
        f"あなたはテスト監査・診断を行うAIワーカーです。以下のpytestシャード結果を分析し、"
        f"1. 実行結果の概要（パス/失敗/スキップ/警告）\n"
        f"2. 異常や低速実行、警告の原因と健全性評価\n"
        f"3. 改善提案または特記事項\n"
        f"を簡潔なMarkdown（5〜10行程度）で報告してください。\n\n"
        f"【シャードID】: Shard {result.shard_id:02d}\n"
        f"【対象ファイル】: {file_list}\n"
        f"【所要時間】: {result.duration_sec:.2f}秒\n"
        f"【終了コード】: {result.return_code}\n"
        f"【出力末尾ログ】:\n```\n{tail_output}\n```"
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
    parser = argparse.ArgumentParser(description="Run sharded pytest with Kilo Code workers.")
    parser.add_argument(
        "-n", "--shards",
        type=int,
        default=8,
        help="Number of shards (default: 8)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Concurrent test runner workers (default: 4)",
    )
    parser.add_argument(
        "--kilo-workers",
        type=int,
        default=8,
        help="Concurrent Kilo Code analyzer workers (default: 8)",
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
        default="test_shards_report.md",
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
    shards = partition_into_shards(files, args.shards)
    print(f"[*] Partitioned into {len(shards)} shards (max {args.workers} parallel test workers)")

    # Step 1: Run pytest shards in parallel
    shard_results: List[ShardResult] = []
    start_total = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_shard = {
            executor.submit(run_single_shard, i + 1, shard_files, args.verbose): i + 1
            for i, shard_files in enumerate(shards)
        }
        for future in concurrent.futures.as_completed(future_to_shard):
            shard_id = future_to_shard[future]
            try:
                res = future.result()
                shard_results.append(res)
                print(f"  -> Shard {res.shard_id:02d} completed: "
                      f"Pass={res.passed}, Fail={res.failed}, Skip={res.skipped}, Warn={res.warnings} ({res.duration_sec:.1f}s)")
            except Exception as exc:
                print(f"  -> Shard {shard_id:02d} generated an exception: {exc}", file=sys.stderr)

    total_test_duration = time.time() - start_total
    shard_results.sort(key=lambda r: r.shard_id)

    total_pass = sum(r.passed for r in shard_results)
    total_fail = sum(r.failed for r in shard_results)
    total_skip = sum(r.skipped for r in shard_results)
    total_warn = sum(r.warnings for r in shard_results)
    any_failed = any(r.return_code != 0 for r in shard_results)

    print("\n" + "=" * 60)
    print(f"[*] All Shards Completed in {total_test_duration:.1f}s")
    print(f"[*] Totals: Passed={total_pass}, Failed={total_fail}, Skipped={total_skip}, Warnings={total_warn}")
    print("=" * 60 + "\n")

    # Step 2: Parallel Kilo Code Analysis (Massive concurrency)
    if not args.no_kilo:
        print(f"[*] Dispatching {len(shard_results)} Kilo Code workers in parallel (Model: {args.model})...")
        kilo_start = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.kilo_workers) as kilo_executor:
            kilo_futures = {
                kilo_executor.submit(analyze_shard_with_kilo, r, args.model, args.verbose): r
                for r in shard_results
            }
            for future in concurrent.futures.as_completed(kilo_futures):
                r = kilo_futures[future]
                try:
                    analysis = future.result()
                    r.analysis = analysis
                    print(f"  [✓ Kilo Worker] Analyzed Shard {r.shard_id:02d}")
                except Exception as exc:
                    r.analysis = f"Analysis failed: {exc}"
                    print(f"  [✗ Kilo Worker] Shard {r.shard_id:02d} failed: {exc}", file=sys.stderr)
        kilo_duration = time.time() - kilo_start
        print(f"[*] All Kilo Code Workers completed in {kilo_duration:.1f}s\n")

    # Step 3: Generate Comprehensive Report
    report_lines = [
        "# Sharded Pytest & Kilo Code Parallel Audit Report",
        "",
        f"- **Date / Time**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **Total Test Files**: {total_files}",
        f"- **Shards**: {len(shards)}",
        f"- **Parallel Test Duration**: {total_test_duration:.2f}s",
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
        "## Shard Details",
        "| Shard | Files | Duration | Status | Passed | Failed | Skipped |",
        "|---|---|---|---|---|---|---|",
    ]

    for r in shard_results:
        status_icon = "✅" if r.return_code == 0 else "❌"
        report_lines.append(
            f"| Shard {r.shard_id:02d} | {len(r.files)} | {r.duration_sec:.1f}s | {status_icon} | {r.passed} | {r.failed} | {r.skipped} |"
        )

    if not args.no_kilo:
        report_lines.extend(["", "## Kilo Code Worker Diagnoses", ""])
        for r in shard_results:
            report_lines.extend([
                f"### Shard {r.shard_id:02d} Analysis",
                f"- **Files**: {', '.join(r.files)}",
                "",
                r.analysis if r.analysis else "No analysis available.",
                "",
            ])

    report_content = "\n".join(report_lines)
    out_path = Path(args.output)
    out_path.write_text(report_content, encoding="utf-8")
    print(f"[*] Report saved to {out_path.resolve()}")

    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
