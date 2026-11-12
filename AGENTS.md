# AGENTS.md

agentbox runs Claude Code and Codex on a Hetzner Cloud server that is snapshotted and
deleted when idle and recreated on demand, with every session, login and file intact.
`box` is the laptop command; `server/` is what gets installed on the box; the repo is
also a Claude Code plugin (`skills/`, `.claude-plugin/`). `README.md` says what it does
for users. Each file's header comment or docstring says how it works: read it before
changing the file.

## Map

- `box`: the laptop command. Decides, passes the money and interruption gates, then runs
  the steps below. `box help` is the command reference.
- `lib.sh`: every setting (`BOX_*`, with defaults and meanings) and the shared helpers:
  server creation, Tailscale-first SSH (`bssh`), `sync_config` (pushes `server/` and runs
  `server/install.sh` on the box).
- `setup.sh`, `up.sh`, `down.sh`, `status.sh`, `tabs.sh`: create, start, close, report,
  open tabs.
- `pool.py`: several boxes. Placement decisions (`plan_place`, `plan_move`) are pure and
  unit-tested; state is labels on Hetzner servers and snapshots.
- `projects.py`, `sessions.py`, `readiness.py`: projects and `agentbox.json`; moving
  conversations between laptop and box; what a conversation needs that the box lacks.
- `agent_setup.py`, `mcp_sync.py`: the laptop's personal agent setup and MCP servers,
  mirrored to the box.
- `onboard.py`: `box doctor` (`CHECKS`) and the `box setup` wizard.
- `clipboard.py`, `winssh.py`, `server_types.py`, `env_to_bitwarden.py`: clipboard images,
  Windows' SSH for T3 Code, server types and prices, `.env` import.
- `server/bin/`: the box-side `box` and its helpers (`agentbox-*`, `secrets` in
  `server/`). `server/install.sh` is idempotent and runs on every sync.
- `skills/`: the plugin's skills (`agentbox` loads by itself; details in its
  `reference.md`). `server/skills/agentbox-box`: the skill for agents on the box.
- `scripts/publish.sh`: releases to the public repository.
- `tests/`: offline tests, no box needed. `video/`: the README video (development only: never published).

## Checks before committing

```bash
uvx --from shellcheck-py shellcheck -x -S warning box lib.sh setup.sh up.sh down.sh status.sh tabs.sh install-local.sh \
  server/install.sh server/shell.sh server/bin/box server/bin/agentbox-browser server/bin/git-credential-box server/bin/with-cli-token server/bin/agentbox-claude server/git-hooks/passthrough \
  scripts/publish.sh
python3 -m unittest discover -s tests
node --check server/novnc/agentbox-clipboard.js && node --check server/novnc/agentbox-clipboard-keys.js
python3 -m py_compile clipboard.py winssh.py server/bin/agentbox-clipboard server/bin/agentbox-tty server/bin/agentbox-agents server/bin/agentbox-t3 onboard.py projects.py sessions.py readiness.py pool.py server_types.py env_to_bitwarden.py agent_setup.py mcp_sync.py server/secrets server/bin/agentbox-mcp server/bin/agentbox-browser-relay server/bin/agentbox-name server/bin/agentbox-browser-router server/bin/agentbox-git server/bin/agentbox-mise-defaults server/bin/github-token server/git-hooks/pre-commit scripts/publish_gates.py
rm -rf __pycache__ tests/__pycache__ server/__pycache__ server/git-hooks/__pycache__ server/bin/__pycache__ scripts/__pycache__
claude plugin validate .
```

`scripts/publish.sh` runs this block as a release gate: keep the heading and the fence.
A new script goes into it. The offline tests don't reach a box: verify box-side changes on
a real one. `./up.sh` deploys to a running box (`BOX_NAME=agentbox-2 ./up.sh` for another);
a full `box close` / `box open` is needed only for save, restore and snapshot changes.

## Rules

- **Money.** Creating or starting a server bills the user. `bill_gate` in `box` is the only
  path that opens or creates a box: it says what would be billed, then `--yes`, a
  terminal's answer, or the user naming the box goes ahead. Never move a snapshot to a
  bigger disk without consent: it can't shrink back (`BOX_ALLOW_GROW`).
