"""Overlapping dispatches into one working tree — the signal the tally can't give.

`personas/_dispatch/` records a dispatch when it **finishes**, so two five minutes
apart sequentially look exactly like two that overlapped. `inflight` records the
**start**, so overlap is observable.

The nudge is deliberately narrow: it fires only when the incoming persona declares
`dispatch-isolation: worktree`. A read-only fan-out overlapping is normal, and a
warning on it would train people to ignore the warning — which is the failure mode
that matters more than the one it reports.

It never denies. `isolation` is the caller's Agent-tool parameter and charter
cannot set it; saying so at dispatch time is the whole of what's available.
"""

from __future__ import annotations

import io
import json
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from charter import config, hooks, inflight


class _Stdin:
    """Feed a payload to a handler that reads stdin."""

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        self._orig = hooks._read_stdin
        hooks._read_stdin = lambda: self.payload
        return self

    def __exit__(self, *a):
        hooks._read_stdin = self._orig


def _dispatch_payload(agent: str) -> dict:
    return {"tool_name": "Task", "tool_input": {"subagent_type": agent}}


class InflightStore(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self._orig = config.STATE_DIR
        config.STATE_DIR = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self.addCleanup(lambda: setattr(config, "STATE_DIR", self._orig))

    def test_nothing_in_flight_initially(self):
        self.assertEqual(inflight.live(), [])

    def test_start_then_live(self):
        inflight.start("coder")
        self.assertEqual(inflight.live(), ["coder"])

    def test_finish_clears_one_record(self):
        inflight.start("coder")
        inflight.finish("coder")
        self.assertEqual(inflight.live(), [])

    def test_two_starts_need_two_finishes(self):
        """A repeat dispatch of one persona must not be retired by a single finish."""
        inflight.start("coder")
        inflight.start("coder")
        inflight.finish("coder")
        self.assertEqual(inflight.live(), ["coder"])

    def test_a_stale_record_is_pruned(self):
        """A killed process leaves a record behind; it must not warn forever."""
        inflight.start("coder")
        p = next((config.STATE_DIR / "dispatch-inflight").glob("*.json"))
        old = time.time() - inflight.TTL_SECONDS - 60
        import os
        os.utime(p, (old, old))
        self.assertEqual(inflight.live(), [])
        self.assertFalse(p.exists(), "a stale record should be removed, not just ignored")

    def test_a_fresh_record_is_not_pruned(self):
        inflight.start("coder")
        self.assertEqual(inflight.live(), ["coder"])

    def test_an_agent_name_with_path_characters_is_made_safe(self):
        inflight.start("../../etc/passwd")
        files = list((config.STATE_DIR / "dispatch-inflight").glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertNotIn("/", files[0].name)

    def test_finish_on_an_unknown_agent_is_harmless(self):
        inflight.finish("never-started")   # must not raise

    def test_start_with_no_agent_is_a_noop(self):
        self.assertIsNone(inflight.start(""))
        self.assertEqual(inflight.live(), [])


class DispatchNudge(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        root = Path(self._td.name)
        self._state, self._root = config.STATE_DIR, config.ROOT
        self._personas = config.PERSONAS_DIR
        config.STATE_DIR = root / ".charter"
        config.ROOT = root
        config.PERSONAS_DIR = root / "personas"
        self.addCleanup(self._td.cleanup)

        def _restore():
            config.STATE_DIR, config.ROOT = self._state, self._root
            config.PERSONAS_DIR = self._personas
        self.addCleanup(_restore)

        self._persona("coder", "dispatch-isolation: worktree\n")
        self._persona("explorer", "")

    def _persona(self, name: str, extra: str) -> None:
        d = config.PERSONAS_DIR / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "persona.md").write_text(
            f"---\nname: {name}\nrole: {name.title()}\nvault: {name}\n"
            f"delegate-when: things\n{extra}---\n\n# {name.title()}\nTask: x.\n")

    def _run(self, agent: str) -> str:
        buf = io.StringIO()
        with _Stdin(_dispatch_payload(agent)), redirect_stdout(buf):
            rc = hooks.pretooluse_dispatch()
        self.assertEqual(rc, 0, "a nudge must never break a turn")
        return buf.getvalue()

    def test_first_dispatch_is_silent(self):
        self.assertEqual(self._run("coder").strip(), "")

    def test_overlapping_code_writer_is_nudged(self):
        self._run("coder")                      # now in flight
        out = self._run("coder")
        spec = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(spec["permissionDecision"], "ask", "must nudge, never deny")
        self.assertIn("isolation: worktree", spec["permissionDecisionReason"])

    def test_a_read_only_persona_overlapping_is_silent(self):
        """The false-positive guard: only a declared code-writer is worth warning about."""
        self._run("coder")
        self.assertEqual(self._run("explorer").strip(), "")

    def test_silent_again_once_the_peer_finishes(self):
        self._run("coder")
        inflight.finish("coder")
        self.assertEqual(self._run("coder").strip(), "")

    def test_an_unknown_agent_does_not_raise(self):
        self._run("coder")
        self.assertEqual(self._run("no-such-persona").strip(), "")

    def test_non_dispatch_tools_are_ignored(self):
        buf = io.StringIO()
        with _Stdin({"tool_name": "Bash", "tool_input": {"command": "ls"}}), redirect_stdout(buf):
            self.assertEqual(hooks.pretooluse_dispatch(), 0)
        self.assertEqual(buf.getvalue().strip(), "")

    def test_empty_payload_is_ignored(self):
        buf = io.StringIO()
        with _Stdin({}), redirect_stdout(buf):
            self.assertEqual(hooks.pretooluse_dispatch(), 0)
        self.assertEqual(buf.getvalue().strip(), "")


class ManifestWiring(unittest.TestCase):
    def test_the_handler_is_registered(self):
        self.assertIn("pretooluse-dispatch", hooks._HANDLERS)

    def test_the_manifest_declares_it_against_task_and_agent(self):
        doc = json.loads((Path(__file__).resolve().parent.parent /
                          "hooks" / "hooks.json").read_text())
        cmds = [(g.get("matcher"), h["command"])
                for g in doc["hooks"]["PreToolUse"] for h in g["hooks"]]
        match = [m for m, c in cmds if "pretooluse-dispatch" in c]
        self.assertEqual(match, ["Task|Agent"],
                         "must match dispatch tools only — never every Bash call")


if __name__ == "__main__":
    unittest.main()
