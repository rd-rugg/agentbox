"""Offline checks for the parts that are easy to get subtly wrong (no box needed).

  python3 -m unittest discover -s tests
"""
import asyncio
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import projects  # noqa: E402
import importlib.machinery, importlib.util  # noqa: E402
_loader = importlib.machinery.SourceFileLoader("box_secrets", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server", "secrets"))
box_secrets = importlib.util.module_from_spec(importlib.util.spec_from_loader("box_secrets", _loader))
_loader.exec_module(box_secrets)
_loader = importlib.machinery.SourceFileLoader("agentbox_mcp", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server", "bin", "agentbox-mcp"))
agentbox_mcp = importlib.util.module_from_spec(importlib.util.spec_from_loader("agentbox_mcp", _loader))
_loader.exec_module(agentbox_mcp)
import mcp_sync  # noqa: E402
import readiness  # noqa: E402
import agent_setup  # noqa: E402
import sessions  # noqa: E402
import pool  # noqa: E402
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Rewrite(unittest.TestCase):
    swap = [("/home/me/p", "/home/dev/work/p"), ("-home-me-p", "-home-dev-work-p")]

    def run_on(self, text):
        return sessions.rewrite(text.encode(), self.swap).decode()

    def test_whole_names_only(self):
        self.assertEqual(self.run_on('"/home/me/p/x.py" "/home/me/p2/y" "/home/me/p-wt"'),
                         '"/home/dev/work/p/x.py" "/home/me/p2/y" "/home/me/p-wt"')

    def test_after_json_escape_and_before_full_stop(self):
        self.assertEqual(self.run_on(r'"x:\n/home/me/p/.a" "in /home/me/p. Next" "\t-home-me-p/1"'),
                         r'"x:\n/home/dev/work/p/.a" "in /home/dev/work/p. Next" "\t-home-dev-work-p/1"')

    def test_extension_is_part_of_the_name(self):
        self.assertEqual(self.run_on('"/home/me/p.git" "an/home/me/p"'), '"/home/me/p.git" "an/home/me/p"')

    def test_binary_passes_through(self):
        data = b"\xff\xfe/home/me/p"
        self.assertEqual(sessions.rewrite(data, self.swap), data)


class Pair(unittest.TestCase):
    def test_layouts(self):
        L, B = "/l/p", "/home/dev/work/p"
        src = [{"path": L, "branch": "main"}, {"path": "/l/p-feat", "branch": "feat"},
               {"path": L + "/.claude/worktrees/x", "branch": "x"}, {"path": L + "/.worktrees/y.z", "branch": "y"},
               {"path": "/l/det", "branch": None}]
        dst = [{"path": B, "branch": "main"}, {"path": B + "/.claude/worktrees/zz", "branch": "feat"},
               {"path": B + "/.claude/worktrees/x", "branch": "other"}]
        got = [(p.dst, p.create) for p in sessions.pair(src, dst, B)]
        self.assertEqual(got, [(B, False), (B + "/.claude/worktrees/zz", False),
                               (B + "/.claude/worktrees/x-2", True), (B + "/.claude/worktrees/y-z", True)])

    def test_owner(self):
        self.assertEqual(sessions.owner("/a/b/c", ["/a", "/a/b"]), "/a/b")
        self.assertIsNone(sessions.owner("/a2", ["/a"]))


class Commands(unittest.TestCase):
    def test_segments_and_wrappers(self):
        got = readiness.commands_in("cd x && AWS_PROFILE=u timeout 30 aws s3 ls | head; sudo terraform init\n"
                                    "$(git rev-parse HEAD) || nohup uv run y")
        self.assertEqual(got, ["cd", "aws", "head", "terraform", "git", "uv"])

    def test_heredoc_body_is_skipped(self):
        self.assertEqual(readiness.commands_in("python3 - <<'EOF'\nimport os\nprint(1)\nEOF\nls"), ["python3", "ls"])

    def test_usage_reads_bash_calls_and_paths(self):
        u = readiness.Usage(["/l/p"])
        line = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "aws sts get-caller-identity"}},
            {"type": "text", "text": "see /l/p/.superpowers/run/STATE.md and /l/p2/x"}]}}
        u.feed((json.dumps(line) + "\n").encode())
        self.assertEqual(u.commands["aws"], 1)
        self.assertEqual(dict(u.paths), {("/l/p", ".superpowers/run/STATE.md"): 1})


