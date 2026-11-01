"""
Automated Benchmarking Suite for ARC, LRU, and FIFO.
Executes reproducible, standardized experiments across four workload patterns:
  1. Sequential Scan (10 working-set sizes: 4, 8, 12, 16, 20, 24, 32, 48, 64, 96)
  2. Cyclic Looping (10 loop sizes: 4, 8, 12, 15, 16, 17, 18, 20, 24, 32)
  3. Uniform Random (10 deterministic seeds: 101-110, num_pages=64)
  4. Repeated Locality (10 deterministic seeds: 201-210, Two-Tier Bimodal 80/20 Partition)

Generates:
  - docs/experiments/benchmark_results.csv (120 raw per-trial records)
  - docs/experiments/benchmark_summary.csv (66 aggregated records: 30 sequential + 30 looping + 3 random + 3 locality)
"""
import csv
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.analysis.comparator import compare_algorithms
from backend.simulation.workload import generate_workload


def run_all_benchmarks():
    print("=" * 80)
    print("Starting Standardized Benchmarks for ARC, LRU, and FIFO")
    print("=" * 80)

    output_dir = Path("docs/experiments")
    output_dir.mkdir(parents=True, exist_ok=True)

    results_csv_path = output_dir / "benchmark_results.csv"
    summary_csv_path = output_dir / "benchmark_summary.csv"

    # Common parameters
    standard_length = 1000
    standard_capacity = 16

    # Experiment Definitions: (workload_type, trial_id, params, seed_label, config_desc)
    experiments: List[Dict[str, Any]] = []

    # 1. Sequential Scan (10 distinct working-set sizes)
    seq_page_counts = [4, 8, 12, 16, 20, 24, 32, 48, 64, 96]
    for idx, num_p in enumerate(seq_page_counts, start=1):
        experiments.append({
            "workload_type": "sequential",
            "trial_id": idx,
            "params": {"length": standard_length, "num_pages": num_p},
            "seed": "N/A",
            "config_desc": f"num_pages={num_p}"
        })

    # 2. Cyclic Looping (10 distinct loop sizes around capacity threshold)
    loop_sizes = [4, 8, 12, 15, 16, 17, 18, 20, 24, 32]
    for idx, loop in enumerate(loop_sizes, start=1):
        experiments.append({
            "workload_type": "looping",
            "trial_id": idx,
            "params": {"length": standard_length, "loop_size": loop},
            "seed": "N/A",
            "config_desc": f"loop_size={loop}"
        })

    # 3. Uniform Random (10 trials with distinct seeds)
    rand_seeds = [101, 102, 103, 104, 105, 106, 107, 108, 109, 110]
    for idx, seed in enumerate(rand_seeds, start=1):
        experiments.append({
            "workload_type": "random",
            "trial_id": idx,
            "params": {"length": standard_length, "num_pages": 64, "seed": seed},
            "seed": str(seed),
            "config_desc": f"num_pages=64, seed={seed}"
        })

    # 4. Repeated Locality (10 trials with distinct seeds, Two-Tier Bimodal 80/20 Partition)
    loc_seeds = [201, 202, 203, 204, 205, 206, 207, 208, 209, 210]
    for idx, seed in enumerate(loc_seeds, start=1):
        experiments.append({
            "workload_type": "repeated_locality",
            "trial_id": idx,
            "params": {
                "length": standard_length,
                "num_pages": 64,
                "hot_pages_count": 8,
                "hot_prob": 0.8,
                "seed": seed
            },
            "seed": str(seed),
            "config_desc": f"num_pages=64, hot=8, prob=0.8, seed={seed}"
        })

    # List to hold individual trial records
    individual_rows: List[Dict[str, Any]] = []

    # Execute all 40 trials
    for exp in experiments:
        w_type = exp["workload_type"]
        t_id = exp["trial_id"]
        params = exp["params"]
        seed_str = exp["seed"]
        cfg_desc = exp["config_desc"]

        # Generate reference string using existing workload generator
        ref_string = generate_workload(w_type, **params)
        assert len(ref_string) == standard_length

        # Run multi-algorithm benchmark using existing comparator
        comp_output = compare_algorithms(ref_string, capacity=standard_capacity)
        results = comp_output["results"]
        winners = comp_output["best_algorithms"]
        is_tie = comp_output["is_tie"]

        for algo_name in ["ARC", "LRU", "FIFO"]:
            algo_res = results[algo_name]
            is_winner = (algo_name in winners)
            is_sole_winner = (is_winner and not is_tie)

            individual_rows.append({
                "workload_type": w_type,
                "trial_id": t_id,
                "config_desc": cfg_desc,
                "seed": seed_str,
                "capacity": standard_capacity,
                "length": standard_length,
                "algorithm": algo_name,
                "page_faults": algo_res["page_faults"],
                "hits": algo_res["hits"],
                "hit_ratio": algo_res["hit_ratio"],
                "miss_ratio": algo_res["miss_ratio"],
                "replacements": algo_res["replacements"],
                "execution_time_ms": algo_res["execution_time_ms"],
                "is_winner": is_winner,
                "is_sole_winner": is_sole_winner,
                "is_tie": is_tie
            })

    print(f"Total individual algorithm executions completed: {len(individual_rows)}")

    # Write docs/experiments/benchmark_results.csv (120 data rows)
    results_fieldnames = [
        "workload_type",
        "trial_id",
        "config_desc",
        "seed",
        "capacity",
        "length",
        "algorithm",
        "page_faults",
        "hits",
        "hit_ratio",
        "miss_ratio",
        "replacements",
        "execution_time_ms",
        "is_winner",
        "is_sole_winner",
        "is_tie"
    ]

    with open(results_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=results_fieldnames)
        writer.writeheader()
        writer.writerows(individual_rows)

    print(f"Saved: {results_csv_path} ({len(individual_rows)} records)")

    # -------------------------------------------------------------------------
    # Build Correct Aggregation for docs/experiments/benchmark_summary.csv:
    # 1. Sequential Scan: 10 configuration rows x 3 algorithms = 30 rows
    # 2. Cyclic Looping: 10 configuration rows x 3 algorithms = 30 rows
    # 3. Uniform Random: Seed-aggregated across 10 seeds x 3 algorithms = 3 rows
    # 4. Repeated Locality: Seed-aggregated across 10 seeds x 3 algorithms = 3 rows
    # Total: 66 rows
    # -------------------------------------------------------------------------
    summary_rows: List[Dict[str, Any]] = []

    # 1. Sequential Scan (Disaggregated by working-set size)
    for num_p in seq_page_counts:
        cfg = f"num_pages={num_p}"
        cfg_rows = [r for r in individual_rows if r["workload_type"] == "sequential" and r["config_desc"] == cfg]
        for algo in ["ARC", "LRU", "FIFO"]:
            row = next(r for r in cfg_rows if r["algorithm"] == algo)
            summary_rows.append({
                "workload_type": "sequential",
                "config_desc": cfg,
                "algorithm": algo,
                "trials": 1,
                "capacity": standard_capacity,
                "length": standard_length,
                "avg_page_faults": round(float(row["page_faults"]), 2),
                "std_page_faults": 0.0,
                "min_page_faults": row["page_faults"],
                "max_page_faults": row["page_faults"],
                "avg_hits": round(float(row["hits"]), 2),
                "avg_hit_ratio": round(float(row["hit_ratio"]), 4),
                "avg_replacements": round(float(row["replacements"]), 2),
                "avg_exec_time_ms": round(float(row["execution_time_ms"]), 3),
                "total_wins": 1 if row["is_winner"] else 0,
                "sole_wins": 1 if row["is_sole_winner"] else 0,
                "tied_wins": 1 if (row["is_winner"] and row["is_tie"]) else 0
            })

    # 2. Cyclic Looping (Disaggregated by loop size)
    for loop in loop_sizes:
        cfg = f"loop_size={loop}"
        cfg_rows = [r for r in individual_rows if r["workload_type"] == "looping" and r["config_desc"] == cfg]
        for algo in ["ARC", "LRU", "FIFO"]:
            row = next(r for r in cfg_rows if r["algorithm"] == algo)
            summary_rows.append({
                "workload_type": "looping",
                "config_desc": cfg,
                "algorithm": algo,
                "trials": 1,
                "capacity": standard_capacity,
                "length": standard_length,
                "avg_page_faults": round(float(row["page_faults"]), 2),
                "std_page_faults": 0.0,
                "min_page_faults": row["page_faults"],
                "max_page_faults": row["page_faults"],
                "avg_hits": round(float(row["hits"]), 2),
                "avg_hit_ratio": round(float(row["hit_ratio"]), 4),
                "avg_replacements": round(float(row["replacements"]), 2),
                "avg_exec_time_ms": round(float(row["execution_time_ms"]), 3),
                "total_wins": 1 if row["is_winner"] else 0,
                "sole_wins": 1 if row["is_sole_winner"] else 0,
                "tied_wins": 1 if (row["is_winner"] and row["is_tie"]) else 0
            })

    # 3. Uniform Random (Seed-aggregated across 10 deterministic seeds)
    rand_rows = [r for r in individual_rows if r["workload_type"] == "random"]
    for algo in ["ARC", "LRU", "FIFO"]:
        algo_rows = [r for r in rand_rows if r["algorithm"] == algo]
        n_trials = len(algo_rows)
        faults = [r["page_faults"] for r in algo_rows]
        hits = [r["hits"] for r in algo_rows]
        hit_ratios = [r["hit_ratio"] for r in algo_rows]
        replacements = [r["replacements"] for r in algo_rows]
        exec_times = [r["execution_time_ms"] for r in algo_rows]

        mean_faults = sum(faults) / n_trials
        std_faults = math.sqrt(sum((x - mean_faults) ** 2 for x in faults) / (n_trials - 1)) if n_trials > 1 else 0.0

        summary_rows.append({
            "workload_type": "random",
            "config_desc": "num_pages=64 (10 seeds: 101-110)",
            "algorithm": algo,
            "trials": n_trials,
            "capacity": standard_capacity,
            "length": standard_length,
            "avg_page_faults": round(mean_faults, 2),
            "std_page_faults": round(std_faults, 2),
            "min_page_faults": min(faults),
            "max_page_faults": max(faults),
            "avg_hits": round(sum(hits) / n_trials, 2),
            "avg_hit_ratio": round(sum(hit_ratios) / n_trials, 4),
            "avg_replacements": round(sum(replacements) / n_trials, 2),
            "avg_exec_time_ms": round(sum(exec_times) / n_trials, 3),
            "total_wins": sum(1 for r in algo_rows if r["is_winner"]),
            "sole_wins": sum(1 for r in algo_rows if r["is_sole_winner"]),
            "tied_wins": sum(1 for r in algo_rows if r["is_winner"] and r["is_tie"])
        })

    # 4. Repeated Locality (Seed-aggregated across 10 deterministic seeds)
    loc_rows = [r for r in individual_rows if r["workload_type"] == "repeated_locality"]
    for algo in ["ARC", "LRU", "FIFO"]:
        algo_rows = [r for r in loc_rows if r["algorithm"] == algo]
        n_trials = len(algo_rows)
        faults = [r["page_faults"] for r in algo_rows]
        hits = [r["hits"] for r in algo_rows]
        hit_ratios = [r["hit_ratio"] for r in algo_rows]
        replacements = [r["replacements"] for r in algo_rows]
        exec_times = [r["execution_time_ms"] for r in algo_rows]

        mean_faults = sum(faults) / n_trials
        std_faults = math.sqrt(sum((x - mean_faults) ** 2 for x in faults) / (n_trials - 1)) if n_trials > 1 else 0.0

        summary_rows.append({
            "workload_type": "repeated_locality",
            "config_desc": "num_pages=64, hot=8, prob=0.8 (10 seeds: 201-210)",
            "algorithm": algo,
            "trials": n_trials,
            "capacity": standard_capacity,
            "length": standard_length,
            "avg_page_faults": round(mean_faults, 2),
            "std_page_faults": round(std_faults, 2),
            "min_page_faults": min(faults),
            "max_page_faults": max(faults),
            "avg_hits": round(sum(hits) / n_trials, 2),
            "avg_hit_ratio": round(sum(hit_ratios) / n_trials, 4),
            "avg_replacements": round(sum(replacements) / n_trials, 2),
            "avg_exec_time_ms": round(sum(exec_times) / n_trials, 3),
            "total_wins": sum(1 for r in algo_rows if r["is_winner"]),
            "sole_wins": sum(1 for r in algo_rows if r["is_sole_winner"]),
            "tied_wins": sum(1 for r in algo_rows if r["is_winner"] and r["is_tie"])
        })

    # Write docs/experiments/benchmark_summary.csv (66 data rows)
    summary_fieldnames = [
        "workload_type",
        "config_desc",
        "algorithm",
        "trials",
        "capacity",
        "length",
        "avg_page_faults",
        "std_page_faults",
        "min_page_faults",
        "max_page_faults",
        "avg_hits",
        "avg_hit_ratio",
        "avg_replacements",
        "avg_exec_time_ms",
        "total_wins",
        "sole_wins",
        "tied_wins"
    ]

    with open(summary_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=summary_fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)

    print(f"Saved: {summary_csv_path} ({len(summary_rows)} records)")

    # -------------------------------------------------------------------------
    # Display Formatted Terminal Tables
    # -------------------------------------------------------------------------
    print("\n" + "=" * 92)
    print("1. SEQUENTIAL SCAN BENCHMARK (Capacity c = 16, Length N = 1000)")
    print("=" * 92)
    print(f"{'Working Set (M)':<16} | {'Algo':<5} | {'Faults':<8} | {'Hits':<8} | {'Hit Ratio':<10} | {'Replacements':<12} | {'Winner':<8}")
    print("-" * 92)
    for sr in [r for r in summary_rows if r["workload_type"] == "sequential"]:
        print(f"{sr['config_desc']:<16} | {sr['algorithm']:<5} | {int(sr['avg_page_faults']):<8} | {int(sr['avg_hits']):<8} | {sr['avg_hit_ratio']*100:<9.2f}% | {int(sr['avg_replacements']):<12} | {'Tie (All)' if sr['total_wins'] else 'No':<8}")

    print("\n" + "=" * 92)
    print("2. CYCLIC LOOPING BENCHMARK (Capacity c = 16, Length N = 1000)")
    print("=" * 92)
    print(f"{'Loop Size (L)':<16} | {'Algo':<5} | {'Faults':<8} | {'Hits':<8} | {'Hit Ratio':<10} | {'Replacements':<12} | {'Winner':<8}")
    print("-" * 92)
    for sr in [r for r in summary_rows if r["workload_type"] == "looping"]:
        print(f"{sr['config_desc']:<16} | {sr['algorithm']:<5} | {int(sr['avg_page_faults']):<8} | {int(sr['avg_hits']):<8} | {sr['avg_hit_ratio']*100:<9.2f}% | {int(sr['avg_replacements']):<12} | {'Tie (All)' if sr['total_wins'] else 'No':<8}")

    print("\n" + "=" * 92)
    print("3. UNIFORM RANDOM BENCHMARK (num_pages = 64, c = 16, N = 1000, 10 Seeds: 101-110)")
    print("=" * 92)
    print(f"{'Algorithm':<10} | {'Mean Faults':<12} | {'Std Dev':<9} | {'Min-Max Faults':<16} | {'Mean Hit %':<11} | {'Total Wins':<10} | {'Sole Wins':<9}")
    print("-" * 92)
    for sr in [r for r in summary_rows if r["workload_type"] == "random"]:
        print(f"{sr['algorithm']:<10} | {sr['avg_page_faults']:<12.1f} | {sr['std_page_faults']:<9.2f} | {sr['min_page_faults']}-{sr['max_page_faults']:<12} | {sr['avg_hit_ratio']*100:<10.2f}% | {sr['total_wins']:<10} | {sr['sole_wins']:<9}")

    print("\n" + "=" * 92)
    print("4. REPEATED LOCALITY BENCHMARK (Two-Tier Bimodal 80/20, c = 16, N = 1000, 10 Seeds: 201-210)")
    print("=" * 92)
    print(f"{'Algorithm':<10} | {'Mean Faults':<12} | {'Std Dev':<9} | {'Min-Max Faults':<16} | {'Mean Hit %':<11} | {'Total Wins':<10} | {'Sole Wins':<9}")
    print("-" * 92)
    for sr in [r for r in summary_rows if r["workload_type"] == "repeated_locality"]:
        print(f"{sr['algorithm']:<10} | {sr['avg_page_faults']:<12.1f} | {sr['std_page_faults']:<9.2f} | {sr['min_page_faults']}-{sr['max_page_faults']:<12} | {sr['avg_hit_ratio']*100:<10.2f}% | {sr['total_wins']:<10} | {sr['sole_wins']:<9}")
    print("=" * 92)


if __name__ == "__main__":
    run_all_benchmarks()
