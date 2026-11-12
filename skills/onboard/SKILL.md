---
name: onboard
description: Bring a laptop project or multi-repo workspace to the agentbox - check what would not move, write its agentbox.json, clone it on the box.
argument-hint: "PATH [NAME]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/box check *) Bash(${CLAUDE_PLUGIN_ROOT}/box ls)
---

Onboard the folder the user named (PATH, on the laptop: one repo, or a
workspace of several repos plus data folders).

1. Run `${CLAUDE_PLUGIN_ROOT}/box check PATH`. It lists, per repo: unpushed or
   uncommitted work, repos without a GitHub remote, extra worktrees, missing
   version pins, code that reads `.env` files, localhost-bound or Windows-only
   code; and data folders that aren't in git (with SQLite files flagged).
2. Read the flagged code yourself where it matters, then explain to the user
   in plain terms what moves as is, what needs a decision and what stays
   laptop-only. Things only on this laptop (no remote, unpushed, data outside
   git) come first: suggest backing them up or pushing them.
3. Draft `agentbox.json` in the project folder with the user (commit it in a
   single-repo project: it holds no secrets, and a fresh clone needs it):
   - `repos`: path + GitHub `owner/repo` for each repo (filled in by `box add`)
   - `data`: each non-git folder with `"mode": "home"` (lives in ONE place,
     e.g. SQLite state; moved with `box data NAME PATH move-to-box`) or
     `"copy"` (copied one way on request)
   - `ports`: dev-server and OAuth-callback ports to forward to the laptop's
     localhost (Clerk, Google OAuth and host allowlists keep working)
   - `env_files`: `{"path": "...", "secrets": "APP"}` for tools that insist on
     a `.env` file; written from Bitwarden into RAM on the box on every attach
   - `tools`: extra tools the box needs; `setup`: a command the project needs
     run on the box after it's cloned (e.g. its own skills installer), rerun when
     it or the commit changes; `account`: Claude account profile;
     `size` (`small|medium|large`) or `ram_gb`: how much memory it needs, which
     decides the box it goes on; `laptop_only`: what can't move (e.g. needs a
     Windows desktop app)
   Never put secret values in it; secrets go to Bitwarden as `APP__NAME`.
4. With the user's OK, run `${CLAUDE_PLUGIN_ROOT}/box add PATH [NAME]` (allow
   up to 10 minutes; Claude Code: Bash timeout 600000). With one box it goes
   there; with several, on a running box only if that box has room for every
   project's estimate. Otherwise, or if its box is down, it stops and
   shows the numbers and what opening or creating a box would bill: rerun with
   `--yes` only after the user agreed, or `--box BOX` for a box they pick. Declined,
   the project stays registered with nothing cloned. It registers the project, clones the repos
   into the same layout under `~/work/NAME` on the box, adds detected `tools` and
   Bitwarden-backed `env_files` to `agentbox.json`, and syncs Claude's project
   memory.
5. Tell the user what's next: `box data NAME PATH copy|move-to-box` for data,
   `box attach NAME` to work (or, for a Claude Code conversation, `box session
   push NAME` to carry it over), and any code changes worth making (e.g. read env vars before
   `.env` files) as suggestions for their repos.
