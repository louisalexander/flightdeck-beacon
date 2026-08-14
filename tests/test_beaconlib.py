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


def s(session_id, state):
    """Shorthand for a session entry in the fleet snapshot."""
    return {"session_id": session_id, "state": state}


def prev(base="idle", done=()):
    return {"base": base, "done": list(done)}


class TestFold(unittest.TestCase):
    """The truth table for the whole lamp.

    Each case is (label, sessions, prev, expected_base, expected_landed,
    expected_changed, expected_done).
    """

    CASES = [
        ("nothing running is idle, and unchanged when already idle",
         [], prev("idle"), "idle", False, False, []),

        ("one working session turns the lamp blue",
         [s("A", "working")], prev("idle"), "working", False, True, []),

        ("blocked outranks working",
         [s("A", "working"), s("B", "blocked")], prev("idle"),
         "blocked", False, True, []),

        ("FAILED OUTRANKS EVERYTHING: it is the only state meaning something broke",
         [s("A", "working"), s("B", "blocked"), s("C", "failed")], prev("idle"),
         "failed", False, True, []),

        ("a failed session alone does NOT fall through to idle and go dark",
         [s("A", "failed")], prev("working"), "failed", False, True, []),

        ("a landing is suppressed under failed, as it is under blocked",
         [s("A", "done"), s("B", "failed")], prev("idle"),
         "failed", False, True, ["A"]),

        ("clearing a failure back to idle returns the lamp to off",
         [s("A", "idle")], prev("failed"), "idle", False, True, []),

        ("a newly done session lands, base idle when nothing else runs",
         [s("A", "done")], prev("working"), "idle", True, True, ["A"]),

        ("LANDING IS AN EDGE: the same done session does not re-land",
         [s("A", "done")], prev("idle", ["A"]), "idle", False, False, ["A"]),

        ("a landing fires even while another session still works",
         [s("A", "done"), s("B", "working")], prev("working"),
         "working", True, True, ["A"]),

        ("AMBER OUTRANKS GREEN: a landing is suppressed while any session is blocked",
         [s("A", "done"), s("B", "blocked")], prev("idle"),
         "blocked", False, True, ["A"]),

        ("two sessions landing at once is a single landing",
         [s("A", "done"), s("B", "done")], prev("working"),
         "idle", True, True, ["A", "B"]),

        ("a second session landing while the first is still done lands again",
         [s("A", "done"), s("B", "done")], prev("idle", ["A"]),
         "idle", True, True, ["A", "B"]),

        ("a session leaving done drops out of the done set",
         [s("A", "working")], prev("idle", ["A"]), "working", False, True, []),

        ("a reaped working session drops to idle, which is a change",
         [], prev("working"), "idle", False, True, []),

        ("a reaped done session is NOT a change; HA already ran the fade",
         [], prev("idle", ["A"]), "idle", False, False, []),

        ("an empty prev is treated as idle with nothing done",
         [s("A", "working")], {}, "working", False, True, []),

        ("junk entries and unknown states are ignored, not fatal",
         [s("A", "working"), "junk", {"no_id": True}, s("C", "weird")],
         prev("idle"), "working", False, True, []),

        ("a done session with no session_id cannot land",
         [{"state": "done"}], prev("idle"), "idle", False, False, []),
    ]

    def test_truth_table(self):
        for case in self.CASES:
            label, sessions, previous, want_base, want_landed, want_changed, want_done = case
            with self.subTest(case=label):
                base, landed, nxt, changed = beaconlib.fold(sessions, previous)
                self.assertEqual(base, want_base, "base")
                self.assertEqual(landed, want_landed, "landed")
                self.assertEqual(changed, want_changed, "changed")
                self.assertEqual(nxt["done"], want_done, "done")

    def test_next_prev_is_json_serialisable_and_round_trips(self):
        _, _, nxt, _ = beaconlib.fold([s("A", "done")], prev("working"))
        self.assertEqual(json.loads(json.dumps(nxt)), nxt)

    def test_fold_does_not_mutate_its_arguments(self):
        sessions = [s("A", "done")]
        previous = prev("working")
        before = json.dumps([sessions, previous], sort_keys=True)
        beaconlib.fold(sessions, previous)
        self.assertEqual(json.dumps([sessions, previous], sort_keys=True), before)

    def test_a_landing_then_a_reap_settles_without_re_firing(self):
        # The exact sequence the 15s reap tick produces after a turn ends.
        state = {}
        base, landed, state, changed = beaconlib.fold([s("A", "done")], state)
        self.assertEqual((base, landed, changed), ("idle", True, True))
        base, landed, state, changed = beaconlib.fold([s("A", "done")], state)
        self.assertEqual((base, landed, changed), ("idle", False, False))
        base, landed, state, changed = beaconlib.fold([], state)
        self.assertEqual((base, landed, changed), ("idle", False, False))


if __name__ == "__main__":
    unittest.main()
