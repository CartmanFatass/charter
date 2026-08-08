"""Workspace structure versioning: a marker (.charter-structure) anchors the current layout,
so a workspace from an older umbrella (missing files or an old marker) is detected as stale
and healed idempotently by `charter workspace reinit`. This is the durable upgrade path for any
future structural addition — bump STRUCTURE_VERSION + create it in scaffold()."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from charter import workspace
from charter import commands_workspace as cw
from tests._isolation import PersonaIso


class StructureCase(PersonaIso):
    def _fresh(self, name):
        workspace.ensure(name)
        workspace.scaffold(name)

    # --- detection ---
    def test_fresh_workspace_is_up_to_date(self):
        self._fresh("w")
        s = workspace.structure_status("w")
        self.assertTrue(s["ok"])
        self.assertEqual(s["missing"], [])
        self.assertEqual(s["version"], workspace.STRUCTURE_VERSION)
        self.assertFalse(workspace.needs_reinit("w"))

    def test_missing_marker_flags_stale(self):
        self._fresh("w")
        workspace._structure_marker("w").unlink()
        self.assertEqual(workspace.structure_version("w"), 0)
        self.assertTrue(workspace.needs_reinit("w"))

    def test_missing_baseline_file_flags_stale(self):
        self._fresh("w")
        workspace.charter_file("w").unlink()
        s = workspace.structure_status("w")
        self.assertIn("workspace.md", s["missing"])
        self.assertFalse(s["ok"])

    def test_version_drift_flags_even_with_all_files(self):
        # The future-feature case: all *current* files exist, but the marker predates a
        # later STRUCTURE_VERSION → still flagged (nothing missing, just an old version).
        self._fresh("w")
        workspace._structure_marker("w").write_text("0\n")
        s = workspace.structure_status("w")
        self.assertEqual(s["missing"], [])
        self.assertFalse(s["ok"])
        self.assertTrue(workspace.needs_reinit("w"))

    def test_nonexistent_workspace_not_flagged(self):
        self.assertFalse(workspace.needs_reinit("ghost"))

    # --- reinit heals ---
    def test_reinit_heals_and_stamps(self):
        self._fresh("w")
        workspace._structure_marker("w").unlink()
        workspace.charter_file("w").unlink()
        workspace.memory_index("w").unlink()
        before = workspace.reinit("w")
        self.assertFalse(before["ok"])
        self.assertIn("workspace.md", before["missing"])
        self.assertIn("memory/MEMORY.md", before["missing"])
        self.assertFalse(workspace.needs_reinit("w"))           # healed
        self.assertEqual(workspace.structure_version("w"), workspace.STRUCTURE_VERSION)

    def test_reinit_is_additive_preserves_content(self):
        self._fresh("w")
        workspace.set_vision("w", "keep me")
        workspace.remember("w", "keep this memory")
        workspace._structure_marker("w").unlink()               # only the marker is stale
        workspace.reinit("w")
        self.assertEqual(workspace.read_vision("w"), "keep me")  # charter untouched
        mem = "\n".join(p.read_text() for p in workspace.memories("w"))
        self.assertIn("keep this memory", mem)                   # memories untouched

    # --- command ---
    def test_reinit_command_single(self):
        self._fresh("w")
        workspace._structure_marker("w").unlink()
        rc = cw.cmd_workspace_reinit(SimpleNamespace(name="w", all=False))
        self.assertEqual(rc, 0)
        self.assertFalse(workspace.needs_reinit("w"))

    def test_reinit_command_all(self):
        for n in ("a", "b"):
            self._fresh(n)
            workspace._structure_marker(n).unlink()
        rc = cw.cmd_workspace_reinit(SimpleNamespace(name=None, all=True))
        self.assertEqual(rc, 0)
        self.assertFalse(workspace.needs_reinit("a"))
        self.assertFalse(workspace.needs_reinit("b"))

    def test_reinit_command_missing_workspace(self):
        self.assertEqual(cw.cmd_workspace_reinit(SimpleNamespace(name="ghost", all=False)), 1)


if __name__ == "__main__":
    unittest.main()
