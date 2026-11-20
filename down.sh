#!/usr/bin/env bash
# Snapshot the agentbox server, then delete it so it stops costing money.
# Old snapshots are removed only after the new one exists.
#
# In two parts. This one saves the sessions and switches the server off. The rest (the
# snapshot, the delete, the old snapshots: minutes of waiting) runs as `down.sh --finish`
# in a process of its own (lib.sh detach), whose output this one follows: when the
# terminal or the agent session that ran box close ends, the close still finishes,
# instead of leaving a switched-off server that bills on. The server carries
# closing=<when the close began> until it's deleted; a close that stops anyway (the
# laptop went down) shows as half-closed (pool.py close_stopped), and the next box close
# finishes it, with the snapshot it already made.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

# The snapshot this close made: one of this server, taken after the close began. Prints
# its id, "creating" while Hetzner still makes it, or nothing.
made_snapshot() {  # SERVER_ID BEGAN
  hcloud image list --type snapshot -o json | python3 -c '
import json, sys
from datetime import datetime
server, began = int(sys.argv[1]), int(sys.argv[2])
def taken(i):
    try:
        return datetime.fromisoformat(i["created"].replace("Z", "+00:00")).timestamp()
    except (KeyError, TypeError, ValueError, AttributeError):
        return 0.0
mine = [i for i in json.load(sys.stdin) if (i.get("created_from") or {}).get("id") == server]
done = [i["id"] for i in mine if i.get("status") == "available" and taken(i) >= began]
if any(i.get("status") == "creating" for i in mine):
    print("creating")
elif done:
    print(max(done))' "$1" "$2"
}

label_of() { server_labels "$NAME" | sed -n "s/^$1=//p"; }

finish() {
  local server began id old
  server=$(hcloud server describe "$NAME" -o format='{{.ID}}')
  began=$(label_of closing)
  [[ "$began" =~ ^[0-9]+$ ]] || began=0
  id=$(made_snapshot "$server" "$began")
  while [ "$id" = creating ]; do
    echo "Waiting for the snapshot Hetzner is making..."
    sleep 15
    id=$(made_snapshot "$server" "$began")
  done
  if [ -n "$id" ]; then
    echo "Using the snapshot this close already made ($id)."
  else
    # The snapshot keeps the box's project placement (p.*) and how many sessions to reopen.
    local -a snap_labels=(--label "$LABEL" --label "sessions=$(label_of sessions | grep . || echo 0)")
    for l in $(server_labels "$NAME" | grep '^p\.' || true); do snap_labels+=(--label "$l"); done
    echo "Taking snapshot (usually 1-3 minutes)..."
    hcloud server create-image --type snapshot \
      --description "$NAME $(date -u +%Y-%m-%dT%H:%MZ)" \
      "${snap_labels[@]}" "$NAME" >/dev/null
    id=$(made_snapshot "$server" "$began")
  fi
  if ! [[ "$id" =~ ^[0-9]+$ ]]; then
    echo "Snapshot not found after creation — NOT deleting the server." >&2
    exit 1
  fi

  echo "Deleting server $NAME..."
  hcloud server delete "$NAME" >/dev/null
  for old in $(hcloud image list --type snapshot --selector "$LABEL" -o noheader -o columns=id); do
    [ "$old" -lt "$id" ] || continue
    echo "Removing old snapshot $old"
    hcloud image delete "$old" >/dev/null
  done
  echo "Done. Saved as snapshot $id. box open $NAME brings it back."
}

if [ "${1:-}" = --finish ]; then   # STATE_FILE: down.sh writes "pid N", then "rc N" when done
  state="$2"
  echo "pid $$" > "$state"
  trap 'echo "rc $?" >> "$state"' EXIT
  finish
  exit 0
fi

if ! hcloud server describe "$NAME" >/dev/null 2>&1; then
  echo "No server named $NAME — nothing to do."
  exit 0
fi

status=$(hcloud server describe "$NAME" -o format='{{.Status}}')
began=$(label_of closing)
if [ "$status" = off ] && [[ "$began" =~ ^[0-9]{10,}$ ]]; then
  echo "Finishing the close of $NAME that stopped before deleting the server..."
else
  # While this runs the box is "closing" (pool.py box_state), so box open doesn't power the
  # server on between the steps below. A close that stops before the server is off takes the
  # mark off again; after that it stays, and the next box close finishes the close.
  hcloud server add-label --overwrite "$NAME" "closing=$(date +%s)" >/dev/null
  unmark() { hcloud server describe "$NAME" >/dev/null 2>&1 && hcloud server remove-label "$NAME" closing >/dev/null 2>&1 || true; }
  trap unmark EXIT

  if [ "$status" != off ]; then
    # Remember which agent sessions are open so up.sh can reopen them (on the server until
    # the snapshot takes it over).
    bssh -o BatchMode=yes -o ConnectTimeout=10 box --save || echo "Could not save session list (continuing)."
    sessions=$(bssh -o BatchMode=yes 'wc -l < ~/.box-sessions' 2>/dev/null || echo 0)
    hcloud server add-label --overwrite "$NAME" "sessions=${sessions//[^0-9]/}" >/dev/null
  fi

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
  trap - EXIT
fi

# The rest in its own process; follow its output, and end with its exit code.
dir="${XDG_CACHE_HOME:-$HOME/.cache}/agentbox/close"
mkdir -p "$dir"
log="$dir/$NAME.log" state="$dir/$NAME.state"
: > "$log"
rm -f "$state"
detach "$0" --finish "$state" </dev/null >>"$log" 2>&1
shown=0
while :; do
  rc=$(sed -n 's/^rc //p' "$state" 2>/dev/null || true)   # before the log: all of it is written then
  size=$(wc -c < "$log")
  if [ "$((size))" -gt "$shown" ]; then
    tail -c +"$((shown + 1))" "$log" | head -c "$((size - shown))"
    shown=$((size))
  fi
  [ -z "$rc" ] || exit "$rc"
  pid=$(sed -n 's/^pid //p' "$state" 2>/dev/null || true)
  if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null && [ -z "$(sed -n 's/^rc //p' "$state")" ]; then
    echo "The close of $NAME stopped before it finished; box close $NAME finishes it." >&2
    exit 1
  fi
  sleep 1
done
