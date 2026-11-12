"""Ctrl+V on the box pastes the laptop's clipboard images: clipboard.py (laptop, one per tab)
answers server/bin/agentbox-clipboard (the box's xclip) over a forwarded Unix socket."""
import importlib.machinery
import importlib.util
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import clipboard  # noqa: E402

_tl = importlib.machinery.SourceFileLoader("agentbox_tty", os.path.join(ROOT, "server", "bin", "agentbox-tty"))
tty_filter = importlib.util.module_from_spec(importlib.util.spec_from_loader(_tl.name, _tl))
_tl.exec_module(tty_filter)
_loader = importlib.machinery.SourceFileLoader("agentbox_clipboard", os.path.join(ROOT, "server", "bin", "agentbox-clipboard"))
box = importlib.util.module_from_spec(importlib.util.spec_from_loader(_loader.name, _loader))
_loader.exec_module(box)

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100_000   # bigger than one socket read


class FakeClipboard(clipboard.Clipboard):
    def __init__(self, png=None):
        super().__init__(k="none")
        self.png = png

    def targets(self):
        return ["image/png"] if self.png else []

    def get(self, mime):
        return self.png if mime == "image/png" else None


class Clipboard(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir="/tmp")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def laptop_tab(self, name, png):
        """A tab's clipboard.py serving at DIR/name (what ssh -R makes of it on the box)."""
        path = os.path.join(self.dir, name)
        threading.Thread(target=clipboard.serve, args=(path, FakeClipboard(png), os.getpid()), daemon=True).start()
        for _ in range(100):
            if os.path.exists(path):
                return path
            time.sleep(0.01)
        self.fail("the laptop side didn't start")

    def test_claude_codes_two_reads_get_the_laptops_image(self):
        self.laptop_tab("clip.k.a.sock", PNG)
        self.assertEqual(box.request("TARGETS", 5, self.dir), b"image/png\n")
        self.assertEqual(box.request("GET image/png", 5, self.dir), PNG)

    def test_no_image_and_text_are_answered_with_nothing(self):
        self.laptop_tab("clip.k.a.sock", None)
        self.assertIsNone(box.request("TARGETS", 5, self.dir))
        self.assertIsNone(box.request("GET image/png", 5, self.dir))
        self.assertEqual(clipboard.answer(b"GET text/plain", FakeClipboard(PNG)), b"NONE\n")
        self.assertEqual(clipboard.answer(b"garbage", FakeClipboard(PNG)), b"NONE\n")

    def test_a_tab_that_went_is_skipped_and_its_socket_removed(self):
        dead = os.path.join(self.dir, "clip.k.dead.sock")
        s = socket.socket(socket.AF_UNIX)
        s.bind(dead)
        s.close()   # the file stays, nobody listens: what a dropped connection leaves
        os.utime(dead, (time.time() + 60, time.time() + 60))   # and it is the newest
        self.laptop_tab("clip.k.live.sock", PNG)
        self.assertEqual(box.request("GET image/png", 5, self.dir), PNG)
        self.assertFalse(os.path.exists(dead))
        self.assertIsNone(box.request("TARGETS", 5, tempfile.mkdtemp(dir=self.dir)))   # no tab at all

    def test_the_tabs_of_the_session_come_first_then_the_newest(self):
        mine = box.session_key("blog+drafts")
        entries = [("clip.other.1.sock", 30), (f"clip.{mine}.1.sock", 10), (f"clip.{mine}.2.sock", 20),
                   ("clip.other.2.sock", 40), ("cache.json", 50)]
        self.assertEqual(box.candidates(entries, "blog+drafts"),
                         [f"clip.{mine}.2.sock", f"clip.{mine}.1.sock", "clip.other.2.sock", "clip.other.1.sock"])
        self.assertEqual(box.candidates(entries, None)[0], "clip.other.2.sock")

    def test_laptop_and_box_name_a_sessions_sockets_alike(self):
        path = clipboard.remote_socket("acme+triage", "laptop-12-30")
        self.assertEqual(os.path.basename(path),
                         f"clip.{box.session_key('acme+triage')}.laptop-12-30.sock")
        self.assertEqual(os.path.dirname(path), box.DIR)
        self.assertLess(len(clipboard.remote_socket("a" * 60, "some-laptop-name-99999-99999")), 108)

    def test_xclip_options_as_claude_code_passes_them(self):
        self.assertEqual(box.parse_xclip("-selection clipboard -t TARGETS -o".split()), ("clipboard", "TARGETS", True))
        self.assertEqual(box.parse_xclip("-selection clipboard -t image/png -o".split()), ("clipboard", "image/png", True))
        self.assertEqual(box.parse_xclip("-sel c -o".split()), ("clipboard", None, True))
        self.assertEqual(box.parse_xclip(["-o"]), ("primary", None, True))
        self.assertEqual(box.parse_xclip("-selection clipboard".split()), ("clipboard", None, False))   # a copy

    def test_the_laptop_serves_only_png_it_could_read(self):
        clip = clipboard.Clipboard(k="none")
        self.assertEqual(clip.targets(), [])
        self.assertIsNone(clip.get("image/png"))
        self.assertIsNone(clip.get("text/plain"))


