# Scripts

实验运行与分析脚本。所有命令在项目根目录下执行。所有实验使用 tmux 启动。

## 前置条件

```bash
# 安装依赖
uv sync --extra all

# 确认 .env 中已配置 OpenRouter
# OPENROUTER_API_KEY=...
# OPENROUTER_BASE_URL=...
```

## 当前实验（SWE-bench Verified Mini, 50 tasks）

### Claude Opus 4.6

```bash
# Baseline（无进化）
tmux new-session -d -s p1-claude \
  "uv run python scripts/run_experiment.py --config experiments/claude-swe/configs/p1-baseline-mini-claude.yaml \
   2>&1 | tee experiments/claude-swe/logs/p1-baseline-mini.log"

# Evolution（GuidedSynthesis 全层进化）
tmux new-session -d -s p2-claude \
  "uv run python scripts/run_experiment.py --config experiments/claude-swe/configs/p2-guided-synth-claude.yaml \
   2>&1 | tee experiments/claude-swe/logs/p2-guided-synth.log"
```

### Qwen3.5-397B-A17B

```bash
# Baseline（无进化）
tmux new-session -d -s p1-qwen \
  "uv run python scripts/run_experiment.py --config experiments/qwen35-swe/configs/p1-baseline-mini.yaml \
   2>&1 | tee experiments/qwen35-swe/logs/p1-baseline-mini.log"

# Evolution（GuidedSynthesis 全层进化）
tmux new-session -d -s p2-qwen \
  "uv run python scripts/run_experiment.py --config experiments/qwen35-swe/configs/p2-guided-synth.yaml \
   2>&1 | tee experiments/qwen35-swe/logs/p2-guided-synth.log"
```

## 监控实验

```bash
# 查看所有 session
tmux ls

# 查看某个实验的实时输出
tmux attach -t p1-claude    # Ctrl+B D 退出

# 查看日志尾部
tail -20 experiments/claude-swe/logs/p1-baseline-mini.log
tail -20 experiments/qwen35-swe/logs/p1-baseline-mini.log
```

## 分析结果

```bash
# Claude 实验对比
uv run python scripts/analyze_results.py experiments/claude-swe/logs/

# Qwen 实验对比
uv run python scripts/analyze_results.py experiments/qwen35-swe/logs/

# 输出到文件
uv run python scripts/analyze_results.py experiments/claude-swe/logs/ --output experiments/claude-swe/results/summary.md
```

## 备选实验

### Qwen: 其他算法（Mini 50 tasks）

```bash
# SkillForge
tmux new-session -d -s p2-qwen-sf \
  "uv run python scripts/run_experiment.py --config experiments/qwen35-swe/configs/p2-skillforge.yaml \
   2>&1 | tee experiments/qwen35-swe/logs/p2-skillforge.log"

# AdaptiveSkill
tmux new-session -d -s p2-qwen-as \
  "uv run python scripts/run_experiment.py --config experiments/qwen35-swe/configs/p2-adaptive-skill.yaml \
   2>&1 | tee experiments/qwen35-swe/logs/p2-adaptive-skill.log"
```

### Full-Scale Validation（500 tasks）

```bash
# Qwen Baseline
tmux new-session -d -s p3-qwen-base \
  "uv run python scripts/run_experiment.py --config experiments/qwen35-swe/configs/p3-baseline-full.yaml \
   2>&1 | tee experiments/qwen35-swe/logs/p3-baseline-full.log"

# Qwen Best config（运行前更新 YAML 中的 algorithm）
tmux new-session -d -s p3-qwen-best \
  "uv run python scripts/run_experiment.py --config experiments/qwen35-swe/configs/p3-best-full.yaml \
   2>&1 | tee experiments/qwen35-swe/logs/p3-best-full.log"
```

## Dry Run

任何实验均可加 `--dry-run` 查看命令而不执行：

```bash
uv run python scripts/run_experiment.py --config experiments/qwen35-swe/configs/p0-smoke.yaml --dry-run
```
