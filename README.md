# agentbox

Run Claude Code and Codex on a cloud server that exists only while you use it.

https://github.com/user-attachments/assets/900d7052-44d5-4f7e-95de-8846048e3861

Agents that work for hours need a computer that stays on. On a laptop that means
keeping the lid open, sharing its memory with every test suite they run, and giving
them a shell next to your SSH keys and browser logins.

agentbox gives them a Hetzner server of their own. `box close` saves everything and
deletes the server, so it costs cents a month while you're away. `box open` brings it
back with every agent session where you left it, one terminal tab each.

- **Cheap:** about €0.01 an hour for 2 vCPU and 4 GB while it runs.
- **Private:** no open ports. You reach the box over your own Tailscale network.
- **No secrets on disk:** they come from Bitwarden into the box's memory.
- **Made for agents:** a Claude Code plugin and Codex skills, so your agent sets it up
  and runs it for you.

## How it works

```
 laptop                          Tailscale                 Hetzner
 ┌──────────────────┐                                 ┌───────────────────────┐
 │ box open/close   │ ──────── private network ─────▶ │ box: tmux sessions,   │
 │ one tab per      │                                 │ one per agent         │
 │ agent session    │ ◀── tabs reconnect by themselves│ secrets in RAM        │
 └──────────────────┘                                 └──────────┬────────────┘
                                                        box close│ ▲ box open
                                                                 ▼ │
                                                      ┌───────────────────────┐
                                                      │ snapshot (cents/month)│
                                                      └───────────────────────┘
```

- **`box close`** saves which sessions are running, snapshots the server and deletes it.
  Hetzner bills a server for every hour it exists, even switched off, so deleting it is
  what stops the cost.
- **`box open`** creates a server from the snapshot, using the cheapest type in stock
  within your budget. The sessions start again, each resuming its conversation, and a
  tab opens for each.
- The agents run inside tmux on the box. Closing a tab, losing Wi-Fi or putting the
  laptop to sleep doesn't stop them.

## Get started

