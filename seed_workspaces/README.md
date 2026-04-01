# Seed Workspaces — Evolvable Layers Reference

This document defines the four evolvable layers in an A-Evolve agent workspace. Each layer corresponds to a directory under the workspace root and is governed by the file system contract in `agent_evolve/contract/workspace.py`.

## Workspace Structure

```
workspace/
├── manifest.yaml              # Workspace identity and configuration
├── prompts/                   # Layer 1: Prompts
│   ├── system.md              #   Primary system prompt (required)
│   └── fragments/             #   Optional prompt fragments
│       └── *.md
├── skills/                    # Layer 2: Skills
│   ├── {skill-name}/
│   │   └── SKILL.md           #   One SKILL.md per skill directory
│   └── _drafts/               #   Staging area for proposed skills
│       └── *.md
├── memory/                    # Layer 3: Memory
│   ├── episodic.jsonl         #   Task-level execution records
│   └── semantic.jsonl         #   High-level patterns and insights
└── tools/                     # Layer 4: Tools
    ├── registry.yaml          #   Tool manifest
    └── *.py                   #   Tool implementations
```

## Evolution Gating

Each layer can be independently enabled or disabled in `EvolveConfig`:

```yaml
evolve_prompts: true    # default: true
evolve_skills: true     # default: true
evolve_memory: true     # default: true
evolve_tools: false     # default: false
```

`manifest.yaml` declares which layers the workspace supports via `evolvable_layers`:

```yaml
evolvable_layers:
  - prompts
  - skills
  - memory
```

---

## Layer 1: Prompts

**Purpose:** Define the agent's problem-solving strategy and behavioral rules.

**Directory:** `prompts/`

### File Format

| File | Required | Description |
|------|----------|-------------|
| `prompts/system.md` | Yes | Primary system prompt. Plain Markdown. |
| `prompts/fragments/*.md` | No | Modular prompt segments, injected inline by the agent. |

### Workspace API

```python
workspace.read_prompt() -> str
workspace.write_prompt(content: str) -> None
workspace.read_fragment(name: str) -> str
workspace.write_fragment(name: str, content: str) -> None
workspace.list_fragments() -> list[str]
```

### Agent Loading

`BaseAgent.reload_from_fs()` reads `system.md` at initialization. The agent may further compose the final prompt by injecting fragments, skill listings, and memory context before passing it to the LLM.

### Evolution Behavior

The evolution engine analyzes failed trajectories to identify recurring behavioral anti-patterns, then appends concise strategy rules to `system.md`. Examples:

- "When a build fails, read the full error message before attempting a fix."
- "Before submitting, verify the solution meets ALL stated requirements."

### Example

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

## Layer 2: Skills

**Purpose:** Maintain a library of reusable, domain-specific knowledge artifacts. Each skill encapsulates a procedure or pattern set for solving a class of problems.

**Directory:** `skills/`

### File Format

Each skill resides in its own subdirectory as `skills/{skill-name}/SKILL.md` with mandatory YAML frontmatter:

