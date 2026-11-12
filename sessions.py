#!/usr/bin/env python3
"""Carry a project's Claude conversations between the laptop and its box (`box session`).

  sessions.py push|pull NAME[/WORKTREE] [--last | --all | --id ID[,ID...]] [--dry-run] [--fix] [--force]
  sessions.py ls NAME     each side's conversations: id, last change, size, title

In a terminal you pick the conversations from a list (none is fine). Without one
(an agent) the newest is carried; --last does that in a terminal too, --all carries
every one the other side doesn't have (or has an older copy of), --id the ones
named (an id prefix will do).
A push first says what the box lacks for the carried conversations (commands,
files not in git, .env files; see readiness.py): --fix applies the fixes,
--dry-run only reports.

Conversations of the project folder, its subfolders and its git worktrees are
carried, with every path rewritten, so `claude --continue` resumes them on the
other side. Worktrees are matched by branch: a worktree on the same branch on
the other side is reused, otherwise one is created there under
.claude/worktrees/ (Claude Code's own folder), on the same branch and commit. A
worktree travels when its commits are on GitHub, on any branch (a branch merged and
deleted there is fine): the test is server/bin/agentbox-git's, shared with box close.
Transcripts only grow, so their message ids tell which side is ahead: a copy
that was continued on the other side is never overwritten, and one continued
on both sides is left alone unless --force.
Only standard-library Python 3.8+.
"""
import inspect
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import time
from collections import namedtuple

import projects
import readiness
from projects import BOX, BOX_HOME, enc, laptop_path, gitwork

WT_DIR = ".claude/worktrees"   # where worktrees are created, on both sides

# Runs on both sides (locally, and on the box via `python3 - ROOT`): the checkouts
# of ROOT's repo (main first) with their subfolders, their conversations (message
# ids per transcript) and the folders Claude processes work in.
SCAN = (f"SKIP = {projects.SKIP!r}\n" + inspect.getsource(projects.rel_dirs)
        + f"REMOTE = {gitwork.REMOTE!r}\nQUIET = {gitwork.QUIET!r}\n"
        + "".join(inspect.getsource(f) for f in (gitwork.git, gitwork.checkouts, gitwork.fetch, gitwork.default_branch,
                                                   gitwork.merged_by_content, gitwork.local_only))
        + r'''
import json, os, re, subprocess, sys, tempfile

def worktrees(root):
    """ROOT first, then the repo's other checkouts. A worktree travels when it's clean and
    GitHub has its commits (agentbox-git's test; origin is fetched only if something looks
    unpushed, and anything git can't read counts as unpushed)."""
    wts = [{"path": root, "branch": git(root, "branch", "--show-current") or None}]
    if os.path.isdir(root):
        others = [c for c in checkouts(root) if os.path.realpath(c["path"]) != os.path.realpath(root)]
        # root itself is the first entry (git lists the repo's first checkout first,
        # which isn't root when root is a linked worktree or a subfolder).
        pushed = {c["path"]: bool(c["head"]) and local_only(c["path"]) == 0 for c in others}
        if not all(pushed.values()) and fetch(root):
            pushed = {c["path"]: bool(c["head"]) and local_only(c["path"]) == 0 for c in others}
        for c in others:
            dirty = git(c["path"], "status", "--porcelain", "--untracked-files=no")
            wts.append({"path": c["path"], "branch": c["branch"], "head": c["head"], "pushed": pushed[c["path"]],
                        "dirty": len(dirty.splitlines()) if dirty is not None else 1})
    for w in wts:
        w["rels"] = rel_dirs(w["path"])
    return wts

def claude_cwds():
    if os.path.isdir("/proc"):
        for pid in filter(str.isdigit, os.listdir("/proc")):
            try:
                if open(f"/proc/{pid}/comm").read().strip() == "claude":
                    yield os.readlink(f"/proc/{pid}/cwd")
            except OSError:
                pass
    else:  # macOS
        pids = subprocess.run(["pgrep", "-x", "claude"], capture_output=True, text=True).stdout.split()
        if pids:
            out = subprocess.run(["lsof", "-a", "-d", "cwd", "-Fn", "-p", ",".join(pids)],
                                 capture_output=True, text=True).stdout
            yield from (l[1:] for l in out.splitlines() if l.startswith("n"))

def read(path):
    """Message ids and a title (custom title, else Claude's, else the first prompt)."""
    found, titles, first = [], {}, None
    with open(path, errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
                u = d.get("uuid")
            except (ValueError, AttributeError):
                continue
            if u:
                found.append(u)
            if d.get("type") in ("custom-title", "ai-title"):
                titles[d["type"]] = d.get("customTitle") or d.get("aiTitle")
            elif first is None and d.get("type") == "user" and isinstance(d.get("message", {}).get("content"), str) \
                    and not d["message"]["content"].startswith("<"):
                first = d["message"]["content"]
    title = titles.get("custom-title") or titles.get("ai-title") or first or ""
    return found, " ".join(title.split())[:70]

def size(path):
    total = os.path.getsize(path + ".jsonl")
    for d, _, names in os.walk(path):
        total += sum(os.path.getsize(os.path.join(d, n)) for n in names if not os.path.islink(os.path.join(d, n)))
    return total

wts = worktrees(sys.argv[1])
base = os.path.expanduser("~/.claude/projects")
files = {}
for w in wts:
    for rel in w["rels"]:
        k = re.sub(r"[^A-Za-z0-9]", "-", os.path.normpath(os.path.join(w["path"], rel)))
        d = os.path.join(base, k)
        for f in (os.listdir(d) if os.path.isdir(d) else []):
            if f.endswith(".jsonl"):
                try:
                    p = os.path.join(d, f)
                    files[f"{k}/{f}"] = [os.path.getmtime(p), *read(p), size(p[:-len(".jsonl")])]
                except OSError:
                    pass
print(json.dumps({"worktrees": wts, "files": files, "cwds": list(claude_cwds())}))
''')

