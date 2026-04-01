#!/usr/bin/env python3
"""Run SWE-bench evolution using OpenRouter models.

Prerequisites:
    1. Set OPENROUTER_API_KEY and OPENROUTER_BASE_URL in .env or environment
    2. uv sync --extra all

Usage:
    # Quick test (2 tasks, no evolution)
    uv run python scripts/run_swe_openrouter.py --limit 2 --no-evolve

    # Small run with evolution
    uv run python scripts/run_swe_openrouter.py --limit 10 --batch-size 5

    # Full run
    uv run python scripts/run_swe_openrouter.py --limit 50 --batch-size 5 --parallel 5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from examples.swe_examples.evolve_sequential import main as evolve_main


def build_args():
    p = argparse.ArgumentParser(description="SWE-bench evolution via OpenRouter")
    p.add_argument("--model-id", type=str, default="anthropic/claude-sonnet-4",
                   help="OpenRouter model ID (default: anthropic/claude-sonnet-4)")
    p.add_argument("--batch-size", type=int, default=5)
    p.add_argument("--parallel", type=int, default=5)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--feedback", type=str, default="minimal", choices=["none", "minimal"])
    p.add_argument("--no-evolve", action="store_true")
    p.add_argument("--output-dir", type=str, default="logs/swe_openrouter")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = build_args()

    # Inject args into sys.argv for evolve_sequential.main() which re-parses
    sys.argv = [
        "evolve_sequential.py",
        "--model-id", args.model_id,
        "--batch-size", str(args.batch_size),
        "--parallel", str(args.parallel),
        "--limit", str(args.limit),
        "--max-tokens", str(args.max_tokens),
        "--feedback", args.feedback,
        "--output-dir", args.output_dir,
    ]
    if args.no_evolve:
        sys.argv.append("--no-evolve")
    if args.verbose:
        sys.argv.append("-v")

    evolve_main()
