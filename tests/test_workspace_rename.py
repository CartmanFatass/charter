"""Workspace rename: move workspaces/<old>/ → workspaces/<new>/ (clones + memory +
manifest come along), fix the manifest ``name``, move the liveness gitignore block, and
repoint the active session/terminal pointer + lock. The LIVE commit/push path (glab) is
not exercised here — only the pure fs/pointer/liveness logic and the command's guards."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from charter import config, workspace
from charter import commands_workspace as cw
from tests._isolation import PersonaIso


class RenameCase(PersonaIso):
    def _mk(self, name, *, live=False, manifest=True, clone="repoA"):
        wd = workspace.workspace_dir(name)
        (wd / "memory").mkdir(parents=True, exist_ok=True)
        if clone:
            (wd / clone).mkdir(parents=True, exist_ok=True)
            (wd / clone / "f.txt").write_text("x")
        if manifest:
            workspace.write_manifest(name, {"name": name,
                                            "repos": [{"name": clone, "branch": "main"}]})
        if live:
            (config.ROOT / ".gitignore").write_text("/workspaces/*/*\n")
            workspace.set_live(name, True)
        return wd

    def _args(self, old, new, message=None):
        return SimpleNamespace(old=old, new=new, message=message)

    # --- workspace.rename: pure fs + manifest + pointers + liveness ---
    def test_moves_dir_clones_and_manifest_name(self):
        self._mk("old")
        workspace.rename("old", "new")
        self.assertFalse(workspace.workspace_dir("old").exists())
        self.assertTrue((workspace.workspace_dir("new") / "repoA" / "f.txt").exists())
        self.assertEqual(workspace.read_manifest("new")["name"], "new")

    def test_repoints_active_pointer_and_lock_only_for_matching_value(self):
        self._mk("old")
        config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        (config.SESSIONS_DIR / "sid.workspace").write_text("old\n")
        (config.SESSIONS_DIR / "sid.lock").write_text("old\n")
        (config.SESSIONS_DIR / "other.workspace").write_text("keepme\n")
        workspace.rename("old", "new")
        self.assertEqual((config.SESSIONS_DIR / "sid.workspace").read_text().strip(), "new")
        self.assertEqual((config.SESSIONS_DIR / "sid.lock").read_text().strip(), "new")
        self.assertEqual((config.SESSIONS_DIR / "other.workspace").read_text().strip(), "keepme")

    def test_moves_liveness_block(self):
        self._mk("old", live=True)
        self.assertTrue(workspace.is_live("old"))
        workspace.rename("old", "new")
        self.assertFalse(workspace.is_live("old"))
        self.assertTrue(workspace.is_live("new"))
        gi = (config.ROOT / ".gitignore").read_text()
        self.assertIn("!/workspaces/new/workspace.json", gi)
        self.assertNotIn("!/workspaces/old/", gi)

    def test_local_workspace_liveness_stays_off(self):
        self._mk("old")  # not live
        workspace.rename("old", "new")
        self.assertFalse(workspace.is_live("new"))

    # --- cmd_workspace_rename: LOCAL success (no git push) + guards ---
    def test_command_local_success(self):
        self._mk("old")
        rc = cw.cmd_workspace_rename(self._args("old", "new"))
        self.assertEqual(rc, 0)
        self.assertTrue(workspace.workspace_dir("new").exists())
        self.assertFalse(workspace.workspace_dir("old").exists())

    def test_refuse_missing_old(self):
        self.assertEqual(cw.cmd_workspace_rename(self._args("ghost", "new")), 1)

    def test_refuse_existing_new(self):
        self._mk("old")
        self._mk("taken")
        self.assertEqual(cw.cmd_workspace_rename(self._args("old", "taken")), 1)
        self.assertTrue(workspace.workspace_dir("old").exists())  # left untouched

    def test_refuse_invalid_new_name(self):
        self._mk("old")
        self.assertEqual(cw.cmd_workspace_rename(self._args("old", "Bad Name")), 1)
        self.assertTrue(workspace.workspace_dir("old").exists())

    def test_refuse_same_name(self):
        self._mk("old")
        self.assertEqual(cw.cmd_workspace_rename(self._args("old", "old")), 1)


if __name__ == "__main__":
    unittest.main()
