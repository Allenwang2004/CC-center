"""
`.env` is where the personal part of the setup lives, so these tests are about
two promises: the file is read the way a person would expect to write it, and it
never overrides what the environment already says.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from tests.support import Sandbox

from cccenter.env import load_env, parse_env


class Parsing(unittest.TestCase):

    def test_the_shapes_people_actually_write(self):
        text = (
            "# a comment\n"
            "\n"
            'CC_HOSTS="alpha beta"\n'
            "export CC_TZ='Europe/Berlin'\n"
            "  CC_BROWSER = Firefox  \n"
            "CC_SSH_TIMEOUT=12\n"
            "EMPTY=\n"
            "this line is not a setting\n"
            "9BAD=starts with a digit\n"
        )
        self.assertEqual(parse_env(text), {
            "CC_HOSTS": "alpha beta",
            "CC_TZ": "Europe/Berlin",
            "CC_BROWSER": "Firefox",
            "CC_SSH_TIMEOUT": "12",
            "EMPTY": "",
        })

    def test_quotes_are_only_stripped_when_they_match(self):
        self.assertEqual(parse_env("A=\"unterminated\n")["A"], '"unterminated')
        self.assertEqual(parse_env("B='x\"\n")["B"], "'x\"")
        self.assertEqual(parse_env("C=\"\"\n")["C"], "")


class Loading(unittest.TestCase):

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.env_file = Path(self._tmp.name) / ".env"
        self._saved = {k: os.environ.get(k) for k in ("CC_T_ONE", "CC_T_TWO")}
        os.environ.pop("CC_T_ONE", None)
        os.environ["CC_T_TWO"] = "from-environment"

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def test_fills_in_only_what_is_missing(self):
        self.env_file.write_text("CC_T_ONE=from-file\nCC_T_TWO=from-file\n", encoding="utf-8")
        added = load_env(self.env_file)
        self.assertEqual(added, {"CC_T_ONE": "from-file"})
        self.assertEqual(os.environ["CC_T_ONE"], "from-file")
        self.assertEqual(os.environ["CC_T_TWO"], "from-environment")   # 環境變數優先

    def test_no_file_is_not_an_error(self):
        self.assertEqual(load_env(self.env_file), {})
        self.assertNotIn("CC_T_ONE", os.environ)


class HostList(Sandbox):
    """Nothing personal is baked in: with nothing configured, it is just this machine."""

    def test_defaults_to_no_remote_hosts(self):
        from cccenter.app import settings
        with mock.patch.object(settings, "HOSTS_FILE", self.tmp / "no-such-hosts"), \
             mock.patch.dict(os.environ, {"CC_HOSTS": ""}):
            self.assertEqual(settings.read_hosts(), [])

    def test_env_then_hosts_file(self):
        from cccenter.app import settings
        hosts_file = self.tmp / "hosts"
        with mock.patch.object(settings, "HOSTS_FILE", hosts_file), \
             mock.patch.dict(os.environ, {"CC_HOSTS": "a b"}):
            self.assertEqual(settings.read_hosts(), ["a", "b"])
            hosts_file.write_text("# mine\nc\n\nd\n", encoding="utf-8")
            self.assertEqual(settings.read_hosts(), ["c", "d"])         # 檔案優先於環境變數


if __name__ == "__main__":
    unittest.main()
