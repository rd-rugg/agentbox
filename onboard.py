#!/usr/bin/env python3
"""agentbox prerequisites: check them, and guide a human through the parts only
a human can do (accounts, tokens from web consoles, sign-ins, money decisions).

  onboard.py doctor [--json]   every prerequisite in setup order: ok or what's missing, and who fixes it
  onboard.py wizard            interactive, for the human: asks, links, reads tokens hidden, verifies
  onboard.py gate              exit 1 if something setup.sh needs is missing (used by setup.sh)

"who": "you" = only a human can do it; "agent" = Claude Code / Codex (or the wizard) can do it.
Tokens typed into the wizard are never printed or written to disk, except the
Bitwarden machine-account token, which goes to ~/.config/agentbox/bws.env (0600).
Standard-library Python 3.8+.
"""
import getpass
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import uuid

REPO = os.path.dirname(os.path.realpath(__file__))
CONF_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "agentbox")
BWS_ENV = os.path.join(CONF_DIR, "bws.env")
BOX_CONF = os.path.join(CONF_DIR, "box.conf")
BOX = os.environ.get("BOX_NAME") or "agentbox"
LABEL = f"app={BOX}"
WSL = "microsoft" in platform.uname().release.lower()
MAC = sys.platform == "darwin"
WSL_TAILSCALE = "/mnt/c/Program Files/Tailscale/tailscale.exe"


def run(*cmd, input=None, timeout=60):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, input=input, timeout=timeout)
        return out.returncode, out.stdout.strip(), out.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, "", str(e)


def conf_value(key):
    try:
        for line in open(BOX_CONF):
            m = re.match(rf"\s*{key}=(.*)", line)
            if m:
                return m.group(1).strip().strip('"\'')
    except FileNotFoundError:
        pass
    return None


def install_hint(pkg_mac, pkg_apt=None, other=""):
    if MAC:
        return f"brew install {pkg_mac}"
    if pkg_apt:
        return f"sudo apt install {pkg_apt}  (or: brew install {pkg_mac}){other}"
    return f"brew install {pkg_mac}{other}"


# --- checks ------------------------------------------------------------------------
# Each returns (ok, detail). Order = the order setup should happen in.

def c_tools():
    missing = [t for t in ("ssh", "scp", "curl", "git", "python3") if not shutil.which(t)]
    rc, out, _ = run("bash", "-c", "echo ${BASH_VERSINFO[0]}")
    if not out.isdigit() or int(out) < 4:
        # lib.sh also finds Homebrew's bash on its own.
        brew = [b for b in ("/opt/homebrew/bin/bash", "/usr/local/bin/bash") if os.path.exists(b)]
        if not brew:
            missing.append("bash 4+")
    rc, out, _ = run("rsync", "--version")
    m = re.search(r"version\s+(\d+)", out)
    if not m or int(m.group(1)) < 3:
        missing.append("rsync 3+")
    return (not missing), ("all present" if not missing else "missing: " + ", ".join(missing))


def c_hcloud_cli():
    return bool(shutil.which("hcloud")), shutil.which("hcloud") or "not installed"


def c_hcloud_context():
    if not shutil.which("hcloud"):
        return False, "hcloud not installed yet"
    rc, ctx, _ = run("hcloud", "context", "active")
    if rc != 0 or not ctx:
        return False, "no active context"
    rc, _, err = run("hcloud", "server", "list", "-o", "noheader")
    return rc == 0, f"context '{ctx}'" + ("" if rc == 0 else f", but the token doesn't work: {err[:80]}")


def local_pubkey():
    for name in ("id_ed25519.pub", "id_ecdsa.pub", "id_rsa.pub"):
        p = os.path.expanduser(f"~/.ssh/{name}")
        if os.path.exists(p):
            return p
    return None


def c_ssh_key_local():
    p = local_pubkey()
    return bool(p), p or "no SSH key in ~/.ssh"


def c_ssh_key_hetzner():
    p = local_pubkey()
    if not p or not shutil.which("hcloud"):
        return False, "needs the local key and hcloud first"
    rc, fp, _ = run("ssh-keygen", "-E", "md5", "-lf", p)
    fp = fp.split()[1].replace("MD5:", "") if rc == 0 and fp else ""
    rc, out, _ = run("hcloud", "ssh-key", "list", "-o", "json")
    if rc != 0:
        return False, "can't list Hetzner SSH keys (check the hcloud context)"
    keys = json.loads(out or "[]")
    match = [k["name"] for k in keys if k.get("fingerprint") == fp]
    return bool(match), (f"uploaded as '{match[0]}'" if match else f"{os.path.basename(p)} isn't in the Hetzner project")


