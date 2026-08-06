"""Reactive memory: recording a memory commits it immediately (and pushes in the
background) for the committed stores — LIVE workspaces and persistent persona memory — so
it reaches the shared repo without waiting for memory-sync / the Stop-hook autosave. LOCAL
workspaces + ephemeral persona memory stay private; --no-sync opts out."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from charter import commands, config, workspace
from charter import commands_workspace as cw
from tests._isolation import PersonaIso

_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def _git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, **_ENV})


class ReactiveCommitCase(unittest.TestCase):
    """The commit half, against a real repo with no remote (push is a no-op → no subprocess)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="edm-react-"))
        self._orig_root = config.ROOT
        config.ROOT = self.tmp
        _git("init", "-q", cwd=self.tmp)
        (self.tmp / "seed").write_text("x")
        _git("add", "-A", cwd=self.tmp)
        _git("commit", "-q", "-m", "seed", cwd=self.tmp)
        self.addCleanup(self._restore)

    def _restore(self):
        config.ROOT = self._orig_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _log_files(self):
        return subprocess.run(["git", "-C", str(self.tmp), "log", "--name-only", "--format="],
                              capture_output=True, text=True).stdout

    def test_reactive_commits_locally(self):
        d = self.tmp / "personas" / "p" / "memory"
        d.mkdir(parents=True)
        (d / "m.md").write_text("# t\n\na durable fact\n")
        rc = commands.commit_memory_reactive(["personas/p/memory/m.md"], "p: m")
        self.assertEqual(rc, 0)
        self.assertIn("personas/p/memory/m.md", self._log_files())  # committed immediately

    def test_reactive_refuses_secret(self):
        d = self.tmp / "personas" / "p" / "memory"
        d.mkdir(parents=True)
        # an AWS-key-shaped value → the shared secret guard refuses the commit
        (d / "s.md").write_text("# t\n\ntoken AKIAIOSFODNN7EXAMPLE here\n")
        rc = commands.commit_memory_reactive(["personas/p/memory/s.md"], "p: s")
        self.assertEqual(rc, 1)
        self.assertNotIn("personas/p/memory/s.md", self._log_files())  # never committed


class ReactiveWiringCase(PersonaIso):
    """The command wiring: reactive fires for LIVE, not LOCAL, and --no-sync opts out.
    commit_memory_reactive is spied so no real git/network happens."""

    def setUp(self):
        super().setUp()
        (config.ROOT / ".gitignore").write_text("/workspaces/*/*\n")
        self.calls = []
        self._orig_reactive = cw.commit_memory_reactive
        cw.commit_memory_reactive = lambda paths, title: (self.calls.append((paths, title)), 0)[1]
        self.addCleanup(lambda: setattr(cw, "commit_memory_reactive", self._orig_reactive))

    def _args(self, ws, no_sync=False):
        return SimpleNamespace(workspace=ws, text="a fact", title=None, message=None,
                               query=None, no_sync=no_sync)

    def test_live_triggers_reactive(self):
        workspace.ensure("live1")
        workspace.scaffold("live1")
        workspace.set_live("live1", True)
        cw.cmd_workspace_remember(self._args("live1"))
        self.assertEqual(len(self.calls), 1)
        paths, _title = self.calls[0]
        self.assertTrue(any(p.endswith("MEMORY.md") for p in paths))  # index included

    def test_local_no_reactive(self):
        workspace.ensure("loc1")
        workspace.scaffold("loc1")  # not live → private
        cw.cmd_workspace_remember(self._args("loc1"))
        self.assertEqual(self.calls, [])

    def test_no_sync_opts_out(self):
        workspace.ensure("live2")
        workspace.scaffold("live2")
        workspace.set_live("live2", True)
        cw.cmd_workspace_remember(self._args("live2", no_sync=True))
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
