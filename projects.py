#!/usr/bin/env python3
"""Projects shared between your laptop and the box (used by the `box` command).

A project is a folder on your laptop (one repo, or a workspace of several) that
also lives on the box at ~/work/NAME. Registry: ~/.config/agentbox/projects.json.
Optional description: agentbox.json in the project folder, e.g.

  {
    "repos": [{"path": "api", "git": "me/api"}, {"path": "web", "git": "me/web"}],
    "data":  [{"path": "state", "mode": "home"},
              {"path": "fixtures/raw", "mode": "copy"}],
    "ports": [5173, 8000],
    "env_files": [{"path": "api/.env", "secrets": "api"}],
    "tools": ["docker", "terraform", "aws-cli@2"],
    "setup": "python3 skills/install.py",
    "account": "work",
    "laptop_only": "the desktop-app integration only runs on Windows"
  }

  projects.py add PATH [NAME]      register a folder; write agentbox.json if missing, then detect
  projects.py detect NAME          add what the repo shows to agentbox.json: tools, .env files in Bitwarden
  projects.py list                 NAME<TAB>laptop path
  projects.py get NAME KEY         a manifest value as JSON (ports, account, ...)
  projects.py check PATH           portability report
  projects.py sync NAME            two-way sync of Claude's project memory, push settings.local.json
  projects.py data NAME PATH copy|move-to-box|move-to-laptop
Only standard-library Python 3.8+.
"""
import json
import os
import re
import shlex
import subprocess
import sys
import time
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader

# What counts as work that exists only on one machine: the box's agentbox-git (no .py
# suffix, so loaded by path), shared by box check, box session and the box's box close.
_GITWORK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server", "bin", "agentbox-git")
gitwork = module_from_spec(spec_from_loader("agentbox_git", SourceFileLoader("agentbox_git", _GITWORK)))
gitwork.__loader__.exec_module(gitwork)

CONF = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "agentbox")
REGISTRY = os.path.join(CONF, "projects.json")
BOX = os.environ.get("BOX_NAME") or "agentbox"
BOX_HOME = "/home/dev"
SKIP = {"node_modules", ".git", ".venv", "venv", "dist", "build", ".next", "__pycache__",
        ".turbo", ".wrangler", "worktrees", ".worktrees", ".cache"}
MANIFEST = "agentbox.json"
OLD_MANIFEST = "claude-box.json"  # name before the rename to agentbox, still read


# --- registry & manifest -----------------------------------------------------
def load_registry():
    try:
        with open(REGISTRY) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def save_registry(reg):
    os.makedirs(CONF, exist_ok=True)
    with open(REGISTRY, "w") as fh:
        json.dump(reg, fh, indent=2, sort_keys=True)


def laptop_path(name):
    reg = load_registry()
    if name not in reg:
        sys.exit(f"Unknown project {name!r}. Register it with: box add PATH [{name}]")
    return reg[name]


def manifest(path):
    for name in (MANIFEST, OLD_MANIFEST):
        try:
            with open(os.path.join(path, name)) as fh:
                return json.load(fh)
        except FileNotFoundError:
            continue
    return {}


def git(path, *args):
    out = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else None


def is_repo_root(path):
    return os.path.exists(os.path.join(path, ".git"))


def find_repos(root, depth=3):
    """Git repos at or below root (not descending into repos or junk folders)."""
    found = []
    def walk(d, level):
        if is_repo_root(d):
            found.append(d)
            return
        if level >= depth:
            return
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            if e.is_dir(follow_symlinks=False) and e.name not in SKIP and not e.name.startswith("."):
                walk(e.path, level + 1)
    walk(root, 0)
    return found


def github_slug(url):
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url or "")
    return m.group(1) if m else None


def cmd_add(path, name=None):
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        sys.exit(f"{path} is not a folder")
    name = name or os.path.basename(path)
    reg = load_registry()
    reg[name] = path
    save_registry(reg)
    mpath = os.path.join(path, MANIFEST)
    if not os.path.exists(mpath) and not os.path.exists(os.path.join(path, OLD_MANIFEST)):
        repos = []
        for r in find_repos(path):
            slug = github_slug(git(r, "remote", "get-url", "origin"))
            repos.append({"path": os.path.relpath(r, path), "git": slug})
        starter = {"repos": repos, "data": [], "ports": [], "env_files": []}
        with open(mpath, "w") as fh:
            json.dump(starter, fh, indent=2)
            fh.write("\n")
        print(f"Wrote a starter {mpath}.")
    print(f"Registered {name} -> {path} (box: ~/work/{name})")
    cmd_detect(name)


