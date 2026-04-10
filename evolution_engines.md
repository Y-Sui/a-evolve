# Evolution Engines

a-evolve 提供了多种进化引擎，每种引擎代表不同的进化策略。引擎的核心职责是：**分析 agent 的执行结果（trajectories + feedback），决定如何修改 workspace 文件（prompts, skills, memory, tools）来提升 agent 性能。**

## 总览

| Engine | 核心思路 | Proposer | 信号来源 | 候选数量 | 评测方式 | 选择策略 | 安全机制 | 复杂度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **MetaHarnessEngine** | 给 proposer 完整文件系统访问（archive + traces），让它自主浏览和修改 | Claude Code CLI | 完整 trajectories（每条消息+工具调用） | k 个并行候选 | 每个候选独立评测 | Pareto frontier（score↑ cost↓） | 语法检查 + 泄漏审计 + 可选回滚 | 高 |
| **AEvolveEngine** | 给 evolver LLM 一个 bash 工具，让它读摘要、改文件 | Bedrock LLM + bash tool | 压缩摘要（300 字反馈 or judge 评分） | 1 | 观察已完成任务 | 直接应用 | 无 | 低 |
| **AdaptiveEvolveEngine** | 多层程序化分析 → 自动修复 → LLM 精细化进化 → 事后清理 | Bedrock LLM + bash tool | 结构化分析（claim/task 类型、judge 反馈、failure patterns） | 1 | 观察已完成任务 | 直接应用 + 停滞回滚 | 6 项清理 + 停滞回滚 + 分级进化 | 高 |
| **GuidedSynthesisEngine** | Solver 自己提案 skill → Curator LLM 审核 ACCEPT / MERGE / SKIP | Curator LLM | Solver 提案 + 置信度 | 1 | 观察已完成任务 | Curator 决定 | 无 | 低 |

---

## Engine 1: MetaHarnessEngine ⭐

**核心创新：Proposer 拥有完整的文件系统访问能力，可以浏览历史所有候选的源码、评分、执行 traces，自主决定修改策略。**

### 架构

```
每个 Cycle:
  ┌─ Propose (串行，k 次) ─────────────────────────────────┐
  │  for i in range(k):                                     │
  │    1. Reset workspace 到 clean state                    │
  │    2. 构建 proposer prompt（指向 archive 目录）          │
  │    3. 启动 Claude Code CLI → proposer 浏览文件系统       │
  │       - cat evolution/observations/batch_*.jsonl         │
  │       - diff 历史候选 vs 当前 workspace                  │
  │       - grep 失败模式                                    │
  │       - 修改 prompts/skills/memory/tools/harness.py     │
  │    4. 捕获 git diff → 记录候选                           │
  │    5. 验证：语法检查 + 泄漏审计                          │
  │    6. 快照 workspace 文件                                │
  └────────────────────────────────────────────────────────┘
  │
  ┌─ Evaluate (可并行) ────────────────────────────────────┐
  │  for each candidate:                                    │
  │    1. 创建临时 workspace 副本                            │
  │    2. Apply candidate 的 diff                           │
  │    3. 在评测任务上运行 agent                             │
  │    4. 计算 score + cost（token 用量）                    │
  └────────────────────────────────────────────────────────┘
  │
  ┌─ Select ───────────────────────────────────────────────┐
  │  1. 过滤无效候选                                        │
  │  2. 计算 Pareto frontier（score↑ + cost↓）              │
  │  3. 从 frontier 中选最高分候选                           │
  │  4. 归档所有候选（含源码快照、评分、diff、reasoning）     │
  │  5. Apply 选中候选的 diff                                │
  └────────────────────────────────────────────────────────┘
```

### Trajectory 和 Feedback 如何作为进化信号

**Trajectories 以完整 JSONL 形式存储在 `evolution/observations/` 目录下：**

```json
{
  "task_id": "django__django-13790",
  "task_input": "Fix the issue where...",
  "success": false,
  "score": 0.0,
  "feedback_detail": "FAIL_TO_PASS: test_admin failed...",
  "conversation": [
    {"role": "user", "content": "...problem statement..."},
    {"role": "assistant", "content": "Let me explore the codebase..."},
    {"role": "tool", "content": "...", "name": "bash", "input": {"command": "grep -r 'get_list_display' /testbed"}},
    {"role": "assistant", "content": "I see the issue. Let me edit..."},
    {"role": "tool", "content": "...", "name": "text_editor", "input": {"file": "/testbed/django/contrib/admin/options.py", "command": "str_replace", "old_str": "...", "new_str": "..."}},
    {"role": "assistant", "content": "Now let me run tests..."},
    {"role": "tool", "content": "FAILED: test_dynamic_list_display...", "name": "bash"},
    {"role": "assistant", "content": "Tests still failing, let me try a different approach..."}
  ]
}
```