class Choice(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(sessions.parse_choice("3 1,2-2", 5), [0, 1, 2])
        self.assertEqual(sessions.parse_choice("", 5), [])
        for bad in ("0", "6", "x", "4-2"):
            with self.assertRaises(ValueError):
                sessions.parse_choice(bad, 5)

    def test_state(self):
        self.assertEqual(sessions.state(["a"], None), "not there")
        self.assertEqual(sessions.state(["a", "b"], [0, ["a"]]), "newer here")
        self.assertEqual(sessions.state(["a"], [0, ["a", "c"]]), "newer there")
        self.assertEqual(sessions.state(["a", "b"], [0, ["a", "c"]]), "continued on both sides")


class MissingFiles(unittest.TestCase):
    def test_paths_in_any_checkout_no_env_files_outside_links_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            main, wt = os.path.join(d, "p"), os.path.join(d, "p", ".claude", "worktrees", "w")
            for rel in ("data", "backend", "run"):
                os.makedirs(os.path.join(main, rel))
            os.makedirs(wt)
            with open(os.path.join(main, "backend", ".env"), "w") as fh:
                fh.write("A=1\n")
            os.symlink("/usr", os.path.join(main, "ext"))
            places = [sessions.Place(main, "/b/p", "main", False), sessions.Place(wt, "/b/p/.claude/worktrees/w", "w", False)]
            u = readiness.Usage([main, wt])
            u.feed(f"{main}/data/x.csv {main}/backend/.env {main}/ext/lib".encode())
            box, unt = readiness.box_run, readiness.untracked
            readiness.box_run = lambda script: "\n".join(re.findall(r"echo '?([^'\n]+)'?", script))  # all absent
            readiness.untracked = lambda root: {"data", "backend", "ext"}
            try:
                got = {(os.path.relpath(s, main), dst, nbytes is None)
                       for s, dst, n, nbytes in readiness.missing_files(u, places)}
                envs = readiness.mentioned_env_files(u)
            finally:
                readiness.box_run, readiness.untracked = box, unt
            self.assertEqual(got, {("data", "/b/p/data", False), ("ext", "/b/p/ext", True)})
            self.assertEqual(envs, {(main, "backend/.env")})


class AgentSetup(unittest.TestCase):
    def test_stage_mirrors_laptop_plus_overlay(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as ov:
            def put(base, rel, text=""):
                os.makedirs(os.path.dirname(os.path.join(base, rel)), exist_ok=True)
                with open(os.path.join(base, rel), "w") as fh:
                    fh.write(text)
            put(home, ".claude/CLAUDE.md", "laptop rules")
            put(home, ".claude/skills/mine/SKILL.md", "x")
            put(home, ".agents/skills/agentbox/SKILL.md", "laptop-only agentbox skill")
            put(home, ".agents/skills/shared/SKILL.md", "y")
            put(ov, "claude/CLAUDE.md", "box rules")
            put(ov, "claude/skills/box-extra/SKILL.md", "z")
            out, origin = agent_setup.stage("claude codex", home=home, overlay=ov)
            try:
                staged = agent_setup.hashes(out, [r for r, _, _ in agent_setup.ROOTS])
                self.assertEqual(sorted(staged), [".agents/skills/shared/SKILL.md", ".claude/CLAUDE.md",
                                                  ".claude/skills/box-extra/SKILL.md", ".claude/skills/mine/SKILL.md"])
                with open(os.path.join(out, ".claude/CLAUDE.md")) as fh:
                    text = fh.read()
                self.assertTrue(text.startswith("laptop rules") and text.endswith("box rules"))
                self.assertEqual(origin[".claude/skills/box-extra/SKILL.md"], os.path.join(ov, "claude/skills/box-extra/SKILL.md"))
            finally:
                shutil.rmtree(out)

    def test_changed_on_box(self):
        manifest = {"a": "1", "b": "2", "c": "3"}
        box = {"a": "1", "b": "edited", "c": "3", "new": "9"}
        staged = {"a": "1", "b": "2", "c": "changed-on-laptop"}
        self.assertEqual(agent_setup.changed_on_box(manifest, box, staged), {"b", "new"})
        self.assertEqual(agent_setup.changed_on_box(None, box, staged), {"new"})   # first sync: box-only files

    @unittest.skipUnless(shutil.which("rsync"), "rsync isn't installed")
    def test_sync_deletes_only_what_it_pushed(self):
        # The real sync, with a stand-in `ssh` that runs the box side in a throwaway "box" home.
        # Links a tool made on the box (to folders outside the synced tree) must survive.
        with tempfile.TemporaryDirectory() as laptop, tempfile.TemporaryDirectory() as box, \
                tempfile.TemporaryDirectory() as ov, tempfile.TemporaryDirectory() as tools:
            def put(base, rel, text="x"):
                os.makedirs(os.path.dirname(os.path.join(base, rel)), exist_ok=True)
                with open(os.path.join(base, rel), "w") as fh:
                    fh.write(text)
            with open(os.path.join(tools, "ssh"), "w") as fh:
                fh.write('#!/bin/sh\nwhile [ "${1#-}" != "$1" ]; do case $1 in -o|-l|-p) shift 2 ;; *) shift ;; esac; done\n'
                         'shift\ncd "$FAKE_BOX_HOME" && HOME="$FAKE_BOX_HOME" exec sh -c "$*"\n')
            os.chmod(os.path.join(tools, "ssh"), 0o755)
            put(laptop, ".claude/skills/mine/SKILL.md")
            put(laptop, ".claude/skills/old/SKILL.md")
            put(laptop, ".agents/skills/shared/SKILL.md")
            stage = agent_setup.stage
            env = {"PATH": f"{tools}:{os.environ['PATH']}", "FAKE_BOX_HOME": box}
            with mock.patch.dict(os.environ, env), mock.patch.object(agent_setup.mcp_sync, "sync"), \
                    mock.patch.object(agent_setup, "stage", lambda agents: stage(agents, home=laptop, overlay=ov)):
                with mock.patch("sys.stdout", new_callable=io.StringIO):
                    agent_setup.sync("claude codex", quiet=True)
                self.assertTrue(os.path.isfile(os.path.join(box, ".claude/skills/old/SKILL.md")))
                put(box, "work/skills-repo/skills/design/SKILL.md", "linked")
                links = [".claude/skills/team-design", ".agents/skills/team-design", ".agents/skills/team-review"]
                for rel in links:
                    os.symlink(os.path.join(box, "work/skills-repo/skills/design"), os.path.join(box, rel))
                put(box, ".claude/skills/copied/SKILL.md")                  # added on the box
                put(box, ".claude/skills/mine/SKILL.md", "edited on the box")
                os.makedirs(os.path.join(box, ".claude/skills/empty"))
                os.remove(os.path.join(laptop, ".claude/skills/old/SKILL.md"))
                os.rmdir(os.path.join(laptop, ".claude/skills/old"))
                with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                    agent_setup.sync("claude codex", quiet=True)
            for rel in links:
                self.assertTrue(os.path.islink(os.path.join(box, rel)), rel)
                self.assertIn(rel, out.getvalue())
            self.assertEqual(open(os.path.join(box, ".agents/skills/team-design/SKILL.md")).read(), "linked")
            self.assertTrue(os.path.isfile(os.path.join(box, ".claude/skills/copied/SKILL.md")))
            self.assertEqual(open(os.path.join(box, ".claude/skills/mine/SKILL.md")).read(), "edited on the box")
            self.assertTrue(os.path.isdir(os.path.join(box, ".claude/skills/empty")))
            self.assertFalse(os.path.exists(os.path.join(box, ".claude/skills/old")))   # pushed, then gone from the laptop
            self.assertTrue(os.path.isfile(os.path.join(box, ".agents/skills/shared/SKILL.md")))


class SecretLayouts(unittest.TestCase):
    entries = [["MYAPP__DATABASE_URL", "prefixed", "dev"], ["MYAPP__FILE__CREDS", "{}", "dev"],
               ["DATABASE_URL", "from-project", "My App"], ["STRIPE_KEY", "sk", "My App"],
               ["OTHER__X", "1", ""], ["PLAIN", "p", ""]]

    def test_prefix_and_project_merge_prefix_wins(self):
        env = box_secrets.app_env(self.entries, "my-app", write_files=False)
        self.assertEqual(env["STRIPE_KEY"], "sk")                 # from the project "My App"
        env2 = box_secrets.app_env(self.entries, "myapp", write_files=False)
        self.assertEqual(env2["DATABASE_URL"], "prefixed")       # prefix only, project name differs
        self.assertIn("CREDS", env2)                              # FILE__ marker handled
        merged = box_secrets.app_env([["MY_APP__DATABASE_URL", "prefixed", ""]] + self.entries, "my-app", write_files=False)
        self.assertEqual(merged["DATABASE_URL"], "prefixed")     # both: the prefixed one wins

    def test_apps_lists_prefixes_and_projects(self):
        self.assertEqual(box_secrets.apps(self.entries), ["dev", "my-app", "myapp", "other"])


class Detect(unittest.TestCase):
    def test_package_json_scripts(self):
        self.assertEqual(projects.CALLS.findall('"deploy": "aws s3 sync out s3://b && gcloud run deploy"'), ["aws", "gcloud"])

    def test_tools_and_env(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", d], check=True)
            os.makedirs(os.path.join(d, "infra"))
            for rel, text in (("infra/main.tf", ""), ("deploy.sh", "set -e\naws s3 sync out s3://b\n  gcloud run deploy x\n"),
                              (".env", "A=1\nexport B=2\n"), (".env.example", "A=\n")):
                with open(os.path.join(d, rel), "w") as fh:
                    fh.write(text)
            subprocess.run(["git", "-C", d, "add", "infra", "deploy.sh", ".env.example"], check=True)
            tools, envs = projects.detect(d)
            self.assertEqual(tools, {"terraform", "aws-cli", "gcloud"})
            self.assertEqual(envs, [".env"])
            self.assertEqual(projects.env_names(os.path.join(d, ".env")), {"A", "B"})


class Permissions(unittest.TestCase):
    BOX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server", "bin", "box")

    def save(self, **env):
        with tempfile.TemporaryDirectory() as home:
            r = subprocess.run(["bash", self.BOX, "--permissions"], capture_output=True, text=True,
                               env={"PATH": os.environ["PATH"], "HOME": home, **env})
            path = os.path.join(home, ".config/agentbox/permissions")
            return (open(path).read() if os.path.exists(path) else None), r.stderr

    def test_saved_with_aliases_and_fallback(self):
        saved, err = self.save(BOX_CLAUDE_PERMISSIONS="bypassPermissions", BOX_CODEX_PERMISSIONS="yolo")
        self.assertEqual(saved, "BOX_CLAUDE_PERMISSIONS=bypass\nBOX_CODEX_PERMISSIONS=auto\nBOX_REMOTE_CONTROL=0\n")
        self.assertIn("BOX_CODEX_PERMISSIONS=yolo isn't a mode", err)
        self.assertIn("BOX_CLAUDE_PERMISSIONS=manual", self.save(BOX_CLAUDE_PERMISSIONS="default")[0])

    def test_nothing_set_keeps_the_file(self):
        self.assertIsNone(self.save()[0])
        with tempfile.TemporaryDirectory() as home:
            env = {"PATH": os.environ["PATH"], "HOME": home}
            subprocess.run(["bash", self.BOX, "--permissions"], env={**env, "BOX_CLAUDE_PERMISSIONS": "bypass"})
            subprocess.run(["bash", self.BOX, "--permissions"], env=env)
            self.assertIn("=bypass", open(os.path.join(home, ".config/agentbox/permissions")).read())



class EnvQuoting(unittest.TestCase):
    """Values in an env_files .env reach compose, python-dotenv, bash and `secret` unchanged where
    they can (compose itself was checked by hand: it read all of these exactly)."""
    PLAIN = ["p$q`id`\"s #t u", "a\\b", "", " spaced "]           # single-quoted as is
    QUOTED = ["it's"]                                              # double-quoted as is
    ESCAPED = ["x\\\\y", "one\ntwo", "tail\\", "a'b$c\\d\"e\nf"]   # double-quoted with escapes

    def write(self, values):
        fd, path = tempfile.mkstemp(suffix=".env")
        with os.fdopen(fd, "w") as fh:
            fh.writelines(f"K{i}={box_secrets.dotenv_quote(v)}\n" for i, v in enumerate(values))
        self.addCleanup(os.unlink, path)
        return path

    def test_forms(self):
        for v in self.PLAIN:
            self.assertEqual(box_secrets.dotenv_quote(v), f"'{v}'")
        for v in self.QUOTED:
            self.assertEqual(box_secrets.dotenv_quote(v), f'"{v}"')
        self.assertEqual(box_secrets.dotenv_quote("one\ntwo"), '"one\\ntwo"')   # one line, as before
        self.assertEqual(box_secrets.dotenv_quote(self.ESCAPED[3]), '"a\'b\\$c\\\\d\\"e\\nf"')

    def test_one_line_each(self):
        with open(self.write(self.PLAIN + self.QUOTED + self.ESCAPED)) as fh:
            self.assertEqual(len(fh.read().splitlines()), len(self.PLAIN + self.QUOTED + self.ESCAPED))

    def test_bash_source(self):
        values = self.PLAIN + self.QUOTED + [self.ESCAPED[0], self.ESCAPED[2]]   # bash keeps `\n` as is
        path = self.write(values)
        script = f"set -a; . {shlex.quote(path)}; " + "; ".join(f'printf "%s\\0" "$K{i}"' for i in range(len(values)))
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env={"PATH": os.environ["PATH"]}).stdout
        self.assertEqual(out.split("\0")[:-1], values)

    def test_secret_tool_style(self):
        # how the workspace `secret` tool reads a line: one pair of outer quotes stripped, nothing else
        values = self.PLAIN + self.QUOTED
        with open(self.write(values)) as fh:
            self.assertEqual([line.rstrip("\n").split("=", 1)[1][1:-1] for line in fh], values)

    @unittest.skipUnless(importlib.util.find_spec("dotenv"), "python-dotenv isn't installed")
    def test_python_dotenv_defaults(self):
        import dotenv
        values = self.PLAIN + self.QUOTED + self.ESCAPED[:2]   # what it can read exactly
        got = dotenv.dotenv_values(self.write(values))
        self.assertEqual([got[f"K{i}"] for i in range(len(values))], values)

    def test_caveats(self):
        for v in self.PLAIN[1:] + self.QUOTED + self.ESCAPED[:2]:
            self.assertIsNone(box_secrets.dotenv_caveat(v), v)
        self.assertIn("${...}", box_secrets.dotenv_caveat("x${HOME}"))
        self.assertIn("python-dotenv", box_secrets.dotenv_caveat("tail\\"))
        self.assertIn("python-dotenv", box_secrets.dotenv_caveat("it's $HOME"))
        self.assertIn("backticks", box_secrets.dotenv_caveat("it's `x`"))


class SecretSources(unittest.TestCase):
    """secrets refuses a name with two different values; the documented precedence still holds."""

    def env(self, entries, app="api"):
        return box_secrets.app_env(entries, app, write_files=False)

    def test_prod_has_its_own_app(self):
        entries = [["API__DB", "dev-url", "dev"], ["API_PROD__DB", "prod-url", "prod"], ["CLI__GH", "t", "master"]]
        self.assertEqual(self.env(entries), {"DB": "dev-url"})
        self.assertEqual(self.env(entries, "api_prod"), {"DB": "prod-url"})

    def test_same_prefixed_name_in_two_projects(self):
        with self.assertRaises(SystemExit) as e:
            self.env([["API__DB", "dev-url", "dev"], ["API__DB", "other-url", "shared"]])
        self.assertIn("DB", str(e.exception))
        self.assertIn("dev and shared", str(e.exception))
        self.assertNotIn("url", str(e.exception))                     # names only, never values

    def test_identical_copies_and_prefix_over_project(self):
        entries = [["API__URL", "same", "dev"], ["API__URL", "same", "master"],   # equal copies: fine
                   ["DB", "from-project", "api"], ["API__DB", "prefixed", "dev"],   # prefixed wins over
                   ["TOKEN", "t", "api"]]                                        # the app's project
        self.assertEqual(self.env(entries), {"URL": "same", "DB": "prefixed", "TOKEN": "t"})

    def test_same_name_twice_in_the_app_project(self):
        with self.assertRaises(SystemExit) as e:
            self.env([["DB", "a", "api"], ["DB", "b", "api"]])
        self.assertIn("(project api)", str(e.exception))

    def test_two_prefixed_values_over_a_project_value(self):
        with self.assertRaises(SystemExit):   # which API__DB would win is a guess
            self.env([["DB", "dev-url", "api"], ["API__DB", "a", "dev"], ["API__DB", "b", "shared"]])

    def test_file_secret_and_plain_name_clash(self):
        with self.assertRaises(SystemExit) as e:
            self.env([["API__FILE__CREDS", "{a}", "dev"], ["API__CREDS", "path", "dev"]])
        self.assertIn("CREDS", str(e.exception))

    def test_dev_names_in_a_production_project(self):
        for project in ("prod", "Production", "prod-eu", "eu-prod"):
            with self.assertRaises(SystemExit) as e:
                self.env([["API__STRIPE_KEY", "live", project]])
            self.assertIn("API_PROD__STRIPE_KEY", str(e.exception))
        self.assertEqual(self.env([["API__X", "1", "products"]]), {"X": "1"})   # not a production project

    def test_get_checks_only_the_name_asked_for(self):
        entries = [["CLI__GH_TOKEN", "t", "master"], ["CLI__RAILWAY", "a", "master"], ["CLI__RAILWAY", "b", "dev"]]
        self.assertEqual(box_secrets.app_env(entries, "cli", write_files=False, only={"GH_TOKEN"})["GH_TOKEN"], "t")
        with self.assertRaises(SystemExit):
            box_secrets.app_env(entries, "cli", write_files=False, only={"RAILWAY"})

    def test_suggested_names_are_valid(self):
        with self.assertRaises(SystemExit) as e:
            self.env([["MY_APP__DB", "a", "dev"], ["MY_APP__DB", "b", "master"]], app="my-app")
        self.assertIn("MY_APP_PROD__NAME", str(e.exception))


class ProdNames(unittest.TestCase):
    """env_to_bitwarden.py names production secrets APP_PROD__ the way `secrets` reads them."""

    def test_same_rules_as_secrets(self):
        import env_to_bitwarden
        self.assertEqual(env_to_bitwarden.PROD_PROJECTS.pattern, box_secrets.PROD_PROJECTS.pattern)
        for app in ("api", "my-app", "Web_2"):
            self.assertEqual(env_to_bitwarden.prefix_of(app), box_secrets.prefix_of(app))

    def test_secret_app(self):
        import env_to_bitwarden
        cases = [("api", "prod", "api_prod"), ("api", "dev", "api"), ("my-app", "production", "my-app_prod"),
                 ("api_prod", "prod", "api_prod"), ("api-prod", "prod-eu", "api-prod"), ("api", "products", "api")]
        for app, project, want in cases:
            self.assertEqual(env_to_bitwarden.secret_app(app, project), want, (app, project))
        self.assertEqual(env_to_bitwarden.secret_app("api", "prod", keep_names=True), "api")
        self.assertEqual(env_to_bitwarden.prefix_of(env_to_bitwarden.secret_app("my-app", "prod")), "MY_APP_PROD__")


class Placement(unittest.TestCase):
    """A box takes a new project only if what its projects need leaves room (pool.room_mb)."""
    GB = 1024

    def test_idle_box_with_projects_is_full(self):
        # a 4 GB box with 2 projects of 1.5 GB and 5 sessions: free memory alone would say yes
        load = {"mem_total_mb": 3800, "mem_avail_mb": 2600, "sessions": 5}
        self.assertLess(pool.room_mb(load, [1.5 * self.GB, 1.5 * self.GB]), 1.5 * self.GB)

    def test_empty_box_offers_its_ram(self):
        load = {"mem_total_mb": 7800, "mem_avail_mb": 7500, "sessions": 0}
        self.assertEqual(pool.room_mb(load, []), 7800 - pool.reserve_mb())

    def test_free_memory_caps_the_promise(self):
        load = {"mem_total_mb": 7800, "mem_avail_mb": 900, "sessions": 1}
        self.assertEqual(pool.room_mb(load, [1.5 * self.GB]), 900)

    def test_extra_sessions_count(self):
        load = {"mem_total_mb": 7800, "mem_avail_mb": 7000, "sessions": 4}
        one = pool.room_mb(dict(load, sessions=1), [1.5 * self.GB])
        self.assertAlmostEqual(one - pool.room_mb(load, [1.5 * self.GB]), 3 * pool.SESSION_GB * self.GB)

    def test_older_box_without_session_count(self):
        load = {"mem_total_mb": 3800, "mem_avail_mb": 3500}
        self.assertEqual(pool.room_mb(load, [1.5 * self.GB]), 3800 - pool.reserve_mb() - 1.5 * self.GB)


class PlacePlan(unittest.TestCase):
    """pool.plan_place: a running box only if every estimate on it plus the new project fits;
    else a closed box, a new one, or "full"; --box overrides. No side effects."""
    LOAD = {"mem_total_mb": 3819, "mem_avail_mb": 2700, "sessions": 1}
    NEED = 1536

    def plan(self, boxes, loads=None, needs=None, **kw):
        view = {n: {"state": s, "projects": list(ps)} for n, (s, ps) in boxes.items()}
        needs = needs or {p: self.NEED for _, ps in boxes.values() for p in ps}
        loads = loads if loads is not None else {n: dict(self.LOAD) for n, (s, _) in boxes.items() if s == "up"}
        kw.setdefault("max_boxes", 3)
        return pool.plan_place("web", view, loads, needs, self.NEED, **kw)

    def test_running_box_with_room(self):
        p = self.plan({"agentbox": ("down", []), "agentbox-2": ("up", ["acme"])})
        self.assertEqual((p["action"], p["box"]), ("use", "agentbox-2"))

    def test_t3_codes_server_takes_room_on_every_box(self):
        boxes = {"agentbox": ("down", []), "agentbox-2": ("up", ["acme"])}
        with mock.patch.dict(os.environ, {"BOX_T3": "0"}):
            self.assertEqual(self.plan(boxes)["box"], "agentbox-2")
        with mock.patch.dict(os.environ, {"BOX_T3": "1"}):
            p = self.plan(boxes)
        self.assertEqual((p["action"], p["box"]), ("open", "agentbox"))   # agentbox-2 is full with the reserve

    def test_free_memory_isnt_capacity(self):
        # the report: agentbox-3 had 2.7 GB free, but its four projects are promised more than it has
        p = self.plan({"agentbox": ("down", ["x"]), "agentbox-2": ("up", ["r1", "r2"]),
                       "agentbox-3": ("up", ["a", "b", "c", "d"])})
        self.assertEqual((p["action"], p["box"]), ("open", "agentbox"))
        text = "\n".join(p["reasons"])
        self.assertIn("agentbox-3: 6.0 GB promised of 3.2", text)
        self.assertIn("--box BOX", text)

    def test_closed_box_with_fewest_projects_then_new_then_full(self):
        full = {"agentbox": ("up", ["a", "b"])}
        self.assertEqual(self.plan({**full, "agentbox-2": ("down", ["x", "y"]), "agentbox-3": ("down", [])})["box"], "agentbox-3")
        p = self.plan(full, next_name="agentbox-2")
        self.assertEqual((p["action"], p["box"]), ("create", "agentbox-2"))
        p = self.plan({**full, "agentbox-2": ("up", ["c", "d"])}, max_boxes=2)
        self.assertEqual(p["action"], "full")
        self.assertIn("--box BOX", p["reasons"][-1])

    def test_policy_pack_and_spread(self):
        boxes = {"agentbox": ("up", []), "agentbox-2": ("up", ["a"])}
        self.assertEqual(self.plan(boxes)["box"], "agentbox-2")                  # pack: the fuller one
        self.assertEqual(self.plan(boxes, policy="spread")["box"], "agentbox")

    def test_unreadable_load_isnt_room(self):
        p = self.plan({"agentbox": ("up", []), "agentbox-2": ("down", [])}, loads={"agentbox": None})
        self.assertEqual((p["action"], p["box"]), ("open", "agentbox-2"))
        self.assertIn("agentbox: its load couldn't be read", p["reasons"])

    def test_placed_project_stays(self):
        self.assertEqual(self.plan({"agentbox": ("up", ["web"])})["action"], "use")
        p = self.plan({"agentbox": ("down", ["web"]), "agentbox-2": ("up", [])})
        self.assertEqual((p["action"], p["box"]), ("open", "agentbox"))

    def test_box_option(self):
        boxes = {"agentbox": ("up", ["a", "b"]), "agentbox-2": ("down", [])}
        self.assertEqual((self.plan(boxes, on="agentbox")["action"]), "use")       # full, but chosen
        self.assertEqual((self.plan(boxes, on="agentbox-2")["action"]), "open")
        self.assertEqual(self.plan(boxes, on="nope")["action"], "error")
        p = self.plan({"agentbox": ("up", ["web"]), "agentbox-2": ("up", [])}, on="agentbox-2")
        self.assertEqual(p["action"], "error")
        self.assertIn("box move web --to agentbox-2", p["reasons"][0])

    def test_single_box(self):
        self.assertEqual(self.plan({"agentbox": ("up", ["a", "b", "c"])}, max_boxes=1)["action"], "use")
        p = self.plan({"agentbox": ("down", [])}, max_boxes=1)
        self.assertEqual((p["action"], p["box"]), ("open", "agentbox"))
        self.assertNotIn("room", " ".join(p["reasons"]))
        self.assertEqual(self.plan({}, max_boxes=1)["action"], "create")


class Gather(unittest.TestCase):
    """pool.gather reads loads only when they decide, and estimates by registered name."""

    def test_names_and_when_loads_are_read(self):
        boxes = {"agentbox": {"box": "agentbox", "state": "up", "labels": {"p.myapp": "1"}, "id": 1}}
        read = []
        saved = pool.members, pool.loads, pool.need_mb, pool.projects.load_registry, pool.MAX_BOXES
        pool.members = lambda: boxes
        pool.loads = lambda names: read.append(names) or {n: None for n in names}
        pool.need_mb = lambda name: {"MyApp": 9216}.get(name, 1536)
        pool.projects.load_registry = lambda: {"MyApp": "/x/MyApp"}
        pool.MAX_BOXES = 3
        try:
            _, _, _, needs = pool.gather("web")
            self.assertEqual(needs, {"myapp": 9216})        # the label's name maps back to the registry's
            pool.gather("web", on="agentbox")
            pool.gather("myapp")
            self.assertEqual(read, [["agentbox"]])           # --box and a placed project read no loads
        finally:
            pool.members, pool.loads, pool.need_mb, pool.projects.load_registry, pool.MAX_BOXES = saved


class MovePlan(unittest.TestCase):
    """pool.plan_move: copy, place (nothing to carry) or error; only the boxes it needs open."""
    UP, DOWN = "up", "down"

    def plan(self, source, target, files, registered=True, next_name=""):
        src = source and {"box": source[0], "state": source[1]}
        dst = target and {"box": target[0], "state": target[1]}
        return pool.plan_move("web", src, dst, files, registered, next_name)

    def test_copy_between_running_boxes(self):
        p = self.plan(("agentbox-2", self.UP), ("agentbox-3", self.UP), "yes")
        self.assertEqual((p["action"], p["open"], p["create"]), ("copy", [], ""))

    def test_no_files_on_the_source_is_a_place(self):
        p = self.plan(("agentbox-2", self.UP), ("agentbox-3", self.DOWN), "no")
        self.assertEqual((p["action"], p["open"]), ("place", ["agentbox-3"]))
        self.assertEqual(self.plan(("agentbox-2", self.UP), ("agentbox-3", self.UP), "no", registered=False)["action"], "error")

    def test_unreachable_source_stops(self):
        p = self.plan(("agentbox-2", self.UP), ("agentbox-3", self.UP), "unreachable")
        self.assertEqual(p["action"], "error")
        self.assertIn("Couldn't reach agentbox-2", p["reasons"][0])

    def test_closed_source_opens_only_when_needed(self):
        p = self.plan(("agentbox", self.DOWN), ("agentbox-3", self.UP), "unknown")
        self.assertEqual((p["action"], p["open"]), ("copy", ["agentbox"]))
        p = self.plan(None, ("agentbox-3", self.UP), "unknown")                   # on no box: never opens one
        self.assertEqual((p["action"], p["open"]), ("place", []))

    def test_new_target_and_errors(self):
        p = self.plan(("agentbox", self.UP), None, "yes", next_name="agentbox-4")
        self.assertEqual((p["action"], p["create"]), ("copy", "agentbox-4"))
        p = pool.plan_move("web", {"box": "agentbox", "state": "up"}, None, "yes", True, "", to_new=True)
        self.assertIn("limit", p["reasons"][0])
        self.assertEqual(self.plan(("agentbox", self.UP), None, "yes")["action"], "error")
        self.assertEqual(self.plan(("agentbox", self.UP), ("agentbox", self.UP), "yes")["action"], "error")


class Migration(unittest.TestCase):
    """Projects from before pools (registered, or only a folder on the first box) are
    labelled onto the configured pool's first box, each step once."""

    def run_migrate(self, registry, labels_anywhere=(), first="down", folders=None, pool_name="agentbox",
                    configured="agentbox", marker_dir=None):
        boxes = {pool_name: {"box": pool_name, "state": first, "labels": {"app": pool_name}, "id": 9}} if first else {}
        calls = []
        saved = (pool.POOL, pool.projects.load_registry, pool.configured_pool, pool._PLACED, pool.set_label,
                 pool.projects.CONF, pool.first_box_folders)
        pool.POOL, pool._PLACED = pool_name, set(labels_anywhere)
        pool.projects.load_registry = lambda: {n: f"/x/{n}" for n in registry}
        pool.configured_pool = lambda: configured
        pool.set_label = lambda box, label, value=None: calls.append((box["box"], label))
        pool.projects.CONF = marker_dir
        pool.first_box_folders = lambda name: folders
        try:
            return pool.migrate(boxes), calls
        finally:
            (pool.POOL, pool.projects.load_registry, pool.configured_pool, pool._PLACED, pool.set_label,
             pool.projects.CONF, pool.first_box_folders) = saved

    def test_registered_ones_then_folders_each_once(self):
        with tempfile.TemporaryDirectory() as d:
            # closed first box: the registered ones go onto its snapshot; folders wait for it to be up
            moved, calls = self.run_migrate(["old", "placed"], labels_anywhere={"p.placed"}, marker_dir=d)
            self.assertEqual((moved, calls), (["old"], [("agentbox", "p.old")]))
            # up: its folders without a label anywhere (a box-only project), not the registry again
            moved, _ = self.run_migrate(["old", "new"], first="up", folders=["tool", "placed"],
                                        labels_anywhere={"p.placed", "p.old"}, marker_dir=d)
            self.assertEqual(moved, ["tool"])
            self.assertEqual(self.run_migrate(["new"], first="up", folders=["late"], marker_dir=d), ([], []))

    def test_an_unreachable_first_box_is_tried_again(self):
        with tempfile.TemporaryDirectory() as d:
            self.run_migrate([], first="up", folders=None, marker_dir=d)
            self.assertEqual(self.run_migrate([], first="up", folders=["tool"], marker_dir=d)[0], ["tool"])

    def test_other_pools_and_no_first_box(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_migrate(["old"], pool_name="zztest", marker_dir=d), ([], []))
            self.assertEqual(self.run_migrate(["old"], first=None, marker_dir=d), ([], []))
            self.assertEqual(self.run_migrate(["old"], marker_dir=d), ([], []))       # nothing left to do

    def test_where_is_the_label_only(self):
        boxes = {"agentbox": {"box": "agentbox", "state": "up", "labels": {}},
                 "agentbox-2": {"box": "agentbox-2", "state": "up", "labels": {"p.web": "1"}}}
        self.assertEqual(pool.where("web", boxes)["box"], "agentbox-2")
        self.assertIsNone(pool.where("old", boxes))

    def test_configured_pool_from_box_conf(self):
        with tempfile.TemporaryDirectory() as d:
            conf = os.path.join(d, "box.conf")
            for text, want in (("", "agentbox"), ('BOX_NAME="work-2"\n', "work"), ("BOX_POOL=team\nBOX_NAME=x\n", "team")):
                with open(conf, "w") as fh:
                    fh.write(text)
                saved = os.environ.get("BOX_CONF")
                os.environ["BOX_CONF"] = conf
                try:
                    self.assertEqual(pool.configured_pool(), want)
                finally:
                    os.environ.pop("BOX_CONF") if saved is None else os.environ.__setitem__("BOX_CONF", saved)


class BoxScript(unittest.TestCase):
    """server/bin/box's login check, closed-tab bookkeeping and auto-mode fallback, with a
    throwaway HOME (and tmux server, and a stand-in `claude`).

    Every tmux call, the script's too, goes to the test's own socket (-S HOME/tmux.sock) with
    TMUX unset: tmux ignores a TMUX_TMPDIR that doesn't exist (a temp HOME already removed)
    and falls back to the default socket, and with TMUX set it uses that server. Either way a
    test once reached and killed the real tmux server of the machine running it."""
    BOX = Permissions.BOX
    TMUX = shutil.which("tmux")

    @staticmethod
    def socket(home):
        return os.path.join(home, "tmux.sock")

    def run_box(self, home, *args, path=None):
        bin_dir = os.path.join(home, "tmux-bin")
        if not os.path.exists(bin_dir):            # a `tmux` that only ever reaches this test's socket
            os.makedirs(bin_dir)
            with open(os.path.join(bin_dir, "tmux"), "w") as fh:
                fh.write(f'#!/bin/sh\nunset TMUX\nexec {shlex.quote(self.TMUX)} -S {shlex.quote(self.socket(home))} "$@"\n'
                         if self.TMUX else "#!/bin/sh\nexit 127\n")
            os.chmod(os.path.join(bin_dir, "tmux"), 0o755)
        env = {"PATH": f"{bin_dir}:{path or os.environ['PATH']}", "HOME": home, "TMUX_TMPDIR": home}
        return subprocess.run(["bash", self.BOX, *args], capture_output=True, text=True, env=env,
                              stdin=subprocess.DEVNULL).stdout

    def own_tmux(self, home):
        """Start a tmux server on this test's socket; returns a stop() that kills that server
        only: it refuses when the socket answers with a pid other than the one started here."""
        sock = self.socket(home)
        self.assertFalse(os.path.exists(sock), "a tmux server this test didn't start is on its socket")
        env = {"PATH": os.environ["PATH"], "HOME": home}   # no TMUX, no TMUX_TMPDIR

        def tmux(*args, check=True):
            return subprocess.run([self.TMUX, "-S", sock, *args], env=env, capture_output=True, text=True, check=check)
        tmux("new-session", "-d", "-s", "main", "sleep 60")
        pid = tmux("display-message", "-p", "-t", "=main", "#{pid}").stdout.strip()

        def stop():
            answer = tmux("display-message", "-p", "#{pid}", check=False).stdout.strip() if os.path.exists(sock) else ""
            if answer and answer != pid:
                raise AssertionError(f"tmux on {sock} is pid {answer}, not the {pid} this test started; not killed")
            if answer:
                tmux("kill-server", check=False)
        return tmux, stop

    @unittest.skipUnless(shutil.which("git"), "git isn't installed")
    def test_worktrees_outside_a_project_are_listed_and_go_with_its_files(self):
        with tempfile.TemporaryDirectory() as home:
            repo = os.path.join(home, "work", "web")
            env = {**os.environ, "HOME": home, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "t",
                   "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
            def git(*a):
                subprocess.run(["git", *a], cwd=repo, env=env, check=True, capture_output=True)
            os.makedirs(repo)
            git("init", "-q")
            git("commit", "-q", "--allow-empty", "-m", "start")
            git("worktree", "add", "-q", os.path.join(home, ".t3", "worktrees", "web", "feature"))
            git("worktree", "add", "-q", os.path.join(repo, ".claude", "worktrees", "inside"))
            self.assertEqual(self.run_box(home, "--worktrees", "web").split(), [".t3/worktrees/web/feature"])
            os.makedirs(os.path.join(home, ".codex", "sessions"))
            with open(os.path.join(home, ".codex", "sessions", "s.jsonl"), "w") as fh:
                fh.write(json.dumps({"cwd": repo}, separators=(",", ":")) + "\n")   # compact, as Codex writes it
            files = self.run_box(home, "--files", "web").split()
            self.assertIn(".t3/worktrees/web/feature", files)
            self.assertIn(".codex/sessions/s.jsonl", files)   # past folders without transcripts too

    def test_claude_login_ignores_mcp_sign_ins(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(self.run_box(home, "--claude-login").strip(), "no")
            os.makedirs(os.path.join(home, ".claude"))
            path = os.path.join(home, ".claude", ".credentials.json")
            with open(path, "w") as fh:
                json.dump({"mcpOAuth": {"plugin:x|1": {"accessToken": ""}}}, fh)
            self.assertEqual(self.run_box(home, "--claude-login").strip(), "no")
            with open(path, "w") as fh:
                json.dump({"claudeAiOauth": {"accessToken": "made-up"}, "mcpOAuth": {}}, fh)
            self.assertEqual(self.run_box(home, "--claude-login").strip(), "yes")

    @unittest.skipUnless(shutil.which("tmux"), "tmux isn't installed")
    def test_closed_tabs_stay_in_the_background(self):
        with tempfile.TemporaryDirectory() as home:
            tmux, stop = self.own_tmux(home)
            try:                                  # stopped while its socket's folder still exists
                for name in ("web", "api+x1"):
                    tmux("new-session", "-d", "-s", name, "sleep 60")
                tmux("kill-session", "-t", "=main")
                self.assertEqual(self.run_box(home, "--tab-sessions").split(), ["api+x1", "web"])
                self.run_box(home, "--tab-closed", "api+x1")
                self.run_box(home, "--tab-closed", "gone")            # not running: not recorded
                self.assertEqual(self.run_box(home, "--tab-sessions").splitlines(), ["web", "background\tapi+x1"])
                self.run_box(home, "--attach", "api+x1")                # a tab again (attach itself fails here)
                self.assertEqual(self.run_box(home, "--tab-sessions").split(), ["api+x1", "web"])
            finally:
                stop()

    def test_auto_mode_falls_back_once(self):
        # a `claude` that exits at once in auto mode: the session starts it again in manual mode, once
        with tempfile.TemporaryDirectory() as home:
            bin_dir = os.path.join(home, "bin")
            os.makedirs(bin_dir)
            log = os.path.join(home, "calls")
            with open(os.path.join(bin_dir, "claude"), "w") as fh:
                fh.write(f'#!/bin/sh\necho "$*" >> {shlex.quote(log)}\nexit 1\n')
            os.chmod(os.path.join(bin_dir, "claude"), 0o755)
            os.makedirs(os.path.join(home, ".config/agentbox"))
            with open(os.path.join(home, ".config/agentbox/permissions"), "w") as fh:
                fh.write("BOX_CLAUDE_PERMISSIONS=auto\nBOX_CODEX_PERMISSIONS=auto\n")
            out = self.run_box(home, "--run-agent", "claude", path=f"{bin_dir}:{os.environ['PATH']}")
            calls = open(log).read().splitlines()
            self.assertEqual(calls, ["--continue --permission-mode auto", "--permission-mode auto",
                                     "--continue --permission-mode default", "--permission-mode default"])
            self.assertIn("isn't signed in", out)
            self.assertIn("manual mode", out)


class ProdDuplicateHint(unittest.TestCase):
    """A clash inside an APP_PROD app doesn't suggest an APP_PROD_PROD name."""

    def test_no_double_prod(self):
        entries = [["ACME_PROD__KEY", "a", "prod"], ["ACME_PROD__KEY", "b", "prod"]]
        with self.assertRaises(SystemExit) as e:
            box_secrets.app_env(entries, "acme_prod", write_files=False)
        self.assertIn("different values for KEY", str(e.exception))
        self.assertNotIn("PROD_PROD", str(e.exception))

    def test_dev_app_hint_names_prod(self):
        entries = [["ACME__KEY", "a", "dev"], ["ACME__KEY", "b", "master"]]
        with self.assertRaises(SystemExit) as e:
            box_secrets.app_env(entries, "acme", write_files=False)
        self.assertIn("ACME_PROD__NAME", str(e.exception))


class BitwardenWrite(unittest.TestCase):
    """env_to_bitwarden.py never lets a secret reach a bws argument slot or error text."""

    def setUp(self):
        import env_to_bitwarden
        self.m = env_to_bitwarden

    def test_hide_removes_values_and_their_lines(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEfakeKeyForTheTestOnly\nQmFkZ2VyQmFkZ2Vy\n-----END RSA PRIVATE KEY-----"
        err = "error: unexpected argument 'QmFkZ2VyQmFkZ2Vy' found; tip: '-- -----BEGIN RSA PRIVATE KEY-----'"
        out = self.m.hide(err, [pem])
        self.assertNotIn("QmFkZ2VyQmFkZ2Vy", out)
        self.assertNotIn("BEGIN RSA", out)
        self.assertIn("<value hidden>", out)

    def test_positionals_follow_double_dash(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        orig = self.m.subprocess.run
        self.m.subprocess.run = fake_run
        try:
            self.m.bws({}, "secret", "create", "K", "-----BEGIN x", "pid", ("--note", "n"), values=("-----BEGIN x",))
        finally:
            self.m.subprocess.run = orig
        cmd = seen["cmd"]
        self.assertEqual(cmd[:5], ["bws", "secret", "create", "--output", "json"])
        self.assertLess(cmd.index("--"), cmd.index("-----BEGIN x"))
        self.assertLess(cmd.index("--note"), cmd.index("--"))


class McpServers(unittest.TestCase):
    """Which MCP servers travel, and how, without their credentials."""

    def test_user_scope_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".claude.json")
            with open(path, "w") as fh:
                json.dump({"mcpServers": {"a": {"type": "http", "url": "https://a.example/mcp"}},
                           "projects": {"/x": {"mcpServers": {"b": {"command": "b"}}}}}, fh)
            self.assertEqual(list(mcp_sync.claude_servers(path)), ["a"])
            self.assertEqual(mcp_sync.local_scope_servers(path), {"b": ["/x"]})

    def test_codex_tables_without_tomllib(self):
        text = """model = "x"
[mcp_servers.docs]
url = 'https://docs.example/mcp'   # comment
http_headers = { "X-Team" = "web", Accept = "json" }
enabled_tools = [
  "search",  # first
  "read",
]
[mcp_servers.local]
command = "npx"
args = ["-y", "pkg@1.0"]
startup_timeout_sec = 20
enabled = true
[mcp_servers.local.env]
API_TOKEN = "abc123def456ghi789"
[mcp_servers.odd]
command = \"\"\"
multi\"\"\"
[profiles.other]
url = "not a server"
"""
        got = mcp_sync.parse_mcp_tables(text)
        self.assertEqual(got["docs"], {"url": "https://docs.example/mcp", "http_headers": {"X-Team": "web", "Accept": "json"},
                                       "enabled_tools": ["search", "read"]})
        self.assertEqual(got["local"], {"command": "npx", "args": ["-y", "pkg@1.0"], "startup_timeout_sec": 20,
                                        "enabled": True, "env": {"API_TOKEN": "abc123def456ghi789"}})
        self.assertIsNone(got["odd"])
        self.assertNotIn("other", got)

    def test_what_looks_secret(self):
        for name, value in [("Authorization", "Bearer x"), ("X-Api-Key", "short"), ("CONTEXT7_API_KEY", "c"),
                            ("apiKey", "abc"), ("X-Org", "a1b2c3d4e5f6g7h8i9j0k1"), ("X", "ghp_short"),
                            ("DB", "postgres://u:pw@h/db"), ("X", "${TOKEN}")]:
            self.assertTrue(mcp_sync.looks_secret(name, value), name)
        for name, value in [("Accept", "application/json"), ("LOG_LEVEL", "debug"), ("X-Region", "eu-west-1"),
                            ("BASE_URL", "https://api.example.com/v1"), ("TOKEN", ""), ("KEYBOARD_LAYOUT", "us"),
                            ("BYPASS_CACHE", "1"), ("NODE_OPTIONS", "--max-old-space-size=4096"),
                            ("SESSION_DIR", "/tmp/s"), ("PRIVATE_MODE", "true")]:
            self.assertFalse(mcp_sync.looks_secret(name, value), name)

    def test_credentials_in_urls_and_arguments(self):
        for url in ("https://u:p@host/mcp", "https://h.example/s/3f9a8c7e1b2d4f60a9e8Xq/sse", "https://x/mcp?token=abc"):
            self.assertIsNone(mcp_sync.claude_def("s", {"type": "http", "url": url})[0], url)
        self.assertIsNotNone(mcp_sync.claude_def("s", {"type": "http", "url": "https://mcp.linear.app/mcp"})[0])
        d, refs, _ = mcp_sync.claude_def("k", {"command": "npx", "args": ["-y", "some-mcp@1.2.0", "--api-key", "sk-live-x",
                                                                         "--token=t0k", "--port", "8080"]})
        self.assertEqual(d["args"], ["run", "claude:k", "--", "npx", "-y", "some-mcp@1.2.0", "--api-key",
                                     "@@agentbox-secret:MCP_K_API_KEY@@", "--token=@@agentbox-secret:MCP_K_TOKEN@@",
                                     "--port", "8080"])
        self.assertEqual(sorted(refs["claude:k"]["args"]), ["MCP_K_API_KEY", "MCP_K_TOKEN"])
        self.assertNotIn("sk-live-x", json.dumps([d, refs]))
        self.assertEqual(mcp_sync.ARG_SECRET, agentbox_mcp.ARG_SECRET)

    def test_http_secret_header_becomes_a_helper(self):
        d, refs, why = mcp_sync.claude_def("ctx", {"type": "http", "url": "https://ctx.example/mcp",
                                                   "headers": {"X-Api-Key": "s3cret", "Accept": "json"}})
        self.assertIsNone(why)
        self.assertEqual(d["headers"], {"Accept": "json"})
        self.assertEqual(d["headersHelper"], "agentbox-mcp headers claude:ctx")
        self.assertEqual(refs, {"claude:ctx": {"headers": {"X-Api-Key": {"secret": "MCP_CTX_X_API_KEY"}}}})
        self.assertNotIn("s3cret", json.dumps([d, refs]))

    def test_laptop_only_servers(self):
        for cfg in ({"type": "http", "url": "http://localhost:3001/mcp"}, {"type": "http", "url": "https://x/mcp?api_key=k1"},
                    {"type": "http", "url": "https://x/mcp", "headersHelper": "/home/me/h.sh"}):
            d, _, why = mcp_sync.claude_def("s", cfg)
            self.assertIsNone(d)
            self.assertTrue(why)
        self.assertIsNone(mcp_sync.claude_def("playwright", {"command": "npx"})[0])

    def test_stdio_paths_and_secret_env(self):
        home, mcp_sync.HOME = mcp_sync.HOME, "/home/me"
        try:
            d, refs, _ = mcp_sync.claude_def("db", {"command": "/home/me/.local/bin/dbmcp", "args": ["--dir", "/home/me/data"],
                                                    "env": {"DB_PASSWORD": "pw", "MODE": "ro"}})
        finally:
            mcp_sync.HOME = home
        self.assertEqual(d["command"], "agentbox-mcp")
        self.assertEqual(d["args"], ["run", "claude:db", "--", "dbmcp", "--dir", projects.BOX_HOME + "/data"])
        self.assertEqual(d["env"], {"MODE": "ro"})
        self.assertEqual(refs["claude:db"], {"env": {"DB_PASSWORD": {"secret": "MCP_DB_DB_PASSWORD"}}})

    def test_codex_variables_come_from_bitwarden(self):
        d, refs, _ = mcp_sync.codex_def("gh", {"url": "https://gh.example/mcp", "bearer_token_env_var": "GH_PAT",
                                               "env_http_headers": {"X-Org": "ORG_ID"}})
        self.assertEqual(d, {"url": "https://gh.example/mcp", "http_headers_helper": "agentbox-mcp headers codex:gh"})
        self.assertEqual(refs["codex:gh"]["headers"], {"X-Org": {"secret": "MCP_GH_ORG_ID"},
                                                       "Authorization": {"secret": "MCP_GH_GH_PAT", "prefix": "Bearer "}})
        d, refs, _ = mcp_sync.codex_def("loc", {"command": "tool", "env_vars": ["TOOL_KEY"]})
        self.assertEqual(d["args"][:3], ["run", "codex:loc", "--"])
        self.assertEqual(refs["codex:loc"], {"env": {"TOOL_KEY": {"secret": "MCP_LOC_TOOL_KEY"}}})

    def test_box_keeps_its_own_and_its_edits(self):
        D = agentbox_mcp.digest
        a, a2, b, own = {"url": "a"}, {"url": "a2"}, {"url": "b"}, {"url": "own"}
        stored_b = {"url": "b", "type": "http"}   # the agent normalised what it stored
        manifest = {"a": {"want": D(a), "have": D(a)}, "b": {"want": D(b), "have": D(stored_b)},
                    "gone": {"want": D({"url": "g"}), "have": D({"url": "g"})},
                    "gone-edited": {"want": D({"url": "e"}), "have": D({"url": "e"})},
                    "edited": {"want": D(a), "have": D(a)}}
        current = {"a": a, "b": stored_b, "mine": own, "gone": {"url": "g"}, "gone-edited": {"url": "e!"},
                   "edited": {"url": "changed on the box"}, "twin": {"url": "t"}}
        # twin: the box added the same server itself; it stays the box's (never adopted, never removed)
        desired = {"a": a2, "b": b, "mine": {"url": "laptop"}, "new": {"url": "n"}, "edited": a, "twin": {"url": "t"}}
        to_set, remove, kept, same = agentbox_mcp.plan_changes(desired, current, manifest)
        self.assertEqual(to_set, {"a": a2, "new": {"url": "n"}})
        self.assertEqual(remove, ["gone"])
        self.assertEqual(sorted(kept), ["edited", "gone-edited", "mine"])
        self.assertEqual(same, ["b"])

    def test_codex_toml_round_trip(self):
        servers = {"docs": {"url": "https://d/mcp", "http_headers_helper": "agentbox-mcp headers codex:docs",
                            "enabled_tools": ["a", "b"], "startup_timeout_sec": 20, "enabled": True},
                   "my tool": {"command": "/usr/bin/t", "args": ["--q", "it's \"quoted\""], "env": {"MODE": "ro"}}}
        self.assertEqual(mcp_sync.parse_mcp_tables(agentbox_mcp.toml_servers(servers)), servers)

    @unittest.skipUnless(importlib.util.find_spec("tomllib"), "agentbox-mcp reads config.toml with tomllib (Python 3.11+, as on the box)")
    def test_codex_config_keeps_the_boxs_own_servers(self):
        names = ("CODEX_DIR", "CODEX_SERVERS", "BOX_SERVERS", "MANIFEST", "PORT_FILE")
        saved = {n: getattr(agentbox_mcp, n) for n in names}
        with tempfile.TemporaryDirectory() as d:
            for n in names:
                setattr(agentbox_mcp, n, os.path.join(d, n.lower()))
            os.makedirs(agentbox_mcp.CODEX_DIR)
            try:
                with open(os.path.join(agentbox_mcp.CODEX_DIR, "config.toml"), "w") as fh:
                    fh.write(agentbox_mcp.toml_servers({"playwright": {"command": "npx"}, "mine": {"url": "https://m"},
                                                        "gone": {"url": "https://g"}}))
                agentbox_mcp.save(agentbox_mcp.MANIFEST, {"codex": {"gone": {"want": "x", "have": "x"}}})
                agentbox_mcp.save(agentbox_mcp.CODEX_SERVERS, {"linear": {"url": "https://l"}})
                with open(agentbox_mcp.PORT_FILE, "w") as fh:
                    fh.write("18742\n")
                agentbox_mcp.compose_codex()   # BOX_PLAYWRIGHT off: no mcp-box.json
                text = open(os.path.join(agentbox_mcp.CODEX_DIR, "config.toml")).read()
                self.assertTrue(text.startswith("mcp_oauth_callback_port = 18742\n"))
                self.assertEqual(sorted(mcp_sync.parse_mcp_tables(text)), ["linear", "mine"])
                agentbox_mcp.save(agentbox_mcp.BOX_SERVERS, {"playwright": {"command": "/x/npx"}})
                agentbox_mcp.compose_codex()
                got = mcp_sync.parse_mcp_tables(open(os.path.join(agentbox_mcp.CODEX_DIR, "config.toml")).read())
                self.assertEqual(got["playwright"], {"command": "/x/npx"})
            finally:
                for n, v in saved.items():
                    setattr(agentbox_mcp, n, v)

    def test_mcp_tools_in_transcripts(self):
        u = readiness.Usage([])
        u.feed(b'{"message":{"content":[{"type":"tool_use","name":"mcp__linear-server__get_issue","input":{}},'
               b'{"type":"tool_use","name":"mcp__claude_ai_Gmail__search","input":{}}]}}\n'
               b'{"message":{"content":[{"type":"text","text":"mcp__x__y in prose"}]}}\n')
        self.assertEqual(dict(u.mcp), {"linear-server": 1, "claude_ai_Gmail": 1})


class Browser(unittest.TestCase):
    """server/bin/agentbox-browser's choices, with a throwaway HOME and stand-ins for npx,
    Chromium and the viewer (the real browser was checked on a box)."""
    TOOL = os.path.join(os.path.dirname(Permissions.BOX), "agentbox-browser")

    def setUp(self):
        # agentbox-browser prefers /run/agentbox to XDG_RUNTIME_DIR: on a box these tests would
        # start, stop and overwrite the real browsers and relays there (they once left one running)
        if os.access("/run/agentbox", os.W_OK):
            self.skipTest("this machine has /run/agentbox (a box): its browser state is real")
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.bin = os.path.join(self.home, "bin")
        os.makedirs(self.bin)
        with open(os.path.join(self.bin, "npx"), "w") as fh:
            fh.write('#!/bin/sh\necho "npx $*"\n')
        os.chmod(os.path.join(self.bin, "npx"), 0o755)
        self.env = {"PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": self.home, "XDG_RUNTIME_DIR": self.home,
                    "AGENTBOX_NOVNC": os.path.join(self.home, "novnc")}
        self.run_dir = os.path.join(self.home, "agentbox", "browser")
        self.addCleanup(self.stop_relays)

    def stop_relays(self):
        for f in (os.listdir(self.run_dir) if os.path.isdir(self.run_dir) else []):
            if f.endswith(".relay"):
                with open(os.path.join(self.run_dir, f)) as fh:
                    pid = re.search(r"pid=(\d+)", fh.read())
                if pid:
                    subprocess.run(["kill", pid.group(1)], capture_output=True)

    def tool(self, *args, cwd=None):
        return subprocess.run(["bash", self.TOOL, *args], capture_output=True, text=True, env=self.env,
                              cwd=cwd or self.home, stdin=subprocess.DEVNULL)

    def fake_browser(self):
        chrome = os.path.join(self.home, ".cache/ms-playwright/chromium-1200/chrome-linux64/chrome")
        os.makedirs(os.path.dirname(chrome))
        os.makedirs(os.path.join(self.home, "novnc"))
        for path in [chrome] + [os.path.join(self.bin, t) for t in ("Xvnc", "vncpasswd", "websockify")]:
            with open(path, "w") as fh:
                fh.write("#!/bin/sh\nexit 0\n")
            os.chmod(path, 0o755)

    def test_mcp_headless_outside_a_project_or_without_a_browser(self):
        project = os.path.join(self.home, "work", "shop", "web")
        os.makedirs(project)
        out = self.tool("mcp", "@playwright/mcp@1.0", cwd=project).stdout   # no Chromium yet
        self.assertEqual(out.strip(), "npx -y @playwright/mcp@1.0 --headless --browser chromium --isolated")
        self.fake_browser()
        out = self.tool("mcp", "@playwright/mcp@1.0", cwd=os.path.join(self.home, "work")).stdout
        self.assertIn("--headless", out)

    def test_mcp_uses_the_project_relay(self):
        self.fake_browser()
        project = os.path.join(self.home, "work", "shop", ".claude", "worktrees", "x")
        os.makedirs(project)
        out = self.tool("mcp", "@playwright/mcp@1.0", "--caps", "pdf", cwd=project).stdout.strip()
        port = re.fullmatch(r"npx -y @playwright/mcp@1\.0 --cdp-endpoint http://127\.0\.0\.1:(\d+) --caps pdf", out)
        self.assertIsNotNone(port, out)
        with open(os.path.join(self.run_dir, "shop.relay")) as fh:
            saved = fh.read()
        self.assertIn(f"port={port.group(1)}", saved)
        # a second agent in the same project gets the same relay
        again = self.tool("mcp", "@playwright/mcp@1.0", cwd=os.path.join(self.home, "work", "shop")).stdout
        self.assertIn(f":{port.group(1)}", again)

    def test_stop_ends_the_processes_and_keeps_a_kept_profile(self):
        state = os.path.join(self.run_dir, "shop")
        os.makedirs(os.path.join(state, "profile"))
        kept = os.path.join(self.home, ".local/share/agentbox/browser/other")
        os.makedirs(kept)
        sleepers = [subprocess.Popen(["sleep", "60"]) for _ in range(3)]
        self.addCleanup(lambda: [p.kill() for p in sleepers])
        with open(os.path.join(state, "state"), "w") as fh:
            fh.write("view=1\ncdp=2\nproxied=0\npersist=0\n")
            fh.write("".join(f"{k}_pid={p.pid}\n" for k, p in zip(("xvnc", "view", "chrome"), sleepers)))
        self.assertIn("shop\tdown\tview=1\tcdp=2", self.tool("status").stdout)
        self.assertEqual(self.tool("stop", "shop").returncode, 0)
        for p in sleepers:
            self.assertIsNotNone(p.wait(timeout=5))
        self.assertFalse(os.path.exists(state))
        self.assertTrue(os.path.isdir(kept))

    def test_the_router_ends_with_the_last_browser(self):
        router = subprocess.Popen(["sleep", "60"])
        self.addCleanup(router.kill)
        for name in ("shop", "blog"):
            os.makedirs(os.path.join(self.run_dir, name))
            with open(os.path.join(self.run_dir, name, "state"), "w") as fh:
                fh.write("view=1\ncdp=2\n")
        with open(os.path.join(self.run_dir, "router.state"), "w") as fh:
            fh.write(f"pid={router.pid}\nport=18800\n")
        self.tool("stop", "shop")
        self.assertIsNone(router.poll())   # blog's browser still runs
        self.tool("stop", "blog")
        self.assertIsNotNone(router.wait(timeout=5))
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "router.state")))

    def test_start_refuses_odd_names_and_a_missing_browser(self):
        self.assertIn("not a project name", self.tool("start", "../x").stderr)
        self.assertIn("BOX_PLAYWRIGHT=1", self.tool("start", "shop").stderr)

    def test_mcp_headless_for_odd_folder_names_and_without_a_viewer(self):
        self.fake_browser()
        odd = os.path.join(self.home, "work", "my app")
        os.makedirs(odd)
        self.assertIn("--headless", self.tool("mcp", "@playwright/mcp@1.0", cwd=odd).stdout)
        os.rmdir(os.path.join(self.home, "novnc"))   # Chromium but no viewer: headless, as before
        project = os.path.join(self.home, "work", "shop")
        os.makedirs(project)
        self.assertIn("--headless", self.tool("mcp", "@playwright/mcp@1.0", cwd=project).stdout)

    def test_never_a_folder_on_disk_for_the_profile(self):
        # no /run/agentbox (setUp) and no per-user runtime folder: refuse rather than fall back to /tmp
        self.fake_browser()
        del self.env["XDG_RUNTIME_DIR"]
        self.assertIn("no RAM folder", self.tool("start", "shop").stderr)
        project = os.path.join(self.home, "work", "shop")
        os.makedirs(project)
        self.assertIn("--headless", self.tool("mcp", "@playwright/mcp@1.0", cwd=project).stdout)



class BrowserTunnel(unittest.TestCase):
    """The laptop's viewer tunnel loop (box --browser-tunnel) with a stand-in ssh: it reconnects,
    ends when the browser was stopped on the box, gives up on a box that's gone, and ends its
    ssh on TERM."""
    BOX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "box")

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.bin = os.path.join(self.home, "bin")
        os.makedirs(self.bin)
        self.calls = os.path.join(self.home, "calls")

    def fake_ssh(self, tunnel, status):
        # tunnel: shell run for `ssh -N ...`; status: shell run for the status check
        with open(os.path.join(self.bin, "ssh"), "w") as fh:
            fh.write(f"""#!/bin/bash
case " $* " in
  *" -N "*) echo tunnel >> {shlex.quote(self.calls)}; {tunnel} ;;
  *"agentbox-browser status"*) echo status >> {shlex.quote(self.calls)}; {status} ;;
esac
""")
        os.chmod(os.path.join(self.bin, "ssh"), 0o755)

    def loop(self, **env):
        e = {"PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": self.home, "BOX_BROWSER_TUNNEL_RETRY": "1", **env}
        return subprocess.Popen(["bash", self.BOX, "--browser-tunnel", "agentbox-2", "shop", "40001", "5001", ""],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=e)

    def test_reconnects_until_the_browser_is_stopped(self):
        n = os.path.join(self.home, "n")
        self.fake_ssh("exit 255", f'c=$(cat {n} 2>/dev/null || echo 0); echo $((c + 1)) > {n}; '
                                  f'[ "$c" -lt 2 ] && printf "shop\\trunning\\tview=5001\\tcdp=1\\tproxy=\\tprofile=ram\\n"; exit 0')
        out, _ = self.loop().communicate(timeout=30)
        with open(self.calls) as fh:
            self.assertEqual(fh.read().split(), ["tunnel", "status"] * 3)
        self.assertIn("was stopped on agentbox-2; tunnel ends", out)

    def test_a_forward_that_keeps_failing_backs_off_and_gives_up(self):
        # the box answers, but every tunnel ends at once (a refused forward): no endless 1 s loop
        self.fake_ssh("exit 255", 'printf "shop\\trunning\\tview=5001\\tcdp=1\\tproxy=\\tprofile=ram\\n"')
        out, _ = self.loop(BOX_BROWSER_TUNNEL_GIVEUP="3").communicate(timeout=30)
        self.assertIn("giving up", out)
        with open(self.calls) as fh:
            self.assertLessEqual(fh.read().split().count("tunnel"), 3)   # 1 s, then 2 s pauses

    def test_ends_when_the_viewer_moved(self):
        self.fake_ssh("exit 255", 'printf "shop\\trunning\\tview=6001\\tcdp=1\\tproxy=\\tprofile=ram\\n"')
        out, _ = self.loop().communicate(timeout=30)
        self.assertIn("was restarted on agentbox-2", out)

    def test_gives_up_on_a_box_that_is_gone(self):
        self.fake_ssh("exit 255", "exit 255")
        out, _ = self.loop(BOX_BROWSER_TUNNEL_GIVEUP="2").communicate(timeout=30)
        self.assertIn("no working connection to agentbox-2 for", out)
        self.assertIn("giving up", out)

    def test_term_ends_the_ssh_too(self):
        pid_file = os.path.join(self.home, "ssh.pid")
        self.fake_ssh(f"echo $$ > {pid_file}; exec sleep 60", "exit 0")
        p = self.loop()
        for _ in range(100):
            if os.path.exists(pid_file) and os.path.getsize(pid_file):
                break
            time.sleep(0.05)
        p.terminate()
        out, _ = p.communicate(timeout=10)
        with open(pid_file) as fh:
            ssh_pid = fh.read().strip()
        time.sleep(0.3)
        state = subprocess.run(["ps", "-o", "stat=", "-p", ssh_pid], capture_output=True, text=True).stdout.strip()
        self.assertTrue(state in ("", "Z") or state.startswith("Z"), state)   # gone (or only a zombie)
        self.assertIn("tunnel stopped", out)



_rl = importlib.machinery.SourceFileLoader("browser_relay", os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "server", "bin", "agentbox-browser-relay"))
browser_relay = importlib.util.module_from_spec(importlib.util.spec_from_loader("browser_relay", _rl))
_rl.exec_module(browser_relay)


