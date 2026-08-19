"""Tests for charter observe CLI commands, parser flags, JSON schemas, and read-only invariants."""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from charter import cli
from tests.test_subagent import write_test_rollout


class TestObserveCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test-cli-obs-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.sessions_dir = self.tmp / "sessions"
        self.env = mock.patch.dict(os.environ, {"CODEX_SESSIONS_PATH": str(self.sessions_dir)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_bare_observe_defaults_to_position(self):
        parser = cli.build_parser()
        args = parser.parse_args(["observe"])
        self.assertEqual(args.func.__name__, "cmd_observe_position")

    def test_observe_position_json_schema(self):
        root = "root-obs-cli-01"
        child = "child-obs-cli-01"
        spawn_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"CLI-101\",\"direction\":\"SCDMP\",\"actor_role\":\"EM\"}\n```",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Gauss"}],
                },
            },
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_ev])

        parser = cli.build_parser()
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            args = parser.parse_args(["observe", "position", "--session", root, "--json"])
            rc = args.func(args)
            self.assertEqual(rc, 0)

        out = json.loads(buf.getvalue())
        self.assertEqual(out["schema"], 1)
        self.assertEqual(out["view"], "position")
        self.assertEqual(out["root_id"], root)
        self.assertIn("captured_at", out)
        self.assertIsInstance(out["data"], list)
        self.assertEqual(len(out["data"]), 1)
        self.assertEqual(out["data"][0]["external_id"], "CLI-101")
        self.assertEqual(out["data"][0]["actor_role"], "EM")
        self.assertEqual(out["data"][0]["direction"], "SCDMP")

    def test_observe_obligations_json_and_plain(self):
        root = "root-obl-cli-01"
        child = "child-obl-cli-01"
        spawn_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"OBL-101\"}\n```",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Noether"}],
                },
            },
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_ev])

        parser = cli.build_parser()

        # JSON
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            args = parser.parse_args(["observe", "obligations", "--session", root, "--json"])
            rc = args.func(args)
            self.assertEqual(rc, 0)

        out = json.loads(buf.getvalue())
        self.assertEqual(out["view"], "obligations")
        self.assertEqual(len(out["data"]), 1)
        self.assertEqual(out["data"][0]["kind"], "return_expected")

        # Plain text
        buf_plain = io.StringIO()
        with mock.patch("sys.stdout", buf_plain):
            args_plain = parser.parse_args(["observe", "obligations", "--session", root, "--plain"])
            rc_plain = args_plain.func(args_plain)
            self.assertEqual(rc_plain, 0)
        text = buf_plain.getvalue()
        self.assertIn("Noether", text)
        self.assertIn("return to", text)

    def test_observe_actors_cli(self):
        root = "root-act-cli-01"
        child = "child-act-cli-01"
        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z")
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="Euler", timestamp="2026-08-19T10:00:10Z")

        parser = cli.build_parser()
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            args = parser.parse_args(["observe", "actors", "--session", root, "--json"])
            rc = args.func(args)
            self.assertEqual(rc, 0)

        out = json.loads(buf.getvalue())
        self.assertEqual(out["view"], "actors")
        self.assertIn("actors", out["data"])
        self.assertIn("relations", out["data"])

    def test_observe_explain_cli(self):
        root = "root-exp-cli-01"
        child = "child-exp-cli-01"
        spawn_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"EXP-999\",\"direction\":\"SGSP\"}\n```",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Gauss"}],
                },
            },
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_ev])

        parser = cli.build_parser()
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            args = parser.parse_args(["observe", "explain", "EXP-999", "--session", root, "--json"])
            rc = args.func(args)
            self.assertEqual(rc, 0)

        out = json.loads(buf.getvalue())
        self.assertEqual(out["view"], "explain")
        self.assertEqual(out["data"]["external_id"], "EXP-999")
        self.assertEqual(out["data"]["direction"], "SGSP")

    def test_empty_session_returns_exit_code_zero_with_empty_schema(self):
        parser = cli.build_parser()
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            args = parser.parse_args(["observe", "position", "--json"])
            rc = args.func(args)
            self.assertEqual(rc, 0)

        out = json.loads(buf.getvalue())
        self.assertEqual(out["schema"], 1)
        self.assertEqual(out["root_id"], "")
        self.assertEqual(out["data"], [])

    def test_filesystem_write_guard_read_only_invariant(self):
        """Proof that charter observe never creates, modifies, or deletes files."""
        root = "root-fs-guard-01"
        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z")

        def take_tree_snapshot(directory: Path) -> dict[str, tuple[int, float]]:
            tree: dict[str, tuple[int, float]] = {}
            if not directory.exists():
                return tree
            for p in directory.rglob("*"):
                if p.is_file():
                    st = p.stat()
                    tree[str(p.relative_to(directory))] = (st.st_size, st.st_mtime)
            return tree

        snap_before = take_tree_snapshot(self.tmp)

        parser = cli.build_parser()
        for cmd in (
            ["observe", "position", "--session", root],
            ["observe", "obligations", "--session", root],
            ["observe", "actors", "--session", root],
            ["observe", "timeline", "--session", root],
            ["observe", "position", "--session", root, "--json"],
            ["observe", "obligations", "--session", root, "--json"],
        ):
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                args = parser.parse_args(cmd)
                rc = args.func(args)
                self.assertEqual(rc, 0)

        snap_after = take_tree_snapshot(self.tmp)
        self.assertEqual(snap_before, snap_after, "File system state was modified during charter observe invocation!")



class TestObserveWatchMode(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test-cli-watch-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.sessions_dir = self.tmp / "sessions"

    def test_watcher_detects_rollout_modification_and_trace_changes(self):
        from charter import subagent, trace, config

        state_tmp = self.tmp / "state"
        with mock.patch.object(config, "PERSONA_STATE_DIR", state_tmp):
            watcher = subagent.SubagentEventWatcher(sessions_dir=self.sessions_dir, max_days_back=3)

            # Initial check -> False
            self.assertFalse(watcher.check_for_changes())

            # Write rollout -> True
            f = write_test_rollout(self.sessions_dir, "sess-watch-01", timestamp="2026-08-19T10:00:00Z")
            self.assertTrue(watcher.check_for_changes())

            # No change -> False
            self.assertFalse(watcher.check_for_changes())

            # Modify rollout -> True
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "hello"}}) + "\n")
            self.assertTrue(watcher.check_for_changes())

            # No change -> False
            self.assertFalse(watcher.check_for_changes())

            # Trace change -> True
            trace.record("subagent_start", "sess-watch-01", agent="Gauss")
            self.assertTrue(watcher.check_for_changes())

if __name__ == "__main__":
    unittest.main()
