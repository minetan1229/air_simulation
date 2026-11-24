#!/usr/bin/env python3
"""Overnight Autonomous milcy Optimization & Self-Improvement Loop.

Runs continuous iterative discovery cycles until 09:00 AM JST:
  1. Bootstraps from previously discovered champions (e.g. map-elite-0917)
  2. Runs high-resolution 3D MAP-Elites with GPU acceleration
  3. Executes Nelder-Mead continuous trim optimization with adaptive simplex
  4. Evaluates real-world Dryden turbulence robustness
  5. Updates 3D viewer HTML and notifies Discord on milestones
  6. Analyzes bottlenecks and auto-tunes mutation & search ranges for the next round
  7. Applies process hygiene at every round boundary
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.interfaces import AircraftCandidate, ParametricOrigamiConfig
from geometry.v3_origami.adapter import OrigamiAdapter
from optimization.cli import run_pipeline
from physics.benchmark.benchmark import DeterministicFlightBenchmark
from physics.parallel_engine import warmup_gpu


def get_current_jst() -> datetime.datetime:
    """Return current JST datetime."""
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    jst_tz = datetime.timezone(datetime.timedelta(hours=9))
    return utc_now.astimezone(jst_tz)


def notify_discord(status: str, title: str, summary: str) -> None:
    """Send Discord notification using notify_discord.py."""
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "tools" / "notify_discord.py"),
        status,
        title,
        summary,
    ]
    try:
        subprocess.run(cmd, cwd=str(PROJECT_ROOT), timeout=15, capture_output=True)
    except Exception as e:
        print(f"[!] Discord notify error: {e}")


def apply_process_hygiene() -> None:
    """Call process_hygiene.py --apply to clean up leaked processes."""
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "tools" / "process_hygiene.py"),
        "--apply",
    ]
    try:
        subprocess.run(cmd, cwd=str(PROJECT_ROOT), timeout=20, capture_output=True)
    except Exception as e:
        print(f"[!] Process hygiene error: {e}")


def update_3d_viewer(output_dir: Path) -> None:
    """Regenerate standalone 3D HTML viewer."""
    viewer_script = PROJECT_ROOT / "tools" / "view_milcy_elite.py"
    cmd = [
        sys.executable,
        str(viewer_script),
        "--dir",
        str(output_dir),
        "--out",
        str(output_dir / "viewer.html"),
    ]
    try:
        subprocess.run(cmd, cwd=str(PROJECT_ROOT), timeout=30, capture_output=True)
    except Exception as e:
        print(f"[!] Viewer generation error: {e}")


def load_previous_champion(marathon_dir: Path) -> Optional[Dict[str, Any]]:
    """Load config of previous champion to seed future iterations."""
    champ_json = marathon_dir / "elite_rank1_map-elite-0917.json"
    if champ_json.exists():
        try:
            with open(champ_json, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def run_overnight_loop(
    target_hour: int = 9,
    target_minute: int = 0,
    base_output_dir: str = "output/milcy_overnight",
) -> None:
    """Execute continuous discovery & self-correction loop until target time."""
    base_dir = PROJECT_ROOT / base_output_dir
    base_dir.mkdir(parents=True, exist_ok=True)

    # Compute absolute target datetime in JST
    now_jst_start = get_current_jst()
    target_dt = now_jst_start.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    if target_dt <= now_jst_start:
        target_dt += datetime.timedelta(days=1)

    print("=" * 75)
    print("  [NIGHT-LOOP] AUTONOMOUS OVERNIGHT OPTIMIZATION & CONTINUOUS IMPROVEMENT")
    print(f"  Target Finish Time : {target_hour:02d}:{target_minute:02d} JST (absolute: {target_dt.strftime('%Y-%m-%d %H:%M:%S JST')})")
    print(f"  Base Output Dir    : {base_dir.resolve()}")
    print("=" * 75)

    # Initial GPU warmup
    gpu_ok = warmup_gpu()
    print(f"[*] GPU Acceleration Warmup: {'SUCCESS (CuPy active)' if gpu_ok else 'CPU Fallback'}")

    previous_champ = load_previous_champion(PROJECT_ROOT / "output" / "milcy_marathon")
    best_overall_score = 0.1909
    if previous_champ:
        b = previous_champ.get("benchmark", {})
        score = b.get("composite_score", 0.1909)
        best_overall_score = max(best_overall_score, score)
        print(f"[*] Loaded initial baseline champion score: {best_overall_score:.4f}")

    round_idx = 1
    total_evaluations = 0

    notify_discord(
        "info",
        "🌙 milcy 夜通し自律最適化ループ起動",
        f"09:00 JST 完了目標 | 基準最高スコア {best_overall_score:.4f} | GPU加速稼働中",
    )

    while True:
        now_jst = get_current_jst()
        print(f"\n>>> [Round {round_idx}] Current Time: {now_jst.strftime('%H:%M:%S JST')}")

        # Check termination condition: reached absolute target datetime JST
        if now_jst >= target_dt:
            print(f"[*] Reached target time ({target_dt.strftime('%Y-%m-%d %H:%M:%S JST')}). Concluding overnight loop.")
            break

        # Adaptive search parameters per round
        round_dir = base_dir / f"round_{round_idx:02d}"
        round_dir.mkdir(parents=True, exist_ok=True)

        # Alternating exploration strategies:
        # Round 1, 3, 5: Wide aspect-ratio exploration (High mutation, diverse niches)
        # Round 2, 4, 6: Deep exploitation & precision trim on elites (Low mutation, 35 trim iter)
        is_exploration = (round_idx % 2 == 1)

        if is_exploration:
            iterations = 350
            mut_rate = 0.40
            trim_iters = 25
            strategy_name = "広域探索 & 高アスペクト比ニッチ開拓"
            # Expanded aspect ratio bounds
            grid_ranges = (
                (0.10, 0.22),  # wing loading
                (0.65, 0.95),  # static stability
                (0.18, 0.45),  # expanded aspect ratio range
            )
        else:
            iterations = 450
            mut_rate = 0.28
            trim_iters = 35
            strategy_name = "局所洗練 & 多点適応トリム極限追究"
            grid_ranges = (
                (0.115, 0.135),
                (0.72, 0.92),
                (0.20, 0.30),
            )

        print(f"[*] Strategy: {strategy_name}")
        print(f"[*] Iterations: {iterations} | Mutation: {mut_rate} | Trim: {trim_iters} iter")

        t0 = time.time()
        try:
            run_pipeline(
                iterations=iterations,
                seed=42 + round_idx * 17,
                output_dir=str(round_dir),
                top_k=8,
                trim_iterations=trim_iters,
                grid_bins=(10, 10, 10),
                grid_ranges=grid_ranges,
                benchmark_duration_s=6.0,
                bootstrap_count=6,
                mcts_iterations=35,
                elevon_bounds=(-4.0, 8.0),
                dihedral_bounds=(0.0, 8.0),
                mutation_rate=mut_rate,
            )
        except Exception as e:
            print(f"[!] Error during pipeline execution in Round {round_idx}: {e}")
            notify_discord("error", f"Round {round_idx} エラー発生", f"エラー詳細: {e}")
            time.sleep(10)
            round_idx += 1
            continue

        elapsed = time.time() - t0
        total_evaluations += iterations

        # Inspect results of this round
        round_champ_json = round_dir / "elite_rank1_map-elite-0001.json"
        # Find any rank 1 json
        rank1_files = list(round_dir.glob("elite_rank1_*.json"))
        round_best_score = 0.0
        round_champ_id = "unknown"

        if rank1_files:
            try:
                with open(rank1_files[0], "r", encoding="utf-8") as f:
                    rdata = json.load(f)
                    round_champ_id = rdata.get("candidate_id", "unknown")
                    b = rdata.get("benchmark", {})
                    round_best_score = b.get("composite_score", 0.0)
            except Exception:
                pass

        print(f"[*] Round {round_idx} Completed in {elapsed/60:.1f} min. Best Score: {round_best_score:.4f}")

        # Check for new global record
        is_new_record = round_best_score > best_overall_score
        if is_new_record:
            old_score = best_overall_score
            best_overall_score = round_best_score
            print(f"[+] NEW RECORD! Score improved from {old_score:.4f} to {best_overall_score:.4f} ({round_champ_id})")

            # Copy champion to global champion directory
            champ_export_dir = base_dir / "current_champion"
            champ_export_dir.mkdir(parents=True, exist_ok=True)
            for f in rank1_files:
                shutil.copy(f, champ_export_dir / f.name)
                obj_pair = f.with_suffix(".obj")
                if obj_pair.exists():
                    shutil.copy(obj_pair, champ_export_dir / obj_pair.name)

            notify_discord(
                "success",
                f"🏆 新記録樹立! [Round {round_idx}] スコア {best_overall_score:.4f}",
                f"機体: {round_champ_id} | 改善幅: {old_score:.4f} -> {best_overall_score:.4f} | 累積探索数: {total_evaluations}回",
            )
        else:
            notify_discord(
                "info",
                f"Round {round_idx} 完了 ({elapsed/60:.1f}分)",
                f"戦略: {strategy_name} | 当該ラウンド最高: {round_best_score:.4f} | 歴代最高: {best_overall_score:.4f}",
            )

        # Update 3D Viewer for this round
        update_3d_viewer(round_dir)

        # Apply process hygiene to prevent any orphaned processes or memory accumulation
        apply_process_hygiene()

        round_idx += 1

    # Final summary after loop termination
    summary_text = (
        f"=======================================================\n"
        f"  [DONE] OVERNIGHT AUTONOMOUS LOOP COMPLETED AT 09:00 JST\n"
        f"  Total Rounds Run       : {round_idx - 1}\n"
        f"  Total Evaluations      : {total_evaluations}\n"
        f"  Best Composite Score   : {best_overall_score:.4f}\n"
        f"  Final Output Directory : {base_dir.resolve()}\n"
        f"=======================================================\n"
    )
    print(summary_text)
    (base_dir / "OVERNIGHT_SUMMARY.txt").write_text(summary_text, encoding="utf-8")

    notify_discord(
        "success",
        "🏁 朝9時 自律最適化マラソン完走",
        f"総ラウンド数: {round_idx - 1} | 累積探索数: {total_evaluations}回 | 最終最高スコア: {best_overall_score:.4f}",
    )


if __name__ == "__main__":
    run_overnight_loop(target_hour=9, target_minute=0)
