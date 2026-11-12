#!/usr/bin/env bash
# Runs on the box as dev on every setup.sh/up.sh (via lib.sh's sync_config).
# Idempotent: each step skips itself when already done.
#   install.sh   with ./server copied to /tmp/agentbox-sync, settings in the environment:
#   BWS_VERSION, BOX_NAME, BOX_GIT_NAME, BOX_GIT_EMAIL, BOX_DOCKER, BOX_PLAYWRIGHT,
#   PLAYWRIGHT_MCP_VERSION, BOX_AGENTS, BOX_CLAUDE_PERMISSIONS, BOX_CODEX_PERMISSIONS,
#   BOX_MCP_CALLBACK_PORT
#   BOX_BROWSER_VIEW_PORT, BOX_REMOTE_CONTROL, BOX_T3
set -euo pipefail
SRC=/tmp/agentbox-sync
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1
export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"
say() { echo "  box: $*"; }
apt_install() { sudo -E apt-get install -y -qq "$@" >/dev/null; }

# --- Helpers and config from ./server -----------------------------------------
sudo install -m 755 "$SRC/secrets" "$SRC/bin/box" "$SRC/bin/git-credential-box" "$SRC/bin/agentbox-mcp" \
  "$SRC/bin/agentbox-browser" "$SRC/bin/agentbox-browser-relay" "$SRC/bin/agentbox-browser-router" "$SRC/bin/github-token" "$SRC/bin/agentbox-git" "$SRC/bin/agentbox-mise-defaults" "$SRC/bin/agentbox-name" "$SRC/bin/agentbox-clipboard" "$SRC/bin/agentbox-tty" "$SRC/bin/agentbox-agents" "$SRC/bin/agentbox-t3" /usr/local/bin/
# The browser viewer's clipboard shared with the laptop (agentbox-browser adds it to noVNC's page).
sudo install -D -m 644 -t /usr/local/share/agentbox/novnc "$SRC/novnc/agentbox-clipboard.js" "$SRC/novnc/agentbox-clipboard-keys.js"
sudo ln -sfn box /usr/local/bin/cs   # older name, still used by sessions started before the rename
box --permissions   # the permission modes sessions start in, and BOX_REMOTE_CONTROL (box.conf)
# This box's name in the pool: sessions answer to BOX/SESSION for messages (box's peer_name).
[ -z "${BOX_NAME:-}" ] || { mkdir -p ~/.config/agentbox && echo "$BOX_NAME" > ~/.config/agentbox/box-name; }
mkdir -p ~/.local/bin ~/work ~/.config/mise ~/.config/git/hooks
install -m 755 "$SRC/bin/with-cli-token" ~/.local/bin/with-cli-token
for tool in gh railway; do ln -sfn with-cli-token ~/.local/bin/$tool; done
# Claude Code reads a pasted image with xclip: this one asks the laptop tab (agentbox-clipboard).
ln -sfn /usr/local/bin/agentbox-clipboard ~/.local/bin/xclip
install -m 644 "$SRC/tmux.conf" ~/.tmux.conf
install -m 644 "$SRC/mise.toml" ~/.config/mise/config.toml
# What tmux.conf's name hook and tab titles read (messaging on or off, this box's name).
agentbox-name tmux-options > ~/.config/agentbox/tmux-options.conf
tmux source-file ~/.tmux.conf 2>/dev/null || true

# Wrappers in ~/.local/bin must come before mise's shims on PATH. Box-wide variables live in
# ~/.config/agentbox/env.sh, rewritten at the end of every sync (the blocks here are added once).
for rc in ~/.bashrc ~/.profile; do
  grep -q '# agentbox PATH' "$rc" || cat >> "$rc" <<'EOF'
# agentbox PATH
export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"
EOF
  grep -q '# agentbox env' "$rc" || cat >> "$rc" <<'EOF'
# agentbox env
[ -f "$HOME/.config/agentbox/env.sh" ] && . "$HOME/.config/agentbox/env.sh"
EOF
  # agentbox's wrappers that stand in for a command (claude with BOX_T3=1) come first of all,
  # also in a login shell, where ~/.profile puts ~/.local/bin in front again after ~/.bashrc.
  grep -q '# agentbox wrappers' "$rc" || cat >> "$rc" <<'EOF'
