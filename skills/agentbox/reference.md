# agentbox reference

Details behind the `agentbox` skill. Commands are `${CLAUDE_PLUGIN_ROOT}/box …`
on the laptop unless noted.

## Settings

Every setting, with its default and meaning, is at the top of `${CLAUDE_PLUGIN_ROOT}/lib.sh`;
`~/.config/agentbox/box.conf` (shell syntax `KEY=value`) overrides them. When a change
applies:
- What gets installed on the box (`BOX_AGENTS`, `BOX_PLAYWRIGHT`, `BOX_DOCKER`, `BOX_T3`,
  the ports): `${CLAUDE_PLUGIN_ROOT}/up.sh` once (safe while the box runs;
  `BOX_NAME=agentbox-2` for another), or the box's next start.
- Session modes (`BOX_CLAUDE_PERMISSIONS`, `BOX_CODEX_PERMISSIONS`, `BOX_REMOTE_CONTROL`):
  sessions started after that. `bypass` and Remote Control are the user's choice only.
- `BOX_CLIPBOARD_IMAGES`: new tabs. Budget, types and locations: the next open. Pool and
  idle settings: at once.

## `agentbox.json` (in the project folder; no secret values in it)

```json
{
  "repos": [{"path": ".", "git": "owner/repo"}],
  "data": [{"path": "state", "mode": "home"}, {"path": "fixtures", "mode": "copy"}],
  "ports": [5173, 8000],
  "env_files": [{"path": ".env", "secrets": "myapp"}],
  "tools": ["docker", "terraform", "aws-cli@2"],
  "setup": "python3 skills/install.py",
  "agent": "claude",
  "account": "work",
  "size": "medium",
  "browser": {"persist": true},
  "laptop_only": "the desktop-app integration only runs on Windows"
}
```

- `repos`: each repo's folder and GitHub `owner/repo`, cloned into the same layout.
- `data`: folders outside git. `home` lives in one place (moved with
  `box data … move-to-box`); `copy` is copied one way on request.
- `ports`: forwarded from the box to the laptop's localhost while a tab is open.
- `env_files`: `.env` files a tool insists on, written from Bitwarden app
  `secrets` into RAM on the box on every attach/sync.
- `tools`: mise tool names (`name` or `name@version`) installed on the box;
  `docker` means `BOX_DOCKER`. A tool any project installed with mise also resolves
  outside it when the system lacks it (a default at its newest installed version,
  set on every attach/sync/open).
- `setup`: a shell command run on the box in the project's folder after the clone
  and whenever it or a commit of the project's repos changes (on attach/sync/open),
  e.g. installing the skills the project ships; a failure or a run past
  `BOX_SETUP_TIMEOUT` (900 s) warns, logs to `~/.config/agentbox/setup/NAME.log`
  and is retried on the next sync.
- `agent`: default agent (`claude`/`codex`); `account`: Claude account profile
  (token `CLI__CLAUDE_TOKEN_<ACCOUNT>` in Bitwarden); `size` (`small|medium|large`)
  or `ram_gb`: memory estimate for pool placement, and the least RAM the box's type gets
  (added up over its projects); `laptop_only`: shown on attach
  and before a push; `browser.persist`: keep the project browser's sign-ins on the
  box's disk (and in snapshots) instead of RAM.
- `box add` fills `repos`, `tools` and `env_files` from the repo; re-run detection
  with `python3 ${CLAUDE_PLUGIN_ROOT}/projects.py detect NAME`.

## Secrets (Bitwarden Secrets Manager)

- Names: `APP__NAME` for apps (or a Bitwarden project per app, names as they
  are; the prefixed one wins over the project layout, any other clash stops `secrets`), `CLI__NAME` for tool tokens (`CLI__GH_TOKEN`,
  `CLI__TS_AUTHKEY`, `CLI__RAILWAY_API_TOKEN`, `CLI__CLAUDE_TOKEN_DEFAULT`).
- On laptop and box: `secrets --apps`, `secrets APP` (names), `secrets APP -- CMD`,
  `secrets APP --env-file PATH` (RAM file, symlinked at PATH).
- The machine-account token is in `~/.config/agentbox/bws.env` on the laptop (never
  read or print it); open/up copy it into RAM on the box.
