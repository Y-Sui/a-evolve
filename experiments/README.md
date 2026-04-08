# A-Evolve SWE-bench Experiments

## Results

### SWE-bench Verified Mini (50 instances, Django 25 + Sphinx 25)

Dataset: `MariusHobbhahn/swe-bench-verified-mini`

| Run | Model | Evolution | Resolved | Rate | API Errors | Notes |
|-----|-------|-----------|----------|------|------------|-------|
| `p1-baseline-mini` | Qwen3.5-397B-A17B | None | 11/50 (22.0%) | 11/21 (52.4%) | 29 | **废弃** — API gateway 故障 + import bug |
| `p2-guided-synth` | Qwen3.5-397B-A17B | guided_synth | 11/50 (22.0%) | 11/27 (40.7%) | 23 | **废弃** — import bug 导致进化未生效 + API 故障 |
| `p1-baseline-mini-claude` | Claude Opus 4.6 | None | 19/50 (38.0%) | 19/29 (65.5%) | 21 | **废弃** — API gateway 故障 + import bug |
| `p2-guided-synth-claude` | Claude Opus 4.6 | guided_synth | 21/50 (42.0%) | 21/29 (72.4%) | 21 | **废弃** — import bug 导致prompt进化未生效 + API 故障 |
| `p1-baseline-mini-v2` | Qwen3.5-397B-A17B | None | 22/50 (44.0%) | | 2 | v2: 修复 retry + import bug |
| `p2-guided-synth-v2` | Qwen3.5-397B-A17B | guided_synth | 28/50 (56.0%) | | 3 | v2: **+12pp vs baseline** |
| `p1-baseline-mini-claude-v2` | Claude Opus 4.6 | None | 36/50 (72.0%) | | 0 | v2: 修复 retry + import bug |
| `p2-guided-synth-claude-v2` | Claude Opus 4.6 | guided_synth | 35/50 (70.0%) | | 0 | v2: 与 baseline 持平 |
| `p3-guided-synth-bs1-mini` | Qwen3.5-397B-A17B | guided_synth (bs=1) | 21/50 (42.0%) | | 4 | 98 次进化，无累积收益 |
| `p3-guided-synth-bs1-mini` | Claude Opus 4.6 | guided_synth (bs=1) | 35/50 (70.0%) | | 0 | 98 次进化，与 bs=25 持平 |

- **Rate** 列为排除 API 错误后的实际 resolve 率
- v1 全部废弃，原因见下方 Bug 说明

### SWE-bench Verified Mini — Meta-Harness (50 instances)

Dataset: `MariusHobbhahn/swe-bench-verified-mini`

| Run | Model | Algorithm | Resolved | Notes |
|-----|-------|-----------|----------|-------|
| `p4-metaharness-mini` (baseline) | Qwen3.5-397B-A17B | — | 26/50 (52.0%) | max_tokens=65536 (vs v2 的 16384) |
| `p4-metaharness-mini` (cycle 1) | Qwen3.5-397B-A17B | meta_harness (1 cycle, k=2) | 27/50 (54.0%) | best candidate score |
| `p4-metaharness-mini` (final) | Qwen3.5-397B-A17B | meta_harness (1 cycle, k=2) | **28/50 (56.0%)** | **+4pp vs baseline, +12pp vs v2 baseline (44%)** |

#### 分析

**Proposer 改了什么**：分析 traces 发现 Qwen3.5 频繁调用 `str_replace` / `str_replace_editor`（Claude 的 tool calling 习惯），但 SweAgent 只注册了 `text_editor`，调用直接失败。Proposer 创建了两个兼容工具注册到 registry，修复了这个 agent-tool mismatch。

**关键结论**：
- max_tokens 16384→65536 贡献 +8pp（44%→52%），Meta-Harness 1 轮进化再贡献 +4pp（52%→56%）
- Meta-Harness 的价值在于自动发现 tool 层面的问题，而非改 prompt 文字——这类问题人工很难从 50 个 traces 里定位到

### Internal SWE-bench Gold (30 instances, miroflow + MiroThinker + sd-torchtune)

Dataset: `swe-workspace/data/all_instances_annotated_20260322_v2_gold.jsonl`

| Run | Model | Evolution | Resolved | API Errors | Notes |
|-----|-------|-----------|----------|------------|-------|
| `p1-baseline-internal-swe-bench-gold` | Qwen3.5-397B-A17B | None | 7/30 (23.3%) | 2 | |
| `p2-guided-synth-internal-swe-bench-gold` | Qwen3.5-397B-A17B | guided_synth | 6/30 (20.0%) | 0 | |
| `p1-baseline-internal-swe-bench-gold` | Claude Opus 4.6 | None | 9/30 (30.0%) | 0 | |
| `p2-guided-synth-internal-swe-bench-gold` | Claude Opus 4.6 | guided_synth | 2/30 (6.7%) | 0 | |
| `p3-guided-synth-bs1-internal-swe-bench-gold` | Qwen3.5-397B-A17B | guided_synth (bs=1) | 7/30 (23.3%) | 0 | 与 baseline 持平 |
| `p3-guided-synth-bs1-internal-swe-bench-gold` | Claude Opus 4.6 | guided_synth (bs=1) | 9/30 (30.0%) | 0 | 与 baseline 持平，优于 bs=25 的 6.7% |

### Internal SWE-bench Gold — Meta-Harness (30 instances)

Dataset: `swe-workspace/data/all_instances_annotated_20260322_v2_gold.jsonl`

