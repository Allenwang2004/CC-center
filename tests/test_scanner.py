"""
The scanner reads a transcript the way a person would summarise it: one question,
what it touched, what it cost. These tests pin the readings that other parts of
the tool depend on being exactly right.
"""

from __future__ import annotations

import unittest

from tests.support import Sandbox

from cccenter import scanner


class ReadingATranscript(Sandbox):

    def collect(self):
        lo, hi = self.window()
        return scanner.collect("testbox", lo, hi, None, False, None)

    def test_one_question_one_file(self):
        self.transcript_of_one_edit()
        sessions = self.collect()
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s["cwd"], str(self.project))
        self.assertEqual(s["branch"], "main")
        day = next(iter(s["days"].values()))
        self.assertEqual(day["prompts"], ["add a hello script"])
        self.assertEqual(len(day["turns"]), 1)
        self.assertEqual(list(day["files"]), [f"{self.project}/hello.py"])
        self.assertEqual(day["added"], 2)       # 從 toolUseResult 算出來的行數
        self.assertEqual(day["tok_out"], 52)

    def test_injected_records_are_not_questions(self):
        """isMeta / system-reminder 是工具塞的, 不是你打的。"""
        self.transcript_of_one_edit()
        day = next(iter(self.collect()[0]["days"].values()))
        self.assertEqual(len(day["prompts"]), 1)

    def test_a_slash_command_is_a_turn_but_not_a_question(self):
        rec = {"type": "user", "uuid": "u9", "cwd": str(self.project),
               "message": {"role": "user",
                           "content": "<command-name>/compact</command-name>"}}
        self.transcript_of_one_edit()
        import json
        p = self.claude / "projects" / "-tmp-proj" / "s1.jsonl"
        from datetime import datetime, timezone
        rec["timestamp"] = datetime.now(timezone.utc).isoformat()
        p.write_text(p.read_text(encoding="utf-8") + json.dumps(rec) + "\n",
                     encoding="utf-8")
        day = next(iter(self.collect()[0]["days"].values()))
        self.assertEqual(len(day["prompts"]), 1)          # 還是一題
        self.assertEqual(len(day["turns"]), 2)            # 但是兩列
        self.assertEqual(dict(day["slash"]), {"/compact": 1})

    def test_a_session_stopped_talking_is_waiting_for_you(self):
        self.transcript_of_one_edit()
        self.assertEqual(self.collect()[0]["tail"]["state"], "waiting")

    def test_an_unanswered_tool_is_the_last_thing_that_happened(self):
        """發出去沒回來的工具 —— 不是還在跑, 就是在等你按同意。"""
        from datetime import datetime, timedelta, timezone
        t0 = datetime.now(timezone.utc) - timedelta(minutes=5)
        cwd = str(self.project)
        self.write_transcript("s2", [
            {"type": "user", "uuid": "u1", "timestamp": t0.isoformat(), "cwd": cwd,
             "message": {"role": "user", "content": "edit the file"}},
            {"type": "assistant", "uuid": "a1",
             "timestamp": (t0 + timedelta(seconds=5)).isoformat(), "cwd": cwd,
             "message": {"content": [{"type": "tool_use", "id": "t9", "name": "Edit",
                                      "input": {"file_path": f"{cwd}/hello.py"}}]}},
        ])
        s = [x for x in self.collect() if x["session_id"] == "s2"][0]
        self.assertEqual(s["tail"]["state"], "tool")
        self.assertEqual(s["tail"]["tool"], "Edit")

    def test_json_round_trip_keeps_the_turns(self):
        """遠端就是這樣把結果送回來的; 掉東西的話一路都對不上。"""
        self.transcript_of_one_edit()
        s = self.collect()[0]
        back = scanner.from_json(scanner.to_json(s))
        day = next(iter(back["days"].values()))
        self.assertEqual(day["prompts"], ["add a hello script"])
        self.assertEqual(len(day["turns"]), 1)
        self.assertEqual(day["turns"][0]["files"], next(iter(s["days"].values()))["turns"][0]["files"])

    def test_a_command_is_kept_as_it_was_run_and_what_the_agent_said_with_it(self):
        """journal 要的是原文: heredoc 幾行就幾行, agent 解釋為什麼的那段話也要在。"""
        from datetime import datetime, timedelta, timezone
        t0 = datetime.now(timezone.utc) - timedelta(minutes=5)
        at = lambda n: (t0 + timedelta(seconds=n)).isoformat()      # noqa: E731
        cwd = str(self.project)
        script = "python3 - <<'EOF'\nimport sys\nprint(sys.version)\nEOF"
        self.write_transcript("s4", [
            {"type": "assistant", "uuid": "a0", "timestamp": at(0), "cwd": cwd,
             "message": {"model": "claude-opus-5",
                         "content": [{"type": "text", "text": "said before anyone asked"}]}},
            {"type": "user", "uuid": "u1", "timestamp": at(1), "cwd": cwd,
             "message": {"role": "user", "content": "check the python version"}},
            {"type": "assistant", "uuid": "a1", "timestamp": at(2), "cwd": cwd,
             "requestId": "r1",
             "message": {"model": "claude-opus-5",
                         "content": [{"type": "text", "text": "Checking, because the build needs 3.12."}]}},
            {"type": "assistant", "uuid": "a1b", "timestamp": at(2), "cwd": cwd,
             "requestId": "r1",                                  # 同一則回覆的下一個 block
             "message": {"model": "claude-opus-5",
                         "content": [{"type": "tool_use", "id": "t1", "name": "Bash",
                                      "input": {"command": script}}]}},
            {"type": "assistant", "uuid": "a1c", "timestamp": at(2), "cwd": cwd,
             "requestId": "r1",                                  # /fork 複製出來的重複紀錄
             "message": {"model": "claude-opus-5",
                         "content": [{"type": "text", "text": "Checking, because the build needs 3.12."}]}},
            {"type": "assistant", "uuid": "a2", "timestamp": at(3), "cwd": cwd,
             "requestId": "r2",
             "message": {"model": "<synthetic>",
                         "content": [{"type": "text", "text": "No response requested."}]}},
            {"type": "assistant", "uuid": "a3", "timestamp": at(4), "cwd": cwd,
             "requestId": "r3",
             "message": {"model": "claude-opus-5",
                         "content": [{"type": "text", "text": "It is 3.12, so we are fine."}]}},
        ])
        s = [x for x in self.collect() if x["session_id"] == "s4"][0]
        turn = next(iter(s["days"].values()))["turns"][0]
        self.assertEqual(turn["commands"], [script])            # 原文, 含換行
        self.assertEqual(turn["said"], ["Checking, because the build needs 3.12.",
                                        "It is 3.12, so we are fine."])

        # 精簡版 JSON (介面、報告) 不帶 said; 完整版 (journal) 帶, 而且來回不掉東西
        lean = scanner.to_json(s)["days"]
        self.assertEqual(next(iter(lean.values()))["turns"][0]["said"], [])
        back = scanner.from_json(scanner.to_json(s, full=True))
        t2 = next(iter(back["days"].values()))["turns"][0]
        self.assertEqual(t2["said"], turn["said"])
        self.assertEqual(t2["commands"], [script])

    def test_scratch_files_are_counted_apart(self):
        """/tmp 底下的腳本不是專案產出。"""
        from datetime import datetime, timezone
        cwd = str(self.project)
        self.write_transcript("s3", [
            {"type": "user", "uuid": "u1", "timestamp": datetime.now(timezone.utc).isoformat(),
             "cwd": cwd, "message": {"role": "user", "content": "try something"}},
            {"type": "assistant", "uuid": "a1",
             "timestamp": datetime.now(timezone.utc).isoformat(), "cwd": cwd,
             "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Write",
                                      "input": {"file_path": "/tmp/scratch.py"}}]}},
        ])
        s = [x for x in self.collect() if x["session_id"] == "s3"][0]
        day = next(iter(s["days"].values()))
        self.assertEqual(dict(day["files"]), {})
        self.assertEqual(list(day["scratch"]), ["/tmp/scratch.py"])


