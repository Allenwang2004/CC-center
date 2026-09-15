"""
The Markdown that lands in the project folder.

The rule these tests hold to: a journal entry contains what you typed, and
nothing else. Everything the tool can recompute stays out of your file.
"""

from __future__ import annotations

import unittest

from tests.support import Sandbox

from cccenter import entries


class JournalMarkdown(Sandbox):

    def test_only_your_words_are_in_the_file(self):
        text = entries.journal_text(str(self.project), "2026-09-10",
                                    "今天把 journal 改成只存我自己打的字。", "box")
        self.assertIn("今天把 journal 改成只存我自己打的字。", text)
        for computed in ("## What changed", "## In your words", "commits", "Question by question"):
            self.assertNotIn(computed, text)

    def test_the_front_matter_says_where_and_when(self):
        text = entries.journal_text(str(self.project), "2026-09-10", "x", "box")
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("date: 2026-09-10", text)
        self.assertIn("project: proj", text)
        self.assertIn("host: box", text)
        self.assertIn("tool: cc-center", text)

    def test_an_empty_entry_is_still_a_valid_file(self):
        text = entries.journal_text(str(self.project), "2026-09-10", "", "box")
        self.assertIn("# 2026-09-10 · proj", text)
        self.assertTrue(text.endswith("\n"))


class NoteMarkdown(Sandbox):

    def test_a_note_without_a_title_is_headed_by_its_time(self):
        text = entries.note_text(str(self.project), "2026-09-10-0340", "a thought", "box")
        self.assertIn("# 2026-09-10 03:40 · proj", text)
        self.assertIn("a thought", text)
        self.assertNotIn("title:", text)

    def test_a_title_becomes_the_heading(self):
        text = entries.note_text(str(self.project), "2026-09-10-0340", "body", "box",
                                 title="Sidebar rethink")
        self.assertIn("title: Sidebar rethink", text)
        self.assertIn("# Sidebar rethink", text)


class WritingItOut(Sandbox):

    def test_each_kind_lands_in_its_own_folder(self):
        j = entries.export_entry("journal", str(self.project), "2026-09-10", "day text", "box")
        n = entries.export_entry("note", str(self.project), "2026-09-10-0340", "note text", "box")
        self.assertEqual(j, str(self.project / "journal" / "2026-09-10.md"))
        self.assertEqual(n, str(self.project / "note" / "2026-09-10-0340.md"))
        self.assertIn("day text", (self.project / "journal" / "2026-09-10.md").read_text(encoding="utf-8"))
        self.assertIn("note text", (self.project / "note" / "2026-09-10-0340.md").read_text(encoding="utf-8"))

    def test_saving_again_replaces_the_file(self):
        entries.export_entry("journal", str(self.project), "2026-09-10", "first", "box")
        entries.export_entry("journal", str(self.project), "2026-09-10", "second", "box")
        text = (self.project / "journal" / "2026-09-10.md").read_text(encoding="utf-8")
        self.assertIn("second", text)
        self.assertNotIn("first", text)


if __name__ == "__main__":
    unittest.main()
