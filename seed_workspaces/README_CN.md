# A-Evolve 可进化层（Evolvable Layers）技术规范

|  |  |
|---|---|
| 版本 | 1.0 |
| 适用范围 | A-Evolve Agent Workspace 文件系统契约 |
| 对应代码 | `agent_evolve/contract/workspace.py` |

---

## 1 概述

A-Evolve 的核心设计原则是：**所有可进化的 agent 状态均以文件形式存储在标准目录结构中**。进化引擎通过读写这些文件完成对 agent 的变异，agent 通过重新加载目录完成状态更新。这一机制被称为**文件系统契约（File System Contract）**。

本文档定义该契约中的四个可进化层：**Prompts、Skills、Memory、Tools**。每个层对应 workspace 根目录下的一个子目录，具有独立的文件格式、API 接口和进化策略。

---

## 2 目录结构

```
workspace/
├── manifest.yaml              # Workspace 身份与配置声明
├── prompts/                   # 层 1: Prompts
│   ├── system.md              #   主系统提示词（必需）
│   └── fragments/             #   可选提示词片段
│       └── *.md
├── skills/                    # 层 2: Skills
│   ├── {skill-name}/
│   │   └── SKILL.md           #   每个 skill 一个目录
│   └── _drafts/               #   skill 提案暂存区
│       └── *.md
├── memory/                    # 层 3: Memory
│   ├── episodic.jsonl         #   任务级执行记录
│   └── semantic.jsonl         #   跨任务模式与洞察
└── tools/                     # 层 4: Tools
    ├── registry.yaml          #   工具注册清单
    └── *.py                   #   工具实现代码
```

---

## 3 进化开关配置

各层的进化行为可在 `EvolveConfig` 中独立控制：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `evolve_prompts` | `true` | 允许进化引擎修改系统提示词 |
| `evolve_skills` | `true` | 允许进化引擎创建、合并、删除 skill |
| `evolve_memory` | `true` | 允许进化引擎写入和清理 memory 条目 |
| `evolve_tools` | `false` | 允许进化引擎修改工具代码（默认关闭） |

`manifest.yaml` 中通过 `evolvable_layers` 字段声明 workspace 支持的可进化层：

```yaml
evolvable_layers:
  - prompts
  - skills
  - memory
```

> **注意：** `evolve_tools` 默认关闭，因为工具层包含可执行代码，变异风险显著高于其他三个文本层。

---

## 4 层 1: Prompts（提示词层）

### 4.1 定义

Prompts 层定义 agent 的问题解决策略与行为规则，是 agent 与 LLM 交互时的系统提示词来源。

### 4.2 文件规范

| 路径 | 是否必需 | 格式 | 说明 |
|------|----------|------|------|
| `prompts/system.md` | 是 | Markdown | 主系统提示词 |
| `prompts/fragments/*.md` | 否 | Markdown | 模块化提示词片段，由 agent 在运行时内联注入 |

### 4.3 API 接口

| 方法 | 说明 |
|------|------|
| `workspace.read_prompt() -> str` | 读取 `system.md` 全文 |
| `workspace.write_prompt(content: str)` | 覆写 `system.md` |
| `workspace.read_fragment(name: str) -> str` | 读取指定 fragment |
| `workspace.write_fragment(name: str, content: str)` | 写入指定 fragment |
| `workspace.list_fragments() -> list[str]` | 列出所有 fragment 文件名 |

### 4.4 Agent 加载流程

`BaseAgent.reload_from_fs()` 在初始化阶段调用 `workspace.read_prompt()` 读取 `system.md`。Agent 实现可在此基础上组合最终提示词，注入 fragments、skill 目录、memory 上下文等内容后传递给 LLM。

### 4.5 进化策略

进化引擎分析失败 trajectory 中反复出现的行为反模式，向 `system.md` 追加针对性的策略规则。

生成规则示例：

> - "当构建失败时，先完整阅读错误信息再尝试修复。"
> - "提交前，验证解决方案满足所有明确列出的需求。"

### 4.6 文件示例

```markdown
You are an expert software engineer tasked with resolving GitHub issues
by producing code patches.

## Approach
1. **Understand the issue**: Read the issue description carefully.
2. **Locate relevant code**: Use search tools to find affected files.
3. **Plan the fix**: Think step-by-step about what needs to change.
4. **Implement the fix**: Make minimal, precise edits.
5. **Verify**: Run existing tests to confirm correctness.
```

---

## 5 层 2: Skills（技能层）

### 5.1 定义

