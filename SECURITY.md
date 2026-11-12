# Security

agentbox handles cloud credentials and secrets, so security reports are welcome.

**Please don't open a public issue for a vulnerability.** Report it privately
through GitHub's "Report a vulnerability" (Security tab → Advisories), with what
you found, how to reproduce it and what it could expose. You'll get an answer
within a few days. Fixes go into the latest release only.

What agentbox is designed to guarantee, so you know what counts as a bug:
- Secret values taken from Bitwarden are never printed and never written to the box's
  disk (only to RAM under `/run/agentbox`). Every repository on the box refuses commits
  that contain them or a `.env` file.
- The Hetzner firewall has no open inbound ports; the box is reached over Tailscale. A
  temporary SSH rule for the laptop's own IP is opened only while a setup or repair
  script runs, and removed when it ends.
- Tokens typed during setup are read hidden and go straight into Bitwarden or
  `~/.config/agentbox/bws.env` (mode 600), never through an agent's conversation.

Not covered, by design: sign-ins made on the box (Claude, Codex, MCP servers, the project
browser with `--persist`) and anything typed into T3 Code are stored on the box's disk
and so in its snapshots. With `BOX_T3=1`, T3 Code's server listens on the box's tailnet
address; only paired devices can use it.
