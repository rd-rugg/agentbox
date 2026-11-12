# shellcheck shell=bash
# macOS ships bash 3.2; these scripts need 4+. Re-run with Homebrew's bash if needed.
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
  for b in /opt/homebrew/bin/bash /usr/local/bin/bash; do
    [ -x "$b" ] && exec "$b" "$0" "$@"
  done
  echo "agentbox needs bash 4 or newer (macOS: brew install bash)." >&2
  exit 1
fi
# Shared settings and helpers for setup.sh, up.sh and down.sh. Sourced, not run.

# The tools agentbox calls (hcloud, tailscale, gh, mise, uv) are found without the user's
# interactive shell setup: agents and runners call box from shells that never read
# ~/.bashrc (Ubuntu's returns early when not interactive, before brew's shellenv). The usual
# install folders go at the end of PATH, so whatever the user's own PATH finds still wins.
for d in "$HOME/.local/bin" /home/linuxbrew/.linuxbrew/bin "$HOME/.linuxbrew/bin" /opt/homebrew/bin \
         /usr/local/bin "$HOME/.local/share/mise/shims"; do
  [ -d "$d" ] || continue
  case ":$PATH:" in *":$d:"*) ;; *) PATH="$PATH:$d" ;; esac
done
export PATH

BOX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Written by `setup.sh --budget`; outside the repo so plugin updates keep it.
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/agentbox"
# The project was called claude-box before; move its settings over once.
if [ ! -e "$CONF_DIR" ] && [ -d "${XDG_CONFIG_HOME:-$HOME/.config}/claude-box" ]; then
  mv "${XDG_CONFIG_HOME:-$HOME/.config}/claude-box" "$CONF_DIR"
fi
BOX_CONF="${BOX_CONF:-$CONF_DIR/box.conf}"
# shellcheck source=/dev/null
[ -f "$BOX_CONF" ] && source "$BOX_CONF"