- **Running work.** `stop_gate` comes before ending sessions, `check_agents` before
  closing under agents outside the tabs. Commands decide first (pure, tested), pass the
  gate, then act, as plain commands, never inside `$(...)` where `set -e` doesn't apply.
- **Secrets.** Never print a secret's value (names, lengths or hashes only); never write
  one to the box's disk (RAM only: `/run/agentbox`); never ask the user to paste a token
  into chat.
- **Deleting.** Delete only exact paths you created, after looking at them; never
  `rm -rf` a parent folder (it once took a live session's transcript with it). The box's
  agent setup is deleted only for files the laptop pushed and then dropped
  (`agent_setup.py`'s manifest).
- **Fail closed.** What `agentbox-git` can't read counts as unpushed work.
- **Other servers** in the Hetzner project aren't agentbox's: touch only the boxes.
- **Session flags** (permissions, Remote Control) are added in `launch_claude`
  (`server/bin/box`), never at one start path, or a resume loses them.
- **Naming.** Generic things are "agentbox" and "agent"; Claude- or Codex-specific names
  only where they are specific (`CLI__CLAUDE_TOKEN_*`, `~/.claude`).
- **Nothing personal.** Everything here is published: no one's own projects, hosts,
  tokens or conventions, also not in examples or tests (use `acme`, `myapp`, `me`). A
  personal convention for agents on the box goes in the owner's overlay
  (`~/.config/agentbox/box/`). `tests/test_doc_names.py` fails when the docs name a
  credential no code reads; the private deny list catches the rest at publish.

## Docs and releases

- A change users see updates `README.md` (or `docs/secrets.md`), `box help`, and the
  skills when an agent should act differently, in the same commit. A new prerequisite
  goes into `CHECKS` in `onboard.py` with the right `who`. A new setting gets its comment
  in `lib.sh`.
- Development happens in this private repository (`origin`); the public one (`public`)
  receives releases only. Never push to `public` by hand: `scripts/publish.sh` (a dry run
  by default; `--push -m "SUMMARY"`) publishes HEAD less `.publishignore` as one commit,
  after its gates (deny list, gitleaks, identity, the checks above).
- A release bumps `version` in `.claude-plugin/plugin.json`. Users get it after the
  publish, with `claude plugin marketplace update agentbox && claude plugin update
  agentbox@agentbox`.
- `install-local.sh` makes the Codex copies of the skills and strips Claude Code asides
  written exactly as `(Claude Code: Bash timeout 600000)`: keep that wording.

## Gotchas

- Scripts use `set -euo pipefail`: a failing `$(cmd | cmd)` assignment ends the script
  silently. Add `|| true` where a miss is normal.
- Claude Code names a project folder by replacing every non-alphanumeric character of
  its path with `-` (`my_app` → `my-app`).
- The global git hooks (`core.hooksPath`) hand over to the repo's own `.git/hooks` via
  `git rev-parse --git-common-dir`; `--git-path hooks` points back at the global folder
  and loops.
- Under `sudo`, mise shims read root's config: call tools by the path from `mise which`.
- `~/.config/mise/config.toml` and `~/.codex/config.toml` on the box are rewritten on
  every sync. Box-wide variables go into `~/.config/agentbox/env.sh` (built by
  `install.sh`), never as one-off lines in `~/.bashrc`.
- Bitwarden rate-limits hard: `secrets` caches in RAM for 5 minutes; anything calling it in
  a loop must use the cache.
- `ssh host 'cmd &'` stays open until the job's output closes: use `setsid cmd
  </dev/null >/dev/null 2>&1 &`.
- Don't `pkill -f PATTERN` from a command whose own text contains PATTERN: it kills its own
  shell. Use `pgrep -f "patter[n]"` or PIDs.
- tmux ignores a `TMUX_TMPDIR` that doesn't exist, and with `TMUX` set it talks to that
  server: a test's `tmux kill-server` once killed every session on a box. Tests give every
  tmux call its own `-S` socket, with `TMUX` unset.
- OpenSSH finds `~/.ssh` from the user database, not `$HOME`: a test with a throwaway HOME
  still reaches the real `known_hosts`. Tests that run laptop scripts stub `ssh`,
  `ssh-keygen` and `tailscale`.