class ViewerClipboard(unittest.TestCase):
    """The viewer's clipboard (server/novnc): which shortcuts it takes over and the keys it then
    sends to the box (run in node), and agentbox-browser serving noVNC's page with it."""
    KEYS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server", "novnc",
                        "agentbox-clipboard-keys.js")
    CTRL, SHIFT, INS, V = 0xffe3, 0xffe1, 0xff63, 0x76

    def js(self, expr):
        script = (f"import * as k from {json.dumps('file://' + self.KEYS)};\n"
                  f"process.stdout.write(JSON.stringify({expr}));")
        r = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    @unittest.skipUnless(shutil.which("node"), "node isn't installed")
    def test_shortcuts(self):
        ev = lambda **e: json.dumps({"ctrlKey": False, "shiftKey": False, "altKey": False, "metaKey": False, **e})
        got = self.js("[" + ", ".join(f"k.shortcut({e}, {m})" for e, m in [
            (ev(code="KeyV", ctrlKey=True), "false"),
            (ev(code="KeyV", ctrlKey=True, shiftKey=True), "false"),   # paste as plain text
            (ev(code="Insert", shiftKey=True), "false"),
            (ev(code="KeyV", metaKey=True), "true"),                   # Cmd+V on a Mac
            (ev(code="KeyC", metaKey=True), "true"),
            (ev(code="KeyV", metaKey=True), "false"),                  # the Windows key elsewhere
            (ev(code="KeyC", ctrlKey=True), "false"),                  # Ctrl+C goes through as it is
            (ev(code="KeyV", ctrlKey=True, altKey=True), "false"),     # AltGr
        ]) + "]")
        self.assertEqual(got, [{"action": "paste", "key": "v"}, {"action": "paste", "key": "v"},
                               {"action": "paste", "key": "Insert"}, {"action": "paste", "key": "v"},
                               {"action": "copy", "key": "c"}, None, None, None])
        self.assertEqual(self.js('k.normalize("a\\r\\nb\\rc\\nd")'), "a\nb\nc\nd")

    @unittest.skipUnless(shutil.which("node"), "node isn't installed")
    def test_keys_sent_to_the_box(self):
        C, S, I, V = self.CTRL, self.SHIFT, self.INS, self.V
        # Ctrl still down there: only V.
        self.assertEqual(self.js(f'k.keySequence("v", {{"ControlLeft": {C}}})'),
                         [[V, "KeyV", True], [V, "KeyV", False]])
        # Ctrl let go while the browser asked: pressed for the paste and let go again.
        self.assertEqual(self.js('k.keySequence("v", {})'),
                         [[C, "ControlLeft", True], [V, "KeyV", True], [V, "KeyV", False], [C, "ControlLeft", False]])
        # A Mac's Command key: up for the shortcut, back down after.
        self.assertEqual(self.js('k.keySequence("v", {"MetaLeft": 65515})'),
                         [[65515, "MetaLeft", False], [C, "ControlLeft", True], [V, "KeyV", True], [V, "KeyV", False],
                          [C, "ControlLeft", False], [65515, "MetaLeft", True]])
        self.assertEqual(self.js(f'k.keySequence("Insert", {{"ShiftRight": {S}}})'),
                         [[I, "Insert", True], [I, "Insert", False]])

    def test_the_viewer_page_loads_the_clipboard(self):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        novnc, js = os.path.join(home, "novnc"), os.path.dirname(self.KEYS)
        os.makedirs(os.path.join(novnc, "app"))
        with open(os.path.join(novnc, "vnc.html"), "w") as fh:
            fh.write('<html><head><script type="module" src="app/ui.js"></script></head><body></body></html>')
        script = ('source <(sed -n "/^viewer_dir()/,/^}/p; /^viewer_web()/,/^}/p" "$1"); '
                  'STATE_DIR="$2"; NOVNC="$3"; VIEWER_JS="$4"; viewer_web proj')
        run = lambda js_dir: subprocess.run(["bash", "-c", script, "_", Browser.TOOL, home, novnc, js_dir],
                                            capture_output=True, text=True).stdout.strip()
        web = run(js)
        self.assertEqual(web, os.path.join(home, "proj", "web"))
        with open(os.path.join(web, "vnc.html")) as fh:
            page = fh.read()
        self.assertLess(page.index('src="app/ui.js"'), page.index('src="agentbox-clipboard.js"'))
        self.assertEqual(os.readlink(os.path.join(web, "app")), os.path.join(novnc, "app"))
        self.assertTrue(os.path.islink(os.path.join(web, "agentbox-clipboard-keys.js")))
        self.assertEqual(run(os.path.join(home, "missing")), novnc)   # no clipboard script: noVNC as it is