# A source checkout and where it goes; create = the worktree doesn't exist there yet.
Place = namedtuple("Place", "src dst branch create head", defaults=(None,))   # head: the commit to create it on


def scan(where, root):
    """What SCAN reports for ROOT on the laptop or the box."""
    if where == "laptop":
        r = subprocess.run([sys.executable, "-c", SCAN, root], capture_output=True, text=True)
    else:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", BOX, "python3 - " + shlex.quote(root)],
                           input=SCAN, capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except ValueError:
        sys.exit(f"Couldn't read the conversations on {where}: {r.stderr.strip() or 'no answer'}")


def pair(src_wts, dst_wts, dst_root):
    """Where each source checkout goes: main to main; a worktree to the destination's
    checkout on the same branch, else to a new worktree under WT_DIR (named after the
    source folder, numbered if taken). Worktrees without a branch stay behind."""
    main, *rest = src_wts
    places = [Place(main["path"], dst_root, main["branch"], False)]
    taken = {w["path"] for w in dst_wts}
    for w in rest:
        if not w["branch"]:
            continue
        same = next((d["path"] for d in dst_wts if d["branch"] == w["branch"]), None)
        if same:
            places.append(Place(w["path"], same, w["branch"], False))
            continue
        name = re.sub(r"[^A-Za-z0-9_-]", "-", os.path.basename(w["path"])).strip("-") or "worktree"
        dst, n = f"{dst_root}/{WT_DIR}/{name}", 2
        while dst in taken:
            dst, n = f"{dst_root}/{WT_DIR}/{name}-{n}", n + 1
        taken.add(dst)
        places.append(Place(w["path"], dst, w["branch"], True, w.get("head")))
    return places


def owner(path, folders):
    """The innermost of folders that contains path (None if none does)."""
    inside = [f for f in folders if path == f or path.startswith(f + "/")]
    return max(inside, key=len) if inside else None


def rewrite(data, swap):
    """Swap paths and folder keys inside a transcript, only where a whole name matches
    (so /work/foo leaves /work/foo-bar alone); binary files pass through."""
    try:
        text = data.decode()
    except UnicodeDecodeError:
        return data
    table = dict(swap)
    # A name may follow a JSON escape (\n/home/...) and end a sentence (/home/x. Next).
    pattern = re.compile(r"(?:(?<![\w.-])|(?<=\\[nrt]))(" + "|".join(re.escape(o) for o in sorted(table, key=len, reverse=True)) + r")(?![\w-]|\.[\w-])")
    return pattern.sub(lambda m: table[m.group(1)], text).encode()


