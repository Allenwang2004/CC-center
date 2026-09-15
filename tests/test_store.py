"""
The database is the only copy of what you wrote that cannot be recomputed, so
these tests are about not losing it: writing, rewriting, adopting what was
already on disk, and never adopting the same file twice.
"""

from __future__ import annotations

import unittest

from tests.support import Sandbox

from cccenter import store


class Entries(Sandbox):

    def test_put_and_get(self):
        row = store.put("note", str(self.project), "box", "2026-09-10-0930", "some thought")
        self.assertEqual(row["body"], "some thought")
        self.assertEqual(store.get("note", str(self.project), "box", "2026-09-10-0930")["body"],
                         "some thought")

    def test_rewriting_keeps_the_title_unless_you_clear_it(self):
        cwd = str(self.project)
        store.put("note", cwd, "box", "2026-09-10-0930", "first", title="Named")
        store.put("note", cwd, "box", "2026-09-10-0930", "second")           # title=None
        self.assertEqual(store.get("note", cwd, "box", "2026-09-10-0930")["title"], "Named")
        store.put("note", cwd, "box", "2026-09-10-0930", "third", title="")  # 真的清掉
        self.assertEqual(store.get("note", cwd, "box", "2026-09-10-0930")["title"], "")

    def test_a_second_note_in_the_same_minute_gets_its_own_ref(self):
        cwd = str(self.project)
        first = store.next_note_ref(cwd, "box")
        store.put("note", cwd, "box", first, "one")
        second = store.next_note_ref(cwd, "box")
        self.assertNotEqual(first, second)
        self.assertTrue(second.startswith(first))

    def test_previous_journal_is_where_the_last_one_stopped(self):
        cwd = str(self.project)
        store.put("journal", cwd, "box", "2026-09-01", "older")
        store.put("journal", cwd, "box", "2026-09-05", "newer")
        prev = store.previous_journal(cwd, "box", "2026-09-10")
        self.assertEqual(prev["ref"], "2026-09-05")
        self.assertIsNone(store.previous_journal(cwd, "box", "2026-09-01"))

    def test_a_ref_that_is_not_a_date_is_refused(self):
        with self.assertRaises(ValueError):
            store.validate("journal", "notes-about-tuesday")
        with self.assertRaises(ValueError):
            store.validate("note", "2026-09-10")          # note 要到分鐘

    def test_deleting_a_note(self):
        cwd = str(self.project)
        store.put("note", cwd, "box", "2026-09-10-0930", "x")
        self.assertEqual(store.delete("note", cwd, "box", "2026-09-10-0930"), 1)
        self.assertIsNone(store.get("note", cwd, "box", "2026-09-10-0930"))

    def test_search_covers_the_body(self):
        store.put("note", str(self.project), "box", "2026-09-10-0930", "about the sidebar")
        self.assertEqual(len(store.search("sidebar")), 1)
        self.assertEqual(len(store.search("nothing like that")), 0)


class AdoptingWhatWasAlreadyThere(Sandbox):

    def write(self, sub, name, text):
        d = self.project / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text, encoding="utf-8")

    def test_a_journal_written_by_an_older_version_still_comes_in(self):
        """舊格式把你的字放在「## In your words」底下, 新格式整份都是你的字。"""
        self.write("journal", "2026-09-01.md",
                   "---\ndate: 2026-09-01\n---\n\n# 2026-09-01 · proj\n\n"
                   "## In your words\n\nthe old shape\n\n## What changed\n\n- 3 commits\n")
        self.write("journal", "2026-09-02.md",
                   "---\ndate: 2026-09-02\n---\n\n# 2026-09-02 · proj\n\nthe new shape\n")
        added = store.import_markdown(str(self.project), "box")
        self.assertEqual(added, 2)
        self.assertEqual(store.get("journal", str(self.project), "box", "2026-09-01")["body"],
                         "the old shape")
        self.assertEqual(store.get("journal", str(self.project), "box", "2026-09-02")["body"],
                         "the new shape")

    def test_importing_twice_changes_nothing(self):
        self.write("note", "2026-09-10-0930.md",
                   "---\nat: 2026-09-10-0930\n---\n\n# 09-10 · proj\n\na thought\n")
        self.assertEqual(store.import_markdown(str(self.project), "box"), 1)
        store.put("note", str(self.project), "box", "2026-09-10-0930", "edited since")
        self.assertEqual(store.import_markdown(str(self.project), "box"), 0)
        self.assertEqual(store.get("note", str(self.project), "box", "2026-09-10-0930")["body"],
                         "edited since")

    def test_files_this_tool_did_not_write_are_left_alone(self):
        self.write("note", "meeting-notes.md", "# Not ours\n")
        self.assertEqual(store.import_markdown(str(self.project), "box"), 0)


if __name__ == "__main__":
    unittest.main()