# agentbox wrappers
export PATH="$HOME/.local/share/agentbox/bin:$PATH"
EOF
done
box_env=()   # lines for ~/.config/agentbox/env.sh, collected below
box_env_values=()   # the same variables as literal KEY=VALUE, for programs that read no shell (T3 Code)
# A box-wide default: a value the user set wins in shells; T3 Code gets the default as a literal.
add_box_env() {  # NAME VALUE (a literal path, written as $HOME/... in env.sh)
  box_env+=(": \"\${$1:=${2/#"$HOME"/\$HOME}}\"; export $1")
  box_env_values+=("$1=$2")
}

# Claude's first-run screens (welcome, theme picker) would block a session started in
# tmux before anyone sees it: mark them done. The rest of the file is kept.
python3 - "$HOME/.claude.json" <<'PY'
import json, os, sys
path = sys.argv[1]
try:
    data = json.load(open(path))
except FileNotFoundError:
    data = {}
except (OSError, ValueError):   # mid-write by a running Claude, or damaged: leave it alone
    sys.exit(0)
want = {"hasCompletedOnboarding": True}   # the theme defaults to dark
# With BOX_REMOTE_CONTROL=1, Remote Control's one-time "Enable Remote Control?" confirmation
# is taken as answered: the owner turned the setting on, which the README and box setup say
# is that consent. Claude Code records it under this key (not documented: if it's renamed,
# a session shows the dialog in its tab once, and connects after it's accepted there).
if os.environ.get("BOX_REMOTE_CONTROL") == "1":
    want["remoteDialogSeen"] = True
if any(data.get(k) != v for k, v in want.items()):
    data.update(want)
    tmp = path + ".agentbox-tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)
PY

# A skill for the agents working on the box (Claude Code and Codex read these folders).
for d in ~/.claude/skills ~/.agents/skills; do
  mkdir -p "$d/agentbox-box" && install -m 644 "$SRC/skills/agentbox-box/SKILL.md" "$d/agentbox-box/SKILL.md"
done

# `claude` in a plain shell starts inside tmux (shell.sh).
mkdir -p ~/.config/agentbox
# MCP sign-ins (OAuth) call back to this port; every tab forwards it from the laptop.
if [ -n "${BOX_MCP_CALLBACK_PORT:-}" ]; then
  echo "$BOX_MCP_CALLBACK_PORT" > ~/.config/agentbox/mcp-callback-port
fi
# The project browsers' viewers are at localhost:PORT/NAME/ (box browser on the box); every
# tab forwards it too.
if [ -n "${BOX_BROWSER_VIEW_PORT:-}" ]; then
  echo "$BOX_BROWSER_VIEW_PORT" > ~/.config/agentbox/browser-view-port
fi
install -m 644 "$SRC/shell.sh" ~/.config/agentbox/shell.sh
grep -q '# agentbox shell' ~/.bashrc || cat >> ~/.bashrc <<'EOF2'
# agentbox shell
[ -f ~/.config/agentbox/shell.sh ] && . ~/.config/agentbox/shell.sh
EOF2

# Memory. When the kernel kills one process for lack of memory, systemd would stop
# every other process in its unit, and each tmux session is one unit: a dev server
# killed would take the agent next to it down too. Keep the rest running instead.
if [ ! -f /etc/systemd/user.conf.d/agentbox.conf ]; then
  sudo mkdir -p /etc/systemd/user.conf.d
  printf '[Manager]\nDefaultOOMPolicy=continue\n' | sudo tee /etc/systemd/user.conf.d/agentbox.conf >/dev/null
  systemctl --user daemon-reexec 2>/dev/null || true
