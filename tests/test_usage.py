"""
Plan usage comes from one place only: what Claude Code hands its status line.
The hook must never break that status line, and the snapshot must reflect a
window that Claude Code has since dropped, or it would keep showing a limit
that has already reset.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from unittest import mock

from tests.support import Sandbox

from cccenter import scanner
from cccenter.app import usage


PAYLOAD = {
    "session_id": "s1",
    "model": {"display_name": "Opus 5"},
    "context_window": {"total_input_tokens": 122_201, "context_window_size": 1_000_000},
    "rate_limits": {"five_hour": {"used_percentage": 7, "resets_at": 1_789_110_000},
                    "seven_day": {"used_percentage": 6, "resets_at": 1_789_500_000}},
}


class RecordingWhatTheStatusLineSaw(Sandbox):

    def setUp(self):
        super().setUp()
        # paths.py resolves STATE_DIR at import; point the module at the sandbox.
        self._patch = mock.patch.object(usage, "USAGE_FILE", self.state / "usage.json")
        self._patch.start()
        self._dir = mock.patch.object(usage, "STATE_DIR", self.state)
        self._dir.start()

    def tearDown(self):
        self._patch.stop()
        self._dir.stop()
        super().tearDown()

    def test_keeps_the_limits_and_the_context_of_that_session(self):
        snap = usage.record(PAYLOAD, at=1000)
        self.assertEqual(snap["rate_limits"]["five_hour"]["used_percentage"], 7)
        self.assertEqual(snap["sessions"]["s1"], {"used": 122_201, "size": 1_000_000, "at": 1000})
        self.assertEqual(usage.read()["model"], "Opus 5")

    def test_a_window_claude_code_dropped_is_dropped_here_too(self):
        usage.record(PAYLOAD, at=1000)
        later = dict(PAYLOAD, rate_limits={"seven_day": {"used_percentage": 9, "resets_at": 2}})
        snap = usage.record(later, at=2000)
        self.assertNotIn("five_hour", snap["rate_limits"])
        self.assertEqual(snap["rate_limits"]["seven_day"]["used_percentage"], 9)

    def test_an_api_key_user_has_no_limits_and_sees_none(self):
        usage.record(PAYLOAD, at=1000)
        snap = usage.record({"session_id": "s1", "rate_limits": {}}, at=2000)
        self.assertEqual(snap["rate_limits"], {})

    def test_sessions_nobody_has_touched_in_a_week_are_forgotten(self):
        usage.record(PAYLOAD, at=1000)
        usage.record(dict(PAYLOAD, session_id="s2"), at=1000 + 8 * 86400)
        self.assertEqual(list(usage.read()["sessions"]), ["s2"])

    def test_the_scanner_reads_the_same_file(self):
        usage.record(PAYLOAD, at=1000)
        self.assertEqual(scanner.read_usage()["at"], 1000)

    def test_the_hook_passes_the_json_on_and_never_breaks_the_chain(self):
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(PAYLOAD).encode()))
        with mock.patch("sys.stdin", stdin):
            code = usage.statusline_main(
                ["python3", "-c", "import json,sys; json.load(sys.stdin)['model']"])
        self.assertEqual(code, 0)
        self.assertEqual(usage.read()["rate_limits"]["seven_day"]["used_percentage"], 6)

        garbage = io.TextIOWrapper(io.BytesIO(b"not json"))
        with mock.patch("sys.stdin", garbage), redirect_stdout(io.StringIO()):
            self.assertEqual(usage.statusline_main(["true"]), 0)


class TheContextOfEachSession(Sandbox):

    def test_the_last_call_is_what_is_in_the_window_now(self):
        self.transcript_of_one_edit()
        lo, hi = self.window()
        s = scanner.collect("testbox", lo, hi, None, False, None)[0]
        # The final assistant record carried no input tokens, so the one
        # before it -- 120 fresh, nothing cached -- is the current context.
        self.assertEqual(s["ctx"]["used"], 120)
        self.assertEqual(s["ctx"]["size"], 1_000_000)
        self.assertEqual(s["ctx"]["model"], "claude-opus-5")

    def test_window_sizes_follow_the_model(self):
        self.assertEqual(scanner.context_window("claude-opus-5"), 1_000_000)
        self.assertEqual(scanner.context_window("claude-haiku-4-5-20251001"), 200_000)
        self.assertEqual(scanner.context_window("claude-sonnet-4-5"), 200_000)
        self.assertEqual(scanner.context_window("claude-sonnet-4-5[1m]"), 1_000_000)
        self.assertEqual(scanner.context_window(None), 200_000)