You need accounts with [Hetzner Cloud](https://www.hetzner.com/cloud) (you pay them
directly), [Tailscale](https://tailscale.com) and [Bitwarden Secrets
Manager](https://bitwarden.com/products/secrets-manager/), both free for this, plus
GitHub and your Claude (or ChatGPT, for Codex) plan.

**1. Install.** In Claude Code:

```
/plugin marketplace add rd-rugg/agentbox
/plugin install agentbox@agentbox
```

For Codex, or to use `box` without an agent:

```bash
git clone https://github.com/rd-rugg/agentbox && cd agentbox && ./install-local.sh
```

**2. Set it up.** Tell your agent *"set up agentbox"*. It checks what's missing (`box
doctor`), does what it can, and tells you what only you can do: create the accounts and
their tokens, and choose a budget. For those steps run `box setup` in your own terminal.
It asks for tokens with hidden input, so they never pass through the chat.

**3. Open it.** Setup ends by building the box, after you agree to the price (about 5
minutes). Then `box open` opens a tab on it: sign in to Claude there, once per box.

## Daily use

```bash
box open                  # start the box and reopen its sessions, one tab each
box new myproject         # a new conversation in a new tab
box new owner/repo        # clone a GitHub repo onto the box and start there
box attach myproject      # open a project, resuming its last conversation
box ls                    # what runs, what it costs, how loaded the box is
box close                 # save, snapshot, delete: stops the cost
```

`box help` lists every command. In Claude Code you can also just ask: *"open my box"*,
*"what is it costing?"*.

Closing a tab leaves the agent working; `box attach` gets the tab back. Quitting the
agent ends the session. Before deleting the box, `box close` checks for work that isn't
on GitHub and for agents still working, and asks.

## What else it does

- **Paste images** into a box tab with Ctrl+V, as with Claude Code on your laptop.
- **Sign in to websites for the agents:** `box browser` shows a Chromium running on the
  box in a page on your laptop. Sign in once and the agents' browser tools use that
  session.
- **Bring your projects:** `box add` brings a laptop project to the box, and `box session
  push` / `pull` carry Claude conversations between laptop and box.
- **Your setup comes along:** your CLAUDE.md, rules, skills and MCP servers are mirrored
  to the box.
- **Secrets from Bitwarden:** `secrets myapp -- npm run dev` works the same on laptop and
  box. See [Secrets](docs/secrets.md).
- **Several boxes:** projects spread over a pool of boxes; `box move` and `box resize`
  change where and on what they run.
- **Codex** next to Claude Code, in the same tabs.
- **Messages between sessions** on the boxes and your laptop, through Claude Code's
  Remote Control.
- **[T3 Code](https://github.com/pingdotgg/t3code):** its desktop, web and phone apps can
  drive the box's agents.

Most of these are switched on in `~/.config/agentbox/box.conf`, which `box setup` writes.
Every setting is listed with its default and meaning at the top of [`lib.sh`](lib.sh).

## Security

Agents run commands, so the box keeps them away from what matters:

- The Hetzner firewall has no inbound rules. You connect over Tailscale; only when Tailscale
  can't reach the box (the first setup, a repair) is SSH opened to your own IP, for a few minutes.
- Secrets from Bitwarden, `.env` files included, live only in the box's memory, never on
  its disk or in a snapshot. The box's Bitwarden token is read-only.
- Every repository on the box refuses commits that contain a `.env` file or a value
  stored in Bitwarden.
- Your SSH keys, password manager and personal browser never reach the box. If an agent
  breaks it, you lose a server, not your laptop.

What does stay on the box's disk, and so in its snapshots: the Claude and Codex sign-ins,
MCP sign-ins, and anything you type into T3 Code. Report vulnerabilities as described in
[SECURITY.md](SECURITY.md).

## Costs

You pay Hetzner by the hour while a box exists: about €0.01 for a 2 vCPU / 4 GB server,
or €6 a month if it never closed. A closed box is a snapshot at €0.0155 per GB a month;
a box in daily use makes a 3 to 13 GB snapshot, so €0.05 to €0.20 a month. `box ls`
shows what each box costs. You set the most expensive server type agentbox may use.

## FAQ

**What happens to a running agent when I close the box?** It's stopped, and its session
starts again, resuming the same conversation, on the next `box open`. `box close` asks
first when an agent is in the middle of something.

**What if I forget to close it?** `box ls` and `box open` warn when a box has been idle
for 2 hours. Set `BOX_AUTO_CLOSE_HOURS` and run `box tick` from a timer to close idle
boxes on their own; it never closes one with unpushed work.

**Hetzner has nothing in stock.** Nothing is changed. Try later, add locations, or raise
the budget.

**Something doesn't work.** Run `box doctor`: it checks every prerequisite and says who
fixes what. On the box, `box doctor` checks its tools, secrets and network.

**How do I use T3 Code with it?** Answer yes to T3 Code in `box setup` (or set `BOX_T3=1`
in `box.conf`). In T3 Code's desktop app: Settings → Environments → Add environment → SSH
→ `agentbox`, then open a project folder in `~/work`. For your phone or a browser, run
`box t3 pair` and scan the code. Open the box first: T3 Code can't open a closed one.

**Does it work on macOS and Linux?** Yes. It's used most on Windows with WSL; on macOS
and Linux, `BOX_TERMINAL=tmux` is the fallback if your terminal's tabs misbehave.

## Limits

- Hetzner only.
- Codex conversations stay on the box; only Claude Code's move between laptop and box.
- A box you delete for good stays listed in Tailscale until you remove it in the admin
  console. agentbox has no admin rights there.

## Removing it

```bash
box close                                   # every box
hcloud image list --type snapshot           # then: hcloud image delete ID, for each agentbox one
hcloud firewall delete agentbox
```

Then remove the boxes from Tailscale's admin console, revoke the Hetzner, Bitwarden,
Tailscale and GitHub tokens, uninstall the plugin (`claude plugin uninstall agentbox@agentbox`) and
delete `~/.config/agentbox`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). MIT license.
