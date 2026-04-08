---
name: Do not kill other tmux sessions
description: When starting new experiments in tmux, never kill existing sessions - each experiment runs independently
type: feedback
---

All experiments should be started in tmux. When starting new experiments, NEVER kill existing tmux sessions.

**Why:** User runs multiple experiments concurrently. Killing sessions to "clean up" before starting new ones destroys in-progress work. This happened when switching from Qwen to Claude experiments — the Qwen sessions were accidentally killed.

**How to apply:** Only use `tmux new-session -d -s <name>` to start new experiments. Never use `tmux kill-session` or `tmux kill-server` unless the user explicitly asks to stop a specific experiment. Clean up Docker containers only for the specific experiment being restarted, not all `swe-agent` containers.