**Proposer 通过文件系统命令自主浏览这些 traces：**

```bash
# 查看最新 batch 的失败任务
cat evolution/observations/batch_*.jsonl | jq 'select(.success == false) | .task_id'

# 查看特定任务的完整对话
cat evolution/observations/batch_*.jsonl | jq 'select(.task_id == "django__django-13790") | .conversation'

# 搜索所有任务中的常见错误
grep -r "FAILED" evolution/observations/ | head -20

# 比较历史候选的效果
cat evolution/candidates/*/scores.json | jq '{cycle, score, selected}'

# 从最佳候选复制策略
diff evolution/candidates/cycle_002_cand_0/snapshot/prompts/system.md prompts/system.md
```

**关键设计原则：不给 proposer 压缩摘要，让它自己浏览原始数据。** 这让 proposer 能发现压缩过程中可能丢失的细节。

### Candidate Archive 结构

```
evolution/candidates/
├── cycle_001_cand_0/
│   ├── snapshot/                    # 提案时的完整 workspace 快照
│   │   ├── prompts/system.md
│   │   ├── skills/*/SKILL.md
│   │   ├── memory/*.jsonl
│   │   ├── tools/*.py
│   │   └── harness.py
│   ├── scores.json                  # 评测结果
│   │   {
│   │     "cycle": 1,
│   │     "candidate_index": 0,
│   │     "score": 0.456,
│   │     "cost": 45000,           # token 总消耗
│   │     "valid": true,
│   │     "selected": true,
│   │     "pareto_optimal": true,
│   │     "proposer_model": "anthropic/claude-opus-4.6"
│   │   }
│   ├── changes.diff                 # proposer 的修改（git diff）
│   ├── proposer_reasoning.md        # Claude Code 的输出文本
│   └── traces/                      # 指向 observation batch 的 symlink
├── cycle_001_cand_1/
│   └── ...
├── cycle_002_cand_0/
│   └── ...
```

**Archive 跨 run 持久化**——后续进化 run 能看到之前所有候选的源码和评分，形成知识累积。

### Pareto Selection

```
Score ↑
  │          * cand_0 (0.56, 30k tokens) ← Pareto optimal + 最高分 → SELECTED
  │        
  │      * cand_2 (0.52, 25k tokens) ← Pareto optimal（更便宜但分低）
  │   
  │  * cand_1 (0.48, 35k tokens) ← 被 cand_0 dominate（分更低且更贵）
  │
  └──────────────────────────────── Cost ↓
```

选择逻辑：从 Pareto frontier 中挑分最高的。允许临时退化（score 下降）以换取探索空间。

### 验证与安全

| 检查 | 做什么 | 失败后果 |
| --- | --- | --- |
| **语法检查** | 编译 harness.py, tools/*.py | 候选标记为 invalid，score=0 |
| **Prompt 非空** | 检查 prompts/system.md 不为空 | 候选标记为 invalid |
| **泄漏审计** | 正则扫描 workspace 文件，检测硬编码的 task ID（≥8 字符匹配） | 候选标记为 invalid + 警告 |
| **可选回滚** | rollback_on_regression=True 时，如果最佳候选分数低于历史最优 → 不 apply | 返回 mutated=False |

### Claude Code CLI 调用细节

```bash
claude -p "<proposer_prompt>" \
  --model opus \
  --max-turns 50 \
  --dangerously-skip-permissions \
  --output-format json \
  --no-session-persistence
```

- **Working directory**: workspace root（Claude Code 自动发现 CLAUDE.md）
- **路由**: 通过 OpenRouter proxy（`ANTHROPIC_BASE_URL` + `OPENROUTER_API_KEY`）
- **模型映射**: OpenRouter ID (`anthropic/claude-opus-4.6`) → Claude Code alias (`opus`)
- **超时**: 600 秒
- **非交互**: `--dangerously-skip-permissions` 跳过审批

### Proposer Prompt 核心内容

```
You are a harness optimizer improving an AI agent's benchmark performance.

## Archive Access
Browse `evolution/candidates/` to see all prior proposals:
- `cat evolution/candidates/*/scores.json | jq .score`
- `diff evolution/candidates/<best>/snapshot/harness.py ./harness.py`
- Can copy code from best candidates

## Workspace Layout
prompts/system.md, skills/*/SKILL.md, memory/*.jsonl, tools/, harness.py

## Observations
Browse `evolution/observations/batch_*.jsonl` for FULL execution traces.
Each entry contains the complete conversation (every message + tool call).

## Score History
<score progression: 0.456 → 0.478 → 0.512>

## Constraints
- Cannot modify evolution/ (read-only archive)
- No hardcoded task IDs (leakage = invalid)
```

