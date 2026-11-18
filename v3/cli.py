"""v3 Headless CUI Entry Point.

Zero-placeholder, strictly headless, pure terminal execution pipeline:
  1. Bootstraps 20-step origami skeletons with MCTS.
  2. Illumination search via 3D MAP-Elites with deterministic 6DoF horizontal benchmark.
  3. Nelder-Mead continuous trim optimization on top elites.
  4. Real-world Dryden turbulence and throw perturbation stress testing.
  5. Exports .obj 3D mesh files, candidate JSONs, and streaming JSONL logs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

from v3.eval.robustness import RobustnessEvaluator
from v3.origami.adapter import OrigamiAdapter
from v3.physics.benchmark import DeterministicFlightBenchmark
from v3.search.engine import MAPElitesEngine
from v3.search.grid import MAPElitesGridArchive
from v3.trim.nelder_mead import NelderMeadTrimOptimizer


def _load_yaml_config(path_str: str) -> Dict[str, Any]:
    """Load configuration YAML file using PyYAML."""
    p = Path(path_str)
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}


def run_pipeline(
    iterations: int = 50,
    seed: int = 42,
    output_dir: str = "output/v3",
    top_k: int = 3,
    trim_iterations: int = 15,
    grid_bins: Tuple[int, int, int] = (10, 10, 10),
    grid_ranges: Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]] = (
        (1.2, 6.5),
        (0.0, 1.0),
        (1.2, 5.5),
    ),
    benchmark_duration_s: float = 6.0,
    bootstrap_count: int = 5,
    mcts_iterations: int = 30,
    elevon_bounds: Tuple[float, float] = (-4.0, 8.0),
    dihedral_bounds: Tuple[float, float] = (0.0, 8.0),
) -> None:
    """Run full headless v3 pipeline."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    jsonl_log_path = out_path / "v3_search.jsonl"

    print("=" * 70)
    print("  AIR SIMULATION v3: CUI AUTONOMOUS ORIGAMI RESEARCH PIPELINE")
    print("=" * 70)
    print(f"  Random Seed           : {seed}")
    print(f"  Search Iterations     : {iterations}")
    print(f"  Grid Bins             : {grid_bins} ({grid_bins[0]*grid_bins[1]*grid_bins[2]} niches)")
    print(f"  Grid Ranges           : {grid_ranges}")
    print(f"  Benchmark Duration    : {benchmark_duration_s}s")
    print(f"  Trim Optimization     : {trim_iterations} iterations")
    print(f"  Output Directory      : {out_path.resolve()}")
    print("=" * 70)

    # Initialize subsystems
    adapter = OrigamiAdapter()
    benchmark = DeterministicFlightBenchmark(duration_s=benchmark_duration_s)
    grid = MAPElitesGridArchive(bins=grid_bins, ranges=grid_ranges)
    engine = MAPElitesEngine(archive=grid, adapter=adapter, benchmark=benchmark, seed=seed)
    trim_optimizer = NelderMeadTrimOptimizer(
        adapter=adapter,
        benchmark=benchmark,
        max_iter=trim_iterations,
        elevon_bounds=elevon_bounds,
        dihedral_bounds=dihedral_bounds,
    )
    evaluator = RobustnessEvaluator(duration_s=benchmark_duration_s)

    # Stage 1: MCTS Bootstrap
    print("\n[Stage 1] Bootstrapping 20-step origami skeletons with MCTS...")
    t0 = time.time()
    engine.bootstrap_population(count=bootstrap_count, mcts_iterations=mcts_iterations)
    initial_elites = len(grid.cells)
    print(f"  Initialized {initial_elites} compliant niche elites in {time.time() - t0:.2f}s.")

    # Stage 2: 3D MAP-Elites Illumination
    print("\n[Stage 2] Running 3D MAP-Elites illumination search...")
    with open(jsonl_log_path, "w", encoding="utf-8") as jsonl_file:
        def progress(it: int, score: float, niches: int) -> None:
            cov = grid.coverage() * 100.0
            sys.stdout.write(
                f"\r  Iteration {it:04d}/{iterations} | Niches: {niches:03d} ({cov:.1f}%) | Best Score: {score:.3f}"
            )
            sys.stdout.flush()

            # Log to JSONL
            entry = {
                "iteration": it,
                "niches_filled": niches,
                "coverage_pct": cov,
                "best_score": score,
                "timestamp": time.time(),
            }
            jsonl_file.write(json.dumps(entry) + "\n")
            jsonl_file.flush()

        engine.run_iterations(iterations=iterations, progress_callback=progress)
    print("\n  MAP-Elites illumination completed.")

    # Stage 3: Nelder-Mead Precision Trim Optimization
    print("\n[Stage 3] Optimizing top elites with Nelder-Mead continuous trim...")
    elites = grid.get_elites()[:top_k]
    trimmed_elites = []

    for rank, elite in enumerate(elites, 1):
        print(f"  Trimming Elite #{rank} ({elite.candidate_id})...")
        trim_res = trim_optimizer.optimize_trim(elite)
        print(
            f"    Score: {trim_res.initial_score:.3f} -> {trim_res.optimized_score:.3f} | "
            f"Elevon: {trim_res.optimal_elevon_deg:.2f} deg | Dihedral: {trim_res.optimal_dihedral_deg:.2f} deg"
        )
        trimmed_elites.append(elite)

    # Stage 4: Real-world Robustness Validation & Export
    print("\n[Stage 4] Performing Stage 4 robustness stress tests and exporting assets...")
    for rank, cand in enumerate(trimmed_elites, 1):
        report = evaluator.evaluate_candidate(cand)
        cand.metadata["robustness_report"] = report.to_dict()

        prefix = f"elite_rank{rank}_{cand.candidate_id}"
        json_file = out_path / f"{prefix}.json"
        obj_file = out_path / f"{prefix}.obj"

        evaluator.export_candidate_json(cand, str(json_file))
        evaluator.export_candidate_obj(cand, str(obj_file))

        score = cand.benchmark.composite_score if cand.benchmark else 0.0
        print(f"  Elite #{rank} [{cand.candidate_id}]:")
        print(f"    Hang-Time Score  : {score:.3f}")
        print(f"    Dryden Survival  : {report.dryden_survival_rate * 100:.0f}%")
        print(f"    Robustness Score : {report.overall_robustness_score:.3f}")
        print(f"    Exported Files   : {json_file.name}, {obj_file.name}")

    print("\n" + "=" * 70)
    print("  ALL V3 STAGES COMPLETED SUCCESSFULLY")
    print(f"  Search Log        : {jsonl_log_path.resolve()}")
    print("=" * 70)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="v3 Autonomous Origami Aircraft CUI Framework")
    parser.add_argument("--config", type=str, default="config/v3_map_elites.yaml", help="Path to config YAML")
    parser.add_argument("--iterations", type=int, default=None, help="Number of search iterations")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--output-dir", type=str, default=None, help="Directory to export outputs")
    parser.add_argument("--top-k", type=int, default=None, help="Number of elite candidates to trim/export")
    parser.add_argument("--trim-iter", type=int, default=None, help="Nelder-Mead trim iterations")

    args = parser.parse_args()

    cfg = _load_yaml_config(args.config)
    search_cfg = cfg.get("search", {})
    grid_cfg = cfg.get("grid", {})
    trim_cfg = cfg.get("trim", {})
    bench_cfg = cfg.get("benchmark", {})
    eval_cfg = cfg.get("evaluation", {})

    iters = args.iterations or int(search_cfg.get("map_elites_iterations", 50))
    seed = args.seed if args.seed is not None else int(search_cfg.get("seed", 42))
    out_dir = args.output_dir or str(eval_cfg.get("output_dir", "output/v3"))
    top_k = args.top_k or int(eval_cfg.get("top_k_export", 3))

    raw_bins = grid_cfg.get("bins", [10, 10, 10])
    grid_bins = (int(raw_bins[0]), int(raw_bins[1]), int(raw_bins[2])) if len(raw_bins) >= 3 else (10, 10, 10)
    
    wl_range = grid_cfg.get("wing_loading_range", [1.2, 6.5])
    st_range = grid_cfg.get("stability_range", [0.0, 1.0])
    ar_range = grid_cfg.get("aspect_ratio_range", [1.2, 5.5])
    grid_ranges = (
        (float(wl_range[0]), float(wl_range[1])),
        (float(st_range[0]), float(st_range[1])),
        (float(ar_range[0]), float(ar_range[1])),
    )

    bench_duration = float(bench_cfg.get("duration_s", 6.0))
    boot_count = int(search_cfg.get("bootstrap_count", 5))
    mcts_iters = int(search_cfg.get("mcts_iterations", 30))

    raw_e_bounds = trim_cfg.get("elevon_bounds_deg", [-4.0, 8.0])
    raw_d_bounds = trim_cfg.get("dihedral_bounds_deg", [0.0, 8.0])
    e_bounds = (float(raw_e_bounds[0]), float(raw_e_bounds[1])) if len(raw_e_bounds) >= 2 else (-4.0, 8.0)
    d_bounds = (float(raw_d_bounds[0]), float(raw_d_bounds[1])) if len(raw_d_bounds) >= 2 else (0.0, 8.0)

    trim_iters = args.trim_iter if args.trim_iter is not None else int(trim_cfg.get("max_iterations", 15))

    run_pipeline(
        iterations=iters,
        seed=seed,
        output_dir=out_dir,
        top_k=top_k,
        trim_iterations=trim_iters,
        grid_bins=grid_bins,
        grid_ranges=grid_ranges,
        benchmark_duration_s=bench_duration,
        bootstrap_count=boot_count,
        mcts_iterations=mcts_iters,
        elevon_bounds=e_bounds,
        dihedral_bounds=d_bounds,
    )


if __name__ == "__main__":
    main()