Skills 层维护一个可复用的领域知识库。每个 skill 封装一组解决特定类别问题的过程、模式或代码片段。Agent 在执行阶段按需加载 skill 内容。

### 5.2 文件规范

每个 skill 位于独立子目录 `skills/{skill-name}/SKILL.md`，文件须以 YAML frontmatter 开头：

```markdown
---
name: <kebab-case 标识符>
description: <一行摘要，说明该 skill 的适用场景>
---

# Skill 标题

<正文：领域知识、操作步骤、代码片段、验证方法>
```

| 字段 | 是否必需 | 约束 |
|------|----------|------|
| `name` | 是 | Kebab-case 格式，须与目录名一致 |
| `description` | 是 | Agent 在系统提示词中仅看到此字段，据此决定是否加载完整正文 |

附加约束：

- 以 `_` 为前缀的目录（如 `_drafts/`）不纳入 skill 列表。
- 正文建议不超过 2000 字符。

### 5.3 API 接口

| 方法 | 说明 |
|------|------|
| `workspace.list_skills() -> list[SkillMeta]` | 返回所有 skill 的 name、description、path |
| `workspace.read_skill(name: str) -> str` | 读取指定 skill 的完整 SKILL.md |
| `workspace.write_skill(name: str, content: str)` | 写入或覆写 skill（含 frontmatter） |
| `workspace.delete_skill(name: str)` | 删除指定 skill 目录 |

**草稿相关接口（Draft Skills）：**

| 方法 | 说明 |
|------|------|
| `workspace.list_drafts() -> list[dict[str, str]]` | 列出暂存区中的提案 |
| `workspace.write_draft(name: str, content: str)` | 写入草稿提案 |
| `workspace.clear_drafts()` | 清空暂存区 |

### 5.4 Agent 加载流程

初始化时调用 `workspace.list_skills()` 获取元数据。Skill 名称和描述被列入系统提示词，作为技能目录供 agent 参考。完整正文**不预加载**——框架注册一个 `read_skill(skill_name)` 工具，agent 在任务执行中根据 description 判断相关性，按需调用该工具加载正文。

### 5.5 进化策略

进化引擎通过三阶段审核循环管理 skill：

| 阶段 | 说明 |
|------|------|
| **提案（Proposal）** | Solver 在完成任务后提议新 skill，写入 `_drafts/` |
| **审核（Curation）** | 进化 LLM 对照已有 skill 审查提案，决定 `ACCEPT`（采纳）、`MERGE`（合并至已有 skill）或 `SKIP`（丢弃） |
| **直接变异** | 引擎可跳过提案流程，直接根据失败模式分析创建或优化 skill |

### 5.6 文件示例

```markdown
---
name: entity-verification
description: Strategies for verifying entities against a data source before responding.
---

# Entity Verification

## When to Apply
Use when the task requires cross-referencing claims against a structured
dataset (database, CSV, API response).

## Procedure
1. Identify all entities mentioned in the query.
2. For each entity, execute a targeted lookup.
3. Compare returned values against the claim.
4. If any mismatch is found, flag it explicitly in the response.
```

---

## 6 层 3: Memory（记忆层）

### 6.1 定义

Memory 层存储任务执行过程中的结构化学习成果，为 agent 提供历史上下文，使其能够避免重复错误并利用过往经验。

### 6.2 文件规范

Memory 文件采用 JSONL（JSON Lines）格式，每行一个 JSON 对象，按类别命名：

| 路径 | 类别 | 说明 |
|------|------|------|
| `memory/episodic.jsonl` | `episodic` | 逐任务的执行记录：task ID、得分、编辑文件、方法摘要 |
| `memory/semantic.jsonl` | `semantic` | 跨任务的高层模式、通用策略、领域洞察 |

可通过自定义类别名创建其他 JSONL 文件。

### 6.3 条目结构

条目为灵活的字典结构，无强制 schema。按惯例，episodic 条目应包含 `task_id` 字段。

典型 episodic 条目：

```json
{
  "task_id": "django__django-13790",
  "cycle": 2,
  "score": 0.0,
  "files_edited": ["django/db/models/fields/related.py"],
  "approach_summary": "Edited field descriptor to handle None values. Target tests still failing.",
  "fail_to_pass_failing": ["test_admin__test_dynamic_list_display"],
  "pass_to_pass_broken": []
}
```

### 6.4 API 接口

