"""T3 Code on a box (BOX_T3=1): agentbox's block in T3 Code's service.env, the claude wrapper that
gives T3 Code's agents the tabs' Claude sign-in, and the setting's default."""
import importlib.machinery
import importlib.util
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_loader = importlib.machinery.SourceFileLoader("agentbox_t3", os.path.join(ROOT, "server", "bin", "agentbox-t3"))
t3 = importlib.util.module_from_spec(importlib.util.spec_from_loader(_loader.name, _loader))
_loader.exec_module(t3)


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    os.chmod(path, mode)


class ServiceEnv(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, ".t3", "service.env")

    def read(self):
        with open(self.path) as fh:
            return fh.read()

    def test_the_block_holds_literal_values_and_the_file_is_private(self):
        self.assertTrue(t3.write_env(self.path, [("CHROME_PATH", "/home/dev/.local/bin/chromium"), ("T3CODE_LAN", "true")]))
        text = self.read()
        self.assertIn("CHROME_PATH=/home/dev/.local/bin/chromium\n", text)
        self.assertIn("T3CODE_LAN=true\n", text)
        self.assertNotIn("$HOME", text)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_other_lines_survive_and_owned_keys_move_into_the_block(self):
        write(self.path, "T3CODE_PORT=3773\nT3CODE_LAN=false\n# my note\n")
        t3.write_env(self.path, [("T3CODE_LAN", "true")])
        text = self.read()
        self.assertIn("T3CODE_PORT=3773", text)
        self.assertIn("# my note", text)
        self.assertNotIn("T3CODE_LAN=false", text)
        self.assertEqual(text.count("T3CODE_LAN="), 1)

    def test_a_second_run_changes_nothing(self):
        pairs = [("MCP_OAUTH_CALLBACK_PORT", "18742"), ("T3CODE_LAN", "true")]
        self.assertTrue(t3.write_env(self.path, pairs))
        before = self.read()
        self.assertFalse(t3.write_env(self.path, pairs))
        self.assertEqual(self.read(), before)

    def test_a_dropped_variable_leaves_the_block(self):
        t3.write_env(self.path, [("CHROME_PATH", "/x"), ("T3CODE_LAN", "true")])
        t3.write_env(self.path, [("T3CODE_LAN", "true")])
        self.assertNotIn("CHROME_PATH", self.read())

    def test_values_with_spaces_are_quoted(self):
        t3.write_env(self.path, [("A", "two words")])
        self.assertIn('A="two words"', self.read())


class ClaudeWrapper(unittest.TestCase):
    """The wrapper finds the real claude after itself on PATH and adds the default token only
    when the caller has none and the box has no login of its own."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.wrap = os.path.join(self.home, ".local/share/agentbox/bin")
        self.bin = os.path.join(self.home, ".local/bin")
        os.makedirs(self.wrap)
        shutil.copy2(os.path.join(ROOT, "server", "bin", "agentbox-claude"), os.path.join(self.wrap, "claude"))
        write(os.path.join(self.bin, "claude"), '#!/bin/sh\necho "token=${CLAUDE_CODE_OAUTH_TOKEN:-none} args=$*"\n', 0o755)
        write(os.path.join(self.bin, "secrets"), '#!/bin/sh\n[ "$*" = "--get cli CLAUDE_TOKEN_DEFAULT" ] && echo from-ram\n', 0o755)

    def run_claude(self, *args, **env):
        e = {"HOME": self.home, "PATH": f"{self.wrap}:{self.bin}:/usr/bin:/bin", **env}
        return subprocess.run([os.path.join(self.wrap, "claude"), *args], env=e, capture_output=True, text=True).stdout.strip()

    def test_a_box_without_its_own_login_gets_the_default_token(self):
        self.assertEqual(self.run_claude("-p", "hi there"), "token=from-ram args=-p hi there")

    def test_a_token_already_set_is_kept(self):
        self.assertEqual(self.run_claude("x", CLAUDE_CODE_OAUTH_TOKEN="tab"), "token=tab args=x")

    def test_a_box_with_its_own_login_gets_no_token(self):
        write(os.path.join(self.home, ".claude/.credentials.json"), '{"claudeAiOauth": {"accessToken": "t"}}')
        self.assertEqual(self.run_claude("x"), "token=none args=x")


class Setting(unittest.TestCase):
    def test_t3_is_off_by_default_and_reaches_install_sh(self):
        script = 'source "$1/lib.sh"; echo "$BOX_T3"; declare -f sync_config | grep -c "BOX_T3=%q"'
        out = subprocess.run(["bash", "-c", script, "_", ROOT], capture_output=True, text=True,
                             env={"PATH": os.environ["PATH"], "HOME": tempfile.gettempdir()}).stdout.split()
        self.assertEqual(out, ["0", "1"])


if __name__ == "__main__":
    unittest.main()
