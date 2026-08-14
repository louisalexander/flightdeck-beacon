"""Unit coverage for bin/beaconlib.py. Standard library unittest only.

Every test points BEACON_HOME at a fresh temp directory, so nothing here can
touch the real ~/.beacon.
"""

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import beaconlib  # noqa: E402


class BeaconlibTestCase(unittest.TestCase):
    """Base class: isolates BEACON_HOME into a temp dir per test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "beacon"
        self._prev = os.environ.get("BEACON_HOME")
        os.environ["BEACON_HOME"] = str(self.home)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._prev is None:
            os.environ.pop("BEACON_HOME", None)
        else:
            os.environ["BEACON_HOME"] = self._prev
        self._tmp.cleanup()


class TestPaths(BeaconlibTestCase):

    def test_beacon_home_honours_the_env_override(self):
        self.assertEqual(beaconlib.beacon_home(), self.home)

    def test_all_state_paths_live_under_beacon_home(self):
        for path in (beaconlib.state_path(), beaconlib.log_path(),
                     beaconlib.config_path(), beaconlib.curl_headers_path()):
            with self.subTest(path=path):
                self.assertEqual(path.parent, self.home)


class TestJsonIO(BeaconlibTestCase):

    def test_write_then_read_round_trips(self):
        target = self.home / "nested" / "out.json"
        obj = {"a": 1, "b": [1, 2, 3], "c": {"d": "e"}}
        beaconlib.write_json_atomic(target, obj)
        self.assertEqual(beaconlib.read_json(target), obj)

    def test_write_creates_missing_parent_directories(self):
        target = self.home / "deep" / "deeper" / "out.json"
        beaconlib.write_json_atomic(target, {"x": 1})
        self.assertTrue(target.is_file())

    def test_write_leaves_no_temp_files_behind(self):
        target = self.home / "out.json"
        beaconlib.write_json_atomic(target, {"x": 1})
        self.assertEqual([p.name for p in self.home.iterdir()], ["out.json"])

    def test_write_stages_its_temp_file_beside_the_target_not_in_tmp(self):
        # os.replace() is only atomic within a filesystem, so the temp file
        # must be a sibling of the destination, never in /tmp.
        target = self.home / "out.json"
        seen = []
        real_mkstemp = tempfile.mkstemp

        def spy(*args, **kwargs):
            seen.append(kwargs.get("dir"))
            return real_mkstemp(*args, **kwargs)

        tempfile.mkstemp = spy
        try:
            beaconlib.write_json_atomic(target, {"x": 1})
        finally:
            tempfile.mkstemp = real_mkstemp
        self.assertEqual(seen, [str(self.home)])

    def test_read_returns_the_default_for_missing_and_corrupt_files(self):
        corrupt = self.home / "bad.json"
        self.home.mkdir(parents=True, exist_ok=True)
        corrupt.write_text("not json{", encoding="utf-8")
        cases = [
            ("missing", self.home / "nope.json", {"d": 1}),
            ("corrupt", corrupt, {"d": 2}),
        ]
        for label, path, default in cases:
            with self.subTest(case=label):
                self.assertEqual(beaconlib.read_json(path, default), default)


class TestLogging(BeaconlibTestCase):

    def test_log_appends_one_line_per_call(self):
        beaconlib.log("hello")
        beaconlib.log("world")
        body = beaconlib.log_path().read_text(encoding="utf-8")
        self.assertEqual(len(body.strip().splitlines()), 2)
        self.assertIn("hello", body)

    def test_log_never_raises_even_when_home_is_unwritable(self):
        os.environ["BEACON_HOME"] = "/dev/null/nope"
        beaconlib.log("this must not raise")  # no assertion: not raising is the test


class TestConfig(BeaconlibTestCase):

    def test_load_config_is_empty_when_no_file_exists(self):
        self.assertEqual(beaconlib.load_config(), {})

    def test_load_config_is_empty_when_the_file_is_not_an_object(self):
        self.home.mkdir(parents=True, exist_ok=True)
        beaconlib.config_path().write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        self.assertEqual(beaconlib.load_config(), {})

    def test_load_config_reads_a_real_config(self):
        self.home.mkdir(parents=True, exist_ok=True)
        beaconlib.config_path().write_text(
            json.dumps({"ha_url": "http://ha.local:8123", "token": "t"}),
            encoding="utf-8")
        self.assertEqual(beaconlib.load_config()["ha_url"], "http://ha.local:8123")


if __name__ == "__main__":
    unittest.main()