class PasteBecomesCtrlV(unittest.TestCase):
    """The laptop terminal pastes instead of sending Ctrl+V: agentbox-tty hands an image paste
    on as Ctrl+V, everything else as it came."""
    S, E, V = tty_filter.START, tty_filter.END, tty_filter.CTRL_V

    def feed(self, *chunks, has_image=True):
        f = tty_filter.Filter(lambda: has_image)
        return b"".join(f.feed(c) for c in chunks) + f.flush()

    def test_an_empty_paste_is_ctrl_v_even_split_across_reads(self):
        self.assertEqual(self.feed(b"a" + self.S + self.E + b"b"), b"a" + self.V + b"b")
        self.assertEqual(self.feed(self.S[:4], self.S[4:] + self.E[:2], self.E[2:]), self.V)

    def test_a_copied_image_files_path_is_ctrl_v_when_the_laptop_has_the_image(self):
        for path in (rb"C:\Users\me\Pictures\shot.PNG", b'"C:\\my pics\\a.jpg"', b"/Users/me/Desktop/x.png"):
            self.assertEqual(self.feed(self.S + path + self.E), self.V, path)
            self.assertEqual(self.feed(self.S + path + self.E, has_image=False), self.S + path + self.E, path)

    def test_text_and_paths_on_the_box_are_pasted_as_they_are(self):
        for text in (b"hello", rb"C:\notes.txt", b"/etc/hostname.png two", b"a.png"):
            self.assertEqual(self.feed(self.S + text + self.E), self.S + text + self.E, text)
        with tempfile.NamedTemporaryFile(suffix=".png") as fh:   # an image that is on the box
            self.assertEqual(self.feed(self.S + fh.name.encode() + self.E), self.S + fh.name.encode() + self.E)
        long = self.S + b"x" * (tty_filter.MAX_PASTE + 10)
        self.assertEqual(self.feed(long, b"y" + self.E), long + b"y" + self.E)   # passed on as it came
        self.assertEqual(self.feed(b"\x1b[A", b"\x1b"), b"\x1b[A\x1b")   # keys pass, a held ESC is let go

    def test_the_command_runs_on_a_terminal_and_its_exit_status_comes_back(self):
        import pty
        tool = os.path.join(ROOT, "server", "bin", "agentbox-tty")
        child = ("import os,sys,tty; tty.setraw(0); d=b''\n"
                 "while not d.endswith(b'.'): d += os.read(0, 100)\n"
                 "os.write(1, repr(d).encode()); sys.exit(3)")
        pid, fd = pty.fork()
        if pid == 0:
            os.execv(sys.executable, [sys.executable, tool, sys.executable, "-c", child])
        time.sleep(1)
        os.write(fd, b"a" + self.S + self.E + b".")
        out = b""
        while True:
            try:
                chunk = os.read(fd, 1000)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        _, status = os.waitpid(pid, 0)
        self.assertIn(b"a\\x16.", out)
        self.assertEqual(os.waitstatus_to_exitcode(status), 3)


if __name__ == "__main__":
    unittest.main()
