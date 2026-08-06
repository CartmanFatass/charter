"""Tests for charter.glstate: the status-line's background GitLab refresh.

F2: the refresh used to shell out to ``bin/edm`` — a script that lived in the
old monorepo the engine was extracted from. No control plane ships it, so the
spawn raised FileNotFoundError every time, silently swallowed by a bare
``except Exception``. These tests pin the fixed behavior: the command targets
the installed package via ``-m charter``, and a spawn failure must not start
the retry cooldown (only a successful spawn should).
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from charter import glstate

from tests._isolation import PersonaIso


class MaybeSpawnCommandTests(PersonaIso):
    def _dirs(self):
        d = self.tmp / "somerepo"
        d.mkdir(parents=True, exist_ok=True)
        return [d]

    def test_spawns_the_installed_package_not_a_repo_path(self):
        captured = {}

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            return mock.MagicMock()

        with mock.patch.object(glstate.subprocess, "Popen", side_effect=fake_popen):
            glstate.maybe_spawn(self._dirs())

        self.assertIn("cmd", captured, "Popen was never called — nothing was stale?")
        cmd = captured["cmd"]
        self.assertEqual(cmd[0], sys.executable)
        # -m charter, not a script path inside the control plane (no bin/edm here).
        self.assertEqual(cmd[1:4], ["-m", "charter", "gl-refresh"])
        joined = " ".join(str(c) for c in cmd)
        self.assertNotIn("bin/edm", joined)
        self.assertNotIn(str(glstate.config.ROOT), joined)

    def test_workspace_is_appended_when_given(self):
        captured = {}

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            return mock.MagicMock()

        with mock.patch.object(glstate.subprocess, "Popen", side_effect=fake_popen):
            glstate.maybe_spawn(self._dirs(), workspace="demo")

        self.assertEqual(captured["cmd"][-2:], ["--workspace", "demo"])

    def test_spawn_failure_does_not_start_the_cooldown(self):
        """A genuine FileNotFoundError/etc must not suppress the next attempt —
        only a spawn that actually launched should arm the cooldown lock."""
        with mock.patch.object(
            glstate.subprocess, "Popen", side_effect=FileNotFoundError("no such file")
        ):
            glstate.maybe_spawn(self._dirs())  # must not raise

        self.assertFalse(glstate._lock_file().exists(), "lock must not be armed on spawn failure")

    def test_successful_spawn_arms_the_cooldown(self):
        with mock.patch.object(glstate.subprocess, "Popen", return_value=mock.MagicMock()):
            glstate.maybe_spawn(self._dirs())

        self.assertTrue(glstate._lock_file().exists(), "lock should be armed once the refresh launched")


if __name__ == "__main__":
    unittest.main()