fi
# Compressed swap in RAM (zram, half the RAM): room before anything gets killed,
# without a swap file on disk (which would end up in every snapshot).
# The cloud kernel keeps the zram module in linux-modules-extra (per kernel version,
# so a kernel update gets its own on the next sync: a box restarted into a new kernel
# runs without swap until then, its zram0 left half set up, hence the reset). The swap
# unit is started explicitly: restarting the setup service doesn't start it. A failure
# here only warns.
if ! swapon --show=NAME --noheadings | grep -q zram; then
  say "setting up compressed swap in RAM (zram)"
  { modinfo zram >/dev/null 2>&1 || apt_install "linux-modules-extra-$(uname -r)"; } \
    && apt_install systemd-zram-generator \
    && printf '[zram0]\nzram-size = ram / 2\ncompression-algorithm = zstd\n' | sudo tee /etc/systemd/zram-generator.conf >/dev/null \
    && echo 'vm.swappiness = 100' | sudo tee /etc/sysctl.d/90-agentbox-zram.conf >/dev/null \
    && sudo sysctl -q -p /etc/sysctl.d/90-agentbox-zram.conf \
    && sudo systemctl daemon-reload \
    && { [ ! -e /sys/block/zram0/reset ] || echo 1 | sudo tee /sys/block/zram0/reset >/dev/null; } \
    && sudo systemctl restart systemd-zram-setup@zram0.service \
    && sudo systemctl start dev-zram0.swap \
    || say "couldn't set up zram swap; the box runs without swap"
fi

# RAM-only folder for the Bitwarden token and file secrets; never in a snapshot.
echo 'd /run/agentbox 0700 dev dev -' | sudo tee /etc/tmpfiles.d/agentbox.conf >/dev/null
sudo systemd-tmpfiles --create /etc/tmpfiles.d/agentbox.conf

# --- Git: GitHub over https with the Bitwarden token, commit guard ------------
git config --global credential.https://github.com.helper box
# Only this value of the key: another one added on the box (ssh://git@github.com/) stays, and
# a plain set would fail with "cannot overwrite multiple values" and end this script.
git config --global --replace-all url."https://github.com/".insteadOf git@github.com: '^git@github\.com:$'
git config --global core.hooksPath ~/.config/git/hooks
git config --global init.defaultBranch main
[ -n "${BOX_GIT_NAME:-}" ] && git config --global user.name "$BOX_GIT_NAME"
[ -n "${BOX_GIT_EMAIL:-}" ] && git config --global user.email "$BOX_GIT_EMAIL"
install -m 755 "$SRC/git-hooks/pre-commit" ~/.config/git/hooks/pre-commit
install -m 755 "$SRC/git-hooks/passthrough" ~/.config/git/hooks/passthrough
for hook in applypatch-msg pre-applypatch post-applypatch pre-merge-commit prepare-commit-msg \
            commit-msg post-commit pre-rebase post-checkout post-merge pre-push post-rewrite \
            pre-auto-gc push-to-checkout; do
  ln -sfn passthrough ~/.config/git/hooks/$hook
done

# --- Bitwarden Secrets Manager CLI, checksum-verified -------------------------
if [ "$(bws --version 2>/dev/null)" != "bws $BWS_VERSION" ]; then
  say "installing bws $BWS_VERSION"
  tmp=$(mktemp -d)
  zip="bws-x86_64-unknown-linux-gnu-$BWS_VERSION.zip"
  base="https://github.com/bitwarden/sdk-sm/releases/download/bws-v$BWS_VERSION"
  curl -fsSL -o "$tmp/$zip" "$base/$zip"
  curl -fsSL -o "$tmp/sums.txt" "$base/bws-sha256-checksums-$BWS_VERSION.txt"
  (cd "$tmp" && grep " $zip\$" sums.txt | sha256sum -c --quiet -)
  python3 -c "import zipfile, sys; zipfile.ZipFile(sys.argv[1]).extract('bws', sys.argv[2])" "$tmp/$zip" "$tmp"
  sudo install -m 755 "$tmp/bws" /usr/local/bin/bws
  rm -rf "$tmp"
fi

# --- GitHub CLI (official apt repo) --------------------------------------------
if [ ! -x /usr/bin/gh ]; then
  say "installing gh"
  sudo mkdir -p -m 755 /etc/apt/keyrings
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
  sudo apt-get update -qq && apt_install gh
fi

