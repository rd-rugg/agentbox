#!/usr/bin/env python3
"""Keep the personal agent setup on the box in step with the laptop (`box sync`, attach, open).

  agent_setup.py sync [--agents "claude codex"] [--pull | --push] [--quiet]

What it manages (the global layer; projects bring their own setup through git):
  Claude: ~/.claude/CLAUDE.md, rules/, skills/, commands/
  Codex:  ~/.codex/AGENTS.md, rules/, ~/.agents/skills/
  both:   your MCP servers, without their credentials (mcp_sync.py)
The box gets a copy of the laptop's (deletions too, of what was pushed; ~/.claude/skills/synced,
which Claude Code syncs from your claude.ai account on each machine, is left to it),
plus a box-only overlay
from ~/.config/agentbox/box/{claude,codex,agents}/... (a CLAUDE.md or AGENTS.md there
is appended, not replacing). agentbox's own laptop skills and the box's agentbox-box
skill are left alone.

Nothing made on the box is lost: a manifest on the box records what was last pushed,
and a sync deletes only files it pushed that the box still has as pushed. Anything
else there (a file changed or added, a folder, a symlink, even one to a folder
outside these trees) is kept and reported. --pull brings changed files to the laptop
(or the overlay they came from); --push overwrites them with the laptop's and deletes
pushed files the laptop no longer has, but still keeps what the box added on its own.
Only standard-library Python 3.8+.
"""
import hashlib
import inspect
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

import mcp_sync
import projects
from projects import BOX

HOME = os.path.expanduser("~")
OVERLAY = os.path.join(projects.CONF, "box")
MANIFEST = ".config/agentbox/setup-manifest.json"   # on the box, relative to its home
MARK = "\n\n<!-- agentbox: box-only additions below (from ~/.config/agentbox/box) -->\n\n"
# (path relative to home on both sides, overlay folder, agent)
ROOTS = [(".claude/CLAUDE.md", "claude/CLAUDE.md", "claude"), (".claude/rules", "claude/rules", "claude"),
         (".claude/skills", "claude/skills", "claude"), (".claude/commands", "claude/commands", "claude"),
         (".codex/AGENTS.md", "codex/AGENTS.md", "codex"), (".codex/rules", "codex/rules", "codex"),
         (".agents/skills", "agents/skills", "codex")]
NEVER_PUSHED = {".agents/skills/agentbox", ".agents/skills/agentbox-setup", ".agents/skills/agentbox-onboard"}
# Never touched on the box: agentbox's box skill (server/install.sh) and the skills Claude
# Code itself syncs from your claude.ai account, which it does on each machine.
BOX_ONLY = {".claude/skills/agentbox-box", ".agents/skills/agentbox-box", ".claude/skills/synced"}


def roots_for(agents):
    return [r for r in ROOTS if r[2] in agents.split()]


def under(path, prefixes):
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def stage(agents, home=HOME, overlay=OVERLAY):
    """Build the tree the box should have, in a temp dir. Returns (dir, {path: origin}),
    origin being the laptop or overlay file each staged file came from (None: merged)."""
    out = tempfile.mkdtemp(prefix="agentbox-setup-")
    origin = {}

    def copy_file(src, rel):
        dst = os.path.join(out, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)   # keeps the timestamp, so rsync skips unchanged files
        origin[rel] = src

    def copy_tree(src, rel):
        def staged_path(path):
            return os.path.normpath(os.path.join(rel, os.path.relpath(path, src)))
        for d, dirs, files in os.walk(src, followlinks=True):
            dirs[:] = [x for x in dirs if not under(staged_path(os.path.join(d, x)), NEVER_PUSHED | BOX_ONLY)]
            for f in files:
                path = os.path.join(d, f)
                r = staged_path(path)
                if os.path.isfile(path) and not under(r, NEVER_PUSHED | BOX_ONLY):
                    copy_file(path, r)

    for rel, over, _ in roots_for(agents):
        src, ov = os.path.join(home, rel), os.path.join(overlay, over)
        if rel.endswith(".md"):
            text = open(src).read() if os.path.isfile(src) else ""
            extra = open(ov).read() if os.path.isfile(ov) else ""
            if os.path.isfile(src) or extra:   # an empty file is still a file
                os.makedirs(os.path.dirname(os.path.join(out, rel)), exist_ok=True)
                with open(os.path.join(out, rel), "w") as fh:
                    fh.write(text + (MARK + extra if extra else ""))
                stamp = max(os.path.getmtime(p) for p in (src, ov) if os.path.isfile(p))
                os.utime(os.path.join(out, rel), (stamp, stamp))   # unchanged sources: rsync skips it
                origin[rel] = None if extra else src
        else:
            os.makedirs(os.path.join(out, rel), exist_ok=True)   # an empty root clears the box's copy
            if os.path.isdir(src):
                copy_tree(src, rel)
            if os.path.isdir(ov):
                copy_tree(ov, rel)
    return out, origin


