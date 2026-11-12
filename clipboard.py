#!/usr/bin/env python3
"""The laptop's clipboard images for the box: Ctrl+V in a box tab pastes them as it does here.

Claude Code on Linux reads a pasted image with `xclip` from the clipboard of the machine it
runs on. On the box that `xclip` is server/bin/agentbox-clipboard, which asks the laptop:
every tab (tabs.sh --connect) runs `clipboard.py serve SOCKET` and forwards a socket on the
box (/run/agentbox/clip.*.sock) to it. Only images are served: text already pastes through
the terminal, and clipboard text is where passwords are. Nothing is kept: each request reads
the clipboard again.

Protocol (one request per connection): the box sends `TARGETS\\n` or `GET <type>\\n`; the
laptop answers `OK\\n` and the data (the image types on offer, one per line, or the image
bytes), or `NONE\\n` when there is no image.

  clipboard.py serve SOCKET [PID]   answer on SOCKET until PID (the tab) goes
  clipboard.py remote SESSION TAG   the socket path on the box for a tab of SESSION
  clipboard.py targets|get    what the box would get, for testing on the laptop
"""
import base64
import hashlib
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.parse

IMAGE_TYPES = ("image/png",)       # every reader converts to PNG, which Claude Code asks for first
MAX_BYTES = 50 * 1024 * 1024       # bigger than any screenshot; a guard against a runaway reader
TIMEOUT = 20                       # a reader that hangs (a busy clipboard owner) gives up


def run(cmd):
    """stdout of cmd, or None when it failed or isn't there."""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def kind():
    """Which clipboard this laptop has: wsl (Windows'), mac, wayland, x11, or None."""
    if sys.platform == "darwin":
        return "mac"
    if "microsoft" in platform.release().lower() or os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop"):
        return "wsl"
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste"):
        return "wayland"
    if os.environ.get("DISPLAY") and shutil.which("xclip"):
        return "x11"
    return None


def powershell():
    return shutil.which("powershell.exe") or "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"


IMAGE_FILE = r"\.(png|jpe?g|gif|bmp)$"   # copied image files the laptop converts to PNG

# Windows' clipboard through PowerShell, as Claude Code itself reads it on WSL: an image, or a
# copied image file (Explorer), as PNG, base64 on stdout (binary through a pipe from Windows
# would be mangled).
PS_IMAGE = ("Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
            "$i = $null; if ([System.Windows.Forms.Clipboard]::ContainsImage()) { $i = [System.Windows.Forms.Clipboard]::GetImage() } "
            "elseif ([System.Windows.Forms.Clipboard]::ContainsFileDropList()) { "
            "foreach ($f in [System.Windows.Forms.Clipboard]::GetFileDropList()) { "
            "if ($f -match '" + IMAGE_FILE + "') { $i = [System.Drawing.Image]::FromFile($f); break } } }; "
            "if ($null -eq $i) { exit 1 }; ")
PS_HAS = PS_IMAGE + "exit 0"
PS_GET = PS_IMAGE + ("$ms = New-Object System.IO.MemoryStream; $i.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png); "
                     "[Convert]::ToBase64String($ms.ToArray())")


def mac_file():
    """A copied image file in Finder (its path), or None."""
    out = run(["osascript", "-e", "POSIX path of (the clipboard as «class furl»)"])
    path = out.decode().strip() if out else ""
    return path if re.search(IMAGE_FILE, path, re.I) and os.path.isfile(path) else None


def linux_file(k):
    """A copied image file in the file manager (text/uri-list), as long as it is a PNG, or None."""
    cmd = (["wl-paste", "--no-newline", "--type", "text/uri-list"] if k == "wayland"
           else ["xclip", "-selection", "clipboard", "-t", "text/uri-list", "-o"])
    for line in (run(cmd) or b"").decode(errors="replace").splitlines():
        path = urllib.parse.unquote(line.strip()[7:]) if line.startswith("file://") else ""
        if path.lower().endswith(".png") and os.path.isfile(path):
            return path
    return None


def read_file(path):
    try:
        with open(path, "rb") as fh:
            return fh.read(MAX_BYTES + 1)
    except OSError:
        return None