---

## Engine 2: AEvolveEngine

**核心思路：最简单的进化——把任务结果摘要给 LLM，让它用 bash 工具自己改文件。**

### 架构

```
observations → 压缩为摘要 → evolver LLM prompt
                                    │
                              evolver LLM + bash 工具
                                    │
                              直接修改 workspace 文件
```

### 信号传递

**标准模式（trajectory_only=False）：** 每个任务压缩为一行摘要：

```json
{"task_id": "task_123", "success": false, "score": 0.0, "feedback": "FAIL_TO_PASS: test_admin...（截断到 300 字符）"}
```

**Trajectory-only 模式（trajectory_only=True）：** 不给反馈分数，而是给行为信号：

```json
{
  "task_id": "task_123",
  "signals": {
    "n_turns": 15,
    "n_tool_calls": 42,
    "n_errors": 2,
    "tools_used": {"bash": 30, "read_file": 12},
    "submitted": true,
    "repeated_commands": ["grep -r 'pattern' /testbed"],
    "error_snippets": ["ERROR: file not found"]
  },
  "judge_verdict": {
    "score": 3,
    "category": "wrong_file",
    "outcome": "Edited wrong module",
    "failure_reason": "Didn't trace the traceback to find correct file"
  }
}
```

Judge verdict 由一个单独的 LLM 评审步骤生成（将 trajectory 压缩后让 LLM 打分 0-10 + 分类）。

### Evolver System Prompt

```
You are a meta-learning agent that improves another agent by modifying its workspace files.

The workspace:
- prompts/system.md  -- the agent's system prompt
- skills/*/SKILL.md  -- reusable skill definitions
- memory/*.jsonl     -- episodic and semantic memory

Your job each cycle:
1. Analyze task observation logs — identify patterns, common failures, recurring themes
2. Review draft skills — refine into real skills, merge with existing, or discard
3. Improve the system prompt if needed
4. Update memory with high-level insights, prune redundant entries
5. Use the provided bash tool to read/write files in the workspace
6. Verify your changes with `git diff` before finishing
```

### 变体模式

| 模式 | 配置 | 行为 |
| --- | --- | --- |
| **标准** | 默认 | 看 score + feedback → 改 prompts + skills + memory |
| **Trajectory-only** | `trajectory_only=True` | 看行为信号 + judge 评分（不给真实分数）→ 改文件 |
| **Prompt-only** | `prompt_only=True` | 只改 prompts/system.md，不碰 skills |
| **Solver-proposed** | `solver_proposed=True` | 重点看 solver 的 draft skills，泛化为正式 skill |
| **Protect skills** | `protect_skills=True` | 只能创建新 skill，不能修改/删除已有的 |

---

## Engine 3: AdaptiveEvolveEngine

**核心思路：程序化多层分析 → 确定性自动修复 → LLM 精细化进化 → 确定性事后清理 → 停滞回滚。**

### 架构（8 个 Phase）

