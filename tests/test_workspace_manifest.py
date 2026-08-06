"""Committed workspace manifest + snapshot with the enforce-push guard.

Uses real (tiny) git repos + a bare remote so the branch/upstream/dirty checks are
genuine. Config paths are redirected to a tmp dir.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from charter import config, workspace
from charter import commands_workspace as cw

_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def _git(*a, cwd):
    import os
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, **_ENV})


class ManifestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="edm-wsmanifest-"))
        self._orig = {k: getattr(config, k) for k in ("WORKSPACES_DIR", "ROOT")}
        config.WORKSPACES_DIR = self.tmp / "workspaces"
        config.ROOT = self.tmp
        config.WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._orig.items():
            setattr(config, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pushed_clone(self, ws: str, name: str, branch: str = "feature/x"):
        """A clone with a real upstream on `branch` (clean, pushed) — no blocker."""
        remote = self.tmp / "remotes" / f"{name}.git"
        remote.mkdir(parents=True)
        _git("init", "--bare", "-q", str(remote), cwd=self.tmp)
        wd = workspace.workspace_dir(ws)
        wd.mkdir(parents=True, exist_ok=True)
        clone = wd / name
        _git("clone", "-q", str(remote), str(clone), cwd=self.tmp)
        (clone / "f.txt").write_text("x")
        _git("checkout", "-q", "-b", branch, cwd=clone)
        _git("add", "-A", cwd=clone)
        _git("commit", "-q", "-m", "init", cwd=clone)
        _git("push", "-q", "-u", "origin", branch, cwd=clone)
        return clone


class TestManifestRoundtrip(ManifestBase):
    def test_write_read(self):
        workspace.write_manifest("ws", {"name": "ws", "repos": [{"name": "a", "branch": "main"}]})
        m = workspace.read_manifest("ws")
        self.assertEqual(m["repos"], [{"name": "a", "branch": "main"}])

    def test_missing_is_empty(self):
        self.assertEqual(workspace.read_manifest("nope"), {})


class TestSnapshot(ManifestBase):
    def test_snapshot_clean_pushed_repo(self):
        self._pushed_clone("ws", "repoA", branch="feature/x")
        rc = cw.cmd_workspace_snapshot(SimpleNamespace(name="ws", force=False, description="my task"))
        self.assertEqual(rc, 0)
        m = workspace.read_manifest("ws")
        self.assertEqual(m["repos"], [{"name": "repoA", "branch": "feature/x"}])
        self.assertEqual(m["description"], "my task")
        self.assertIn("updated_at", m)

    def test_enforce_push_refuses_dirty(self):
        clone = self._pushed_clone("ws", "repoA")
        (clone / "dirty.txt").write_text("uncommitted")  # now dirty
        rc = cw.cmd_workspace_snapshot(SimpleNamespace(name="ws", force=False, description=None))
        self.assertEqual(rc, 2)  # refused
        self.assertEqual(workspace.read_manifest("ws"), {})  # nothing written

    def test_force_snapshots_dirty(self):
        clone = self._pushed_clone("ws", "repoA")
        (clone / "dirty.txt").write_text("uncommitted")
        rc = cw.cmd_workspace_snapshot(SimpleNamespace(name="ws", force=True, description=None))
        self.assertEqual(rc, 0)
        self.assertEqual(workspace.read_manifest("ws")["repos"][0]["name"], "repoA")

    def test_blocker_for_unpushed_branch(self):
        # a local branch with no upstream → not restorable → blocker
        self._pushed_clone("ws", "repoA", branch="feature/x")
        clone = workspace.workspace_dir("ws") / "repoA"
        _git("checkout", "-q", "-b", "local-only", cwd=clone)  # new branch, never pushed
        blockers = cw._restore_blockers("ws")
        self.assertTrue(any("isn't pushed" in b for b in blockers), blockers)


class TestLiveness(ManifestBase):
    def setUp(self):
        super().setUp()
        (self.tmp / ".gitignore").write_text("/workspaces/*/*\n!/workspaces/.gitkeep\n")

    def test_set_live_roundtrip(self):
        self.assertFalse(workspace.is_live("w"))
        self.assertTrue(workspace.set_live("w", True))
        self.assertTrue(workspace.is_live("w"))
        self.assertEqual(workspace.live_workspaces(), {"w"})
        gi = (self.tmp / ".gitignore").read_text()
        self.assertIn("!/workspaces/w/workspace.json", gi)
        self.assertIn("!/workspaces/w/memory/**", gi)

    def test_unlive(self):
        workspace.set_live("w", True)
        self.assertTrue(workspace.set_live("w", False))
        self.assertFalse(workspace.is_live("w"))
        self.assertNotIn("!/workspaces/w/", (self.tmp / ".gitignore").read_text())

    def test_idempotent(self):
        workspace.set_live("w", True)
        self.assertFalse(workspace.set_live("w", True))  # already live → no change

    def test_two_live_workspaces(self):
        workspace.set_live("a", True)
        workspace.set_live("b", True)
        self.assertEqual(workspace.live_workspaces(), {"a", "b"})

    def test_save_refuses_local_workspace(self):
        (workspace.workspace_dir("w") / "memory").mkdir(parents=True)
        rc = cw.cmd_workspace_save(SimpleNamespace(name="w", message=None))
        self.assertEqual(rc, 1)  # LOCAL → refused (nothing committed)


if __name__ == "__main__":
    unittest.main()
