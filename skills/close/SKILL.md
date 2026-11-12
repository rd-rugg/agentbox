---
name: close
description: Save the agentbox's running sessions, snapshot it and delete it so it stops costing money.
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/box close) Bash(${CLAUDE_PLUGIN_ROOT}/box close --idle)
---

The user asked to close their box (with several boxes: all of them, unless
they named one, e.g. `box close agentbox-2`, or asked for idle ones: `box close --idle`). Run `${CLAUDE_PLUGIN_ROOT}/box close` with a
Bash timeout of 600000. If it stops because work exists only on the box
(uncommitted or unpushed), or because agents outside the tabs (T3 Code's) are working,
show what it listed and ask: push it or let them finish first, or close anyway with `box
close BOX --force` for that box (the work stays in the snapshot; working agents are
stopped mid-turn; `--force` without BOX forces every box). A refused close leaves the box up and
billing: the command exits non-zero; run `box ls` to see which boxes are still up
before reporting anything as closed. Report which sessions were saved (they reopen with
`/agentbox:open`), the snapshot ID, and that the server is no longer billed.
Anything the agents were doing on the box was stopped; its conversations are saved.