# --- Languages: mise (Node/Python per project), uv, corepack, railway ----------
if ! command -v mise >/dev/null; then
  say "installing mise"
  curl -fsSL https://mise.run | MISE_INSTALL_PATH="$HOME/.local/bin/mise" sh >/dev/null 2>&1
fi
if ! command -v uv >/dev/null; then
  say "installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | UV_NO_MODIFY_PATH=1 sh >/dev/null 2>&1
fi
say "installing global Node/Python (first run takes a minute)"
(cd ~ && mise install --yes >/dev/null 2>&1 && mise reshim)

# --- Network: firewall, Tailscale ----------------------------------------------
if ! command -v tailscale >/dev/null; then
  say "installing tailscale"
  curl -fsSL https://tailscale.com/install.sh | sh >/dev/null 2>&1
fi
# Only SSH from the internet; everything (dev servers, databases) is reachable over the tailnet.
if ! sudo ufw status | grep -q 'Status: active'; then
  sudo ufw allow OpenSSH >/dev/null
  sudo ufw allow in on tailscale0 >/dev/null
  sudo ufw default deny incoming >/dev/null
  sudo ufw --force enable >/dev/null
fi
if ! tailscale status >/dev/null 2>&1; then
  if key=$(secrets --get cli TS_AUTHKEY 2>/dev/null); then
    sudo tailscale up --auth-key="$key" --hostname="${BOX_NAME:-agentbox}" >/dev/null
    say "joined the tailnet as ${BOX_NAME:-agentbox}"
  else
    say "tailscale not connected: add CLI__TS_AUTHKEY to Bitwarden to join the tailnet"
  fi
fi

# sshd notices a laptop that went away (sleep, Wi-Fi) within about a minute, so a session's
# forwards (box browser's SOCKS port) are freed for the reconnecting laptop.
if [ ! -f /etc/ssh/sshd_config.d/agentbox-keepalive.conf ]; then
  printf 'ClientAliveInterval 15\nClientAliveCountMax 4\n' | sudo tee /etc/ssh/sshd_config.d/agentbox-keepalive.conf >/dev/null
  sudo systemctl reload ssh 2>/dev/null || true
fi

# --- Optional: Docker, Playwright ------------------------------------------------
if [ "${BOX_DOCKER:-0}" = 1 ] && ! command -v docker >/dev/null; then
  say "installing docker"
  sudo apt-get update -qq && apt_install docker.io docker-compose-v2
  # Published ports bind to localhost unless a compose file says otherwise (Docker bypasses ufw).
  echo '{ "ip": "127.0.0.1" }' | sudo tee /etc/docker/daemon.json >/dev/null
  sudo usermod -aG docker "$USER"
  sudo systemctl restart docker
fi
if [ "${BOX_PLAYWRIGHT:-0}" = 1 ]; then
  mcp="@playwright/mcp@$PLAYWRIGHT_MCP_VERSION"
  if [ ! -d ~/.cache/ms-playwright ]; then
    say "installing Playwright system libraries and Chromium"
    # Under sudo, mise's shims would look for root's config, so use the real node directory.
    node_bin=$(dirname "$(mise which node)")
    sudo env "PATH=$node_bin:$PATH" "$node_bin/npx" -y playwright@latest install-deps chromium >/dev/null 2>&1
    npx -y playwright@latest install chromium >/dev/null 2>&1
  fi
  # The browser you can see from the laptop (box browser NAME): a virtual screen that is
  # also a VNC server, and noVNC to show it as a web page. Nothing runs until it's used.
  if ! command -v Xvnc >/dev/null || ! command -v vncpasswd >/dev/null || ! command -v websockify >/dev/null \
      || ! command -v jwm >/dev/null || [ ! -d /usr/share/novnc ]; then
    say "installing the browser viewer (Xvnc, noVNC, JWM)"
    sudo apt-get update -qq && apt_install tigervnc-standalone-server tigervnc-tools novnc websockify jwm
  fi
  # Ubuntu 24.04 lets only programs with an AppArmor profile create the user namespaces
  # Chromium's sandbox needs; this one covers Playwright's Chromium, as Ubuntu's own does
  # for Chrome, so the visible browser keeps its sandbox.
  if [ "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null)" = 1 ] \
      && [ ! -f /etc/apparmor.d/agentbox-chromium ]; then
    sudo tee /etc/apparmor.d/agentbox-chromium >/dev/null <<'EOF'