NAME="${BOX_NAME:-agentbox}"
# Boxes form a pool named after the first box: agentbox, agentbox-2, ... up to
# BOX_MAX_BOXES (default 1). Membership follows from the name; set once, exported.
export BOX_POOL="${BOX_POOL:-$(sed -E 's/-[0-9]+$//' <<< "$NAME")}"
POOL="$BOX_POOL"
# shellcheck disable=SC2034  # used by the scripts that source this file
LABEL="app=$NAME"
export BOX_MAX_BOXES="${BOX_MAX_BOXES:-1}"     # 1 = a single box, exactly as before
export BOX_POLICY="${BOX_POLICY:-pack}"         # pack: fewest boxes; spread: most headroom
export BOX_PROJECT_RAM="${BOX_PROJECT_RAM:-1}"  # GB a project needs, unless its agentbox.json says
export BOX_SETUP_TIMEOUT="${BOX_SETUP_TIMEOUT:-900}"  # seconds a project's agentbox.json "setup" may run on the box
export BOX_SESSION_RAM="${BOX_SESSION_RAM:-0.5}" # GB per live agent session
export BOX_IDLE_WARN_HOURS="${BOX_IDLE_WARN_HOURS:-2}"   # warn when a box has had no agent activity this long
export BOX_AUTO_CLOSE_HOURS="${BOX_AUTO_CLOSE_HOURS:-}"  # set to close idle boxes automatically
export BOX_LOCATIONS="${BOX_LOCATIONS:-fsn1 nbg1 hel1}"   # where boxes may run: the cheapest in stock wins, ties in this order
export BOX_MAX_TYPE="${BOX_MAX_TYPE:-cx23}"   # most expensive type we may create
export BOX_MIN_RAM="${BOX_MIN_RAM:-4}"        # GB
export BOX_TYPE="${BOX_TYPE:-}"               # set to force exactly one type
export BOX_DOCKER="${BOX_DOCKER:-0}"               # 1: install Docker on the box
BOX_PLAYWRIGHT="${BOX_PLAYWRIGHT:-0}"         # 1: install Playwright's Chromium + system libraries
BOX_AGENTS="${BOX_AGENTS:-claude}"            # agents to install: "claude" or "claude codex"
BOX_AGENT_SYNC="${BOX_AGENT_SYNC:-${BOX_CLAUDE_SYNC:-0}}"  # 1: mirror your laptop's Claude/Codex rules, skills, plugins
# Permission mode box sessions start in. Claude: auto, bypass (--dangerously-skip-permissions),
# manual, acceptEdits, plan or dontAsk. Codex: auto (--approve-for-me), bypass
# (--dangerously-bypass-approvals-and-sandbox) or manual (as ~/.codex/config.toml says).
BOX_CLAUDE_PERMISSIONS="${BOX_CLAUDE_PERMISSIONS:-auto}"
BOX_CODEX_PERMISSIONS="${BOX_CODEX_PERMISSIONS:-auto}"
# 1: Claude sessions on the box connect to Remote Control as BOX/SESSION, so they and your
# sessions on other machines can message each other (their transcripts are then stored on
# Anthropic's servers while connected). 0: messages stay on each machine.
BOX_REMOTE_CONTROL="${BOX_REMOTE_CONTROL:-0}"
# 1: Ctrl+V in a box tab pastes an image from the laptop's clipboard, as it does here: while a
# tab is open, the box can read the laptop's clipboard images (never its text). 0: it can't.
BOX_CLIPBOARD_IMAGES="${BOX_CLIPBOARD_IMAGES:-1}"
# 1: every box runs T3 Code's server (github.com/pingdotgg/t3code), so its desktop, browser and
# phone apps can drive the box's agents; agentbox gives those agents the tabs' environment and
# Claude sign-in, and pairs phones over Tailscale (box t3 pair). 0: nothing about T3 Code.
export BOX_T3="${BOX_T3:-0}"   # exported: placement (pool.py) keeps memory for T3 Code's server
# MCP sign-ins (OAuth) on a box call back to this port, which every tab forwards from the
# laptop; agentbox-2 uses the next one, and so on, so each box gets its own.
BOX_MCP_CALLBACK_PORT="${BOX_MCP_CALLBACK_PORT:-18741}"
# The project browsers' viewers, for links from the box. Each box adds N-1 to both ports, so
# the bases stay further apart than the number of boxes.
BOX_BROWSER_VIEW_PORT="${BOX_BROWSER_VIEW_PORT:-18800}"
# box browser: 1 sends the project browser's traffic out through this laptop (--via-laptop),
# 0 not (--no-via-laptop); unset, each project keeps the choice it was last given.
BOX_BROWSER_VIA_LAPTOP="${BOX_BROWSER_VIA_LAPTOP:-}"
# box browser's tunnel: seconds before the first reconnect after a drop, and how long it keeps
# trying while no connection lasts (the box closed or unreachable) before it gives up.
BOX_BROWSER_TUNNEL_RETRY="${BOX_BROWSER_TUNNEL_RETRY:-3}"
BOX_BROWSER_TUNNEL_GIVEUP="${BOX_BROWSER_TUNNEL_GIVEUP:-900}"
# Settings read where they're used, listed here so every setting is in one place:
#   BOX_NAME (agentbox)    the box (and its pool) commands act on; BOX_NAME=other box ... for another pool
#   BOX_TERMINAL (auto)    tabs: wt, iterm, terminal, gnome, konsole, xfce, wezterm, kitty, tmux or print
#   BOX_WT_WINDOW (agentbox)  the Windows Terminal window the tabs go into
#   BOX_YES (0)            1: as --yes on every command (agree to costs and interruptions ahead of time)
#   BOX_ALLOW_GROW (0)     1: open or resize may move a box to a bigger disk without asking (can't shrink back)
#   BOX_SSH_KEY (all)      Hetzner SSH key names or ids to install on a new box
#   BOX_WINDOWS_PROFILE    the Windows profile folder winssh.py writes to, when it can't be found
#   BOX_GIT_NAME, BOX_GIT_EMAIL (the laptop's git identity)  the commit author on the box
BOX_GIT_NAME="${BOX_GIT_NAME:-$(git config --global user.name 2>/dev/null || true)}"
BOX_GIT_EMAIL="${BOX_GIT_EMAIL:-$(git config --global user.email 2>/dev/null || true)}"
SSH_CONFIG="$HOME/.ssh/config"
FIREWALL="$POOL"   # one Hetzner Cloud firewall per pool: no inbound rules except temporary SSH
SERVER_DIR="$BOX_DIR/server"
BWS_ENV="${BWS_ENV:-$(dirname "$BOX_CONF")/bws.env}"   # Bitwarden token, never committed
BWS_VERSION=2.1.0
PLAYWRIGHT_MCP_VERSION=0.0.83   # each release expects one Chromium build; bump deliberately

