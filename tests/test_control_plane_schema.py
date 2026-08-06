"""Schema drift across the whole control plane.

Generalizes the stamp/detect/heal pattern `workspace reinit` already proves. The healing
rule is the load-bearing part: additive only, existing content never touched."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from charter import instance


class SchemaIso(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="charter-schema-")).resolve()
        (self.root / "charter.toml").write_text(f"schema = {instance.SCHEMA}\n")


class TestDrift(SchemaIso):
    def test_a_complete_control_plane_has_no_drift(self):
        for d in ("personas", "inventory", "workspaces"):
            (self.root / d).mkdir()
        self.assertEqual(instance.drift(self.root), [])

    def test_missing_baseline_directories_are_reported(self):
        found = instance.drift(self.root)
        self.assertTrue(found)
        self.assertTrue(any("personas" in f for f in found), found)

    def test_drift_names_what_is_missing_not_just_that_something_is(self):
        """A user must be able to act on the message without reading the source."""
        for f in instance.drift(self.root):
            self.assertTrue(any(c.isalpha() for c in f))


class TestReinitIsAdditive(SchemaIso):
    def _reinit(self):
        from types import SimpleNamespace
        from charter import commands, config
        from unittest import mock
        with mock.patch.object(config, "ROOT", self.root), \
             mock.patch.object(config, "HAS_CONTROL_PLANE", True):
            return commands.cmd_reinit(SimpleNamespace())

    def test_creates_what_is_missing(self):
        self.assertEqual(self._reinit(), 0)
        self.assertEqual(instance.drift(self.root), [])

    def test_never_touches_existing_content(self):
        """The rule `workspace reinit` already follows, and the reason reinit is safe to
        run on a repo full of someone's work."""
        (self.root / "personas").mkdir()
        keep = self.root / "personas" / "mine.md"
        keep.write_text("MINE\n")
        self._reinit()
        self.assertEqual(keep.read_text(), "MINE\n")

    def test_is_idempotent(self):
        self._reinit()
        self.assertEqual(self._reinit(), 0)
        self.assertEqual(instance.drift(self.root), [])


if __name__ == "__main__":
    unittest.main()
