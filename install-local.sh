#!/usr/bin/env bash
# Install agentbox's laptop commands into ~/.local/bin: box, secrets, and the
# Bitwarden Secrets Manager CLI (bws) they use. Safe to run again.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
BIN="$HOME/.local/bin"
mkdir -p "$BIN"

ln -sfn "$BOX_DIR/box" "$BIN/box"
ln -sfn "$BOX_DIR/server/secrets" "$BIN/secrets"

if [ "$("$BIN/bws" --version 2>/dev/null)" != "bws $BWS_VERSION" ]; then
  case "$(uname -s)-$(uname -m)" in
    Darwin-*) zip="bws-macos-universal-$BWS_VERSION.zip" ;;
    Linux-x86_64) zip="bws-x86_64-unknown-linux-gnu-$BWS_VERSION.zip" ;;
    Linux-aarch64 | Linux-arm64) zip="bws-aarch64-unknown-linux-gnu-$BWS_VERSION.zip" ;;
    *) echo "No bws build for $(uname -sm); install it yourself: github.com/bitwarden/sdk-sm/releases" >&2; exit 1 ;;
  esac
  base="https://github.com/bitwarden/sdk-sm/releases/download/bws-v$BWS_VERSION"
  tmp=$(mktemp -d)
  curl -fsSL -o "$tmp/$zip" "$base/$zip"
  curl -fsSL -o "$tmp/sums.txt" "$base/bws-sha256-checksums-$BWS_VERSION.txt"
  if command -v sha256sum >/dev/null; then sum=(sha256sum); else sum=(shasum -a 256); fi
  (cd "$tmp" && grep " $zip\$" sums.txt | "${sum[@]}" -c --quiet -)
  python3 -c "import zipfile, sys; zipfile.ZipFile(sys.argv[1]).extract('bws', sys.argv[2])" "$tmp/$zip" "$tmp"
  install -m 755 "$tmp/bws" "$BIN/bws"
  rm -rf "$tmp"
fi

# The same skills for Codex (it reads ~/.agents/skills): Claude-only frontmatter
# dropped and the plugin path replaced by this checkout's path.
python3 - "$BOX_DIR" <<'EOF'
import os, re, sys
repo = sys.argv[1]
target_root = os.path.expanduser("~/.agents/skills")
for skill, target in (("agentbox", "agentbox"), ("setup", "agentbox-setup"), ("onboard", "agentbox-onboard")):
    text = open(os.path.join(repo, "skills", skill, "SKILL.md")).read()
    _, front, body = text.split("---", 2)
    keep = [l for l in front.strip().splitlines() if l.split(":")[0] in ("name", "description")]
    keep = [f"name: {target}" if l.startswith("name:") else l for l in keep]
    for name in ("setup", "onboard"):   # skill names as Codex knows them
        body = body.replace(f"the `{name}` skill", f"the agentbox-{name} skill")
    body = re.sub(r"`/agentbox:(\w+)`", r"the agentbox-\1 skill", body)
    # Claude Code-only asides about its Bash tool's timeout.
    body = re.sub(r" ?\(Claude Code: Bash timeout 600000\)|; Claude Code: Bash timeout 600000", "", body)
    body = re.sub(r" ?In Claude Code that's the Bash `timeout`\s+600000\.", "", body)
    body = body.replace("${CLAUDE_PLUGIN_ROOT}", repo)
    os.makedirs(os.path.join(target_root, target), exist_ok=True)
    with open(os.path.join(target_root, target, "SKILL.md"), "w") as fh:
        fh.write("---\n" + "\n".join(keep) + "\n---" + body)
    extra = os.path.join(repo, "skills", skill, "reference.md")
    if os.path.exists(extra):
        with open(extra) as src, open(os.path.join(target_root, target, "reference.md"), "w") as fh:
            text = src.read().replace("${CLAUDE_PLUGIN_ROOT}", repo)
            for name in ("setup", "onboard"):
                text = text.replace(f"the `{name}` skill", f"the agentbox-{name} skill")
            fh.write(text)
print("Codex skills: agentbox, agentbox-setup, agentbox-onboard in ~/.agents/skills")
EOF

echo "Installed in $BIN: box, secrets, $("$BIN/bws" --version)"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "Add $BIN to your PATH." ;; esac
[ -f "$BWS_ENV" ] || echo "For secrets, put BWS_ACCESS_TOKEN=... in $BWS_ENV (chmod 600)."