# This box's port for BASE (the MCP sign-in callback, the browser viewers): BASE, plus N-1
# for <pool>-N, so tabs of several boxes can forward theirs at once.
box_port() {
  if [[ "$NAME" =~ -([0-9]+)$ ]]; then echo $(($1 + BASH_REMATCH[1] - 1))
  else echo "$1"; fi
}
mcp_port() { box_port "$BOX_MCP_CALLBACK_PORT"; }
view_port() { box_port "$BOX_BROWSER_VIEW_PORT"; }

# Something listens on the laptop's 127.0.0.1:PORT. A half-second limit: on some systems
# (WSL among them) connecting to a closed local port hangs instead of being refused.
port_open() {
  python3 -c 'import socket, sys
s = socket.socket(); s.settimeout(0.5)
sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)' "$1" 2>/dev/null
}

# Open a web address in the laptop's own browser: Windows' from WSL, else macOS's or Linux's.
open_url() {
  if grep -qi microsoft /proc/version 2>/dev/null; then
    if command -v wslview >/dev/null; then wslview "$1"
    # Not explorer.exe: it opens a folder window for an address with a query string.
    elif command -v rundll32.exe >/dev/null; then rundll32.exe url.dll,FileProtocolHandler "$1"
    else return 1; fi
  elif [ "$(uname)" = Darwin ]; then
    open "$1"
  elif command -v xdg-open >/dev/null && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    xdg-open "$1" >/dev/null 2>&1 &
  else
    return 1
  fi
}

# The permission settings as a command prefix for the box-side `box`.
permission_env() {
  printf 'BOX_CLAUDE_PERMISSIONS=%q BOX_CODEX_PERMISSIONS=%q BOX_REMOTE_CONTROL=%q ' \
    "$BOX_CLAUDE_PERMISSIONS" "$BOX_CODEX_PERMISSIONS" "$BOX_REMOTE_CONTROL"
}

# Run server_types.py against the live type list.
server_types() {
  hcloud server-type list -o json | python3 "$BOX_DIR/server_types.py" "$@"
}

# --ssh-key flags for server creation: BOX_SSH_KEY (comma-separated names/IDs),
# or every SSH key in the Hetzner project when unset.
ssh_key_args() {
  local keys
  keys="${BOX_SSH_KEY:-$(hcloud ssh-key list -o noheader -o columns=name | paste -sd, -)}"
  if [ -z "$keys" ]; then
    echo "No SSH keys in this Hetzner project. Upload one first, e.g.:" >&2
    echo "  hcloud ssh-key create --name my-laptop --public-key-from-file ~/.ssh/id_ed25519.pub" >&2
    exit 1
  fi
  local k
  IFS=, read -ra list <<< "$keys"
  for k in "${list[@]}"; do printf -- '--ssh-key\n%s\n' "$k"; done
}

# Candidates for image $1 (min disk $2). A snapshot can't shrink, and moving it
# to a bigger disk can't be undone, so bigger disks need BOX_ALLOW_GROW=1.
# The box's projects can need more RAM than BOX_MIN_RAM (agentbox.json "ram_gb").
candidates_for() {
  local ram
  ram=$(python3 "$BOX_DIR/pool.py" min-ram "$NAME" 2>/dev/null) || ram=$BOX_MIN_RAM
  if [ "$1" = ubuntu-24.04 ] || [ "${BOX_ALLOW_GROW:-0}" = 1 ]; then
    BOX_MIN_RAM=${ram:-$BOX_MIN_RAM} server_types candidates "$2"
  else
    BOX_MIN_RAM=${ram:-$BOX_MIN_RAM} server_types candidates "$2" "$2"
  fi
}

