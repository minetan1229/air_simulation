#!/usr/bin/env python
"""Benchmark script for Issue #91: Compare search performance and compute time
for GA2 step counts 3, 5, 8 (default), 11, 14."""

from __future__ import annotations

import json
import time
import statistics
from pathlib import Path
import sys

project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from physics.ga2.scaling import ScalingProfile, detect_profile
from physics.ga2.runner import EvolutionRunner


def compute_population_diversity(runner: EvolutionRunner) -> float:
    """Compute population diversity based on behaviour descriptors in the archive."""
    entries = runner.archive.entries()
    if len(entries) < 2:
        return 0.0

    metrics_list = []
    for entry in entries:
        metrics_list.append([
            entry.metrics.get("wing_loading", 0.0),
            entry.metrics.get("aspect_ratio", 0.0),
            entry.metrics.get("fold_count", 0.0),
        ])

    if len(metrics_list) < 2:
        return 0.0

    # Compute average pairwise Euclidean distance in normalized space
    import math
    n = len(metrics_list)
    if n > 50:  # Sample if too large
        import random
        metrics_list = random.sample(metrics_list, 50)
        n = 50

    # Normalize each dimension
    mins = [min(m[i] for m in metrics_list) for i in range(3)]
    maxs = [max(m[i] for m in metrics_list) for i in range(3)]
    spans = [maxs[i] - mins[i] if maxs[i] - mins[i] > 1e-12 else 1.0 for i in range(3)]

    normalized = [
        [(m[i] - mins[i]) / spans[i] for i in range(3)]
        for m in metrics_list
    ]

    total_dist = 0.0
    pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            dist = math.sqrt(sum((normalized[i][k] - normalized[j][k]) ** 2 for k in range(3)))
            total_dist += dist
            pairs += 1

    return total_dist / pairs if pairs > 0 else 0.0


def run_benchmark(max_fold_steps: int, population_size: int = 30, generations: int = 4, seed: int = 42) -> dict:
    """Run a single benchmark with given max_fold_steps."""
    print(f"\n{'='*60}")
    print(f"Running benchmark: max_fold_steps={max_fold_steps}, population={population_size}, generations={generations}")
    print(f"{'='*60}")

    # Use small_long profile with overrides
    profile = ScalingProfile.small_long(device_count=1, device_vram_bytes=8 * 1024**3)
    profile = profile.with_overrides(
        population_size=population_size,
        max_fold_steps=max_fold_steps,
        min_fold_steps=min(3, max_fold_steps),
        islands=1,  # Single island for simpler benchmark
        stage2_passthrough=min(10, population_size),
        stage3_passthrough=0,  # Disable stage3 for faster benchmark
        stage1_chunk_width=population_size,
        stage1_chunk_repeats=1,
    ).validate()

    out_dir = f"runs/benchmark_mfs_{max_fold_steps}"

    start_time = time.perf_counter()

    runner = EvolutionRunner(
        profile=profile,
        out_dir=out_dir,
        seed=seed,
        paper_gsm=100.0,
        stage3_enabled=False,  # Disable stage3 for faster runs
        checkpoint_interval=generations + 1,  # Only checkpoint at end
        surrogate_interval=generations + 1,  # Disable surrogate retraining
    )

    reports = runner.run(generations)

    total_time = time.perf_counter() - start_time

    # Collect metrics
    generations_completed = len(reports)
    best_fitness = max(r.best_fitness for r in reports) if reports else 0.0
    best_fitness_per_gen = [r.best_fitness for r in reports]

    # Population diversity (from final generation archive)
    diversity = compute_population_diversity(runner)

    # Stage timings
    stage_times = {}
    for report in reports:
        for stage, elapsed in report.stage_seconds.items():
            stage_times.setdefault(stage, []).append(elapsed)

    avg_stage_times = {stage: statistics.mean(times) for stage, times in stage_times.items()}
    total_stage_time = sum(sum(times) for times in stage_times.values())

    # Archive and pareto sizes
    final_archive_size = len(runner.archive)
    final_pareto_size = len(runner.pareto)

    # Pass rates
    stage0_passed = [r.stage0_passed for r in reports]
    stage1_passed = [r.stage1_passed for r in reports]
    stage2_passed = [r.stage2_passed for r in reports]
    stage3_passed = [r.stage3_passed for r in reports]

    result = {
        "max_fold_steps": max_fold_steps,
        "population_size": population_size,
        "generations_requested": generations,
        "generations_completed": generations_completed,
        "best_fitness": best_fitness,
        "best_fitness_per_generation": best_fitness_per_gen,
        "total_compute_time_s": total_time,
        "total_stage_time_s": total_stage_time,
        "avg_stage_times_s": avg_stage_times,
        "population_diversity": diversity,
        "final_archive_size": final_archive_size,
        "final_pareto_size": final_pareto_size,
        "stage0_passed_per_gen": stage0_passed,
        "stage1_passed_per_gen": stage1_passed,
        "stage2_passed_per_gen": stage2_passed,
        "stage3_passed_per_gen": stage3_passed,
        "seed": seed,
    }

    print(f"Generations completed: {generations_completed}")
    print(f"Best fitness: {best_fitness:.6f}")
    print(f"Total compute time: {total_time:.2f}s")
    print(f"Population diversity: {diversity:.6f}")
    print(f"Archive size: {final_archive_size}")
    print(f"Pareto size: {final_pareto_size}")
    print(f"Avg stage times: {avg_stage_times}")

    return result


def main():
    # Benchmark parameters
    max_fold_steps_values = [3, 5, 8, 11, 14]
    population_size = 30
    generations = 4
    seed = 42

    print("Issue #91 Benchmark: GA2 max_fold_steps comparison")
    print(f"Population size: {population_size}")
    print(f"Generations: {generations}")
    print(f"Seed: {seed}")
    print(f"max_fold_steps values: {max_fold_steps_values}")

    results = []

    for mfs in max_fold_steps_values:
        try:
            result = run_benchmark(mfs, population_size, generations, seed)
            results.append(result)
        except Exception as e:
            print(f"ERROR running max_fold_steps={mfs}: {e}")
            import traceback
            traceback.print_exc()
            results.append({
                "max_fold_steps": mfs,
                "error": str(e),
            })

    # Save results
    output_file = "benchmark_max_fold_steps_results.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2, default=str)

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"{'mfs':>4} | {'gen':>3} | {'best_fit':>10} | {'time(s)':>8} | {'diversity':>10} | {'archive':>6} | {'pareto':>6}")
    print("-" * 70)
    for r in results:
        if "error" in r:
            print(f"{r['max_fold_steps']:>4} | ERROR: {r['error']}")
        else:
            print(f"{r['max_fold_steps']:>4} | {r['generations_completed']:>3} | {r['best_fitness']:>10.6f} | {r['total_compute_time_s']:>8.2f} | {r['population_diversity']:>10.6f} | {r['final_archive_size']:>6} | {r['final_pareto_size']:>6}")

    print(f"\nResults saved to {output_file}")


if __name__ == "__main__":
    main()
