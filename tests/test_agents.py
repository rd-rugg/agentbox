"""Agents outside agentbox's tmux sessions (server/bin/agentbox-agents): which count, what
started them, their conversations, and whether they are working."""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_loader = importlib.machinery.SourceFileLoader("agentbox_agents", os.path.join(ROOT, "server", "bin", "agentbox-agents"))
agents = importlib.util.module_from_spec(importlib.util.spec_from_loader(_loader.name, _loader))
_loader.exec_module(agents)


def proc(ppid, comm, exe=None, cwd="/home/dev", cpu=0):
    return {"ppid": ppid, "comm": comm, "exe": exe or comm, "cwd": cwd, "cpu": cpu, "fds": []}


class AgentsOutsideTabs(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.now = time.time()

    def conversation(self, folder, age):
        d = os.path.join(self.home, ".claude", "projects", agents.dashed(folder))
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"c{age}.jsonl")
        open(path, "w").close()
        os.utime(path, (self.now - age, self.now - age))

    def codex_session(self, name, folder, age):
        d = os.path.join(self.home, ".codex", "sessions", "2026", "11", "11")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name)
        with open(path, "w") as fh:
            fh.write(json.dumps({"type": "session_meta", "payload": {"cwd": folder}}) + "\n")
        os.utime(path, (self.now - age, self.now - age))
        return path

    def rows(self, procs, busy=(), files=lambda pid: []):
        return agents.rows(procs, self.now, set(busy), self.home, files)

    def test_tab_agents_are_left_out_and_others_listed_with_their_starter(self):
        procs = {10: proc(1, "tmux: server"), 11: proc(10, "box"), 12: proc(11, "claude", cwd="/home/dev/work/a"),
                 20: proc(1, "t3"), 21: proc(20, "claude", cwd="/home/dev/work/b")}
        self.conversation("/home/dev/work/b", 60)
        [row] = self.rows(procs)
        self.assertEqual((row["pid"], row["agent"], row["folder"], row["starter"]), (21, "claude", "/home/dev/work/b", "t3"))
        self.assertTrue(row["working"])

    def test_the_starter_is_the_first_non_shell_ancestor_by_name_only(self):
        procs = {30: proc(1, "node", exe="node"), 31: proc(30, "bash"), 32: proc(31, "claude")}
        self.assertEqual(self.rows(procs)[0]["starter"], "node")

    def test_an_agent_with_a_claude_child_counts_once(self):
        procs = {40: proc(1, "sshd"), 41: proc(40, "claude"), 42: proc(41, "claude")}
        self.assertEqual([r["pid"] for r in self.rows(procs)], [41])

    def test_without_a_tmux_server_every_agent_is_outside(self):
        procs = {50: proc(1, "systemd"), 51: proc(50, "codex"), 52: proc(1, "claude")}
        self.assertEqual(sorted(r["pid"] for r in self.rows(procs)), [51, 52])

    def test_claude_activity_comes_from_its_own_folder_and_is_empty_without_one(self):
        procs = {60: proc(1, "t3"), 61: proc(60, "claude", cwd="/home/dev/work/old"), 62: proc(60, "claude", cwd="/x")}
        self.conversation("/home/dev/work/old", 3 * 3600)
        rows = {r["pid"]: r for r in self.rows(procs)}
        self.assertEqual(rows[61]["last"], int(self.now - 3 * 3600))
        self.assertFalse(rows[61]["working"])
        self.assertIsNone(rows[62]["last"])

    def test_a_codex_app_server_gives_one_row_per_open_session(self):
        a = self.codex_session("rollout-a.jsonl", "/home/dev/work/a", 30)
        b = self.codex_session("rollout-b.jsonl", "/home/dev/work/b", 2 * 3600)
        procs = {70: proc(1, "t3"), 71: proc(70, "codex"), 72: proc(71, "bash")}
        files = {71: [a, "/dev/null"], 72: [b]}
        rows = self.rows(procs, files=lambda pid: files.get(pid, []))
        self.assertEqual([(r["folder"], r["working"]) for r in rows], [("/home/dev/work/a", True), ("/home/dev/work/b", False)])

    def test_a_codex_process_without_open_sessions_is_idle_with_no_folder(self):
        procs = {80: proc(1, "t3"), 81: proc(80, "codex")}
        [row] = self.rows(procs)
        self.assertEqual((row["folder"], row["last"], row["working"]), (None, None, False))

    def test_a_long_tool_call_counts_as_working_by_cpu(self):
        procs = {90: proc(1, "t3"), 91: proc(90, "claude", cwd="/w")}
        self.conversation("/w", 5 * 3600)
        self.assertFalse(self.rows(procs)[0]["working"])
        self.assertTrue(self.rows(procs, busy={91})[0]["working"])

    def test_cpu_counts_the_whole_process_tree(self):
        procs = {1: proc(0, "init"), 90: proc(1, "claude", cpu=5), 91: proc(90, "make", cpu=100), 92: proc(1, "other", cpu=7)}
        self.assertEqual(agents.cpu(procs, agents.tree(procs, 90)), 105)

    def test_an_idle_agents_timers_are_not_work_but_a_build_is(self):
        before = {1: proc(0, "init"), 90: proc(1, "claude", cpu=500)}
        idle = {1: proc(0, "init"), 90: proc(1, "claude", cpu=501)}          # measured on a box: 1 tick in 2 s
        building = {1: proc(0, "init"), 90: proc(1, "claude", cpu=505), 95: proc(90, "cc1", cpu=60)}
        self.assertEqual(agents.busy_pids(before, idle, {90}, 100), set())
        self.assertEqual(agents.busy_pids(before, building, {90}, 100), {90})   # a child started in the sample counts

    def test_a_child_that_ended_during_the_sample_still_counts(self):
        before = {1: proc(0, "init"), 90: proc(1, "claude", cpu=5), 91: proc(90, "cc1", cpu=150)}
        after = {1: proc(0, "init"), 90: proc(1, "claude", cpu=5 + 180)}       # reaped: its 180 ticks move to the parent
        self.assertEqual(agents.busy_pids(before, after, {90}, 100), {90})

    def test_a_subagents_transcript_counts_for_its_conversation(self):
        self.conversation("/w", 3 * 3600)
        sub = os.path.join(self.home, ".claude", "projects", agents.dashed("/w"), "abc", "subagents")
        os.makedirs(sub)
        open(os.path.join(sub, "agent-1.jsonl"), "w").close()
        procs = {1: proc(0, "init"), 91: proc(1, "claude", cwd="/w")}
        self.assertTrue(self.rows(procs)[0]["working"])

    def test_a_tab_sessions_transcript_is_not_an_outside_agents_work(self):
        tab_id, own_id = "11111111-2222-3333-4444-555555555555", "66666666-7777-8888-9999-000000000000"
        project = os.path.join(self.home, ".claude", "projects", agents.dashed("/w"))
        os.makedirs(project)
        for name, age in ((tab_id, 10), (own_id, 3 * 3600)):
            path = os.path.join(project, name + ".jsonl")
            open(path, "w").close()
            os.utime(path, (self.now - age, self.now - age))
        tab = {10: proc(1, "tmux: server"), 11: proc(10, "claude", cwd="/w")}
        tab[11]["args"] = ["--session-id", tab_id]
        named = {**tab, 20: proc(1, "t3"), 21: proc(20, "claude", cwd="/w")}
        named[21]["args"] = ["--resume", own_id]
        self.assertFalse(self.rows(named)[0]["working"])         # its own conversation is old
        unnamed = {**tab, 20: proc(1, "t3"), 21: proc(20, "claude", cwd="/w")}
        self.assertFalse(self.rows(unnamed)[0]["working"])       # the folder's newest is the tab's: not counted

    def test_no_agents_prints_nothing(self):
        self.assertEqual(self.rows({1: proc(0, "init"), 2: proc(1, "sshd")}), [])

    def test_the_real_proc_table_parses(self):
        procs = agents.read_procs()
        self.assertIn(os.getpid(), procs)
        self.assertEqual(procs[os.getpid()]["ppid"], os.getppid())


if __name__ == "__main__":
    unittest.main()
