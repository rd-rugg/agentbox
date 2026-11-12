"""What carried conversations will need on the box that it doesn't have (`box session push`).

Read from the conversations themselves: the commands they ran, the MCP tools they
called, the files they used that aren't in git, and the .env files next to the code. Each gap comes with
its fix, which `--fix` applies: a tool goes into agentbox.json "tools", a .env
whose variables are in Bitwarden into "env_files" (both installed by `box sync`),
and a folder or file is copied (only the ones the conversations used).
Only standard-library Python 3.8+.
"""
import collections
import json
import os
import re
import shlex
import shutil
import subprocess

import mcp_sync
import projects
from projects import BOX, SKIP, git, manifest

# Words that start a shell segment without being a command to look for.
KEYWORDS = {"if", "then", "else", "elif", "fi", "for", "in", "do", "done", "while", "until", "case", "esac",
            "function", "select", "time", "coproc", "return", "local", "export", "declare", "readonly",
            "unset", "source", ".", "set", "shift", "true", "false", "exit", "break", "continue", "eval"}
WRAPPERS = {"sudo", "nohup", "env", "exec", "command", "builtin", "nice", "stdbuf", "xargs", "time", "then", "do", "else", "!"}
SEGMENT = re.compile(r"&&|\|\||[;|&(){}`\n]|\$\(")
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?")
PATH_CHARS = rb"[^\s\"'\\`:,;()<>|&*?\[\]{}$]+"
QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"", re.S)
MCP_TOOL = re.compile(rb'"name":\s*"mcp__([A-Za-z0-9_-]+?)__[A-Za-z0-9_-]+"')
COPY_LIMIT = 100e6   # --fix copies smaller files and folders; bigger ones are left to you
# The laptop's PATH without Windows folders (WSL): lookups there are slow, and a
# Windows program isn't something the box needs.
LAPTOP_PATH = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep) if not p.startswith("/mnt/"))


def commands_in(script):
    """Command names a shell command line runs (first word of each segment)."""
    names, lines, end = [], [], None
    for line in script.splitlines():   # drop heredoc bodies
        if end:
            if line.strip() == end:
                end = None
            continue
        lines.append(line)
        m = HEREDOC.search(line)
        if m:
            end = m.group(1)
    # Quoted text is arguments (python -c code, messages), not commands.
    for seg in SEGMENT.split(QUOTED.sub("''", "\n".join(lines))):
        words = seg.split()
        while words and (words[0] in WRAPPERS or re.fullmatch(r"\w+=\S*", words[0])):
            words.pop(0)
            if words and words[0].isdigit():   # timeout 30 cmd / nice -n 5
                words.pop(0)
        if words and words[0] == "timeout" and len(words) > 2:
            words = words[2:]
        if words and re.fullmatch(r"[A-Za-z][\w.+-]*", words[0]) and words[0] not in KEYWORDS:
            names.append(words[0])
    return names


class Usage:
    """Accumulates, over transcript files: commands run through Bash, and paths
    under the given checkout roots that the conversations mention."""

    def __init__(self, roots):
        self.commands = collections.Counter()
        self.mcp = collections.Counter()   # MCP server (as in tool names mcp__SERVER__tool) -> calls
        self.paths = collections.Counter()   # (root, relative path) -> mentions
        self.patterns = [(r, re.compile(re.escape(r.encode()) + rb"/(" + PATH_CHARS + rb")")) for r in roots]

    def feed(self, data):
        for line in data.splitlines():
            if b'"tool_use"' in line and b'"mcp__' in line:
                self.mcp.update(m.group(1).decode() for m in MCP_TOOL.finditer(line))
            if b'"Bash"' in line and b'"tool_use"' in line:
                try:
                    items = json.loads(line).get("message", {}).get("content", [])
                except (ValueError, AttributeError):
                    items = []
                for c in items if isinstance(items, list) else []:
                    if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Bash":
                        self.commands.update(commands_in(str(c.get("input", {}).get("command", ""))))
        for root, rx in self.patterns:
            for m in rx.finditer(data):
                self.paths[(root, m.group(1).decode(errors="replace").rstrip("."))] += 1


def box_run(script):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", BOX, "bash -s"], input=script, capture_output=True, text=True)
    return r.stdout


def missing_commands(usage):
    """[(command, uses, mise tool or None)] run on the laptop but absent on the box."""
    names = [c for c in usage.commands if shutil.which(c, path=LAPTOP_PATH)]
    if not names:
        return []
    out = box_run('export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"\n'
                  f"for c in {' '.join(map(shlex.quote, names))}; do command -v \"$c\" >/dev/null || echo \"$c\"; done\n"
                  "echo ---; mise registry 2>/dev/null | awk '{print $1}'")
    missing, _, registry = out.partition("---")
    registry = set(registry.split())
    gaps = []
    for c in missing.split():
        tool = projects.CLI_TOOLS.get(c) or (c if c in registry else None)
        gaps.append((c, usage.commands[c], tool))
    return sorted(gaps, key=lambda g: -g[1])


