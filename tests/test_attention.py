"""
"Is this waiting for you?" is the one judgement in cc-center that interrupts you,
so it has to be wrong in the quiet direction. These tests are the cases that
produced false alarms before: a finished session, a one-shot run, and something
that stopped hours ago.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from tests.support import Sandbox  # noqa: F401  (keeps sys.path pointing at src)

from cccenter.app.attention import attention_of
from cccenter.app.settings import DEFAULT_SETTINGS


def session(state, age_s, tool=None, entrypoint="cli", interrupted=False):
    when = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    return {"session_id": "s1", "host": "box", "cwd": "/tmp/proj",
            "entrypoint": entrypoint,
            "tail": {"state": state, "tool": tool, "since": when.isoformat(),
                     "at": when.isoformat(), "interrupted": interrupted}}


class WhenItSpeaksUp(unittest.TestCase):
    st = DEFAULT_SETTINGS

    def test_a_session_that_just_stopped_is_not_an_interruption_yet(self):
        self.assertIsNone(attention_of(session("waiting", 5), self.st, alive=True))

    def test_after_the_threshold_it_is_waiting_for_you(self):
        a = attention_of(session("waiting", 120), self.st, alive=True)
        self.assertEqual(a["kind"], "waiting")

    def test_a_file_tool_that_hangs_is_probably_a_permission_prompt(self):
        a = attention_of(session("tool", 300, tool="Edit"), self.st, alive=True)
        self.assertEqual(a["kind"], "permission")

    def test_a_slow_tool_is_only_worth_saying_after_a_long_time(self):
        self.assertIsNone(attention_of(session("tool", 300, tool="Bash"), self.st, alive=True))
        a = attention_of(session("tool", 1200, tool="Bash"), self.st, alive=True)
        self.assertEqual(a["kind"], "stuck")

    def test_with_no_process_left_a_stopped_session_is_over_not_waiting(self):
        self.assertIsNone(attention_of(session("waiting", 600), self.st, alive=False))

    def test_with_no_process_left_a_half_finished_tool_died(self):
        a = attention_of(session("tool", 600, tool="Edit"), self.st, alive=False)
        self.assertEqual(a["kind"], "died")

    def test_a_one_shot_run_never_waits_for_anyone(self):
        self.assertIsNone(
            attention_of(session("waiting", 600, entrypoint="sdk-py"), self.st, alive=True))

    def test_this_morning_is_not_now(self):
        self.assertIsNone(attention_of(session("waiting", 5 * 3600), self.st, alive=True))

    def test_not_knowing_whether_anything_is_alive_falls_back_to_the_transcript(self):
        a = attention_of(session("waiting", 120), self.st, alive=None)
        self.assertEqual(a["kind"], "waiting")
        self.assertIsNone(a["alive"])


if __name__ == "__main__":
    unittest.main()
