---
name: agentbox
description: Run the user's agentbox from their laptop - a Hetzner Cloud server where coding agents (Claude Code, Codex) work in persistent tmux sessions, snapshotted and deleted when idle. Use for opening/closing the box and what it costs; starting, continuing or moving agent conversations between laptop and box (continue this conversation on the box, a second conversation, an older one, bring it back); bringing a project or its data to the box; secrets and .env files, AWS logins; a slow, stuck or unreachable box or tab; resizing; and setting agentbox up. Not for agents running on the box itself: they use the agentbox-box skill.
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/box ls) Bash(${CLAUDE_PLUGIN_ROOT}/box ls --json) Bash(${CLAUDE_PLUGIN_ROOT}/box status) Bash(${CLAUDE_PLUGIN_ROOT}/box doctor) Bash(${CLAUDE_PLUGIN_ROOT}/box doctor --json) Bash(${CLAUDE_PLUGIN_ROOT}/box check *) Bash(${CLAUDE_PLUGIN_ROOT}/box session ls *) Bash(${CLAUDE_PLUGIN_ROOT}/box sync *) Bash(${CLAUDE_PLUGIN_ROOT}/box plan) Bash(${CLAUDE_PLUGIN_ROOT}/box prices)
---

# agentbox

The box is a Hetzner Cloud server (`agentbox`; with `BOX_MAX_BOXES` > 1 also
`agentbox-2`, …) where coding agents work for the user. It exists only while in
use: `box close` saves it to a snapshot and deletes it (billing stops), `box
open` recreates it with all files, logins, conversations and running sessions.
Every command below is `${CLAUDE_PLUGIN_ROOT}/box …`, run on the laptop.

## Model

- **Project**: a laptop folder (one repo or a workspace of several) that also
  lives on the box in `~/work/NAME`, registered with `box add`. Code moves through
  git; Claude's small per-project memory is synced both ways automatically.
- **Session**: a live agent in a tmux session on the box. `NAME` runs Claude in
  `~/work/NAME`, `NAME@codex` runs Codex there, `NAME+LABEL` is another session in
  the same folder with its own conversation. A session keeps its conversation:
  after a dropped connection the tab reconnects by itself, and after `box close`
  / `box open` it resumes the same conversation. The agent *is* the session:
  quitting it ends the session; closing a tab only detaches, and `box open` then
  leaves that session in the background (listed; `box attach SESSION` gives it a
  tab again).
- **Tabs** open in the user's terminal (one window named `agentbox` in Windows
  Terminal; iTerm2/Terminal on macOS; GNOME Terminal, Konsole, WezTerm, kitty… on
  Linux; else tmux windows). You can't type into them.
- `claude` typed in a plain shell on the box (after `box ssh`) runs in a tmux
  session automatically, so it survives a dropped connection.

## Recipes

**Is it up, what does it cost, is it busy?** `box ls` (state, type, price,
budget, a load line with free memory/swap/CPU and a warning when it's overloaded,
projects, sessions). `box ls --json` for parsing. Down: `box plan` shows the
type and price an open would pick. A box listed `off`, `starting`, `stopping` or
`closing` has a server that doesn't run and still bills: `box open BOX` powers an
`off` one on (no new cost, so no `--yes` needed); a `closing` one is being saved by
`box close`, so wait for it.