def tool_key(name):
    """A server name as it appears in tool names (Claude Code keeps [A-Za-z0-9_-])."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


BOX_MCP = r"""
import json, os, sys
names = set()
try:
    d = json.load(open(os.path.expanduser("~/.claude.json")))
except (OSError, ValueError):
    d = {}
names |= set(d.get("mcpServers") or {})                      # user scope
for folder in sys.argv[1:]:
    names |= set(((d.get("projects") or {}).get(folder) or {}).get("mcpServers") or {})   # local scope
    try:
        names |= set(json.load(open(os.path.join(folder, ".mcp.json"))).get("mcpServers") or {})  # the repo's
    except (OSError, ValueError):
        pass
print("\n".join(names))
"""


def missing_mcp(usage, places=()):
    """[(server, calls, fix)] for MCP servers the conversations called that the box won't have.
    claude.ai connectors come with the account and plugins with BOX_AGENT_SYNC, so only
    servers added with `claude mcp add` or a repo's .mcp.json are checked: the box's user
    scope, its local scope for the carried folders, and .mcp.json in those folders."""
    used = {s: n for s, n in usage.mcp.items() if not s.startswith(("claude_ai_", "plugin_"))}
    if not used:
        return []
    folders = " ".join(shlex.quote(p.dst) for p in places)
    out = box_run(f"python3 - {folders} <<'EOF'\n{BOX_MCP}\nEOF\n")
    on_box = {tool_key(n) for n in out.split()}
    p = mcp_sync.plan("claude")
    skipped = {tool_key(n): why for a, n, why in p["skipped"] if a == "claude"}
    user = {tool_key(n) for n in p["claude"]}
    local = {tool_key(n) for n in mcp_sync.local_scope_servers()}
    gaps = []
    for s, n in sorted(used.items(), key=lambda x: -x[1]):
        if s in on_box:
            continue
        if s in skipped:
            fix = f"stays on the laptop: {skipped[s]}"
        elif s in user:
            fix = ("box sync brings it when BOX_AGENT_SYNC=1 (your personal setup on the box); "
                   "then sign in once with /mcp in a box session")
        elif s in local:
            fix = "added for one laptop folder only: re-add it with `claude mcp add --scope user` and run box sync"
        else:
            fix = "not on the box: a claude.ai connector (claude.ai → Settings → Connectors) works everywhere"
        gaps.append((s, n, fix))
    return gaps


def untracked(root):
    """Top-level-ish paths under root that git doesn't track (ignored or new)."""
    out = set()
    for extra in (["--ignored"], []):
        out |= {p.rstrip("/") for p in (git(root, "ls-files", "--others", "--exclude-standard", "--directory", *extra) or "").splitlines()}
    return out


def is_env_file(name):
    return bool(projects.ENV_FILE.fullmatch(name)) and not name.endswith(projects.ENV_TEMPLATE)


def mentioned_env_files(usage):
    """{(root, rel)} of .env files the conversations used, at any depth (never copied: secrets)."""
    return {(root, rel) for (root, rel) in usage.paths
            if is_env_file(os.path.basename(rel)) and os.path.isfile(os.path.join(root, rel))}


def missing_files(usage, places):
    """[(src path, dst path, uses, bytes or None)] used by the conversations, not in git, absent
    on the box. places: every checkout (a carried conversation may use any of them). A symlink
    stands for its target when that lies in a checkout; bytes is None for one pointing elsewhere
    (not copied: the target could be anything)."""
    dst_of = {p.src: p.dst for p in places}
    wt_roots = [p.src for p in places]
    counts = collections.Counter()
    for (root, rel), n in usage.paths.items():
        parts = rel.split("/")
        if set(parts) & SKIP or parts[0] in (".git", ".claude") or any(is_env_file(x) for x in parts):
            continue
        two = os.path.join(root, *parts[:2])
        cand = "/".join(parts[:2]) if len(parts) > 1 and os.path.isdir(two) and not os.path.islink(two) \
            and not os.path.islink(os.path.join(root, parts[0])) else parts[0]
        full = os.path.join(root, cand)
        if os.path.lexists(full):
            counts[(root, cand)] += n
    found, cache = {}, {}
    for (root, cand), n in counts.items():
        if root not in cache:
            cache[root] = untracked(root)
        if not any(cand == u or cand.startswith(u + "/") for u in cache[root]):
            continue
        src = os.path.join(root, cand)
        real = os.path.realpath(src)
        owner = max((r for r in wt_roots if real == r or real.startswith(r + "/")), key=len, default=None)
        outside = os.path.islink(src) and not owner
        if os.path.islink(src) and owner:
            src, root = real, owner   # count it as its target
        dst = os.path.join(dst_of[root], os.path.relpath(src, root))
        found[src] = (src, dst, found.get(src, (0, 0, 0, 0))[2] + n, outside)
    if not found:
        return []
    absent = set(box_run("".join(f"[ -e {shlex.quote(d)} ] || echo {shlex.quote(d)}\n" for _, d, _, _ in found.values())).splitlines())
    return sorted([(s, d, n, None if out else projects.du_bytes(s)) for s, d, n, out in found.values() if d in absent],
                  key=lambda g: -g[2])