def tailscale_cmd():
    if shutil.which("tailscale"):
        return ["tailscale"]
    if WSL and os.path.exists(WSL_TAILSCALE):
        return [WSL_TAILSCALE]
    if MAC and os.path.exists("/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        return ["/Applications/Tailscale.app/Contents/MacOS/Tailscale"]
    return None


def tailscale_status():
    cmd = tailscale_cmd()
    if not cmd:
        return None
    rc, out, _ = run(*cmd, "status", "--json")
    try:
        return json.loads(out) if rc == 0 else None
    except ValueError:
        return None


def tailnet_host(name):
    """The tailnet name to reach box `name` at. Tailscale adds a suffix (agentbox-2-1)
    when an old machine with the same hostname is still registered, so pick the
    online machine with that hostname, else the one seen most recently."""
    peers = [p for p in ((tailscale_status() or {}).get("Peer") or {}).values() if p.get("HostName") == name]
    if not peers:
        return name
    best = max(peers, key=lambda p: (bool(p.get("Online")), p.get("LastSeen") or ""))
    return (best.get("DNSName") or name).rstrip(".")


def c_tailscale():
    st = tailscale_status()
    if st is None:
        return False, "Tailscale isn't installed" + (" on Windows" if WSL else "")
    return st.get("BackendState") == "Running", f"state: {st.get('BackendState')}"


def c_magicdns():
    st = tailscale_status() or {}
    enabled = (st.get("CurrentTailnet") or {}).get("MagicDNSEnabled")
    return bool(enabled), "enabled" if enabled else "off (the box is reached by name through it)"


def secrets_names(app="cli"):
    exe = os.path.join(REPO, "server", "secrets")
    rc, out, err = run(sys.executable, exe, app, timeout=90)
    return (rc, out.splitlines(), err)


def c_bws_token():
    if not os.path.exists(BWS_ENV):
        return False, f"{BWS_ENV} missing"
    if not shutil.which("bws"):
        return False, "bws CLI not installed (install-local.sh installs it)"
    rc, out, err = run(sys.executable, os.path.join(REPO, "server", "secrets"), "--refresh", timeout=90)
    if rc != 0:
        return False, "token rejected: " + err[:120].replace("\n", " ")
    return True, out


def bitwarden_ready():
    return os.path.exists(BWS_ENV) and bool(shutil.which("bws"))


def c_bws_access():
    if not bitwarden_ready():
        return None, "waits for the Bitwarden token"
    rc, out, err = run(sys.executable, os.path.join(REPO, "server", "secrets"), "--apps", timeout=90)
    if rc != 0:
        return False, "Bitwarden not reachable yet"
    return bool(out), ("apps: " + " ".join(out.split())) if out else "0 secrets visible: grant the machine account access to your project"


def _has_cli(name):
    rc, names, _ = secrets_names("cli")
    return rc == 0 and name in names


def c_ts_key():
    if not bitwarden_ready():
        return None, "waits for the Bitwarden token"
    return _has_cli("TS_AUTHKEY"), "CLI__TS_AUTHKEY in Bitwarden" if _has_cli("TS_AUTHKEY") else "CLI__TS_AUTHKEY missing"


def c_gh_token():
    if not bitwarden_ready():
        return None, "waits for the Bitwarden token"
    ok = _has_cli("GH_TOKEN")
    return ok, "CLI__GH_TOKEN in Bitwarden" if ok else "CLI__GH_TOKEN missing (the box can't clone or push)"


def c_claude_default_token():
    if int(conf_value("BOX_MAX_BOXES") or os.environ.get("BOX_MAX_BOXES") or 1) <= 1:
        return None, "only needed with more than one box (BOX_MAX_BOXES)"
    if not bitwarden_ready():
        return None, "waits for the Bitwarden token"
    ok = _has_cli("CLAUDE_TOKEN_DEFAULT")
    return ok, ("in Bitwarden: new boxes are logged in automatically" if ok
                else "missing: every new box needs a manual Claude login in its tab")


def c_budget():
    v = conf_value("BOX_MAX_TYPE")
    return bool(v), f"up to {v}" if v else "not chosen yet"


def c_local_commands():
    missing = [c for c in ("box", "secrets", "bws") if not shutil.which(c)]
    return not missing, "installed" if not missing else "missing: " + ", ".join(missing)


def c_terminal():
    kind = detect_terminal()
    return kind != "print", {"wt": "Windows Terminal", "iterm": "iTerm2", "terminal": "macOS Terminal",
                             "gnome": "GNOME Terminal", "konsole": "Konsole", "xfce": "Xfce Terminal",
                             "wezterm": "WezTerm", "kitty": "kitty", "tmux": "tmux (one window per session)",
                             "print": "none found: box prints the ssh commands instead (install tmux)"}[kind]


def detect_terminal():
    forced = os.environ.get("BOX_TERMINAL") or conf_value("BOX_TERMINAL")
    if forced:
        return forced
    if os.environ.get("TMUX"):
        return "tmux"
    if WSL and shutil.which("wt.exe"):
        return "wt"
    if MAC:
        return "iterm" if os.path.exists("/Applications/iTerm.app") else "terminal"
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        for kind, exe in (("gnome", "gnome-terminal"), ("konsole", "konsole"), ("xfce", "xfce4-terminal"),
                          ("wezterm", "wezterm"), ("kitty", "kitty")):
            if shutil.which(exe):
                return kind
    return "tmux" if shutil.which("tmux") else "print"


def box_state():
    if not shutil.which("hcloud"):
        return "unknown"
    rc, _, _ = run("hcloud", "server", "describe", BOX)
    if rc == 0:
        return "up"
    rc, out, _ = run("hcloud", "image", "list", "--type", "snapshot", "--selector", LABEL, "-o", "noheader")
    return "down" if out else "none"


def c_box():
    s = box_state()
    return s in ("up", "down"), {"up": "running", "down": "saved as a snapshot (box open)",
                                 "none": "not built yet (setup.sh)", "unknown": "hcloud not ready"}[s]


def ssh_box(cmd):
    return run("ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", BOX, f"bash -lc {json.dumps(cmd)}", timeout=40)


def c_claude_login():
    if box_state() != "up":
        return None, "check when the box is up"
    # The credentials file also holds MCP servers' sign-ins: ask the box for a real login.
    rc, out, _ = ssh_box("python3 -c 'import json,os; p=os.path.expanduser(\"~/.claude/.credentials.json\"); "
                         "d=json.load(open(p)) if os.path.exists(p) else {}; "
                         "print(\"yes\" if (d.get(\"claudeAiOauth\") or {}).get(\"accessToken\") else \"no\")'")
    if rc == 0 and out.strip() == "yes":
        return True, "logged in"
    if bitwarden_ready() and _has_cli("CLAUDE_TOKEN_DEFAULT"):
        return True, "no login of its own; sessions use CLI__CLAUDE_TOKEN_DEFAULT from Bitwarden"
    return False, "not logged in: type /login in a box tab (or store CLI__CLAUDE_TOKEN_DEFAULT, see the setup skill)"


def c_t3():
    if conf_value("BOX_T3") != "1":
        return None, "T3 Code not enabled (BOX_T3)"
    if box_state() != "up":
        return None, "check when the box is up"
    rc, out, _ = ssh_box("systemctl --user is-active t3code.service")
    ok = rc == 0 and out.strip() == "active"
    return ok, "T3 Code's server is running" if ok else "T3 Code's server isn't running on the box"


def c_codex_login():
    if "codex" not in (conf_value("BOX_AGENTS") or "claude"):
        return None, "Codex not enabled (BOX_AGENTS)"
    if box_state() != "up":
        return None, "check when the box is up"
    rc, out, _ = ssh_box("codex login status")
    ok = rc == 0 and "not logged" not in out.lower()
    return ok, "logged in" if ok else "not logged in: box login codex"


def c_mcp():
    """Which of your MCP servers reach the boxes (definitions only; sign-ins are per box)."""
    import mcp_sync
    if (conf_value("BOX_AGENT_SYNC") or conf_value("BOX_CLAUDE_SYNC") or "0") != "1":
        return None, "your personal setup isn't synced (BOX_AGENT_SYNC=0); claude.ai connectors still work"
    p = mcp_sync.plan(conf_value("BOX_AGENTS") or "claude")
    going = len(p["claude"]) + len(p["codex"])
    by = {}
    for agent, n, why in p["skipped"]:
        by.setdefault((n, why), []).append(agent)
    fixable = {k: a for k, a in by.items() if k[1] not in mcp_sync.BY_DESIGN}
    by_design = sorted({n for (n, why) in by if why in mcp_sync.BY_DESIGN})
    detail = f"{going} go to the box; sign in once per box with /mcp" if going else "none to bring"
    if by_design:
        detail += f"; laptop-only by design: {', '.join(by_design)}"
    if not fixable:
        return True, detail
    stay = "; ".join(f"{n} for {' and '.join(a)}: {why}" for (n, why), a in fixable.items())
    return False, f"{detail}; laptop-only until fixed: {stay}"


def claude_settings():
    """Claude Code's settings on this laptop that matter here, in its precedence order: managed
    settings first, then the user's ~/.claude/settings.json (project files can't turn
    Remote Control on)."""
    paths = ["/Library/Application Support/ClaudeCode/managed-settings.json", "/etc/claude-code/managed-settings.json",
             os.path.expanduser("~/.claude/settings.json")]
    found = []
    for path in paths:
        try:
            found.append(json.load(open(path)))
        except (OSError, ValueError):
            pass
    return found


def c_messaging():
    """Messages between sessions on different machines: the boxes' switch and the laptop's own
    Claude Code setting, which agentbox only reads (it's the user's choice)."""
    if conf_value("BOX_REMOTE_CONTROL") != "1":
        return None, "off (BOX_REMOTE_CONTROL=0): sessions message only sessions on their own machine"
    settings = claude_settings()
    env = dict(os.environ)
    for s in reversed(settings):       # a settings file's env block applies to its sessions too
        env.update({k: str(v) for k, v in (s.get("env") or {}).items()})
    off = [v for v in ("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "DISABLE_GROWTHBOOK") if env.get(v)]
    if off:
        return False, f"{' and '.join(off)} turns Remote Control off for this laptop's sessions"
    on = next((s["remoteControlAtStartup"] for s in settings if "remoteControlAtStartup" in s), None)
    if on is True:
        return True, "on: box sessions answer to BOX/SESSION, and this laptop's sessions connect too"
    return False, ("box sessions are reachable, but this laptop's sessions don't connect, so boxes can't "
                   "see them or reply")


CHECKS = [
    # id, title, who, required, check, how to fix
    ("tools", "Laptop tools (bash 4+, ssh, curl, git, rsync 3+)", "agent", True, c_tools,
     "macOS: brew install bash rsync. Linux: install them with your package manager."),
    ("hcloud-cli", "Hetzner CLI (hcloud)", "agent", True, c_hcloud_cli,
     install_hint("hcloud", None, "; or a release binary from github.com/hetznercloud/cli")),
    ("hcloud-token", "Hetzner account + project + API token", "you", True, c_hcloud_context,
     "console.hetzner.com → your project → Security → API tokens → Read & Write; then: hcloud context create agentbox"),
    ("ssh-key", "SSH key on this laptop", "agent", True, c_ssh_key_local,
     "mkdir -p -m 700 ~/.ssh && ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519"),
    ("ssh-key-hetzner", "SSH key uploaded to Hetzner", "agent", True, c_ssh_key_hetzner,
     "hcloud ssh-key create --name $(hostname)-agentbox --public-key-from-file ~/.ssh/id_ed25519.pub"),
    ("tailscale", "Tailscale on this laptop, signed in", "you", True, c_tailscale,
     "Install from tailscale.com/download" + (" (the Windows app; WSL uses it)" if WSL else "") + " and sign in"),
    ("magicdns", "Tailscale MagicDNS on", "you", True, c_magicdns,
     "login.tailscale.com/admin/dns → Enable MagicDNS"),
    ("local-commands", "agentbox commands on this laptop (box, secrets, bws)", "agent", True, c_local_commands,
     "./install-local.sh"),
    ("bws-token", "Bitwarden Secrets Manager machine-account token", "you", True, c_bws_token,
     "box setup (asks for it hidden and saves it to ~/.config/agentbox/bws.env)"),
    ("bws-access", "Machine account can read your project", "you", True, c_bws_access,
     "Bitwarden → Secrets Manager → Machine accounts → yours → Projects → add your project, Can read"),
    ("ts-authkey", "Tailscale auth key in Bitwarden (CLI__TS_AUTHKEY)", "you", True, c_ts_key,
     "box setup (login.tailscale.com/admin/settings/keys → reusable key)"),
    ("gh-token", "GitHub token in Bitwarden (CLI__GH_TOKEN)", "you", False, c_gh_token,
     "box setup (github.com/settings/personal-access-tokens/new → fine-grained)"),
    ("claude-default-token", "Claude token for extra boxes (CLI__CLAUDE_TOKEN_DEFAULT)", "you", False,
     c_claude_default_token, "run `claude setup-token`, then box setup adds it to Bitwarden"),
    ("budget", "Most expensive server type allowed", "you", True, c_budget,
     "a money decision: box setup, or ask your agent to show ./setup.sh --prices"),
    ("terminal", "Terminal for one tab per session", "agent", False, c_terminal,
     "install tmux, or set BOX_TERMINAL in box.conf"),
    ("box", "The box itself", "agent", True, c_box,
     "./setup.sh (creates a paid server: about €0.01/hour for a cx23)"),
    ("claude-login", "Claude signed in on the box", "you", False, c_claude_login,
     "box open, then follow Claude's login link in the tab"),
    ("codex-login", "Codex signed in on the box", "you", False, c_codex_login,
     "box login codex"),
    ("t3", "T3 Code's server on the box (BOX_T3)", "agent", False, c_t3,
     "BOX_NAME=<box> ./up.sh in the agentbox folder installs or repairs it; on the box, t3 service status says why"),
    ("messaging", "Messages between sessions on different machines", "you", False, c_messaging,
     "in Claude Code on this laptop: /config → Enable Remote Control for all sessions (sessions already "
     "running: /remote-control in each)"),
    ("mcp", "Your MCP servers on the box", "you", False, c_mcp,
     "python3 mcp_sync.py status shows why; a claude.ai connector (claude.ai → Settings → Connectors) "
     "works on every box with no setup"),
]
GATE = {"tools", "hcloud-cli", "hcloud-token", "ssh-key", "ssh-key-hetzner", "tailscale", "magicdns",
        "local-commands", "bws-token", "bws-access", "ts-authkey", "budget"}


def evaluate(only=None):
    results, blocked = [], False
    for cid, title, who, required, fn, fix in CHECKS:
        if only and cid not in only:
            continue
        try:
            ok, detail = fn()
        except Exception as e:  # a broken check must not hide the others
            ok, detail = False, f"check failed: {e}"
        results.append({"id": cid, "title": title, "who": who, "required": required,
                        "ok": ok, "detail": detail, "fix": fix})
    return results


def cmd_doctor(as_json):
    results = evaluate()
    if as_json:
        print(json.dumps(results, indent=2))
        return
    for r in results:
        mark = "✓" if r["ok"] else ("·" if r["ok"] is None else ("✗" if r["required"] else "○"))
        who = "" if r["ok"] else f"  [{'YOU' if r['who'] == 'you' else 'agent'}]"
        print(f" {mark} {r['title']}: {r['detail']}{who}")
        if r["ok"] is False:
            print(f"     → {r['fix']}")
    todo_you = [r for r in results if r["ok"] is False and r["who"] == "you" and r["required"]]
    todo_agent = [r for r in results if r["ok"] is False and r["who"] == "agent" and r["required"]]
    print()
    if not todo_you and not todo_agent:
        print("Everything required is in place.")
    else:
        if todo_you:
            print(f"{len(todo_you)} step(s) only you can do: run `box setup` in your own terminal.")
        if todo_agent:
            print(f"{len(todo_agent)} step(s) your agent can do (or `box setup` offers to).")


def cmd_gate():
    missing = [r for r in evaluate(GATE) if not r["ok"]]
    if missing:
        print("Not ready to build the box yet:", file=sys.stderr)
        for r in missing:
            print(f"  ✗ {r['title']}: {r['detail']}\n    → {r['fix']}", file=sys.stderr)
        print("Run `box doctor` for the full list, or `box setup` to be guided through it.", file=sys.stderr)
        sys.exit(1)


# --- wizard (human) ------------------------------------------------------------------
def say(text=""):
    print(text)


def ask(question, default=None):
    suffix = f" [{default}]" if default else ""
    answer = input(f"{question}{suffix}: ").strip()
    return answer or (default or "")


def yes(question, default=True):
    d = "Y/n" if default else "y/N"
    answer = input(f"{question} [{d}] ").strip().lower()
    return default if not answer else answer.startswith("y")


def hidden(prompt, pattern=None, example=""):
    while True:
        value = getpass.getpass(f"{prompt} (hidden, paste and press Enter): ").strip()
        if not value:
            return None
        if pattern and not re.match(pattern, value):
            say(f"  That doesn't look right{(' (expected ' + example + ')') if example else ''}. Try again, or leave empty to skip.")
            continue
        return value


def pause(text="Press Enter when that's done"):
    input(f"{text} ")


def copy_to_clipboard(text):
    candidates = []
    if WSL:
        candidates.append(["powershell.exe", "-NoProfile", "-Command", "$input | Out-String | Set-Clipboard"])
    if MAC:
        candidates.append(["pbcopy"])
    candidates += [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    for cmd in candidates:
        if shutil.which(cmd[0]):
            rc, _, _ = run(*cmd, input=text)
            if rc == 0:
                return True
    return False


def set_conf(key, value):
    os.makedirs(CONF_DIR, exist_ok=True)
    lines = open(BOX_CONF).read().splitlines() if os.path.exists(BOX_CONF) else []
    lines = [l for l in lines if not re.match(rf"\s*{key}=", l)] + [f'{key}="{value}"']
    with open(BOX_CONF, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def ok_now(cid):
    return evaluate({cid})[0]["ok"]


def step(title):
    say()
    say(f"── {title} " + "─" * max(0, 60 - len(title)))


def import_to_bitwarden(pending):
    """Hand tool tokens to Bitwarden via the web app's import (the machine account is read-only)."""
    if not pending:
        return
    data = {"projects": [], "secrets": [
        {"id": str(uuid.uuid4()), "key": k, "value": v, "note": "agentbox tool token", "projectIds": []}
        for k, v in pending.items()]}
    text = json.dumps(data)
    step("Put the tokens into Bitwarden")
    if copy_to_clipboard(text):
        say(f"The {len(pending)} token(s) ({', '.join(pending)}) are in your clipboard as a Bitwarden import.")
    else:
        path = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", f"agentbox-import-{os.getpid()}.json")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        say(f"No clipboard tool found; the import is in {path} (delete it afterwards).")
    say("  1. Bitwarden web app → Secrets Manager → Settings → Import data → paste (or choose the file)")
    say("  2. Open each imported secret and set its Project to the one your machine account can read")
    say("  3. Clear your clipboard (and its history, if you use one)")
    while True:
        pause("Press Enter when imported")
        run(sys.executable, os.path.join(REPO, "server", "secrets"), "--refresh", timeout=90)
        rc, names, _ = secrets_names("cli")
        missing = [k for k in pending if k.split("__", 1)[1] not in names]
        if not missing:
            say("  ✓ The box can read them now.")
            return
        say(f"  Still not visible: {', '.join(missing)} (imported? project set?)")
        if not yes("Check again?"):
            return


def cmd_wizard():
    if not sys.stdin.isatty():
        sys.exit("box setup is interactive: run it in your own terminal (it asks for tokens, hidden).")
    say("agentbox setup. It checks what's already done and only asks for what's missing.")
    say("Tokens you paste are hidden, never printed and not saved, except the Bitwarden one (0600).")
    pending = {}

    # Agent-doable basics, offered here for people setting up without an agent.
    if not ok_now("local-commands") and yes("Install box, secrets and bws into ~/.local/bin now?"):
        subprocess.run([os.path.join(REPO, "install-local.sh")])

    step("1. Hetzner Cloud (servers)")
    if ok_now("hcloud-token"):
        say("  ✓ already set up")
    elif not shutil.which("hcloud"):
        say(f"  Install the Hetzner CLI first: {install_hint('hcloud')}, then run box setup again.")
    else:
        say("  Create an account at hetzner.com/cloud and a project in console.hetzner.com.")
        say("  In the project: Security → API tokens → Generate → Read & Write. Copy it.")
        pause("Press Enter, then paste the token when hcloud asks")
        subprocess.run(["hcloud", "context", "create", "agentbox"])
        say("  ✓ works" if ok_now("hcloud-token") else "  ✗ still not working; check the token and run box setup again")
    if not ok_now("ssh-key") and yes("  No SSH key on this laptop. Create one (~/.ssh/id_ed25519)?"):
        os.makedirs(os.path.expanduser("~/.ssh"), mode=0o700, exist_ok=True)   # ssh-keygen -f won't create it
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", os.path.expanduser("~/.ssh/id_ed25519")])
    if ok_now("hcloud-token") and ok_now("ssh-key") and not ok_now("ssh-key-hetzner"):
        if yes("  Upload your SSH key to the Hetzner project?"):
            run("hcloud", "ssh-key", "create", "--name", f"{platform.node()}-agentbox",
                "--public-key-from-file", local_pubkey())
            say("  ✓ uploaded" if ok_now("ssh-key-hetzner") else "  ✗ upload failed")

    step("2. Tailscale (private network: the box has no public ports)")
    if not ok_now("tailscale"):
        say("  Install Tailscale from tailscale.com/download" + (" — the Windows app; WSL uses it." if WSL else "") + " and sign in.")
        pause()
    say("  ✓ signed in" if ok_now("tailscale") else "  ✗ not running yet (box setup will check again next time)")
    if not ok_now("magicdns"):
        say("  Turn on MagicDNS: login.tailscale.com/admin/dns → Enable MagicDNS")
        pause()
    if not ok_now("ts-authkey"):
        say("  Create an auth key for the box: login.tailscale.com/admin/settings/keys →")
        say("  Generate auth key → Reusable ON (the box rejoins after every open), expiry up to 90 days.")
        key = hidden("  Tailscale auth key", r"^tskey-auth-", "tskey-auth-…")
        if key:
            pending["CLI__TS_AUTHKEY"] = key

    step("3. Bitwarden Secrets Manager (all secrets and tokens, never on the box's disk)")
    if ok_now("bws-token"):
        say("  ✓ token works")
    else:
        say("  Sign up / log in at bitwarden.com (US) or bitwarden.eu (EU) — pick one; remember which.")
        say("  Create an organization (free is fine) and enable Secrets Manager (free plan).")
        say("  In Secrets Manager: Projects → New project (e.g. 'dev').")
        say("  Machine accounts → New → name 'agentbox' → Projects tab → add your project, Can read.")
        say("  Then Access tokens → Create access token. Copy it (shown once).")
        region = ask("  Region (us = bitwarden.com, eu = bitwarden.eu)", "us").lower()
        token = hidden("  Machine-account access token", r"^0\.", "0.xxxxxxxx…")
        if token:
            os.makedirs(CONF_DIR, exist_ok=True)
            fd = os.open(BWS_ENV, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(f"BWS_ACCESS_TOKEN={token}\n")
                if region.startswith("eu"):
                    fh.write("BWS_SERVER_URL=https://vault.bitwarden.eu\n")
            ok, detail = c_bws_token()
            say("  ✓ token works" if ok else f"  ✗ {detail}")
    if ok_now("bws-token") and not ok_now("bws-access"):
        say("  The machine account sees no secrets yet. Give it access: Machine accounts → yours →")
        say("  Projects → add your project with Can read. (An empty project also shows nothing.)")

    step("4. GitHub (so agents on the box can clone and push)")
    if ok_now("gh-token"):
        say("  ✓ already in Bitwarden")
    elif yes("  Add a GitHub token now? (recommended)"):
        say("  github.com/settings/personal-access-tokens/new → fine-grained → only the repos you want on the box;")
        say("  permissions: Contents RW, Pull requests RW (Metadata R is automatic); set an expiry.")
        token = hidden("  GitHub token", r"^(github_pat_|ghp_)", "github_pat_…")
        if token:
            pending["CLI__GH_TOKEN"] = token

    step("5. Optional tokens")
    if yes("  Railway API token (railway CLI on the box)?", False):
        t = hidden("  Railway API token")
        if t:
            pending["CLI__RAILWAY_API_TOKEN"] = t
    if yes("  Separate Claude accounts per project (advanced)?", False):
        say("  For each account run `claude setup-token` in another terminal, logged in as that account.")
        while True:
            name = ask("  Account name (e.g. work; empty to finish)")
            if not name:
                break
            t = hidden(f"  Token for {name}")
            if t:
                pending[f"CLI__CLAUDE_TOKEN_{re.sub(r'[^A-Za-z0-9]', '_', name).upper()}"] = t

    import_to_bitwarden(pending)
    pending.clear()

    step("6. Budget and options (your decisions)")
    if not ok_now("budget"):
        subprocess.run([os.path.join(REPO, "setup.sh"), "--budget"])
    agents = "claude codex" if yes("  Also run Codex (OpenAI) on the box?", "codex" in (conf_value("BOX_AGENTS") or "")) else "claude"
    set_conf("BOX_AGENTS", agents)
    set_conf("BOX_PLAYWRIGHT", "1" if yes("  Browser tools for the agents, and a browser you can sign in to from here (Chromium, ~1 GB)?", True) else "0")
    set_conf("BOX_DOCKER", "1" if yes("  Docker on the box?", conf_value("BOX_DOCKER") == "1") else "0")
    set_conf("BOX_AGENT_SYNC", "1" if yes("  Copy your laptop's Claude/Codex rules, skills and plugins to the box?", True) else "0")
    say("  Messages between sessions on different machines go through Claude Code's Remote Control:")
    say("  while a session is connected, its transcript is stored on Anthropic's servers.")
    set_conf("BOX_REMOTE_CONTROL", "1" if yes("  Let box sessions and your other sessions message each other?",
                                              conf_value("BOX_REMOTE_CONTROL") == "1") else "0")
    say("  T3 Code (github.com/pingdotgg/t3code) is a desktop, browser and phone app for the agents;")
    say("  its server on each box uses about 400 MB, and only devices you pair can reach it.")
    t3_before = conf_value("BOX_T3") == "1"
    t3 = yes("  Use T3 Code with the box?", t3_before)
    set_conf("BOX_T3", "1" if t3 else "0")
    if t3 != t3_before and box_state() == "up":
        say(f"  {BOX} is running: `BOX_NAME={BOX} {os.path.join(REPO, 'up.sh')}` applies it now"
            " (closed boxes get it when they open).")
    bypass = yes("  Let the agents act without asking (skip every permission check; otherwise auto mode)?",
                 conf_value("BOX_CLAUDE_PERMISSIONS") == "bypass")
    for key in ("BOX_CLAUDE_PERMISSIONS", "BOX_CODEX_PERMISSIONS"):
        if bypass:
            set_conf(key, "bypass")
        elif conf_value(key) in (None, "", "bypass"):   # another mode chosen by hand stays
            set_conf(key, "auto")
    say("  More boxes: projects are spread over up to N boxes by free memory; 1 keeps a single box.")
    while True:
        n = ask("  How many boxes at most?", conf_value("BOX_MAX_BOXES") or "1")
        if n.isdigit() and int(n) >= 1:
            set_conf("BOX_MAX_BOXES", n)
            break
    if int(n) > 1:
        set_conf("BOX_POLICY", "spread" if not yes("  Pack projects onto as few boxes as possible (cheaper)?", True) else "pack")
        if not ok_now("claude-default-token") and yes("  Add a Claude token so new boxes log in by themselves?", True):
            say("  Run `claude setup-token` in another terminal (logged in to the account to use).")
            t = hidden("  Claude token")
            if t:
                import_to_bitwarden({"CLI__CLAUDE_TOKEN_DEFAULT": t})

    step("Status")
    cmd_doctor(False)
    if box_state() == "none" and all(r["ok"] for r in evaluate(GATE)):
        say()
        if yes("Build the box now? It starts billing (about €0.01/hour for a cx23)", False):
            subprocess.run([os.path.join(REPO, "setup.sh")])
    say()
    say("When the box exists: `box open` opens a tab; log in to Claude there once.")
    if "codex" in (conf_value("BOX_AGENTS") or ""):
        say("For Codex: `box login codex`.")
    say("Recommended: in login.tailscale.com/admin/machines → the box → Disable key expiry.")


def main(argv):
    cmd = argv[0] if argv else "doctor"
    if cmd == "doctor":
        cmd_doctor("--json" in argv)
    elif cmd == "tailnet-host":
        print(tailnet_host(argv[1]))
    elif cmd == "gate":
        cmd_gate()
    elif cmd == "wizard":
        try:
            cmd_wizard()
        except (KeyboardInterrupt, EOFError):
            print("\nStopped. Run `box setup` again any time; it continues where things are missing.")
            sys.exit(130)
    else:
        print(__doc__.strip())


if __name__ == "__main__":
    main(sys.argv[1:])
