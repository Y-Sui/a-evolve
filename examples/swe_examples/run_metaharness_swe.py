#!/usr/bin/env python3
"""MetaHarness experiment on SWE-bench benchmarks.

Implements the Meta-Harness search loop (Lee et al., 2026, arXiv:2603.28052)
adapted for SWE-bench code-repair tasks:

  Phase 0 — Baseline: solve all tasks with seed workspace (no evolution)
  Phase 1 — Evolution: N cycles of MetaHarness (proposer -> validate -> eval -> archive)
  Phase 2 — Final eval: solve all tasks with best evolved workspace

Usage:
    # Full experiment on SWE-bench Verified Mini
    uv run python examples/swe_examples/run_metaharness_swe.py \
        --config experiments/claude-swe/configs/p4-metaharness-mini.yaml

    # Quick smoke test (5 tasks, 1 cycle, 1 candidate)
    uv run python examples/swe_examples/run_metaharness_swe.py \
        --config experiments/claude-swe/configs/p4-metaharness-mini.yaml \
        --task-limit 5 --max-cycles 1 --num-candidates 1 --parallel 5
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv

load_dotenv()

from agent_evolve.agents.swe.agent import SweAgent
from agent_evolve.algorithms.meta_harness import MetaHarnessEngine
from agent_evolve.benchmarks.swe_verified_mini.benchmark import SweVerifiedMiniBenchmark
from agent_evolve.config import EvolveConfig
from agent_evolve.engine.history import EvolutionHistory
from agent_evolve.engine.observer import Observer
from agent_evolve.engine.trial import TrialRunner
from agent_evolve.engine.versioning import VersionControl
from agent_evolve.types import CycleRecord, Feedback, Observation, Task, Trajectory

log = logging.getLogger("metaharness_swe")


# ---------------------------------------------------------------------------
# Parallel solve — runs SWE tasks in separate processes (Docker-heavy)
# ---------------------------------------------------------------------------

def _solve_one_task(
    task_dict: dict,
    workspace_dir: str,
    model_id: str,
    max_tokens: int,
    max_steps: int,
    window_size: int,
    efficiency_prompt: bool,
    max_retries: int,
    dataset_name: str,
    benchmark_type: str = "swe-verified-mini",
    namespace: str | None = None,
) -> dict:
    """Solve a single SWE-bench task in its own process."""
    import logging
    import random
    import time
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from dotenv import load_dotenv
    load_dotenv()
    from agent_evolve.agents.swe.agent import SweAgent
    from agent_evolve.benchmarks.swe_verified_mini.benchmark import SweVerifiedMiniBenchmark
    from agent_evolve.types import Task

    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s [{task_dict['id']}] %(message)s",
    )
    for n in ("botocore", "urllib3", "httpcore", "httpx",
              "strands.models", "strands.tools", "strands.telemetry"):
        logging.getLogger(n).setLevel(logging.WARNING)
    wlog = logging.getLogger("worker")

    task = Task(id=task_dict["id"], input=task_dict["input"], metadata=task_dict["metadata"])

    agent = SweAgent(
        workspace_dir=workspace_dir,
        model_id=model_id,
        max_tokens=max_tokens,
        max_steps=max_steps,
        window_size=window_size,
        efficiency_prompt=efficiency_prompt,
    )

    if benchmark_type == "internal-swe":
        from agent_evolve.benchmarks.internal_swe import InternalSweBenchmark
        bm = InternalSweBenchmark(dataset_path=dataset_name, namespace=namespace, shuffle=False)
    else:
        bm = SweVerifiedMiniBenchmark(dataset_name=dataset_name, shuffle=False)

    t0 = time.time()
    trajectory = None
    for attempt in range(max_retries):
        try:
            trajectory = agent.solve(task)
            break
        except Exception as e:
            err_str = str(e)
            transient = any(k in err_str for k in (
                "internalServerException", "ThrottlingException",
                "timed out", "Read timed out", "ServiceUnavailableException",
                "Error code: 500", "Error code: 502", "Error code: 503",
                "Error code: 429",
                "model_not_found", "No available channel",
                "failed to connect", "tls error",
                "connection slots", "query_data_error",
                "无效的令牌", "数据库查询出错",
            ))
            if transient and attempt < max_retries - 1:
                base_wait = min(30 * (2 ** attempt), 300)
                jitter = random.uniform(0, base_wait * 0.3)
                wait = base_wait + jitter
                wlog.warning("solve attempt %d/%d failed (transient), retrying in %.0fs: %s",
                             attempt + 1, max_retries, wait, e)
                time.sleep(wait)
                continue
            wlog.error("solve failed after %d attempt(s): %s", attempt + 1, e)
            return {
                "instance_id": task.id, "success": False, "score": 0.0,
                "error": err_str, "elapsed": round(time.time() - t0, 1),
                "patch": "",
            }

    if trajectory is None:
        return {
            "instance_id": task.id, "success": False, "score": 0.0,
            "error": "no trajectory", "elapsed": round(time.time() - t0, 1),
            "patch": "",
        }

    elapsed = time.time() - t0
    feedback = bm.evaluate(task, trajectory)
    wlog.info("%s score=%.1f elapsed=%.1fs patch=%dch",
              "PASS" if feedback.success else "FAIL",
              feedback.score, elapsed, len(trajectory.output))

    return {
        "instance_id": task.id,
        "success": feedback.success,
        "score": feedback.score,
        "elapsed": round(elapsed, 1),
        "patch": trajectory.output,
        "feedback_detail": feedback.detail,
    }


class ParallelSweTrialRunner(TrialRunner):
    """TrialRunner that evaluates SWE tasks in parallel via ProcessPoolExecutor."""

    def __init__(
        self,
        agent: SweAgent,
        benchmark,
        max_workers: int = 25,
        max_retries: int = 5,
        benchmark_type: str = "swe-verified-mini",
        namespace: str | None = None,
    ):
        super().__init__(agent, benchmark)
        self.max_workers = max_workers
        self.max_retries = max_retries
        self._model_id = agent.model_id
        self._max_tokens = agent.max_tokens
        self._max_steps = agent.max_steps
        self._window_size = agent.window_size
        self._efficiency_prompt = agent.efficiency_prompt
        self._workspace_dir = str(agent.workspace.root)
        self._dataset_name = getattr(benchmark, 'dataset_name', '') or getattr(benchmark, 'dataset_path', '')
        self._benchmark_type = benchmark_type
        self._namespace = namespace

    def run_tasks(self, tasks: list[Task]) -> list[Observation]:
        if len(tasks) <= 1 or self.max_workers <= 1:
            return super().run_tasks(tasks)

        task_dicts = [{"id": t.id, "input": t.input, "metadata": t.metadata} for t in tasks]
        results: list[Observation] = []
        errors = 0

        with ProcessPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {
                pool.submit(
                    _solve_one_task, td, self._workspace_dir,
                    self._model_id, self._max_tokens, self._max_steps,
                    self._window_size, self._efficiency_prompt,
                    self.max_retries, self._dataset_name,
                    self._benchmark_type, self._namespace,
                ): td
                for td in task_dicts
            }
            for fut in as_completed(futures):
                td = futures[fut]
                try:
                    r = fut.result()
                    task_obj = next(t for t in tasks if t.id == r["instance_id"])
                    traj = Trajectory(task_id=r["instance_id"], output=r.get("patch", ""), steps=[])
                    fb = Feedback(
                        success=r["success"], score=r["score"],
                        detail=r.get("feedback_detail", ""), raw=r,
                    )
                    results.append(Observation(task=task_obj, trajectory=traj, feedback=fb))
                    status = "PASS" if r["success"] else "FAIL"
                    passed = sum(1 for o in results if o.feedback.success)
                    print(f"  {status} {r['instance_id']} ({r['elapsed']:.0f}s) | {passed}/{len(results)}")
                except Exception as e:
                    errors += 1
                    log.error("Task %s failed: %s", td["id"], e)

        if errors:
            log.warning("%d/%d tasks failed", errors, len(tasks))
        return results


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(observations: list[Observation]) -> dict:
    total = len(observations)
    passed = sum(1 for o in observations if o.feedback.success)
    return {
        "total": total,
        "passed": passed,
        "pass_rate": passed / total if total > 0 else 0.0,
        "avg_score": sum(o.feedback.score for o in observations) / total if total > 0 else 0.0,
    }


def print_metrics(label: str, metrics: dict) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    print(f"  Total: {metrics['total']}  Passed: {metrics['passed']}  "
          f"Rate: {metrics['pass_rate']:.1%}  Avg Score: {metrics['avg_score']:.3f}")
    print()


# ---------------------------------------------------------------------------
# Phase 0 — Baseline
# ---------------------------------------------------------------------------

def run_baseline(
    agent: SweAgent,
    trial: ParallelSweTrialRunner,
    observer: Observer,
    versioning: VersionControl,
    history: EvolutionHistory,
    tasks: list[Task],
) -> list[Observation]:
    print(f"\n{'=' * 60}")
    print(f"  PHASE 0: Baseline Evaluation ({len(tasks)} tasks)")
    print(f"{'=' * 60}\n")

    t0 = time.time()
    observations = trial.run_tasks(tasks)
    elapsed = time.time() - t0

    observer.collect(observations)

    score = sum(o.feedback.score for o in observations) / len(observations) if observations else 0.0
    passed = sum(1 for o in observations if o.feedback.success)
    print(f"Baseline: {score:.3f} ({passed}/{len(observations)} passed) in {elapsed:.0f}s")

    versioning.commit(message=f"baseline: score={score:.3f}", tag="baseline")

    record = CycleRecord(
        cycle=0, score=score, mutated=False,
        engine_name="baseline",
        summary=f"Baseline: {passed}/{len(observations)} ({score:.3f})",
        observation_batch="batch_0001.jsonl",
    )
    history.record_cycle(record)

    print_metrics("Baseline Results", compute_metrics(observations))

    # Save baseline results
    output_dir = agent.workspace.root.parent
    baseline_results = {
        "cycle": 0,
        "phase": "baseline",
        "score": score,
        "passed": passed,
        "total": len(observations),
        "per_instance": [
            {"instance_id": o.task.id, "success": o.feedback.success, "score": o.feedback.score}
            for o in observations
        ],
        "elapsed_sec": elapsed,
        "timestamp": datetime.now().isoformat(),
    }
    (output_dir / "results_baseline.json").write_text(json.dumps(baseline_results, indent=2))

    return observations


# ---------------------------------------------------------------------------
# Phase 1 — MetaHarness evolution
# ---------------------------------------------------------------------------

def run_evolution(
    engine: MetaHarnessEngine,
    agent: SweAgent,
    trial: ParallelSweTrialRunner,
    observer: Observer,
    versioning: VersionControl,
    history: EvolutionHistory,
    observations: list[Observation],
    max_cycles: int,
    config: EvolveConfig,
    tasks: list[Task],
    eval_factory: Callable | None = None,
    start_cycle: int = 1,
) -> float:
    print(f"\n{'=' * 60}")
    print(f"  PHASE 1: MetaHarness Evolution (cycles {start_cycle}-{max_cycles}, "
          f"k={engine.num_candidates})")
    print(f"{'=' * 60}\n")

    score_history = history.get_score_curve()
    best_score = max(score_history) if score_history else 0.0

    for cycle in range(start_cycle, max_cycles + 1):
        cycle_t0 = time.time()
        print(f"\n--- Cycle {cycle}/{max_cycles} (best so far: {best_score:.3f}) ---")

        step_result = engine.step(
            workspace=agent.workspace,
            observations=observations,
            history=history,
            trial=trial,
            tasks=tasks,
            eval_factory=eval_factory,
        )

        cycle_elapsed = time.time() - cycle_t0
        cycle_score = step_result.metadata.get("best_score", 0.0)
        best_score = max(best_score, cycle_score)

        tag = f"evo-{cycle}"
        versioning.commit(
            message=f"evo-{cycle}: {step_result.summary}",
            tag=tag,
        )

        record = CycleRecord(
            cycle=cycle, score=cycle_score, mutated=step_result.mutated,
            engine_name="MetaHarnessEngine",
            summary=step_result.summary,
            metadata=step_result.metadata,
        )
        history.record_cycle(record)

        agent.reload_from_fs()

        _append_history(agent.workspace.root / "evolution", cycle, cycle_score, step_result.mutated)
        _write_metrics(agent.workspace.root / "evolution", history.get_score_curve())

        print(f"Cycle {cycle}: score={cycle_score:.3f} mutated={step_result.mutated} "
              f"({cycle_elapsed:.0f}s) | {step_result.summary}")

        # Save per-cycle results
        output_dir = agent.workspace.root.parent
        cycle_results = {
            "cycle": cycle,
            "score": cycle_score,
            "best_score": best_score,
            "mutated": step_result.mutated,
            "summary": step_result.summary,
            "candidate_scores": step_result.metadata.get("candidate_scores", []),
            "selected": step_result.metadata.get("selected", ""),
            "score_history": history.get_score_curve(),
            "elapsed_sec": cycle_elapsed,
            "timestamp": datetime.now().isoformat(),
        }
        (output_dir / f"results_cycle_{cycle}.json").write_text(
            json.dumps(cycle_results, indent=2)
        )

    return best_score


# ---------------------------------------------------------------------------
# Phase 2 — Final eval with best candidate
# ---------------------------------------------------------------------------

def _restore_best_candidate(work_dir: Path, agent: SweAgent) -> None:
    candidates_dir = work_dir / "evolution" / "candidates"
    if not candidates_dir.exists():
        log.warning("No candidates directory — skipping")
        return

    candidates = []
    for scores_path in sorted(candidates_dir.glob("*/scores.json")):
        try:
            data = json.loads(scores_path.read_text())
            if not data.get("valid", True):
                continue
            candidates.append({
                "label": scores_path.parent.name,
                "score": data.get("score", 0.0),
                "cost": data.get("cost", 0),
                "snapshot_dir": scores_path.parent / "snapshot",
            })
        except (json.JSONDecodeError, KeyError):
            continue

    if not candidates:
        log.warning("No valid candidates — skipping")
        return

    # Pareto frontier
    frontier = []
    for c in candidates:
        dominated = any(
            o["score"] >= c["score"] and o["cost"] <= c["cost"]
            and (o["score"] > c["score"] or o["cost"] < c["cost"])
            for o in candidates if o is not c
        )
        if not dominated:
            frontier.append(c)

    best = max(frontier, key=lambda c: c["score"])
    snapshot_dir = best["snapshot_dir"]

    print(f"\n  Archive selection: {best['label']} "
          f"(score={best['score']:.3f}) from {len(candidates)} candidates")

    if not snapshot_dir.exists():
        log.error("Snapshot %s missing", snapshot_dir)
        return

    workspace_root = agent.workspace.root
    for item in snapshot_dir.iterdir():
        dest = workspace_root / item.name
        if item.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    agent.reload_from_fs()
    print(f"  Agent reloaded from best candidate\n")


def run_final_eval(
    agent: SweAgent,
    trial: ParallelSweTrialRunner,
    observer: Observer,
    tasks: list[Task],
) -> list[Observation]:
    print(f"\n{'=' * 60}")
    print(f"  PHASE 2: Final Evaluation ({len(tasks)} tasks)")
    print(f"{'=' * 60}\n")

    t0 = time.time()
    observations = trial.run_tasks(tasks)
    elapsed = time.time() - t0

    observer.collect(observations)

    score = sum(o.feedback.score for o in observations) / len(observations) if observations else 0.0
    passed = sum(1 for o in observations if o.feedback.success)
    print(f"Final: {score:.3f} ({passed}/{len(observations)} passed) in {elapsed:.0f}s")

    print_metrics("Final Evolved Results", compute_metrics(observations))

    # Save final results
    output_dir = agent.workspace.root.parent
    final_results = {
        "phase": "final",
        "score": score,
        "passed": passed,
        "total": len(observations),
        "per_instance": [
            {"instance_id": o.task.id, "success": o.feedback.success, "score": o.feedback.score}
            for o in observations
        ],
        "elapsed_sec": elapsed,
        "timestamp": datetime.now().isoformat(),
    }
    (output_dir / "results_final.json").write_text(json.dumps(final_results, indent=2))

    return observations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _append_history(evolution_dir: Path, cycle: int, score: float, mutated: bool) -> None:
    entry = {"cycle": cycle, "score": score, "mutated": mutated, "timestamp": datetime.now().isoformat()}
    with open(evolution_dir / "history.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")


def _write_metrics(evolution_dir: Path, scores: list[float]) -> None:
    metrics = {
        "cycles_completed": len(scores),
        "latest_score": scores[-1] if scores else 0.0,
        "best_score": max(scores) if scores else 0.0,
    }
    (evolution_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))


def _load_resume_state(evolution_dir: Path) -> tuple[int, list[float]]:
    history_scores: dict[int, float] = {}
    history_file = evolution_dir / "history.jsonl"
    if history_file.exists():
        for line in history_file.read_text().strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                history_scores[entry["cycle"]] = entry["score"]
            except (json.JSONDecodeError, KeyError):
                continue

    candidates_dir = evolution_dir / "candidates"
    candidate_cycles: dict[int, float] = {}
    if candidates_dir.exists():
        for scores_path in candidates_dir.glob("*/scores.json"):
            label = scores_path.parent.name
            try:
                cycle_num = int(label.split("_")[1])
                data = json.loads(scores_path.read_text())
                score = data.get("score", 0.0)
                if cycle_num not in candidate_cycles or score > candidate_cycles[cycle_num]:
                    candidate_cycles[cycle_num] = score
            except (ValueError, json.JSONDecodeError, KeyError, IndexError):
                continue

    all_scores: dict[int, float] = {}
    all_scores.update(history_scores)
    for c, s in candidate_cycles.items():
        if c not in all_scores:
            all_scores[c] = s

    if not all_scores:
        return 0, []

    last_cycle = max(all_scores.keys())
    score_curve = [all_scores.get(i, 0.0) for i in range(min(all_scores.keys()), last_cycle + 1)]
    return last_cycle, score_curve


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="MetaHarness experiment on SWE-bench")
    p.add_argument("--config", type=str, required=True, help="YAML config path")
    p.add_argument("--phase", type=str, default="all", choices=["0", "1", "2", "all"])

    # Overrides
    p.add_argument("--parallel", type=int, default=None)
    p.add_argument("--max-cycles", type=int, default=None)
    p.add_argument("--num-candidates", type=int, default=None)
    p.add_argument("--task-limit", type=int, default=None)
    p.add_argument("--solver-model", type=str, default=None)
    p.add_argument("--resume", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for name in ("metaharness_swe", "agent_evolve.algorithms.meta_harness"):
        logging.getLogger(name).setLevel(logging.INFO)
    for n in ("botocore", "urllib3", "httpcore", "httpx",
              "strands.models", "strands.tools", "strands.telemetry"):
        logging.getLogger(n).setLevel(logging.WARNING)

    # Load config
    config = EvolveConfig.from_yaml(args.config)
    cfg_raw = {}
    with open(args.config) as f:
        import yaml
        cfg_raw = yaml.safe_load(f)

    # Extract params
    model_id = args.solver_model or cfg_raw.get("model_id", "anthropic/claude-opus-4.6")
    dataset = cfg_raw.get("dataset", "MariusHobbhahn/swe-bench-verified-mini")
    task_limit = args.task_limit or cfg_raw.get("limit", 50)
    parallel = args.parallel or cfg_raw.get("parallel", 25)
    max_cycles = args.max_cycles or config.max_cycles
    max_steps = cfg_raw.get("max_steps", 140)
    window_size = cfg_raw.get("window_size", 70)
    max_tokens = cfg_raw.get("max_tokens", 16384)
    efficiency_prompt = cfg_raw.get("efficiency_prompt", True)
    max_retries = cfg_raw.get("max_retries", 5)
    seed_workspace = cfg_raw.get("seed_workspace", "seed_workspaces/swe")
    output_dir = Path(cfg_raw.get("output_dir", "experiments/claude-swe/logs/p4-metaharness-mini"))
    benchmark_type = cfg_raw.get("benchmark", "swe-verified-mini")
    namespace = cfg_raw.get("namespace", None)

    if args.num_candidates is not None:
        config.extra["num_candidates"] = args.num_candidates

    # Setup workspace
    work_dir = output_dir / "workspace"
    seed_dir = Path(seed_workspace)
    if not work_dir.exists() and seed_dir.exists():
        output_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(seed_dir, work_dir)
        log.info("Copied seed workspace %s -> %s", seed_dir, work_dir)

    # Save config copy
    config_copy = output_dir / "experiment_config.yaml"
    with open(config_copy, "w") as f:
        import yaml
        yaml.dump(cfg_raw, f, default_flow_style=False)

    # Initialize components
    agent = SweAgent(
        workspace_dir=work_dir,
        model_id=model_id,
        max_tokens=max_tokens,
        max_steps=max_steps,
        window_size=window_size,
        efficiency_prompt=efficiency_prompt,
    )

    if benchmark_type == "internal-swe":
        from agent_evolve.benchmarks.internal_swe import InternalSweBenchmark
        benchmark = InternalSweBenchmark(dataset_path=dataset, namespace=namespace, shuffle=False)
    else:
        benchmark = SweVerifiedMiniBenchmark(dataset_name=dataset, shuffle=False)
    all_tasks = benchmark.get_tasks(split="test", limit=task_limit)

    trial = ParallelSweTrialRunner(
        agent, benchmark,
        max_workers=parallel,
        max_retries=max_retries,
        benchmark_type=benchmark_type,
        namespace=namespace,
    )

    engine = MetaHarnessEngine(config)

    evolution_dir = work_dir / "evolution"
    evolution_dir.mkdir(parents=True, exist_ok=True)

    # File log
    fh = logging.FileHandler(evolution_dir / "experiment.log")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
    logging.getLogger().addHandler(fh)

    observer = Observer(evolution_dir)
    versioning = VersionControl(work_dir)
    versioning.init()
    history = EvolutionHistory(observer, versioning)

    print(f"\n{'=' * 60}")
    print(f"  MetaHarness SWE Experiment: {cfg_raw.get('experiment_id', 'unknown')}")
    print(f"  Workspace:        {work_dir}")
    print(f"  Tasks:            {len(all_tasks)}")
    print(f"  Max cycles:       {max_cycles}")
    print(f"  Candidates/cycle: {engine.num_candidates}")
    print(f"  Solver:           {model_id}")
    print(f"  Proposer:         {engine.model}")
    print(f"  Parallel:         {parallel}")
    print(f"  Phase:            {args.phase}")
    print(f"{'=' * 60}")

    run_phases = args.phase
    global_t0 = time.time()
    observations: list[Observation] = []
    start_cycle = 1

    # Resume
    if args.resume:
        last_cycle, prev_scores = _load_resume_state(evolution_dir)
        if last_cycle > 0:
            start_cycle = last_cycle + 1
            for i, score in enumerate(prev_scores):
                history.record_cycle(CycleRecord(
                    cycle=i, score=score, mutated=i > 0,
                    engine_name="baseline" if i == 0 else "MetaHarnessEngine",
                    summary=f"[resumed] cycle {i}: score={score:.3f}",
                ))
            print(f"\n  RESUMING from cycle {start_cycle} (best={max(prev_scores):.3f})")
        else:
            print("\n  --resume: no prior state, starting fresh")

    try:
        # Phase 0: Baseline
        if run_phases in ("0", "all") and start_cycle <= 1:
            observations = run_baseline(
                agent, trial, observer, versioning, history, all_tasks,
            )

        # Phase 1: Evolution
        if run_phases in ("1", "all") and start_cycle <= max_cycles:
            def _eval_factory(workspace_path: Path) -> ParallelSweTrialRunner:
                eval_agent = SweAgent(
                    workspace_dir=workspace_path,
                    model_id=model_id,
                    max_tokens=max_tokens,
                    max_steps=max_steps,
                    window_size=window_size,
                    efficiency_prompt=efficiency_prompt,
                )
                if benchmark_type == "internal-swe":
                    from agent_evolve.benchmarks.internal_swe import InternalSweBenchmark
                    eval_bm = InternalSweBenchmark(dataset_path=dataset, namespace=namespace, shuffle=False)
                else:
                    eval_bm = SweVerifiedMiniBenchmark(dataset_name=dataset, shuffle=False)
                return ParallelSweTrialRunner(
                    eval_agent, eval_bm,
                    max_workers=parallel,
                    max_retries=max_retries,
                    benchmark_type=benchmark_type,
                    namespace=namespace,
                )

            best_score = run_evolution(
                engine, agent, trial, observer, versioning, history,
                observations, max_cycles, config, tasks=all_tasks,
                eval_factory=_eval_factory,
                start_cycle=start_cycle,
            )

        # Phase 2: Final eval with best candidate
        if run_phases in ("2", "all"):
            _restore_best_candidate(output_dir / "workspace", agent)
            final_obs = run_final_eval(agent, trial, observer, all_tasks)

            scores = history.get_score_curve()
            if scores:
                baseline = scores[0]
                final_score = sum(o.feedback.score for o in final_obs) / len(final_obs) if final_obs else 0.0
                print(f"\n  Baseline: {baseline:.3f} -> Final: {final_score:.3f} "
                      f"(delta: {final_score - baseline:+.3f})")

    except KeyboardInterrupt:
        print("\n\nInterrupted!")

    total_elapsed = time.time() - global_t0
    print(f"\nTotal wall time: {total_elapsed / 3600:.1f} hours")

    # Save summary
    summary = {
        "experiment_id": cfg_raw.get("experiment_id"),
        "solver_model": model_id,
        "proposer_model": engine.model,
        "max_cycles": max_cycles,
        "num_candidates": engine.num_candidates,
        "num_tasks": len(all_tasks),
        "score_history": history.get_score_curve(),
        "total_wall_time_sec": total_elapsed,
        "timestamp": datetime.now().isoformat(),
    }
    (evolution_dir / "experiment_summary.json").write_text(json.dumps(summary, indent=2))

    # Save results
    results_path = output_dir / "results.json"
    results_data = []
    for o in observations:
        results_data.append({
            "instance_id": o.task.id,
            "success": o.feedback.success,
            "score": o.feedback.score,
        })
    results_path.write_text(json.dumps(results_data, indent=2, ensure_ascii=False))
    print(f"Results saved to {results_path}")


if __name__ == "__main__":
    main()