# Print what create_server would try for image $1, without creating anything.
plan_server() {
  local image="$1" min_disk candidates type loc disk price
  min_disk=$(hcloud image describe --architecture x86 "$image" -o format='{{.DiskSize}}')
  min_disk="${min_disk%.*}"
  candidates=$(candidates_for "$image" "$min_disk")
  if [ -z "$candidates" ]; then
    echo "Nothing with a $min_disk GB disk in stock up to $BOX_MAX_TYPE in: $BOX_LOCATIONS."
    if [ "${BOX_ALLOW_GROW:-0}" != 1 ] && [ -n "$(server_types candidates "$min_disk")" ]; then
      echo "Types with a bigger disk are available: BOX_ALLOW_GROW=1 allows them (the disk can't shrink back later)."
    fi
    return
  fi
  read -r type loc disk price <<< "$(head -1 <<< "$candidates")"
  echo "Would create $type in $loc at $price (budget: up to $BOX_MAX_TYPE)."
  if [ "$disk" -gt "$min_disk" ]; then
    echo "Disk would grow from $min_disk GB to $disk GB; after that, restores need types with at least $disk GB."
  fi
  if [ "$(wc -l <<< "$candidates")" -gt 1 ]; then
    echo "Fallbacks if that has no capacity:"
    tail -n +2 <<< "$candidates" | awk '{print "  " $1 " in " $2 " at " $4 " " $5}'
  fi
}

# Create server $NAME from image $1: the cheapest in-stock x86 type and location up to
# $BOX_MAX_TYPE whose disk fits the image, at that location's price (equal prices in
# $BOX_LOCATIONS order). Hetzner's stock list is not always right, so a "resource_unavailable"
# failure moves on to the next candidate.
create_server() {
  local image="$1" min_disk key_lines candidates type loc disk price err
  local -a keys
  key_lines=$(ssh_key_args)
  mapfile -t keys <<< "$key_lines"
  min_disk=$(hcloud image describe --architecture x86 "$image" -o format='{{.DiskSize}}')
  candidates=$(candidates_for "$image" "${min_disk%.*}")
  if [ -z "$candidates" ]; then
    echo "Nothing with a ${min_disk%.*} GB disk in stock up to $BOX_MAX_TYPE ($(server_types price "$BOX_MAX_TYPE")) in: $BOX_LOCATIONS." >&2
    echo "Try again later, raise the limit with ./setup.sh --budget, or allow a bigger disk with BOX_ALLOW_GROW=1." >&2
    exit 1
  fi
  ensure_firewall
  # Carry the snapshot's project placement (p.*) over to the new server.
  local -a carry=(--label "$LABEL")
  local l
  for l in $(image_labels "$image" | grep '^p\.' || true); do carry+=(--label "$l"); done
  while read -r type loc disk price; do
    echo "Creating $NAME ($type, $price) in $loc..."
    if err=$(hcloud server create --name "$NAME" --type "$type" --location "$loc" \
        --image "$image" "${keys[@]}" "${carry[@]}" --firewall "$FIREWALL" 2>&1 >/dev/null); then
      if [ "$image" != ubuntu-24.04 ] && [ "$disk" -gt "${min_disk%.*}" ]; then
        echo "  Note: disk grows from ${min_disk%.*} GB to $disk GB. Snapshots can't shrink, so later"
        echo "  ./up.sh runs can only use types with at least $disk GB of disk."
      fi
      return 0
    fi
    if [[ "$err" == *resource_unavailable* ]]; then
      echo "  No capacity after all, trying the next option."
      continue
    fi
    echo "$err" >&2
    exit 1
  done <<< "$candidates"
  echo "Every option up to $BOX_MAX_TYPE was out of capacity. Try again later." >&2
  exit 1
}

