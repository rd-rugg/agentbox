"""Windows' own SSH reaching the boxes (winssh.py, BOX_T3=1 on WSL): agentbox's key, its marked
Host blocks and host keys in the Windows profile, and removing exactly those again. Every test
uses a temporary profile and stand-ins for both ssh-keygen programs."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import winssh  # noqa: E402

USER_CONFIG = "Host work\n    HostName work.example\n    User me\n\nHost agentbox-2\n    HostName mine.example\n"


class WindowsSsh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.profile = os.path.join(self.tmp, "profile")
        self.ssh = os.path.join(self.profile, ".ssh")
        os.makedirs(self.ssh)
        bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(bin_dir)
        # Windows' ssh-keygen makes the key; WSL's answers -F with a hashed known_hosts entry.
        self.stub(bin_dir, "ssh-keygen.exe", 'f="${@: -1}"; echo PRIVATE > "$f"; echo "ssh-ed25519 AAAAwin agentbox-windows" > "$f.pub"\n')
        self.stub(bin_dir, "ssh-keygen", '[ "$1" = -F ] && echo "|1|abc=|def= ssh-ed25519 AAAAbox"\n')
        self.stub(bin_dir, "wslpath", 'echo "${@: -1}"\n')   # the Windows form of a path: here, the same path
        self.env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "BOX_WINDOWS_PROFILE": self.profile}

    @staticmethod
    def stub(d, name, body):
        path = os.path.join(d, name)
        with open(path, "w") as fh:
            fh.write("#!/bin/bash\n" + body)
        os.chmod(path, 0o755)

    def run_winssh(self, *args):
        return subprocess.run([sys.executable, os.path.join(ROOT, "winssh.py"), *args], env=self.env,
                              capture_output=True, text=True)

    def read(self, name):
        with open(os.path.join(self.ssh, name)) as fh:
            return fh.read()

    def write(self, name, text):
        with open(os.path.join(self.ssh, name), "w") as fh:
            fh.write(text)

    def test_sync_adds_a_block_key_and_host_key_and_keeps_the_users_entries(self):
        self.write("config", USER_CONFIG)
        self.write("known_hosts", "github.com ssh-ed25519 AAAAgh\n")
        r = self.run_winssh("sync", "agentbox-3", "agentbox-3.tail.ts.net", "dev")
        self.assertEqual(r.stdout.strip(), "ssh-ed25519 AAAAwin agentbox-windows", r.stderr)
        config = self.read("config")
        self.assertIn("Host work\n    HostName work.example", config)
        self.assertIn("Host agentbox-2\n    HostName mine.example", config)   # the user's own, untouched
        self.assertIn("Host agentbox-3\n    " + winssh.MARK, config)
        self.assertIn("IdentityFile ~/.ssh/agentbox_ed25519", config)
        self.assertNotIn("ControlMaster", config)
        self.assertEqual(self.read("known_hosts"), "github.com ssh-ed25519 AAAAgh\nagentbox-3 ssh-ed25519 AAAAbox\n")

    def test_a_second_sync_replaces_only_agentboxs_block_and_host_key(self):
        self.run_winssh("sync", "agentbox-3", "old.ts.net", "dev")
        self.write("known_hosts", self.read("known_hosts") + "agentbox-3 ssh-ed25519 AAAAstale\n")
        self.run_winssh("sync", "agentbox-3", "new.ts.net", "dev")
        config = self.read("config")
        self.assertEqual(config.count("Host agentbox-3"), 1)
        self.assertIn("HostName new.ts.net", config)
        self.assertNotIn("old.ts.net", config)
        self.assertEqual(self.read("known_hosts"), "agentbox-3 ssh-ed25519 AAAAbox\n")   # a recreated box: no stale key

    def test_turning_it_off_removes_exactly_what_agentbox_added(self):
        self.write("config", USER_CONFIG)
        self.run_winssh("sync", "agentbox-3", "a.ts.net", "dev")
        self.run_winssh("sync", "agentbox-4", "b.ts.net", "dev")
        r = self.run_winssh("remove", "agentbox-3")
        self.assertEqual(r.stdout.strip(), "ssh-ed25519 AAAAwin agentbox-windows")   # for the box to stop accepting
        self.assertNotIn("Host agentbox-3", self.read("config"))
        self.assertTrue(os.path.exists(os.path.join(self.ssh, winssh.KEY)))   # agentbox-4 still uses it
        self.run_winssh("remove", "agentbox-4")
        self.assertEqual(self.read("config").strip(), USER_CONFIG.strip())
        self.assertFalse(os.path.exists(os.path.join(self.ssh, winssh.KEY)))
        self.assertNotIn("agentbox-", self.read("known_hosts"))

    def test_turning_it_off_without_a_prior_setup_touches_nothing(self):
        self.write("config", USER_CONFIG)
        r = self.run_winssh("remove", "agentbox-2")
        self.assertEqual(r.stdout, "")
        self.assertEqual(self.read("config"), USER_CONFIG)   # the user's own agentbox-2 block stays

    def test_no_windows_profile_means_nothing_to_do(self):
        env = {"PATH": "/usr/bin:/bin"}
        r = subprocess.run([sys.executable, os.path.join(ROOT, "winssh.py"), "sync", "agentbox-2", "h", "dev"],
                           env=env, capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