class BoxHere(unittest.TestCase):
    """Which box a per-box command (box login) acts on when --box isn't given."""
    BOX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "box")

    def here(self, cwd, on=""):
        script = ('source <(sed -n "/^project_here()/,/^}/p; /^box_here()/,/^}/p" "$1"); '
                  'projects() { printf "acme\\t/src/acme\\nac\\t/src/ac\\n'
                  'kit\\t/src/acme/kit\\n"; }; '
                  'box_of() { echo "box-of-$1"; }; pick_box() { echo picked; }; '
                  'ON="$2"; PWD="$3" box_here')
        return subprocess.run(["bash", "-c", script, "_", self.BOX, on, cwd],
                              capture_output=True, text=True).stdout.strip()

    def test_the_project_you_are_in_decides(self):
        self.assertEqual(self.here("/src/acme"), "box-of-acme")
        self.assertEqual(self.here("/src/acme/code/app"), "box-of-acme")

    def test_the_deepest_project_wins_and_names_are_not_prefixes(self):
        self.assertEqual(self.here("/src/acme/kit/x"), "box-of-kit")
        self.assertEqual(self.here("/src/acme-templates"), "picked")

    def test_box_flag_wins_and_elsewhere_you_choose(self):
        self.assertEqual(self.here("/src/acme", on="agentbox-3"), "agentbox-3")
        self.assertEqual(self.here("/tmp"), "picked")


class BrowserRelay(unittest.TestCase):
    """What one agent session sees of the shared project browser (agentbox-browser-relay):
    only the pages it opened, each in a window of its own; cookies stay shared because
    everything is in the browser's default context."""

    @staticmethod
    def attach(target, session, typ="page", opener=None, waiting=True, within=None):
        m = {"method": "Target.attachedToTarget", "params": {
            "sessionId": session, "waitingForDebugger": waiting,
            "targetInfo": {"targetId": target, "type": typ, **({"openerId": opener} if opener else {})}}}
        if within:
            m["sessionId"] = within
        return m

    def test_pages_it_did_not_open_are_hidden_and_resumed(self):
        c = browser_relay.Client()
        out, back = c.to_client(self.attach("USER", "s-user"))      # the user's own window, already open
        self.assertEqual(out, [])
        self.assertEqual([m["method"] for m in back], ["Runtime.runIfWaitingForDebugger", "Target.detachFromTarget"])
        self.assertEqual(back[0]["sessionId"], "s-user")
        for m in back:   # the relay's own requests: their answers don't reach the client
            self.assertEqual(c.to_client({"id": m["id"], "result": {}}), ([], []))
        self.assertEqual(c.to_client({"method": "Target.detachedFromTarget", "params": {"sessionId": "s-user"}}), ([], []))

    def test_its_own_page_in_a_new_window(self):
        c = browser_relay.Client()
        to_b, back = c.to_browser({"id": 1, "method": "Target.createTarget", "params": {"url": "about:blank"}})
        self.assertEqual(to_b[0]["params"], {"url": "about:blank", "newWindow": True})
        self.assertEqual(back, [])
        # the attach can come before the reply: held, then delivered with it
        self.assertEqual(c.to_client(self.attach("MINE", "s1")), ([], []))
        out, back = c.to_client({"id": 1, "result": {"targetId": "MINE"}})
        self.assertEqual([m.get("id", m.get("method")) for m in out], ["Target.attachedToTarget", 1])
        self.assertEqual(back, [])
        page_event = {"sessionId": "s1", "method": "Page.frameNavigated", "params": {}}
        self.assertEqual(c.to_client(page_event), ([page_event], []))
        self.assertEqual(c.to_client({"sessionId": "s-other", "method": "Page.frameNavigated"}), ([], []))
        # a worker of its page attaches inside its session: allowed, and so is that session
        out, _ = c.to_client(self.attach("W", "s1w", typ="worker", within="s1"))
        self.assertEqual(len(out), 1)
        self.assertIn("s1w", c.sessions)
        # a popup it opened is its own too
        out, _ = c.to_client(self.attach("POP", "s2", opener="MINE"))
        self.assertEqual(len(out), 1)
        self.assertEqual(c.closing()[0]["method"], "Target.closeTarget")
        self.assertEqual(sorted(m["params"]["targetId"] for m in c.closing()), ["MINE", "POP"])

    def test_another_sessions_page_while_waiting_for_its_own(self):
        c = browser_relay.Client()
        c.to_browser({"id": 7, "method": "Target.createTarget", "params": {"url": "about:blank"}})
        self.assertEqual(c.to_client(self.attach("THEIRS", "sx")), ([], []))
        out, back = c.to_client({"id": 7, "result": {"targetId": "MINE"}})
        self.assertEqual(len(out), 1)                                     # only the reply
        self.assertEqual(back[-1]["method"], "Target.detachFromTarget")   # theirs let go

    def test_target_lists_and_closing_the_browser(self):
        c = browser_relay.Client()
        c.owned.add("MINE")
        out, _ = c.to_client({"id": 3, "result": {"targetInfos": [
            {"targetId": "MINE", "type": "page"}, {"targetId": "USER", "type": "page"}, {"targetId": "B", "type": "browser"}]}})
        self.assertEqual([t["targetId"] for t in out[0]["result"]["targetInfos"]], ["MINE", "B"])
        to_b, back = c.to_browser({"id": 4, "method": "Browser.close"})
        self.assertEqual((to_b, back), ([], [{"id": 4, "result": {}}]))
        self.assertEqual(c.to_client({"method": "Target.targetCreated", "params": {"targetInfo": {"targetId": "USER", "type": "page"}}}), ([], []))

    def test_a_held_page_is_let_go_after_two_seconds(self):
        now = [100.0]
        c = browser_relay.Client(clock=lambda: now[0])
        c.to_browser({"id": 1, "method": "Target.createTarget", "params": {}})   # no reply for a while
        self.assertEqual(c.to_client(self.attach("THEIRS", "sx")), ([], []))
        now[0] += 2.5
        self.assertEqual([m["method"] for m in c.expire_held()], ["Runtime.runIfWaitingForDebugger", "Target.detachFromTarget"])
        self.assertEqual(c.held, [])

    def test_commands_on_others_and_on_the_shared_browser_are_refused(self):
        c = browser_relay.Client()
        c.owned.add("MINE")
        c.sessions["s1"] = "MINE"
        for msg in ({"id": 1, "method": "Target.closeTarget", "params": {"targetId": "USER"}},
                    {"id": 2, "method": "Target.attachToTarget", "params": {"targetId": "USER", "flatten": True}},
                    {"id": 3, "method": "Browser.crash"},
                    {"id": 4, "method": "Storage.clearCookies", "params": {}},
                    {"id": 5, "method": "Target.attachToBrowserTarget"},
                    {"id": 6, "sessionId": "s-other", "method": "Runtime.evaluate", "params": {}},
                    {"id": 7, "method": "Target.disposeBrowserContext", "params": {"browserContextId": "C"}}):
            to_b, back = c.to_browser(msg)
            self.assertEqual(to_b, [], msg)
            self.assertIn("error", back[0], msg)
        browser_info = {"id": 10, "method": "Target.getTargetInfo", "params": {}}   # the browser's own
        self.assertEqual(c.to_browser(browser_info), ([browser_info], []))
        ok = {"id": 8, "method": "Target.closeTarget", "params": {"targetId": "MINE"}}
        self.assertEqual(c.to_browser(ok), ([ok], []))
        mine = {"id": 9, "sessionId": "s1", "method": "Runtime.evaluate", "params": {}}
        self.assertEqual(c.to_browser(mine), ([mine], []))

    def test_the_browser_target_is_seen_but_never_closed(self):
        c = browser_relay.Client()
        out, _ = c.to_client(self.attach("BROWSER", "sb", typ="browser", waiting=False))
        self.assertEqual(len(out), 1)
        self.assertNotIn("BROWSER", c.owned)
        self.assertEqual(c.closing(), [])

    def test_sessions_go_with_their_target(self):
        c = browser_relay.Client()
        c.to_browser({"id": 1, "method": "Target.createTarget", "params": {}})
        c.to_client({"id": 1, "result": {"targetId": "MINE"}})
        c.to_client(self.attach("MINE", "s1"))
        c.to_client(self.attach("W", "s1w", typ="worker", within="s1"))
        c.to_client({"sessionId": "s1", "method": "Target.detachedFromTarget", "params": {"sessionId": "s1w"}})
        self.assertNotIn("s1w", c.sessions)
        c.to_client({"method": "Target.targetDestroyed", "params": {"targetId": "MINE"}})
        self.assertEqual((c.owned, c.sessions), (set(), {}))

    def test_a_ping_between_fragments_loses_nothing(self):
        async def go():
            r = asyncio.StreamReader()
            part1, part2 = b'{"id": 1, "res', b'ult": {}}'
            r.feed_data(bytes([0x01, len(part1)]) + part1)          # text, not final
            r.feed_data(browser_relay.frame(0x9, b"hi", masked=False))   # a ping in between
            r.feed_data(bytes([0x80, len(part2)]) + part2)          # continuation, final
            r.feed_eof()
            m = browser_relay.Messages(r)
            self.assertEqual(await m.next(), (0x9, b"hi"))
            self.assertEqual(await m.next(), (0x1, part1 + part2))
        asyncio.run(go())
        data = os.urandom(1001)
        key = b"\x01\x02\x03\x04"
        self.assertEqual(browser_relay.xor(data, key), bytes(c ^ key[i % 4] for i, c in enumerate(data)))

    def test_websocket_frames_round_trip(self):
        async def go():
            for size in (5, 300, 70000):
                data = os.urandom(size)
                r = asyncio.StreamReader()
                r.feed_data(browser_relay.frame(0x1, data, masked=True))
                r.feed_eof()
                self.assertEqual(await browser_relay.read_message(r), (0x1, data))
        asyncio.run(go())