def pack_local(base, members):
    """Tar the given relative paths under base (missing ones are left out)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for m in members:
            if os.path.exists(os.path.join(base, m)):
                t.add(os.path.join(base, m), arcname=m)
    return buf.getvalue()


def add_worktree(side, root, path, branch, head=None):
    """Check out BRANCH as a worktree at path, next to root's repo, at the source's commit
    HEAD. The branch itself needn't be on GitHub (merged and deleted there, say): HEAD is,
    on some remote branch, so it comes with the fetch, and the branch is made locally."""
    q = shlex.quote
    at = q(head) if head else q(f"origin/{branch}")
    script = f"""set -e
cd {q(root)}
git fetch -q origin
at=$(git rev-parse -q --verify {at}^{{commit}}) || {{ echo {q((head or branch) + " isn't on GitHub (push it first)")} >&2; exit 1; }}
if git show-ref -q --verify refs/heads/{q(branch)}; then
  git worktree add -q {q(path)} {q(branch)}
  git -C {q(path)} merge -q --ff-only "$at" 2>/dev/null || true
elif [ "$(git rev-parse -q --verify refs/remotes/origin/{q(branch)} || true)" = "$at" ]; then
  git worktree add -q --track -b {q(branch)} {q(path)} origin/{q(branch)}
else
  git worktree add -q -b {q(branch)} {q(path)} "$at"
fi
x="$(git rev-parse --git-common-dir)/info/exclude"
grep -qxF '/{WT_DIR}/' "$x" 2>/dev/null || echo '/{WT_DIR}/' >> "$x"
"""
    cmd = ["bash", "-s"] if side == "laptop" else ["ssh", "-o", "BatchMode=yes", BOX, "bash -s"]
    r = subprocess.run(cmd, input=script, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"Couldn't create the worktree {path} on {side} for {branch}: {r.stderr.strip()}")


def state(src_ids, other):
    """How the destination's copy compares: what `ask` shows next to a conversation."""
    if not other:
        return "not there"
    ahead, behind = set(src_ids) - set(other[1]), set(other[1]) - set(src_ids)
    return "continued on both sides" if ahead and behind else "newer here" if ahead else \
        "newer there" if behind else "same there"


def parse_choice(text, n):
    """Numbers like "1 3,5" or a range "2-4" -> sorted indexes (0-based) up to n."""
    picked = set()
    for part in re.split(r"[\s,]+", text.strip()):
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not part:
            continue
        if not m:
            raise ValueError(part)
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        if not 1 <= lo <= hi <= n:
            raise ValueError(part)
        picked |= set(range(lo - 1, hi))
    return sorted(picked)


def ask(candidates, dst_files, key_map, direction, there):
    """List the conversations newest first and let the user pick which to carry."""
    rows = sorted(candidates.items(), key=lambda kv: -kv[1][0])
    print(f"Conversations to {direction} to {there} (newest first):", file=sys.stderr)
    for i, (path, (mtime, ids, title, nbytes)) in enumerate(rows, 1):
        k, f = path.split("/", 1)
        print(f"  {i:2}) {time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime))}  {nbytes / 1e6:7.1f} MB  "
              f"{title or f[:8]}  [{state(ids, dst_files.get(f'{key_map[k]}/{f}'))}]", file=sys.stderr)
    while True:
        try:
            text = input("Numbers to carry (e.g. 1 3, or 2-4), Enter for none: ")
            return dict(rows[i] for i in parse_choice(text, len(rows)))
        except ValueError as e:
            print(f"  {e}? Use numbers from 1 to {len(rows)}.", file=sys.stderr)
        except EOFError:
            return {}


