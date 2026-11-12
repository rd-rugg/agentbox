#!/usr/bin/env python3
"""The boxes for Windows' own SSH, so T3 Code's Windows desktop app (BOX_T3=1) can connect to a
box by its name. WSL's ~/.ssh/config and keys are invisible to Windows' OpenSSH, so on a WSL
laptop agentbox keeps, in the Windows profile's .ssh folder:

- its own key, agentbox_ed25519 (made by Windows' ssh-keygen, so Windows' permissions are right;
  the WSL key is never copied: one copy of a secret is enough)
- one marked `Host BOX` block per box in config (no ControlMaster: Windows' OpenSSH lacks it)
- the box's host key in known_hosts, copied from WSL's on every sync, so a recreated box
  needs no prompt

Everything else in those files is the user's and is kept. Turning BOX_T3 off removes exactly
these blocks and lines (and the key once no block is left).

  winssh.py sync BOX HOST USER          key, Host block and host key for BOX; prints the public key
  winssh.py remove BOX                  BOX's block and host key; the key too when no block is left;
                                        prints the public key that boxes should stop accepting
"""
import fcntl
import os
import re
import shutil
import subprocess
import sys

KEY = "agentbox_ed25519"
MARK = "# agentbox: box sync writes this block (BOX_T3=1); edits here are replaced"


def profile():
    """The Windows profile as a WSL path: BOX_WINDOWS_PROFILE, else %USERPROFILE% through cmd.exe."""
    p = os.environ.get("BOX_WINDOWS_PROFILE")
    if p:
        return p
    if not shutil.which("cmd.exe") or not shutil.which("wslpath"):
        return None
    try:
        win = subprocess.run(["cmd.exe", "/c", "echo %USERPROFILE%"], capture_output=True, text=True,
                             timeout=20, cwd="/mnt/c").stdout.strip()
        return subprocess.run(["wslpath", "-u", win], capture_output=True, text=True, timeout=20).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def block(name, host, user, identity):
    return (f"Host {name}\n    {MARK}\n    HostName {host}\n    HostKeyAlias {name}\n    User {user}\n"
            f"    IdentityFile {identity}\n    IdentitiesOnly yes\n    ServerAliveInterval 30\n")


def _blocks(text):
    """The config split into (host, text) chunks; a chunk is a Host line and its indented lines."""
    chunks, cur = [], None
    for line in text.splitlines(keepends=True):
        m = re.match(r"Host\s+(\S+)\s*$", line)
        if m:
            cur = [m.group(1), line]
            chunks.append(cur)
        elif cur is not None and (line.startswith((" ", "\t")) or not line.strip()):
            cur[1] += line
        else:
            cur = None
            chunks.append([None, line])
    return chunks


def set_block(text, name, new):
    """Replace agentbox's block for name, or add it; the user's own blocks are never touched."""
    out, done = [], False
    for host, chunk in _blocks(text):
        if host == name and MARK in chunk:
            out.append(new if not done else "")
            done = True
        else:
            out.append(chunk)
    s = "".join(out)
    if not done:   # first: OpenSSH takes the first match, so an earlier `Host *` mustn't win
        s = new + ("\n" + s if s.strip() else "")
    return s


def drop_block(text, name):
    return "".join(chunk for host, chunk in _blocks(text) if not (host == name and MARK in chunk))


def has_blocks(text):
    return MARK in text


def set_host_key(text, name, keys):
    """known_hosts with name's plain lines replaced by keys ("type base64" each)."""
    lines = [l for l in text.splitlines() if l.split(" ", 1)[0] != name]
    lines += [f"{name} {k}" for k in keys]
    return "\n".join(lines) + "\n" if lines else ""


def wsl_host_keys(name):
    """The box's host keys from WSL's known_hosts ("type base64"), hashed entries included."""
    try:
        out = subprocess.run(["ssh-keygen", "-F", name], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    keys = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and not line.startswith("#"):
            keys.append(f"{parts[1]} {parts[2]}")
    return keys


def read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


def ensure_key(ssh_dir):
    """agentbox's key in the Windows .ssh folder, made with Windows' ssh-keygen; its public key."""
    key = os.path.join(ssh_dir, KEY)
    if not os.path.exists(key):
        os.makedirs(ssh_dir, exist_ok=True)
        win = subprocess.run(["wslpath", "-w", key], capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["ssh-keygen.exe", "-q", "-t", "ed25519", "-N", "", "-C", "agentbox-windows", "-f", win],
                       check=True, capture_output=True, timeout=60)
    return read(key + ".pub").strip()


def locked(fn):
    """Boxes open in parallel, each syncing: one winssh.py at a time edits the Windows files."""
    def run(*args):
        path = os.path.expanduser("~/.config/agentbox/winssh.lock")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return fn(*args)
    return run


@locked
def sync(name, host, user):
    prof = profile()
    if not prof:
        return None
    ssh_dir = os.path.join(prof, ".ssh")
    pub = ensure_key(ssh_dir)
    identity = "~/.ssh/" + KEY
    config = os.path.join(ssh_dir, "config")
    write(config, set_block(read(config), name, block(name, host, user, identity)))
    keys = wsl_host_keys(name)
    if keys:
        kh = os.path.join(ssh_dir, "known_hosts")
        write(kh, set_host_key(read(kh), name, keys))
    return pub


@locked
def remove(name):
    prof = profile()
    if not prof:
        return None
    ssh_dir = os.path.join(prof, ".ssh")
    config = os.path.join(ssh_dir, "config")
    text = read(config)
    if MARK not in text:
        return None
    write(config, drop_block(text, name))
    kh = os.path.join(ssh_dir, "known_hosts")
    if os.path.exists(kh):
        write(kh, set_host_key(read(kh), name, []))
    pub = read(os.path.join(ssh_dir, KEY + ".pub")).strip()
    if not has_blocks(read(config)):
        for f in (KEY, KEY + ".pub"):
            try:
                os.unlink(os.path.join(ssh_dir, f))
            except OSError:
                pass
    return pub or None


def main(argv):
    if len(argv) == 4 and argv[0] == "sync":
        pub = sync(*argv[1:])
        if pub:
            print(pub)
        return 0
    if len(argv) == 2 and argv[0] == "remove":
        pub = remove(argv[1])
        if pub:
            print(pub)
        return 0
    print(__doc__.split("\n\n")[-1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
