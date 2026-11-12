---
name: new
description: Start a new conversation on the agentbox in a new terminal tab - in an existing project (its own session NAME+LABEL if one is running) or a new one (OWNER/REPO is cloned from GitHub first).
disable-model-invocation: true
argument-hint: "NAME [--as LABEL] | OWNER/REPO [NAME] [--agent codex] [--box BOX] [--yes]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/box new *)
---

Run `${CLAUDE_PLUGIN_ROOT}/box new $ARGUMENTS` with a Bash timeout of 600000.
For OWNER/REPO it clones into `~/work/REPO` on the box and installs the Node and
Python versions the repo pins. It then starts the agent (Claude by default) there and opens a tab.
If the clone fails, the GitHub token (`CLI__GH_TOKEN`) may not cover that repo.

With several boxes, a new repo goes on a running box only if that box has room for
every project's memory estimate (with one box it goes there: check `box ls`'s load
line first). If its box is down, or no running box has room, the command stops
and says what opening or creating a box would bill: tell the user and rerun with
`--yes` only after they agree, or with `--box BOX` if they pick a box themselves.

With `--agent codex` the session runs Codex instead of Claude (session name
`NAME@codex`, same folder); Codex must be enabled (`BOX_AGENTS="claude codex"`)
and logged in (`box login codex`, which the user runs in their own terminal).
A new Codex conversation starts fresh again after a box close/open (Claude's
keeps its conversation).