_rr = importlib.machinery.SourceFileLoader("browser_router", os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "server", "bin", "agentbox-browser-router"))
browser_router = importlib.util.module_from_spec(importlib.util.spec_from_loader("browser_router", _rr))
_rr.exec_module(browser_router)


class BrowserRouter(unittest.TestCase):
    """agentbox-browser-router: /NAME/... on the fixed port reaches project NAME's viewer, with
    /NAME taken off, for the page and for the WebSocket; /NAME/open?t=TOKEN sends a valid link
    on to the viewer with its password; only localhost, and the WebSocket only same-origin."""

    def test_routes(self):
        ports = {"shop": 5001, "my.site-2": 5002}
        route = lambda t: browser_router.route(t, ports.get)
        self.assertEqual(route("/shop/vnc.html?autoconnect=1"), (5001, "/vnc.html?autoconnect=1"))
        self.assertEqual(route("/my.site-2/websockify"), (5002, "/websockify"))
        self.assertEqual(route("/shop"), (5001, "/"))
        for bad in ("/other/vnc.html", "/../shop/x", "/./x", "//x", "/sh op/x", "vnc.html", ""):
            self.assertIsNone(route(bad), bad)

    def test_only_localhost_and_the_websocket_from_its_own_page(self):
        ok = browser_router.allowed
        self.assertTrue(ok({"host": "localhost:18800"}, False))
        self.assertTrue(ok({"host": "127.0.0.1:18800", "origin": "http://127.0.0.1:18800"}, True))
        self.assertFalse(ok({"host": "localhost:18800", "origin": "https://evil.example"}, True))
        self.assertFalse(ok({"host": "evil.example:18800"}, False))   # a name of another site pointing here
        self.assertFalse(ok({}, False))

    def projects(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "shop"))
        with open(os.path.join(d, "shop", "state"), "w") as fh:
            fh.write("display=10\nview=5001\ncdp=5002\npassword=Pw8chars\n")
        with open(os.path.join(d, "shop", "links"), "w") as fh:
            fh.write(f"good {time.time() + 60}\nold {time.time() - 1}\n")
        return browser_router.Projects(d)

    def test_state_and_links(self):
        pr = self.projects()
        self.assertEqual((pr.view_port("shop"), pr.view_port("gone")), (5001, None))
        to = pr.opening("shop", "good")
        self.assertTrue(to.startswith("/shop/vnc.html?") and "password=Pw8chars" in to and "path=shop%2Fwebsockify" in to)
        for token in ("old", "bad", ""):
            self.assertIsNone(pr.opening("shop", token))
        self.assertIsNone(pr.opening("gone", "good"))

    def test_page_and_websocket_through_the_router(self):
        async def go():
            seen = []

            async def viewer(r, w):   # a stand-in websockify: records the request, answers it
                head = await r.readuntil(b"\r\n\r\n")
                seen.append(head)
                if b"Upgrade: websocket" in head:
                    w.write(b"HTTP/1.1 101 Switching Protocols\r\n\r\n")
                    w.write(await r.readexactly(4))   # then an echo, as a WebSocket would carry data
                else:
                    w.write(b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nok")
                await w.drain()
                w.close()

            backend = await asyncio.start_server(viewer, "127.0.0.1", 0)
            vport = backend.sockets[0].getsockname()[1]
            projects = self.projects()
            projects.view_port = {"shop": vport}.get
            router = await asyncio.start_server(lambda r, w: browser_router.serve(r, w, projects), "127.0.0.1", 0)
            rport = router.sockets[0].getsockname()[1]

            async def ask(req, extra=b""):
                r, w = await asyncio.open_connection("127.0.0.1", rport)
                w.write(req + extra)
                await w.drain()
                out = await asyncio.wait_for(r.read(), 5)
                w.close()
                return out

            H = b"Host: localhost:18800\r\n"
            async with backend, router:
                out = {
                    "page": await ask(b"GET /shop/vnc.html?x=1 HTTP/1.1\r\n" + H + b"Connection: keep-alive\r\n\r\n"),
                    "ws": await ask(b"GET /shop/websockify HTTP/1.1\r\n" + H + b"Origin: http://localhost:18800\r\n"
                                    b"Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n", b"ping"),
                    "foreign": await ask(b"GET /shop/websockify HTTP/1.1\r\n" + H + b"Origin: https://evil.example\r\n"
                                         b"Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n"),
                    "missing": await ask(b"GET /other/vnc.html HTTP/1.1\r\n" + H + b"\r\n"),
                    "open": await ask(b"GET /shop/open?t=good HTTP/1.1\r\n" + H + b"\r\n"),
                    "expired": await ask(b"GET /shop/open?t=old HTTP/1.1\r\n" + H + b"\r\n"),
                }
            return seen, out

        seen, out = asyncio.run(go())
        self.assertTrue(out["page"].endswith(b"ok"))
        self.assertTrue(seen[0].startswith(b"GET /vnc.html?x=1 HTTP/1.1\r\n"))
        self.assertIn(b"Connection: close", seen[0])           # one request per connection
        self.assertNotIn(b"keep-alive", seen[0])
        self.assertTrue(seen[1].startswith(b"GET /websockify HTTP/1.1\r\n"))
        self.assertIn(b"Connection: Upgrade", seen[1])
        self.assertTrue(out["ws"].startswith(b"HTTP/1.1 101") and out["ws"].endswith(b"ping"))
        self.assertTrue(out["foreign"].startswith(b"HTTP/1.1 403"))
        self.assertTrue(out["missing"].startswith(b"HTTP/1.1 404") and b"box browser other" in out["missing"])
        self.assertTrue(out["open"].startswith(b"HTTP/1.1 302") and b"Location: /shop/vnc.html?" in out["open"])
        self.assertTrue(out["expired"].startswith(b"HTTP/1.1 403"))
        self.assertEqual(len(seen), 2)   # nothing else reached the viewer


class BoxStatesPlacement(unittest.TestCase):
    """Labels and placement follow whether a server exists, not only whether it runs."""

    def test_labels_go_on_the_server_whatever_its_state(self):
        calls = []
        orig = pool.hcloud
        pool.hcloud = lambda *a: calls.append(a) or ""
        try:
            pool.set_label({"server": True, "state": "off", "id": 7}, "p.x", "1")
            pool.set_label({"server": False, "state": "down", "id": 8}, "p.x", "1")
        finally:
            pool.hcloud = orig
        self.assertEqual(calls[0][0], "server")
        self.assertEqual(calls[1][0], "image")

    def test_placement_prefers_a_box_that_is_off_to_a_closed_one(self):
        boxes = {"agentbox": {"state": "down", "projects": []},
                 "agentbox-2": {"state": "off", "projects": ["a", "b"]},
                 "agentbox-3": {"state": "closing", "projects": []}}
        r = pool.plan_place("new", boxes, {}, {}, 1536, max_boxes=3, next_name="")
        self.assertEqual((r["action"], r["box"]), ("open", "agentbox-2"))
        self.assertTrue(any("billed already" in x for x in r["reasons"]))

    def test_a_closing_box_is_never_chosen(self):
        boxes = {"agentbox-3": {"state": "closing", "projects": []}}
        r = pool.plan_place("new", boxes, {}, {}, 1536, max_boxes=3, next_name="")
        self.assertNotEqual(r["box"], "agentbox-3")


class BoxStates(unittest.TestCase):
    """pool.box_state: a server is "up" only while it runs; every other existing server
    says what it's doing (it still bills)."""

    def test_states(self):
        import pool
        self.assertEqual(pool.box_state("running"), "up")
        self.assertEqual(pool.box_state("off"), "off")
        self.assertEqual(pool.box_state("off", closing=True), "closing")
        self.assertEqual(pool.box_state("running", closing=True), "closing")
        self.assertEqual(pool.box_state("deleting"), "closing")
        self.assertEqual(pool.box_state("initializing"), "starting")
        self.assertEqual(pool.box_state("starting"), "starting")
        self.assertEqual(pool.box_state("stopping"), "stopping")
        self.assertEqual(pool.box_state(None), "unknown")


class PlacementScript(unittest.TestCase):
    """The laptop's box add / new / move against stand-ins: an hcloud that keeps servers and
    snapshots in a JSON file, an ssh that answers box --load and folder checks, an rsync that
    does nothing. Nothing reaches Hetzner, a box or tmux. The pool: agentbox closed (a
    snapshot), agentbox-2 up with one project, agentbox-3 up with four."""
    BOX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "box")
    HCLOUD = r"""#!/usr/bin/env python3
import json, os, sys
state_file, log = os.environ["FAKE_STATE"], os.environ["FAKE_LOG"]
with open(state_file) as fh:
    s = json.load(fh)
a = sys.argv[1:]
with open(log, "a") as fh:
    fh.write("hcloud " + " ".join(a) + "\n")
def out(x):
    print(json.dumps(x)); sys.exit(0)
def server(name):
    return next((x for x in s["servers"] if x["name"] == name or str(x["id"]) == name), None)
if a[:2] == ["server", "list"]: out(s["servers"])
if a[:2] == ["image", "list"] and "--selector" in a:
    app = a[a.index("--selector") + 1].split("=", 1)[1]
    print("\n".join(str(i["id"]) for i in s["images"] if i["labels"].get("app") == app)); sys.exit(0)
if a[:2] == ["image", "list"]: out(s["images"])
if a[:2] == ["server", "describe"]:
    srv = server(a[2])
    fmt = next((x for x in a if x.startswith("format=")), "")
    if srv and "Status" in fmt:
        print(srv.get("status", "running"))
    elif srv and "ServerType.Name" in fmt:
        print(srv["server_type"]["name"])
    elif srv and "Location.Name" in fmt:
        print(srv["location"]["name"])
    elif srv and "PrimaryDiskSize" in fmt:
        print(40)
    sys.exit(0 if srv else 1)
if a[:2] == ["server-type", "list"]:
    def t(name, ram, disk, price):
        return {"name": name, "cores": 2, "memory": ram, "disk": disk, "cpu_type": "shared", "architecture": "x86",
                "deprecation": None, "locations": [{"name": "hel1", "available": True}],
                "prices": [{"location": "hel1", "price_hourly": {"gross": price}, "price_monthly": {"gross": "6"}}]}
    out([t("cx23", 4, 40, "0.0095"), t("cx33", 8, 80, "0.0147"), t("cx43", 16, 160, "0.0277")])
if a[:2] == ["server", "shutdown"]:
    server(a[2])["status"] = "off"
    with open(state_file, "w") as fh:
        json.dump(s, fh)
    sys.exit(0)
if a[:2] == ["server", "change-type"]:
    rest = [x for x in a[2:] if not x.startswith("--")]
    server(rest[0])["server_type"]["name"] = rest[1]
    with open(state_file, "w") as fh:
        json.dump(s, fh)
    sys.exit(0)
if a[:2] == ["image", "describe"]: print("40"); sys.exit(0)
if a[0] in ("server", "image") and a[1] in ("add-label", "remove-label"):
    items = s["servers"] if a[0] == "server" else s["images"]
    rest = [x for x in a[2:] if x != "--overwrite"]
    it = next(x for x in items if str(x["id"]) == rest[0])
    if a[1] == "add-label":
        k, v = rest[1].split("=", 1); it["labels"][k] = v
    else:
        it["labels"].pop(rest[1], None)
    with open(state_file, "w") as fh:
        json.dump(s, fh)
    sys.exit(0)
if a[:2] == ["server", "poweron"]:
    server(a[2])["status"] = "running"
    with open(state_file, "w") as fh:
        json.dump(s, fh)
    sys.exit(0)
if a[:2] == ["firewall", "describe"]: out({"labels": {}})
if a[0] == "firewall": sys.exit(0)
sys.exit(1)
"""
    SSH = r"""#!/bin/bash
args=("$@"); i=0
while [ $i -lt ${#args[@]} ]; do case "${args[$i]}" in -o|-p|-i|-L|-R) i=$((i+2));; -*) i=$((i+1));; *) break;; esac; done
host="${args[$i]}"; cmd="${args[*]:$((i+1))}"
echo "ssh $host $cmd" >> "$FAKE_LOG"
[ "$host" != "${FAKE_SSH_DOWN:-}" ] || exit 255
case "$cmd" in
  *"box --sessions"*) [ "${FAKE_SESSIONS_FAIL:-0}" != 1 ] || exit 255; [ -z "${FAKE_SESSIONS:-}" ] || tr ' ' '\n' <<< "$FAKE_SESSIONS" ;;
  *"box --worktrees"*) [ -z "${FAKE_WORKTREES:-}" ] || printf '%s\n' "$FAKE_WORKTREES" ;;
  *"box --agents"*) [ "${FAKE_AGENTS_FAIL:-0}" != 1 ] || exit 255; [ -z "${FAKE_AGENTS:-}" ] || printf '%s\n' "$FAKE_AGENTS" ;;
  *"box --load"*) v="FAKE_AVAIL_${host//-/_}"; printf '{"mem_total_mb": 3819, "mem_avail_mb": %s, "sessions": 1%s}\n' "${!v:-${FAKE_AVAIL:-2700}}" "${FAKE_IDLE_S:+, \"idle_s\": $FAKE_IDLE_S}" ;;
  *"test -d ~/work/"*"/.git"*) exit 1 ;;
  "test -d ~/work/"*) [ "${FAKE_FOLDER_MISSING:-0}" != 1 ] || exit 1 ;;
  "if test -d ~/work/"*) if [ "${FAKE_SOURCE_DIR_MISSING:-0}" = 1 ]; then echo no; else echo yes; fi ;;
esac
exit 0
"""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.bin = os.path.join(self.home, "bin")
        os.makedirs(self.bin)
        # ssh-keygen too: OpenSSH finds ~/.ssh from the user database, not $HOME, so the real
        # one would remove the laptop's host key for agentbox-2 (connect_alias runs ssh-keygen -R).
        for name, text in (("hcloud", self.HCLOUD), ("ssh", self.SSH), ("rsync", "#!/bin/sh\nexit 0\n"),
                           ("scp", "#!/bin/sh\nexit 0\n"), ("ssh-keygen", "#!/bin/sh\nexit 0\n"),
                           ("tailscale", "#!/bin/sh\nexit 1\n")):
            with open(os.path.join(self.bin, name), "w") as fh:
                fh.write(text)
            os.chmod(os.path.join(self.bin, name), 0o755)
        self.conf = os.path.join(self.home, ".config", "agentbox")
        os.makedirs(self.conf)
        open(os.path.join(self.conf, "box.conf"), "w").close()
        self.state, self.log = os.path.join(self.home, "state.json"), os.path.join(self.home, "log")
        self.project = os.path.join(self.home, "src", "web")
        os.makedirs(self.project)
        with open(os.path.join(self.project, "agentbox.json"), "w") as fh:
            json.dump({"repos": [{"path": ".", "git": "acme/web"}], "data": [], "ports": [], "env_files": []}, fh)
        self.pool_state()

    def pool_state(self, on_closed=(), servers=True):
        def server(i, name, labels):
            return {"id": i, "name": name, "status": "running", "labels": {"app": name, **labels}, "location": {"name": "hel1"},
                    "server_type": {"name": "cx23", "prices": [{"location": "hel1", "price_hourly": {"gross": "0.0095"}}]}}
        state = {"servers": [server(2, "agentbox-2", {"p.acme": "1"}),
                             server(3, "agentbox-3", {"p.a": "1", "p.b": "1", "p.c": "1", "p.d": "1"})] if servers else [],
                 "images": [{"id": 900, "labels": {"app": "agentbox", **{f"p.{p}": "1" for p in on_closed}}}]}
        self.write_state(state)

    def read_state(self):
        with open(self.state) as fh:
            return json.load(fh)

    def write_state(self, state):
        with open(self.state, "w") as fh:
            json.dump(state, fh)

    def label(self, server, project):
        s = self.read_state()
        s["servers"][[x["name"] for x in s["servers"]].index(server)]["labels"][f"p.{project}"] = "1"
        self.write_state(s)

    def labels(self):
        s = self.read_state()
        return {x.get("name", "agentbox (snapshot)"): x["labels"] for x in s["servers"] + s["images"]}

    def register(self, **names):
        with open(os.path.join(self.conf, "projects.json"), "w") as fh:
            json.dump(names, fh)

    def box(self, *args, **env):
        e = {"PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": self.home, "BOX_MAX_BOXES": "3",
             "BOX_CONF": os.path.join(self.conf, "box.conf"), "FAKE_STATE": self.state, "FAKE_LOG": self.log,
             "XDG_CONFIG_HOME": os.path.join(self.home, ".config"),
             "BOX_TERMINAL": "print", **env}   # tabs.sh prints instead of opening tabs on this laptop
        r = subprocess.run(["bash", self.BOX, *args], capture_output=True, text=True, env=e,
                           stdin=subprocess.DEVNULL, timeout=120)
        calls = ""
        if os.path.exists(self.log):
            with open(self.log) as fh:
                calls = fh.read()
        return r.returncode, r.stdout + r.stderr, calls

    def test_add_goes_on_the_running_box_with_room(self):
        # case 2 of the report: agentbox closed; agentbox-2 has room for its estimates, agentbox-3 doesn't
        rc, out, calls = self.box("add", self.project, "web")
        self.assertEqual(rc, 0, out)
        self.assertIn("p.web", self.labels()["agentbox-2"])
        self.assertIn("cloned acme/web -> agentbox-2", out)
        self.assertNotIn("starts billing", out)

    def test_new_and_attach_clone_a_placed_project_whose_folder_is_missing(self):
        # placed on agentbox-2 (its label), but never cloned there: the folder check fails
        self.register(web=self.project)
        self.label("agentbox-2", "web")
        for cmd in ("new", "attach"):
            rc, out, calls = self.box(cmd, "web", FAKE_FOLDER_MISSING="1")
            self.assertEqual(rc, 0, out)
            self.assertIn("web isn't on agentbox-2 yet: cloning its repos", out)
            self.assertIn("git clone -q https://github.com/acme/web", calls)
        os.remove(self.log)
        rc, out, calls = self.box("attach", "web")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("git clone", calls)   # already there: nothing cloned

    def test_add_without_room_asks_and_places_nothing(self):
        rc, out, calls = self.box("add", self.project, "web", FAKE_AVAIL="200")
        self.assertNotEqual(rc, 0)
        self.assertIn("agentbox-3: 6.0 GB promised of 3.2", out)
        self.assertIn("Opening agentbox starts billing", out)
        self.assertIn("--yes", out)
        self.assertIn("web is registered, but not on a box and nothing was cloned", out)
        self.assertTrue(all("p.web" not in l for l in self.labels().values()))
        self.assertNotIn("git clone", calls)

    def set_server(self, name, status, snapshotting=False, closing_label=False):
        s = self.read_state()
        srv = next(x for x in s["servers"] if x["name"] == name)
        srv["status"] = status
        if closing_label:
            srv["labels"]["closing"] = "1"
        if snapshotting:
            s["images"].append({"id": 901, "status": "creating", "created_from": {"id": srv["id"]},
                                "labels": {"app": name}})
        self.write_state(s)

    def test_ls_shows_an_off_server_as_off_not_up(self):
        # a box whose server exists but is switched off was listed as "up"
        self.set_server("agentbox-2", "off")
        rc, out, calls = self.box("ls")
        self.assertIn("agentbox-2: off (switched off, billed while it exists", out)
        self.assertNotIn("agentbox-2: up", out)
        self.assertNotIn("ssh agentbox-2", calls)   # not treated as reachable

    def test_ls_shows_a_box_being_saved_as_closing(self):
        self.set_server("agentbox-2", "off", snapshotting=True)
        rc, out, calls = self.box("ls")
        self.assertIn("agentbox-2: closing (box close is saving a snapshot", out)

    def test_open_refuses_a_box_that_is_closing(self):
        self.set_server("agentbox-2", "off", snapshotting=True)
        rc, out, calls = self.box("open", "agentbox-2")
        self.assertNotEqual(rc, 0, out)
        self.assertIn("agentbox-2 is closing; not opened (box close is saving", out)
        self.assertNotIn("poweron", calls)
        self.assertNotIn("up.sh", calls)

    def test_bare_open_leaves_a_closing_box_alone(self):
        self.set_server("agentbox-3", "off", snapshotting=True)
        rc, out, calls = self.box("open")
        self.assertIn("agentbox-3 is closing; not opened (box close is saving", out)
        self.assertNotIn("poweron", calls)

    def test_opening_an_off_box_costs_nothing_more(self):
        self.set_server("agentbox-2", "off")
        rc, out, calls = self.box("open", "agentbox-2")
        self.assertIn("agentbox-2 is switched off (billed all the same); powering it on", out)
        self.assertIn("hcloud server poweron agentbox-2", calls)
        self.assertIn("ssh agentbox-2 box --restore", calls)   # a stopped close's saved sessions
        self.assertNotIn("starts billing", out)

    def test_a_box_box_close_is_working_on_counts_as_closing(self):
        # down.sh labels the server closing=1 for the whole close: between its steps the
        # server is off with no snapshot in progress, and must not be powered on
        self.set_server("agentbox-2", "off", closing_label=True)
        rc, out, calls = self.box("open", "agentbox-2")
        self.assertNotEqual(rc, 0, out)
        self.assertIn("agentbox-2 is closing", out)
        self.assertNotIn("poweron", calls)

    def test_ls_doesnt_connect_to_an_off_box_in_single_box_mode(self):
        self.set_server("agentbox-2", "off")
        rc, out, calls = self.box("ls", BOX_MAX_BOXES="1", BOX_NAME="agentbox-2")
        self.assertIn("switched off, billed while it exists", out)
        self.assertNotIn("ssh agentbox-2", calls)

    WORKING = '{"pid": 7, "agent": "claude", "folder": "/home/dev/work/web", "starter": "t3", "last": 1, "working": true}'
    IDLE = '{"pid": 8, "agent": "codex", "folder": "/home/dev/work/api", "starter": "t3", "last": 1, "working": false}'

    def test_close_waits_for_an_agent_working_outside_the_tabs(self):
        rc, out, calls = self.box("close", "agentbox-2", FAKE_AGENTS=self.WORKING)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("agents working outside its tabs", out)
        self.assertIn("claude in /home/dev/work/web (working, started by t3", out)
        self.assertNotIn("closing=1", calls)

    def test_close_refuses_when_the_box_cant_be_asked_about_its_agents(self):
        rc, out, calls = self.box("close", "agentbox-2", FAKE_AGENTS_FAIL="1")
        self.assertNotEqual(rc, 0, out)
        self.assertIn("Couldn't check agentbox-2 for agents working outside its tabs", out)
        self.assertNotIn("closing=1", calls)
        rc, out, calls = self.box("close", "agentbox-2", "--force", FAKE_AGENTS_FAIL="1")
        self.assertIn("closing=1", calls)

    def test_what_the_box_reports_reaches_the_terminal_without_control_characters(self):
        row = json.dumps({"pid": 7, "agent": "claude", "folder": "/home/dev/work/web\x1b[2J",
                          "starter": "t3\x1b]0;owned\x07", "last": "soon", "working": True})
        rc, out, calls = self.box("close", "agentbox-2", FAKE_AGENTS=row)
        self.assertIn("claude in /home/dev/work/web[2J (working, started by t3]0;owned", out)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\x07", out)

    def test_close_goes_ahead_with_force_or_when_the_agent_is_idle(self):
        for extra, env in ((("--force",), {"FAKE_AGENTS": self.WORKING}), ((), {"FAKE_AGENTS": self.IDLE})):
            rc, out, calls = self.box("close", "agentbox-2", *extra, **env)
            self.assertNotIn("agents working outside its tabs", out)
            self.assertIn("closing=1", calls)   # down.sh started

    def test_tick_keeps_a_box_whose_agent_is_in_a_long_tool_call(self):
        # an old conversation but a busy process tree: box --agents reports it working
        rc, out, calls = self.box("tick", BOX_AUTO_CLOSE_HOURS="1", FAKE_IDLE_S="7200", FAKE_AGENTS=self.WORKING)
        self.assertIn("wasn't closed", out)
        self.assertNotIn("closing=1", calls)

    def test_t3_pair_needs_the_setting_a_running_box_and_a_terminal(self):
        rc, out, calls = self.box("t3", "pair", "agentbox-2")
        self.assertNotEqual(rc, 0)
        self.assertIn("set BOX_T3=1", out)
        self.set_server("agentbox-2", "off")
        rc, out, calls = self.box("t3", "pair", "agentbox-2", BOX_T3="1")
        self.assertIn("box open agentbox-2", out)
        self.set_server("agentbox-2", "running")
        rc, out, calls = self.box("t3", "pair", "agentbox-2", BOX_T3="1")
        self.assertNotEqual(rc, 0)
        self.assertIn("run box t3 pair in your own terminal", out)
        self.assertNotIn("t3 pair", calls)

    def test_ls_lists_agents_outside_the_tabs(self):
        script = 'source <(sed -n "/^agents_text()/,/^}/p" "$1"); printf "%s\\n%s\\n" "$2" "$3" | agents_text all'
        out = subprocess.run(["bash", "-c", script, "_", os.path.join(ROOT_DIR, "box"), self.WORKING, self.IDLE],
                             capture_output=True, text=True).stdout
        self.assertIn("claude in /home/dev/work/web (working, started by t3", out)
        self.assertIn("codex in /home/dev/work/api (idle, started by t3", out)

    def test_move_waits_only_for_agents_in_that_projects_folder(self):
        self.label("agentbox-2", "web")
        self.register(web=self.project)
        rc, out, calls = self.box("move", "web", "--to", "agentbox-3", "--yes", FAKE_SESSIONS="web",
                                  FAKE_AGENTS=self.WORKING)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("claude in /home/dev/work/web", out)
        self.assertNotIn("tar -cf", calls)
        other = self.WORKING.replace("/work/web", "/work/webapp")
        rc, out, calls = self.box("move", "web", "--to", "agentbox-3", "--yes", FAKE_SESSIONS="web", FAKE_AGENTS=other)
        self.assertEqual(rc, 0, out)

    def test_new_on_a_closed_box_needs_consent(self):
        # case 1: the project's box is closed; box new without a terminal or --yes
        self.pool_state(on_closed=("knowledge",))
        self.register(knowledge=self.project)
        rc, out, calls = self.box("new", "knowledge", "--as", "issues")
        self.assertNotEqual(rc, 0)
        self.assertIn("knowledge is on agentbox, which is closed", out)
        self.assertIn("Opening agentbox starts billing", out)
        self.assertNotIn("up.sh", calls)
        self.assertNotIn("--start", calls)

    def test_box_option_places_a_new_project_where_asked(self):
        rc, out, calls = self.box("add", self.project, "web", "--box", "agentbox-3")   # full, but chosen
        self.assertEqual(rc, 0, out)
        self.assertIn("p.web", self.labels()["agentbox-3"])
        self.assertIn("cloned acme/web -> agentbox-3", out)

    def test_move_without_files_on_the_source_places_and_clones(self):
        self.label("agentbox-2", "web")
        self.register(web=self.project)
        rc, out, calls = self.box("move", "web", "--to", "agentbox-3", FAKE_SOURCE_DIR_MISSING="1")
        self.assertEqual(rc, 0, out)
        self.assertIn("has no files on agentbox-2", out)
        self.assertNotIn("tar ", calls)
        self.assertIn("p.web", self.labels()["agentbox-3"])
        self.assertNotIn("p.web", self.labels()["agentbox-2"])
        self.assertIn("cloned acme/web -> agentbox-3", out)

    def test_move_from_a_closed_box_needs_consent(self):
        self.pool_state(on_closed=("web",))
        self.register(web=self.project)
        rc, out, calls = self.box("move", "web", "--to", "agentbox-3")
        self.assertNotEqual(rc, 0)
        self.assertIn("Opening agentbox starts billing", out)
        self.assertNotIn("up.sh", calls)

    def test_move_after_a_declined_add_doesnt_open_a_box(self):
        self.box("add", self.project, "web", FAKE_AVAIL="200")
        rc, out, calls = self.box("move", "web", "--to", "agentbox-3")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("starts billing", out)
        self.assertIn("web isn't on any box", out)
        self.assertIn("p.web", self.labels()["agentbox-3"])

    def test_move_stops_when_the_source_cant_be_reached(self):
        self.label("agentbox-2", "web")
        self.register(web=self.project)
        rc, out, calls = self.box("move", "web", "--to", "agentbox-3", FAKE_SSH_DOWN="agentbox-2")
        self.assertNotEqual(rc, 0)
        self.assertIn("Couldn't reach agentbox-2", out)
        self.assertIn("p.web", self.labels()["agentbox-2"])
        self.assertNotIn("git clone", calls)

    def test_single_box_asks_to_open_it_without_talk_of_room(self):
        self.pool_state(servers=False)
        rc, out, _ = self.box("add", self.project, "web", BOX_MAX_BOXES="1")
        self.assertNotEqual(rc, 0)
        self.assertIn("web goes on agentbox, which is closed", out)
        self.assertNotIn("room", out)

    def test_open_all_without_a_terminal_needs_yes(self):
        rc, out, calls = self.box("open", "--all")
        self.pool_state(on_closed=("web",))
        s = self.read_state()
        s["images"][0]["labels"]["sessions"] = "2"
        self.write_state(s)
        rc, out, calls = self.box("open", "--all")
        self.assertNotEqual(rc, 0)
        self.assertIn("Opening agentbox starts billing", out)
        self.assertNotIn("up.sh", calls)

    def test_move_ending_sessions_needs_consent(self):
        # box move ended the project's sessions without asking, unlike box stop
        self.label("agentbox-2", "web")
        self.register(web=self.project)
        rc, out, calls = self.box("move", "web", "--to", "agentbox-3", FAKE_SESSIONS="web web+issues")
        self.assertNotEqual(rc, 0)
        self.assertIn("This ends sessions on agentbox-2", out)
        self.assertIn(": web web+issues", out)
        self.assertIn("--yes", out)
        self.assertNotIn("kill-session", calls)
        self.assertNotIn("tar -cf", calls)
        self.assertIn("p.web", self.labels()["agentbox-2"])

    def test_move_stops_when_the_sessions_cant_be_listed(self):
        # a failed listing read as "no sessions" would copy the folder under running agents
        self.label("agentbox-2", "web")
        self.register(web=self.project)
        rc, out, calls = self.box("move", "web", "--to", "agentbox-3", "--yes", FAKE_SESSIONS_FAIL="1")
        self.assertNotEqual(rc, 0)
        self.assertIn("Couldn't list web's sessions on agentbox-2", out)
        self.assertNotIn("tar -cf", calls)
        self.assertIn("p.web", self.labels()["agentbox-2"])

    def test_move_from_a_closed_source_asks_about_its_sessions_before_billing(self):
        self.pool_state(on_closed=("web",))
        self.register(web=self.project)
        rc, out, calls = self.box("move", "web", "--to", "agentbox-3")
        self.assertNotEqual(rc, 0)
        self.assertNotIn("up.sh", calls)
        self.assertIn("Opening agentbox starts billing", out)

    def test_move_carries_worktrees_outside_the_folder_and_waits_for_agents_in_them(self):
        self.label("agentbox-2", "web")
        self.register(web=self.project)
        wt = ".t3/worktrees/web/feature"
        busy = self.WORKING.replace("/home/dev/work/web", f"/home/dev/{wt}")
        rc, out, calls = self.box("move", "web", "--to", "agentbox-3", "--yes", FAKE_WORKTREES=wt, FAKE_AGENTS=busy)
        self.assertNotEqual(rc, 0, out)
        self.assertIn(f"claude in /home/dev/{wt}", out)
        self.assertNotIn("tar -cf", calls)
        os.remove(self.log)
        rc, out, calls = self.box("move", "web", "--to", "agentbox-3", "--yes", FAKE_WORKTREES=wt)
        self.assertEqual(rc, 0, out)
        self.assertIn(f"mv ~/{wt} ~/{wt}.moved-to-agentbox-3", calls)
        self.assertIn(f"Its worktrees outside the folder came along: {wt}", out)

    def test_resize_in_place_saves_changes_the_type_and_restores(self):
        rc, out, calls = self.box("resize", "cx33", "agentbox-2", "--yes", BOX_MAX_TYPE="cx33")
        self.assertEqual(rc, 0, out)
        self.assertIn("box --save", calls)
        self.assertIn("hcloud server change-type --keep-disk agentbox-2 cx33", calls)
        self.assertIn("box --restore", calls)
        self.assertIn("agentbox-2 is now a cx33", out)

    def test_resize_in_place_asks_first_and_stops_for_working_agents_and_the_budget(self):
        for args, env, says in (((), {"BOX_MAX_TYPE": "cx33"}, "Rerun with --yes once the user agrees"),
                                (("--yes",), {"BOX_MAX_TYPE": "cx33", "FAKE_AGENTS": self.WORKING},
                                 "agents working outside its tabs"),
                                (("--yes",), {"BOX_MAX_TYPE": "cx23"}, "above your budget")):
            rc, out, calls = self.box("resize", "cx33", "agentbox-2", *args, **env)
            self.assertNotEqual(rc, 0, out)
            self.assertIn(says, out)
            self.assertNotIn("server shutdown", calls)
            os.remove(self.log)

    def test_move_with_yes_ends_the_sessions_and_moves(self):
        self.label("agentbox-2", "web")
        self.register(web=self.project)
        rc, out, calls = self.box("move", "web", "--to", "agentbox-3", "--yes", FAKE_SESSIONS="web")
        self.assertEqual(rc, 0, out)
        self.assertIn("kill-session", calls)
        self.assertIn("p.web", self.labels()["agentbox-3"])

    def test_projects_from_before_pools_get_labels_and_a_new_one_doesnt(self):
        old = os.path.join(self.home, "src", "old")
        os.makedirs(old)
        self.register(old=old)
        rc, out, _ = self.box("add", self.project, "web")
        self.assertEqual(rc, 0, out)
        self.assertIn("p.old", self.labels()["agentbox (snapshot)"])      # labelled onto the first box
        self.assertNotIn("p.web", self.labels()["agentbox (snapshot)"])   # the new one went by the plan
        self.assertIn("p.web", self.labels()["agentbox-2"])


