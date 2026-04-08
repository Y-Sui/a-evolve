---
name: Qwen3.5 SWE-bench experiment
description: Running self-evolution experiments with Qwen3.5-397B on SWE-bench Verified via OpenRouter
type: project
---

Qwen3.5-397B-A17B self-evolution experiment on SWE-bench Verified.

**Why:** Validate that A-Evolve can improve a model's SWE-bench performance through self-evolution. Qwen official score is 76.4%.

**How to apply:**
- Configs in `experiments/qwen35-swe/configs/`
- Run via `scripts/run_experiment.py --config <yaml>`
- Results analyzed via `scripts/analyze_results.py`
- Two core experiments: p1-baseline-mini (no evolve) vs p2-guided-synth (evolve with v32g config)
- `evolve_sequential.py` now supports `--algorithm` flag (guided_synth/skillforge/adaptive_skill)
- Model accessed via OpenRouter env vars in `.env`
