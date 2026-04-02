#!/usr/bin/env python3
"""Analyze and compare results across experiments.

Scans experiment log directories for results.json files and produces a
comparison summary table.

Usage:
    uv run python scripts/analyze_results.py experiments/qwen35-swe/logs/
    uv run python scripts/analyze_results.py experiments/qwen35-swe/logs/ --output experiments/qwen35-swe/results/summary.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml


def load_experiment(log_dir: Path) -> dict | None:
    results_path = log_dir / "results.json"
    config_path = log_dir / "experiment_config.yaml"

    if not results_path.exists():
        return None

    results = json.loads(results_path.read_text())

    cfg = {}
    if config_path.exists():
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}

    total = len(results)
    passed = sum(1 for r in results if r.get("success"))
    rate = passed / total if total > 0 else 0.0

    avg_elapsed = sum(r.get("elapsed", 0) for r in results) / total if total else 0

    return {
        "experiment_id": cfg.get("experiment_id", log_dir.name),
        "description": cfg.get("description", ""),
        "algorithm": cfg.get("algorithm", ""),
        "no_evolve": cfg.get("no_evolve", False),
        "total": total,
        "passed": passed,
        "resolve_rate": rate,
        "avg_elapsed_s": round(avg_elapsed, 1),
    }


def main():
    p = argparse.ArgumentParser(description="Analyze experiment results")
    p.add_argument("logs_dir", type=str, help="Parent directory containing experiment log dirs")
    p.add_argument("--output", type=str, help="Write markdown summary to file")
    args = p.parse_args()

    logs_dir = Path(args.logs_dir)
    if not logs_dir.exists():
        print(f"Directory not found: {logs_dir}")
        sys.exit(1)

    experiments = []
    for d in sorted(logs_dir.iterdir()):
        if d.is_dir():
            exp = load_experiment(d)
            if exp:
                experiments.append(exp)

    if not experiments:
        print("No results found.")
        return

    # Print table
    header = f"{'Experiment':<30} {'Algorithm':<18} {'Evolve':<8} {'Passed':>8} {'Total':>7} {'Rate':>8} {'Avg(s)':>8}"
    sep = "-" * len(header)

    lines = [header, sep]
    for e in experiments:
        evolve_str = "No" if e["no_evolve"] else "Yes"
        line = (
            f"{e['experiment_id']:<30} "
            f"{e['algorithm']:<18} "
            f"{evolve_str:<8} "
            f"{e['passed']:>8} "
            f"{e['total']:>7} "
            f"{e['resolve_rate']:>7.1%} "
            f"{e['avg_elapsed_s']:>8}"
        )
        lines.append(line)

    # Delta vs first baseline
    baselines = [e for e in experiments if e["no_evolve"]]
    evolved = [e for e in experiments if not e["no_evolve"]]
    if baselines and evolved:
        baseline_rate = baselines[0]["resolve_rate"]
        lines.append(sep)
        lines.append(f"Baseline resolve rate: {baseline_rate:.1%}")
        for e in evolved:
            delta = e["resolve_rate"] - baseline_rate
            lines.append(f"  {e['experiment_id']}: {e['resolve_rate']:.1%} (delta: {delta:+.1%})")

    output = "\n".join(lines)
    print(output)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(f"# Experiment Results Summary\n\n```\n{output}\n```\n")
        print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
