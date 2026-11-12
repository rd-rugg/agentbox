#!/usr/bin/env bash
# Open sessions running on the box, one terminal tab (or window) each.
#   ./tabs.sh            every session whose tab wasn't closed (the others are listed)
#   ./tabs.sh api main   only these
#   ./tabs.sh --connect api [BOX]   what a tab runs: attach, reconnecting after a dropped connection
# Terminal: Windows Terminal (WSL), iTerm2 / Terminal (macOS), GNOME Terminal,
# Konsole, Xfce Terminal, WezTerm, kitty (Linux), else tmux windows. Override with
# BOX_TERMINAL=wt|iterm|terminal|gnome|konsole|xfce|wezterm|kitty|tmux|print.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

ports_of() {  # ports declared by the registered project a session belongs to
  local p="${1%@*}"; p="${p%%+*}"; p="${p%%/*}"
  python3 "$BOX_DIR/projects.py" list | cut -f1 | grep -qx "$p" || return 0
  python3 "$BOX_DIR/projects.py" get "$p" ports | tr -d '[],' | tr ' ' '\n' | grep -E '^[0-9]+$' || true
}

# A tab: attach to session $2 over its own connection (long-lived tabs on the
# shared ControlMaster connection would use up the server's per-connection session
# limit). The project's ports (agentbox.json "ports") are forwarded so its dev
# servers, OAuth callbacks and cookies work at localhost:PORT on the laptop.
# The box's MCP sign-in callback port is forwarded too, so /mcp's browser sign-in on
# the laptop reaches the box; only one tab can hold it, and any tab of that box will do.
# With BOX_CLIPBOARD_IMAGES=1 the tab also serves the laptop's clipboard images to the box
# (clipboard.py, through a socket forwarded to /run/agentbox), so Ctrl+V pastes an image
# there; it lives as long as the tab.
# Keepalives notice a dead connection in ~30 s; ssh then fails and the tab
# reconnects, while the session keeps running on the box. Detaching or the agent
# ending exits cleanly (0): the tab is done.
# A tab the user closes (or detaches) is recorded on the box, so `box open` leaves
# that session running in the background instead of opening it again (unless another
# tab here still shows it). `box open` skips sessions already in a tab on this laptop. When several
# of a box's tabs go at once (the terminal or the laptop shutting down), nothing is
# recorded: those sessions come back on the next `box open`.
# A box still on an older `box` lacks these options (it would start a session named
# after the option), so they're only used where it has them.
BOX_HAS_TABS='grep -q -- "--tab-sessions)" "$(command -v box)"'
tab_closed() {
  ssh -o BatchMode=yes -o ConnectTimeout=5 -o ServerAliveInterval=3 -o ServerAliveCountMax=2 "$NAME" \
    "$BOX_HAS_TABS && box --tab-closed $(printf %q "$1")" </dev/null >/dev/null 2>&1 || true
}
# This laptop's other tabs for session $1 on this box (their pids), from the tab processes:
# `box open` doesn't open a second tab for a session that has one, and a tab that goes while
# another still shows its session doesn't record it as closed.
other_tabs() {
  local pid args
  while read -r pid args; do
    [ "$pid" != $$ ] && [[ "$args" == *"tabs.sh --connect $1 $NAME" ]] && echo "$pid"
  done < <(ps -eo pid=,args= 2>/dev/null) || true
}
closed_alone() {  # the tab got SIGHUP: closed on its own, or together with this box's other tabs?
  local dir="${TMPDIR:-/tmp}" pid together=1
  touch "$dir/agentbox-tab-closing.$$"
  sleep 2
  for pid in $(pgrep -f "tabs[.]sh --connect [^ ]+ $NAME\$" || true); do
    [ "$pid" != $$ ] && [ -e "$dir/agentbox-tab-closing.$pid" ] && together=0
  done
  rm -f "$dir/agentbox-tab-closing.$$"
  [ "$together" = 1 ]
}
if [ "${1:-}" = --connect ]; then
  session="${2:?usage: tabs.sh --connect SESSION}" conn=""
  # ssh runs in the background with the tab's input, so that a closed tab's SIGHUP
  # interrupts `wait` at once (a trap waits for a foreground command, and an ssh
  # whose terminal is gone may linger, still attached on the box).
  on_hup() {
    [ -z "$conn" ] || kill "$conn" 2>/dev/null || true
    closed_alone && [ -z "$(other_tabs "$session")" ] && tab_closed "$session"
    exit 0
  }
  trap on_hup HUP
  clip_dir="" clip_pid=""
  if [ "$BOX_CLIPBOARD_IMAGES" = 1 ]; then
    clip_dir=$(mktemp -d "${TMPDIR:-/tmp}/agentbox-clip.XXXXXX")
    python3 "$BOX_DIR/clipboard.py" serve "$clip_dir/socket" $$ </dev/null >/dev/null 2>&1 &
    clip_pid=$!
  fi
  clip_stop() {
    [ -z "$clip_pid" ] || kill "$clip_pid" 2>/dev/null || true
    [ -z "$clip_dir" ] || rm -f "$clip_dir/socket"
    [ -z "$clip_dir" ] || rmdir "$clip_dir" 2>/dev/null || true
  }
  trap clip_stop EXIT
  cmd=(ssh -t -o ControlPath=none -o ExitOnForwardFailure=no -o LogLevel=ERROR
       -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -o ConnectTimeout=10)
  for port in $(ports_of "$session"); do cmd+=(-L "$port:localhost:$port"); done
  # The MCP sign-in callback, and the project browsers' viewers (links from box browser there).
  fixed="$(mcp_port) $(view_port)"
  while :; do
    rc=0
    fwd=()   # checked on every (re)connect: another tab may have let one go
    # A new socket name per connection: the one a dropped connection left is still there.
    [ -z "$clip_dir" ] || fwd+=(-R "$(python3 "$BOX_DIR/clipboard.py" remote "$session" "$$-$RANDOM-$SECONDS"):$clip_dir/socket")
    for port in $fixed; do
      if python3 -c 'import socket, sys; socket.socket().bind(("127.0.0.1", int(sys.argv[1])))' "$port" 2>/dev/null; then
        fwd+=(-L "$port:localhost:$port")
      fi
    done
    printf '\033c'   # reset the terminal: a reconnect otherwise draws over what the last connection left
    "${cmd[@]}" ${fwd[@]+"${fwd[@]}"} "$NAME" box --attach "$session" <&0 &
    conn=$!
    wait "$conn" || rc=$?
    conn=""
    [ "$rc" -ne 0 ] || { [ -n "$(other_tabs "$session")" ] || tab_closed "$session"; exit 0; }
    echo
    echo "Connection to $NAME lost (ssh exit $rc), reconnecting in 5 s (the session is still running). Close the tab to stop."
    sleep 5
  done
