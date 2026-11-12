#!/usr/bin/env bash
# Snapshot the agentbox server, then delete it so it stops costing money.
# Old snapshots are removed only after the new one exists.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

if ! hcloud server describe "$NAME" >/dev/null 2>&1; then
  echo "No server named $NAME — nothing to do."
  exit 0
fi

# While this runs the box is "closing" (pool.py box_state), so box open doesn't power the
# server on between the steps below; a close that stops early takes the mark off again.
hcloud server add-label --overwrite "$NAME" closing=1 >/dev/null
unmark() { hcloud server describe "$NAME" >/dev/null 2>&1 && hcloud server remove-label "$NAME" closing >/dev/null 2>&1 || true; }
trap unmark EXIT

old_snapshots=$(hcloud image list --type snapshot --selector "$LABEL" -o noheader -o columns=id)

# Remember which agent sessions are open so up.sh can reopen them.
bssh -o BatchMode=yes -o ConnectTimeout=10 box --save || echo "Could not save session list (continuing)."
sessions=$(bssh -o BatchMode=yes 'wc -l < ~/.box-sessions' 2>/dev/null || echo 0)
# The snapshot keeps the box's project placement (p.*) and how many sessions to reopen.
snap_labels=(--label "$LABEL" --label "sessions=${sessions//[^0-9]/}")
for l in $(server_labels "$NAME" | grep '^p\.' || true); do snap_labels+=(--label "$l"); done

echo "Shutting down $NAME cleanly..."
hcloud server shutdown "$NAME" >/dev/null || true
for _ in $(seq 1 30); do
  [ "$(hcloud server describe "$NAME" -o format='{{.Status}}')" = "off" ] && break
  sleep 2
done
if [ "$(hcloud server describe "$NAME" -o format='{{.Status}}')" != "off" ]; then
  echo "Clean shutdown timed out, forcing power off..."
  hcloud server poweroff "$NAME" >/dev/null
fi

echo "Taking snapshot (usually 1-3 minutes)..."
hcloud server create-image --type snapshot \
  --description "$NAME $(date -u +%Y-%m-%dT%H:%MZ)" \
  "${snap_labels[@]}" "$NAME"

new_id=$(hcloud image list --type snapshot --selector "$LABEL" -o noheader -o columns=id \
  | sort -n | tail -1)
if [ -z "$new_id" ]; then
  echo "Snapshot not found after creation — NOT deleting the server." >&2
  exit 1
fi

echo "Deleting server $NAME..."
hcloud server delete "$NAME"
trap - EXIT

for id in $old_snapshots; do
  [ "$id" = "$new_id" ] && continue
  echo "Removing old snapshot $id"
  hcloud image delete "$id" >/dev/null
done

echo "Done. Saved as snapshot $new_id. box open $NAME brings it back."
