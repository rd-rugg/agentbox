---
name: ls
description: Show whether the agentbox is up, what it costs, and its projects with running/done status.
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/box ls)
---

Run `${CLAUDE_PLUGIN_ROOT}/box ls` and summarize: up or down, server type and
price, budget, snapshot cost, the load line (free memory, swap, CPU; pass on an
overload warning and its suggestions), and each project (running or done, last activity).
Pass on any idle warning: an idle box still costs money until `/agentbox:close`.
