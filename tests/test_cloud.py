"""
Supabase is where what you wrote now lives, so these tests are about the two
things that can lose it: a client that talks to the service wrongly, and a
sync that lets the cache and the cloud drift apart.

Everything runs against `tests/fake_supabase.py` on a local port --- the real
`urllib` client, real headers, real paging --- never the network.
"""

from __future__ import annotations

import time
import unittest

from tests.fake_supabase import ANON, CODE, FakeSupabase
from tests.support import Sandbox

from cccenter import store, sync
from cccenter.cloud import Cloud, CloudError, NotSignedIn, Unreachable


class SigningIn(Sandbox):

    def setUp(self):
        super().setUp()
        self.fake = FakeSupabase().__enter__()
        self._fake = self.fake
        self.c = Cloud(self.fake.url, ANON, self.state / "auth.json", timeout=5)

    def test_a_code_goes_to_the_email_and_verifying_it_signs_you_in(self):
        self.assertFalse(self.c.signed_in)
        self.c.request_code("you@example.com")
        self.assertEqual(self.fake.state.emails, ["you@example.com"])
        me = self.c.verify_code("you@example.com", " 123 456 ")   # 貼進來的碼可能有空白
        self.assertEqual(me["email"], "you@example.com")
        self.assertTrue(self.c.signed_in)
        self.assertTrue((self.state / "auth.json").exists())
        self.assertEqual(oct((self.state / "auth.json").stat().st_mode & 0o777), "0o600")

    def test_a_wrong_code_is_an_error_you_can_read(self):
        self.c.request_code("you@example.com")
        with self.assertRaises(CloudError) as cm:
            self.c.verify_code("you@example.com", "000000")
        self.assertIn("invalid", str(cm.exception).lower())
        self.assertFalse(self.c.signed_in)

    def test_the_session_survives_a_restart(self):
        self.c.request_code("you@example.com")
        self.c.verify_code("you@example.com", CODE)
        again = Cloud(self.fake.url, ANON, self.state / "auth.json", timeout=5)
        self.assertTrue(again.signed_in)
        self.assertEqual(again.account()["email"], "you@example.com")
        self.assertEqual(again.list_entries(), [])

    def test_an_expired_token_is_refreshed_without_asking_you(self):
        self.fake.state.token_ttl = 1
        self.c.request_code("you@example.com")
        self.c.verify_code("you@example.com", CODE)
        first = self.c.token()
        time.sleep(1.2)
        self.assertEqual(self.c.list_entries(), [])
        self.assertNotEqual(self.c.token(), first)

    def test_a_revoked_session_becomes_signed_out(self):
        self.fake.state.token_ttl = 1
        self.c.request_code("you@example.com")
        self.c.verify_code("you@example.com", CODE)
        time.sleep(1.2)
        self.fake.state.reject_refresh = True
        with self.assertRaises(NotSignedIn):
            self.c.list_entries()
        self.assertFalse(self.c.signed_in)
        self.assertFalse((self.state / "auth.json").exists())

    def test_signing_out_forgets_the_session_even_if_the_cloud_is_gone(self):
        self.c.request_code("you@example.com")
        self.c.verify_code("you@example.com", CODE)
        self.fake.__exit__(None, None, None)
        self._fake = None
        self.c.sign_out()
        self.assertFalse(self.c.signed_in)

    def test_no_network_is_unreachable_not_signed_out(self):
        self.c.request_code("you@example.com")
        self.c.verify_code("you@example.com", CODE)
        self.fake.__exit__(None, None, None)
        self._fake = None
        with self.assertRaises(Unreachable):
            self.c.list_entries()
        self.assertTrue(self.c.signed_in)          # 離線不等於登出

    def test_unconfigured_says_what_to_set(self):
        c = Cloud("", "", self.state / "auth.json")
        self.assertFalse(c.configured)
        with self.assertRaises(CloudError) as cm:
            c.request_code("you@example.com")
        self.assertIn("SUPABASE_URL", str(cm.exception))