| Run | Model | Algorithm | Resolved | Notes |
|-----|-------|-----------|----------|-------|
| p4-metaharness-gold-v2 (baseline) | Qwen3.5-397B-A17B | — | 11/30 (36.7%) | v2 config¹ |
| p4-metaharness-gold-v2 (cycle 1) | Qwen3.5-397B-A17B | meta_harness (k=2) | (incomplete) | eval 卡死 (ProcessPoolExecutor bug) |
| p4-metaharness-gold-v2 (baseline) | Claude Opus 4.6 | — | 7/30 (23.3%) | v2 config¹ |
| p4-metaharness-gold-v2 (cycle 1) | Claude Opus 4.6 | meta_harness (k=2) | **10/30 (33.3%)** | **+10pp vs baseline** |

¹ v2 config: efficiency_prompt=false, window_size=120, proposer_max_turns=200。对比 v1 config (efficiency_prompt=true, window_size=70) Qwen baseline 从 8/30 (26.7%) → 11/30 (36.7%)

#### 分析

- Proposer 发现 task metadata 里的 `hints_text` 和 `FAIL_TO_PASS` 从未传给 solver，通过创建 `harness.py` hook 注入这些信息，使 Claude 从 23.3% → 33.3%
- 对比 guided_synth 在 Gold 上 Claude 从 30% 崩到 6.7%，Meta-Harness 的 Pareto selection 保证候选不如 baseline 时不 apply，避免退步

## v1 → v2 修复的 Bug

1. **`guided_synth` import bug** — `engine.py` 引用了不存在的 `from ..aevolve.tools`（应为 `from ..skillforge.tools`），导致 skill curation LLM 调用静默失败，进化从未真正生效
2. **API retry 缺失** — transient 错误列表只包含 `ThrottlingException` 等，未覆盖 API gateway 返回的 500/503/401 错误，导致只尝试 1 次就放弃
3. **retry 增强** — 重试次数 3→5，指数退避（30s→60s→120s→240s）+ 30% jitter

## Run Commands

```bash
# 所有实验通过 scripts/run_experiment.py 运行，config 在对应目录下
uv run python scripts/run_experiment.py --config <config_path>

# SWE-bench Verified Mini (v2)
uv run python scripts/run_experiment.py --config experiments/qwen35-swe/configs/p1-baseline-mini-v2.yaml
uv run python scripts/run_experiment.py --config experiments/qwen35-swe/configs/p2-guided-synth-v2.yaml
uv run python scripts/run_experiment.py --config experiments/claude-swe/configs/p1-baseline-mini-claude-v2.yaml
uv run python scripts/run_experiment.py --config experiments/claude-swe/configs/p2-guided-synth-claude-v2.yaml

# Internal SWE-bench Gold
uv run python scripts/run_experiment.py --config experiments/qwen35-swe/configs/p1-baseline-internal-swe-bench-gold.yaml
uv run python scripts/run_experiment.py --config experiments/qwen35-swe/configs/p2-guided-synth-internal-swe-bench-gold.yaml
uv run python scripts/run_experiment.py --config experiments/claude-swe/configs/p1-baseline-internal-swe-bench-gold.yaml
uv run python scripts/run_experiment.py --config experiments/claude-swe/configs/p2-guided-synth-internal-swe-bench-gold.yaml
```

## Common Config

| Parameter | Baseline | Evolution |
|-----------|----------|-----------|
| Max steps | 140 | 140 |
| Window size | 70 | 70 |
| Max tokens | 16384 | 16384 |
| Efficiency prompt | true | true |
| Feedback | none | none |
| Algorithm | — | guided_synth |
| Solver proposes | — | true |
| Verification focus | — | true |
| Max retries | 5 | 5 |

## API 说明

- 所有实验通过 `api.miromind.site` 代理转发至 OpenRouter
- v1 的 API 故障均来自代理层：数据库连接池满（500）、路由失败（503）、token 校验失败（401）
- v2 增加了指数退避重试，预期可消除大部分瞬态错误

## Directory Structure

```
experiments/
├── README.md
├── qwen35-swe/
│   ├── configs/
│   │   ├── p1-baseline-mini.yaml              # v1 废弃
│   │   ├── p1-baseline-mini-v2.yaml
│   │   ├── p2-guided-synth.yaml               # v1 废弃
│   │   ├── p2-guided-synth-v2.yaml
│   │   ├── p1-baseline-internal-swe-bench-gold.yaml
│   │   └── p2-guided-synth-internal-swe-bench-gold.yaml
│   └── logs/
│       ├── p1-baseline-mini/                   # v1 废弃
│       ├── p1-baseline-mini-v2/
│       ├── p2-guided-synth/                    # v1 废弃
│       ├── p2-guided-synth-v2/
│       ├── p1-baseline-internal-swe-bench-gold/
│       ├── p2-guided-synth-internal-swe-bench-gold/
│       ├── p3-guided-synth-bs1-mini/
│       ├── p3-guided-synth-bs1-internal-swe-bench-gold/
│       └── p4-metaharness-mini/
└── claude-swe/
    ├── configs/
    │   ├── p1-baseline-mini-claude.yaml        # v1 废弃
    │   ├── p1-baseline-mini-claude-v2.yaml
    │   ├── p2-guided-synth-claude.yaml         # v1 废弃
    │   ├── p2-guided-synth-claude-v2.yaml
    │   ├── p1-baseline-internal-swe-bench-gold.yaml
    │   └── p2-guided-synth-internal-swe-bench-gold.yaml
    └── logs/
        ├── p1-baseline-mini-claude/            # v1 废弃
        ├── p1-baseline-mini-claude-v2/
        ├── p2-guided-synth-claude/             # v1 废弃
        ├── p2-guided-synth-claude-v2/
        ├── p1-baseline-internal-swe-bench-gold/
        ├── p2-guided-synth-internal-swe-bench-gold/
        ├── p3-guided-synth-bs1-mini/
        └── p3-guided-synth-bs1-internal-swe-bench-gold/
```