DIR = "dir"   # an empty folder, in entries()


def entries(base, rels, skip=()):
    """What is under the roots `rels` of `base`: {path: sha256 of a file, "link:<target>" of a
    symlink (never followed), DIR for an empty folder}; paths under `skip` are left out.
    The box runs this same function (BOX_PY)."""
    found = {}

    def add(path, value):
        r = os.path.relpath(path, base)
        if not any(r == s or r.startswith(s + "/") for s in skip):
            found[r] = value

    def note(path):
        if os.path.islink(path):
            add(path, "link:" + os.readlink(path))
        elif os.path.isfile(path):
            with open(path, "rb") as fh:
                add(path, hashlib.sha256(fh.read()).hexdigest())

    for rel in rels:
        top = os.path.join(base, rel)
        if os.path.islink(top) or not os.path.isdir(top):
            note(top)
            continue
        for d, dirs, files in os.walk(top):       # symlinked folders are listed, not entered
            for x in dirs + files:
                note(os.path.join(d, x))
            if d != top and not dirs and not files:
                add(d, DIR)
    return found


def hashes(base, rels):
    return entries(base, rels, BOX_ONLY)


def changed_on_box(manifest, box, staged):
    """What the box changed or added since the last push: different from what was pushed
    and from what would be pushed now. First sync (no manifest): only what the laptop
    doesn't have counts, so nothing on the box is deleted blindly. An empty folder the
    laptop fills isn't in the way."""
    if manifest is None:
        changed = {p for p in box if p not in staged}
    else:
        changed = {p for p, h in box.items() if h != manifest.get(p) and h != staged.get(p)}
    return {p for p in changed if not (box[p] == DIR and any(s.startswith(p + "/") for s in staged))}


def to_delete(manifest, box, staged, changed, mode):
    """Files a sync pushed before that the laptop no longer has: {path: hash the box must
    still have for it to go (None with --push: whatever it is now)}. Nothing else is deleted."""
    return {p: None if mode == "push" else h for p, h in (manifest or {}).items()
            if p not in staged and p in box and (mode == "push" or p not in changed)}


# Run on the box, after entries(): its entries under the roots and the last manifest.
SCAN = r'''
rels, box_only, manifest_path = json.loads(sys.argv[1])
home = os.path.expanduser("~")
try:
    manifest = json.load(open(os.path.join(home, manifest_path)))
except (OSError, ValueError):
    manifest = None
print(json.dumps({"files": entries(home, rels, box_only), "manifest": manifest}))
'''

# Removes the files to_delete() picked if they are still as expected, then the folders under
# a root that this leaves empty.
REMOVE = r'''
targets, rels = json.loads(sys.argv[1])
home = os.path.expanduser("~")
removed = []
for rel, want in sorted(targets.items()):
    p = os.path.join(home, rel)
    now = entries(home, [rel]).get(rel)
    if now is None or now == DIR or want is not None and now != want:
        continue
    os.remove(p)
    removed.append(rel)
    d = os.path.dirname(rel)
    while any(d.startswith(r + "/") for r in rels) and not os.listdir(os.path.join(home, d)):
        os.rmdir(os.path.join(home, d))
        d = os.path.dirname(d)
print(json.dumps(removed))
'''


def box_py(script, arg):
    src = f"import hashlib, json, os, sys\nDIR = {DIR!r}\n" + inspect.getsource(entries) + script
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", BOX, "python3 - " + shlex.quote(json.dumps(arg))],
                       input=src, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"agent setup: couldn't read {BOX}: {r.stderr.strip()[-200:]}")
    return json.loads(r.stdout)