def send(cmd, data, label):
    """Run cmd with data on stdin, showing progress for big transfers."""
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    step, total, shown = 1 << 20, len(data), 0
    for i in range(0, total, step):
        try:
            p.stdin.write(data[i:i + step])
        except BrokenPipeError:   # the other end quit (disk full, connection lost): its exit code says
            break
        pct = min(100, (i + step) * 100 // total)
        if total > 20 * step and (sys.stderr.isatty() or pct >= shown + 25):
            shown = pct
            print(f"\r  {label}: {min(i + step, total) / 1e6:.0f} of {total / 1e6:.0f} MB ({pct}%)",
                  end="" if sys.stderr.isatty() else "\n", file=sys.stderr, flush=True)
    try:
        p.stdin.close()
    except BrokenPipeError:
        pass
    if sys.stderr.isatty() and total > 20 * step:
        print(file=sys.stderr)
    if p.wait():
        sys.exit(f"{label} failed (exit {p.returncode}).")


def cmd_session(direction, target, force=False, pick="last", dry_run=False, fix=False):
    """pick: "last" (the newest), None (every conversation) or a list of id prefixes."""
    if direction not in ("push", "pull"):
        sys.exit(USAGE)
    name, _, only = target.partition("/")
    lroot, broot = laptop_path(name), f"{BOX_HOME}/work/{name}"
    laptop, box = scan("laptop", lroot), scan(BOX, broot)
    if direction == "push":
        src, dst, dst_root, here, there = laptop, box, broot, "the laptop", BOX
    else:
        src, dst, dst_root, here, there = box, laptop, lroot, BOX, "the laptop"
    places = pair(src["worktrees"], dst["worktrees"], dst_root)

    # Paths and folder keys to swap: every checkout's, even when only one is carried,
    # since a conversation can mention the others.
    rels = {w["path"]: w["rels"] for w in src["worktrees"]}
    swap, key_place = [], {}
    for p in places:
        swap.append((p.src, p.dst))
        for rel in rels[p.src]:
            k = enc(os.path.normpath(os.path.join(p.src, rel)))
            swap.append((k, enc(os.path.normpath(os.path.join(p.dst, rel)))))
            key_place[k] = p
    key_map = dict(swap)
    # Other registered projects (e.g. sibling repos the conversation also worked in):
    # their folder on the other side, so paths into them keep working.
    for other, path in projects.load_registry().items():
        nested = (lroot + "/").startswith(path + "/") or path.startswith(lroot + "/")
        if other != name and not nested:   # a parent folder would swap every path below it
            there_ = f"{BOX_HOME}/work/{other}"
            swap.append((path, there_) if direction == "push" else (there_, path))
    if only:
        chosen_places = [p for p in places if only in (os.path.basename(p.src), os.path.basename(p.dst), p.branch)]
        if not chosen_places:
            sys.exit(f"{name} has no worktree {only!r} with a branch on {here}. Worktrees: "
                     + (", ".join(os.path.basename(p.src) for p in places[1:]) or "none"))
    else:
        chosen_places = places

    candidates = {path: v for path, v in src["files"].items() if key_place.get(path.split("/")[0]) in chosen_places}
    if pick == "ask":
        candidates = ask(candidates, dst["files"], key_map, direction, there)
        if not candidates:
            print(f"Nothing chosen; nothing carried.")
            return
    elif pick == "last" and candidates:
        newest = max(candidates, key=lambda c: candidates[c][0])
        candidates = {newest: candidates[newest]}
    elif pick and pick != "last":
        picked = {}
        for prefix in pick:
            hits = [c for c in candidates if c.split("/")[1].startswith(prefix)]
            if len(hits) != 1:
                sys.exit(f"{len(hits)} conversations of {target} on {here} start with {prefix!r}; see: box session ls {name}")
            picked[hits[0]] = candidates[hits[0]]
        candidates = picked

    chosen, current, diverged = [], [], []
    for path, (_, src_ids, *_) in candidates.items():
        k, f = path.split("/", 1)
        other = dst["files"].get(f"{key_map[k]}/{f}")
        ahead = set(src_ids) - set(other[1]) if other else True
        behind = set(other[1]) - set(src_ids) if other else set()
        if force or (ahead and not behind):
            chosen.append((k, f[:-len(".jsonl")]))
        elif not ahead:
            current.append(f)
        else:
            diverged.append(f)
    notes = (f"; {len(current)} already up to date or newer there" if current else "") + \
            (f"; {len(diverged)} continued on both sides, left alone (--force overwrites the copy on {there}): "
             + ", ".join(diverged) if diverged else "")
    stranded = [w["path"] for w in src["worktrees"][1:] if not w["branch"]
                and any(k.split("/")[0] == enc(w["path"]) for k in src["files"])]
    if stranded:
        notes += "; left behind (no branch checked out, nothing to match on the other side): " + ", ".join(stranded)
    if not chosen:
        print(f"Nothing to {direction}{notes}.")
        return
    carried = {key_place[k] for k, _ in chosen}

    # Collect each conversation (<id>.jsonl plus its <id>/ folder) from the source.
    local_base = os.path.expanduser("~/.claude/projects")
    members = [m for k, i in chosen for m in (f"{k}/{i}.jsonl", f"{k}/{i}")]
    if direction == "push":
        blob = pack_local(local_base, members)
    else:
        # Folder keys start with "-", hence the "--" before the names.
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", BOX, "cd ~/.claude/projects && tar -czf - --ignore-failed-read -- "
                            + " ".join(map(shlex.quote, members)) + " 2>/dev/null"], capture_output=True)
        blob = r.stdout
        if r.returncode or not blob:
            sys.exit(f"Couldn't collect the conversations on {BOX} (exit {r.returncode}).")
    # One pass over the transcripts (subagents' too): what they ran and used, and
    # the worktrees they work in (by path), which come along: a session started in
    # the main checkout keeps its transcript there while it edits .claude/worktrees/x.
    usage = readiness.Usage([p.src for p in places])
    mentioned = [(p, re.compile(re.escape(p.src.encode()) + rb"(?![\w.-])")) for p in places[1:] if p not in carried]
    used = set()
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tin:
        for m in tin:
            if m.isfile() and m.name.endswith(".jsonl"):
                data = tin.extractfile(m).read()
                usage.feed(data)
                used |= {p for p, rx in mentioned if p not in used and rx.search(data)}
    needed = carried | used

    # Checks, for the checkouts that go: nobody is working in them,
    # and a worktree's code is on GitHub (the other side gets it from there).
    if not force:
        problems = []
        for side, cwds, folders, mine in ((here, src["cwds"], [w["path"] for w in src["worktrees"]], {p.src for p in needed}),
                                          (there, dst["cwds"], [w["path"] for w in dst["worktrees"]] + [p.dst for p in places],
                                           {p.dst for p in needed})):
            for c in cwds:
                if owner(c, folders) in mine:
                    problems.append(f"Claude is still running in {c} on {side}; end it first")
        info = {w["path"]: w for w in src["worktrees"][1:]}
        for p in needed:
            w = info.get(p.src)
            if w and not w["pushed"]:
                problems.append(f"{p.branch} has commits that aren't on GitHub; push it from {p.src}")
            if w and w["dirty"]:
                problems.append(f"{p.src} has {w['dirty']} uncommitted change(s), which don't travel; commit and push them")
        if problems:
            sys.exit("\n".join(sorted(set(problems))) + "\n(or --force)")
    main_src, main_dst = src["worktrees"][0]["branch"], dst["worktrees"][0]["branch"]
    if places[0] in carried and main_src and main_dst and main_src != main_dst:
        print(f"Note: {name} is on {main_src} on {here} but on {main_dst} on {there}.")

    fixes = readiness.report(name, lroot, usage, needed, places) if direction == "push" else None
    if dry_run:
        print(f"Would carry {len(chosen)} conversation(s)" + "".join(
            f"\n  {p.dst}" + (f" (new worktree on {p.branch})" if p.create else "") for p in sorted(needed, key=lambda p: p.dst))
            + (f"\n--fix would apply the fixes above." if fixes else ""))
        return
    for p in sorted(needed, key=lambda p: p.dst):
        if p.create:
            print(f"Creating worktree {p.dst} on {there} ({p.branch})...")
            add_worktree("laptop" if direction == "pull" else BOX, dst_root, p.dst, p.branch, p.head)
    if fixes and fix:
        readiness.apply(name, lroot, fixes)
    elif fixes:
        print("Run again with --fix to apply these (the conversations are carried either way).")

    out, done = io.BytesIO(), []
    # Transcripts are text: compressed they travel several times faster.
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tin, tarfile.open(fileobj=out, mode="w:gz", compresslevel=3) as tout:
        for m in tin.getmembers():
            k, _, rest = m.name.partition("/")
            m.name = f"{key_map.get(k, k)}/{rest}"
            m.pax_headers.pop("path", None)  # a long name lives there and would win over m.name
            data = tin.extractfile(m).read() if m.isfile() else None
            if data is not None:
                data = rewrite(data, swap)
                m.size = len(data)
                if rest.endswith(".jsonl") and "/" not in rest:
                    done.append((src["files"][f"{k}/{rest}"][0], m.name))
            tout.addfile(m, io.BytesIO(data) if data is not None else None)
    if not done:
        print(f"Nothing to {direction}: the conversations disappeared meanwhile.")
        return
    # The newest carried conversation becomes the newest on the other side (for --continue).
    newest = max(done)[1]
    if direction == "push":
        send(["ssh", "-o", "BatchMode=yes", BOX,
              f"mkdir -p ~/.claude/projects && cd ~/.claude/projects && tar -xzf - && touch -c -- {shlex.quote(newest)}"],
             out.getvalue(), f"Sending {len(done)} conversation(s) to {BOX}")
    else:
        os.makedirs(local_base, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(out.getvalue())) as t:
            t.extractall(local_base, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
        os.utime(os.path.join(local_base, newest))
    print(f"Carried {len(done)} conversation(s) of {name} to {there}{notes}.")
    for p in sorted(carried, key=lambda p: p.dst):
        if direction == "pull" or owner(p.dst, [dst_root]) is None:
            how = f"cd {p.dst} && claude --continue"
        elif p.dst == dst_root:
            how = f"box attach {name}"
        elif p.dst.startswith(f"{dst_root}/{WT_DIR}/"):
            how = f"box attach {name}/{os.path.basename(p.dst)}"
        else:
            how = f"box attach {name}/{os.path.relpath(p.dst, dst_root)}"
        print(f"  {p.dst}: {how}")
    for p in sorted(used, key=lambda p: p.dst):
        print(f"  {p.dst}: worktree on {p.branch}, used in the conversation" + (" (created)" if p.create else ""))


def cmd_ls(name):
    """Each side's conversations of the project, newest first."""
    lroot, broot = laptop_path(name), f"{BOX_HOME}/work/{name}"
    for where, root in (("laptop", lroot), (BOX, broot)):
        r = scan(where, root)
        folders = {enc(os.path.normpath(os.path.join(w["path"], rel))): os.path.normpath(os.path.join(w["path"], rel))
                   for w in r["worktrees"] for rel in w["rels"]}
        print(f"On {where} ({root}):")
        rows = sorted(r["files"].items(), key=lambda kv: -kv[1][0])
        for path, (mtime, _, title, nbytes) in rows:
            k, f = path.split("/", 1)
            folder = os.path.relpath(folders.get(k, k), root)
            print(f"  {f[:8]}  {time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime))}  {nbytes / 1e6:7.1f} MB"
                  f"  {'' if folder == '.' else folder + ': '}{title}")
        if not rows:
            print("  (none)")


USAGE = """usage: sessions.py push|pull NAME[/WORKTREE] [--all | --id ID[,ID...]] [--dry-run] [--fix] [--force]
       sessions.py ls NAME"""

if __name__ == "__main__":
    argv, opts = sys.argv[1:], {}
    # In a terminal, choose from a list; an agent (no terminal) gets the newest.
    if sys.stdin.isatty() and sys.stderr.isatty():
        opts["pick"] = "ask"
    args = []
    while argv:
        a = argv.pop(0)
        if a == "--id":
            if not argv:
                sys.exit(USAGE)
            opts["pick"] = [i for i in argv.pop(0).split(",") if i]
        elif a == "--last":
            opts["pick"] = "last"
        elif a == "--all":
            opts["pick"] = None
        elif a in ("--force", "--dry-run", "--fix"):
            opts[a[2:].replace("-", "_")] = True
        else:
            args.append(a)
    if args[:1] in (["-h"], ["--help"]):
        print(__doc__.strip())
    elif len(args) == 2 and args[0] == "ls":
        cmd_ls(args[1])
    elif len(args) == 2:
        cmd_session(args[0], args[1], **opts)
    else:
        sys.exit(USAGE)
