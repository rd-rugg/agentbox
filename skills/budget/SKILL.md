---
name: budget
description: Set the most expensive Hetzner server type agentbox may use.
disable-model-invocation: true
argument-hint: "[server type, e.g. cpx32]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/box prices) Bash(${CLAUDE_PLUGIN_ROOT}/box budget *)
---

Requested type: $ARGUMENTS

1. Run `${CLAUDE_PLUGIN_ROOT}/box prices` and show the table (type,
   vCPU, RAM, disk, price per hour and per month, where it's in stock).
2. If no type was given above, ask the user which one is the most they're
   willing to pay for. Don't pick for them.
3. Run `${CLAUDE_PLUGIN_ROOT}/box budget TYPE` and confirm what was saved.

Mention that `box open` still takes the cheapest type in stock up to this limit
(a bigger box right now: `box resize TYPE`; always at least N GB: `BOX_MIN_RAM`
in box.conf), and that a bigger disk than the snapshot's can't be undone later.
