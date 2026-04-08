# Layers to Evolve

a-evolve 将 agent 的能力分解为四个可独立进化的层（Layer）。每一层封装了 agent 的不同维度：从行为策略（Prompts）、领域知识（Skills）、历史经验（Memory）到可执行能力（Tools）。

其中 **Prompts、Skills、Memory** 三层默认开启进化，因为它们本质上都是文本或结构化数据，变异风险低、可回滚。**Tools** 层由于涉及可执行代码的修改，进化风险较高，默认关闭，需显式启用。

## 总览

| Layer        | 核心用途                                       | 本质                 | 必需文件                            | 文件格式                       | 默认进化  | 加载方式                                                        | 进化方式                                            | 进化风险 | Seed 阶段            |
| ------------ | ---------------------------------------------- | -------------------- | ----------------------------------- | ------------------------------ | --------- | --------------------------------------------------------------- | --------------------------------------------------- | -------- | -------------------- |
| **Prompts**  | 定义 agent 的问题解决策略与行为规则            | 文本（策略/规则）    | `system.md`                         | Markdown                       | `true`    | 初始化时全量读入 `system.md`；可组合 fragments                  | 分析失败 trajectory → 追加策略规则                  | 低       | 提供初始 `system.md` |
| **Skills**   | 维护可复用的领域知识库，封装特定类别问题的解决过程 | 文本（领域知识/过程） | `{name}/SKILL.md`（含 YAML frontmatter） | Markdown + YAML frontmatter    | `true`    | 初始化只读元数据列入系统提示词；正文通过 `read_skill()` 按需懒加载 | 提案→审核循环（ACCEPT / MERGE / SKIP）；也可直接变异 | 低       | 空                   |
| **Memory**   | 存储任务执行中的结构化学习成果，提供历史上下文 | 结构化数据（执行记录） | `episodic.jsonl`, `semantic.jsonl`  | JSONL（每行一个 JSON）         | `true`    | 初始化 `read_all_memories()`，按 `task_id` 过滤注入 user prompt | Episodic: 记录每次任务结果；Semantic: 综合跨任务模式 | 低       | 空                   |
| **Tools**    | 提供 agent 可调用的可执行工具实现              | 可执行代码（Python） | `registry.yaml` • `*.py`           | YAML + Python（`@tool` 装饰器） | `false`   | 读 `registry.yaml` → 动态 import `.py` → 注册到 strands agent  | 修改实现 / 添加新工具 / 调整描述（默认关闭）        | 高       | 提供初始工具集       |

---

## Examples

### Prompts

**`prompts/system.md`:**

```markdown
You are an expert software engineer tasked with resolving GitHub issues by producing code patches.

## Approach:
1. Understand the issue
2. Locate relevant code
3. Plan the fix
4. Implement the fix
5. Verify
```

**进化追加的规则：**

- `"当构建失败时，先完整阅读错误信息再尝试修复。"`
- `"提交前，验证解决方案满足所有明确要求。"`

**`prompts/fragments/django-conventions.md`:**

```markdown
## Django-Specific Conventions
- Always check if migrations are needed after model changes.
- Use queryset.exists() instead of len(queryset) > 0.
```

---

### Skills

**`skills/entity-verification/SKILL.md`:**

```yaml
# frontmatter
name: entity-verification
description: Strategies for verifying entities against a data source before responding.
```

```markdown
## When to Apply
Use when cross-referencing claims against a structured dataset.

## Procedure
1. Identify all entities
2. Execute targeted lookup
3. Compare returned values
4. Flag mismatches
```

**`skills/test-driven-debug/SKILL.md`:**

```yaml
# frontmatter
name: test-driven-debug
description: Use failing test output to guide debugging when a patch breaks existing tests.
```

```markdown
1. Run broken test with -v
2. Read assertion error
3. Trace back to edited code
4. Fix root cause not symptom
```

**`skills/_drafts/import-cycle-fix.md`:** 草稿暂存，等待审核循环决定 ACCEPT / MERGE / SKIP

---

### Memory

**`memory/episodic.jsonl`（每行一条）：**

```json
{"task_id": "django__django-13790", "cycle": 2, "score": 0.0, "files_edited": ["django/db/models/fields/related.py"], "approach_summary": "Edited field descriptor to handle None values. Target tests still failing.", "fail_to_pass_failing": ["test_admin__test_dynamic_list_display"], "pass_to_pass_broken": []}
```

```json
{"task_id": "django__django-13790", "cycle": 3, "score": 1.0, "files_edited": ["django/db/models/fields/related.py", "django/contrib/admin/options.py"], "approach_summary": "Also patched ModelAdmin.get_list_display. All tests pass.", "fail_to_pass_failing": [], "pass_to_pass_broken": []}
```

```json
{"task_id": "flask__flask-4992", "cycle": 1, "score": 0.0, "files_edited": ["src/flask/app.py"], "approach_summary": "Tried modifying error handler registration. Wrong approach — issue is in Blueprint."}
```

**`memory/semantic.jsonl`：**

```json
{"pattern": "Django admin 相关任务通常需要检查 ModelAdmin.get_list_display", "source_tasks": ["django__django-13790", "django__django-14855"], "confidence": 0.8}
```

```json
{"pattern": "When fixing Flask blueprint issues, always check both Blueprint and App level registrations", "source_tasks": ["flask__flask-4992", "flask__flask-5014"], "confidence": 0.7}
```

---

### Tools

**`tools/registry.yaml`:**

