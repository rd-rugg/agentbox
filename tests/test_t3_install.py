"""The T3 Code part of server/install.sh (BOX_T3), run on its own with stand-ins for t3,
systemctl, loginctl, sudo, curl and the box-side box: what it installs, when it restarts T3
Code's service, and what turning the setting off undoes."""
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKING = '{"pid": 7, "agent": "claude", "folder": "/home/dev/work/web", "working":true}'


def block():
    with open(os.path.join(ROOT, "server", "install.sh")) as fh:
        text = fh.read()
    start = text.index("# --- T3 Code's server")
    return text[start:text.index("# Every tool a project installed with mise", start)]


class T3Install(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.bin = os.path.join(self.home, "stubs")
        os.makedirs(self.bin)
        os.makedirs(os.path.join(self.home, ".config", "agentbox"))
        self.log = os.path.join(self.home, "calls")
        log = f'echo "$(basename "$0") $*" >> {self.log}'
        self.stub("t3", log)
        self.stub("systemctl", log + '\ncase "$*" in *is-enabled*|*is-active*) [ -f "$HOME/active" ];; esac')
        self.stub("loginctl", log + "\necho Linger=yes")
        self.stub("sudo", log)
        self.stub("curl", log + "\nexit 7")
        self.stub("box", log + '\n[ "$1" = --agents ] && [ -n "${AGENTS:-}" ] && echo "$AGENTS"; exit 0')
        os.symlink(os.path.join(ROOT, "server", "bin", "agentbox-t3"), os.path.join(self.bin, "agentbox-t3"))

    def stub(self, name, body):
        path = os.path.join(self.bin, name)
        with open(path, "w") as fh:
            fh.write("#!/bin/bash\n" + body + "\n")
        os.chmod(path, 0o755)

    def run_block(self, t3="1", **env):
        script = ("set -euo pipefail\nsay() { echo \"  $*\"; }\nSRC=" + os.path.join(ROOT, "server") +
                  "\nbox_env_values=(CHROME_PATH=$HOME/.local/bin/chromium)\n" + block())
        e = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": self.home, "USER": "dev", "BOX_T3": t3, **env}
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=e)
        calls = ""
        if os.path.exists(self.log):
            with open(self.log) as fh:
                calls = fh.read()
            os.remove(self.log)
        return r, calls

    def path(self, *parts):
        return os.path.join(self.home, *parts)

    def test_first_run_installs_the_wrapper_settings_and_service(self):
        r, calls = self.run_block()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.access(self.path(".local/share/agentbox/bin/claude"), os.X_OK))
        with open(self.path(".t3/service.env")) as fh:
            env = fh.read()
        self.assertIn("T3CODE_LAN=true", env)
        self.assertIn(f"CHROME_PATH={self.home}/.local/bin/chromium", env)
        self.assertIn("t3 service install", calls)
        self.assertTrue(os.path.exists(self.path(".config/agentbox/t3-installed")))

    def test_a_running_service_restarts_only_on_a_change_and_never_under_a_working_agent(self):
        self.run_block()
        open(self.path("active"), "w").close()
        r, calls = self.run_block()
        self.assertNotIn("restart", calls)                  # nothing changed
        with open(self.path(".config/agentbox/mcp-callback-port"), "w") as fh:
            fh.write("18742")
        r, calls = self.run_block(AGENTS=WORKING)          # changed, but an agent is working (compact JSON)
        self.assertNotIn("restart", calls)
        self.assertIn("apply at its next restart", r.stdout)
        with open(self.path(".config/agentbox/mcp-callback-port"), "w") as fh:
            fh.write("18743")
        r, calls = self.run_block()                         # changed, nobody working
        self.assertIn("systemctl --user restart t3code.service", calls)

    def test_a_failed_download_doesnt_stop_the_sync(self):
        os.remove(os.path.join(self.bin, "t3"))
        r, calls = self.run_block()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("didn't install; the next sync tries again", r.stdout)
        self.assertFalse(os.path.exists(self.path(".config/agentbox/t3-installed")))

    def test_turning_it_off_undoes_only_what_agentbox_set_up(self):
        self.run_block()
        r, calls = self.run_block(t3="0")
        self.assertIn("systemctl --user disable --now t3code.service", calls)
        self.assertFalse(os.path.exists(self.path(".local/share/agentbox/bin/claude")))
        self.assertFalse(os.path.exists(self.path(".config/agentbox/t3-installed")))
        self.assertTrue(os.path.exists(self.path(".t3/service.env")))      # the user's T3 Code data stays
        r, calls = self.run_block(t3="0")
        self.assertNotIn("disable", calls)                  # not set up by agentbox: left alone


if __name__ == "__main__":
    unittest.main()
