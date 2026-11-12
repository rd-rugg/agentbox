#!/usr/bin/env bash
# Show whether the box is up, what it costs, and what's saved while it's down.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

echo "Budget: up to $BOX_MAX_TYPE, $(server_types price "$BOX_MAX_TYPE")"

if hcloud server describe "$NAME" >/dev/null 2>&1; then
  hcloud server describe "$NAME" -o json | python3 -c "$(cat <<'EOF'
import json, sys
s = json.load(sys.stdin)
t = s["server_type"]
loc = s["location"]["name"]
price = next((p for p in t["prices"] if p["location"] == loc), None)
if price:
    hourly = float(price["price_hourly"]["gross"])
    monthly = float(price["price_monthly"]["gross"])
    cost = f"€{hourly:.4f}/h (€{monthly:.2f}/month cap)"
else:
    cost = "price unknown"
since = s["created"][:16].replace("T", " ")
print(f"{s['name']}: {s['status']}, created {since} UTC")
print(f"  {t['name']} ({t['cores']} vCPU, {t['memory']:g} GB RAM, {t['disk']} GB disk) in {loc}")
print(f"  IP {s['public_net']['ipv4']['ip']}, costs {cost} while it exists")
EOF
)"
  state=$(python3 "$BOX_DIR/pool.py" state "$NAME" 2>/dev/null || echo unknown)
  if [ "$state" != up ]; then
    hint=$(box_state_hint "$state" "$NAME")
    echo "  ${hint:-not running}"
  elif bssh -o BatchMode=yes -o ConnectTimeout=5 true 2>/dev/null; then
    # One connection: the sessions, the disk, and (box --peers, on a box that has it) the name
    # each Claude session answers to for messages and whether other machines reach it.
    out=$(bssh 'echo "Sessions: $(tmux list-sessions -F "#{session_name}" 2>/dev/null | paste -sd" " - || true)"
      df -h / | awk "NR==2{print \"Disk used \" \$3 \" of \" \$2}"
      grep -q -- "--peers)" "$(command -v box)" && box --peers' 2>/dev/null || true)
    { grep -E '^(Sessions|Disk used)' <<< "$out" || true; } | sed 's/^/  /'
    if grep -qx $'messaging\t1' <<< "$out"; then
      echo "  Messaging: on (Remote Control), each session answers to its short name BOX/SESSION, then its title"
      { grep -E $'\t(claude|codex)\t' <<< "$out" || true; } | while IFS=$'\t' read -r s _ name state; do
        [ "$name" != - ] || name=""
        # A name without its short name in front (just renamed, or started before agentbox
        # kept names): the box puts it back once the session is idle with an empty prompt.
        if [ -n "$name" ] && [ "$state" != codex ] && [ "$name" != "$NAME/$s" ] && [[ "$name" != "$NAME/$s · "* ]]; then
          name="$name (no short name yet: it gets $NAME/$s on its next rename or start, or now with: box names)"
        fi
        case "$state" in
          remote) echo "    $s: ${name:-unnamed}, reachable from your other machines" ;;
          local) echo "    $s: ${name:-unnamed}, this box only (not connected to Remote Control; started before it was on?)" ;;
          codex) echo "    $s: Codex can't message other sessions" ;;
          *) echo "    $s: no Claude session found (starting, or ended)" ;;
        esac
      done
    elif grep -qx $'messaging\t0' <<< "$out"; then
      echo "  Messaging: this box only (BOX_REMOTE_CONTROL=0 in box.conf)"
    fi
  else
    echo "  (not reachable over Tailscale right now; up.sh can repair that)"
  fi
else
  echo "$NAME: down (not billed)"
fi

snap=$(hcloud image list --type snapshot --selector "$LABEL" -o noheader -o columns=id | sort -n | tail -1)
if [ -n "$snap" ]; then
  hcloud image describe --architecture x86 "$snap" -o json | python3 -c "$(cat <<'EOF'
import json, sys
i = json.load(sys.stdin)
size = i.get("image_size") or 0
when = i["created"][:16].replace("T", " ")
print(f"Snapshot {i['id']} from {when} UTC: {size:.2f} GB stored, ~€{size * 0.0155:.2f}/month,"
      f" restores onto types with at least {i['disk_size']:g} GB disk")
EOF
)"
else
  echo "No snapshot yet."
fi
