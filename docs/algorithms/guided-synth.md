# Guided Synthesis (`guided_synth`)

Memory-first evolution with LLM-guided intervention synthesis. This is the algorithm behind A-Evolve's **SWE-bench Verified** result (~#5, **76.8%**).

## Overview

Guided Synthesis replaces complex multi-phase evolution pipelines with a simple 2-phase loop:

1. **Write Memory** — Record minimal episodic memory from each task attempt (files edited, score, approach summary).
2. **Curate Skills** — An LLM curator reviews solver-proposed skills and decides whether to ACCEPT, MERGE, or SKIP each one.

The key insight: instead of the evolver *generating* interventions from scratch, the **solver proposes skills** after completing each task, and the evolver acts as a **curator** — keeping the library lean and generalizable.

## How It Works

```
┌──────────┐     ┌──────────────┐     ┌──────────────┐
│  Solver   │────▶│  Propose     │────▶│  Curator     │
│  (batch)  │     │  Skills      │     │  (LLM)       │
└──────────┘     └──────────────┘     └──────────────┘
                                            │
                                   ┌────────┼────────┐
                                   ▼        ▼        ▼
                                ACCEPT    MERGE    SKIP
```

### Phase 1: Episodic Memory

After each task, the engine writes a compact memory entry:
- Task ID, cycle number, score
- Files edited (extracted from the diff)
- One-line approach summary

This builds a lightweight history the solver can reference in future tasks.

### Phase 2: Skill Curation

Solvers propose skills in a structured format (TYPE, NAME, DESCRIPTION, CONTENT). The curator LLM reviews proposals against the existing skill library and makes one of three decisions:

- **ACCEPT** — Skill is new and generalizable. Added as-is.
- **MERGE** — Skill overlaps with an existing one. Combined into the existing skill.
- **SKIP** — Skill is too task-specific or already covered.

The curator prefers MERGE over ACCEPT — a library of 5-10 broad skills beats 30 narrow ones.

### Verification Focus Mode

When `--verification-focus` is enabled, the curator only accepts skills about **testing and verifying fixes** — finding test files, writing repro scripts, before/after comparison, edge case testing. This keeps the skill library focused on the highest-leverage area for SWE-bench.

### Pruning

When the skill library grows, the engine can prune redundant interventions. An LLM reviews all skills and fragments, identifies overlapping ones, and removes the weaker duplicates.

## Usage

### Evolution (recommended)

```bash
# Full SWE-bench Verified (500 tasks) — v32g config (76.8%)
uv run python examples/swe_examples/evolve_sequential.py \
  --dataset princeton-nlp/SWE-bench_Verified \
  --batch-size 20 --parallel 20 \
  --max-steps 140 --window-size 70 \
  --efficiency-prompt \
  --solver-proposes --verification-focus \
  --feedback none \
  --model-id us.anthropic.claude-opus-4-6-v1 \
  --seed-workspace seed_workspaces/swe \
  --output-dir logs/v32g-full \
  --limit 500
```

### Mini (quick testing)

```bash
uv run python examples/swe_examples/evolve_sequential.py \
  --dataset MariusHobbhahn/swe-bench-verified-mini \
  --batch-size 5 --parallel 5 \
  --max-steps 140 --window-size 40 \
  --efficiency-prompt \
  --solver-proposes --verification-focus \
  --feedback none \
  --model-id us.anthropic.claude-opus-4-6-v1 \
  --seed-workspace seed_workspaces/swe \
  --output-dir logs/test-mini \
  --limit 50
```

### Baseline (no evolution)

```bash
uv run python examples/swe_examples/solve_all.py \
  --dataset princeton-nlp/SWE-bench_Verified \
  --model-id us.anthropic.claude-opus-4-6-v1 \
  --workers 16 --max-turns 140 \
  --output-dir logs/baseline \
  --limit 500
```

## Key Flags

| Flag | Description |
| :--- | :--- |
| `--batch-size` | Tasks per evolution batch |
| `--parallel` | Parallel workers within each batch |
| `--max-steps` | Max tool calls per task (140 recommended) |
| `--window-size` | Sliding window message count (70 recommended) |
| `--efficiency-prompt` | Add hypothesis-first approach constraints |
| `--solver-proposes` | Solver proposes skills after each task |
| `--verification-focus` | Only accept verification-related skills |
| `--feedback none` | Evolver doesn't see pass/fail scores |
| `--no-evolve` | Disable evolution (baseline with workspace tools) |
| `--seed-workspace` | Starting workspace directory |

## Design Decisions

### Why solver-proposes?

Traditional evolution has the evolver analyze failures and generate interventions. Guided Synthesis flips this: the solver — which has deep context from actually working on the task — proposes skills, and the evolver curates. This produces higher-quality, more actionable skills.

### Why feedback=none?

Counter-intuitively, hiding pass/fail scores from the evolver improves results. When the evolver sees scores, it over-fits to surface-level patterns. With `--feedback none`, the curator judges proposals purely on generalizability, which produces more robust skills.

### Why verification focus?

On SWE-bench, the highest-leverage skills are about *verifying* fixes — finding the right test files, writing reproduction scripts, comparing before/after behavior. Code-finding and patch-writing skills tend to be too task-specific. Verification skills generalize across repos.

## Output

```
logs/<experiment>/
├── patches/              # One .diff per task
├── conversations/        # Full conversation JSON per task
├── workspace/            # Evolved workspace (skills, memory, prompts)
└── results.json          # Per-task scores and metrics
```

## Implementation

The algorithm is implemented in [`agent_evolve/algorithms/guided_synth/engine.py`](../../agent_evolve/algorithms/guided_synth/engine.py) as `GuidedSynthesisEngine`, which extends the `EvolutionEngine` base class.

Core class: `GuidedSynthesisEngine`
- `step()` — Main evolution loop (memory write + skill curation)
- `evolve()` — Standalone convenience API with git versioning
- `_curate_proposals()` — LLM-based skill curation
- `_prune_similar()` — Redundancy removal