abi <abi/4.0>,
include <tunables/global>

profile agentbox-chromium /home/*/.cache/ms-playwright/chromium-*/chrome-linux*/chrome flags=(unconfined) {
  userns,
  include if exists <local/agentbox-chromium>
}
EOF
    sudo apparmor_parser -r /etc/apparmor.d/agentbox-chromium
  fi
  # Browser tools for Claude (navigate, click, fill, screenshot), through agentbox-browser:
  # in a project folder they use that project's browser (the one box browser shows, so
  # sign-ins made there count), elsewhere a headless one. The MCP version is pinned
  # because each release expects one exact Chromium build.
  if ! grep -qF '"/usr/local/bin/agentbox-browser"' ~/.claude.json 2>/dev/null || ! grep -qF "$mcp" ~/.claude.json; then
    say "setting up Playwright browser tools for Claude ($mcp)"
    (cd /tmp && npx -y "$mcp" install-browser chromium >/dev/null 2>&1)
    claude mcp remove --scope user playwright >/dev/null 2>&1 || true
    claude mcp add --scope user playwright -- /usr/local/bin/agentbox-browser mcp "$mcp" >/dev/null
  fi
fi

# --- Optional: Codex next to Claude (BOX_AGENTS contains "codex") ---------------
if [[ " ${BOX_AGENTS:-claude} " == *" codex "* ]]; then
  # Always the newest release (projects needing another version can pin it with
  # mise). The global mise config was just rewritten from server/mise.toml, so
  # register codex again on every sync; this also picks up new releases.
  say "installing/updating codex"
  mise use -g --yes codex@latest >/dev/null 2>&1
  mise reshim
  # ~/.codex/config.toml is composed here on every sync: your laptop's global
  # settings (config.laptop.toml, pushed by lib.sh), your laptop's MCP servers
  # (mcp_sync.py), the servers added on the box itself, the MCP sign-in callback
  # port, trust for ~/work, and the headless browser tools. Change settings on the
  # laptop, not here.
  # agentbox's own servers for Codex (mcp-box.json): the headless browser tools, or none.
  if [ "${BOX_PLAYWRIGHT:-0}" = 1 ]; then
    # agentbox-browser picks the project's browser (box browser) or a headless one.
    printf '{"playwright": {"command": "/usr/local/bin/agentbox-browser", "args": ["mcp", "@playwright/mcp@%s"]}}\n' \
      "$PLAYWRIGHT_MCP_VERSION" > ~/.config/agentbox/mcp-box.json
  else
    rm -f ~/.config/agentbox/mcp-box.json
  fi
  agentbox-mcp codex-compose
  codex login status >/dev/null 2>&1 || say "codex is not logged in yet: run 'box login codex' on your laptop"
fi

# --- Browser for other tools ----------------------------------------------------
# Tools that drive a browser (impeccable, Lighthouse, Puppeteer...) look for `chromium` on PATH or
# the usual variables: both lead to Playwright's Chromium, the build agentbox-browser uses (and
# its AppArmor profile covers). The wrapper follows Chromium updates; a variable you set wins.
if [ "${BOX_PLAYWRIGHT:-0}" = 1 ]; then
  cat > ~/.local/bin/chromium <<'EOF'
#!/bin/sh
# Playwright's Chromium, the newest build on this box (agentbox-browser chromium-path).
c=$(agentbox-browser chromium-path 2>/dev/null) && [ -n "$c" ] && exec "$c" "$@"
echo "chromium: no Playwright Chromium on this box (BOX_PLAYWRIGHT=1 installs it)" >&2
exit 127
EOF
  chmod 755 ~/.local/bin/chromium
  for v in CHROME_PATH PUPPETEER_EXECUTABLE_PATH IMPECCABLE_BROWSER; do
    add_box_env "$v" "$HOME/.local/bin/chromium"
  done
