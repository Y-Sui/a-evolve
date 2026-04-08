---
name: Default parallel to 25
description: All experiments should use parallel=25 and batch_size=25 for faster execution
type: feedback
---

All experiment configs should use parallel=25 and batch_size=25.

**Why:** OpenRouter throughput can handle it, and parallel=10 makes 50-task runs take 2-3 hours unnecessarily.

**How to apply:** When creating or modifying experiment configs, always set parallel and batch_size to 25.
