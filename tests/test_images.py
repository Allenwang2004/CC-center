"""
A picture pasted into the journal has one way up (the private bucket, in the
account's own folder) and one way back (the local server, from its cache if it
can). These are the things that would silently break that: the wrong folder,
a type the bucket refuses, a cache that is not used, a name that could reach
outside the cache directory.
"""

from __future__ import annotations

import unittest

from tests.fake_supabase import USER
from tests.support import Sandbox

from cccenter.app.images import ImageError, Images
from cccenter.cloud import CloudError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


class Uploading(Sandbox):

    def setUp(self):
        super().setUp()
        self.images = Images(self.state / "images", self.cloud())

    def test_a_png_lands_in_the_accounts_own_folder_and_gets_a_name(self):
        name = self.images.save(PNG)
        self.assertRegex(name, r"^\d{8}-\d{6}-[0-9a-f]{8}\.png$")
        self.assertEqual(list(self.fake.state.objects), [f"cc-images/{USER['id']}/{name}"])
        ctype, raw = self.fake.state.objects[f"cc-images/{USER['id']}/{name}"]
        self.assertEqual((ctype, raw), ("image/png", PNG))
        # and a copy stays here, so the next open needs no network
        self.assertEqual((self.state / "images" / name).read_bytes(), PNG)

    def test_the_type_comes_from_the_bytes_not_from_a_claim(self):
        name = self.images.save(JPEG)
        self.assertTrue(name.endswith(".jpg"))
        self.assertEqual(self.fake.state.objects[f"cc-images/{USER['id']}/{name}"][0], "image/jpeg")

    def test_what_the_bucket_refuses_is_refused_here_first(self):
        with self.assertRaises(ImageError):
            self.images.save(b"%PDF-1.4 not an image")
        with self.assertRaises(ImageError):
            self.images.save(b"")
        with self.assertRaises(ImageError) as cm:
            self.images.save(PNG + b"\x00" * (10 * 1024 * 1024))
        self.assertIn("10 MB", str(cm.exception))
        self.assertEqual(self.fake.state.objects, {})


class FetchingBack(Sandbox):

    def setUp(self):
        super().setUp()
        self.images = Images(self.state / "images", self.cloud())

    def test_a_picture_comes_back_from_the_cache_without_asking_the_cloud(self):
        name = self.images.save(PNG)
        before = len(self.fake.state.calls)
        self.assertEqual(self.images.get(name), ("image/png", PNG))
        self.assertEqual(len(self.fake.state.calls), before)

    def test_another_machine_fetches_it_from_the_bucket_and_keeps_a_copy(self):
        name = self.images.save(PNG)
        elsewhere = Images(self.state / "other-machine", self.cloud())
        self.assertEqual(elsewhere.get(name), ("image/png", PNG))
        self.assertTrue((self.state / "other-machine" / name).exists())
        self.assertEqual(elsewhere.get(name), ("image/png", PNG))   # second time: cached

    def test_a_name_that_is_not_ours_is_not_looked_up(self):
        for bad in ("../auth.json", "x.png", "20260917-000000-deadbeef.svg", ""):
            with self.assertRaises(ImageError):
                self.images.get(bad)

    def test_a_picture_the_cloud_does_not_have_is_a_readable_error(self):
        with self.assertRaises(CloudError) as cm:
            self.images.get("20260917-000000-deadbeef.png")
        self.assertIn("not found", str(cm.exception).lower())


if __name__ == "__main__":
    unittest.main()