**Open / close.** Confirm first (money, running work), unless the user asked for
exactly that. Before opening a down box, run `box plan` and tell the user the type
and price. `box open` starts it and reopens its sessions in tabs (a session already
in a tab here keeps that tab). With several boxes, a bare `box open` only reconnects
the running ones and lists the closed ones: name one (`box open BOX`) to start it,
after the user agreed (several closed boxes and no terminal: it lists them and
stops; ask which, then `box open BOX`). Run by you, `box open` with one box, or
`--all`, that would start a closed box stops and says what it bills: rerun with
`--yes` only after the user agreed. `box close` saves sessions, snapshots and
deletes; it refuses while work exists only on the box (uncommitted/unpushed), listing
it: push it, or `box close BOX --force` for that box with the user's OK (the work
stays in the snapshot; without BOX, `--force` applies to every box). `--force` also
skips the check for agents working outside the tabs (T3 Code's), stopping them mid-turn:
say so when asking. A refused close
leaves the box up and billing: the command exits non-zero, and `box ls` shows which
boxes are still up.

**Work on a project on the box.** `box attach NAME` opens its session in a tab
(starts Claude with the newest conversation of that folder if none is running;
`--agent codex` for Codex). A new conversation: `box new NAME [--as LABEL]`
(session `NAME+LABEL` if NAME is running; LABEL defaults to an id). An older
conversation: `box session ls NAME` lists each side's conversations (id, date,
size, title), then `box attach NAME --resume ID --as LABEL` (always a session
`NAME+LABEL`). `--agent codex` resumes the newest Codex conversation of the folder. `attach`/`new` open
the box first if it's down: that starts billing, so without a terminal they stop, say what
would be billed and ask for `--yes`; pass it only after the user agreed.