# --- detection: what the repo needs on the box -----------------------------------
# Command-line tools a repo's scripts call, as mise tool names. `docker` is BOX_DOCKER.
CLI_TOOLS = {"aws": "aws-cli", "gcloud": "gcloud", "az": "azure-cli", "kubectl": "kubectl", "helm": "helm",
             "flyctl": "flyctl", "supabase": "supabase", "pulumi": "pulumi", "doctl": "doctl", "heroku": "heroku",
             "terraform": "terraform", "tofu": "opentofu", "packer": "packer", "ansible-playbook": "ansible"}
SCRIPT_EXT = {".sh", ".bash", ".zsh"}
SCRIPT_NAMES = {"Makefile", "justfile", "Justfile", "Taskfile.yml", "package.json"}
CALLS = re.compile(r"(?:^|[;&|(`\"]|\$\()\s*(?:sudo\s+|exec\s+|timeout\s+\S+\s+)?(" + "|".join(map(re.escape, CLI_TOOLS)) + r")\s+[a-z][\w-]*",
                   re.M)
ENV_FILE = re.compile(r"\.env(\.[\w-]+)?$")
ENV_TEMPLATE = (".example", ".sample", ".template", ".dist", ".defaults")


def env_files_in(root):
    """Untracked .env files directly in root (templates like .env.example don't count)."""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    return [n for n in names if ENV_FILE.fullmatch(n) and not n.endswith(ENV_TEMPLATE)
            and os.path.isfile(os.path.join(root, n)) and not git(root, "ls-files", "--", n)]


def env_names(path):
    names = set()
    with open(path, errors="replace") as fh:
        for line in fh:
            m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
            if m:
                names.add(m.group(1))
    return names


_APPS = {}


def bitwarden_app_names():
    """{app: {variable names}} from Bitwarden (names only), via the `secrets` command."""
    if not _APPS:
        try:
            r = subprocess.run(["secrets", "--apps"], capture_output=True, text=True)
        except FileNotFoundError:   # not installed on this laptop (install-local.sh)
            print("The `secrets` command isn't installed here (./install-local.sh), so .env files can't be matched to Bitwarden.",
                  file=sys.stderr)
            _APPS[None] = set()
            return {}
        for app in r.stdout.split() if r.returncode == 0 else []:
            _APPS[app] = set(subprocess.run(["secrets", app], capture_output=True, text=True).stdout.split())
    return _APPS


def bitwarden_match(path):
    """(app, missing names): the Bitwarden app holding every variable of the .env
    file at path, else the closest app and the names it lacks."""
    names = env_names(path)
    best, best_missing = None, names
    for app, have in bitwarden_app_names().items():
        if app is None:
            continue
        missing = names - have
        if len(missing) < len(best_missing):
            best, best_missing = app, missing
    return (best, set()) if best and not best_missing else (best, best_missing)