class BoxPorts(unittest.TestCase):
    """Fixed ports per box (MCP callback, browser viewers): the base, plus N-1 for <pool>-N."""

    def test_ports(self):
        lib = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib.sh")
        for box, want in (("agentbox", "18741 18800"), ("agentbox-3", "18743 18802")):
            r = subprocess.run(["bash", "-c", 'source "$1"; NAME="$2"; echo "$(mcp_port) $(view_port)"', "_", lib, box],
                               capture_output=True, text=True, env={"PATH": os.environ["PATH"], "HOME": tempfile.gettempdir()})
            self.assertEqual(r.stdout.strip(), want, r.stderr)



class ToolPath(unittest.TestCase):
    """lib.sh finds the laptop's tools without the user's interactive shell setup (agents
    and runners call box from shells that never read ~/.bashrc)."""
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def run_lib(self, home, path):
        env = {"HOME": home, "PATH": path, "BOX_CONF": os.path.join(home, "none.conf")}
        return subprocess.run(["bash", "-c", 'source "$1/lib.sh"; command -v hcloud || echo MISSING; echo "$PATH"',
                               "_", self.ROOT], env=env, capture_output=True, text=True, timeout=30)

    def test_tool_in_local_bin_is_found_from_a_bare_shell(self):
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, ".local/bin"))
            tool = os.path.join(home, ".local/bin/hcloud")
            with open(tool, "w") as fh:
                fh.write("#!/bin/sh\n")
            os.chmod(tool, 0o755)
            r = self.run_lib(home, "/usr/bin:/bin")
            self.assertEqual(r.stdout.splitlines()[0], tool, r.stderr)

    def test_users_path_order_wins(self):
        with tempfile.TemporaryDirectory() as home:
            mine = os.path.join(home, "mine")
            os.makedirs(os.path.join(home, ".local/bin"))
            os.makedirs(mine)
            for d in (mine, os.path.join(home, ".local/bin")):
                with open(os.path.join(d, "hcloud"), "w") as fh:
                    fh.write("#!/bin/sh\n")
                os.chmod(os.path.join(d, "hcloud"), 0o755)
            r = self.run_lib(home, f"{mine}:/usr/bin:/bin")
            first, path = r.stdout.splitlines()[:2]
            self.assertEqual(first, os.path.join(mine, "hcloud"))
            self.assertTrue(path.startswith(f"{mine}:/usr/bin:/bin"), path)
            self.assertEqual(path.split(":").count(os.path.join(home, ".local/bin")), 1)

