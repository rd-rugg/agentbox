---
name: open
description: Start the agentbox if it's down, reopen its agent sessions, and open a terminal tab per session.
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/box open) Bash(${CLAUDE_PLUGIN_ROOT}/box open --yes) Bash(${CLAUDE_PLUGIN_ROOT}/box plan)
---

The user asked to open their box.

1. If the box is down, run `${CLAUDE_PLUGIN_ROOT}/box plan` and tell the user
   the type and price it will use. If the plan says the disk will grow, stop and
   ask: that can't be undone later. Only if they agree, rerun step 2 with
   `BOX_ALLOW_GROW=1` in front.
2. Run `${CLAUDE_PLUGIN_ROOT}/box open` with a Bash timeout of 600000. With one
   box that's down, it stops and says what it would bill (you have no terminal):
   the user asking to open their box is their OK, so rerun with `--yes`.
3. Report which session tabs were opened, the sessions it left in the background
   (their tabs had been closed) and the closed boxes it listed, and any idle or
   cost warning it printed. With several boxes a closed one opens only when named:
   ask before running `box open BOX` (it bills). Sessions already in a tab keep it.