| 方法 | 说明 |
|------|------|
| `workspace.add_memory(entry: dict, category: str = "episodic")` | 追加一条记录到对应 JSONL 文件 |
| `workspace.read_memories(category: str, limit: int = 100) -> list[dict]` | 读取指定类别的最近 `limit` 条记录 |
| `workspace.read_all_memories(limit: int = 100) -> list[dict]` | 跨所有 JSONL 文件读取，每条记录自动添加 `_category` 标签 |

### 6.5 Agent 加载流程

`BaseAgent.reload_from_fs()` 在初始化时调用 `workspace.read_all_memories()`。Agent 实现按需过滤（通常基于 `task_id`），将相关历史条目注入当前任务的 user prompt 中。

### 6.6 进化策略

进化引擎在每个 batch 完成后写入 memory：

| 类别 | 写入内容 |
|------|----------|
| **Episodic** | 任务结果、patch 内容、失败测试列表、方法摘要 |
| **Semantic** | 跨任务模式综合（如"Django admin 相关任务通常需要检查 `ModelAdmin.get_list_display`"） |

引擎也可清理冗余或过期的条目。

---

## 7 层 4: Tools（工具层）

### 7.1 定义

Tools 层提供 agent 在任务执行期间可调用的工具实现。每个工具是一个 Python 模块，包含以 `@tool` 装饰的函数。

### 7.2 文件规范

**注册清单** `tools/registry.yaml`：

```yaml
tools:
  - name: bash
    description: Execute a bash command inside the Docker container.
    file: bash.py
  - name: text_editor
    description: Edit files with structured commands (view, create, str_replace, insert, undo_edit).
    file: text_editor.py
  - name: python_exec
    description: Execute Python code directly. Each call is independent.
    file: python_exec.py
  - name: submit
    description: Submit your solution and end the task.
    file: submit.py
```

**工具实现** `tools/*.py`：

```python
from strands import tool

@tool
def bash(command: str, workdir: str = "/testbed") -> str:
    """Execute a bash command inside the Docker container."""
    ...
```

每个工具模块可定义 `reset(**kwargs)` 函数，在每个任务开始前被调用以重置内部状态。

### 7.3 API 接口

| 方法 | 说明 |
|------|------|
| `workspace.read_tool_registry() -> list[dict]` | 读取 `registry.yaml` 中的工具列表 |
| `workspace.write_tool_registry(tools: list[dict])` | 覆写 `registry.yaml` |
| `workspace.read_tool(name: str) -> str` | 读取指定工具的 `.py` 源码 |
| `workspace.write_tool(name: str, content: str)` | 写入或覆写工具实现 |

### 7.4 Agent 加载流程

Agent 读取 `registry.yaml`，按 `file` 字段动态导入对应 `.py` 文件，将其中的 `@tool` 函数注册到 strands agent 框架。

### 7.5 进化策略

工具进化**默认关闭**。工具是可执行代码，变异风险显著高于文本层（Prompts、Skills、Memory）。启用后，进化引擎可以：

- 修改已有工具的实现逻辑。
- 添加新工具模块并更新 `registry.yaml`。
- 调整工具的 `description` 字段以优化 LLM 的工具选择行为。

---

## 8 生命周期总览

下表描述一个完整进化周期中，各层在不同阶段的状态变化：

| 阶段 | Prompts | Skills | Memory | Tools |
|------|---------|--------|--------|-------|
| **Seed** | 提供初始 `system.md` | 空 | 空 | 提供初始工具集 |
| **Load** | 读入系统提示词 | 列出元数据；正文通过 `read_skill()` 按需加载 | 将相关历史条目注入 user prompt | 从 `registry.yaml` 动态导入 |
| **Solve** | 引导 LLM 的推理与行为 | Agent 在 description 匹配时调用 `read_skill()` | 历史尝试为当前方法提供参考 | Agent 在执行中调用工具完成操作 |
| **Evolve** | 追加策略规则 | 创建 / 合并 / 优化 skill | 写入 episodic 记录；综合 semantic 模式 | 修改工具实现（如启用） |
| **Reload** | `reload_from_fs()` 重新加载全部层的最新状态 |

---

## 9 内置 Seed Workspace 一览

| 目录 | 对应领域 | 预置工具 | 说明 |
|------|----------|----------|------|
| `swe/` | SWE-bench | bash, text_editor, python_exec, submit | 通过代码 patch 解决 GitHub issue |
| `mcp/` | MCP-Atlas | MCP server tools | 通过 Model Context Protocol 进行工具调用 |
| `terminal/` | Terminal-Bench | bash, text_editor, submit | Docker 环境中的终端/CLI 操作 |
| `skillbench/` | SkillsBench | bash, text_editor, python_exec, submit | 智能体技能发现与评估 |