def has_image(k):
    if k == "wsl":
        return run([powershell(), "-NoProfile", "-NonInteractive", "-Sta", "-Command", PS_HAS]) is not None
    if k == "mac":
        return run(["osascript", "-e", "the clipboard as «class PNGf»"]) is not None or mac_file() is not None
    if k == "wayland":
        return b"image/png" in (run(["wl-paste", "--list-types"]) or b"").split() or linux_file(k) is not None
    if k == "x11":
        return (b"image/png" in (run(["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"]) or b"").split()
                or linux_file(k) is not None)
    return False


def image(k):
    """The clipboard image (or copied image file) as PNG bytes, or None."""
    if k == "wsl":
        out = run([powershell(), "-NoProfile", "-NonInteractive", "-Sta", "-Command", PS_GET])
        try:
            return base64.b64decode(out.replace(b"\r", b"").strip(), validate=False) if out else None
        except ValueError:
            return None
    if k == "mac":
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            src = mac_file()
            if src:   # sips (macOS) converts any image file to PNG
                ok = run(["sips", "-s", "format", "png", src, "--out", path]) is not None
            else:
                ok = run(["osascript", "-e", "set png_data to (the clipboard as «class PNGf»)",
                          "-e", f'set fp to open for access POSIX file "{path}" with write permission',
                          "-e", "write png_data to fp", "-e", "close access fp"]) is not None
            return read_file(path) if ok else None
        finally:
            os.unlink(path)
    if k in ("wayland", "x11"):
        if k == "wayland":
            data = run(["wl-paste", "--no-newline", "--type", "image/png"])
        else:
            data = run(["xclip", "-selection", "clipboard", "-t", "image/png", "-o"])
        if data:
            return data
        src = linux_file(k)
        return read_file(src) if src else None
    return None


def remote_socket(session, tag):
    """Where a tab of session forwards its socket on the box. The box's agentbox-clipboard
    prefers the tabs of the session it runs in, by the same short key."""
    return f"/run/agentbox/clip.{hashlib.sha1(session.encode()).hexdigest()[:10]}.{tag}.sock"


class Clipboard:
    """This laptop's clipboard, images only."""
    def __init__(self, k=None):
        self.kind = k if k is not None else kind()

    def targets(self):
        return list(IMAGE_TYPES) if has_image(self.kind) else []

    def get(self, mime):
        if mime not in IMAGE_TYPES:
            return None
        data = image(self.kind)
        return data if data and len(data) <= MAX_BYTES and data.startswith(b"\x89PNG") else None


def answer(request, clip):
    """The reply to one request line (bytes, without its newline)."""
    words = request.decode("ascii", "replace").split()
    if words == ["TARGETS"]:
        t = clip.targets()
        return b"OK\n" + "".join(x + "\n" for x in t).encode() if t else b"NONE\n"
    if len(words) == 2 and words[0] == "GET":
        data = clip.get(words[1])
        return b"OK\n" + data if data else b"NONE\n"
    return b"NONE\n"


def handle(conn, clip):
    with conn:
        conn.settimeout(10)
        line = b""
        try:
            while not line.endswith(b"\n") and len(line) < 256:
                chunk = conn.recv(256)
                if not chunk:
                    break
                line += chunk
            conn.settimeout(None)
            conn.sendall(answer(line.strip(), clip))
        except OSError:
            pass


def serve(path, clip=None, parent=None):
    """Answer on the Unix socket at path (only this user can connect) until parent (a pid) exits."""
    clip = clip or Clipboard()
    old = os.umask(0o077)
    try:
        if os.path.exists(path):
            os.unlink(path)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
    finally:
        os.umask(old)
    srv.listen(8)
    srv.settimeout(5)
    try:
        while True:
            if parent and not alive(parent):
                return
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            threading.Thread(target=handle, args=(conn, clip), daemon=True).start()
    finally:
        srv.close()
        try:
            os.unlink(path)
        except OSError:
            pass


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def main(argv):
    if len(argv) >= 2 and argv[0] == "serve":
        serve(argv[1], parent=int(argv[2]) if len(argv) > 2 else os.getppid())
        return 0
    if len(argv) == 3 and argv[0] == "remote":
        print(remote_socket(argv[1], argv[2]))
        return 0
    clip = Clipboard()
    if argv == ["targets"]:
        print(f"clipboard: {clip.kind or 'none found'}; images: {' '.join(clip.targets()) or 'none'}")
        return 0
    if argv == ["get"]:
        data = clip.get("image/png")
        print(f"{len(data)} bytes of PNG" if data else "no image")
        return 0 if data else 1
    print(__doc__.split("\n\n")[-1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