**Continue *this* conversation on the box** (Claude Code only; Codex
conversations don't move yet). A push refuses while Claude runs in that project
folder on either side, and you can't end yourself. So: run `box session push
NAME --dry-run` now and tell the user what the box lacks (commands, files not in
git, `.env`); then ask them to quit this session (`/exit`) and any box session in
that folder, and run in their own terminal (not a new Claude session in the same
folder): `box session push NAME --fix` (sets those up and carries the
conversation) and `box attach NAME`. If NAME is still running on the box, `box
session ls NAME` shows the carried conversation's id for `box attach NAME
--resume ID --as LABEL`.

**Bring a conversation back to the laptop.** No Claude may be running in that
project folder on either side (the user quits the box session in its tab, or `box
stop SESSION` with their OK; `--force` overrides only with their OK, since a live
agent keeps writing). If you yourself run in that folder on the laptop, you block it
too: ask the user to quit you first and run the pull in their terminal. Code doesn't
travel with it: first check that the box's work is pushed (`ssh BOX 'cd ~/work/NAME
&& git status -sb && git worktree list'`) and fast-forward the laptop's checkout.
Every worktree a carried conversation used must have its commits on GitHub, on any
branch (one merged into main and deleted there is fine; the laptop gets a local
branch at the same commit). Files outside git
(outputs, data, `.env` values) stay behind: copy what the laptop needs. Then `box
session pull NAME [--last|--all|--id ID] --dry-run`, and the pull itself (large
conversations take more than 10 minutes: run it in the background, without a
`timeout`). On the laptop: `cd PROJECT && claude --continue` (or `claude --resume
ID`). Conversations are Claude Code's; Codex conversations don't move yet.

**Bring a project to the box.** Follow the `onboard` skill: `box check PATH`,
discuss, then `box add PATH [NAME]` (clones the repos into `~/work/NAME`, fills
`agentbox.json` with detected tools and Bitwarden-backed `.env` files). With one box
it goes there (check `box ls`'s load line first). With several, it goes on a running
box only if that box has room for every project's estimate; otherwise it stops,
shows the numbers and asks for `--yes` (opening or creating a box: the user's OK to the
cost) or `--box BOX` (a box the user chooses). Nothing is cloned until then; the project
stays registered.

**Data that isn't in git.** `box data NAME PATH copy` (laptop → box, one way) or
`move-to-box` / `move-to-laptop` (one home, e.g. a SQLite database; stop what uses
it first). PATH is relative to the project folder. Confirm first.

**Secrets and `.env`.** Apps get secrets from Bitwarden, named `APP__NAME`
(arriving without the prefix: `MYAPP__DATABASE_URL` → `DATABASE_URL`) or kept
in a Bitwarden project named after the app (names as they are); production values
are the app `APP_PROD` (`APP_PROD__NAME`, `secrets app_prod -- CMD`), used only for
production work the user asked for; `secrets
--apps` shows which apps exist: `ssh agentbox 'cd ~/work/NAME && secrets APP -- CMD'`. A tool
that insists on a `.env` file gets one in RAM via the laptop's `agentbox.json`
`env_files` (written on every `box attach NAME` and `box sync NAME`). Variables not
in Bitwarden yet: `env_to_bitwarden.py --write`; see reference.md.

**A box session lacks an MCP tool (Linear, Notion, …).** A claude.ai connector
(claude.ai → Settings → Connectors) works on every box with no setup: suggest it
first, not an API key. A server the user added with `claude mcp add --scope user`
reaches the box with `BOX_AGENT_SYNC=1`; the user then signs in once per box with
`/mcp` in a box session (the tab forwards the callback). `python3
${CLAUDE_PLUGIN_ROOT}/mcp_sync.py status` says why one stays on the laptop (a key it
needs goes into Bitwarden as `CLI__MCP_<SERVER>_<NAME>`; localhost servers can't move).

**The user must sign in to a site before an agent works there.** `box browser
NAME` (needs `BOX_PLAYWRIGHT=1`): starts the project's browser on its box and opens it
in a page on the laptop; the user signs in, and the agents' browser tools work in
that same browser. From a box tab, `box browser` there prints a link that opens the
same viewer. Sign-ins live in RAM unless `--persist`; a site that blocks server
addresses (often Google): `--via-laptop`, run on the laptop. `box browser NAME --stop` frees about
500 MB and loses RAM sign-ins: ask first.

**Paste an image into a box session.** The user copies it on the laptop (a screenshot, or an
image file in Explorer/Finder) and presses Ctrl+V in the box tab (Claude Code there reads it
from the laptop through the tab; images only, `BOX_CLIPBOARD_IMAGES`, default on). If nothing
pastes: `agentbox-clipboard status` on the box says whether a tab is connected, has an image, and
what the last Ctrl+V asked for.

**T3 Code on the boxes** (`BOX_T3=1`): the user drives the box's agents from T3 Code's apps;
agentbox installs its server and gives its agents the tabs' environment and sign-in. Agents
outside the tabs show in `box ls`. While one is working, close, resize and move ask in a
terminal and refuse without one: tell the user which, never add `--force` without their OK. `box t3 pair [BOX]` prints a pairing link,
a credential: hand the command to the user to run in their own terminal; never run it to
relay the link. A closed box is unreachable from T3 Code until `box open`.

**Message a session on a box, or have one message you** (Claude Code only). With `BOX_REMOTE_CONTROL=1`
Claude sessions on a box are named `BOX/SESSION · title`
(`agentbox-2/acme+blog · Blog publishing`), and answer to the short
name in front: `SendMessage` to `agentbox-2/acme+blog`, and when it answers "re-send
with the ref", re-send to the full name and ref it offers. `box ls` lists each name. It only works if this laptop session is
connected to Remote Control too (`remoteControlAtStartup`, or `/remote-control`); that's
the user's own setting, never change it. Not listed: `box ls` shows each session's name
and whether it's reachable (`this box only` means it started before the switch was on:
the user types `/remote-control` in its tab; one without its short name yet gets it with
`box names`, which types into the user's sessions: ask first). A closed box's sessions are
`offline`: messages arrive after `box open`. Codex sessions can't message. A handoff that
must outlive the sessions goes in the user's issue tracker, with a short message pointing to it;
never a file.

**AWS on the box.** `box login aws [PROFILE]` copies the laptop's `~/.aws/config`
(no keys) and waits for a device-code approval: start it so you can read its
output while it waits (in the background, or with output to a file you read),
give the user only the link it prints, and wait. Without PROFILE it uses the
config's only SSO session or lists the profiles to choose from. Needed again after every box open.
It signs in on the box of the project whose folder you run it in; elsewhere the only box that is up
(several: pass `--box BOX`). The same goes for `box login codex`.

**The box or a session is slow / stuck.** `box ls`: the load line says whether
the box is short of memory or CPU and lists the biggest users. Ask the session to
stop its dev server/browser, end a session with `box stop SESSION` (confirm; its
conversation stays saved), or `box resize TYPE` (1-2 minutes, sessions resume;
`box prices` lists types; resize refuses types above the budget, with a smaller
disk than the box's. If the type is out of stock where the box is (a server
can't change location) but in stock elsewhere, it offers to move the box through a
snapshot: 3-5 minutes, a new public IP, sessions resume; a bigger disk than the
snapshot's grows for good, so `box open` asks first, and without a terminal that
needs the user's OK and `BOX_ALLOW_GROW=1`). A tab that lost its connection
reconnects by itself; one that says its session ended: `box attach NAME`.

**Unreachable** (`ssh agentbox` fails): `up.sh` (in `${CLAUDE_PLUGIN_ROOT}`,
safe while the box runs; `BOX_NAME=agentbox-2` for another box) repairs the
Tailscale connection and resyncs config. A box that Hetzner shows running but that
doesn't answer at all (`hcloud server metrics BOX --type cpu` near 200% on 2 cores)
has usually run out of memory: tell the user; resetting it (`hcloud server reset
BOX`) stops every agent and is their call. After a reset or restart, the box has lost
what lives only in RAM (the Bitwarden token, `.env` files, the project browser, AWS
logins) and its sessions: run `up.sh` for it, `box sync NAME`, then reopen its sessions: the main one with `box attach NAME` only if its conversation is
the folder's newest (`box session ls NAME`), every other one with `box attach NAME
--resume ID --as LABEL`; while those run, a plain `box attach NAME` would start another
agent on the newest conversation. Old tabs
just report that their session ended.

**Setup, new laptop, budget.** Follow the `setup` skill (`box doctor`, then the
user's own `box setup`). Whether a box or snapshot already exists: `box ls`
(once `hcloud` works). A second laptop for an existing box isn't streamlined
yet: don't run `setup.sh` there (with the same Hetzner project it refuses, since the box
exists; with another it would build a second, paid box). Instead run
`install-local.sh`, copy `~/.config/agentbox/` (box.conf, projects.json; the user
recreates bws.env), use the same Hetzner context, and have the new laptop's SSH
public key added to `~/.ssh/authorized_keys` on the box (from the old laptop). Budget: `box prices`, the user picks, `box budget TYPE`. A higher
budget allows bigger types but `box open` still takes the cheapest in stock; to
get a bigger one now use `box resize`, to keep it `BOX_MIN_RAM` in box.conf.

## Commands

| Command | What it does | Ask first? |
|---|---|---|
| `box ls [--json]`, `box status` | State, cost, load, projects, sessions | No |
| `box plan [BOX]`, `box prices` | What open would pick; price table and budget | No |
| `box doctor [--json]` | Every prerequisite, and who fixes it (user/agent) | No |
| `box open [BOX\|--all] [--yes]` | Start if down (billing), reopen sessions, tabs; several boxes: bare = running ones only | Yes |
| `box close [BOX…\|--idle] [--force]` | Save sessions, snapshot, delete (asks, or refuses without a terminal, while agents outside the tabs work; `--force` skips that and the unpushed-work check) | Yes |
| `box t3 pair [BOX]` | Pairing link for T3 Code's phone/browser apps (`BOX_T3=1`); the user runs it | The user runs it |
| `box attach [NAME] [--agent codex] [--resume ID --as LABEL] [--yes]` | Open a session in a tab | If the box is down |
| `box new [NAME\|OWNER/REPO] [--as LABEL] [--agent codex] [--box BOX] [--yes]` | New conversation (repo cloned first) | If a box would open |
| `box stop [SESSION] [--yes]` | End one session (`--yes` needed without a terminal) | Yes |
| `box resize TYPE [BOX] [--yes] [--force]` | Change type now within budget, disk kept (`--yes` without a terminal; asks or refuses while an agent outside the tabs works, `--force` goes ahead) | Yes |
| `box session push\|pull [NAME[/WORKTREE]] [--last\|--all\|--id ID] [--dry-run\|--fix]` | Carry Claude conversations laptop ↔ box (worktrees too) | Push/pull if asked |
| `box session ls [NAME]` | Each side's conversations | No |
| `box add PATH [NAME] [--box BOX] [--yes]`, `box check [PATH\|NAME]` | Register a project and clone it; what wouldn't move | `add`: yes |
| `box data NAME PATH copy\|move-to-box\|move-to-laptop` | Data outside git | Yes |
| `box sync [NAME] [--pull-setup\|--push-setup]` | Claude memory both ways, personal agent setup onto the box (attach/open do it too) | `--push-setup` overwrites the box's changes: yes |
| `box login aws [PROFILE] [--box BOX]` | AWS config (no keys) + device-code login; you run it, the user approves the link | If asked |
| `box login codex [--box BOX]` | Codex sign-in in the browser; interactive, the user runs it | — |
| `box move [PROJECT] --to BOX\|new [--yes] [--force]` | Move a project to another box (pool): its sessions stop and restart there; the old copy stays as `~/work/NAME.moved-to-BOX` (asks or refuses while an agent outside the tabs works; `--force` goes ahead) | Yes |
| `box budget [TYPE]` | The most expensive type allowed (user's choice only) | User decides |
| `box browser [NAME] [--via-laptop] [--persist] [--stop]` | The project's browser on its box, opened in a page on the laptop | `--stop`: yes |
| `box names [BOX]` | Give running sessions their short names now: types `/rename` into idle ones with nothing typed | Yes (it types into the user's sessions) |
| `box ssh [PROJECT\|BOX]`, `box setup` | Interactive: the user runs them | — |
| `box tick` | Idle warnings; closes idle boxes if `BOX_AUTO_CLOSE_HOURS` is set | Yes, when that is set |

Names are optional for `attach`, `new`, `stop`, `move`, `session`, `ssh`: in a
terminal the user picks from a list; without one (as when you run them) the
command fails listing the names, so pass one. `NAME/SUB` means a subfolder or
worktree of NAME.

## Rules

- **Money and running work.** Opening or creating a box costs money, whichever
  command does it (`open`, `attach`, `new`, `add`, `move`, `setup.sh`), and so does
  `resize`. Without a terminal most of them stop and say what they'd bill, but two
  don't: `box open BOX` (naming the box counts as the user's request) and `setup.sh`
  (it builds a box when none exists). `close`, `stop`, `resize` and `move` interrupt agents; `stop` and `move` list the
  sessions they'd end and, without a terminal, need `--yes`. Confirm unless the user asked for
  exactly that in their latest message, and pass `--yes` only after they agreed.
  Never choose the budget, a type or a box for them (`--box BOX` is their choice).
- **Disk growth.** If `box plan` says the disk would grow, ask first; only then
  `BOX_ALLOW_GROW=1 box open` (snapshots can't shrink).
- **Secrets.** Never print secret values, never ask the user to paste a token into
  chat (`box setup` reads tokens hidden), never commit `.env` files or bypass the
  commit guard with `--no-verify`.
- **Create, delete, power or resize the box only through these commands**; other
  servers in the Hetzner project aren't yours. Never open Hetzner firewall ports
  yourself. Running ordinary commands on it is fine: `ssh agentbox 'cd
  ~/work/NAME && …'` (`ssh agentbox-2 …` for a second box), non-interactive.
- **Long commands** (`open`, `close`, `add`, `new`, `attach`, `resize`, `session
  push|pull`): allow up to 10 minutes. In Claude Code that's the Bash `timeout`
  600000.
- **Idle**: no agent activity for `BOX_IDLE_WARN_HOURS` (default 2). `box ls`
  warns; an idle box still costs money; mention it.

## Codex

Codex sessions (`--agent codex`, session `NAME@codex`) run next to Claude's and
come back after close/open (`codex resume --last`). The user signs Codex in once
per box with `box login codex`. `box new NAME --agent codex` starts a fresh Codex
conversation in its own session, but after close/open that session starts fresh
again. Not yet for Codex: `box session` (conversation transfer). These commands need network access (Hetzner,
Tailscale, ssh) and open terminal tabs: approve them outside the sandbox.

## More

Settings, `agentbox.json` fields, pools, troubleshooting and the Bitwarden import:
see [reference.md](reference.md).
