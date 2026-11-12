---
name: setup
description: Set up agentbox from scratch or finish a partial setup - check every prerequisite, do everything an agent can, and hand only the human-only steps to the user's guided `box setup`. Use on a new laptop, for a first setup, or when `box doctor` reports missing items.
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/box doctor) Bash(${CLAUDE_PLUGIN_ROOT}/box doctor --json) Bash(${CLAUDE_PLUGIN_ROOT}/install-local.sh) Bash(${CLAUDE_PLUGIN_ROOT}/box prices) Bash(${CLAUDE_PLUGIN_ROOT}/box plan) Bash(${CLAUDE_PLUGIN_ROOT}/box ls)
---

Goal: get the user from nothing to a working box, doing as much as possible
yourself. The split is fixed:

- **You (the agent)**: install laptop tools, create and upload an SSH key,
  install agentbox's laptop commands, write settings to
  `~/.config/agentbox/box.conf`, build and open the box.
- **The user only**: create accounts, create tokens in web consoles (Hetzner,
  Tailscale, Bitwarden, GitHub), import tokens into Bitwarden, sign in to
  Claude/Codex, and money decisions (budget, building the box). Tokens must
  never pass through this conversation: the user types them into `box setup`,
  which reads them hidden. Never ask for a token in chat, never read
  `~/.config/agentbox/bws.env` or print secret values.

## Steps

1. Run `${CLAUDE_PLUGIN_ROOT}/box doctor --json`. Each item has `who`
   (`you` = the user, `agent` = you), `required`, `ok` (true / false / null =
   not checkable yet), `detail` and `fix`.
2. Fix every failing `agent` item, in order, re-running doctor after each:
   - `tools`, `hcloud-cli`: install the missing tools with the platform's
     package manager (`brew install ...` on macOS; apt/dnf/pacman or Homebrew
     on Linux; WSL is Linux). Say what you'll install first; `sudo` needs the
     user's OK.
   - `local-commands`: run `${CLAUDE_PLUGIN_ROOT}/install-local.sh`.
   - `ssh-key`: ask whether they want a passphrase, then
     `mkdir -p -m 700 ~/.ssh && ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519`
     (with `-N ""` only if they said no passphrase; `-f` doesn't create `~/.ssh`).
   - `ssh-key-hetzner` (once `hcloud-token` is ok):
     `hcloud ssh-key create --name <hostname>-agentbox --public-key-from-file ~/.ssh/id_ed25519.pub`.
   - `terminal`: if none was found, suggest installing tmux (optional).
3. If any required `you` item fails, tell the user exactly which ones, in plain
   words, and what they need ready (accounts at Hetzner Cloud, Tailscale,
   Bitwarden Secrets Manager; optionally GitHub). Then ask them to run, **in
   their own terminal** (it is interactive and reads tokens hidden; it won't
   work through your shell tool):

       box setup

   It skips whatever is already done, walks through the rest with links,
   verifies each step, and puts the tokens into Bitwarden via a clipboard
   import. Wait for them, then re-run doctor.
4. Budget (`budget`), if still missing: show `${CLAUDE_PLUGIN_ROOT}/box prices`,
   ask which type is the most they'll pay for (never choose for them), then
   `${CLAUDE_PLUGIN_ROOT}/box budget TYPE`.
5. Options, if the user hasn't set them (look at `~/.config/agentbox/box.conf`):
   ask, then write the lines yourself: `BOX_AGENTS="claude codex"` (Codex too?),
   `BOX_PLAYWRIGHT=1` (browser tools, and `box browser` for sites the user signs in to; recommended), `BOX_DOCKER=1`,
   `BOX_AGENT_SYNC=1` (copy their Claude/Codex rules, skills, plugins; recommended).
   Permissions stay `auto` unless the user asks for another mode
   (`BOX_CLAUDE_PERMISSIONS`, `BOX_CODEX_PERMISSIONS`; `bypass` is their call, never yours).
   Messages between box sessions and their other sessions (`BOX_REMOTE_CONTROL=1`): their
   call too. Tell them first that while a session is connected to Remote Control its
   transcript is stored on Anthropic's servers; off unless they say yes. If they do, the
   laptop side is theirs as well (`/config` → Enable Remote Control for all sessions; `box
   doctor`'s `messaging` check reports it). T3 Code's server on the boxes (`BOX_T3=1`, about
   400 MB a box): their call, `box setup` asks.
6. The box itself (doctor's `box` item): once all required items pass, tell the user the type and cost
   `${CLAUDE_PLUGIN_ROOT}/box plan` shows (billed hourly while the box exists)
   and, with their OK, run `${CLAUDE_PLUGIN_ROOT}/setup.sh`; allow up to 10
   minutes (Claude Code: Bash timeout 600000). If a box or snapshot already
   exists (another laptop set it up), don't run `setup.sh`: see the agentbox
   skill's "new laptop" recipe.
7. Run `${CLAUDE_PLUGIN_ROOT}/box open` (opens a terminal tab on the box). The
   user logs in to Claude in that tab once with `/login` (the tab says so when
   the box isn't signed in; a Codex-only user can skip it). If Codex is enabled, they run
   `box login codex` in their terminal. Recommend disabling key expiry for the
   box in login.tailscale.com/admin/machines.
8. Final `box doctor`. If its `t3` item fails (`BOX_T3=1`), run
   `BOX_NAME=<box> ${CLAUDE_PLUGIN_ROOT}/up.sh`, which installs or repairs T3 Code's server. Offer `box close` to take the first snapshot (it saves
   the logins) and explain `box open` / `box close` for daily use.
