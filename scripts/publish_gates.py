#!/usr/bin/env python3
"""The checks behind scripts/publish.sh and its pre-push guard, kept apart so they can be tested.

A release is a tree built from the development repository: its files less the paths in
`.publishignore`. Before it goes out, nothing in it (file names, contents, the commit message)
may match the private deny list, a file outside every repository with one entry per line: a
case-insensitive substring, or `re:` and a Python regular expression. Findings name the file and
the entry's number, never the text that matched, so a report can't leak what it guards.

  publish_gates.py deny TREE [--message FILE] [--denylist FILE]   findings, exit 1 if any
  publish_gates.py ignored TREE [REV]                             paths REV's .publishignore excludes (default HEAD), if any are in TREE
  publish_gates.py identity NAME EMAIL                            exit 1 unless EMAIL is a noreply address and clean
  publish_gates.py release-tree REV                               writes the release tree of REV, prints its id
  publish_gates.py redact                                         copies stdin to stdout, deny list matches masked
"""
import fnmatch
import os
import re
import subprocess
import sys
import tempfile

DEFAULT_DENYLIST = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                                "agentbox", "publish-denylist")
IGNORE_FILE = ".publishignore"
NOREPLY = "@users.noreply.github.com"


def git(*args, input=None, env=None):
    r = subprocess.run(["git", *args], capture_output=True, input=input, env=env)
    if r.returncode:
        raise SystemExit(f"git {' '.join(args[:2])} failed: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout


def load_denylist(path):
    """[(entry number, compiled pattern)]: comments and blank lines skipped, numbers count entries."""
    if not os.path.isfile(path):
        raise SystemExit(f"No deny list at {path}: create it (one private string per line) before publishing.")
    out, n = [], 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            n += 1
            try:
                pat = re.compile(line[3:], re.I) if line.startswith("re:") else re.compile(re.escape(line), re.I)
            except re.error as e:
                raise SystemExit(f"Deny list entry {n} isn't a valid regular expression ({e}).")
            out.append((n, pat))
    if not out:
        raise SystemExit(f"The deny list at {path} has no entries.")
    return out


def scan_text(text, pats):
    return sorted({n for n, p in pats if p.search(text)})


def tree_files(tree):
    """[(path, blob id)] of a tree, recursively; symlinks and submodules by their recorded value."""
    out = []
    for line in git("ls-tree", "-r", "-z", "--full-tree", tree).split(b"\0"):
        if not line:
            continue
        meta, path = line.split(b"\t", 1)
        out.append((path.decode(errors="replace"), meta.split()[2].decode()))
    return out


def scan_tree(tree, pats, message=None):
    """[(where, [entry numbers])]: file names, file contents and the commit message."""
    findings = []
    for path, blob in tree_files(tree):
        hits = set(scan_text(path, pats))
        hits |= set(scan_text(git("cat-file", "blob", blob).decode("utf-8", "replace"), pats))
        if hits:
            findings.append((path, sorted(hits)))
    if message is not None:
        hits = scan_text(message, pats)
        if hits:
            findings.append(("(commit message)", hits))
    return findings


def ignore_patterns(text):
    return [l.strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]


def ignored(path, patterns):
    """True when PATH falls under one of the .publishignore patterns: a folder (`docs/plans/`),
    a file, or a glob (`*.local.md`); patterns are relative to the repository root."""
    for pat in patterns:
        p = pat.lstrip("/")
        if p.endswith("/"):
            if path.startswith(p) or path == p.rstrip("/"):
                return True
        elif path == p or path.startswith(p + "/") or fnmatch.fnmatch(path, p):
            return True
    return False


def patterns_of(rev):
    try:
        return ignore_patterns(git("show", f"{rev}:{IGNORE_FILE}").decode())
    except SystemExit:
        return []


def release_tree(rev):
    """The tree of REV less what its .publishignore excludes, written to the object store."""
    patterns = patterns_of(rev)
    with tempfile.TemporaryDirectory() as d:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(d, "index"))
        git("read-tree", rev, env=env)
        drop = [p for p, _ in tree_files(rev) if ignored(p, patterns)]
        if drop:
            git("update-index", "--force-remove", "-z", "--stdin", input="\0".join(drop).encode() + b"\0", env=env)
        return git("write-tree", env=env).decode().strip()


def identity_problems(name, email, pats):
    problems = []
    if not name or not email:
        problems.append("no author name or email is set (git config user.name / user.email in this repository)")
    elif not email.endswith(NOREPLY):
        problems.append(f"the author email isn't a GitHub noreply address (…{NOREPLY})")
    if scan_text(f"{name} {email}", pats):
        problems.append("the author name or email matches the deny list")
    return problems


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    cmd, args = argv[0], argv[1:]
    denylist = os.environ.get("PUBLISH_DENYLIST") or DEFAULT_DENYLIST
    if "--denylist" in args:
        i = args.index("--denylist")
        denylist = args[i + 1]
        del args[i:i + 2]
    if cmd == "deny":
        message = None
        if "--message" in args:
            i = args.index("--message")
            with open(args[i + 1], encoding="utf-8") as fh:
                message = fh.read()
            del args[i:i + 2]
        findings = scan_tree(args[0], load_denylist(denylist), message)
        for where, entries in findings:
            print(f"  {where}: deny list entr{'y' if len(entries) == 1 else 'ies'} {', '.join(map(str, entries))}")
        return 1 if findings else 0
    if cmd == "ignored":
        patterns = patterns_of(args[1] if len(args) > 1 else "HEAD")
        bad = [p for p, _ in tree_files(args[0]) if ignored(p, patterns)]
        for p in bad:
            print(f"  {p}")
        return 1 if bad else 0
    if cmd == "identity":
        problems = identity_problems(args[0], args[1], load_denylist(denylist))
        for p in problems:
            print(f"  {p}")
        return 1 if problems else 0
    if cmd == "redact":
        try:
            pats = load_denylist(denylist)
        except SystemExit as e:          # no list: nothing can be shown safely
            for _ in iter(sys.stdin.readline, ""):
                pass
            print(f"publish: {e}")
            return 1
        for line in iter(sys.stdin.readline, ""):   # line by line, as the output comes
            for _, p in pats:
                line = p.sub("[private]", line)
            sys.stdout.write(line)
            sys.stdout.flush()
        return 0
    if cmd == "release-tree":
        print(release_tree(args[0]))
        return 0
    raise SystemExit(f"unknown command {cmd!r}; see --help")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