class Rows(Sandbox):

    def setUp(self):
        super().setUp()
        self.c = self.cloud()

    def test_upsert_then_list_then_delete(self):
        row = self.c.upsert_entry({"kind": "note", "cwd": "/p", "host": "box",
                                   "ref": "2026-09-10-0930", "day": "2026-09-10",
                                   "body": "hi"})
        self.assertEqual(row["body"], "hi")
        self.assertTrue(row["id"])
        row2 = self.c.upsert_entry({"kind": "note", "cwd": "/p", "host": "box",
                                    "ref": "2026-09-10-0930", "day": "2026-09-10",
                                    "body": "changed"})
        self.assertEqual(row2["id"], row["id"])
        self.assertEqual([r["body"] for r in self.c.list_entries()], ["changed"])
        self.assertEqual(self.c.delete_entry("note", "/p", "box", "2026-09-10-0930"), 1)
        self.assertEqual(self.c.list_entries(), [])

    def test_keep_existing_never_overwrites(self):
        base = {"kind": "note", "cwd": "/p", "host": "box",
                "ref": "2026-09-10-0930", "day": "2026-09-10"}
        self.c.upsert_entry({**base, "body": "cloud"})
        self.assertIsNone(self.c.upsert_entry({**base, "body": "local"}, keep_existing=True))
        self.assertEqual(self.c.list_entries()[0]["body"], "cloud")

    def test_a_path_with_odd_characters_still_deletes_the_right_row(self):
        cwd = '/Users/me/a,b (c) "quoted"'
        self.c.upsert_entry({"kind": "note", "cwd": cwd, "host": "box",
                             "ref": "2026-09-10-0930", "day": "2026-09-10", "body": "x"})
        self.c.upsert_entry({"kind": "note", "cwd": "/other", "host": "box",
                             "ref": "2026-09-10-0930", "day": "2026-09-10", "body": "y"})
        self.assertEqual(self.c.delete_entry("note", cwd, "box", "2026-09-10-0930"), 1)
        self.assertEqual([r["cwd"] for r in self.c.list_entries()], ["/other"])

    def test_listing_pages_through_everything(self):
        from cccenter import cloud as cloudmod
        old = cloudmod.PAGE
        cloudmod.PAGE = 3
        try:
            for i in range(7):
                self.c.upsert_entry({"kind": "journal", "cwd": "/p", "host": "box",
                                     "ref": f"2026-09-{10 + i:02d}", "day": f"2026-09-{10 + i:02d}",
                                     "body": str(i)})
            self.assertEqual(len(self.c.list_entries()), 7)
        finally:
            cloudmod.PAGE = old


