"""scripts/publish.sh and its gates, offline: temporary repositories (a bare "public" and a bare
"dev" one), a temporary deny list and a stand-in gitleaks. Nothing reaches GitHub."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import publish_gates as gates  # noqa: E402

DENIED = "acmeprivateclient"          # stands for a private name in the deny list
IDENTITY = ("tester", "1+tester@users.noreply.github.com")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class GateLogic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.deny = os.path.join(self.tmp, "deny")
        write(self.deny, "# a comment\n\nAcmePrivateClient\nre:\\bXYZ\\b\n/home/someone\n")

    def test_denylist_entries_are_numbered_and_case_insensitive(self):
        pats = gates.load_denylist(self.deny)
        self.assertEqual([n for n, _ in pats], [1, 2, 3])
        self.assertEqual(gates.scan_text("see ACMEPRIVATECLIENT here", pats), [1])
        self.assertEqual(gates.scan_text("the XYZ team", pats), [2])
        self.assertEqual(gates.scan_text("XYZZY", pats), [])          # the regex keeps its word boundary
        self.assertEqual(gates.scan_text("cd /home/someone/src", pats), [3])

    def test_missing_or_empty_denylist_refuses(self):
        with self.assertRaises(SystemExit):
            gates.load_denylist(os.path.join(self.tmp, "nope"))
        write(self.deny, "# only comments\n")
        with self.assertRaises(SystemExit):
            gates.load_denylist(self.deny)

    def test_ignore_patterns(self):
        pats = gates.ignore_patterns("# comment\ndocs/plans/\nvideo/build/\n*.local.md\nNOTES.txt\n")
        self.assertTrue(gates.ignored("docs/plans/a.md", pats))
        self.assertTrue(gates.ignored("video/build/out.mp4", pats))
        self.assertTrue(gates.ignored("notes.local.md", pats))
        self.assertTrue(gates.ignored("NOTES.txt", pats))
        self.assertFalse(gates.ignored("docs/guide.md", pats))
        self.assertFalse(gates.ignored("video/script.md", pats))

    def test_identity(self):
        pats = gates.load_denylist(self.deny)
        self.assertEqual(gates.identity_problems(*IDENTITY, pats), [])
        self.assertTrue(gates.identity_problems("tester", "tester@example.com", pats))
        self.assertTrue(gates.identity_problems("AcmePrivateClient", IDENTITY[1], pats))
        self.assertTrue(gates.identity_problems("", "", pats))


class PublishFlow(unittest.TestCase):
    """The real scripts in a throwaway development clone, publishing to a local bare repository."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        t = self.tmp
        self.env = dict(os.environ, HOME=t, GIT_CONFIG_NOSYSTEM="1", XDG_CONFIG_HOME=os.path.join(t, "cfg"),
                        PUBLISH_DENYLIST=os.path.join(t, "deny"), PUBLISH_CHECKS="true",
                        PUBLISH_GITLEAKS=os.path.join(t, "gitleaks"))
        for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            self.env.pop(k, None)
        write(self.env["PUBLISH_DENYLIST"], f"{DENIED}\n")
        # A stand-in gitleaks: reports a leak when a file holds the marker FAKELEAK.
        write(self.env["PUBLISH_GITLEAKS"], "#!/bin/sh\nif grep -rq FAKELEAK . 2>/dev/null; then echo 'Finding: REDACTED'; exit 1; fi\nexit 0\n")
        os.chmod(self.env["PUBLISH_GITLEAKS"], 0o755)
        self.public = os.path.join(t, "public.git")
        self.devbare = os.path.join(t, "dev.git")
        self.dev = os.path.join(t, "dev")
        self.git(t, "init", "-q", "--bare", "-b", "main", self.public)
        self.git(t, "init", "-q", "--bare", "-b", "main", self.devbare)
        self.git(t, "clone", "-q", self.public, self.dev)
        self.ident(self.dev)
        write(os.path.join(self.dev, "README.md"), "hello\n")
        self.git(self.dev, "add", "-A")
        self.git(self.dev, "commit", "-q", "-m", "start")
        self.git(self.dev, "push", "-q", "origin", "main")
        self.git(self.dev, "remote", "rename", "origin", "public")
        self.git(self.dev, "remote", "add", "origin", self.devbare)
        for f in ("publish.sh", "publish_gates.py"):
            dst = os.path.join(self.dev, "scripts", f)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(ROOT, "scripts", f), dst)
        write(os.path.join(self.dev, ".publishignore"), "docs/plans/\n")
        write(os.path.join(self.dev, "docs/plans/private-plan.md"), f"talk to {DENIED}\n")
        self.version("0.1.0")
        self.git(self.dev, "add", "-A")
        self.git(self.dev, "commit", "-q", "-m", f"private notes about {DENIED}")
        self.git(self.dev, "push", "-q", "origin", "main")

    def git(self, cwd, *args, check=True):
        r = subprocess.run(["git", *args], cwd=cwd, env=self.env, capture_output=True, text=True)
        if check and r.returncode:
            raise AssertionError(f"git {args}: {r.stderr}")
        return r

    def ident(self, repo):
        self.git(repo, "config", "user.name", IDENTITY[0])
        self.git(repo, "config", "user.email", IDENTITY[1])

    def version(self, v):
        write(os.path.join(self.dev, ".claude-plugin/plugin.json"), f'{{"name": "x", "version": "{v}"}}\n')

    def publish(self, *args):
        return subprocess.run([os.path.join(self.dev, "scripts/publish.sh"), *args], cwd=self.dev, env=self.env,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def public_main(self):
        return self.git(self.public, "rev-parse", "main").stdout.strip()

    def test_dry_run_changes_nothing_and_passes(self):
        before = self.public_main()
        r = self.publish()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("✔ deny list", r.stdout)
        self.assertIn("nothing was changed", r.stdout)
        self.assertNotIn("docs/plans", r.stdout.split("Gates:")[0])     # left out of the release
        self.assertEqual(self.public_main(), before)

    def test_denied_string_is_refused_without_showing_it(self):
        write(os.path.join(self.dev, "notes.md"), f"from {DENIED.upper()}\n")
        self.git(self.dev, "add", "-A")
        self.git(self.dev, "commit", "-q", "-m", "notes")
        r = self.publish()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("notes.md: deny list entry 1", r.stdout)
        self.assertNotIn(DENIED.lower(), (r.stdout + r.stderr).lower())

    def test_output_never_shows_denied_text(self):
        # A file named after the denied string, and the dev remote's own address holding it.
        write(os.path.join(self.dev, f"{DENIED}-notes.md"), "plain\n")
        self.git(self.dev, "add", "-A")
        self.git(self.dev, "commit", "-q", "-m", "named notes")
        r = self.publish()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("[private]-notes.md", r.stdout)
        self.assertNotIn(DENIED, (r.stdout + r.stderr).lower())

    def test_gitleaks_finding_is_refused(self):
        write(os.path.join(self.dev, "config.txt"), "token FAKELEAK\n")
        self.git(self.dev, "add", "-A")
        self.git(self.dev, "commit", "-q", "-m", "config")
        r = self.publish()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("✘ secrets", r.stdout)

    def test_push_publishes_one_clean_commit_and_records_it(self):
        start = self.public_main()
        r = self.publish("--push", "-m", "first release", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        head = self.public_main()
        self.assertEqual(self.git(self.public, "rev-parse", f"{head}^").stdout.strip(), start)
        files = self.git(self.public, "ls-tree", "-r", "--name-only", head).stdout.split()
        self.assertNotIn("docs/plans/private-plan.md", files)
        self.assertIn("scripts/publish.sh", files)
        log = self.git(self.public, "log", "-1", "--format=%an|%ae|%cn|%ce|%s", head).stdout.strip()
        self.assertEqual(log, f"{IDENTITY[0]}|{IDENTITY[1]}|{IDENTITY[0]}|{IDENTITY[1]}|0.1.0: first release")
        # Recorded in the development history: the public commit is an ancestor of HEAD.
        self.git(self.dev, "merge-base", "--is-ancestor", head, "HEAD")
        self.git(self.dev, "rev-parse", "published/0.1.0")
        # The same version again is refused; nothing new is "nothing to publish".
        r = self.publish()
        self.assertIn("nothing to publish", r.stdout)
        write(os.path.join(self.dev, "more.md"), "more\n")
        self.git(self.dev, "add", "-A")
        self.git(self.dev, "commit", "-q", "-m", "more")
        r = self.publish()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("was published already", r.stdout)

    def test_first_publish_to_an_empty_repository_has_no_history(self):
        empty = os.path.join(self.tmp, "fresh.git")
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", empty)
        self.git(self.dev, "remote", "set-url", "public", empty)
        r = self.publish()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("none yet", r.stdout)
        r = self.publish("--push", "-m", "first release", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        head = self.git(empty, "rev-parse", "main").stdout.strip()
        self.assertEqual(self.git(empty, "rev-list", "--count", head).stdout.strip(), "1")   # no parent
        self.assertEqual(self.git(empty, "log", "-1", "--format=%s", head).stdout.strip(), "0.1.0: first release")
        self.assertNotIn("docs/plans/private-plan.md", self.git(empty, "ls-tree", "-r", "--name-only", head).stdout)
        self.git(self.dev, "merge-base", "--is-ancestor", head, "HEAD")   # recorded here
        self.assertIn("nothing to publish", self.publish().stdout)   # the next publish builds on it
        self.assertIn("Nothing new", self.publish("--pull").stdout)

    def test_personal_identity_is_refused(self):
        self.git(self.dev, "config", "user.email", "tester@example.com")
        r = self.publish()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("noreply", r.stdout)

    def test_public_contributions_must_be_pulled_first(self):
        self.assertEqual(self.publish("--push", "-m", "first", "--yes").returncode, 0)
        other = os.path.join(self.tmp, "contributor")
        self.git(self.tmp, "clone", "-q", self.public, other)
        self.ident(other)
        write(os.path.join(other, "CONTRIB.md"), "thanks\n")
        self.git(other, "add", "-A")
        self.git(other, "commit", "-q", "-m", "a contribution")
        self.git(other, "push", "-q", "origin", "main")
        self.version("0.2.0")
        self.git(self.dev, "commit", "-q", "-am", "next")
        r = self.publish()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--pull", r.stdout)
        r = self.publish("--pull")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.dev, "CONTRIB.md")))
        r = self.publish()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("✔ public branch", r.stdout)

    def test_pre_push_guard_refuses_a_manual_push(self):
        r = self.publish("--install-hooks")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        before = self.public_main()
        # The dev branch's own history carries the denied string (a commit message, docs/plans).
        r = self.git(self.dev, "push", "public", "HEAD:main", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("push refused", r.stdout + r.stderr)
        self.assertNotIn(DENIED, r.stdout + r.stderr)
        self.assertEqual(self.public_main(), before)
        # A push to the private remote isn't checked.
        self.assertEqual(self.git(self.dev, "push", "-q", "origin", "HEAD:refs/heads/side", check=False).returncode, 0)


if __name__ == "__main__":
    unittest.main()
