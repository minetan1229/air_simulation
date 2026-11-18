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
from typing import Any, Dict, Optional

from v3.eval.robustness import RobustnessEvaluator
from v3.origami.adapter import OrigamiAdapter
from v3.physics.benchmark import DeterministicFlightBenchmark
from v3.search.engine import MAPElitesEngine
from v3.search.grid import MAPElitesGridArchive
from v3.trim.nelder_mead import NelderMeadTrimOptimizer


def _load_yaml_config(path_str: str) -> Dict[str, Any]:
    """Lightweight subset YAML parser avoiding external dependencies."""
    p = Path(path_str)
    if not p.exists():
        return {}
    res: Dict[str, Any] = {}
    current_section = res
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                parts = line.split(":", 1)
                k = parts[0].strip()
                v = parts[1].strip()
                if not v:
                    current_section = {}
                    res[k] = current_section
                else:
                    if v.startswith("[") and v.endswith("]"):
                        items = [x.strip() for x in v[1:-1].split(",") if x.strip()]
                        parsed_list: Any = []
                        for itm in items:
                            try:
                                parsed_list.append(float(itm) if "." in itm else int(itm))
                            except ValueError:
                                parsed_list.append(itm)
                        current_section[k] = parsed_list
                    elif v.lower() == "true":
                        current_section[k] = True
                    elif v.lower() == "false":
                        current_section[k] = False
                    else:
                        try:
                            current_section[k] = float(v) if "." in v else int(v)
                        except ValueError:
                            current_section[k] = v.strip('"').strip("'")
    return res


def run_pipeline(
    iterations: int = 50,
    seed: int = 42,
    output_dir: str = "output/v3",
    top_k: int = 3,
    trim_iterations: int = 15,
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
    print(f"  Trim Optimization     : {trim_iterations} iterations")
    print(f"  Output Directory      : {out_path.resolve()}")
    print("=" * 70)

    # Initialize subsystems
    adapter = OrigamiAdapter()
    benchmark = DeterministicFlightBenchmark(duration_s=6.0)
    grid = MAPElitesGridArchive(bins=(8, 8, 8))
    engine = MAPElitesEngine(archive=grid, adapter=adapter, benchmark=benchmark, seed=seed)
    trim_optimizer = NelderMeadTrimOptimizer(adapter=adapter, benchmark=benchmark, max_iter=trim_iterations)
    evaluator = RobustnessEvaluator(duration_s=6.0)

    # Stage 1: MCTS Bootstrap
    print("\n[Stage 1] Bootstrapping 20-step origami skeletons with MCTS...")
    t0 = time.time()
    engine.bootstrap_population(count=4, mcts_iterations=20)
    initial_elites = len(grid.cells)
    print(f"  Initialized {initial_elites} compliant niche elites in {time.time() - t0:.2f}s.")

    # Stage 2: 3D MAP-Elites Illumination
    print("\n[Stage 2] Running 3D MAP-Elites illumination search...")
    jsonl_file = open(jsonl_log_path, "w", encoding="utf-8")

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
    jsonl_file.close()
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
    parser.add_argument("--trim-iter", type=int, default=15, help="Nelder-Mead trim iterations")

    args = parser.parse_args()

    cfg = _load_yaml_config(args.config)
    search_cfg = cfg.get("search", {})
    eval_cfg = cfg.get("evaluation", {})

    iters = args.iterations or int(search_cfg.get("map_elites_iterations", 50))
    seed = args.seed if args.seed is not None else int(search_cfg.get("seed", 42))
    out_dir = args.output_dir or str(eval_cfg.get("output_dir", "output/v3"))
    top_k = args.top_k or int(eval_cfg.get("top_k_export", 3))

    run_pipeline(
        iterations=iters,
        seed=seed,
        output_dir=out_dir,
        top_k=top_k,
        trim_iterations=args.trim_iter,
    )


if __name__ == "__main__":
    main()