else
  rm -f ~/.local/bin/chromium
fi

# --- Box-wide environment, and mise defaults --------------------------------------
if [ ${#box_env[@]} -gt 0 ]; then
  { echo '# Written by agentbox on every sync; your own settings go in ~/.bashrc.'; printf '%s\n' "${box_env[@]}"; } \
    > ~/.config/agentbox/env.sh
else
  rm -f ~/.config/agentbox/env.sh
fi
# --- T3 Code's server (BOX_T3=1): its agents get the tabs' PATH, variables and Claude sign-in ---
# Installed and repaired here, never updated (T3 Code's own update button keeps app and server in
# step) and never restarted under an agent that is working outside the tabs. The marker records
# that agentbox set it up, so turning the setting off undoes only that.
t3_marker=~/.config/agentbox/t3-installed
if [ "${BOX_T3:-0}" = 1 ]; then
  # T3 Code reads PATH (and finds this wrapper) only when its service starts: a new wrapper
  # counts as a change to its settings.
  wrapper_new=0
  cmp -s "$SRC/bin/agentbox-claude" ~/.local/share/agentbox/bin/claude || wrapper_new=1
  install -D -m 755 "$SRC/bin/agentbox-claude" ~/.local/share/agentbox/bin/claude
  # An opt-in extra never stops a box from opening: a failed install says so, the next sync retries.
  command -v t3 >/dev/null || [ -x ~/.local/bin/t3 ] \
    || { say "installing T3 Code's server"; curl -fsSL --connect-timeout 20 --max-time 300 https://t3.codes/install.sh | sh >/dev/null \
         || say "T3 Code's server didn't install; the next sync tries again"; }
  t3=$(command -v t3 || echo ~/.local/bin/t3)
fi
if [ "${BOX_T3:-0}" = 1 ] && [ -x "$t3" ]; then
  t3_env=("${box_env_values[@]+"${box_env_values[@]}"}")
  [ ! -s ~/.config/agentbox/mcp-callback-port ] || t3_env+=("MCP_OAUTH_CALLBACK_PORT=$(cat ~/.config/agentbox/mcp-callback-port)")
  t3_env+=("T3CODE_LAN=true")
  changed=$(agentbox-t3 env ~/.t3/service.env "${t3_env[@]}")
  [ "$wrapper_new" = 0 ] || changed=changed
  loginctl show-user "$USER" --property=Linger 2>/dev/null | grep -q '=yes' || sudo loginctl enable-linger "$USER"
  if ! systemctl --user is-enabled t3code.service >/dev/null 2>&1 || ! systemctl --user is-active t3code.service >/dev/null 2>&1; then
    "$t3" service install </dev/null >/dev/null || say "T3 Code's service didn't install; see: t3 service status"
    if [ "$(agentbox-t3 env ~/.t3/service.env "${t3_env[@]}")" = changed ]; then   # install wrote its own defaults
      systemctl --user restart t3code.service 2>/dev/null || true
    fi
  elif [ "$changed" = changed ]; then
    if box --agents 2>/dev/null | python3 -c 'import json, sys; sys.exit(0 if any(json.loads(l).get("working") for l in sys.stdin if l.strip()) else 1)'; then
      say "T3 Code's settings changed; they apply at its next restart (agents are working: t3 service restart then)"
    else
      systemctl --user restart t3code.service || say "T3 Code's service didn't restart; see: t3 service status"
    fi
  fi
  touch "$t3_marker"
elif [ "${BOX_T3:-0}" != 1 ]; then
  rm -f ~/.local/share/agentbox/bin/claude
  if [ -f "$t3_marker" ]; then   # agentbox set it up: stop it, keep ~/.t3 (the user's threads)
    systemctl --user disable --now t3code.service >/dev/null 2>&1 || true
    rm -f "$t3_marker"
  fi
fi

# Every tool a project installed with mise resolves everywhere (last: after all installs here).
agentbox-mise-defaults || say "couldn't write mise's defaults; see: agentbox-mise-defaults --check"

rm -rf "$SRC"