# What a box in STATE (pool.py box_state) is doing and what to do about it; empty for
# up and down. Shared by box ls and status.sh.
box_state_hint() {  # STATE BOX
  case "$1" in
    off) echo "switched off, billed while it exists: box open $2 powers it on, box close $2 saves and deletes it" ;;
    closing) echo "box close is saving a snapshot, then deletes the server" ;;
    starting|stopping) echo "billed" ;;
    up|down|none|"") ;;
    *) echo "Hetzner reports it $1; billed" ;;
  esac
}

# --- Reaching the box ----------------------------------------------------------
# Normally over Tailscale: `ssh agentbox` resolves via MagicDNS and Hetzner's
# firewall blocks every inbound port. Only when the box isn't on the tailnet yet
# (first setup, or Tailscale broke) do we open SSH on its public IP, for this
# laptop's IP only, and close it again when the script exits.
BOX_SSH_OPTS=()   # set to (-o HostName=<public ip>) while using the fallback

bssh() { ssh "${BOX_SSH_OPTS[@]}" "$NAME" "$@"; }

# Point the helpers (and child scripts, via BOX_NAME) at another box of the pool.
# Used in a subshell: ( use_box agentbox-2; open_one ).
use_box() {
  export BOX_NAME="$1"
  NAME="$1"
  LABEL="app=$NAME"
  BOX_SSH_OPTS=()
}
bscp() { scp "${BOX_SSH_OPTS[@]}" "$@"; }

# Labels of a server or image as key=value lines.
server_labels() { hcloud server describe "$1" -o json 2>/dev/null | labels_json; }
image_labels() { [[ "$1" =~ ^[0-9]+$ ]] && hcloud image describe "$1" -o json 2>/dev/null | labels_json || true; }
labels_json() { python3 -c 'import json, sys; [print(f"{k}={v}") for k, v in json.load(sys.stdin).get("labels", {}).items()]'; }

ensure_firewall() {
  hcloud firewall describe "$FIREWALL" >/dev/null 2>&1 \
    || hcloud firewall create --name "$FIREWALL" --label "app=$POOL" >/dev/null
  local id
  id=$(hcloud server describe "$NAME" -o format='{{.ID}}' 2>/dev/null) || return 0
  hcloud firewall describe "$FIREWALL" -o json | grep -q "\"id\": $id" \
    || hcloud firewall apply-to-resource "$FIREWALL" --type server --server "$NAME" >/dev/null
}