```
Phase 1: Base Analysis
  └── 提取工具错误、幻觉映射、策略问题、pass rate

Phase 2: Code Execution Analysis
  └── 统计 execute_code 使用率、miss opportunities

Phase 3: Adaptive Analysis（核心层）
  ├── Claim 类型分析：哪种 requirement 失败最多
  ├── Task 类型分析：哪类任务表现差
  ├── Judge 反馈挖掘：评审给的失败原因归类
  └── Failure Pattern 检测：系统性问题识别

Phase 4: Auto-corrections（确定性）
  ├── 工具名幻觉修复（McpAutoCorrector）
  └── Memory 裁剪（上限 15 条）

Phase 5: Auto-seed Skills（确定性）
  ├── multi_requirement_miss ≥3 次 → 注入 multi-requirement-handler
  ├── wrong_entity ≥2 次 → 注入 entity-verification
  └── 某 claim 类型 pass rate <50% → 注入 {type}-handler

Phase 6: Graduated Scope → LLM Evolution
  ├── pass rate ≥90% + 稳定 2 轮 → 跳过进化
  ├── ≥90% → 极小改动（只动 skill）
  ├── ≥85% → 只改弱 claim 类型的 skill
  ├── ≥70% → 定向改 skill + prompt
  └── <70% → 全面改

Phase 7: Workspace Sanity Check（确定性）
  ├── 截断过长 prompt（保留 seed）
  ├── 删空 skill（body < 20 字符）
  ├── 去重 skill（Jaccard > 0.6）
  ├── 去过拟合 pattern（batch 号、task ID）
  ├── 超 15 个 skill → 删多余
  └── Seed 开头段落被删 → 恢复

Phase 8: Stagnation Gate
  └── 连续 5 轮无 ≥2% 提升 → git rollback 到最优 tag
```

### 分析数据结构

```python
@dataclass
class AdaptiveAnalysisResult:
    base_analysis: BatchAnalysis        # 基础统计
    code_stats: CodeExecStats           # 代码执行分析
    claim_stats: dict[str, ClaimStats]  # 按 claim 类型的表现
    task_type_stats: dict[str, TaskTypeStats]  # 按 task 类型的表现
    judge_patterns: dict[str, list]     # 评审反馈模式
    failure_patterns: list[FailurePattern]  # 系统性失败模式
    weakest_claim_types: list[tuple[str, float]]  # 最弱 claim 类型
    weakest_task_types: list[tuple[str, float]]   # 最弱 task 类型
    evolution_recommendations: list[str]  # 进化建议
```

### Evolution Prompt 结构

Evolver LLM 收到的 prompt 包含所有分析结果：

```markdown
# Evolution Cycle 5

## Batch Summary
- Tasks: 10, Passed: 6, Failed: 4, Pass Rate: 60%

## Claim-Type Performance（哪类需求失败最多）
### calculate: 30% pass rate (1/3 fulfilled)
  Failed: "Get the difference between repo creation date and domain registration"
  Why: Agent found both dates but didn't compute the difference

## Task-Type Performance（哪类任务表现差）
- multi_requirement: 40% (2/5 tasks)
- single_fact: 80% (4/5 tasks)

## Judge Feedback Patterns（评审常见失败原因）
### missing_requirement: 3 occurrences
  Example: "Agent addressed requirement 1 but completely skipped requirement 2"

## Detected Failure Patterns（系统性问题）
### multi_requirement_miss: 3 tasks
  Suggested Fix: Create multi-requirement extraction protocol

## Evolution History（什么改动有效/有害）
### ✓ Successful: Cycle 3 added entity-verification skill (+8%)
### ✗ Harmful: Cycle 4 rewrote system prompt (-3%)

## Current Workspace
- System prompt: 2100 chars
- Skills: 5/15 (entity-verification, multi-req-handler, ...)

## Your Task
Failure patterns detected. Make SURGICAL fixes:
- Address the top 1-2 failure patterns only
- Don't change things that are already working
```

### 关键 Feature 详解

**分级进化（Graduated Scope）：**

| Pass Rate | 进化强度 | 允许的修改 |
| --- | --- | --- |
| ≥90% + 稳定 2 轮 | 跳过 | 无 |
| ≥90% | 极小 | 只动 skill |
| ≥85% | 极小 | 只改弱 claim 类型的 skill |
| ≥70% | 定向 | Skill + prompt（如果有 failure pattern） |
| <70% | 全面 | Prompt + skill |

**停滞回滚：**

```
Cycle 1: score 0.45 → best=0.45, stagnation=0
Cycle 2: score 0.52 → best=0.52, stagnation=0  (improved ≥2%)
Cycle 3: score 0.51 → stagnation=1
Cycle 4: score 0.50 → stagnation=2
Cycle 5: score 0.49 → stagnation=3
Cycle 6: score 0.48 → stagnation=4
Cycle 7: score 0.47 → stagnation=5 → ROLLBACK to "pre-evo-2" tag (score=0.52)
```

**Auto-seed Skills：**

检测到特定失败模式 → 立即注入预写好的 skill（不等 LLM 判断）：