- Adding a `.env`: `python3 ${CLAUDE_PLUGIN_ROOT}/env_to_bitwarden.py --write
  [--skip 'PROD|LIVE'] APP=PATH[,PATH…]` creates the `APP__NAME` secrets in the
  existing `dev` project (`--project` for another), leaves names already there
  alone and prints names only. Leave production values out (`--skip`). It uses a
  read-write token in `~/.config/agentbox/bws-admin.env` that stays on the laptop
  (never read or print it). If that file is missing, the user creates a second
  machine account with read-write access to the project and saves its token
  there with a hidden prompt in their own terminal:
  `read -rsp "token: " t; echo; umask 077; { printf 'BWS_ACCESS_TOKEN=%s\n' "$t"; grep '^BWS_SERVER_URL=' ~/.config/agentbox/bws.env; } > ~/.config/agentbox/bws-admin.env; unset t`.
  Don't use `--out` (an import file) unless they ask: a Bitwarden import always
  creates a new project, which the read-only machine account can't see.
- Layout: project `master` (`CLI__NAME` tool keys agents use), `dev` (`APP__NAME`
  development values), `prod` (`APP_PROD__NAME` production values: `secrets
  app_prod -- cmd`). Never give a production value a `dev` name: `secrets` merges
  every project it can read, so `secrets app` must only ever mean development.
  `secrets` stops with an error when one name has two different values (except an
  `APP__NAME` overriding the same name in a project called `app`). Whether the
  boxes may read `prod` is the user's choice; use `APP_PROD` only when the user
  asked for production work. `env_to_bitwarden.py --write --project prod APP=PATH`
  names them `APP_PROD__NAME` by itself.

## Agent setup on the box (`BOX_AGENT_SYNC=1`)

- Personal setup (`~/.claude` CLAUDE.md/rules/skills/commands, `~/.codex`
  AGENTS.md/rules, `~/.agents/skills`) is a copy of the laptop's, refreshed on
  attach, new, `box sync` and whenever a box starts or `up.sh` runs.
- Something for the box only: put it under `~/.config/agentbox/box/claude/…`,
  `…/codex/…` or `…/agents/skills/…` on the laptop (CLAUDE.md/AGENTS.md there are
  appended). Something for one project: commit it in that repo.