```markdown
---
name: <kebab-case identifier>
description: <one-line summary; indicates when the skill applies>
---

# Skill Title

<body: domain knowledge, procedures, code snippets, verification steps>
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Kebab-case identifier matching the directory name. |
| `description` | Yes | Concise trigger condition. The agent sees only this field in its system prompt and decides whether to load the full body. |

**Constraints:**
- Directories prefixed with `_` (e.g., `_drafts/`) are excluded from skill listing.
- Recommended max body length: 2000 characters.

### Workspace API

```python
workspace.list_skills() -> list[SkillMeta]     # Returns name, description, path
workspace.read_skill(name: str) -> str          # Full SKILL.md content
workspace.write_skill(name: str, content: str) -> None
workspace.delete_skill(name: str) -> None
```

### Draft Skills

Proposed skills are staged in `skills/_drafts/` before curation:

```python
workspace.list_drafts() -> list[dict[str, str]]
workspace.write_draft(name: str, content: str) -> None
workspace.clear_drafts() -> None
```

### Agent Loading

At initialization, `workspace.list_skills()` returns metadata (name + description). These are listed in the system prompt as a skill catalog. The full body is **not** preloaded — a `read_skill(skill_name)` tool is registered, allowing the agent to load content on demand during execution.

### Evolution Behavior

The evolution engine manages skills through a curation loop:

1. **Proposal** — The solver proposes new skills after task completion.
2. **Curation** — The evolution LLM reviews proposals against existing skills:
   - `ACCEPT` — Add the skill as-is.
   - `MERGE` — Combine the proposal into an existing skill.
   - `SKIP` — Discard (too task-specific, already covered, etc.).
3. **Direct mutation** — The engine may also create or refine skills directly based on failure pattern analysis.

### Example

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

## Layer 3: Memory

**Purpose:** Store structured learning from task execution. Provide historical context about previous attempts, enabling the agent to avoid repeated mistakes and leverage past experience.

**Directory:** `memory/`

### File Format

Memory files use JSONL (JSON Lines) format — one JSON object per line, named by category:

| File | Category | Description |
|------|----------|-------------|
| `memory/episodic.jsonl` | `episodic` | Per-task execution records: task ID, score, files edited, approach summary. |
| `memory/semantic.jsonl` | `semantic` | High-level patterns, cross-task insights, generalized strategies. |

Additional categories can be created by specifying a custom category name.

### Entry Schema

Entries are flexible dictionaries with no enforced schema. A typical episodic entry:

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

By convention, `task_id` is present in episodic entries.

### Workspace API

```python
workspace.add_memory(entry: dict, category: str = "episodic") -> None
workspace.read_memories(category: str, limit: int = 100) -> list[dict]
workspace.read_all_memories(limit: int = 100) -> list[dict]
```

`read_memories` returns the last `limit` entries. `read_all_memories` reads across all `.jsonl` files and tags each entry with `_category`.

### Agent Loading

`BaseAgent.reload_from_fs()` calls `workspace.read_all_memories()` at initialization. The agent injects relevant entries (typically filtered by `task_id`) into the user prompt for the current task.

### Evolution Behavior

The evolution engine writes memory after each batch:

- **Episodic** — Records task outcomes, patch contents, failing tests, approach summaries.
- **Semantic** — Synthesizes cross-task patterns (e.g., "Django admin tasks consistently require checking `ModelAdmin.get_list_display`").

The engine may also prune redundant or outdated entries.

---

## Layer 4: Tools

**Purpose:** Provide executable tool implementations that the agent invokes during task execution. Each tool is a Python module containing a `@tool`-decorated function.

**Directory:** `tools/`

### File Format

**`tools/registry.yaml`** declares available tools:

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

**`tools/*.py`** exports a `@tool`-decorated function:

```python
from strands import tool

@tool
def bash(command: str, workdir: str = "/testbed") -> str:
    """Execute a bash command inside the Docker container."""
    ...
```

Each tool module may define a `reset(**kwargs)` function, called before each task to reinitialize state.

### Workspace API

```python
workspace.read_tool_registry() -> list[dict[str, Any]]
workspace.write_tool_registry(tools: list[dict[str, Any]]) -> None
workspace.read_tool(name: str) -> str       # Returns .py source
workspace.write_tool(name: str, content: str) -> None
```

### Agent Loading

The agent reads `registry.yaml`, dynamically imports each `.py` file, and registers the `@tool` functions with the strands agent framework.

### Evolution Behavior

Tool evolution is **disabled by default** (`evolve_tools: false`). Tools are executable code; mutations carry higher risk than text-based layers. When enabled, the evolution engine may:

- Modify existing tool implementations.
- Add new tool modules and update `registry.yaml`.
- Adjust tool descriptions to improve LLM tool selection.

---

## Lifecycle Summary

| Phase | Prompts | Skills | Memory | Tools |
|-------|---------|--------|--------|-------|
| **Seed** | `system.md` provided | Empty | Empty | Provided |
| **Load** | Read into system prompt | List metadata; body loaded on demand via `read_skill()` | Inject relevant entries into user prompt | Dynamic import from `registry.yaml` |
| **Solve** | Guides LLM behavior | Agent calls `read_skill()` when description matches task | Previous attempts inform current approach | Agent invokes tools during execution |
| **Evolve** | Append strategy rules from failure analysis | Create / merge / refine skills | Write episodic records; synthesize semantic patterns | Modify implementations (if enabled) |
| **Reload** | `reload_from_fs()` picks up all mutations across layers |||||

## Included Seed Workspaces

| Directory | Domain | Tools | Description |
|-----------|--------|-------|-------------|
| `swe/` | SWE-bench | bash, text_editor, python_exec, submit | GitHub issue resolution via code patches |
| `mcp/` | MCP-Atlas | MCP server tools | Tool-calling via Model Context Protocol |
| `terminal/` | Terminal-Bench | bash, text_editor, submit | Terminal/CLI operations in Docker |
| `skillbench/` | SkillsBench | bash, text_editor, python_exec, submit | Agentic skill discovery |
