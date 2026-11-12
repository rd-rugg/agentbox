---
name: attach
description: Open one agentbox project in a terminal tab, resuming its last Claude conversation.
disable-model-invocation: true
argument-hint: "NAME [--agent codex] [--resume ID --as LABEL] [--yes]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/box attach *) Bash(${CLAUDE_PLUGIN_ROOT}/box ls)
---

If no name was given, run `${CLAUDE_PLUGIN_ROOT}/box ls` and ask which project.
Otherwise run `${CLAUDE_PLUGIN_ROOT}/box attach $ARGUMENTS`: it starts the agent (Claude, or Codex with `--agent codex`) in
`~/work/NAME` if it isn't running (with `claude --continue`) and opens a tab.
`--resume ID --as LABEL` opens another session, `NAME+LABEL`, on an older
conversation (ids from `box session ls NAME`).

If the project's box is down (or, with several boxes, it needs one), the command
stops and says what opening or creating that box would bill: tell the user and rerun
with `--yes` only after they agree.

With `--agent codex` the session runs Codex instead of Claude (session name
`NAME@codex`, same folder); Codex must be enabled (`BOX_AGENTS="claude codex"`)
and logged in (`box login codex`, which the user runs in their own terminal).