if __name__ == "__main__":
    unittest.main()


_loader = importlib.machinery.SourceFileLoader("github_token", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server", "bin", "github-token"))
github_token = importlib.util.module_from_spec(importlib.util.spec_from_loader("github_token", _loader))
_loader.exec_module(github_token)


class GithubToken(unittest.TestCase):
    def test_app_jwt_verifies_with_the_public_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            key, pub, data, sig = (os.path.join(tmp, n) for n in ("k.pem", "p.pem", "data", "sig"))
            subprocess.run(["openssl", "genrsa", "-out", key, "2048"], check=True, capture_output=True)
            subprocess.run(["openssl", "rsa", "-in", key, "-pubout", "-out", pub], check=True, capture_output=True)
            jwt = github_token.app_jwt("12345", open(key).read(), 1_800_000_000)
            header, payload, signature = jwt.split(".")
            claims = json.loads(github_token.base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            self.assertEqual(claims, {"iat": 1_800_000_000 - 60, "exp": 1_800_000_000 + 540, "iss": "12345"})
            open(data, "wb").write(f"{header}.{payload}".encode())
            open(sig, "wb").write(github_token.base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)))
            ok = subprocess.run(["openssl", "dgst", "-sha256", "-verify", pub, "-signature", sig, data], capture_output=True)
            self.assertEqual(ok.returncode, 0)

    def test_cache_is_used_until_five_minutes_before_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            github_token.CACHE = os.path.join(tmp, "t.json")
            github_token.save("tok", 10_000)
            self.assertEqual(github_token.cached(10_000 - 301), "tok")
            self.assertIsNone(github_token.cached(10_000 - 299))
            self.assertEqual(os.stat(github_token.CACHE).st_mode & 0o777, 0o600)


class GitWork(unittest.TestCase):
    """What counts as work only on one machine (server/bin/agentbox-git), on real repos: a bare
    "remote" and clones of it in a temporary home. Nothing touches the user's repos or a box."""
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.env = {**os.environ, "HOME": self.home, "GIT_CONFIG_GLOBAL": os.path.join(self.home, ".gitconfig"),
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t", "PATH": f"{self.ROOT}/server/bin:{os.environ['PATH']}"}
        self.remote = os.path.join(self.home, "remote.git")
        self.git(self.home, "init", "-q", "--bare", "-b", "main", self.remote)
        self.work = os.path.join(self.home, "work")
        self.repo = os.path.join(self.work, "proj")
        self.git(self.home, "clone", "-q", self.remote, self.repo)
        self.git(self.repo, "switch", "-q", "-c", "main")
        self.commit(self.repo, "first")
        self.git(self.repo, "push", "-q", "-u", "origin", "main")

    def git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, env=self.env, check=True, capture_output=True,
                              text=True).stdout.strip()

    def commit(self, cwd, msg):
        with open(os.path.join(cwd, msg + ".txt"), "w") as fh:
            fh.write(msg)
        self.git(cwd, "add", ".")
        self.git(cwd, "commit", "-q", "-m", msg)
        return self.git(cwd, "rev-parse", "HEAD")

    def worktree(self, name, branch):
        path = os.path.join(self.repo, ".claude", "worktrees", name)
        self.git(self.repo, "worktree", "add", "-q", "-b", branch, path)
        with open(os.path.join(self.repo, ".git", "info", "exclude"), "a") as fh:
            fh.write("/.claude/worktrees/\n")   # as box session (and Claude Code) set it up
        return path

    def git_status(self, *args):
        r = subprocess.run(["bash", os.path.join(self.ROOT, "server", "bin", "box"), "--git-status", *args],
                           env=self.env, capture_output=True, text=True, timeout=60)
        return r.stdout

    def with_env(self, fn, *args):
        old = dict(os.environ)
        os.environ.update(self.env)
        try:
            return fn(*args)
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_box_close_sees_a_worktree_five_folders_deep(self):
        # bug 1: ~/work/NAME/.claude/worktrees/X is deeper than the old 4-level search
        wt = self.worktree("feat", "feat/thing")
        self.commit(wt, "only-here")
        out = self.git_status()
        self.assertIn("proj/.claude/worktrees/feat\t", out)
        self.assertIn("1 commit(s) on branch feat/thing not on GitHub", out)

    def test_a_merged_and_deleted_branch_isnt_work_only_here(self):
        # bug 2: a checkout on a branch GitHub never had by name, whose commits are in main there
        self.git(self.repo, "switch", "-q", "-c", "kit-refresh")
        self.commit(self.repo, "kit")
        self.git(self.repo, "push", "-q", "origin", "kit-refresh:main")   # merged; the branch never pushed
        self.assertEqual(self.git_status("--fetch"), "")

    def test_stashes_and_branches_not_checked_out_count(self):
        self.git(self.repo, "switch", "-q", "-c", "side")
        self.commit(self.repo, "side-work")
        self.git(self.repo, "switch", "-q", "main")
        with open(os.path.join(self.repo, "first.txt"), "w") as fh:
            fh.write("changed")
        self.git(self.repo, "stash", "-q")
        out = self.git_status()
        self.assertIn("branch(es) not on GitHub: side", out)
        self.assertIn("1 stash(es)", out)

    def test_a_squash_merged_and_deleted_branch_isnt_work_only_here(self):
        # GitHub squash-merges (this repo does): the branch's own commits are on no branch,
        # and the fetch prunes origin/feat; its changes are in main, so nothing is lost
        wt = self.worktree("feat", "feat/x")
        self.commit(wt, "a")
        self.commit(wt, "b")
        self.git(wt, "push", "-q", "origin", "feat/x")
        squash = os.path.join(self.home, "squash")
        self.git(self.home, "clone", "-q", self.remote, squash)
        self.git(squash, "merge", "-q", "--squash", "origin/feat/x")
        self.git(squash, "commit", "-q", "-m", "feat (#1)")
        self.git(squash, "push", "-q", "origin", "main", ":feat/x")   # merged, branch deleted
        self.assertEqual(self.git_status("--fetch"), "")
        self.commit(wt, "c")   # then something new on it: that is work only here
        self.assertIn("on branch feat/x not on GitHub", self.git_status("--fetch"))

    def test_a_repo_git_cant_read_is_reported_not_taken_as_safe(self):
        with open(os.path.join(self.repo, ".git", "HEAD"), "w") as fh:
            fh.write("garbage\n")
        self.assertIn("git can't read it", self.git_status())

    def test_session_scan_treats_a_merged_worktree_branch_as_pushed(self):
        # bug 2: box session pull refused it ("push it"), though its commits are on GitHub
        sys.path.insert(0, self.ROOT)
        import sessions
        wt = self.worktree("fix", "fix/done")
        sha = self.commit(wt, "fix")
        self.git(wt, "push", "-q", "origin", "HEAD:main")
        found = {w["path"]: w for w in self.with_env(sessions.scan, "laptop", self.repo)["worktrees"]}
        self.assertTrue(found[wt]["pushed"])
        self.assertEqual(found[wt]["head"], sha)

    def test_a_worktree_is_recreated_from_its_commit_when_the_branch_isnt_on_github(self):
        # bug 2: re-creating it failed with "couldn't find remote ref fix/done"
        sys.path.insert(0, self.ROOT)
        import sessions
        wt = self.worktree("fix", "fix/done")
        sha = self.commit(wt, "fix")
        self.git(wt, "push", "-q", "origin", "HEAD:main")
        other = os.path.join(self.home, "laptop", "proj")
        self.git(self.home, "clone", "-q", self.remote, other)
        dst = os.path.join(other, ".claude", "worktrees", "fix")
        self.with_env(sessions.add_worktree, "laptop", other, dst, "fix/done", sha)
        self.assertEqual(self.git(dst, "rev-parse", "HEAD"), sha)
        self.assertEqual(self.git(dst, "branch", "--show-current"), "fix/done")



class SessionMessaging(unittest.TestCase):
    """BOX_REMOTE_CONTROL: the switch is saved like the permission modes, every way a Claude
    session starts passes its permission mode and, when on, --remote-control BOX/SESSION with
    the inbound setting (a resume once came back in auto mode and held bypass senders), and
    box --peers matches Claude Code's session files to tmux sessions."""
    BOX = Permissions.BOX
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ACCEPT = '{"crossSessionInbound":"accept"}'

    def home_with(self, home, remote, mode="bypass", box="agentbox-2"):
        os.makedirs(os.path.join(home, ".config/agentbox"), exist_ok=True)
        with open(os.path.join(home, ".config/agentbox/permissions"), "w") as fh:
            fh.write(f"BOX_CLAUDE_PERMISSIONS={mode}\nBOX_CODEX_PERMISSIONS=auto\nBOX_REMOTE_CONTROL={remote}\n")
        with open(os.path.join(home, ".config/agentbox/box-name"), "w") as fh:
            fh.write(box + "\n")

    def run_agent(self, home, agent="claude", exit_code=0, **env):
        """box --run-agent with a stand-in claude/codex that logs its arguments; the calls."""
        bin_dir = os.path.join(home, "bin")
        os.makedirs(bin_dir, exist_ok=True)
        log = os.path.join(home, "calls")
        for tool in ("claude", "codex", "tmux"):
            with open(os.path.join(bin_dir, tool), "w") as fh:   # tmux: never the machine's real server
                fh.write(f'#!/bin/sh\n[ "$(basename "$0")" = tmux ] && exit 1\necho "$(basename "$0") $*" >> {shlex.quote(log)}\n'
                         f'echo "peer=$AGENTBOX_PEER_NAME" >> {shlex.quote(log)}\nexit {exit_code}\n')
            os.chmod(os.path.join(bin_dir, tool), 0o755)
        subprocess.run(["bash", self.BOX, "--run-agent", agent], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": home, **env})
        return open(log).read().splitlines() if os.path.exists(log) else []

    def test_switch_saved_and_kept(self):
        with tempfile.TemporaryDirectory() as home:
            env = {"PATH": os.environ["PATH"], "HOME": home}
            path = os.path.join(home, ".config/agentbox/permissions")
            subprocess.run(["bash", self.BOX, "--permissions"], env={**env, "BOX_CLAUDE_PERMISSIONS": "bypass",
                                                                      "BOX_REMOTE_CONTROL": "1"})
            self.assertIn("BOX_REMOTE_CONTROL=1", open(path).read())
            # a laptop on an older box command sends only the modes: the switch stays on
            subprocess.run(["bash", self.BOX, "--permissions"], env={**env, "BOX_CLAUDE_PERMISSIONS": "auto"})
            self.assertEqual(open(path).read(), "BOX_CLAUDE_PERMISSIONS=auto\nBOX_CODEX_PERMISSIONS=auto\nBOX_REMOTE_CONTROL=1\n")
            r = subprocess.run(["bash", self.BOX, "--permissions"], env={**env, "BOX_REMOTE_CONTROL": "yes"},
                               capture_output=True, text=True)
            self.assertIn("isn't 0 or 1", r.stderr)
            self.assertIn("BOX_REMOTE_CONTROL=0", open(path).read())
            self.assertIn("BOX_CLAUDE_PERMISSIONS=auto", open(path).read())   # the mode kept

    def test_every_start_path_passes_mode_and_messaging(self):
        rc = ["--remote-control", "agentbox-2/acme+blog", "--settings", self.ACCEPT]
        with tempfile.TemporaryDirectory() as home:
            self.home_with(home, 1)
            conv = "11111111-2222-3333-4444-555555555555"
            # no conversation chosen: --continue (a new one if that fails)
            self.assertEqual(self.run_agent(home, CB_SESSION="acme+blog")[0],
                             " ".join(["claude", "--continue", "--dangerously-skip-permissions", *rc]))
            os.remove(os.path.join(home, "calls"))
            # box new: a fresh conversation id, named by its short name (a resume never is: --name
            # would replace the owner's title)
            self.assertEqual(self.run_agent(home, CB_SESSION="acme+blog", CB_CONVERSATION=conv)[0],
                             " ".join(["claude", "--session-id", conv, "--name", "agentbox-2/acme+blog",
                                       "--dangerously-skip-permissions", *rc]))
            os.remove(os.path.join(home, "calls"))
            # restore, box attach --resume ID --as LABEL: the conversation exists, so it's resumed
            tdir = os.path.join(home, ".claude/projects/-home-dev-work-acme")
            os.makedirs(tdir)
            open(os.path.join(tdir, conv + ".jsonl"), "w").close()
            calls = self.run_agent(home, CB_SESSION="acme+blog", CB_CONVERSATION=conv)
            self.assertEqual(calls[0], " ".join(["claude", "--resume", conv, "--dangerously-skip-permissions", *rc]))
            self.assertEqual(calls[1], "peer=agentbox-2/acme+blog")

    def test_auto_fallback_after_remote_control(self):
        # auto mode failing too: messaging is dropped first, then manual mode is tried
        with tempfile.TemporaryDirectory() as home:
            self.home_with(home, 1, mode="auto")
            calls = [c for c in self.run_agent(home, exit_code=1, CB_SESSION="web") if c.startswith("claude")]
            self.assertEqual(len(calls), 6)
            self.assertTrue(all("--remote-control agentbox-2/web" in c for c in calls[:2]))
            self.assertTrue(all("--remote-control" not in c for c in calls[2:]))
            self.assertEqual(calls[-1], "claude --permission-mode default")

    def test_no_session_name_outside_a_pane(self):
        # without CB_SESSION and outside tmux there's no name to give: no messaging, rather than
        # the most recently used session's name
        with tempfile.TemporaryDirectory() as home:
            self.home_with(home, 1)
            self.assertEqual(self.run_agent(home)[0], "claude --continue --dangerously-skip-permissions")

    def test_remote_control_failure_retries_without_it(self):
        with tempfile.TemporaryDirectory() as home:
            self.home_with(home, 1, mode="bypass")
            calls = [c for c in self.run_agent(home, exit_code=1, CB_SESSION="web") if c.startswith("claude")]
            self.assertEqual(calls, ["claude --continue --dangerously-skip-permissions --remote-control agentbox-2/web "
                                     "--settings " + self.ACCEPT,
                                     "claude --name agentbox-2/web --dangerously-skip-permissions --remote-control agentbox-2/web --settings " + self.ACCEPT,
                                     "claude --continue --dangerously-skip-permissions",
                                     "claude --dangerously-skip-permissions"])

    def test_off_and_codex_get_no_messaging(self):
        with tempfile.TemporaryDirectory() as home:
            self.home_with(home, 0)
            calls = self.run_agent(home, CB_SESSION="web")
            self.assertEqual(calls[0], "claude --continue --dangerously-skip-permissions")
            self.assertEqual(calls[1], "peer=")
        with tempfile.TemporaryDirectory() as home:
            self.home_with(home, 1)
            calls = self.run_agent(home, agent="codex", CB_SESSION="web@codex")
            self.assertTrue(calls and calls[0].startswith("codex") and "--remote-control" not in calls[0], calls)

    @unittest.skipUnless(shutil.which("tmux"), "tmux isn't installed")
    def test_peers_match_session_files_to_tmux(self):
        harness = BoxScript()
        with tempfile.TemporaryDirectory() as home:
            self.home_with(home, 1)
            tmux, stop = harness.own_tmux(home)
            try:
                for name in ("web", "api", "web@codex", "bare"):
                    tmux("new-session", "-d", "-s", name, "sleep 60")
                tmux("kill-session", "-t", "=main")
                tmux("new-window", "-t", "=web", "sleep 60")          # a second window, its own claude
                pane = {s: tmux("list-panes", "-t", f"={s}:0", "-F", "#{pane_id}").stdout.split()[0]
                        for s in ("web", "api")}
                other = tmux("list-panes", "-t", "=web:1", "-F", "#{pane_id}").stdout.split()[0]
                sdir = os.path.join(home, ".claude/sessions")
                os.makedirs(sdir)
                files = {"1.json": {"pid": os.getpid(), "tmux": f"web:@1.{pane['web']}", "name": "agentbox-2/web",
                                    "bridgeSessionId": "session_x"},
                         "0.json": {"pid": os.getpid(), "tmux": f"web:@9.{other}", "name": "typed later"},
                         "2.json": {"pid": os.getpid(), "tmux": f"api:@2.{pane['api']}"},           # no name
                         "3.json": {"pid": 2 ** 22 + 12345, "tmux": f"api:@3.{pane['api']}", "name": "x"},  # gone
                         "4.json": "not json"}
                for f, d in files.items():
                    with open(os.path.join(sdir, f), "w") as fh:
                        fh.write(d if isinstance(d, str) else json.dumps(d))
                rows = harness.run_box(home, "--peers").splitlines()
                self.assertEqual(rows, ["messaging\t1", "api\tclaude\t-\tlocal", "bare\tclaude\t-\tnone",
                                        "web\tclaude\tagentbox-2/web\tremote", "web@codex\tcodex\t-\tcodex"])
                for row in rows[1:]:                                  # no empty field for read to merge
                    self.assertNotIn("\t\t", row)
            finally:
                stop()

    def test_install_records_the_confirmation_only_when_on(self):
        src = open(os.path.join(self.ROOT, "server/install.sh")).read()
        code = src.split('python3 - "$HOME/.claude.json" <<\'PY\'\n', 1)[1].split("\nPY\n", 1)[0]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".claude.json")
            with open(path, "w") as fh:
                json.dump({"projects": {"/x": {"hasTrustDialogAccepted": True}}, "hasCompletedOnboarding": True}, fh)
            subprocess.run(["python3", "-c", code, path], env={"PATH": os.environ["PATH"], "BOX_REMOTE_CONTROL": "0"}, check=True)
            self.assertNotIn("remoteDialogSeen", json.load(open(path)))
            subprocess.run(["python3", "-c", code, path], env={"PATH": os.environ["PATH"], "BOX_REMOTE_CONTROL": "1"}, check=True)
            data = json.load(open(path))
            self.assertIs(data["remoteDialogSeen"], True)
            self.assertTrue(data["projects"]["/x"]["hasTrustDialogAccepted"])   # the rest kept
            with open(path, "w") as fh:
                fh.write("{half")                                   # mid-write: left alone
            subprocess.run(["python3", "-c", code, path], env={"PATH": os.environ["PATH"], "BOX_REMOTE_CONTROL": "1"}, check=True)
            self.assertEqual(open(path).read(), "{half")

    def test_doctor_reads_the_laptop_setting(self):
        import onboard
        orig_conf, orig_home = onboard.conf_value, os.environ.get("HOME")
        orig_settings = onboard.claude_settings
        saved_env = {v: os.environ.pop(v) for v in ("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "DISABLE_GROWTHBOOK")
                     if v in os.environ}
        try:
            with tempfile.TemporaryDirectory() as home:
                os.environ["HOME"] = home
                onboard.conf_value = lambda k: "0" if k == "BOX_REMOTE_CONTROL" else None
                self.assertIsNone(onboard.c_messaging()[0])
                onboard.conf_value = lambda k: "1" if k == "BOX_REMOTE_CONTROL" else None
                self.assertIs(onboard.c_messaging()[0], False)              # no settings file
                os.makedirs(os.path.join(home, ".claude"))
                with open(os.path.join(home, ".claude/settings.json"), "w") as fh:
                    json.dump({"remoteControlAtStartup": True}, fh)
                self.assertIs(onboard.c_messaging()[0], True)
                with open(os.path.join(home, ".claude/settings.json"), "w") as fh:   # its env block counts
                    json.dump({"remoteControlAtStartup": True, "env": {"DISABLE_GROWTHBOOK": "1"}}, fh)
                self.assertIs(onboard.c_messaging()[0], False)
                # managed settings come first (stand-in: the real paths aren't touched)
                onboard.claude_settings = lambda: [{"remoteControlAtStartup": True}, {"remoteControlAtStartup": False}]
                self.assertIs(onboard.c_messaging()[0], True)
        finally:
            onboard.conf_value, onboard.claude_settings = orig_conf, orig_settings
            os.environ.update(saved_env)
            if orig_home is not None:
                os.environ["HOME"] = orig_home


_md = importlib.machinery.SourceFileLoader("mise_defaults", os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "server", "bin", "agentbox-mise-defaults"))
mise_defaults = importlib.util.module_from_spec(importlib.util.spec_from_loader("mise_defaults", _md))
_md.exec_module(mise_defaults)