# Allow SSH to the public IP from this laptop's current IP until the script exits.
# The firewall is shared by the pool, so each box adds and removes only its own rule.
open_public_ssh() {
  local me
  # Already open from earlier in this script (setup.sh opens it, then connect_alias
  # asks again): Hetzner rejects a duplicate rule.
  if [ -n "${SSH_RULE+x}" ]; then
    BOX_SSH_OPTS=(-o "HostName=$(hcloud server ip "$NAME")")
    return 0
  fi
  me=$(curl -4 -fsS -m 10 https://api.ipify.org)
  echo "Opening SSH on the public IP for $me only (closed again when this script ends)..."
  SSH_RULE=(--direction in --protocol tcp --port 22 --source-ips "$me/32" --description "temporary: $NAME")
  hcloud firewall add-rule "$FIREWALL" "${SSH_RULE[@]}" >/dev/null
  # Closed on any exit: an error, Ctrl+C, the terminal closing or a kill (TERM).
  trap close_public_ssh EXIT
  trap 'exit 130' INT TERM HUP
  BOX_SSH_OPTS=(-o "HostName=$(hcloud server ip "$NAME")")
}

# Rules "temporary: <box>" left by an earlier run that was killed outright (setup.sh
# calls it before it starts: another run using the rule right now would lose it).
drop_stale_ssh_rules() {
  local rule
  while IFS=$'\t' read -r -a rule; do
    [ ${#rule[@]} -gt 0 ] || continue
    hcloud firewall delete-rule "$FIREWALL" "${rule[@]}" >/dev/null 2>&1 \
      && echo "Removed a temporary SSH rule an earlier run left open."
  done < <(hcloud firewall describe "$FIREWALL" -o json 2>/dev/null | python3 -c '
import json, sys
name = sys.argv[1]
for r in json.load(sys.stdin).get("rules") or []:
    if r.get("description") == f"temporary: {name}" and r.get("direction") == "in":
        args = ["--direction", "in", "--protocol", r["protocol"], "--description", r["description"]]
        if r.get("port"): args += ["--port", r["port"]]
        for ip in r.get("source_ips") or []: args += ["--source-ips", ip]
        print("\t".join(args))' "$NAME" 2>/dev/null || true)
}

close_public_ssh() {
  [ -n "${SSH_RULE+x}" ] && hcloud firewall delete-rule "$FIREWALL" "${SSH_RULE[@]}" >/dev/null 2>&1 \
    && echo "Temporary public SSH closed."
  unset SSH_RULE
  BOX_SSH_OPTS=()
}

wait_ssh() {  # $1: attempts (3 s apart)
  local _
  for _ in $(seq 1 "$1"); do
    bssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new true 2>/dev/null && return 0
    sleep 3
  done
  return 1
}

# Write the `ssh <box>` alias. HostName is the box's actual tailnet name: when an
# old machine with the same hostname is still registered, Tailscale gives the new
# one a suffix (agentbox-2-1), so it's looked up rather than assumed.
write_alias() {
  local user="$1"
  mkdir -p "$(dirname "$SSH_CONFIG")"
  python3 - "$SSH_CONFIG" "$NAME" "$user" "$(python3 "$BOX_DIR/onboard.py" tailnet-host "$NAME")" <<'EOF'
import re, sys
path, name, user, host = sys.argv[1:]
try:
    text = open(path).read()
except FileNotFoundError:
    text = ""
# ControlMaster: the many short commands to a box share one connection.
block = (f"Host {name}\n    HostName {host}\n    HostKeyAlias {name}\n    User {user}\n    ServerAliveInterval 30\n"
         f"    ControlMaster auto\n    ControlPath ~/.ssh/cm-%C\n    ControlPersist 60\n")
pattern = re.compile(r"^Host %s\n(?:[ \t]+.*\n?)*" % re.escape(name), re.M)
text = pattern.sub(block, text, count=1) if pattern.search(text) else text.rstrip("\n") + "\n\n" + block
open(path, "w").write(text)
EOF
  chmod 600 "$SSH_CONFIG"
}

# Make `ssh <box>` (user $1) go over Tailscale, then connect: over the tailnet if
# the box is on it, otherwise through the temporary public fallback.
#   $2 = bootstrap: skip waiting for the tailnet (fresh server, no Tailscale yet)
connect_alias() {
  local user="$1" mode="${2:-}"
  write_alias "$user"
  # A new instance may have regenerated host keys. Close any shared connection
  # too: reusing it skips the key check, so the new key would never be recorded
  # (and every fresh connection would fail once it expired).
  ssh-keygen -R "$NAME" >/dev/null 2>&1 || true
  ssh -O exit "$NAME" >/dev/null 2>&1 || true

  if [ "$mode" != bootstrap ]; then
    echo "Waiting for $NAME on the tailnet..."
    wait_ssh 30 && return 0
    echo "Not reachable over Tailscale."
  fi
  open_public_ssh
  echo "Waiting for SSH on the public IP..."
  wait_ssh 40 || bssh -o BatchMode=yes true
}

# After sync_config: if we came in through the fallback, check that the tailnet
# works now and close public SSH.
back_to_tailnet() {
  [ "${#BOX_SSH_OPTS[@]}" -gt 0 ] || return 0
  BOX_SSH_OPTS=()
  local _ ok=1
  # The box just joined the tailnet; the laptop may take a moment to see it.
  for _ in $(seq 1 20); do
    write_alias "$(ssh -G "$NAME" | awk '$1 == "user" {print $2}')"
    wait_ssh 1 && { ok=0; break; }
    sleep 2
  done
  if [ "$ok" = 0 ]; then
    close_public_ssh
    trap - EXIT
  else
    echo "Warning: $NAME still isn't reachable over Tailscale. Check that this laptop is on the" >&2
    echo "tailnet and that CLI__TS_AUTHKEY in Bitwarden is valid. Public SSH will now be closed." >&2
  fi
}

# Install ./server onto the box (see server/install.sh), hand over the
# Bitwarden token, and optionally mirror your Claude Code setup.
sync_config() {
  echo "Syncing config from $SERVER_DIR..."
  bssh 'rm -rf /tmp/agentbox-sync'
  bscp -qr "$SERVER_DIR" "$NAME:/tmp/agentbox-sync"
  push_bws_token   # before install.sh, which may need secrets (e.g. the Tailscale key)
  [ "$BOX_AGENT_SYNC" = 1 ] && [[ " $BOX_AGENTS " == *" codex "* ]] && sync_codex_config
  bssh "$(printf 'BWS_VERSION=%q PLAYWRIGHT_MCP_VERSION=%q BOX_NAME=%q BOX_GIT_NAME=%q BOX_GIT_EMAIL=%q BOX_DOCKER=%q BOX_PLAYWRIGHT=%q BOX_AGENTS=%q BOX_MCP_CALLBACK_PORT=%q BOX_BROWSER_VIEW_PORT=%q BOX_T3=%q %sbash /tmp/agentbox-sync/install.sh' \
    "$BWS_VERSION" "$PLAYWRIGHT_MCP_VERSION" "$NAME" "$BOX_GIT_NAME" "$BOX_GIT_EMAIL" "$BOX_DOCKER" "$BOX_PLAYWRIGHT" "$BOX_AGENTS" "$(mcp_port)" "$(view_port)" "$BOX_T3" "$(permission_env)")"
  [ "$BOX_AGENT_SYNC" = 1 ] && sync_claude
  sync_windows_ssh
  return 0
}

# On a WSL laptop with BOX_T3=1, Windows' own SSH (T3 Code's Windows desktop app) reaches box
# $NAME by name: winssh.py keeps a Windows-only key, a Host block and the host key there, and the
# box accepts that key. With BOX_T3=0, what agentbox added for $NAME is removed again.
sync_windows_ssh() {
  local pub conf host user
  grep -qi microsoft /proc/version 2>/dev/null || [ -n "${BOX_WINDOWS_PROFILE:-}" ] || return 0
  if [ "$BOX_T3" = 1 ]; then
    conf=$(ssh -G "$NAME" 2>/dev/null || true)
    host=$(awk '$1 == "hostname" {print $2}' <<< "$conf")
    user=$(awk '$1 == "user" {print $2}' <<< "$conf")
    pub=$(python3 "$BOX_DIR/winssh.py" sync "$NAME" "${host:-$NAME}" "${user:-dev}") \
      || { echo "Windows SSH for $NAME wasn't set up (see above)." >&2; return 0; }
    [ -n "$pub" ] || return 0
    bssh "mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && { grep -qxF $(printf %q "$pub") ~/.ssh/authorized_keys || echo $(printf %q "$pub") >> ~/.ssh/authorized_keys; }"
  else
    pub=$(python3 "$BOX_DIR/winssh.py" remove "$NAME") || return 0
    [ -n "$pub" ] || return 0
    # grep exits 1 when nothing is left (fine) and 2 on an error: then the file stays as it was.
    bssh "f=~/.ssh/authorized_keys; [ ! -f \$f ] || { rc=0; grep -vxF $(printf %q "$pub") \$f > \$f.agentbox || rc=\$?; [ \$rc -gt 1 ] || cat \$f.agentbox > \$f; rm -f \$f.agentbox; }"
  fi
}

# Mirror ~/.claude's CLAUDE.md, rules, skills and commands, and install the same
# user-scope plugins. settings.json is left alone: its hooks and paths are laptop-specific.
sync_claude() {
  echo "Syncing your Claude Code setup..."
  bssh 'mkdir -p ~/.claude'
  # CLAUDE.md, rules, skills, commands (and Codex's) as an exact mirror plus the
  # box-only overlay; edits made on the box are kept and reported (agent_setup.py).
  BOX_NAME="$NAME" python3 "$BOX_DIR/agent_setup.py" sync --agents "$BOX_AGENTS" || true
  python3 - "$HOME/.claude/plugins" <<'EOF' | bssh 'bash -s'
import json, os, shlex, sys
base = sys.argv[1]
markets = json.load(open(os.path.join(base, "known_marketplaces.json")))
installed = json.load(open(os.path.join(base, "installed_plugins.json")))
installed = installed.get("plugins", installed)
print("export PATH=$HOME/.local/bin:$PATH")
print('have=$(claude plugin list 2>/dev/null)')
# A marketplace can be listed under a different name on the box than on the
# laptop (renamed upstream), so find it on the box by its source instead.
print('box_market() { claude plugin marketplace list 2>/dev/null | grep -B1 -F "$1" | head -1 | sed "s/.*❯ //"; }')
sources = {}
for name, market in markets.items():
    if name == "agentbox":
        continue
    src = market.get("source", {})
    ref = src.get("repo") or src.get("url")
    if ref:
        sources[name] = ref
        print(f"[ -n \"$(box_market {shlex.quote(ref)})\" ] || "
              f"claude plugin marketplace add {shlex.quote(ref)} >/dev/null 2>&1")
for plugin, entries in installed.items():
    name, _, market = plugin.partition("@")
    # playwright: the box gets a headless MCP setup instead (install.sh).
    if market == "agentbox" or name == "playwright" or market not in sources \
            or not any(e.get("scope") == "user" for e in entries):
        continue
    print(f'm=$(box_market {shlex.quote(sources[market])}); '
          f'grep -q "{name}@$m" <<< "$have" || '
          f'{{ claude plugin install "{name}@$m" >/dev/null 2>&1 && echo "  claude: installed {name}@$m"; }}')
EOF
}

# Push your laptop's Codex setup: global settings from ~/.codex/config.toml
# without the laptop-path tables [projects.*], [hooks.state.*] and the MCP servers
# [mcp_servers.*] with their callback settings (mcp_sync.py brings the servers without
# credentials; install.sh composes the box's file), plus ~/.codex/rules and your user
# skills in ~/.agents/skills.
sync_codex_config() {
  echo "Syncing your Codex setup..."
  bssh 'mkdir -p ~/.codex ~/.agents'
  if [ -f "$HOME/.codex/config.toml" ]; then
    python3 - "$HOME/.codex/config.toml" <<'EOF' | bssh 'cat > ~/.codex/config.laptop.toml'
import re, sys
keep = True
for line in open(sys.argv[1]):
    header = re.match(r"\s*\[+\s*([^\]]+)", line)
    if header:
        keep = not header.group(1).strip().startswith(("projects", "mcp_servers", "hooks.state"))
    elif re.match(r"\s*mcp_oauth_callback_(port|url)\s*=", line):
        continue
    if keep:
        sys.stdout.write(line)
EOF
  fi
  # AGENTS.md, rules and ~/.agents/skills are mirrored by agent_setup.py (in sync_claude).
  return 0
}

# Copy ~/.config/agentbox/bws.env (Bitwarden machine-account token) into the
# box's RAM. Skipped quietly when the file doesn't exist.
push_bws_token() {
  [ -f "$BWS_ENV" ] || return 0
  bssh 'sudo install -d -m 700 -o "$USER" -g "$USER" /run/agentbox
    umask 077; cat > /run/agentbox/bws.env
    mkdir -p /run/agentbox/aws/sso-cache /run/agentbox/aws/cli-cache' < "$BWS_ENV"   # AWS tokens (box login aws)
  echo "Bitwarden token copied to the box (memory only)."
}