def scan_box(rels):
    return box_py(SCAN, [rels, sorted(BOX_ONLY), MANIFEST])


def pull(paths, origin, box_files):
    """Bring box-changed files to where they came from on the laptop."""
    for rel in sorted(paths):
        if box_files.get(rel, "").startswith("link:") or box_files.get(rel) == DIR:
            print(f"agent setup: {rel} is a link or an empty folder on {BOX}; not pulled, kept there")
            continue
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", BOX, f"cat ~/{shlex.quote(rel)}"], capture_output=True)
        if r.returncode:
            continue
        data = r.stdout
        target = origin.get(rel) or os.path.join(HOME, rel)
        if rel in origin and origin[rel] is None:     # merged with the overlay: keep that part where it lives
            if MARK.encode() not in data:
                print(f"agent setup: {rel} on {BOX} lost the box-only marker; not pulled, merge it by hand")
                continue
            data = data.split(MARK.encode())[0]
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(data)
        print(f"agent setup: pulled {rel} -> {target}")


def sync(agents="claude", mode=None, quiet=False):
    rels = [r for r, _, _ in roots_for(agents)]
    box = scan_box(rels)
    old = box["manifest"]
    out, origin = stage(agents)
    try:
        staged = hashes(out, rels)
        changed = changed_on_box(old, box["files"], staged)
        if mode == "pull" and changed:
            pull(changed, origin, box["files"])
            shutil.rmtree(out)
            out, origin = stage(agents)
            staged = hashes(out, rels)
            changed = changed_on_box(old, box["files"], staged)   # what couldn't be pulled
        if mode == "push":   # the laptop's wins, but what the box added on its own stays
            changed = {p for p in changed if p not in staged and p not in (old or {})
                       and not any(s.startswith(p + "/") for s in staged)}
        # Not pushed: what the box changed or added, and laptop files in its way.
        held = changed | {p for p in staged if any(q.startswith(p + "/") for q in changed)}

        # Deletions first (a pushed folder the laptop turned into a file is gone before rsync
        # writes the file), and only of files this sync pushed before (manifest).
        gone = to_delete(old, box["files"], staged, changed, mode)
        removed = len(box_py(REMOVE, [gone, rels])) if gone else 0
        # Then one rsync over the managed roots, without --delete; held paths are excluded.
        filters = [f"--exclude=/{p}" for p in sorted(held)]
        sources = [os.path.join(out, ".", r) for r in rels if os.path.exists(os.path.join(out, r))]
        cmd = ["rsync", "-aR", "--itemize-changes", "--rsync-path=mkdir -p .claude .codex .agents && rsync",
               *filters, *sources, f"{BOX}:"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"agent setup: rsync failed: {r.stderr.strip()[-300:]}")
        updated = sum(1 for l in r.stdout.splitlines() if l.startswith(("<f", ">f")))

        manifest = {p: h for p, h in staged.items() if p not in held}
        for p in held:                             # still unresolved: keep what we last pushed
            if old and p in old:
                manifest[p] = old[p]
        subprocess.run(["ssh", "-o", "BatchMode=yes", BOX,
                        f"mkdir -p ~/{os.path.dirname(MANIFEST)} && cat > ~/{MANIFEST}"],
                       input=json.dumps(manifest), text=True, check=True)
    finally:
        shutil.rmtree(out, ignore_errors=True)

    if updated or removed or not quiet:
        print(f"agent setup: {updated} updated, {removed} removed on {BOX}")
    if changed:
        print(f"agent setup: changed or added on {BOX}, kept as they are: " + ", ".join(sorted(changed)))
        if mode is None:
            print("  box sync --pull-setup brings them to the laptop, box sync --push-setup overwrites them")
    mcp_sync.sync(agents, quiet)


def main(argv):
    if not argv or argv[0] != "sync":
        print(__doc__.strip())
        sys.exit(0 if argv[:1] in (["-h"], ["--help"]) else 1)
    agents = argv[argv.index("--agents") + 1] if "--agents" in argv else "claude"
    mode = "pull" if "--pull" in argv else "push" if "--push" in argv else None
    sync(agents, mode, quiet="--quiet" in argv)


if __name__ == "__main__":
    main(sys.argv[1:])
