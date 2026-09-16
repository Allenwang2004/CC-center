"""
CLAUDE.md files are edited in place, so the thing to get right is the place:
the global file under CLAUDE_CONFIG_DIR, the project file under .claude/, a
missing file offered rather than invented, and a remote machine asked once for
all of its files.
"""

from __future__ import annotations

import json
import subprocess
import unittest

from tests.support import Sandbox

from cccenter.app import claudemd


class Local(Sandbox):

    def test_global_and_project_files_are_listed_whether_or_not_they_exist(self):
        (self.claude / "CLAUDE.md").write_text("# global\n", encoding="utf-8")
        files = claudemd.collect("box", [], [{"cwd": str(self.project), "host": "box",
                                               "exists": True, "last_active": 1}])
        by = {(f["scope"], f["cwd"]): f for f in files}
        g = by[("global", None)]
        self.assertTrue(g["exists"])
        self.assertEqual(g["body"], "# global\n")
        self.assertEqual(g["path"], str(self.claude / "CLAUDE.md"))
        pr = by[("project", str(self.project))]
        self.assertFalse(pr["exists"])
        self.assertEqual(pr["body"], "")
        self.assertEqual(pr["path"], f"{self.project}/.claude/CLAUDE.md")

    def test_a_project_whose_folder_is_gone_is_not_offered(self):
        files = claudemd.collect("box", [], [{"cwd": "/nowhere", "host": "box", "exists": False}])
        self.assertEqual([f["scope"] for f in files], ["global"])

    def test_writing_creates_the_folder_and_the_file(self):
        path = claudemd.write("box", "box", str(self.project), "be terse")
        self.assertEqual(path, f"{self.project}/.claude/CLAUDE.md")
        self.assertEqual((self.project / ".claude" / "CLAUDE.md").read_text(encoding="utf-8"),
                         "be terse\n")
        files = claudemd.collect("box", [], [{"cwd": str(self.project), "host": "box",
                                               "exists": True}])
        self.assertTrue(next(f for f in files if f["scope"] == "project")["exists"])

    def test_writing_the_global_file_lands_under_claude_config_dir(self):
        path = claudemd.write("box", "", None, "# rules")
        self.assertEqual(path, str(self.claude / "CLAUDE.md"))
        self.assertEqual((self.claude / "CLAUDE.md").read_text(encoding="utf-8"), "# rules\n")


class Remote(Sandbox):
    """ssh is stubbed: the command line is what is under test."""

    def setUp(self):
        super().setUp()
        self.calls = []

    def fake_run(self, answer):
        def run(cmd, **kw):
            self.calls.append((cmd, kw))
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(answer).encode(), stderr=b"")
        return run

    def test_one_ssh_per_machine_asks_for_every_path_at_once(self):
        answer = {"~/.claude/CLAUDE.md": {"exists": True, "body": "g", "mtime": 1},
                  "/srv/app/.claude/CLAUDE.md": {"exists": False}}
        files = claudemd.collect("box", ["lab"],
                                 [{"cwd": "/srv/app", "host": "lab", "exists": True},
                                  {"cwd": "/srv/other", "host": "off", "exists": True}],
                                 run=self.fake_run(answer))
        self.assertEqual(len(self.calls), 1)                 # 一台一次, 關掉的機器不問
        cmd = self.calls[0][0]
        self.assertEqual(cmd[:1], ["ssh"])
        self.assertIn("lab", cmd)
        self.assertIn("python3 -c", cmd[-1])
        self.assertIn("'/srv/app/.claude/CLAUDE.md'", cmd[-1])
        remote = [f for f in files if f["host"] == "lab"]
        self.assertEqual({(f["scope"], f["exists"]) for f in remote},
                         {("global", True), ("project", False)})
        self.assertEqual([f["host"] for f in files if f["cwd"] == "/srv/other"], [])

    def test_an_unreachable_machine_reports_an_error_not_an_empty_file(self):
        def run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 255, stdout=b"", stderr=b"ssh: connect refused")
        files = claudemd.collect("box", ["lab"], [], run=run)
        lab = next(f for f in files if f["host"] == "lab")
        self.assertFalse(lab["exists"])
        self.assertIn("refused", lab["error"])

    def test_writing_remotely_makes_the_folder_first(self):
        def run(cmd, **kw):
            self.calls.append((cmd, kw))
            return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")
        claudemd.write("box", "lab", "/srv/my app", "hi", run=run)
        cmd, kw = self.calls[0]
        self.assertEqual(cmd[-1], "mkdir -p '/srv/my app/.claude' && cat > '/srv/my app/.claude/CLAUDE.md'")
        self.assertEqual(kw["input"], b"hi\n")
        claudemd.write("box", "lab", None, "g", run=run)
        self.assertIn('"$HOME/.claude/CLAUDE.md"', self.calls[1][0][-1])

    def test_a_failed_remote_write_raises(self):
        def run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"Permission denied")
        with self.assertRaises(OSError) as cm:
            claudemd.write("box", "lab", "/srv/app", "x", run=run)
        self.assertIn("Permission denied", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
