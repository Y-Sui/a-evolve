"""Internal SWE-bench benchmark adapter.

Loads tasks from a local JSONL file and evaluates patches using Docker containers
with custom per-repo test specifications. Compatible with the internal-swe-bench
format used in swe-workspace.

Usage:
    benchmark = InternalSweBenchmark(
        dataset_path="/path/to/all_instances_annotated.jsonl",
    )
    tasks = benchmark.get_tasks(split="test", limit=52)
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...types import Feedback, Task, Trajectory
from ..base import BenchmarkAdapter

logger = logging.getLogger(__name__)

DEFAULT_EVAL_TIMEOUT = 300


# ---------------------------------------------------------------------------
# Per-repo test specifications
# ---------------------------------------------------------------------------

@dataclass
class RepoTestSpec:
    setup_script: str
    pytest_flags: list[str] = field(default_factory=lambda: ["-v", "--override-ini=addopts="])


REPO_SPECS: dict[str, RepoTestSpec] = {
    "MiroMindAI/miroflow": RepoTestSpec(
        setup_script=r"""
if [ -f /testbed/pyproject.toml ]; then
  PROJ_ROOT="/testbed"
else
  PROJ_ROOT="/testbed/libs/miroflow"
fi
VENV="${PROJ_ROOT}/.venv"
PYTEST_BIN="${VENV}/bin/pytest"
if [ -f /testbed/pyproject.toml ]; then
  export PYTHONPATH="/testbed:${PYTHONPATH:-}"
else
  export PYTHONPATH="/testbed:/testbed/libs/miroflow:/testbed/libs/miroflow/src:${PYTHONPATH:-}"
fi
cd /testbed
""",
    ),
    "MiroMindAI/MiroThinker": RepoTestSpec(
        setup_script=r"""
