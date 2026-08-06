"""The per-session workspace lock: once a session confirms a workspace, it can't be
switched mid-session (unless forced), and SessionStart nudges the agent to confirm one.

The lock is keyed by the Claude session id and stored at ``.edm/sessions/<sid>.lock``.
These paths are import-time-derived from ``EDM_HOME``, so the test repoints
``EDM_HOME``/``SESSIONS_DIR``/``TERMINALS_DIR``/``WORKSPACES_DIR`` at a tmp dir and pins
``$CLAUDE_CODE_SESSION_ID`` so ``set_active`` sees a stable session.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from charter import config, hooks, workspace


class WorkspaceLockBase(unittest.TestCase):
    SID = "sess-lock-test"

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="edm-wslock-"))
        self._orig = {k: getattr(config, k)
                      for k in ("ROOT", "EDM_HOME", "SESSIONS_DIR", "TERMINALS_DIR", "WORKSPACES_DIR")}
        config.ROOT = self.tmp
        config.EDM_HOME = self.tmp / ".edm"
        config.SESSIONS_DIR = config.EDM_HOME / "sessions"
        config.TERMINALS_DIR = config.EDM_HOME / "terminals"
        config.WORKSPACES_DIR = self.tmp / "workspaces"
        config.WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)

        self._orig_env = {k: os.environ.get(k) for k in ("CLAUDE_CODE_SESSION_ID", "EDM_WORKSPACE")}
        os.environ["CLAUDE_CODE_SESSION_ID"] = self.SID
        os.environ.pop("EDM_WORKSPACE", None)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        for k, v in self._orig.items():
            setattr(config, k, v)
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestSessionLock(WorkspaceLockBase):
    def test_first_confirm_locks_the_session(self):
        self.assertIsNone(workspace.is_locked())
        scope = workspace.set_active("alpha")
        self.assertNotEqual(scope, "locked")
        self.assertEqual(workspace.is_locked(), "alpha")
        self.assertEqual(workspace.resolve(), "alpha")

    def test_switch_mid_session_is_refused(self):
        workspace.set_active("alpha")
        scope = workspace.set_active("beta")
        self.assertEqual(scope, "locked")
        # nothing moved: still locked + resolving to alpha
        self.assertEqual(workspace.is_locked(), "alpha")
        self.assertEqual(workspace.resolve(), "alpha")

    def test_reaffirming_same_workspace_is_allowed(self):
        workspace.set_active("alpha")
        scope = workspace.set_active("alpha")
        self.assertNotEqual(scope, "locked")
        self.assertEqual(workspace.is_locked(), "alpha")

    def test_force_overrides_and_relocks(self):
        workspace.set_active("alpha")
        scope = workspace.set_active("beta", force=True)
        self.assertNotEqual(scope, "locked")
        self.assertEqual(workspace.is_locked(), "beta")
        self.assertEqual(workspace.resolve(), "beta")

    def test_unlock_then_switch(self):
        workspace.set_active("alpha")
        self.assertTrue(workspace.unlock())
        self.assertIsNone(workspace.is_locked())
        scope = workspace.set_active("beta")
        self.assertNotEqual(scope, "locked")
        self.assertEqual(workspace.is_locked(), "beta")

    def test_unlock_when_unlocked_is_noop(self):
        self.assertFalse(workspace.unlock())

    def test_env_pin_still_wins_over_lock(self):
        # $EDM_WORKSPACE is a hard launch pin: it dominates resolution regardless of lock.
        workspace.set_active("alpha")
        os.environ["EDM_WORKSPACE"] = "pinned"
        self.assertEqual(workspace.resolve(), "pinned")

    def test_a_different_session_starts_unlocked(self):
        workspace.set_active("alpha")               # locks THIS session
        self.assertIsNone(workspace.is_locked("other-session"))

    def test_reconcile_seeds_but_does_not_lock(self):
        # Simulate a terminal pane that already had a workspace, then a fresh session.
        tid = workspace._terminal_id("myterm")
        config.TERMINALS_DIR.mkdir(parents=True, exist_ok=True)
        (config.TERMINALS_DIR / f"{tid}.workspace").write_text("gamma\n")
        seeded = workspace.reconcile(session_id="fresh-sess", terminal_id="myterm")
        self.assertEqual(seeded, "gamma")
        # the session pointer is seeded (continuity) but the session is NOT locked
        self.assertEqual(workspace.resolve(session_id="fresh-sess"), "gamma")
        self.assertIsNone(workspace.is_locked("fresh-sess"))


class TestConfirmNudge(WorkspaceLockBase):
    def test_nudge_fires_when_unconfirmed(self):
        msg = hooks._workspace_confirm_nudge(self.SID)
        self.assertIn("Confirm the workspace", msg)
        self.assertIn("charter workspace use", msg)

    def test_no_nudge_once_locked(self):
        workspace.set_active("alpha")
        self.assertEqual(hooks._workspace_confirm_nudge(self.SID), "")

    def test_no_nudge_when_env_pinned(self):
        os.environ["EDM_WORKSPACE"] = "pinned"
        self.assertEqual(hooks._workspace_confirm_nudge(self.SID), "")

    def test_sessionstart_emits_the_nudge(self):
        old = sys.stdin
        sys.stdin = io.StringIO(json.dumps({"session_id": self.SID}))
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                hooks.sessionstart()
        finally:
            sys.stdin = old
        out = buf.getvalue().strip()
        self.assertTrue(out, "sessionstart should emit context when unconfirmed")
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Confirm the workspace", ctx)


class TestCommandLayer(WorkspaceLockBase):
    """The `edm workspace` command handlers surface the lock as a non-zero exit + message."""

    def _run(self, fn, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = fn(SimpleNamespace(**kw))
        return rc, buf.getvalue()

    def test_use_locks_then_refuses_switch(self):
        rc, _ = self._run(commands.cmd_workspace_use, name="alpha", force=False)
        self.assertEqual(rc, 0)
        self.assertEqual(workspace.is_locked(), "alpha")
        rc, out = self._run(commands.cmd_workspace_use, name="beta", force=False)
        self.assertEqual(rc, 2)  # refused (message goes to stderr)
        self.assertEqual(workspace.is_locked(), "alpha")

    def test_use_force_switches(self):
        self._run(commands.cmd_workspace_use, name="alpha", force=False)
        rc, _ = self._run(commands.cmd_workspace_use, name="beta", force=True)
        self.assertEqual(rc, 0)
        self.assertEqual(workspace.is_locked(), "beta")

    def test_create_use_respects_lock(self):
        self._run(commands.cmd_workspace_use, name="alpha", force=False)
        rc, out = self._run(commands.cmd_workspace_create, name="beta", use=True, force=False, repos=[])
        self.assertEqual(rc, 2)
        # the workspace is still created even though the switch was refused
        self.assertTrue((config.WORKSPACES_DIR / "beta").exists())
        self.assertEqual(workspace.is_locked(), "alpha")


# imported late so the module-under-test picks up the patched config at call time
from charter import commands_workspace as commands  # noqa: E402


if __name__ == "__main__":
    unittest.main()