class Syncing(Sandbox):

    def setUp(self):
        super().setUp()
        self.c = self.cloud()
        self.cwd = str(self.project)

    def test_a_save_reaches_the_cloud_before_the_cache(self):
        row = sync.save_entry(self.c, "note", self.cwd, "box", "2026-09-10-0930", "一則想法",
                              title="Named")
        self.assertEqual(row["body"], "一則想法")
        self.assertEqual(row["title"], "Named")
        self.assertIsNotNone(row["synced_at"])
        self.assertEqual(self.fake.rows()[0]["body"], "一則想法")
        self.assertEqual(row["cloud_id"], self.fake.rows()[0]["id"])

    def test_a_failed_save_leaves_the_cache_untouched(self):
        self.fake.__exit__(None, None, None)
        self._fake = None
        with self.assertRaises(Unreachable):
            sync.save_entry(self.c, "note", self.cwd, "box", "2026-09-10-0930", "lost?")
        self.assertIsNone(store.get("note", self.cwd, "box", "2026-09-10-0930"))

    def test_rewriting_without_a_title_keeps_the_title(self):
        sync.save_entry(self.c, "note", self.cwd, "box", "2026-09-10-0930", "first", title="T")
        sync.save_entry(self.c, "note", self.cwd, "box", "2026-09-10-0930", "second")
        self.assertEqual(store.get("note", self.cwd, "box", "2026-09-10-0930")["title"], "T")
        self.assertEqual(self.fake.rows()[0]["title"], "T")
        sync.save_entry(self.c, "note", self.cwd, "box", "2026-09-10-0930", "third", title="")
        self.assertEqual(self.fake.rows()[0]["title"], "")

    def test_pull_brings_down_what_another_machine_wrote(self):
        self.c.upsert_entry({"kind": "journal", "cwd": "/elsewhere", "host": "lab",
                             "ref": "2026-09-10", "day": "2026-09-10", "body": "from lab"})
        out = sync.pull(self.c, 1000.0)
        self.assertEqual(out["pulled"], 1)
        got = store.get("journal", "/elsewhere", "lab", "2026-09-10")
        self.assertEqual(got["body"], "from lab")
        self.assertIsNotNone(got["synced_at"])
        self.assertEqual(store.stats()["synced_at"], 1000.0)

    def test_pull_sends_up_what_only_this_machine_has_without_overwriting(self):
        # 收進來的舊 markdown: 快取有、雲端沒有
        store.put("note", self.cwd, "box", "2026-09-10-0930", "local only")
        # 兩邊都有的: 雲端贏
        store.put("note", self.cwd, "box", "2026-09-10-0940", "stale local")
        self.c.upsert_entry({"kind": "note", "cwd": self.cwd, "host": "box",
                             "ref": "2026-09-10-0940", "day": "2026-09-10", "body": "cloud"})
        self.assertEqual(store.stats()["pending"], 2)
        out = sync.pull(self.c, 1.0)
        self.assertEqual(out["pushed"], 1)
        self.assertEqual(store.stats()["pending"], 0)
        bodies = {r["ref"]: r["body"] for r in self.fake.rows()}
        self.assertEqual(bodies, {"2026-09-10-0930": "local only", "2026-09-10-0940": "cloud"})
        self.assertEqual(store.get("note", self.cwd, "box", "2026-09-10-0940")["body"], "cloud")

    def test_pull_drops_what_the_cloud_no_longer_has(self):
        sync.save_entry(self.c, "note", self.cwd, "box", "2026-09-10-0930", "gone soon")
        self.c.delete_entry("note", self.cwd, "box", "2026-09-10-0930")     # 別台機器刪的
        out = sync.pull(self.c, 1.0)
        self.assertEqual(out["removed"], 1)
        self.assertIsNone(store.get("note", self.cwd, "box", "2026-09-10-0930"))

    def test_pull_leaves_the_cache_alone_when_the_cloud_is_unreachable(self):
        sync.save_entry(self.c, "note", self.cwd, "box", "2026-09-10-0930", "kept")
        self.fake.__exit__(None, None, None)
        self._fake = None
        with self.assertRaises(Unreachable):
            sync.pull(self.c, 1.0)
        self.assertEqual(store.get("note", self.cwd, "box", "2026-09-10-0930")["body"], "kept")

    def test_a_different_account_starts_from_an_empty_cache(self):
        sync.save_entry(self.c, "note", self.cwd, "box", "2026-09-10-0930", "mine")
        sync.pull(self.c, 1.0)
        self.c.sign_out()
        self.fake.state.user = {"id": "22222222-2222-2222-2222-222222222222",
                                "email": "other@example.com"}
        self.c.request_code("other@example.com")
        self.c.verify_code("other@example.com", CODE)
        out = sync.pull(self.c, 2.0)
        self.assertTrue(out["owner_changed"])
        self.assertIsNone(store.get("note", self.cwd, "box", "2026-09-10-0930"))
        self.assertEqual(store.stats()["notes"], 0)

    def test_delete_goes_to_the_cloud_first(self):
        sync.save_entry(self.c, "note", self.cwd, "box", "2026-09-10-0930", "x")
        self.fake.__exit__(None, None, None)
        self._fake = None
        with self.assertRaises(Unreachable):
            sync.remove_entry(self.c, "note", self.cwd, "box", "2026-09-10-0930")
        self.assertIsNotNone(store.get("note", self.cwd, "box", "2026-09-10-0930"))


if __name__ == "__main__":
    unittest.main()
