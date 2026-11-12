#!/usr/bin/env bash
# One-time setup: build the agentbox server from plain Ubuntu.
# Afterwards log in to Claude on it once, then use ./down.sh and ./up.sh.
#   ./setup.sh               ask for a budget (first time), then build the server
#   ./setup.sh --budget      ask again for the most expensive server type allowed
#   ./setup.sh --budget TYPE set it without asking
#   ./setup.sh --prices      show the price table only
set -euo pipefail
source "$(dirname "$0")/lib.sh"

show_prices() {
  echo "Server types with at least ${BOX_MIN_RAM} GB RAM in $BOX_LOCATIONS (prices incl. VAT):"
  echo
  server_types table
  echo
  echo "Current limit: $BOX_MAX_TYPE. up.sh always takes the cheapest type in stock up to the limit."
}

# Save the most expensive server type the user accepts to $BOX_CONF.
save_budget() {
  local type="$1" price
  if ! price=$(server_types price "$type" 2>/dev/null); then
    echo "'$type' is not a usable server type. See ./setup.sh --prices." >&2
    return 1
  fi
  mkdir -p "$(dirname "$BOX_CONF")"
  # Replace only the BOX_MAX_TYPE line; box.conf holds other settings too.
  { grep -v '^BOX_MAX_TYPE=' "$BOX_CONF" 2>/dev/null || true; echo "BOX_MAX_TYPE=$type"; } > "$BOX_CONF.tmp"
  mv "$BOX_CONF.tmp" "$BOX_CONF"
  export BOX_MAX_TYPE="$type"
  echo "Saved to $BOX_CONF: up to $type, $price."
}

ask_budget() {
  local answer
  show_prices
  while true; do
    read -rp "Most expensive type you're willing to use [$BOX_MAX_TYPE]: " answer
    save_budget "${answer:-$BOX_MAX_TYPE}" && return
  done
}

case "${1:-}" in
  --prices) show_prices; exit 0 ;;
  --budget)
    if [ -n "${2:-}" ]; then save_budget "$2"; else ask_budget; fi
    exit 0 ;;
esac

# A server of ours (app=<box>) that setup never finished, e.g. after a dropped
# connection during the install: carry on with it. Every step below can run twice.
# Only a recent one: boxes set up before setup=done existed have no label either.
resume=0
if hcloud server describe "$NAME" >/dev/null 2>&1; then
  created=$(date -d "$(hcloud server describe "$NAME" -o format='{{.Created}}' 2>/dev/null | cut -d' ' -f1-3)" +%s 2>/dev/null || echo 0)
  if [ "$(hcloud server describe "$NAME" -o format='{{index .Labels "app"}}' 2>/dev/null)" = "$NAME" ] \
      && [ -z "$(hcloud server describe "$NAME" -o format='{{index .Labels "setup"}}' 2>/dev/null)" ] \
      && [ $(( $(date +%s) - created )) -lt 21600 ] \
      && ! hcloud image list --type snapshot --selector "$LABEL" -o noheader -o columns=id | grep -q .; then
    resume=1
    echo "$NAME exists but its setup didn't finish: continuing with it."
  else
    echo "A server named $NAME already exists. Use ./up.sh, or set BOX_NAME=... for another box." >&2
    exit 1
  fi
fi
if [ "$resume" = 0 ] && hcloud image list --type snapshot --selector "$LABEL" -o noheader -o columns=id | grep -q .; then
  echo "A snapshot for $NAME already exists. Use ./up.sh to restore it." >&2
  exit 1
fi

if ! grep -q '^BOX_MAX_TYPE=' "$BOX_CONF" 2>/dev/null; then
  if [ -t 0 ]; then
    ask_budget
    echo
  else
    echo "No budget set yet, so only $BOX_MAX_TYPE is allowed. Set one with: setup.sh --budget TYPE"
  fi
fi

# Everything setup needs (Hetzner, SSH key, Tailscale, Bitwarden, the Tailscale
# auth key, a budget) must be in place before a paid server is created.
python3 "$BOX_DIR/onboard.py" gate
ensure_firewall
drop_stale_ssh_rules

[ "$resume" = 1 ] || create_server ubuntu-24.04
ip=$(hcloud server ip "$NAME")

# A fresh server isn't on the tailnet yet: provision it over the public IP,
# opened for this laptop only until the script ends.
open_public_ssh
ssh-keygen -R "$ip" >/dev/null 2>&1 || true
echo "Waiting for SSH at $ip..."
for _ in $(seq 1 40); do
  ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new "root@$ip" true 2>/dev/null && break
  sleep 3
done

echo "Installing packages and creating user 'dev' (a few minutes)..."
ssh -o BatchMode=yes "root@$ip" 'bash -s' <<'EOF'
set -euo pipefail
cloud-init status --wait >/dev/null 2>&1 || true
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1
# Wait for the lock: an earlier run that lost its connection may still be installing.
apt() { apt-get -o DPkg::Lock::Timeout=600 "$@"; }
apt update -qq
apt upgrade -y -qq >/dev/null
apt install -y -qq tmux git curl ripgrep build-essential unzip >/dev/null

# Claude Code runs as a normal user with passwordless sudo, not as root.
id dev >/dev/null 2>&1 || adduser --disabled-password --gecos "" dev >/dev/null
usermod -aG sudo dev
echo "dev ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/dev
chmod 440 /etc/sudoers.d/dev
install -d -m 700 -o dev -g dev /home/dev/.ssh
install -m 600 -o dev -g dev /root/.ssh/authorized_keys /home/dev/.ssh/authorized_keys

# Key-only SSH.
printf "PasswordAuthentication no\nKbdInteractiveAuthentication no\n" > /etc/ssh/sshd_config.d/99-hardening.conf
systemctl reload ssh 2>/dev/null || true
EOF

connect_alias dev bootstrap

echo "Installing Claude Code..."
bssh 'curl -fsSL https://claude.ai/install.sh | bash >/dev/null
  grep -q ".local/bin" ~/.bashrc || echo "export PATH=\$HOME/.local/bin:\$PATH" >> ~/.bashrc
  ~/.local/bin/claude --version'

sync_config   # also joins the tailnet if CLI__TS_AUTHKEY is in Bitwarden
back_to_tailnet
hcloud server add-label --overwrite "$NAME" setup=done >/dev/null   # setup.sh won't resume it again

cat <<EOF

$NAME is ready at $ip.

Next steps:
  1. box new OWNER/REPO      # clone a repo and start Claude Code in a tab; sign in once via the link it shows
  2. box close $NAME          # saves everything to a snapshot and deletes the server (stops the cost)

From then on: box open in the morning, box close in the evening.
EOF

# Tailscale keeps a deleted box listed as offline; a new box with the same name
# then gets a suffix. agentbox copes, but the old entry should go.
host=$(python3 "$BOX_DIR/onboard.py" tailnet-host "$NAME")
if [ "${host%%.*}" != "$NAME" ]; then
  echo
  echo "Note: an old machine named $NAME is still in your tailnet, so this box joined as ${host%%.*}."
  echo "Remove the offline one: login.tailscale.com/admin/machines → $NAME → Remove."
fi