def missing_env_files(usage, needed, places):
    """[(src, dst, app or None, missing names)] for .env files the box lacks: the ones next to
    the carried checkouts' code, and any the conversations used."""
    dst_of = {p.src: p.dst for p in places}
    cands = [(os.path.join(p.src, f), os.path.join(p.dst, f)) for p in needed for f in projects.env_files_in(p.src)]
    cands += [(os.path.join(root, rel), os.path.join(dst_of[root], rel)) for root, rel in mentioned_env_files(usage)]
    cands = list(dict.fromkeys(cands))
    if not cands:
        return []
    absent = set(box_run("".join(f"[ -e {shlex.quote(d)} ] || echo {shlex.quote(d)}\n" for _, d in cands)).splitlines())
    gaps = []
    for src, dst in cands:
        if dst in absent:
            app, missing = projects.bitwarden_match(src)
            gaps.append((src, dst, app, missing))
    return gaps


def report(name, lroot, usage, needed, places):
    """Print what the box lacks for these conversations (carrying the checkouts in needed;
    places is every checkout); return the fixes to apply."""
    cmds, files, envs = missing_commands(usage), missing_files(usage, places), missing_env_files(usage, needed, places)
    mcps = missing_mcp(usage, places)
    fixes = {"tools": [], "files": [], "env_files": []}
    lines = []
    for c, n, tool in cmds:
        if tool:
            lines.append(f"command {c} (run {n}x): add \"{tool}\" to tools in agentbox.json")
            fixes["tools"].append(tool)
        else:
            lines.append(f"command {c} (run {n}x): not in mise's registry; install it on the box yourself")
    for s, n, fix in mcps:
        lines.append(f"MCP server {s} (called {n}x): {fix}")
    for src, dst, n, nbytes in files:
        rel = os.path.relpath(src, lroot)
        if nbytes is None:
            lines.append(f"{rel} (used {n}x): a symlink to {os.path.realpath(src)}, outside the project; "
                         "copy what the work needs yourself")
        elif nbytes <= COPY_LIMIT:
            lines.append(f"{rel} ({nbytes / 1e6:.1f} MB, used {n}x): not in git; copy it")
            fixes["files"].append((src, dst))
        else:
            lines.append(f"{rel} ({nbytes / 1e6:.0f} MB, used {n}x): not in git; big, so --fix leaves it: "
                         f"rsync -a {shlex.quote(src)}/ {BOX}:{shlex.quote(dst)}/ if the work needs it")
    for src, dst, app, missing in envs:
        rel = os.path.relpath(src, lroot)
        if app and not missing:
            lines.append(f"{rel}: all its variables are in Bitwarden app {app}; add it to env_files")
            if not rel.startswith(".."):
                fixes["env_files"].append({"path": rel, "secrets": app})
        else:
            lines.append(f"{rel}: {len(missing)} variable(s) aren't in Bitwarden ({', '.join(sorted(missing))}); "
                         "if the work on the box needs them, add them as APP__NAME and run this again")
    note = manifest(lroot).get("laptop_only")
    if note:
        lines.append(f"laptop only: {note}")
    if lines:
        print(f"The box lacks what these conversations used:")
        for l in lines:
            print(f"  - {l}")
    return fixes if any(fixes.values()) else None


def apply(name, lroot, fixes):
    """Add tools and env files to agentbox.json, install them (box sync), copy the files."""
    m = manifest(lroot)
    if fixes["tools"] or fixes["env_files"]:
        have = {t.split("@")[0] for t in m.get("tools", [])}
        m["tools"] = m.get("tools", []) + [t for t in dict.fromkeys(fixes["tools"]) if t not in have]
        paths = {e["path"] for e in m.get("env_files", [])}
        m["env_files"] = m.get("env_files", []) + [e for e in fixes["env_files"] if e["path"] not in paths]
        with open(os.path.join(lroot, projects.MANIFEST), "w") as fh:
            json.dump(m, fh, indent=2)
            fh.write("\n")
        projects.cmd_sync(name)
    for src, dst in fixes["files"]:
        print(f"Copying {src} -> {BOX}:{dst}")
        projects.ensure_dir(f"{BOX}:{dst}" + ("/" if os.path.isdir(src) else ""))
        subprocess.run(["rsync", "-a", src + ("/" if os.path.isdir(src) else ""), f"{BOX}:{dst}"], check=True)
