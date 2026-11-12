#!/usr/bin/env bash
# Recreate the agentbox server from its latest snapshot, point
# `ssh agentbox` at it over Tailscale and reopen the saved agent sessions.
#   ./up.sh         do it
#   ./up.sh --plan  only show which type, location and price it would pick
set -euo pipefail
source "$(dirname "$0")/lib.sh"

if [ "${1:-}" = --plan ]; then
  if hcloud server describe "$NAME" >/dev/null 2>&1; then
    echo "$NAME is already running; up.sh would only reconnect and resync config."
    exit 0
  fi
  snapshot=$(hcloud image list --type snapshot --selector "$LABEL" -o noheader -o columns=id \
    | sort -n | tail -1)
  [ -n "$snapshot" ] || { echo "No snapshot labelled $LABEL found. Run setup.sh first."; exit 1; }
  plan_server "$snapshot"
  exit 0
fi

created=0
if hcloud server describe "$NAME" >/dev/null 2>&1; then
  echo "$NAME is already running at $(hcloud server ip "$NAME")."
else
  snapshot=$(hcloud image list --type snapshot --selector "$LABEL" -o noheader -o columns=id \
    | sort -n | tail -1)
  if [ -z "$snapshot" ]; then
    echo "No snapshot labelled $LABEL found. Run ./setup.sh first." >&2
    exit 1
  fi
  create_server "$snapshot"
  created=1
fi

ensure_firewall
connect_alias dev
sync_config
back_to_tailnet

if [ "$created" = 1 ]; then
  bssh box --restore
fi

echo "Ready. Connect with:  ssh -t $NAME box   (or ./tabs.sh for one tab per session)"