| 失败模式 | 触发条件 | 注入的 Skill |
| --- | --- | --- |
| multi_requirement_miss | ≥3 次 | multi-requirement-handler: 提取所有需求 → 逐个解决 → 逐个验证 |
| wrong_entity_targeting | ≥2 次 | entity-verification: 第一次工具调用后验证实体是否正确 |
| 弱 claim 类型 | pass rate <50% | {type}-handler: 针对该类型的操作指南 |

---

## Engine 4: GuidedSynthesisEngine

**核心思路：让 solver agent 在解题后自己提出 skill 提案，由 curator LLM 审核决定接受、合并还是丢弃。**

### 架构

```
Phase 1: Write Memory（每个 observation）
  └── 写入 episodic memory: {task_id, cycle, score, files_edited, approach_summary}

Phase 2: Curate Skills（LLM 审核）
  ├── 从 trajectory 中提取 solver 的 skill 提案
  │   格式: CONFIDENCE: HIGH/MEDIUM/LOW
  │          ACTION: NEW/ENHANCE
  │          NAME: verify-before-edit
  │          DESCRIPTION: ...
  │          CONTENT: ...
  │
  ├── 构建 curator prompt（当前 skills + 所有提案）
  │
  └── Curator LLM 逐个审核:
      ├── ACCEPT: <name>  → 直接添加
      ├── MERGE: <name> INTO <existing_skill>  → 合并到已有 skill
      └── SKIP: <name>  → 丢弃
```

### Curator Prompt 核心指令

```
You are a SKILL CURATOR. Decide for each proposal: ACCEPT, MERGE, or SKIP.

Decision criteria:
- HIGH confidence proposals → lean towards ACCEPT
- LOW confidence proposals → lean towards SKIP
- ENHANCE proposals: ACCEPT if adds value
- NEW proposals: FIRST check overlap with existing. If so, MERGE.
- Prefer 5-10 broad skills over 30 narrow ones.
- NEVER shrink existing skills when merging.
```

### Verification Focus 模式

专门用于进化验证类 skills：

| 接受 | 拒绝 |
| --- | --- |
| 如何找测试文件 | 如何找代码 |
| 如何写复现脚本 | 如何写 patch |
| 修改前后对比 | 调试逻辑 |
| 边界情况测试 | |

---

## 引擎选择指南

### 按场景推荐

| 场景 | 推荐引擎 | 原因 |
| --- | --- | --- |
| **首次尝试进化** | AEvolveEngine | 最简单，容易调试，快速验证进化概念 |
| **成熟的进化 pipeline** | MetaHarnessEngine | 多候选搜索 + Pareto 选择 + 完整 trace 分析 = 最强进化效果 |
| **有结构化反馈的 benchmark** | AdaptiveEvolveEngine | 多层分析充分利用结构化反馈（claim 类型、judge 评语） |
| **希望 solver 参与进化** | GuidedSynthesisEngine | Solver 基于实际解题经验提案，curator 把关质量 |
| **SWE-bench** | MetaHarnessEngine 或 AEvolveEngine | SWE-bench 反馈是 test output，MetaHarness 让 proposer 自己浏览完整 traces 最有效 |

### 按成本排序（低→高）

```
GuidedSynthesis（1 次 curator LLM 调用）
  ↓
AEvolve（1 次 evolver LLM + bash 循环，~10-30 轮工具调用）
  ↓
AdaptiveEvolve（程序化分析 + 1 次 evolver LLM + bash）
  ↓
MetaHarness（k 次 Claude Code CLI + k 次完整评测）
```

### 可组合使用

引擎不是互斥的。可以组合：

- **GuidedSynthesis + AEvolve**：solver 提案 skill → curator 审核 → evolver 补充改 prompt
- **AEvolve → MetaHarness**：先用 AEvolve 快速迭代找到大方向，再用 MetaHarness 精细优化

---

## 核心接口

所有引擎实现同一个接口：

```python
class EvolutionEngine(ABC):
    @abstractmethod
    def step(
        self,
        workspace: AgentWorkspace,
        observations: list[Observation],
        history: EvolutionHistory,
        trial: TrialRunner,
    ) -> StepResult:
        """Run one evolution step. Mutate workspace as needed.
        
        Args:
            workspace: 读写 workspace 文件
            observations: (task, trajectory, feedback) 三元组列表
            history: 查询历史 cycles 和 observations
            trial: 可选——在当前 workspace 上跑任务评测
        
        Returns:
            StepResult(mutated=True/False, summary="...", metadata={...})
        """
    
    def on_cycle_end(self, accepted: bool, score: float) -> None:
        """Optional callback after each cycle."""
```
