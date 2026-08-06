"""Umbrella freshness: the 'behavior version' + delta since a session's baseline, and the
UserPromptSubmit nudge state machine (fires once per bump, silent on memory-only churn).
Uses a real tiny git repo so the git plumbing is genuinely exercised."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from charter import config, freshness as fr, hooks

_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def _git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, **_ENV})


class FreshBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="edm-fresh-"))
        self._orig = {k: getattr(config, k) for k in ("ROOT", "EDM_HOME", "SESSIONS_DIR")}
        config.ROOT = self.tmp
        config.EDM_HOME = self.tmp / ".edm"
        config.SESSIONS_DIR = config.EDM_HOME / "sessions"
        _git("init", "-q", cwd=self.tmp)
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._orig.items():
            setattr(config, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _commit(self, path, msg, content="x") -> str:
        f = self.tmp / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
        _git("add", "-A", cwd=self.tmp)
        _git("commit", "-q", "-m", msg, cwd=self.tmp)
        return subprocess.run(["git", "-C", str(self.tmp), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()


class TestVersionAndDelta(FreshBase):
    def test_behavior_count_excludes_memory(self):
        self._commit("CLAUDE.md", "c1")
        self._commit("edm/x.py", "c2")
        self._commit("personas/p/memory/m.md", "mem")   # not a behavior path
        self._commit("docs/y.md", "c4")
        self.assertEqual(fr.behavior_count("HEAD"), 3)   # c1, c2, c4 (not mem)

    def test_behavior_delta_filtered_newest_first(self):
        c1 = self._commit("CLAUDE.md", "c1")
        self._commit("edm/x.py", "c2")
        self._commit("personas/p/memory/m.md", "mem")
        self._commit("docs/y.md", "c4")
        self.assertEqual(fr.behavior_delta(c1), ["c4", "c2"])  # mem excluded, newest first

    def test_needs_fresh_session(self):
        c1 = self._commit("edm/x.py", "live-only change")
        self.assertFalse(fr.needs_fresh_session(c1))          # nothing after c1
        self._commit("edm/y.py", "another live change")
        self.assertFalse(fr.needs_fresh_session(c1))          # only edm/ → live
        self._commit("CLAUDE.md", "prompt change")
        self.assertTrue(fr.needs_fresh_session(c1))           # CLAUDE.md → fresh session


class TestNudge(FreshBase):
    def _nudge(self, sid, seen=None):
        if seen is not None:
            hooks._write_configver(hooks._configver_file(sid), seen)
        return hooks._config_update_nudge(sid)

    def test_first_prompt_records_baseline_no_nudge(self):
        self._commit("CLAUDE.md", "c1")
        self.assertEqual(self._nudge("s1"), "")
        self.assertTrue(hooks._configver_file("s1").exists())

    def test_up_to_date_silent(self):
        head = self._commit("CLAUDE.md", "c1")
        self.assertEqual(self._nudge("s1", seen=head), "")

    def test_behavior_update_nudges_once_then_quiet(self):
        c1 = self._commit("CLAUDE.md", "c1")
        self._commit("edm/x.py", "feat: add command")
        msg = self._nudge("s1", seen=c1)
        self.assertIn("Umbrella updated", msg)
        self.assertIn("feat: add command", msg)
        self.assertIn("(v1 → v2)", msg)
        self.assertEqual(self._nudge("s1"), "")   # baseline advanced → quiet next turn

    def test_memory_only_churn_is_silent(self):
        c1 = self._commit("CLAUDE.md", "c1")
        self._commit("personas/p/memory/m.md", "mem")
        self.assertEqual(self._nudge("s1", seen=c1), "")

    def test_restart_hint_for_claude_md(self):
        c1 = self._commit("edm/x.py", "c1")
        self._commit("CLAUDE.md", "prompt change")
        self.assertIn("fresh", self._nudge("s1", seen=c1))

    def test_live_hint_for_edm_only(self):
        c1 = self._commit("CLAUDE.md", "c1")
        self._commit("edm/x.py", "cli change")
        self.assertIn("no restart needed", self._nudge("s1", seen=c1))

    def test_no_session_id_no_nudge(self):
        self._commit("CLAUDE.md", "c1")
        self.assertEqual(hooks._config_update_nudge(None), "")


if __name__ == "__main__":
    unittest.main()
