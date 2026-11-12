# shellcheck shell=bash
# Sourced by ~/.bashrc on the box (installed by install.sh on every sync).
# `claude` typed in a plain shell (an ssh login) runs in a tmux session for this
# folder instead, so a dropped connection doesn't end it; tabs reconnect to it.
# Inside tmux, with -p or other flags, or with BOX_NO_TMUX=1 it runs as usual.
claude() {
  if [ -n "${TMUX:-}" ] || [ -n "${BOX_NO_TMUX:-}" ] || [ ! -t 0 ] || [ ! -t 1 ]; then
    command claude "$@"
  else
    box --here "$@"
  fi
}
# MCP sign-ins (/mcp) call back on the port the laptop's tabs forward.
if [ -s ~/.config/agentbox/mcp-callback-port ]; then
  MCP_OAUTH_CALLBACK_PORT=$(cat ~/.config/agentbox/mcp-callback-port)
  export MCP_OAUTH_CALLBACK_PORT
fi
