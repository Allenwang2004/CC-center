"""
Saving, end to end: the cloud first, then the cache, then the copy of a note in
the project folder.

The failures this guards against: a save that reports success while the cloud
never got it, a journal entry landing in a project folder it no longer belongs
in, or a note file on disk saying something else than what was saved.
"""

from __future__ import annotations

import unittest

from tests.support import Sandbox

from cccenter import store, sync
from cccenter.app.settings import DEFAULT_SETTINGS
from cccenter.app.writing import Writer
from cccenter.cloud import CloudError, NotSignedIn


class Saving(Sandbox):

    def setUp(self):
        super().setUp()
        self.said = []
        self.w = Writer("box", lambda msg, level="info", host=None: self.said.append(msg),
                        cloud=self.cloud())

    def test_a_note_reaches_the_cloud_the_cache_and_the_folder(self):
        out = self.w.save("note", "box", str(self.project), "", "第一則想法", DEFAULT_SETTINGS)
        ref = out["ref"]
        self.assertIsNone(out["warn"])
        self.assertEqual([r["body"] for r in self.fake.rows()], ["第一則想法"])
        self.assertEqual(store.get("note", str(self.project), "box", ref)["body"], "第一則想法")
        self.assertIn("第一則想法",
                      (self.project / "note" / f"{ref}.md").read_text(encoding="utf-8"))

    def test_a_mind_map_is_one_row_per_project_saved_back_to_the_same_day(self):
        scene = '{"type":"excalidraw","elements":[{"id":"a"}]}'
        out = self.w.save("mindmap", "box", str(self.project), "2026-09-17", scene,
                          DEFAULT_SETTINGS, title="ignored")
        self.assertEqual(out, {"ref": "2026-09-17", "path": None, "warn": None, "title": ""})
        again = '{"type":"excalidraw","elements":[{"id":"a"},{"id":"b"}]}'
        self.w.save("mindmap", "box", str(self.project), "2026-09-17", again, DEFAULT_SETTINGS)
        rows = [r for r in self.fake.rows() if r["kind"] == "mindmap"]
        self.assertEqual([(r["ref"], r["day"], r["body"]) for r in rows],
                         [("2026-09-17", "2026-09-17", again)])
        self.assertEqual(store.get("mindmap", str(self.project), "box", "2026-09-17")["body"], again)
        self.assertFalse((self.project / "note").exists())
        with self.assertRaises(ValueError):
            self.w.save("mindmap", "box", str(self.project), "", scene, DEFAULT_SETTINGS)
        with self.assertRaises(ValueError):
            self.w.delete("mindmap", "box", str(self.project), "2026-09-17")

    def test_a_journal_entry_goes_to_the_cloud_and_stays_out_of_the_folder(self):
        out = self.w.save("journal", "box", str(self.project), "2026-09-10",
                          "把 journal 改成只存我打的字", DEFAULT_SETTINGS)
        self.assertIsNone(out["path"])
        self.assertIsNone(out["warn"])
        self.assertEqual(self.fake.rows()[0]["body"], "把 journal 改成只存我打的字")
        self.assertEqual(store.get("journal", str(self.project), "box", "2026-09-10")["body"],
                         "把 journal 改成只存我打的字")
        self.assertFalse((self.project / "journal").exists())

    def test_nothing_is_saved_when_the_cloud_refuses(self):
        self.fake.__exit__(None, None, None)
        self._fake = None
        with self.assertRaises(CloudError):
            self.w.save("note", "box", str(self.project), "2026-09-10-0930", "lost",
                        DEFAULT_SETTINGS)
        self.assertIsNone(store.get("note", str(self.project), "box", "2026-09-10-0930"))
        self.assertFalse((self.project / "note").exists())

    def test_you_have_to_be_signed_in_to_save(self):
        self.w.cloud.forget()
        with self.assertRaises(NotSignedIn):
            self.w.save("note", "box", str(self.project), "", "x", DEFAULT_SETTINGS)

    def test_a_journal_entry_needs_a_date_and_a_project(self):
        with self.assertRaises(ValueError):
            self.w.save("journal", "box", str(self.project), "", "x", DEFAULT_SETTINGS)
        with self.assertRaises(ValueError):
            self.w.save("note", "box", "", "", "x", DEFAULT_SETTINGS)

    def test_a_journal_entry_is_named_by_its_date_not_by_a_title(self):
        out = self.w.save("journal", "box", str(self.project), "2026-09-10", "x",
                          DEFAULT_SETTINGS, title="ignore me")
        self.assertEqual(out["title"], "")

    def test_what_you_wrote_survives_losing_the_cache(self):
        """快取沒了, 從雲端再收一次應該一字不差; note 的檔也還在。"""
        self.w.save("journal", "box", str(self.project), "2026-09-10", "整天的說明",
                    DEFAULT_SETTINGS)
        self.w.save("note", "box", str(self.project), "2026-09-10-0930", "一則想法",
                    DEFAULT_SETTINGS)
        store.wipe()
        self.assertEqual(store.for_project(str(self.project)), [])

        sync.pull(self.w.cloud, 1.0)
        self.assertEqual(store.get("journal", str(self.project), "box", "2026-09-10")["body"],
                         "整天的說明")
        self.assertEqual(store.get("note", str(self.project), "box", "2026-09-10-0930")["body"],
                         "一則想法")
        self.assertTrue((self.project / "note" / "2026-09-10-0930.md").is_file())

    def test_an_old_journal_file_in_the_folder_is_adopted_and_sent_up(self):
        """以前投影出去的 journal/*.md: 收進快取, 下一次 sync 推上雲端。"""
        d = self.project / "journal"
        d.mkdir()
        (d / "2026-09-09.md").write_text(
            "---\ndate: 2026-09-09\n---\n\n# 2026-09-09 · proj\n\n舊的一天\n", encoding="utf-8")
        self.w.adopt([str(self.project)], "box")
        self.assertEqual(store.stats()["pending"], 1)
        sync.pull(self.w.cloud, 1.0)
        self.assertEqual(store.stats()["pending"], 0)
        self.assertEqual([r["body"] for r in self.fake.rows()], ["舊的一天"])

    def test_a_project_folder_that_is_gone_still_saves_your_words(self):
        missing = str(self.tmp / "not-there")
        out = self.w.save("note", "box", missing, "2026-09-10-0930", "kept anyway",
                          DEFAULT_SETTINGS)
        self.assertIsNone(out["path"])
        self.assertIn("no markdown was written", out["warn"])
        self.assertEqual(store.get("note", missing, "box", "2026-09-10-0930")["body"],
                         "kept anyway")

    def test_deleting_a_note_takes_the_file_with_it(self):
        out = self.w.save("note", "box", str(self.project), "", "throwaway", DEFAULT_SETTINGS)
        path = self.project / "note" / f"{out['ref']}.md"
        self.assertTrue(path.is_file())
        self.assertIsNone(self.w.delete("note", "box", str(self.project), out["ref"]))
        self.assertFalse(path.exists())
        self.assertIsNone(store.get("note", str(self.project), "box", out["ref"]))

    def test_only_notes_can_be_deleted(self):
        with self.assertRaises(ValueError):
            self.w.delete("journal", "box", str(self.project), "2026-09-10")


class Adopting(Sandbox):

    def test_a_project_is_only_adopted_once(self):
        w = Writer("box", cloud=self.cloud())
        d = self.project / "note"
        d.mkdir()
        (d / "2026-09-10-0930.md").write_text("---\nat: 2026-09-10-0930\n---\n\nhi\n",
                                              encoding="utf-8")
        w.adopt([str(self.project)], "box")
        self.assertEqual(store.get("note", str(self.project), "box", "2026-09-10-0930")["body"], "hi")
        store.delete("note", str(self.project), "box", "2026-09-10-0930")
        w.adopt([str(self.project)], "box")            # 第二次不再看那個資料夾
        self.assertIsNone(store.get("note", str(self.project), "box", "2026-09-10-0930"))


if __name__ == "__main__":
    unittest.main()