def detect(root):
    """(tools, env_files) the checkout at root needs on the box, from its files."""
    tools = set()
    tracked = (git(root, "ls-files") or "").splitlines()
    pinned = ""
    for f in (".tool-versions", "mise.toml", ".mise.toml"):
        if os.path.exists(os.path.join(root, f)):
            with open(os.path.join(root, f), errors="replace") as fh:
                pinned += fh.read()
    for rel in tracked:
        base, ext = os.path.basename(rel), os.path.splitext(rel)[1]
        if ext == ".tf":
            tools.add("terraform")
        elif base == "go.mod":
            tools.add("go")
        elif base == "Cargo.toml":
            tools.add("rust")
        elif base in ("justfile", "Justfile"):
            tools.add("just")
        elif base.startswith("Dockerfile") or base in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
            tools.add("docker")
        if ext in SCRIPT_EXT or base in SCRIPT_NAMES:
            try:
                with open(os.path.join(root, rel), errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            tools |= {CLI_TOOLS[m] for m in CALLS.findall(text)}
    tools = {t for t in tools if not re.search(rf"(?m)^\s*\"?{re.escape(t)}\"?\s*[= ]", pinned)}
    return tools, env_files_in(root)


def cmd_detect(name):
    """Add what the repos show to agentbox.json: tools, and .env files whose variables
    are all in one Bitwarden app. Only adds; never removes or changes entries."""
    lroot = laptop_path(name)
    mpath = os.path.join(lroot, MANIFEST if os.path.exists(os.path.join(lroot, MANIFEST)) or
                         not os.path.exists(os.path.join(lroot, OLD_MANIFEST)) else OLD_MANIFEST)
    m = manifest(lroot)
    tools = list(m.get("tools", []))
    envs = list(m.get("env_files", []))
    have_tools = {t.split("@")[0] for t in tools}
    have_envs = {e["path"] for e in envs}
    added = []
    for r in find_repos(lroot) or [lroot]:
        found_tools, found_envs = detect(r)
        for t in sorted(found_tools - have_tools):
            tools.append(t)
            have_tools.add(t)
            added.append(f"tool {t}")
        for f in found_envs:
            rel = os.path.normpath(os.path.relpath(os.path.join(r, f), lroot))
            if rel in have_envs:
                continue
            app, missing = bitwarden_match(os.path.join(r, f))
            if app and not missing:
                envs.append({"path": rel, "secrets": app})
                have_envs.add(rel)
                added.append(f"{rel} from Bitwarden app {app}")
            else:
                print(f"{name}: {rel} has {len(missing)} variable(s) that aren't in Bitwarden"
                      + (f" (closest app: {app})" if app else "") + "; add them as APP__NAME to use it on the box: "
                      + ", ".join(sorted(missing)))
    if added:
        m["tools"], m["env_files"] = tools, envs
        with open(mpath, "w") as fh:
            json.dump(m, fh, indent=2)
            fh.write("\n")
        print(f"{name}: added to {os.path.basename(mpath)}: " + "; ".join(added))
    if "docker" in have_tools and os.environ.get("BOX_DOCKER", "0") != "1":
        print(f"{name}: uses Docker, but BOX_DOCKER isn't 1 in box.conf")
    if m.get("laptop_only"):
        print(f"{name}: laptop only: {m['laptop_only']}")


# --- portability check ---------------------------------------------------------
ENV_READS = re.compile(r"""(load_dotenv\(|dotenv\.config\(|env_file\s*:|read_text\(\)|open\([^)]*['"]\.env)""")
LOCALHOST = re.compile(r"(localhost|127\.0\.0\.1):\d{4}")
WINDOWS = re.compile(r"(powershell\.exe|/mnt/c/|\.exe['\"\s]|C:\\\\)")
HOMEPATH = re.compile(re.escape(os.path.expanduser("~")) + r"/")
TEXT_EXT = {".py", ".ts", ".tsx", ".js", ".mjs", ".sh", ".toml", ".yml", ".yaml", ".json", ".ps1"}


def scan_code(repo, limit=4):
    hits = {"reads .env files": [], "localhost-bound": [], "Windows-only": [], "hardcoded home path": []}
    files = git(repo, "ls-files") or ""
    for rel in files.splitlines():
        if os.path.splitext(rel)[1] not in TEXT_EXT or "lock" in rel:
            continue
        try:
            with open(os.path.join(repo, rel), errors="ignore") as fh:
                for n, line in enumerate(fh, 1):
                    for label, rx in (("reads .env files", ENV_READS), ("localhost-bound", LOCALHOST),
                                      ("Windows-only", WINDOWS), ("hardcoded home path", HOMEPATH)):
                        if label == "reads .env files" and ".env" not in line:
                            continue
                        if rx.search(line) and len(hits[label]) < limit:
                            hits[label].append(f"{rel}:{n}")
        except OSError:
            continue
    return {k: v for k, v in hits.items() if v}


def du_bytes(path):
    out = subprocess.run(["du", "-sk", path], capture_output=True, text=True).stdout.split()
    return int(out[0]) * 1024 if out else 0


def du(path):
    out = subprocess.run(["du", "-sh", path], capture_output=True, text=True).stdout.split()
    return out[0] if out else "?"


def cmd_check(path):
    path = os.path.abspath(os.path.expanduser(path))
    m = manifest(path)
    print(f"Portability check: {path}" + ("" if m else f"  (no {MANIFEST} yet)"))
    repos = find_repos(path)
    for r in repos:
        rel = os.path.relpath(r, path)
        print(f"\n■ {rel}")
        remote = git(r, "remote", "get-url", "origin")
        slug = github_slug(remote)
        issues = []
        if not remote:
            issues.append("no git remote: this is the only copy")
        elif not slug:
            issues.append(f"remote isn't GitHub ({remote.split('@')[-1][:40]}); the box can't clone it with the GitHub token")
        # Work only on this laptop, in the repo and each worktree: agentbox-git's test, the
        # same one box close and box session use (a worktree travels when it's in none).
        if remote:
            for wpath, found in gitwork.problems(r):
                where = "" if os.path.realpath(wpath) == os.path.realpath(r) else f"worktree {os.path.basename(wpath)}: "
                issues.append(where + ", ".join(found))
        for f in (".claude/settings.local.json", ".agents/skills", ".claude/skills"):
            p = os.path.join(r, f)
            if os.path.exists(p) and not git(r, "ls-files", f):
                issues.append(f"{f} is not committed (won't reach the box through git)")
        for f in (".nvmrc", ".node-version", ".python-version", ".tool-versions", "mise.toml"):
            if os.path.exists(os.path.join(r, f)):
                break
        else:
            if os.path.exists(os.path.join(r, "package.json")) or os.path.exists(os.path.join(r, "pyproject.toml")):
                issues.append("no version pin file (.nvmrc / .python-version / mise.toml)")
        for label, where in scan_code(r).items():
            issues.append(f"{label}: {', '.join(where)}")
        for i in issues:
            print(f"  - {i}")
        if not issues:
            print("  ✓ portable")
    declared = {d["path"] for d in m.get("data", [])}
    # Data the box won't get through git: folders outside every repo, and
    # git-ignored folders inside repos (outputs, local databases, ...).
    untracked = []
    def loose(d):
        """Folders under d outside every repo; descend into folders that contain repos."""
        for e in sorted(os.scandir(d), key=lambda e: e.name):
            if not e.is_dir(follow_symlinks=False) or e.name in SKIP or e.name.startswith("."):
                continue
            if any(r == e.path for r in repos):
                continue
            if any(r.startswith(e.path + os.sep) for r in repos):
                loose(e.path)
            else:
                untracked.append(e.path)
    if not is_repo_root(path):
        loose(path)
    for r in repos:
        ignored = git(r, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory") or ""
        for rel in ignored.splitlines():
            full = os.path.join(r, rel.rstrip("/"))
            parts = set(rel.rstrip("/").split("/"))
            if rel.endswith("/") and not parts & SKIP and not any(p.startswith(".") for p in parts) \
                    and not rel.rstrip("/").endswith(".egg-info"):
                untracked.append(full)
    if untracked:
        print("\n■ data not in git (the box won't get it by cloning)")
        for d in untracked:
            rel = os.path.relpath(d, path)
            kb = subprocess.run(["du", "-sk", d], capture_output=True, text=True).stdout.split()
            if not kb or int(kb[0]) < 50:
                continue
            size = du(d)
            sqlite = subprocess.run(["find", d, "-maxdepth", "4", "(", "-name", "*.sqlite*", "-o", "-name", "*.db", ")",
                                     "-size", "+0"], capture_output=True, text=True).stdout.split()
            note = f", {len(sqlite)} SQLite file(s): give it one home" if sqlite else ""
            status = "declared" if rel in declared else "not declared in agentbox.json"
            print(f"  - {rel} ({size}{note}): {status}")


# --- Claude memory & settings sync --------------------------------------------
def enc(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)  # how Claude Code names project folders


def ssh(cmd, input=None):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", BOX, cmd], input=input, capture_output=True, text=True)


def ensure_dir(target):
    """Create the folder an rsync target writes into (a local path or BOX:path)."""
    remote = target.startswith(f"{BOX}:")
    path = target[len(BOX) + 1:] if remote else target
    folder = path if path.endswith("/") else os.path.dirname(path)
    if remote:
        ssh(f"mkdir -p {shlex.quote(folder)}")
    elif folder:
        os.makedirs(folder, exist_ok=True)


def rsync(src, dst, *extra):
    ensure_dir(dst)  # rsync --mkpath needs 3.2.3+, which macOS doesn't have
    subprocess.run(["rsync", "-rtu", *extra, src, dst], capture_output=True, text=True)


def rel_dirs(root, depth=3):
    rels = [""]
    def walk(d, level):
        if level >= depth:
            return
        try:
            for e in os.scandir(d):
                if e.is_dir(follow_symlinks=False) and e.name not in SKIP and not e.name.startswith("."):
                    rels.append(os.path.relpath(e.path, root))
                    walk(e.path, level + 1)
        except OSError:
            pass
    walk(root, 0)
    return rels


def merge_index(a, b):
    """Union of MEMORY.md lines: a's order first, then lines only in b."""
    seen, out = set(), []
    for line in a.splitlines() + b.splitlines():
        key = line.strip()
        if key and key in seen:
            continue
        seen.add(key)
        out.append(line)
    return "\n".join(out).strip("\n") + "\n"


def cmd_sync(name, quiet=False):
    lroot = laptop_path(name)
    broot = f"{BOX_HOME}/work/{name}"
    lproj = os.path.expanduser("~/.claude/projects")
    remote_keys = set(ssh("ls ~/.claude/projects 2>/dev/null").stdout.split())
    pairs = 0
    for rel in rel_dirs(lroot):
        lkey = enc(os.path.normpath(os.path.join(lroot, rel)))
        bkey = enc(os.path.normpath(os.path.join(broot, rel)))
        lmem = os.path.join(lproj, lkey, "memory")
        bmem = f".claude/projects/{bkey}/memory"
        have_local, have_remote = os.path.isdir(lmem), bkey in remote_keys
        if not (have_local or have_remote):
            continue
        pairs += 1
        os.makedirs(lmem, exist_ok=True)
        # Topic files: newest copy wins, in both directions.
        rsync(f"{lmem}/", f"{BOX}:{bmem}/", "--exclude", "MEMORY.md")
        if have_remote:
            rsync(f"{BOX}:{bmem}/", f"{lmem}/", "--exclude", "MEMORY.md")
        # The index: merge line by line so neither side's entries are lost.
        local_idx = open(os.path.join(lmem, "MEMORY.md")).read() if os.path.exists(os.path.join(lmem, "MEMORY.md")) else ""
        remote_idx = ssh(f"cat {shlex.quote(bmem)}/MEMORY.md 2>/dev/null").stdout
        merged = merge_index(local_idx, remote_idx)
        if merged.strip() and merged != local_idx:
            with open(os.path.join(lmem, "MEMORY.md"), "w") as fh:
                fh.write(merged)
        if merged.strip() and merged != remote_idx:
            subprocess.run(["ssh", "-o", "BatchMode=yes", BOX, f"mkdir -p {shlex.quote(bmem)} && cat > {shlex.quote(bmem)}/MEMORY.md"],
                           input=merged, text=True, capture_output=True)
    # Personal per-project settings: laptop -> box.
    settings = 0
    for rel in rel_dirs(lroot):
        f = os.path.join(lroot, rel, ".claude", "settings.local.json")
        if os.path.exists(f):
            dst = os.path.normpath(os.path.join(broot, rel))
            rsync(f, f"{BOX}:{dst}/.claude/settings.local.json")
            # Our copy, not work: keep it out of the box's "uncommitted changes" (box close).
            ssh(f"cd {shlex.quote(dst)} && x=$(git rev-parse --git-path info/exclude 2>/dev/null) && "
                f"{{ grep -qxF '.claude/settings.local.json' \"$x\" 2>/dev/null || echo '.claude/settings.local.json' >> \"$x\"; }}")
            settings += 1
    tools = sync_tools(name, lroot)
    envs = sync_env_files(name, lroot, broot)
    sync_setup(name, lroot, broot, quiet=quiet)
    if not quiet:
        print(f"{name}: synced Claude memory for {pairs} folder(s), settings.local.json for {settings}"
              + (f", tools: {' '.join(tools)}" if tools else "") + (f", env files: {' '.join(envs)}" if envs else ""))


def sync_env_files(name, lroot, broot):
    """The manifest's "env_files" become .env files on the box, made from Bitwarden
    by `secrets APP --env-file` (in RAM, so they're recreated after every open)."""
    done = []
    entries = manifest(lroot).get("env_files", [])
    if entries and ssh(f"test -d {shlex.quote(broot)}").returncode != 0:
        return done   # the project isn't on this box (yet)
    for e in entries:
        path = os.path.normpath(os.path.join(broot, e["path"]))
        r = ssh(f"secrets {shlex.quote(e['secrets'])} --env-file {shlex.quote(path)}")
        if r.returncode:
            print(f"{name}: couldn't write {e['path']} from Bitwarden app {e['secrets']}: {r.stderr.strip()[-200:]}", file=sys.stderr)
        else:
            done.append(e["path"])
            for line in r.stderr.splitlines():   # values some readers get changed (names only)
                if line.startswith("note: "):
                    print(f"{name}: {line[6:]}", file=sys.stderr)
    return done


SETUP_TIMEOUT = int(os.environ.get("BOX_SETUP_TIMEOUT", "900") or 900)   # seconds a project's setup may take


def setup_script(name, broot, repos=(".",), timeout=SETUP_TIMEOUT):
    """The box side of "setup": runs the command (read from stdin) in the project's folder,
    once per (command, commits of REPOS): a marker in ~/.config/agentbox/setup records the last
    run that worked, so an unchanged project costs a git call per repo, a pull in any of its
    repos or a changed command runs it again, and a failed run is retried on the next sync. Its
    output goes to NAME.log next to the marker. The outcome is the last line it prints, not its
    exit code (the command's own exit codes can be anything): "agentbox-setup skip", "... absent"
    (no such folder), "... ran", or "... failed RC" followed by the end of the log."""
    q = shlex.quote
    heads = " ".join(q(r) for r in repos)
    return (f"cmd=$(cat); cd {q(broot)} 2>/dev/null || {{ echo 'agentbox-setup absent'; exit 0; }}; "
            f"heads=$(for r in {heads}; do git -C \"$r\" rev-parse HEAD 2>/dev/null || echo none; done); "
            "key=$(printf '%s\\n%s' \"$heads\" \"$cmd\" | sha256sum | cut -c1-16); "
            f"d=~/.config/agentbox/setup; f=$d/{q(name)}; log=$d/{q(name)}.log; "
            "[ \"$(cat \"$f\" 2>/dev/null)\" = \"$key\" ] && { echo 'agentbox-setup skip'; exit 0; }; "
            "mkdir -p \"$d\"; "
            f"timeout {int(timeout)} bash -lc \"$cmd\" </dev/null >\"$log\" 2>&1; rc=$?; "
            "if [ $rc -eq 0 ]; then echo \"$key\" > \"$f\"; echo 'agentbox-setup ran'; "
            "else echo \"agentbox-setup failed $rc\"; tail -5 \"$log\"; fi")


def sync_setup(name, lroot, broot, quiet=False):
    """The manifest's "setup": a command the project needs run on the box after it's cloned
    and whenever it or a commit of its repos changes (e.g. installing user-level skills or
    hooks the project ships). It's the project's own committed instruction, like "tools";
    a failure or a timeout (BOX_SETUP_TIMEOUT) warns and is retried on the next sync, it
    never stops the sync. Returns the outcome: skip, absent, ran, failed or unreachable."""
    m = manifest(lroot)
    cmd = m.get("setup")
    if not cmd:
        return None
    repos = [r["path"] for r in m.get("repos", []) if r.get("path")] or ["."]
    r = ssh(setup_script(name, broot, repos), input=cmd)
    out = r.stdout.splitlines()
    status = next((i for i, line in enumerate(out) if line.startswith("agentbox-setup ")), None)
    if status is None:
        print(f"{name}: couldn't reach {BOX} to run its setup (ssh exit {r.returncode}); "
              "it runs on the next sync.", file=sys.stderr)
        return "unreachable"
    word, *rest = out[status].split()[1:]
    if word == "ran" and not quiet:
        print(f"{name}: ran its setup on {BOX} ({cmd})")
    elif word == "failed":
        code = rest[0] if rest else "?"
        why = "timed out" if code == "124" else f"exit {code}"
        tail = "\n".join(out[status + 1:])
        print(f"{name}: its setup failed on {BOX} ({why}); it runs again on the next sync. "
              f"Log: ~/.config/agentbox/setup/{name}.log" + (f"\n{tail}" if tail else ""), file=sys.stderr)
    return word


def sync_tools(name, lroot):
    """The manifest's "tools" (mise names, e.g. "terraform", "aws-cli@2"; "docker"
    is BOX_DOCKER) go into a mise conf.d file on the box, which the full rewrite
    of the global mise config on every sync leaves alone. Installed on change. Then
    agentbox-mise-defaults (if this box has it) gives every tool a project installed a
    default outside it, so its shim doesn't fail elsewhere; that also covers tools a
    project's own mise.toml installed since the last sync."""
    tools = [t for t in manifest(lroot).get("tools", []) if t.split("@")[0] != "docker"]
    toml = "[tools]\n" + "".join(f'"{t.split("@")[0]}" = "{t.split("@")[1] if "@" in t else "latest"}"\n' for t in tools)
    path = f".config/mise/conf.d/agentbox-{name}.toml"
    defaults = "if command -v agentbox-mise-defaults >/dev/null; then agentbox-mise-defaults >/dev/null; fi"
    if not tools:
        ssh(f"rm -f {shlex.quote(path)}; {defaults}")
        return tools
    r = ssh(f"mkdir -p ~/.config/mise/conf.d && f={shlex.quote(path)} && new=$(cat) && "
            f'{{ [ "$new" = "$(cat "$f" 2>/dev/null)" ] || {{ printf "%s\\n" "$new" > "$f" && ~/.local/bin/mise install --yes >/dev/null; }}; }} && '
            f"{defaults}", input=toml)
    if r.returncode:
        print(f"{name}: couldn't install tools {' '.join(tools)} on {BOX}: {r.stderr.strip()[-300:]}", file=sys.stderr)
    return tools


# --- data ------------------------------------------------------------------------
def cmd_data(name, rel, action):
    lroot = laptop_path(name)
    src = os.path.join(lroot, rel)
    dst = f"{BOX}:{BOX_HOME}/work/{name}/{rel}"
    moved = src + ".moved-to-box"
    if action == "copy":
        ensure_dir(dst + "/")
        subprocess.run(["rsync", "-a", "--info=progress2", src + "/", dst + "/"], check=True)
        print(f"Copied {rel} to the box (one way; the laptop copy is unchanged).")
    elif action == "move-to-box":
        if not os.path.isdir(src):
            sys.exit(f"{src} isn't here (already moved?)")
        print(f"Moving {rel} ({du(src)}) to the box. Stop anything using it first.")
        ensure_dir(dst + "/")
        subprocess.run(["rsync", "-a", "--info=progress2", src + "/", dst + "/"], check=True)
        # Verify byte-for-byte by comparing checksums of both sides.
        diff = subprocess.run(["rsync", "-anc", "--delete", "--itemize-changes", src + "/", dst + "/"],
                              capture_output=True, text=True).stdout.strip()
        if diff:
            sys.exit(f"Copies differ after the move, laptop copy left in place:\n{diff[:500]}")
        os.rename(src, moved)
        print(f"Done. Its home is now the box. The laptop copy was renamed to {moved} (delete it when happy).")
    elif action == "move-to-laptop":
        if os.path.exists(src):
            sys.exit(f"{src} already exists on the laptop; move or delete it first.")
        subprocess.run(["rsync", "-a", "--info=progress2", dst + "/", src + "/"], check=True)
        ssh(f"mv {shlex.quote(f'{BOX_HOME}/work/{name}/{rel}')} {shlex.quote(f'{BOX_HOME}/work/{name}/{rel}.moved-to-laptop')}")
        print(f"Done. Its home is the laptop again; the box copy was renamed to {rel}.moved-to-laptop.")
    else:
        sys.exit("action: copy | move-to-box | move-to-laptop")


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return
    cmd, args = argv[0], argv[1:]
    if cmd == "add":
        cmd_add(*args)
    elif cmd == "list":
        for n, p in sorted(load_registry().items()):
            print(f"{n}\t{p}")
    elif cmd == "get":
        print(json.dumps(manifest(laptop_path(args[0])).get(args[1])))
    elif cmd == "check":
        cmd_check(args[0])
    elif cmd == "sync":
        cmd_sync(args[0], quiet="--quiet" in args)
    elif cmd == "data":
        cmd_data(*args)
    elif cmd == "detect":
        cmd_detect(args[0])
    else:
        sys.exit(f"unknown command {cmd}")


if __name__ == "__main__":
    main(sys.argv[1:])