class MiseDefaults(unittest.TestCase):
    """A tool one project installs with mise gets a version elsewhere when its shim would fail there."""
    OWN = "/home/dev/.config/mise/conf.d/00-agentbox-defaults.toml"

    def v(self, version, active=False, path=None, installed=True):
        return {"version": version, "installed": installed, "active": active,
                "source": {"path": path} if path else None}

    def test_only_tools_without_a_global_version_get_the_newest_installed(self):
        listing = {
            "node": [self.v("22.1.0"), self.v("24.2.0", True, "/home/dev/.config/mise/config.toml")],
            "jq": [self.v("1.7.0"), self.v("1.7.1")],                       # only one project pins it
            "terraform": [self.v("1.10.5", True, "/home/dev/.config/mise/conf.d/agentbox-x.toml")],
            "gone": [self.v("1.0", installed=False)],
        }
        self.assertEqual(mise_defaults.defaults(listing, lambda t, v: True, self.OWN), {"jq": "1.7.1"})

    def test_a_tool_the_system_also_has_is_never_shadowed(self):
        listing = {"jq": [self.v("1.7.1")], "core:python": [self.v("3.13.1")]}
        system = {"core:python"}                                          # /usr/bin/python3 exists
        self.assertEqual(mise_defaults.defaults(listing, lambda t, v: t not in system, self.OWN), {"jq": "1.7.1"})

    def test_its_own_file_doesnt_count_as_a_global_version(self):
        listing = {"jq": [self.v("1.7.1"), self.v("1.8.0", True, self.OWN)]}   # recomputed, not kept
        self.assertEqual(mise_defaults.defaults(listing, lambda t, v: True, self.OWN), {"jq": "1.8.0"})

    def test_outside_mise_ignores_mises_shims_and_installs(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        data = os.path.join(d, "mise")
        shims, install, system = os.path.join(data, "shims"), os.path.join(data, "installs", "jq", "1.7.1"), os.path.join(d, "bin")
        for folder in (shims, install, system):
            os.makedirs(folder)
        for f in (os.path.join(shims, "jq"), os.path.join(install, "jq"), os.path.join(shims, "python3"),
                  os.path.join(system, "python3")):
            open(f, "w").close()
            os.chmod(f, 0o755)
        # mise puts an active tool's install folder on PATH for what it runs: that's not the system's jq
        self.assertFalse(mise_defaults.outside_mise("jq", [shims, install, system], data))
        self.assertTrue(mise_defaults.outside_mise("python3", [shims, install, system], data))

    def test_its_file_cant_collide_with_a_project_tools_file(self):
        self.assertFalse(os.path.basename(mise_defaults.CONF).startswith("agentbox-"))   # projects: agentbox-NAME.toml

    def test_render(self):
        self.assertEqual(mise_defaults.render({}), "")
        text = mise_defaults.render({"jq": "1.7.1", "ubi:foo/bar": "2.0"})
        self.assertIn('"jq" = "1.7.1"\n', text)
        self.assertIn('"ubi:foo/bar" = "2.0"\n', text)
        self.assertIn("[tools]\n", text)


class ProjectSetup(unittest.TestCase):
    """agentbox.json "setup" runs on the box once per (command, commits) and is retried after a failure."""
    GIT = ["git", "-c", "user.email=a@b", "-c", "user.name=a"]

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home)
        self.repo = os.path.join(self.home, "work", "proj")
        os.makedirs(os.path.join(self.repo, "api"))
        for folder in (self.repo, os.path.join(self.repo, "api")):
            subprocess.run(["git", "init", "-q"], cwd=folder, check=True)
            subprocess.run(self.GIT + ["commit", "-q", "--allow-empty", "-m", "one"], cwd=folder, check=True)

    def run_setup(self, cmd, folder=None, repos=(".",), timeout=60):
        env = dict(os.environ, HOME=self.home)
        r = subprocess.run(["bash", "-c", projects.setup_script("proj", folder or self.repo, repos, timeout)],
                           input=cmd, text=True, capture_output=True, env=env)
        return [line for line in r.stdout.splitlines() if line.startswith("agentbox-setup ")][0].split()[1:]

    def test_runs_once_per_command_and_commit(self):
        cmd = "echo ran >> ../ran.txt"
        self.assertEqual(self.run_setup(cmd), ["ran"])
        self.assertEqual(self.run_setup(cmd), ["skip"])
        self.assertEqual(self.run_setup(cmd + " # v2"), ["ran"])           # a changed command runs again
        subprocess.run(self.GIT + ["commit", "-q", "--allow-empty", "-m", "two"], cwd=self.repo, check=True)
        self.assertEqual(self.run_setup(cmd + " # v2"), ["ran"])           # a new commit runs it again
        with open(os.path.join(self.home, "work", "ran.txt")) as fh:
            self.assertEqual(fh.read().count("ran"), 3)

    def test_a_pull_in_any_repo_of_a_workspace_runs_it_again(self):
        repos = (".", "api")
        self.assertEqual(self.run_setup("true", repos=repos), ["ran"])
        self.assertEqual(self.run_setup("true", repos=repos), ["skip"])
        subprocess.run(self.GIT + ["commit", "-q", "--allow-empty", "-m", "two"],
                       cwd=os.path.join(self.repo, "api"), check=True)
        self.assertEqual(self.run_setup("true", repos=repos), ["ran"])

    def test_any_exit_code_of_the_command_is_a_failure_not_a_skip(self):
        for code in ("10", "11", "7"):
            self.assertEqual(self.run_setup(f"echo boom; exit {code}"), ["failed", code])
        self.assertEqual(self.run_setup("echo boom; exit 7"), ["failed", "7"])   # not recorded: tried again
        with open(os.path.join(self.home, ".config", "agentbox", "setup", "proj.log")) as fh:
            self.assertIn("boom", fh.read())

    def test_a_setup_that_hangs_is_stopped(self):
        self.assertEqual(self.run_setup("sleep 30", timeout=1), ["failed", "124"])

    def test_a_project_not_on_this_box_is_skipped(self):
        self.assertEqual(self.run_setup("true", folder=os.path.join(self.home, "work", "missing")), ["absent"])

    def outcome(self, stdout, code=0, quiet=False):
        result = subprocess.CompletedProcess([], code, stdout=stdout, stderr="")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(projects, "manifest", return_value={"setup": "make setup"}), \
                mock.patch.object(projects, "ssh", return_value=result), \
                mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            word = projects.sync_setup("proj", "/laptop/proj", "/home/dev/work/proj", quiet=quiet)
        return word, out.getvalue(), err.getvalue()

    def test_sync_setup_reports_each_outcome(self):
        word, out, _ = self.outcome("agentbox-setup ran\n")
        self.assertEqual(word, "ran")
        self.assertIn("ran its setup", out)
        self.assertEqual(self.outcome("agentbox-setup ran\n", quiet=True)[1], "")      # --quiet: silent
        word, _, err = self.outcome("agentbox-setup failed 124\nlast log line\n")
        self.assertEqual(word, "failed")
        self.assertIn("timed out", err)
        self.assertIn("last log line", err)
        word, _, err = self.outcome("", code=255)                                      # box unreachable
        self.assertEqual(word, "unreachable")
        self.assertIn("couldn't reach", err)
        self.assertNotIn("failed", err)

    def test_sync_setup_does_nothing_without_a_setup(self):
        with mock.patch.object(projects, "manifest", return_value={"tools": []}), \
                mock.patch.object(projects, "ssh") as ssh:
            self.assertIsNone(projects.sync_setup("proj", "/laptop/proj", "/home/dev/work/proj"))
            ssh.assert_not_called()


class BrowserForTools(unittest.TestCase):
    """Other tools find the box's browser: `agentbox-browser chromium-path` is Playwright's newest build."""

    def test_chromium_path_is_the_newest_build(self):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home)
        for d in ("chromium-1243/chrome-linux", "chromium-1247/chrome-linux64", "chromium_headless_shell-1250/x"):
            os.makedirs(os.path.join(home, ".cache", "ms-playwright", d))
        for f in ("chromium-1243/chrome-linux/chrome", "chromium-1247/chrome-linux64/chrome"):
            open(os.path.join(home, ".cache", "ms-playwright", f), "w").close()
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server", "bin", "agentbox-browser")
        r = subprocess.run(["bash", script, "chromium-path"], capture_output=True, text=True, env=dict(os.environ, HOME=home))
        self.assertEqual(r.stdout.strip(), os.path.join(home, ".cache", "ms-playwright", "chromium-1247", "chrome-linux64", "chrome"))


_loader = importlib.machinery.SourceFileLoader("agentbox_name", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server", "bin", "agentbox-name"))
agentbox_name = importlib.util.module_from_spec(importlib.util.spec_from_loader("agentbox_name", _loader))
_loader.exec_module(agentbox_name)


class SessionNames(unittest.TestCase):
    """A box session's name is its short name BOX/SESSION, then its own title. Claude Code
    gives a session one name, and only a /rename in it reaches other machines, so
    agentbox-name puts the short name back (from tmux's pane-title-changed hook) by typing
    /rename into the session's own agent when it's idle, its input line is empty and nobody
    is typing, keeping the title, at most twice per change."""
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    SHORT = "agentbox-2/acme+blog"

    def test_combine_keeps_the_title_and_replaces_old_short_names(self):
        c = agentbox_name.combine
        self.assertEqual(c(self.SHORT, "login refactor"), f"{self.SHORT} · login refactor")
        self.assertEqual(c(self.SHORT, f"{self.SHORT} · login refactor"), f"{self.SHORT} · login refactor")
        self.assertEqual(c(self.SHORT, "agentbox/acme+blog · login refactor"), f"{self.SHORT} · login refactor")  # moved
        self.assertEqual(c(self.SHORT, self.SHORT), self.SHORT)
        self.assertEqual(c(self.SHORT, ""), self.SHORT)
        self.assertEqual(c(self.SHORT, "frontend/api"), f"{self.SHORT} · frontend/api")   # a title, not a short name
        self.assertEqual(c(self.SHORT, "notes · draft"), f"{self.SHORT} · notes · draft")

    def test_prompt_empty_only_with_nothing_typed_and_no_work(self):
        e = agentbox_name.prompt_empty
        box = "─" * 20 + " title ─\n"
        bar = "─" * 20 + "\n  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
        self.assertTrue(e(box + "\x1b[39m❯\xa0\n" + bar))
        self.assertTrue(e(box + '\x1b[39m❯\xa0\x1b[2mTry "fix lint errors"\x1b[0m\n' + bar))     # the dim suggestion
        self.assertTrue(e("● It said esc to interrupt earlier\n" + box + "❯\xa0\n" + bar))       # in the conversation
        self.assertFalse(e(box + "❯ half a sentence\n" + bar))                                    # a draft
        self.assertFalse(e(box + "❯\xa0\n" + "  ⏵⏵ bypass permissions on · esc to interrupt\n"))  # working
        self.assertFalse(e(" Do you trust this folder?\n ❯ Yes, I trust this folder\n   No\n"))  # a dialog
        self.assertFalse(e("dev@agentbox-2:~$ \n"))                                               # no Claude

    def write_session(self, home, tmux_where, name, source="user", status="idle", n=1):
        os.makedirs(os.path.join(home, ".claude/sessions"), exist_ok=True)
        path = os.path.join(home, ".claude/sessions", f"{n}.json")
        with open(path, "w") as fh:
            json.dump({"pid": os.getpid(), "tmux": tmux_where, "name": name, "nameSource": source, "status": status}, fh)
        return path

    def test_in_pane_reads_the_name_and_drops_only_generated_ones(self):
        with tempfile.TemporaryDirectory() as home:
            d = os.path.join(home, ".claude/sessions")
            self.write_session(home, "acme+blog:@1.%1", "login refactor")
            self.assertEqual(agentbox_name.in_pane("acme+blog", "%1", d),
                             {"name": "login refactor", "title": "login refactor", "idle": True, "remote": False})
            self.assertIsNone(agentbox_name.in_pane("acme+blog", "%2", d))
            self.assertIsNone(agentbox_name.in_pane("acme", "%1", d))
            self.write_session(home, "acme+blog:@1.%1", "acme-3f", source="derived", status="busy")
            self.assertEqual(agentbox_name.in_pane("acme+blog", "%1", d)["title"], "")
            self.write_session(home, "acme+blog:@1.%1", "Titled on claude.ai", source="remote")   # kept
            self.assertEqual(agentbox_name.in_pane("acme+blog", "%1", d)["title"], "Titled on claude.ai")

    def keep(self, name, screen="❯\xa0\n", remote="1", session="acme+blog", status="idle",
             agent_pane="%1", client_activity=None, claude_renames=True):
        """agentbox-name keep with a stand-in tmux; the /renames it typed ("" for none)."""
        with tempfile.TemporaryDirectory() as home:
            conf = os.path.join(home, ".config/agentbox")
            os.makedirs(conf)
            with open(os.path.join(conf, "permissions"), "w") as fh:
                fh.write(f"BOX_REMOTE_CONTROL={remote}\n")
            with open(os.path.join(conf, "box-name"), "w") as fh:
                fh.write("agentbox-2\n")
            sess = self.write_session(home, f"{session}:@1.%1", name, status=status)
            bindir = os.path.join(home, "bin")
            os.makedirs(bindir)
            with open(os.path.join(home, "screen"), "w") as fh:
                fh.write(screen)
            activity = int(time.time()) if client_activity == "now" else int(time.time()) - 600
            with open(os.path.join(bindir, "tmux"), "w") as fh:   # renames like Claude Code does
                fh.write(f"""#!/usr/bin/env python3
import json, sys
a = sys.argv[1:]
if a[0] == "list-panes":
    print({session!r} + "\\t" + {agent_pane!r} + "\\t1")
elif a[0] == "list-clients":
    print({activity})
elif a[0] == "capture-pane":
    print(open({os.path.join(home, "screen")!r}).read(), end="")
elif a[0] == "send-keys" and "-l" in a:
    text = a[a.index("-l") + 1]
    open({os.path.join(home, "typed")!r}, "a").write(text + ("+Enter" if "Enter" in a[a.index("-l") + 2:] else "") + "\\n")
    if {claude_renames!r}:
        d = json.load(open({sess!r})); d["name"] = text[len("/rename "):]; json.dump(d, open({sess!r}, "w"))
""")
            os.chmod(os.path.join(bindir, "tmux"), 0o755)
            env = dict(os.environ, HOME=home, PATH=bindir + os.pathsep + os.environ["PATH"],
                       AGENTBOX_NAME_TRIES="4", AGENTBOX_NAME_PAUSE="0.05", AGENTBOX_NAME_SETTLE="0")
            subprocess.run([sys.executable, os.path.join(self.ROOT, "server/bin/agentbox-name"), "keep", session, "%1"],
                           env=env, check=True, timeout=30)
            try:
                return open(os.path.join(home, "typed")).read()
            except OSError:
                return ""

    def test_keep_puts_the_short_name_back_once_with_enter_in_the_same_command(self):
        self.assertEqual(self.keep("login refactor"), f"/rename {self.SHORT} · login refactor+Enter\n")

    def test_keep_leaves_alone(self):
        self.assertEqual(self.keep(f"{self.SHORT} · login refactor"), "")              # already right
        self.assertEqual(self.keep("login refactor", screen="❯ my draft\n"), "")        # a draft
        self.assertEqual(self.keep("login refactor", status="busy"), "")               # working
        self.assertEqual(self.keep("login refactor", client_activity="now"), "")       # someone is typing
        self.assertEqual(self.keep("login refactor", remote="0"), "")                  # messaging off
        self.assertEqual(self.keep("login refactor", session="acme@codex"), "")   # Codex
        self.assertEqual(self.keep("login refactor", agent_pane="%7"), "")             # a claude in another window

    def test_keep_stops_after_two_renames_that_dont_take(self):
        typed = self.keep("login refactor", claude_renames=False)
        self.assertEqual(typed.count("/rename "), 2)

    def test_peers_reads_the_same_files_for_box_ls(self):
        with tempfile.TemporaryDirectory() as home:
            self.write_session(home, "acme+blog:@1.%1", f"{self.SHORT} · login refactor")
            bindir = os.path.join(home, "bin")
            os.makedirs(bindir)
            with open(os.path.join(bindir, "tmux"), "w") as fh:
                fh.write("#!/bin/sh\nprintf 'acme+blog\\t%%1\\t1\\nacme+blog\\t%%9\\t0\\nweb@codex\\t%%3\\t1\\nweb\\t%%4\\t1\\n'\n")
            os.chmod(os.path.join(bindir, "tmux"), 0o755)
            r = subprocess.run([sys.executable, os.path.join(self.ROOT, "server/bin/agentbox-name"), "peers"],
                               capture_output=True, text=True, env=dict(os.environ, HOME=home, PATH=bindir + os.pathsep + os.environ["PATH"]))
            self.assertEqual(r.stdout.splitlines(), [f"acme+blog\tclaude\t{self.SHORT} · login refactor\tlocal",
                                                     "web\tclaude\t-\tnone", "web@codex\tcodex\t-\tcodex"])

    def test_tmux_options_and_config(self):
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, ".config/agentbox"))
            with open(os.path.join(home, ".config/agentbox/permissions"), "w") as fh:
                fh.write("BOX_REMOTE_CONTROL=1\n")
            with open(os.path.join(home, ".config/agentbox/box-name"), "w") as fh:
                fh.write("agentbox-2\n")
            r = subprocess.run([sys.executable, os.path.join(self.ROOT, "server/bin/agentbox-name"), "tmux-options"],
                               capture_output=True, text=True, env=dict(os.environ, HOME=home))
            self.assertEqual(r.stdout.splitlines(), ["set -g @agentbox-names 1", "set -g @agentbox-box agentbox-2",
                                                     "set -g @agentbox-pool agentbox"])
        conf = open(os.path.join(self.ROOT, "server/tmux.conf")).read()
        self.assertIn("source-file -q ~/.config/agentbox/tmux-options.conf", conf)
        self.assertIn("set-hook -g pane-title-changed[42] 'if -F \"#{?#{@agentbox-names},", conf)
        self.assertIn("/usr/local/bin/agentbox-name keep #{q:session_name} #{q:pane_id}", conf)
        self.assertNotIn("#{!:", conf)   # tmux 3.4 doesn't evaluate it
        install = open(os.path.join(self.ROOT, "server/install.sh")).read()
        self.assertIn('"$SRC/bin/agentbox-name"', install)
        self.assertIn("agentbox-name tmux-options > ~/.config/agentbox/tmux-options.conf", install)
