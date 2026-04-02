#!/usr/bin/env python3
"""Run a single A-Evolve experiment from a YAML config file.

Reads an experiment config YAML and translates it into CLI arguments for
evolve_sequential.py. Handles .env loading for OpenRouter credentials.

Usage:
    uv run python scripts/run_experiment.py --config experiments/qwen35-swe/configs/p0-smoke.yaml
    uv run python scripts/run_experiment.py --config experiments/qwen35-swe/configs/p0-smoke.yaml --dry-run
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def build_command(cfg: dict) -> list[str]:
    """Translate experiment config into evolve_sequential.py CLI args."""
    cmd = [
        sys.executable,
        "examples/swe_examples/evolve_sequential.py",
        "--model-id", cfg["model_id"],
        "--dataset", cfg["dataset"],
        "--limit", str(cfg["limit"]),
        "--batch-size", str(cfg["batch_size"]),
        "--parallel", str(cfg["parallel"]),
        "--max-steps", str(cfg["max_steps"]),
        "--window-size", str(cfg["window_size"]),
        "--max-tokens", str(cfg["max_tokens"]),
        "--feedback", cfg["feedback"],
        "--algorithm", cfg.get("algorithm", "guided_synth"),
        "--seed-workspace", cfg.get("seed_workspace", "seed_workspaces/swe"),
        "--output-dir", cfg["output_dir"],
    ]

    if cfg.get("no_evolve"):
        cmd.append("--no-evolve")
    if cfg.get("efficiency_prompt"):
        cmd.append("--efficiency-prompt")
    if cfg.get("solver_proposes"):
        cmd.append("--solver-proposes")
    if cfg.get("verification_focus"):
        cmd.append("--verification-focus")
    if cfg.get("benchmark"):
        cmd.extend(["--benchmark", cfg["benchmark"]])
    if cfg.get("namespace"):
        cmd.extend(["--namespace", cfg["namespace"]])

    return cmd


def main():
    p = argparse.ArgumentParser(description="Run A-Evolve experiment from YAML config")
    p.add_argument("--config", type=str, required=True, help="Path to experiment YAML config")
    p.add_argument("--dry-run", action="store_true", help="Print command without executing")
    args = p.parse_args()

    load_dotenv()

    # Verify OpenRouter credentials
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("ERROR: OPENROUTER_API_KEY not set in environment or .env")
        sys.exit(1)
    if not os.environ.get("OPENROUTER_BASE_URL"):
        print("ERROR: OPENROUTER_BASE_URL not set in environment or .env")
        sys.exit(1)

    cfg = load_config(args.config)
    cmd = build_command(cfg)

    experiment_id = cfg.get("experiment_id", "unknown")
    description = cfg.get("description", "")
    output_dir = Path(cfg["output_dir"])

    print(f"Experiment: {experiment_id}")
    print(f"Description: {description}")
    print(f"Output: {output_dir}")
    print(f"Command: {' '.join(cmd)}")
    print()

    if args.dry_run:
        print("[dry-run] Command not executed.")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    # Save config copy to output dir
    config_copy = output_dir / "experiment_config.yaml"
    with open(config_copy, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)

    print(f"Starting experiment {experiment_id}...")
    print("=" * 60)

    result = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parent.parent))
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