PROJ_ROOT="/testbed/apps/miroflow-agent"
VENV="${PROJ_ROOT}/.venv"
PYTEST_BIN="${VENV}/bin/pytest"
PYTHONPATH="/testbed"
for d in /testbed/apps/*/; do
  PYTHONPATH="${d}:${PYTHONPATH}"
done
export PYTHONPATH="${PYTHONPATH}:${PYTHONPATH:-}"
cd /testbed
""",
    ),
    "MiroMindAI/sd-torchtune": RepoTestSpec(
        setup_script=r"""
PROJ_ROOT="/testbed"
PYTEST_BIN="pytest"
export PYTHONPATH="/testbed:${PYTHONPATH:-}"
cd /testbed
""",
        pytest_flags=["-v", "--without-integration", "--override-ini=addopts="],
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_test_ids(test_ids: list[str]) -> list[str]:
    resolved = []
    for tid in test_ids:
        if "::" in tid:
            module_part, test_name = tid.split("::", 1)
        else:
            module_part = tid
            test_name = None
        if "/" not in module_part and "." in module_part and not module_part.endswith(".py"):
            module_part = module_part.replace(".", "/") + ".py"
        if test_name:
            resolved.append(f"{module_part}::{test_name}")
        else:
            resolved.append(module_part)
    return resolved


def _build_test_command(spec: RepoTestSpec, test_ids: list[str]) -> str:
    resolved = _resolve_test_ids(test_ids)
    flags = " ".join(spec.pytest_flags)
    tests = " ".join(f'"{t}"' for t in resolved)
    return f"$PYTEST_BIN {flags} {tests}"


def _parse_list_field(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            import ast
            try:
                return ast.literal_eval(raw)
            except Exception:
                return [raw]
    return []


def _resolve_docker_image(docker_image: str, namespace: str | None) -> str:
    if namespace is None:
        return docker_image
    if "/" in docker_image:
        _, suffix = docker_image.split("/", 1)
        return f"{namespace}/{suffix}"
    return docker_image


def _build_eval_script(instance: dict, model_patch: str) -> str:
    repo = instance["repo"]
    if repo not in REPO_SPECS:
        return f"#!/bin/bash\necho 'UNSUPPORTED_REPO: {repo}'\nexit 1\n"
    spec = REPO_SPECS[repo]

    f2p_ids = _parse_list_field(instance.get("FAIL_TO_PASS", "[]"))
    p2p_ids = _parse_list_field(instance.get("PASS_TO_PASS", "[]"))

    if not f2p_ids and not p2p_ids:
        return "#!/bin/bash\necho 'NO_TESTS'\nexit 0\n"

    lines = [
        "#!/bin/bash",
        "set -uo pipefail",
        "",
        "# Setup environment",
        spec.setup_script.strip(),
        "",
    ]

    # Apply test_patch
    test_patch = instance.get("test_patch", "")
    if test_patch:
        lines += [
            "cat <<'EOF_TEST_PATCH' > /tmp/test.diff",
            test_patch,
            "EOF_TEST_PATCH",
            "cd /testbed",
            "git apply --verbose /tmp/test.diff 2>&1 || git apply --verbose --reject /tmp/test.diff 2>&1 || patch --batch --fuzz=5 -p1 -i /tmp/test.diff 2>&1",
            "",
        ]

    # Apply model patch
    if model_patch and model_patch.strip():
        lines += [
            "cat <<'EOF_MODEL_PATCH' > /tmp/model.diff",
            model_patch,
            "EOF_MODEL_PATCH",
            "cd /testbed",
            "git apply --verbose /tmp/model.diff 2>&1 || git apply --verbose --reject /tmp/model.diff 2>&1 || patch --batch --fuzz=5 -p1 -i /tmp/model.diff 2>&1",
            "",
        ]

    # Run F2P tests
    if f2p_ids:
        f2p_cmd = _build_test_command(spec, f2p_ids)
        lines += [
            "echo '===F2P_START==='",
            f"{f2p_cmd} 2>&1",
            "F2P_EXIT=$?",
            "echo '===F2P_END==='",
            'echo "F2P_EXIT_CODE=$F2P_EXIT"',
            "",
        ]
    else:
        lines += ["F2P_EXIT=0", "echo 'F2P_EXIT_CODE=0'", ""]

    # Run P2P tests
    if p2p_ids:
        p2p_cmd = _build_test_command(spec, p2p_ids)
        lines += [
            "echo '===P2P_START==='",
            f"{p2p_cmd} 2>&1",
            "P2P_EXIT=$?",
            "echo '===P2P_END==='",
            'echo "P2P_EXIT_CODE=$P2P_EXIT"',
            "",
        ]
    else:
        lines += ["P2P_EXIT=0", "echo 'P2P_EXIT_CODE=0'", ""]

    lines += [
        'echo "SUMMARY: F2P=$F2P_EXIT P2P=$P2P_EXIT"',
        'if [ "$F2P_EXIT" -eq 0 ] && [ "$P2P_EXIT" -eq 0 ]; then exit 0; else exit 1; fi',
    ]
    return "\n".join(lines)


def _run_in_docker(image: str, script: str, timeout: int = 300) -> tuple[int, str]:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write(script)
        script_path = f.name
    try:
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "-v", f"{script_path}:/eval.sh:ro",
                "--platform", "linux/x86_64",
                image,
                "/bin/bash", "/eval.sh",
            ],
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    except Exception as e:
        return -1, str(e)
    finally:
        Path(script_path).unlink(missing_ok=True)


def _parse_exit_codes(output: str) -> dict[str, int]:
    codes = {}
    for line in output.splitlines():
        if line.startswith("F2P_EXIT_CODE="):
            codes["f2p"] = int(line.split("=", 1)[1])
        elif line.startswith("P2P_EXIT_CODE="):
            codes["p2p"] = int(line.split("=", 1)[1])
    return codes


# ---------------------------------------------------------------------------
# Benchmark adapter
# ---------------------------------------------------------------------------

class InternalSweBenchmark(BenchmarkAdapter):
    """Benchmark adapter for internal SWE-bench (local JSONL datasets)."""

    def __init__(
        self,
        dataset_path: str,
        namespace: str | None = None,
        shuffle: bool = True,
        holdout_ratio: float = 0.2,
        eval_timeout: int = DEFAULT_EVAL_TIMEOUT,
    ):
        self.dataset_path = dataset_path
        self.namespace = namespace
        self.shuffle = shuffle
        self.holdout_ratio = holdout_ratio
        self.eval_timeout = eval_timeout
        self._cache: dict[str, list[dict]] = {}
        self._split_done = False

    def get_tasks(self, split: str = "test", limit: int = 10) -> list[Task]:
        rows = self._load_split(split)
        tasks = []
        for row in rows[:limit]:
            instance_id = row["instance_id"]
            docker_image = row.get("docker_image", "")
            if self.namespace and docker_image:
                docker_image = _resolve_docker_image(docker_image, self.namespace)

            tasks.append(Task(
                id=instance_id,
                input=row.get("problem_statement", ""),
                metadata={
                    "instance_id": instance_id,
                    "docker_image": docker_image,
                    "repo": row.get("repo", ""),
                    "base_commit": row.get("base_commit", ""),
                    "version": row.get("version", ""),
                    "test_patch": row.get("test_patch", ""),
                    "hints_text": row.get("hints_text", ""),
                    "FAIL_TO_PASS": _parse_list_field(row.get("FAIL_TO_PASS", "[]")),
                    "PASS_TO_PASS": _parse_list_field(row.get("PASS_TO_PASS", "[]")),
                    "patch": row.get("patch", ""),
                    "created_at": row.get("created_at", ""),
                    "environment_setup_commit": row.get("environment_setup_commit", ""),
                    "mask_patch": row.get("mask_patch", ""),
                    # Internal-swe-bench specific
                    "_raw_instance": row,
                },
            ))
        return tasks

    def evaluate(self, task: Task, trajectory: Trajectory) -> Feedback:
        patch = trajectory.output
        metadata = task.metadata
        instance_id = task.id

        if not patch.strip():
            return Feedback(
                success=False, score=0.0,
                detail=f"Empty patch for {instance_id}",
                raw={"instance_id": instance_id, "reason": "empty_patch"},
            )

        raw_instance = metadata.get("_raw_instance", metadata)
        docker_image = metadata.get("docker_image", "")

        if not docker_image:
            return Feedback(
                success=False, score=0.0,
                detail=f"No docker_image for {instance_id}",
                raw={"instance_id": instance_id, "reason": "no_docker_image"},
            )

        repo = metadata.get("repo", "")
        if repo not in REPO_SPECS:
            return Feedback(
                success=False, score=0.0,
                detail=f"Unsupported repo: {repo}. Supported: {list(REPO_SPECS.keys())}",
                raw={"instance_id": instance_id, "reason": "unsupported_repo"},
            )

        script = _build_eval_script(raw_instance, patch)
        exit_code, output = _run_in_docker(docker_image, script, self.eval_timeout)

        if exit_code == -1 and output == "TIMEOUT":
            return Feedback(
                success=False, score=0.0,
                detail=f"Eval timed out after {self.eval_timeout}s",
                raw={"instance_id": instance_id, "reason": "timeout"},
            )

        codes = _parse_exit_codes(output)
        f2p_pass = codes.get("f2p", -1) == 0
        p2p_pass = codes.get("p2p", -1) == 0
        resolved = f2p_pass and p2p_pass
        score = 1.0 if resolved else 0.0

        detail = (
            f"F2P: {'PASS' if f2p_pass else 'FAIL'}, "
            f"P2P: {'PASS' if p2p_pass else 'FAIL'}\n\n"
            f"{output[-2000:]}"
        )

        return Feedback(
            success=resolved,
            score=score,
            detail=detail,
            raw={"instance_id": instance_id, "f2p_pass": f2p_pass, "p2p_pass": p2p_pass},
        )

    # ── Internals ────────────────────────────────────────────────────

    def _load_split(self, split: str) -> list[dict]:
        if not self._split_done:
            self._do_split()
        if split in self._cache:
            return self._cache[split]
        return self._cache.get("train", [])

    def _do_split(self) -> None:
        path = Path(self.dataset_path)
        rows = []
        if path.suffix == ".jsonl":
            for line in path.read_text().strip().splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        else:
            data = json.loads(path.read_text())
            rows = data if isinstance(data, list) else list(data.values())

        if self.shuffle:
            random.shuffle(rows)

        n_holdout = max(1, int(len(rows) * self.holdout_ratio))
        self._cache["holdout"] = rows[:n_holdout]
        self._cache["train"] = rows[n_holdout:]
        self._cache["test"] = rows

        self._split_done = True
        logger.info(
            "Loaded %d tasks from %s (train=%d, holdout=%d)",
            len(rows), self.dataset_path,
            len(self._cache["train"]), len(self._cache["holdout"]),
        )
