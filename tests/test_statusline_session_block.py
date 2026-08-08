"""The session block — the rows under the repo tree that the layout already wasted.

A third column was the obvious idea and does not fit: two columns already need 131
cols, a third needs 167, and a typical pane is 150. The free space is *vertical* —
on a real control plane 11 of 13 left-hand rows were `│` padding — so this block
costs no width and works at 80 columns.

It renders ONLY when it has news. A block present every turn becomes furniture
within a day, and then a real guard denial in it gets no more attention than a
zero. Presence is the signal.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from charter import config, statusline, update


def _plain(lines):
    return [statusline.tui.strip_ansi(x) for x in lines]


class Silence(unittest.TestCase):
    """Nothing happening must produce nothing at all."""

    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        root = Path(self._td.name)
        self._root, self._state = config.ROOT, config.STATE_DIR
        config.ROOT, config.STATE_DIR = root, root / ".charter"
        (root / "charter.toml").write_text("schema = 1\n")
        self.addCleanup(self._td.cleanup)

        def _restore():
            config.ROOT, config.STATE_DIR = self._root, self._state
        self.addCleanup(_restore)

    def test_quiet_session_renders_nothing(self):
        self.assertEqual(statusline._session_block("no-such-session", "default"), [])

    def test_no_session_id_renders_nothing(self):
        self.assertEqual(statusline._session_block(None, "default"), [])

    def test_in_flight_dispatch_appears(self):
        from charter import inflight
        inflight.start("coder")
        out = _plain(statusline._session_block(None, "default"))
        self.assertTrue(any("in flight" in l and "coder" in l for l in out), out)

    def test_many_in_flight_are_truncated_not_wrapped(self):
        from charter import inflight
        for n in ("a", "b", "c", "d", "e"):
            inflight.start(n)
        out = _plain(statusline._session_block(None, "default"))
        self.assertTrue(any("…" in l for l in out), out)

    def test_version_drift_appears_with_its_fix(self):
        from charter import instance
        instance.set_locked_version(config.ROOT, "99.0.0")
        out = _plain(statusline._session_block(None, "default"))
        self.assertTrue(any("pinned" in l and "99.0.0" in l for l in out), out)
        self.assertTrue(any("charter version sync" in l for l in out), out)

    def test_a_matching_lock_is_not_reported(self):
        from charter import __version__, instance
        instance.set_locked_version(config.ROOT, __version__)
        out = _plain(statusline._session_block(None, "default"))
        self.assertFalse(any("pinned" in l for l in out), out)


class NeverBreaksTheStatusLine(unittest.TestCase):
    def setUp(self) -> None:
        # render() reaches _brand() -> update.maybe_spawn(), which forks a real
        # network child. A suite that quietly reaches the internet is not hermetic.
        self._spawn = update.maybe_spawn
        update.maybe_spawn = lambda: None
        self.addCleanup(lambda: setattr(update, "maybe_spawn", self._spawn))

    def test_a_broken_control_plane_yields_no_lines(self):
        orig = config.ROOT
        config.ROOT = Path("/nonexistent-control-plane-xyz")
        try:
            statusline._session_block("s", "default")   # must not raise
        finally:
            config.ROOT = orig

    def test_render_still_works_when_the_block_explodes(self):
        """The status line's whole contract is that it never crashes."""
        orig = statusline._session_block
        statusline._session_block = lambda *a: 1 / 0
        try:
            out = statusline.render({"session_id": "s"})
            self.assertTrue(out.strip())
        finally:
            statusline._session_block = orig

    def test_block_lines_respect_the_width(self):
        from charter import inflight
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        orig = config.STATE_DIR
        config.STATE_DIR = Path(td.name)
        try:
            for n in ("aaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbb", "ccccccccccccccc"):
                inflight.start(n)
            out = statusline.render({"session_id": "s"})
            for line in out.split("\n"):
                self.assertLessEqual(statusline.tui.width(line), 200)
        finally:
            config.STATE_DIR = orig


if __name__ == "__main__":
    unittest.main()