- A sync deletes only files it pushed itself. A file changed or added on the box,
  a folder or a symlink there (say a tool's skill linked into a checkout) is kept
  and reported; `box sync --pull-setup` brings changed files to the laptop,
  `--push-setup` overwrites them (what the box added on its own stays). Don't edit the personal setup on
  the box yourself; edit it on the laptop.

## MCP servers on the box

- claude.ai connectors (claude.ai → Settings → Connectors) work on every box with
  no setup: suggest one first when a box lacks a tool like Linear or Notion.
- With `BOX_AGENT_SYNC=1`, servers the user added with `claude mcp add --scope user`
  (and Codex's `[mcp_servers]`) are copied to the box on every sync, definitions
  only. Sign-ins are per box: the user runs `/mcp` in a box session once (the
  tab forwards the callback port). Don't copy tokens from the laptop.
- A header or variable holding a key comes from Bitwarden `CLI__MCP_<SERVER>_<NAME>`
  (the sync names it); servers at localhost, or whose command the box lacks,
  stay laptop-only. `python3 ${CLAUDE_PLUGIN_ROOT}/mcp_sync.py status` explains
  each server; never print secret values.

## Several boxes (`BOX_MAX_BOXES` > 1)

A project and all its sessions live on one box, recorded as Hetzner labels.
`box new`/`attach`/`add` may reopen a closed box or create a new one when no
running box has room: that costs money, so without a terminal they stop, say
what would be billed and ask for `--yes`; pass it only after the user agreed,
or use `--box BOX` for a box the user chose. Declined, nothing is placed or
cloned. `box move PROJECT --to BOX|new` moves a project (files, conversations,
Codex sessions); it opens only the boxes it needs (asking the same way), ends the
project's sessions only after the same kind of consent (it lists them; `--yes`), and a
project with no files on its box, or on no box, is placed and cloned on BOX. `box close --idle` closes only idle boxes.
`ssh agentbox-2 …` reaches the second box. A project goes to a running box only
if the box's RAM minus what its projects need (declared `ram_gb`/`size`, else
`BOX_PROJECT_RAM`, plus `BOX_SESSION_RAM` per session) leaves room, and never more
than its free memory (free memory alone isn't room: idle projects need theirs
again). A bare `box open` reconnects running boxes only; a closed
box starts when named (`box open BOX`) or with `--all` (without a terminal, `--all`
needs `--yes`). A project lives on box X only if X carries its `p.<project>` label; a
registered project without one is on no box yet. Projects from before pools are
labelled onto the first box once, by itself (`pool.py migrate`).

## A browser the user signs in to (`box browser`, needs `BOX_PLAYWRIGHT=1`)

- One Chromium per project on its box. `box browser NAME` on the laptop starts it and
  opens it in a page there (a tunnel that reconnects by itself); the user signs in, and
  the agents' browser tools in any folder of that project use the same browser, each
  agent session in its own windows. Outside a project folder they get a throwaway
  headless browser.
- Sign-ins live in RAM unless `--persist` or `"browser": {"persist": true}` (then on the
  box's disk and in its snapshots).
- `--via-laptop`: the browser's traffic goes out through the laptop, for sites that
  block servers. Run on the laptop; it stays the project's choice until `--no-via-laptop`.
- On the box, `box browser [NAME]` prints a link (15 minutes, no password in it) that
  opens the same page on the laptop; `agentbox-browser status` and `agentbox-browser
  stop NAME` manage it. About 500 MB while it runs; `box browser NAME --stop` frees it and
  loses RAM sign-ins: ask first.

## Messages between sessions (`BOX_REMOTE_CONTROL=1`, Claude Code only)

- Claude sessions on a box connect to Remote Control named `BOX/SESSION · title`
  (`agentbox-2/acme+blog · Blog publishing`). `SendMessage` to the short name; when it
  answers "re-send with the ref", re-send to the name and ref it offers. `box ls` lists
  each session's name and whether other machines reach it.
- A session started before the switch was on joins when the user types `/remote-control`
  in its tab; `box names [BOX]` then gives it its short name (it types into the user's
  sessions: ask first).
- The laptop side is the user's own setting: `/config` → Enable Remote Control for all
  sessions, and `/remote-control` in running ones. `box doctor` reports it; never change
  it for them.
- While connected, a session's transcript is stored on Anthropic's servers.
- Not covered: Codex sessions, and a message to a session that has ended. A handoff that
  must outlive the sessions goes in the user's issue tracker, with a message pointing to it.

## Troubleshooting

- **`box close` refused** ("holds work that isn't on GitHub"): the box is still up and
  billing (the command exits non-zero; `box ls` shows which boxes are up). Push what it
  lists, or `box close BOX --force` with the user's OK (the work stays in the snapshot;
  `--force` without BOX forces every box). A branch whose commits are all on GitHub under another name
  still counts until it's pushed under its own.
- **A box doesn't answer** (ssh times out, Tailscale shows `rx 0`) while Hetzner says
  running: usually out of memory and swapping (`hcloud server metrics BOX --type cpu`
  near 100% times the core count, as Hetzner adds the cores up; `--type disk` reads in
  GB/s). Tell the user; a reset is their call. Afterwards: `BOX_NAME=BOX up.sh`
  (Bitwarden token, compressed swap, config), `box sync NAME` (`.env` files), then
  reopen its sessions: the main one with `box attach NAME` only if its conversation is
the folder's newest (`box session ls NAME`), every other one with `box attach NAME
--resume ID --as LABEL`; while those run, a plain `box attach NAME` would start another
agent on the newest conversation. On a 4 GB box, test suites should run with few workers and one at a time.
- **`box session pull` refuses a worktree** ("has commits that aren't on GitHub"):
  some of its commits are on no remote branch; push them. A branch merged into main
  and deleted on GitHub doesn't count: the worktree is recreated on the laptop as a
  local branch at the same commit. `box close` and `box ls` use the same test
  (`agentbox-git`, every checkout and worktree, plus stashes and branches not checked out).
- **A tab says Claude isn't signed in**: the user types `/login` there, or stores a
  `claude setup-token` token as `CLI__CLAUDE_TOKEN_DEFAULT` for every new box.
- **`setup.sh` stopped halfway**: run it again; it continues with the server it
  created (`app=<box>` label, no `setup=done` yet) and removes leftover temporary
  SSH rules.
- **"Nothing in stock up to …"**: Hetzner's cheap types sold out; nothing changed
  and the snapshot is safe. Try later or another location, or raise the budget.
- **Host key warnings / `ssh agentbox` fails**: `${CLAUDE_PLUGIN_ROOT}/up.sh`
  re-resolves the Tailscale name, records the box's key and resyncs (safe while it
  runs). If the laptop is off the tailnet it uses a temporary SSH rule for the
  user's IP only.
- **Box diagnostics**: `ssh agentbox box doctor` (tools, secrets, GitHub, Tailscale).
- **Costs**: billed hourly while the server exists, even powered off; while down
  only the snapshot (about €0.0155 per GB per month).
- **Prerequisite errors**: `box doctor` names each one and who fixes it; the `setup` skill
  handles them.
- **Removing a project**: there's no command yet; it's an entry in
  `~/.config/agentbox/projects.json` and a folder `~/work/NAME` on the box.
