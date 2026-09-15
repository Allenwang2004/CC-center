"""
Shared scaffolding. Every test runs against a throwaway state directory and a
throwaway ~/.claude, so nothing here can touch your real database, your real
projects, or anything you wrote.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class Sandbox(unittest.TestCase):
    """A temp CC_CENTER_STATE, a temp CLAUDE_CONFIG_DIR, and a temp project."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.state = self.tmp / "state"
        self.claude = self.tmp / "claude"
        self.project = self.tmp / "proj"
        (self.claude / "projects").mkdir(parents=True)
        self.project.mkdir()
        self._env = {"CC_CENTER_STATE": os.environ.get("CC_CENTER_STATE"),
                     "CLAUDE_CONFIG_DIR": os.environ.get("CLAUDE_CONFIG_DIR")}
        os.environ["CC_CENTER_STATE"] = str(self.state)
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.claude)
        from cccenter import store
        store.connect(self.state / "test.db")           # never the real one

    def tearDown(self):
        from cccenter import store
        if store._CONN is not None:                      # noqa: SLF001 - test teardown
            store._CONN.close()
            store._CONN, store._PATH = None, None
        if getattr(self, "_fake", None) is not None:
            self._fake.__exit__(None, None, None)
            self._fake = None
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    # -- 一顆假的 Supabase, 已經登入 --------------------------------------

    def cloud(self):
        """一個對著本機假 Supabase、已經用驗證碼登入的 Cloud。同一個測試裡共用。"""
        if getattr(self, "_cloud", None) is not None:
            return self._cloud
        from cccenter.cloud import Cloud
        from tests.fake_supabase import ANON, CODE, FakeSupabase
        self._fake = FakeSupabase().__enter__()
        self.fake = self._fake
        c = Cloud(self._fake.url, ANON, self.state / "auth.json", timeout=5)
        c.request_code("you@example.com")
        c.verify_code("you@example.com", CODE)
        self._cloud = c
        return c

    # -- 造一份 transcript ------------------------------------------------

    def write_transcript(self, name="s1", records=()):
        d = self.claude / "projects" / "-tmp-proj"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{name}.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n",
                        encoding="utf-8")
        return path

    def transcript_of_one_edit(self, name="s1"):
        """一題 → 一次 Write → 結果回來。夠真實, 也夠小。"""
        t0 = datetime.now(timezone.utc) - timedelta(minutes=10)
        at = lambda n: (t0 + timedelta(seconds=n)).isoformat()      # noqa: E731
        cwd = str(self.project)
        self.write_transcript(name, [
            {"type": "user", "uuid": "u1", "timestamp": at(0), "cwd": cwd,
             "gitBranch": "main", "version": "2.0.0", "entrypoint": "cli",
             "message": {"role": "user", "content": "add a hello script"}},
            {"type": "assistant", "uuid": "a1", "timestamp": at(20), "cwd": cwd,
             "message": {"model": "claude-opus-5",
                         "usage": {"input_tokens": 120, "output_tokens": 40},
                         "content": [{"type": "tool_use", "id": "t1", "name": "Write",
                                      "input": {"file_path": f"{cwd}/hello.py"}}]}},
            {"type": "user", "uuid": "u2", "timestamp": at(24), "cwd": cwd,
             "toolUseResult": {"type": "create", "filePath": f"{cwd}/hello.py",
                               "content": "print('hi')\nprint('there')"},
             "message": {"role": "user",
                         "content": [{"type": "tool_result", "tool_use_id": "t1"}]}},
            {"type": "user", "uuid": "u3", "timestamp": at(25), "cwd": cwd,
             "isMeta": True,
             "message": {"role": "user", "content": "<system-reminder>noise</system-reminder>"}},
            {"type": "assistant", "uuid": "a2", "timestamp": at(26), "cwd": cwd,
             "message": {"model": "claude-opus-5", "usage": {"output_tokens": 12},
                         "content": [{"type": "text", "text": "Written."}]}},
            {"type": "cost-state", "totalCostUSD": 0.25, "totalLinesAdded": 2},
        ])
        return t0

    def window(self, minutes=60):
        now = datetime.now(timezone.utc)
        return now - timedelta(minutes=minutes), now + timedelta(minutes=minutes)
