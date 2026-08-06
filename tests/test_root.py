"""Locating the control plane.

This replaces `ROOT = Path(__file__).parent.parent` — the single line that made the
engine unshippable, because it found its DATA by its own CODE location."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from charter import root


class RootIso(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="charter-root-")).resolve()
        self.plane = self.tmp / "my-control-plane"
        (self.plane / "deep" / "nested").mkdir(parents=True)
        (self.plane / root.MARKER).write_text("schema = 1\n")


class TestFindRoot(RootIso):
    def test_finds_marker_in_the_start_directory(self):
        self.assertEqual(root.find_root(self.plane), self.plane)

    def test_walks_up_from_a_nested_directory(self):
        self.assertEqual(root.find_root(self.plane / "deep" / "nested"), self.plane)

    def test_stops_at_the_nearest_marker(self):
        inner = self.plane / "deep" / "inner-plane"
        inner.mkdir()
        (inner / root.MARKER).write_text("schema = 1\n")
        self.assertEqual(root.find_root(inner), inner)

    def test_raises_with_an_actionable_message_when_absent(self):
        with self.assertRaises(root.ControlPlaneNotFound) as cm:
            root.find_root(self.tmp)
        self.assertIn(root.MARKER, str(cm.exception))
        self.assertIn("charter init", str(cm.exception))

    def test_a_directory_named_charter_toml_is_not_a_marker(self):
        """The marker must be a FILE — a stray directory must not end the walk."""
        d = self.tmp / "decoy"
        (d / root.MARKER).mkdir(parents=True)
        with self.assertRaises(root.ControlPlaneNotFound):
            root.find_root(d)

    def test_env_var_overrides_the_walk(self):
        with mock.patch.dict(os.environ, {"CHARTER_ROOT": str(self.plane)}):
            self.assertEqual(root.find_root(self.tmp), self.plane)

    def test_env_var_pointing_somewhere_invalid_raises_not_silently_falls_back(self):
        """A wrong CHARTER_ROOT must be loud. Falling back to a walk would operate on a
        DIFFERENT control plane than the one the user named — the worst outcome."""
        with mock.patch.dict(os.environ, {"CHARTER_ROOT": str(self.tmp / "nope")}):
            with self.assertRaises(root.ControlPlaneNotFound):
                root.find_root(self.plane / "deep")

    def test_find_root_or_cwd_never_raises(self):
        self.assertEqual(root.find_root_or_cwd(self.plane), self.plane)
        self.assertEqual(root.find_root_or_cwd(self.tmp), self.tmp)


if __name__ == "__main__":
    unittest.main()