```yaml
tools:
  - name: bash
    description: "Execute a bash command inside the Docker container."
    file: bash.py
  - name: text_editor
    description: "Edit files with structured commands."
    file: text_editor.py
  - name: python_exec
    description: "Execute Python code directly."
    file: python_exec.py
  - name: submit
    description: "Submit your solution and end the task."
    file: submit.py
```

**`tools/bash.py`:**

```python
from strands import tool

@tool
def bash(command: str, workdir: str = "/testbed") -> str:
    """Execute a bash command inside the Docker container."""
    import subprocess
    result = subprocess.run(
        command, shell=True, cwd=workdir,
        capture_output=True, text=True, timeout=120
    )
    return result.stdout + result.stderr
```

**`reset()` 函数：**

```python
def reset(**kwargs):
    """Called before each task to reset tool state."""
    pass
```

---

## Evolution Guide for the Agent

This section is written as a reference prompt for the evolving agent itself. It explains what each layer is, how they differ, and how to modify them effectively.

### Understanding the Four Layers

You have four layers of scaffolding that shape how you solve tasks. Each layer serves a distinct purpose and should be evolved differently.

**Prompts** are your strategic brain. They define *how you think* — your overall approach to problem-solving, the rules you follow, and the heuristics you apply. Prompts are loaded fully into your system context at the start of every task. When you evolve prompts, you are changing your default reasoning behavior across all future tasks.

**Skills** are your knowledge library. They capture *what you know* about specific problem domains — reusable procedures, patterns, and domain conventions. Unlike prompts, skills are lazy-loaded: only their names and descriptions are visible initially, and the full content is read on demand via `read_skill()`. When you evolve skills, you are building up specialized expertise that can be selectively applied.

**Memory** is your experience journal. It records *what happened* — the concrete results of past task attempts (episodic) and the cross-task patterns you have distilled (semantic). Memory provides historical context so you avoid repeating mistakes and can leverage proven approaches. Memory evolves automatically through task execution.

**Tools** are your hands. They define *what you can do* — the executable capabilities available to you (run shell commands, edit files, execute code, etc.). Tools are code, not text. Evolving tools means modifying Python implementations or adding new ones, which carries the highest risk since a broken tool can make you unable to act at all.

### Key Distinctions

| Question | Prompts | Skills | Memory | Tools |
| --- | --- | --- | --- | --- |
| What does it control? | General reasoning strategy | Domain-specific procedures | Historical task context | Executable capabilities |
| When is it loaded? | Always (full system prompt) | On demand (`read_skill()`) | Filtered by task similarity | At initialization (registry) |
| What format? | Free-form Markdown | Structured Markdown + YAML | JSONL records | Python code + YAML registry |
| Scope of impact? | All tasks | Tasks matching the domain | Tasks similar to past ones | All tasks using that tool |
| Risk of bad evolution? | Low — worst case is a suboptimal strategy | Low — worst case is irrelevant advice | Low — worst case is misleading context | High — can break task execution entirely |

### How to Decide What to Evolve

When a task fails, diagnose the root cause before choosing which layer to evolve:

1. **"I had the right tools and knowledge but chose a bad approach."**
   → Evolve **Prompts**. Add or refine a strategic rule in `system.md`.
   - Example: You kept trying to fix symptoms instead of root causes → add a rule: *"Before patching, trace the error to its origin. Fix the cause, not the symptom."*

2. **"I lacked domain-specific knowledge that would have helped."**
   → Evolve **Skills**. Create or update a skill document.
   - Example: You didn't know Django requires migration files after model changes → create `skills/django-models/SKILL.md` with that procedure.

3. **"I've seen this exact problem before but didn't leverage that experience."**
   → Evolve **Memory**. This usually happens automatically, but check that episodic records are being written and semantic patterns are being extracted.
   - Example: You solved a similar Flask blueprint issue last cycle but repeated the same wrong approach → ensure the episodic memory was recorded and a semantic pattern was distilled.

4. **"I literally couldn't perform a needed action."** (rare, requires explicit opt-in)
   → Evolve **Tools**. Add a new tool or modify an existing one.
   - Example: You needed to query a database but had no SQL tool → add `sql_exec.py` and register it in `registry.yaml`.

### Evolution Guidelines

**For Prompts:**
- Keep rules concise and actionable. Each rule should be a clear instruction, not a vague aspiration.
- Prefer specific over general. *"Read the full traceback before attempting a fix"* is better than *"Be thorough."*
- Use fragments (`prompts/fragments/`) for domain-specific conventions that don't belong in the core `system.md`.
- Remove rules that are no longer helping or that conflict with newer, better rules.

**For Skills:**
- Each skill should address a coherent problem category, not a single task instance.
- Structure skills with: *When to Apply* (trigger conditions), *Procedure* (step-by-step), and *Pitfalls* (common mistakes).
- Use the draft → review cycle (`_drafts/` → ACCEPT / MERGE / SKIP) to avoid polluting the skill library with noise.
- Merge overlapping skills rather than accumulating near-duplicates.

**For Memory:**
- Episodic memory is written automatically after each task cycle. Ensure records include: task ID, cycle number, score, files edited, and a brief approach summary.
- Semantic memory should be distilled periodically: look across episodic records for recurring patterns and extract them with a confidence score.
- Prune low-confidence semantic memories that haven't been validated by subsequent tasks.

**For Tools:**
- Only evolve tools when the current toolset genuinely cannot express a needed action.
- Every tool file must include a `reset()` function for per-task state cleanup.
- Test tool changes carefully — a broken tool is worse than a missing one.
- Prefer composing existing tools (e.g., a bash command) over adding new ones when the capability gap is small.