fi

if [ $# -gt 0 ]; then
  sessions=("$@")
else
  sessions=() background=()
  while IFS=$'\t' read -r a b; do
    if [ "$a" = background ]; then background+=("$b"); elif [ -n "$a" ]; then sessions+=("$a"); fi
  done < <(bssh -o BatchMode=yes -o ConnectTimeout=10 \
    "if $BOX_HAS_TABS; then box --tab-sessions; else tmux list-sessions -F '#{session_name}'; fi" 2>/dev/null || true)
  if [ "${#background[@]}" -gt 0 ]; then
    echo "Running in the background on $NAME (their tabs were closed): ${background[*]}. Open one with: box attach SESSION"
    [ "${#sessions[@]}" -gt 0 ] || exit 0
  fi
  # Sessions already in a tab on this laptop keep that tab (box attach SESSION opens another).
  if [ "${#sessions[@]}" -gt 0 ]; then
    shown=() fresh=()
    for s in "${sessions[@]}"; do
      if [ -n "$(other_tabs "$s")" ]; then shown+=("$s"); else fresh+=("$s"); fi
    done
    [ "${#shown[@]}" -eq 0 ] || echo "Already in a tab here: ${shown[*]}"
    [ "${#fresh[@]}" -gt 0 ] || exit 0
    sessions=("${fresh[@]}")
  fi
fi
if [ "${#sessions[@]}" -eq 0 ]; then
  echo "No sessions running on $NAME. Start one with: box attach NAME" >&2
  exit 1
fi


# The command a tab runs for session $1: this script with --connect (a plain
# command: Windows Terminal splits its arguments at ";", and wsl.exe mangles
# line breaks, so the reconnect loop can't be spelled out inline).
tab_cmd() { printf '%q ' env "BOX_NAME=$NAME" "$BOX_DIR/tabs.sh" --connect "$1" "$NAME"; }
title_of() { if [[ "$1" == *@codex ]]; then echo "${1%@codex} (codex)"; else echo "$1"; fi; }

applescript_quote() { local s="${1//\\/\\\\}"; printf '"%s"' "${s//\"/\\\"}"; }

kind="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import onboard; print(onboard.detect_terminal())' "$BOX_DIR")"
case "$kind" in
  wt)
    # All agentbox tabs go into one Windows Terminal window named "agentbox"
    # (BOX_WT_WINDOW), reused while open, so later attaches join it. ";" separates tabs.
    args=(-w "${BOX_WT_WINDOW:-agentbox}")
    for s in "${sessions[@]}"; do
      [ ${#args[@]} -gt 2 ] && args+=(";")
      args+=(new-tab --title "$(title_of "$s")" wsl.exe -d "$WSL_DISTRO_NAME" -- bash -lc "$(tab_cmd "$s")")
    done
    wt.exe "${args[@]}"
    ;;
  iterm)
    script='tell application "iTerm"
  activate
  set w to (create window with default profile)'
    first=1
    for s in "${sessions[@]}"; do
      [ $first = 1 ] || script+='
  tell w to create tab with default profile'
      script+="
  tell current session of current tab of w to write text $(applescript_quote "$(tab_cmd "$s")")"
      first=0
    done
    osascript -e "$script
end tell" >/dev/null
    ;;
  terminal)
    for s in "${sessions[@]}"; do
      osascript -e "tell application \"Terminal\" to do script $(applescript_quote "$(tab_cmd "$s")")" >/dev/null
    done
    osascript -e 'tell application "Terminal" to activate' >/dev/null
    ;;
  gnome)
    first=--window
    for s in "${sessions[@]}"; do
      gnome-terminal "$first" --title "$(title_of "$s")" -- bash -lc "$(tab_cmd "$s")"
      first=--tab
    done
    ;;
  konsole)
    for s in "${sessions[@]}"; do
      konsole --new-tab -p "tabtitle=$(title_of "$s")" -e bash -lc "$(tab_cmd "$s")" >/dev/null 2>&1 &
    done
    ;;
  xfce)
    args=()
    for s in "${sessions[@]}"; do args+=(--tab --title "$(title_of "$s")" -x bash -lc "$(tab_cmd "$s")"); done
    xfce4-terminal "${args[@]}" >/dev/null 2>&1 &
    ;;
  wezterm)
    for s in "${sessions[@]}"; do wezterm start -- bash -lc "$(tab_cmd "$s")" >/dev/null 2>&1 & done
    ;;
  kitty)
    for s in "${sessions[@]}"; do kitty --title "$(title_of "$s")" bash -lc "$(tab_cmd "$s")" >/dev/null 2>&1 & done
    ;;
  tmux)
    # Inside tmux: new windows here. Otherwise a local tmux session named after
    # the box. The box runs tmux too: press Ctrl+b twice to reach the box's tmux.
    if [ -n "${TMUX:-}" ]; then
      for s in "${sessions[@]}"; do tmux new-window -n "$(title_of "$s")" "$(tab_cmd "$s")"; done
    else
      local_session="$NAME"
      rest=("${sessions[@]}")
      if ! tmux has-session -t "=$local_session" 2>/dev/null; then
        tmux new-session -d -s "$local_session" -n "$(title_of "${rest[0]}")" "$(tab_cmd "${rest[0]}")"
        rest=("${rest[@]:1}")
      fi
      for s in ${rest[@]+"${rest[@]}"}; do tmux new-window -t "=$local_session:" -n "$(title_of "$s")" "$(tab_cmd "$s")"; done
      if [ -t 1 ]; then exec tmux attach -t "=$local_session"; fi
      echo "Opened in the local tmux session '$local_session': tmux attach -t $local_session"
      exit 0
    fi
    ;;
  *)
    if [ "${BOX_TERMINAL:-}" = print ]; then echo "Open these yourself:"
    else echo "No supported terminal found. Open these yourself (or install tmux):"; fi
    for s in "${sessions[@]}"; do echo "  $(tab_cmd "$s")"; done
    exit 0
    ;;
esac
echo "Opened ${#sessions[@]} tab(s): ${sessions[*]}"
