"""
The journal Claude writes for you, without Claude: the model is a stub here, so
what is tested is everything around it --- what it is shown, when it is asked,
where the answer goes, and that a day you have already written on is left alone.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from tests.support import Sandbox

from cccenter import scanner, store
from cccenter.app import journal
from cccenter.app.settings import DEFAULT_SETTINGS
from cccenter.app.writing import Writer
from cccenter.render.journal import render_journal_facts, worth_noting


class ADay(Sandbox):
    """One project, one day, one question that ran a script and edited a file."""

    def setUp(self):
        super().setUp()
        self.st = dict(DEFAULT_SETTINGS)
        self.t0 = self.transcript_of_one_edit()
        self.day = self.t0.astimezone(scanner.parse_tz(self.st["tz"])).date().isoformat()
        self.cwd = str(self.project)
        self.calls = []
        self.w = Writer("box", lambda msg, level="info", host=None: None, cloud=self.cloud())

    def stub(self, text="## hello: 一個會打招呼的腳本\n\n**做了什麼**: 寫了 hello.py。\n"):
        def run(prompt, model, cwd=None):
            self.calls.append({"prompt": prompt, "model": model, "cwd": cwd})
            return text
        return run

    def collect(self):
        return journal.collect_day("box", self.day, self.st, "box")

    # -- what the model is shown

    def test_the_facts_carry_the_question_the_edit_and_the_agents_words(self):
        sessions, orphans = self.collect()
        text, level = render_journal_facts(sessions, self.cwd, self.day, orphans)
        self.assertEqual(level, 0)
        self.assertIn('<day date="%s"' % self.day, text)
        self.assertIn("<asked>\nadd a hello script\n</asked>", text)
        self.assertIn("<agent>\nWritten.\n</agent>", text)
        self.assertIn("<edited>hello.py (+2 −0)</edited>", text)
        self.assertNotIn("<commit ", text)                 # 不是 git repo, 沒有 commit 可對

    def test_a_long_day_is_trimmed_in_stages_but_never_the_question(self):
        sessions, _ = self.collect()
        turn = next(iter(sessions[0]["days"].values()))["turns"][0]
        turn["said"] = ["x" * 5000]
        turn["commands"] = ["\n".join(f"line {i}" for i in range(200))]
        full, level = render_journal_facts(sessions, self.cwd, self.day, budget=10 ** 9)
        self.assertEqual(level, 0)
        cut, level = render_journal_facts(sessions, self.cwd, self.day, budget=4000)
        self.assertGreater(level, 0)
        self.assertLess(len(cut), len(full))
        self.assertIn("add a hello script", cut)
        self.assertIn("略", cut)

    def test_commands_are_cut_to_the_ones_worth_noting(self):
        """指令佔一天九成的字, 對日誌卻沒貢獻 (量過); 只留報告那種眼光挑出來的幾筆,
        但寫檔的一定留 —— heredoc 寫的檔只有這裡看得到檔名。"""
        cmds = [
            "ls -la src/",                                          # 只看看
            "cd /Users/me/proj; git commit -m 'feat: x'",           # cd 剝掉, git 留
            "cd /Users/me/proj && npm test",
            "cat > src/new.py <<'EOF'\nprint(1)\nEOF",              # cat, 但寫檔
            "cat src/old.py",                                       # cat, 只看
            "grep -rn foo . > /dev/null 2>&1",                      # 丟進 /dev/null 不算寫
            "sed -i '' 's/a/b/' src/x.py",                          # sed -i 寫檔
            "python3 - <<'EOF'\nimport this\nEOF",
            "python3 - <<'EOF'\nimport that\nEOF",                  # 同一件事只記一次
        ]
        self.assertEqual(worth_noting(cmds), [
            "git commit -m 'feat: x'",
            "npm test",
            "cat > src/new.py (inline script)",
            "sed -i '' 's/a/b/' src/x.py",
            "python3 - (inline script)",
        ])
        self.assertEqual(len(worth_noting(["git status"] * 3 + [f"git log -{i}" for i in range(20)],
                                          limit=4)), 4)

    def test_only_projects_someone_asked_in_count(self):
        sessions, _ = self.collect()
        self.assertEqual(journal.projects_of(sessions, self.day), [self.cwd])
        self.assertEqual(journal.projects_of(sessions, "1999-01-01"), [])

    # -- writing the day

    def test_the_entry_reaches_the_cloud_and_the_cache_but_not_the_folder(self):
        res = journal.write_day(self.day, self.st, self.w, [], "box", run=self.stub())
        self.assertEqual([r["status"] for r in res], ["written"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["model"], "sonnet")
        self.assertTrue(self.calls[0]["prompt"].rstrip().endswith("只輸出日誌本身。"))
        row = store.get("journal", self.cwd, "box", self.day)
        self.assertIn("寫了 hello.py", row["body"])
        self.assertIn("寫了 hello.py", self.fake.rows()[0]["body"])
        self.assertFalse((self.project / "journal").exists())     # journal 不進專案資料夾

    def test_a_day_you_already_wrote_on_is_left_alone(self):
        self.w.save("journal", "box", self.cwd, self.day, "我自己補的話", self.st)
        res = journal.write_day(self.day, self.st, self.w, [], "box", run=self.stub())
        self.assertEqual([r["status"] for r in res], ["skipped"])
        self.assertEqual(self.calls, [])
        self.assertEqual(store.get("journal", self.cwd, "box", self.day)["body"], "我自己補的話")

    def test_an_empty_entry_does_not_count_as_written(self):
        """介面存過一個空的 journal 檔 (只有標題) —— 那不算你寫過。"""
        self.w.save("journal", "box", self.cwd, self.day, "   ", self.st)
        res = journal.write_day(self.day, self.st, self.w, [], "box", run=self.stub())
        self.assertEqual([r["status"] for r in res], ["written"])

    def test_force_redoes_it_and_dry_run_saves_nothing(self):
        self.w.save("journal", "box", self.cwd, self.day, "舊的", self.st)
        res = journal.write_day(self.day, self.st, self.w, [], "box", run=self.stub(),
                                dry_run=True)
        self.assertEqual([r["status"] for r in res], ["skipped"])   # 沒 --force 連問都不問
        res = journal.write_day(self.day, self.st, self.w, [], "box", run=self.stub("新的"),
                                force=True, dry_run=True)
        self.assertEqual([r["status"] for r in res], ["dry"])
        self.assertEqual(store.get("journal", self.cwd, "box", self.day)["body"], "舊的")
        res = journal.write_day(self.day, self.st, self.w, [], "box", run=self.stub("新的"),
                                force=True)
        self.assertEqual([r["status"] for r in res], ["written"])
        self.assertEqual(store.get("journal", self.cwd, "box", self.day)["body"], "新的")

    def test_facts_only_never_calls_the_model(self):
        res = journal.write_day(self.day, self.st, self.w, [], "box", run=self.stub(),
                                facts_only=True)
        self.assertEqual(res[0]["status"], "facts")
        self.assertIn("<asked>", res[0]["text"])
        self.assertEqual(self.calls, [])

    def test_a_model_failure_is_a_result_not_a_crash(self):
        def broken(prompt, model, cwd=None):
            raise ValueError("claude failed: no credits")
        res = journal.write_day(self.day, self.st, self.w, [], "box", run=broken)
        self.assertEqual(res[0]["status"], "error")
        self.assertIn("no credits", res[0]["error"])
        self.assertIsNone(store.get("journal", self.cwd, "box", self.day))

    def test_a_day_with_no_questions_writes_nothing(self):
        res = journal.write_day("1999-01-01", self.st, self.w, [], "box",
                                run=self.stub())
        self.assertEqual(res, [])
        self.assertEqual(self.calls, [])


class WhenToRun(unittest.TestCase):
    """The morning rule: once the clock is past journal_at, anything not finished is due."""

    def setUp(self):
        self.st = dict(DEFAULT_SETTINGS, journal_at="06:00", journal_auto=True)
        self.morning = datetime(2026, 9, 14, 6, 30, tzinfo=timezone.utc)

    def test_nothing_before_the_hour_everything_unwritten_after(self):
        early = self.morning.replace(hour=5, minute=59)
        self.assertEqual(journal.due_days(early, self.st, {}), [])
        self.assertEqual(journal.due_days(self.morning, self.st, {}),
                         ["2026-09-13", "2026-09-12", "2026-09-11"])

    def test_off_means_off(self):
        self.assertEqual(journal.due_days(self.morning, dict(self.st, journal_auto=False), {}), [])

    def test_a_finished_day_is_never_asked_again(self):
        runs = {}
        journal.record_run(runs, "2026-09-13", [{"status": "written"}, {"status": "skipped"}])
        self.assertTrue(runs["2026-09-13"]["done"])
        self.assertNotIn("2026-09-13", journal.due_days(self.morning, self.st, runs))

    def test_an_unfinished_day_waits_then_retries_a_bounded_number_of_times(self):
        runs = {}
        journal.record_run(runs, "2026-09-13", [{"status": "written"}, {"status": "unreachable"}])
        self.assertFalse(runs["2026-09-13"]["done"])
        runs["2026-09-13"]["at"] = self.morning.timestamp()
        soon = self.morning + timedelta(minutes=5)
        self.assertNotIn("2026-09-13", journal.due_days(soon, self.st, runs))
        later = self.morning + timedelta(seconds=journal.RETRY_AFTER)
        self.assertIn("2026-09-13", journal.due_days(later, self.st, runs))
        runs["2026-09-13"]["n"] = journal.MAX_ATTEMPTS
        self.assertNotIn("2026-09-13", journal.due_days(later, self.st, runs))

    def test_a_bad_time_falls_back_to_six(self):
        st = dict(self.st, journal_at="25:99")
        self.assertEqual(journal.due_days(self.morning.replace(hour=5), st, {}), [])
        self.assertTrue(journal.due_days(self.morning, st, {}))


if __name__ == "__main__":
    unittest.main()
