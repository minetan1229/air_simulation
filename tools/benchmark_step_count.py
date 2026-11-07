#!/usr/bin/env python3
"""Benchmark GA performance across different max_fold_steps values."""

from __future__ import annotations

import json
import time
import sys
from pathlib import Path

sys.path.insert(0, '.')
from physics.ga_config import GAConfig, load_config_file
from physics.ga_domain import run_research


def run_benchmark(step_count: int, generations: int = 5, seed: int = 42) -> dict:
    """Run a GA benchmark with specified step count."""
    config_overrides = {
        "objective": "distance",
        "stat_mode": "hybrid_median_mean",
        "max_trials": 10000,
        "top_k": 100,
        "tier1_trials": 8,
        "tier2_trials": 4,
        "tier3_trials": 2,
        "seed": seed,
        "generations": generations,
        "islands": 2,
        "population_size": 8,
        "max_fold_steps": step_count,
        "max_fold_steps_limit": 14,
        "fidelity_mode": "multifidelity",
        "high_fidelity_reval_top_k": 2,
        "processes": 1,
        "out_dir": f"runs/benchmark_step_{step_count}",
        "checkpoint_interval": 1,
    }

    start_time = time.time()
    result = run_research(config_overrides)
    elapsed = time.time() - start_time

    # Extract metrics from result
    best_fitness = 0.0
    avg_fitness = 0.0
    diversity = 0.0

    if result and "islands" in result:
        all_fitness = []
        for island in result["islands"]:
            for ind in island.get("population", []):
                if ind.get("feasible", False):
                    fit = ind.get("fitness", 0.0)
                    all_fitness.append(fit)

        if all_fitness:
            best_fitness = max(all_fitness)
            avg_fitness = sum(all_fitness) / len(all_fitness)

            # Simple diversity metric: std dev of fitness
            import statistics
            if len(all_fitness) > 1:
                diversity = statistics.stdev(all_fitness)

    return {
        "step_count": step_count,
        "generations": generations,
        "best_fitness": best_fitness,
        "avg_fitness": avg_fitness,
        "compute_time_sec": round(elapsed, 2),
        "diversity_metric": round(diversity, 6),
    }


def main():
    step_counts = [3, 5, 8, 11, 14]
    generations = 5
    seed = 42

    print(f"Running benchmark for step counts: {step_counts}")
    print(f"Generations: {generations}, Seed: {seed}")
    print("=" * 60)

    results = []
    for sc in step_counts:
        print(f"\nRunning step_count={sc}...")
        try:
            result = run_benchmark(sc, generations, seed)
            results.append(result)
            print(f"  best_fitness: {result['best_fitness']:.6f}")
            print(f"  avg_fitness:  {result['avg_fitness']:.6f}")
            print(f"  time:         {result['compute_time_sec']:.2f}s")
            print(f"  diversity:    {result['diversity_metric']:.6f}")
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({
                "step_count": sc,
                "generations": generations,
                "best_fitness": 0.0,
                "avg_fitness": 0.0,
                "compute_time_sec": 0.0,
                "diversity_metric": 0.0,
                "error": str(e)
            })

    # Save results
    output_path = Path("runs/benchmark_step_count_results.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    print("\n" + "=" * 60)
    print("BENCHMARK COMPLETE")
    print(f"Results saved to: {output_path}")

    # Print summary table
    print("\n| step_count | generations | best_fitness | avg_fitness | compute_time_sec | diversity_metric |")
    print("|------------|-------------|--------------|-------------|------------------|------------------|")
    for r in results:
        print(f"| {r['step_count']:^10} | {r['generations']:^11} | {r['best_fitness']:.6f} | {r['avg_fitness']:.6f} | {r['compute_time_sec']:^16.2f} | {r['diversity_metric']:.6f} |")


if __name__ == "__main__":
    main()