class SmallReadings(unittest.TestCase):

    def test_time_and_number_shapes(self):
        self.assertEqual(scanner.human_time(59 * 60), "59m")
        self.assertEqual(scanner.human_time(3 * 3600 + 5 * 60), "3h05m")
        self.assertEqual(scanner.human_num(12_500), "12.5k")
        self.assertEqual(scanner.human_num(2_000_000), "2.0M")

    def test_plural(self):
        self.assertEqual(scanner.plural(1, "session"), "1 session")
        self.assertEqual(scanner.plural(2, "session"), "2 sessions")

    def test_a_fixed_offset_works_without_tzdata(self):
        """遠端常常沒有 tzdata, 所以位移一定要能用。"""
        self.assertIsNotNone(scanner.parse_tz("+08:00"))
        self.assertIsNotNone(scanner.parse_tz("Asia/Taipei"))

    def test_patch_stats_counts_both_directions(self):
        add, rem = scanner.patch_stats(
            {"structuredPatch": [{"lines": ["+a", "+b", "-c", " d"]}]})
        self.assertEqual((add, rem), (2, 1))


if __name__ == "__main__":
    unittest.main()


class WhatItCost(Sandbox):
    """Tokens are priced at list, once per API response, however many records it took."""

    def response(self, rid, ts, usage, blocks=2, model="claude-opus-5", cwd="/tmp/proj"):
        # Claude Code writes one record per content block; the usage repeats on each.
        return [{"type": "assistant", "uuid": f"{rid}-{i}", "requestId": rid, "timestamp": ts,
                 "cwd": cwd, "message": {"id": f"msg_{rid}", "model": model, "usage": usage,
                                         "content": [{"type": "text", "text": "…"}]}}
                for i in range(blocks)]

    def test_list_prices_per_token_class(self):
        u = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
             "cache_read_input_tokens": 1_000_000,
             "cache_creation_input_tokens": 2_000_000,
             "cache_creation": {"ephemeral_5m_input_tokens": 1_000_000,
                                "ephemeral_1h_input_tokens": 1_000_000}}
        # opus 5: $5 in, $25 out, reads at a tenth, 5m writes at 1.25x, 1h writes at 2x
        self.assertAlmostEqual(scanner.api_cost("claude-opus-5", u), 5 + 25 + 0.5 + 6.25 + 10)
        self.assertAlmostEqual(scanner.api_cost("claude-haiku-4-5-20251001", u),
                               1 + 5 + 0.1 + 1.25 + 2)
        # no TTL breakdown (older records): the whole write is taken as 5-minute
        old = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 1_000_000}
        self.assertAlmostEqual(scanner.api_cost("claude-sonnet-5", old), 2 * 1.25)

    def test_a_response_written_as_three_records_is_counted_once(self):
        from datetime import datetime, timedelta, timezone
        t0 = datetime.now(timezone.utc) - timedelta(minutes=5)
        usage = {"input_tokens": 100, "output_tokens": 10}
        self.write_transcript("s1",
            [{"type": "user", "uuid": "u1", "timestamp": t0.isoformat(), "cwd": "/tmp/proj",
              "message": {"role": "user", "content": "hi"}}]
            + self.response("r1", (t0 + timedelta(seconds=5)).isoformat(), usage, blocks=3))
        lo, hi = self.window()
        day = next(iter(scanner.collect("t", lo, hi, None, False, None)[0]["days"].values()))
        self.assertEqual(day["tok_in"], 100)
        self.assertEqual(day["assistant_turns"], 1)
        totals = scanner.daily_totals()
        self.assertEqual(sum(d["calls"] for d in totals.values()), 1)

    def test_every_day_on_record_and_a_forked_copy_does_not_double(self):
        from datetime import datetime, timedelta, timezone
        t0 = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)
        usage = {"input_tokens": 1_000_000, "output_tokens": 0}
        a = self.response("r1", t0.isoformat(), usage)
        b = self.response("r2", (t0 + timedelta(days=1)).isoformat(), usage)
        self.write_transcript("s1", a + b)
        self.write_transcript("s1-fork", a + b + self.response("r3", (t0 + timedelta(days=1)).isoformat(), usage))
        cache = {}
        totals = scanner.daily_totals(cache)
        self.assertEqual(sorted(totals), ["2026-01-05", "2026-01-06"])
        self.assertAlmostEqual(totals["2026-01-05"]["cost"], 5.0)
        self.assertAlmostEqual(totals["2026-01-06"]["cost"], 10.0)      # r2 once, plus r3
        self.assertEqual(totals["2026-01-06"]["models"], {"claude-opus-5": 10.0})
        # a second pass with the cache reads nothing and says the same
        self.assertEqual(scanner.daily_totals(cache), totals)
        self.assertEqual(len(cache), 2)
