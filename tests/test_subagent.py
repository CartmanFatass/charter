"""Tests for subagent tracking, tree construction, rollout parsing, and rendering."""

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

from charter import cli, hooks, inflight, statusline, subagent, trace


def write_test_rollout(
    sessions_dir: Path,
    session_id: str,
    parent_id: str | None = None,
    nickname: str | None = None,
    timestamp: str | None = None,
    cwd: str | None = None,
    collab_events: list[dict] | None = None,
    mtime: float | None = None,
) -> Path:
    """Helper to create a mock rollout file matching Codex rollout structure."""
    now = datetime.now(timezone.utc)
    year = str(now.year)
    month = f"{now.month:02d}"
    day = f"{now.day:02d}"
    stamp = now.strftime("%Y-%m-%dT%H-%M-%S")
    target_dir = sessions_dir / year / month / day
    target_dir.mkdir(parents=True, exist_ok=True)

    file_path = target_dir / f"rollout-{stamp}-{session_id}.jsonl"

    ts = timestamp or now.isoformat()
    meta_payload = {
        "id": session_id,
        "parent_thread_id": parent_id,
        "timestamp": ts,
        "cwd": cwd or str(sessions_dir.parent),
    }
    if nickname:
        meta_payload["agent_nickname"] = nickname

    lines = [json.dumps({"type": "session_meta", "payload": meta_payload})]

    if collab_events:
        for ev in collab_events:
            lines.append(json.dumps(ev))

    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if mtime is not None:
        os.utime(file_path, (mtime, mtime))
    return file_path


class TestSubagentParsing(unittest.TestCase):
    def test_parse_rollout_filename(self):
        fn = "rollout-2026-01-15T17-47-44-019bc10d-c89d-7352-935c-76b351384357.jsonl"
        res = subagent.parse_rollout_filename(fn)
        self.assertIsNotNone(res)
        dt, sid = res
        self.assertEqual(sid, "019bc10d-c89d-7352-935c-76b351384357")
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 1)
        self.assertEqual(dt.day, 15)
        self.assertEqual(dt.hour, 17)
        self.assertEqual(dt.minute, 47)
        self.assertEqual(dt.second, 44)

    def test_peek_session_link_simple(self):
        tmp = Path(tempfile.mkdtemp(prefix="test-subagent-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        p = write_test_rollout(tmp / "sessions", "sess-1", nickname="Oracle")
        link = subagent.peek_session_link(p)
        self.assertIsNotNone(link)
        self.assertEqual(link.id, "sess-1")
        self.assertIsNone(link.parent_id)
        self.assertEqual(link.name, "Oracle")

    def test_peek_session_link_source_nickname(self):
        tmp = Path(tempfile.mkdtemp(prefix="test-subagent-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        dir_p = tmp / "sessions" / "2026" / "01" / "01"
        dir_p.mkdir(parents=True, exist_ok=True)
        file_p = dir_p / "rollout-2026-01-01T10-00-00-sess-2.jsonl"
        meta = {
            "type": "session_meta",
            "payload": {
                "id": "sess-2",
                "parent_thread_id": "sess-1",
                "source": {
                    "subagent": {
                        "thread_spawn": {
                            "agent_nickname": "Gauss"
                        }
                    }
                },
                "timestamp": "2026-01-01T10:00:00Z"
            }
        }
        file_p.write_text(json.dumps(meta) + "\n", encoding="utf-8")
        link = subagent.peek_session_link(file_p)
        self.assertIsNotNone(link)
        self.assertEqual(link.id, "sess-2")
        self.assertEqual(link.parent_id, "sess-1")
        self.assertEqual(link.name, "Gauss")
    def test_peek_session_link_nested_thread_spawn_parent(self):
        tmp = Path(tempfile.mkdtemp(prefix="test-subagent-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        dir_p = tmp / "sessions" / "2026" / "01" / "01"
        dir_p.mkdir(parents=True, exist_ok=True)
        file_p = dir_p / "rollout-2026-01-01T10-00-00-nested-gc.jsonl"
        meta = {
            "type": "session_meta",
            "payload": {
                "id": "nested-gc",
                "source": {
                    "subagent": {
                        "thread_spawn": {
                            "parent_thread_id": "parent-sess-1",
                            "agent_nickname": "NestedWorker"
                        }
                    }
                },
                "timestamp": "2026-01-01T10:00:00Z"
            }
        }
        file_p.write_text(json.dumps(meta) + "\n", encoding="utf-8")
        link = subagent.peek_session_link(file_p)
        self.assertIsNotNone(link)
        self.assertEqual(link.id, "nested-gc")
        self.assertEqual(link.parent_id, "parent-sess-1")
        self.assertEqual(link.name, "NestedWorker")

        tmp = Path(tempfile.mkdtemp(prefix="test-subagent-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        collab_ev = {
            "type": "event_msg",
            "timestamp": "2026-01-01T10:05:00Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "receiver_agents": [
                        {"thread_id": "child-1", "agent_nickname": "Singer"},
                        {"thread_id": "child-2", "agent_nickname": "Euler"}
                    ],
                    "agents_states": {
                        "child-1": {"running": True},
                        "child-2": {"completed": True}
                    }
                }
            }
        }
        p = write_test_rollout(tmp / "sessions", "root-1", collab_events=[collab_ev])
        agents = subagent.parse_rollout_subagents(p)
        self.assertEqual(len(agents), 2)
        by_id = {a.id: a for a in agents}
        self.assertIn("child-1", by_id)
        self.assertEqual(by_id["child-1"].name, "Singer")
        self.assertEqual(by_id["child-1"].status, "running")
        self.assertIn("child-2", by_id)
        self.assertEqual(by_id["child-2"].name, "Euler")
        self.assertEqual(by_id["child-2"].status, "completed")


class TestSubagentTree(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test-subagent-tree-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.sessions_dir = self.tmp / "sessions"

    def test_build_subagent_tree_hierarchy(self):
        """Replicates multi-level test: root -> child (Gauss) -> grand (Scout), sibling (Singer)."""
        root = "01a00bd0-0000-4000-8000-000000000001"
        child = "01a00bd0-0000-4000-8000-000000000002"
        grand = "01a00bd0-0000-4000-8000-000000000003"
        sibling = "01a00bd0-0000-4000-8000-000000000004"

        now_ts = datetime.now(timezone.utc).timestamp()
        write_test_rollout(self.sessions_dir, root, parent_id=None, nickname=None, mtime=now_ts)
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="Gauss", mtime=now_ts)
        write_test_rollout(self.sessions_dir, grand, parent_id=child, nickname="Scout", mtime=now_ts)
        write_test_rollout(self.sessions_dir, sibling, parent_id=root, nickname="Singer", mtime=now_ts)

        tree = subagent.build_subagent_tree(
            root_id=root,
            sessions_dir=self.sessions_dir,
            max_days_back=3,
            now_ts=now_ts,
        )

        self.assertEqual(tree.total_count, 3, "Should include child, grandchild, sibling")
        self.assertEqual(len(tree.nodes), 2, "Root should have two top-level children")
        names = {n.name for n in tree.nodes}
        self.assertEqual(names, {"Gauss", "Singer"})

        gauss_node = next(n for n in tree.nodes if n.name == "Gauss")
        self.assertEqual(len(gauss_node.children), 1)
        self.assertEqual(gauss_node.children[0].name, "Scout")
        self.assertEqual(gauss_node.children[0].depth, 2)

        singer_node = next(n for n in tree.nodes if n.name == "Singer")
        self.assertEqual(len(singer_node.children), 0)
        self.assertEqual(singer_node.depth, 1)
    def test_find_root_session_id_multi_level(self):
        root = "root-100"
        child = "child-200"
        grand = "grand-300"
        now_ts = datetime.now(timezone.utc).timestamp()
        write_test_rollout(self.sessions_dir, root, parent_id=None, nickname="MainCoordinator", mtime=now_ts)
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="WorkerOne", mtime=now_ts)
        write_test_rollout(self.sessions_dir, grand, parent_id=child, nickname="SpecialistSubagent", mtime=now_ts)

        resolved_from_grand = subagent.find_root_session_id(grand, sessions_dir=self.sessions_dir)
        self.assertEqual(resolved_from_grand, root)

        resolved_from_child = subagent.find_root_session_id(child, sessions_dir=self.sessions_dir)
        self.assertEqual(resolved_from_child, root)

        resolved_from_root = subagent.find_root_session_id(root, sessions_dir=self.sessions_dir)
        self.assertEqual(resolved_from_root, root)

    def test_build_subagent_tree_inflight_integration(self):
        from charter import inflight, config
        state_tmp = Path(tempfile.mkdtemp(prefix="test-inflight-tree-"))
        self.addCleanup(lambda: shutil.rmtree(state_tmp, ignore_errors=True))
        with mock.patch.object(config, "STATE_DIR", state_tmp):
            inflight.start("scout-analyzer")
            tree = subagent.build_subagent_tree("main-sess", sessions_dir=self.sessions_dir)
            names = [n.name for n in tree.nodes]
            self.assertIn("scout-analyzer", names)
            node = next(n for n in tree.nodes if n.name == "scout-analyzer")
            self.assertEqual(node.status, "running")

    def test_subagent_event_watcher_detects_changes(self):
        watcher = subagent.SubagentEventWatcher(sessions_dir=self.sessions_dir, max_days_back=3)
        # Initially no changes
        self.assertFalse(watcher.check_for_changes())

        # Write a new rollout file -> change detected
        write_test_rollout(self.sessions_dir, "sess-event-test", parent_id=None, nickname="TestAgent")
        self.assertTrue(watcher.check_for_changes())

        # Without further modifications -> False
        self.assertFalse(watcher.check_for_changes())

    def test_resolve_status_timeout(self):
        now_ts = 1000.0
        # modified 10 seconds ago -> running
        recent_time = datetime.fromtimestamp(990.0, tz=timezone.utc)
        self.assertEqual(subagent.resolve_status(None, recent_time, now_ts), "running")

        # modified 100 seconds ago (> 45s) -> completed
        old_time = datetime.fromtimestamp(800.0, tz=timezone.utc)
        self.assertEqual(subagent.resolve_status(None, old_time, now_ts), "completed")

class TestSubagentRendering(unittest.TestCase):
    def test_format_elapsed(self):
        t0 = datetime.fromtimestamp(1000.0, tz=timezone.utc)
        t_10s = datetime.fromtimestamp(1010.0, tz=timezone.utc)
        t_2m5s = datetime.fromtimestamp(1125.0, tz=timezone.utc)
        t_1h2m = datetime.fromtimestamp(4720.0, tz=timezone.utc)

        self.assertEqual(subagent.format_elapsed(t0, t_10s), "10s")
        self.assertEqual(subagent.format_elapsed(t0, t_2m5s), "2m05s")
        self.assertEqual(subagent.format_elapsed(t0, t_1h2m), "1h02m")

    def test_render_tree_page(self):
        node_grand = subagent.SubagentTreeNode(id="g1", name="Scout", status="running", depth=2)
        node_child = subagent.SubagentTreeNode(id="c1", name="Gauss", status="completed", depth=1, children=[node_grand])
        node_sibling = subagent.SubagentTreeNode(id="c2", name="Singer", status="starting", depth=1)
        tree = subagent.SubagentTree(root_id="r1", nodes=[node_child, node_sibling], total_count=3)

        plain = subagent.render_subagent_tree_page_plain(tree)
        self.assertIn("Subagent tree", plain)
        self.assertIn("Gauss", plain)
        self.assertIn("Scout", plain)
        self.assertIn("Singer", plain)
        self.assertIn("3 node(s)", plain)
        self.assertIn("├─", plain)
        self.assertIn("└─", plain)

    def test_render_empty_tree_page(self):
        tree = subagent.SubagentTree(root_id="r1", nodes=[], total_count=0)
        plain = subagent.render_subagent_tree_page_plain(tree)
        self.assertIn("Session r1 (main session)", plain)
        self.assertIn("(no subagents in this session)", plain)
        self.assertIn("0 node(s)", plain)

    def test_comm_triggered_animation_and_bubbles(self):
        now = datetime.fromtimestamp(1000.0, tz=timezone.utc)
        recent_time = datetime.fromtimestamp(995.0, tz=timezone.utc)  # 5s ago (< 15s)
        old_time = datetime.fromtimestamp(950.0, tz=timezone.utc)     # 50s ago (> 15s)

        node_running = subagent.SubagentTreeNode(id="a1", name="Worker", status="running", depth=1)
        node_completed = subagent.SubagentTreeNode(id="a2", name="DoneWorker", status="completed", depth=1)

        # 1. Idle running node (no exchange) -> icon is ▶, no speech bubble
        chip_idle = subagent.render_chip(node_running, now=now, color=False, tick=1)
        self.assertIn("▶ Worker", chip_idle)
        self.assertNotIn("💬", chip_idle)
        self.assertNotIn("➔", chip_idle)

        # 2. Idle running node (expired exchange > 15s) -> icon is ▶, no speech bubble
        old_ex = subagent.SubagentExchange(
            timestamp=old_time, sender_id="a1", sender_name="Worker",
            receiver_id="root", receiver_name="Main", content="Old status report", kind="response"
        )
        chip_expired = subagent.render_chip(node_running, now=now, color=False, tick=1, latest_exchange=old_ex)
        self.assertIn("▶ Worker", chip_expired)
        self.assertNotIn("Old status report", chip_expired)

        # 3. Actively communicating running node (< 15s) -> spinner icon (frame 1), speech bubble
        active_ex = subagent.SubagentExchange(
            timestamp=recent_time, sender_id="a1", sender_name="Worker",
            receiver_id="root", receiver_name="Main", content="Active working progress update", kind="response"
        )
        chip_active = subagent.render_chip(node_running, now=now, color=False, tick=1, latest_exchange=active_ex)
        self.assertIn(f"{subagent.SPINNER_FRAMES[1]} Worker", chip_active)
        self.assertIn("➔ Main:", chip_active)
        self.assertIn("Active working progress update", chip_active)

        # 4. Completed node with recent exchange -> ✔, no speech bubble
        chip_done = subagent.render_chip(node_completed, now=now, color=False, tick=1, latest_exchange=active_ex)
        self.assertIn("○ DoneWorker", chip_done)
        self.assertIn("stopped", chip_done)
        self.assertNotIn("Active working progress", chip_done)

    def test_branch_flow_animation_and_fading(self):
        now = datetime.fromtimestamp(1000.0, tz=timezone.utc)
        t_blazing = datetime.fromtimestamp(999.0, tz=timezone.utc)  # 1s ago (blazing)
        t_fading = datetime.fromtimestamp(994.0, tz=timezone.utc)   # 6s ago (fading)
        t_settled = datetime.fromtimestamp(980.0, tz=timezone.utc)  # 20s ago (settled)

        node_child = subagent.SubagentTreeNode(id="worker-1", name="WorkerAgent", status="running", depth=1)
        node_grand = subagent.SubagentTreeNode(id="spec-2", name="Specialist", status="running", depth=2)

        # 1. Downward flow (dispatch from root to worker-1)
        ex_down = subagent.SubagentExchange(
            timestamp=t_blazing, sender_id="root", sender_name="MainCoordinator",
            receiver_id="worker-1", receiver_name="WorkerAgent", kind="dispatch",
            content="Please run security scan"
        )
        branch_blaze, tag_blaze = subagent.render_branch_connector(
            is_last=False, latest_exchange=ex_down, node=node_child, parent_id="root",
            now=now, color=False, tick=2
        )
        self.assertIn("├➔", branch_blaze)
        self.assertIn("▼ [MainCoordinator ➔ WorkerAgent]", tag_blaze)

        # 2. Upward flow (response from spec-2 to worker-1)
        ex_up = subagent.SubagentExchange(
            timestamp=t_fading, sender_id="spec-2", sender_name="Specialist",
            receiver_id="worker-1", receiver_name="WorkerAgent", kind="response",
            content="Security scan clean"
        )
        branch_fade, tag_fade = subagent.render_branch_connector(
            is_last=True, latest_exchange=ex_up, node=node_grand, parent_id="worker-1",
            now=now, color=False, tick=1
        )
        self.assertIn("└▲", branch_fade)
        self.assertIn("▲ [Specialist ➔ WorkerAgent]", tag_fade)

        # 3. Settled flow (> 15s ago) -> returns standard branch and empty tag
        ex_old = subagent.SubagentExchange(
            timestamp=t_settled, sender_id="worker-1", sender_name="WorkerAgent",
            receiver_id="root", receiver_name="MainCoordinator", kind="response",
            content="Old report"
        )
        branch_settled, tag_settled = subagent.render_branch_connector(
            is_last=False, latest_exchange=ex_old, node=node_child, parent_id="root",
            now=now, color=False, tick=1
        )
        self.assertEqual(branch_settled, "├─ ")
        self.assertEqual(tag_settled, "")

    def test_verbose_rendering_and_feed(self):
        now = datetime.fromtimestamp(1000.0, tz=timezone.utc)
        recent_time = datetime.fromtimestamp(995.0, tz=timezone.utc)
        node = subagent.SubagentTreeNode(id="worker-id-12345", name="CoderAgent", status="running", depth=1)
        tree = subagent.SubagentTree(root_id="sess-root-1", nodes=[node], total_count=1)
        ex = subagent.SubagentExchange(
            timestamp=recent_time, sender_id="worker-id-12345", sender_name="CoderAgent",
            receiver_id="sess-root-1", receiver_name="Main", content="Running implementation tasks", kind="response"
        )
        # Default (verbose=False) -> no feed, no [id] tag in chip
        plain_default = subagent.render_subagent_tree_page_plain(tree, now=now, verbose=False)
        self.assertNotIn("⚡ Live Exchanges", plain_default)
        self.assertNotIn("[worker-i]", plain_default)

        # Verbose (verbose=True) with recent exchanges -> live exchange feed, [id] tag
        lines_verbose = subagent.render_subagent_tree_page(tree, now=now, color=False, recent_exchanges=[ex], verbose=True)
        text_verbose = "\n".join(lines_verbose)
        self.assertIn("⚡ Live Exchanges", text_verbose)
        self.assertIn("[worker-i]", text_verbose)
        self.assertIn("Running implementation tasks", text_verbose)

    def test_render_parallel_chips_and_summary(self):
        node_grand = subagent.SubagentTreeNode(id="g1", name="Scout", status="running", depth=2)
        node_child = subagent.SubagentTreeNode(id="c1", name="Gauss", status="completed", depth=1, children=[node_grand])
        node_sibling = subagent.SubagentTreeNode(id="c2", name="Singer", status="running", depth=1)
        tree = subagent.SubagentTree(root_id="r1", nodes=[node_child, node_sibling], total_count=3)

        chips = subagent.render_parallel_chips(tree.nodes, width=120, color=False)
        self.assertEqual(len(chips), 1)
        self.assertIn("Gauss", chips[0])
        self.assertIn("▾", chips[0])
        self.assertIn("Singer", chips[0])

        summ = subagent.subagent_summary(tree, color=False)
        self.assertIn("3 subagents", summ)
        self.assertIn("2 running", summ)
        self.assertIn("1 stopped", summ)
class TestSubagentCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test-subagent-cli-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.sessions_dir = self.tmp / "sessions"
        self.env = mock.patch.dict(os.environ, {"CODEX_SESSIONS_PATH": str(self.sessions_dir)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_cli_tree_and_list_json(self):
        root = "root-sess-123"
        child = "child-sess-456"
        write_test_rollout(self.sessions_dir, root, parent_id=None, nickname="Main")
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="WorkerAgent")

        parser = cli.build_parser()

        # Test subagent tree --json
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            args = parser.parse_args(["subagent", "tree", "--session", root, "--json"])
            rc = args.func(args)
            self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())
        self.assertEqual(out["root_id"], root)
        self.assertEqual(out["total_count"], 1)
        self.assertEqual(out["nodes"][0]["name"], "WorkerAgent")

        # Test subagent list --json
        buf_list = io.StringIO()
        with mock.patch("sys.stdout", buf_list):
            args_list = parser.parse_args(["subagent", "list", "--session", root, "--json"])
            rc_list = args_list.func(args_list)
            self.assertEqual(rc_list, 0)
        out_list = json.loads(buf_list.getvalue())
        self.assertEqual(len(out_list), 1)
        self.assertEqual(out_list[0]["name"], "WorkerAgent")

        # Test subagent show
        buf_show = io.StringIO()
        with mock.patch("sys.stdout", buf_show):
            args_show = parser.parse_args(["subagent", "show", child, "--json"])
            rc_show = args_show.func(args_show)
            self.assertEqual(rc_show, 0)
        out_show = json.loads(buf_show.getvalue())
        self.assertEqual(out_show["id"], child)
        self.assertEqual(out_show["name"], "WorkerAgent")
        self.assertEqual(out_show["parent_id"], root)

    def test_characterization_legacy_subagent_json_shape(self):
        root = "root-charact-001"
        child = "child-charact-002"
        write_test_rollout(self.sessions_dir, root, parent_id=None, nickname="Main")
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="ChildWorker")

        parser = cli.build_parser()

        # tree json shape
        buf_tree = io.StringIO()
        with mock.patch("sys.stdout", buf_tree):
            args = parser.parse_args(["subagent", "tree", "--session", root, "--json"])
            rc = args.func(args)
            self.assertEqual(rc, 0)
        tree_data = json.loads(buf_tree.getvalue())
        self.assertIn("root_id", tree_data)
        self.assertIn("total_count", tree_data)
        self.assertIn("updated_at", tree_data)
        self.assertIn("nodes", tree_data)
        self.assertIsInstance(tree_data["nodes"], list)
        self.assertEqual(tree_data["nodes"][0]["name"], "ChildWorker")
        self.assertIn("status", tree_data["nodes"][0])
        self.assertIn("children", tree_data["nodes"][0])

        # list json shape
        buf_list = io.StringIO()
        with mock.patch("sys.stdout", buf_list):
            args = parser.parse_args(["subagent", "list", "--session", root, "--json"])
            rc = args.func(args)
            self.assertEqual(rc, 0)
        list_data = json.loads(buf_list.getvalue())
        self.assertIsInstance(list_data, list)
        self.assertIn("id", list_data[0])
        self.assertIn("name", list_data[0])
        self.assertIn("status", list_data[0])

        # show json shape
        buf_show = io.StringIO()
        with mock.patch("sys.stdout", buf_show):
            args = parser.parse_args(["subagent", "show", child, "--json"])
            rc = args.func(args)
            self.assertEqual(rc, 0)
        show_data = json.loads(buf_show.getvalue())
        self.assertIn("id", show_data)
        self.assertIn("name", show_data)
        self.assertIn("status", show_data)
        self.assertIn("parent_id", show_data)
        self.assertIn("rollout_file", show_data)

    def test_legacy_status_and_additive_runtime_state(self):
        info = subagent.SubagentInfo(id="a1", name="Worker", status="completed")
        d = info.to_dict()
        self.assertEqual(d["status"], "completed")
        self.assertEqual(d["runtime_state"], "stopped")

        node = subagent.SubagentTreeNode(id="n1", name="WorkerNode", status="completed")
        nd = node.to_dict()
        self.assertEqual(nd["status"], "completed")
        self.assertEqual(nd["runtime_state"], "stopped")

    def test_stopped_subagent_never_renders_completed_word_in_text(self):
        root = "root-stopped-text"
        child = "child-stopped-text"
        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z")
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="OldWorker", timestamp="2026-08-19T10:00:10Z", mtime=100.0)

        parser = cli.build_parser()
        buf_tree = io.StringIO()
        with mock.patch("sys.stdout", buf_tree):
            args = parser.parse_args(["subagent", "tree", "--session", root, "--plain"])
            args.func(args)
        tree_text = buf_tree.getvalue()
        self.assertNotIn("completed", tree_text)
        self.assertIn("stopped", tree_text)

        buf_list = io.StringIO()
        with mock.patch("sys.stdout", buf_list):
            args_list = parser.parse_args(["subagent", "list", "--session", root])
            args_list.func(args_list)
        list_text = buf_list.getvalue()
        self.assertIn("RUNTIME", list_text)
        self.assertIn("stopped", list_text)
    def test_cli_log_and_exchanges(self):
        root = "root-sess-999"
        child = "child-sess-888"
        collab_ev = {
            "type": "event_msg",
            "timestamp": "2026-01-01T10:05:00Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "Please review this authentication pull request.",
                    "receiver_agents": [
                        {"thread_id": child, "agent_nickname": "SecurityReviewer"}
                    ]
                }
            }
        }
        child_events = [
            {
                "type": "event_msg",
                "timestamp": "2026-01-01T10:06:00Z",
                "payload": {
                    "type": "user_message",
                    "message": "Review authentication code."
                }
            },
            {
                "type": "event_msg",
                "timestamp": "2026-01-01T10:07:30Z",
                "payload": {
                    "type": "agent_message",
                    "message": "Audit completed: found 0 vulnerabilities."
                }
            }
        ]
        write_test_rollout(self.sessions_dir, root, parent_id=None, nickname="MainCoordinator", collab_events=[collab_ev])
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="SecurityReviewer", collab_events=child_events)

        exchanges = subagent.extract_subagent_exchanges(session_id=root, sessions_dir=self.sessions_dir)
        self.assertGreaterEqual(len(exchanges), 2)
        prompt_ex = next(e for e in exchanges if e.kind in ("dispatch", "collab_msg"))
        self.assertEqual(prompt_ex.sender_name, "MainCoordinator")
        self.assertEqual(prompt_ex.receiver_name, "SecurityReviewer")
        self.assertIn("review", prompt_ex.content.lower())

        resp_ex = next(e for e in exchanges if e.kind == "response")
        self.assertEqual(resp_ex.sender_name, "SecurityReviewer")
        self.assertEqual(resp_ex.receiver_name, "MainCoordinator")
        self.assertIn("Audit completed", resp_ex.content)

        # Test CLI subagent log --json
        parser = cli.build_parser()
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            args = parser.parse_args(["subagent", "log", "--session", root, "--json"])
            rc = args.func(args)
            self.assertEqual(rc, 0)
        logs_out = json.loads(buf.getvalue())
        self.assertGreaterEqual(len(logs_out), 2)
        self.assertEqual(logs_out[0]["sender_name"], "MainCoordinator")
    def test_cli_parser_interval_and_verbose_flags(self):
        parser = cli.build_parser()

        # Default interval is 10.0
        args_def = parser.parse_args(["subagent", "tree"])
        self.assertEqual(args_def.interval, 10.0)
        self.assertFalse(args_def.verbose)

        # Custom -i / --interval
        args_i = parser.parse_args(["subagent", "tree", "-i", "5.5", "-v"])
        self.assertEqual(args_i.interval, 5.5)
        self.assertTrue(args_i.verbose)

        args_long = parser.parse_args(["subagent", "tree", "--interval", "12", "--verbose"])
        self.assertEqual(args_long.interval, 12.0)
        self.assertTrue(args_long.verbose)

        # List with verbose
        args_list_v = parser.parse_args(["subagent", "list", "-v"])
        self.assertTrue(args_list_v.verbose)

        # Show with verbose
        args_show_v = parser.parse_args(["subagent", "show", "some-id", "-v"])
        self.assertTrue(args_show_v.verbose)

    def test_cli_tree_baseline_and_verbose_text(self):
        root = "root-sess-tree"
        child = "child-sess-tree"
        collab_ev = {
            "type": "event_msg",
            "timestamp": "2026-01-01T10:05:00Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "Investigate performance bottleneck.",
                    "receiver_agents": [
                        {"thread_id": child, "agent_nickname": "PerfAnalyst"}
                    ]
                }
            }
        }
        write_test_rollout(self.sessions_dir, root, parent_id=None, nickname="Main", collab_events=[collab_ev])
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="PerfAnalyst")

        parser = cli.build_parser()

        # 1. Plain tree output: includes root session and child
        buf_plain = io.StringIO()
        with mock.patch("sys.stdout", buf_plain):
            args = parser.parse_args(["subagent", "tree", "--session", root, "--plain"])
            rc = args.func(args)
            self.assertEqual(rc, 0)
        out_plain = buf_plain.getvalue()
        self.assertIn("Session root-ses (main session)", out_plain)
        self.assertIn("PerfAnalyst", out_plain)
        self.assertNotIn("⚡ Live Exchanges", out_plain)

        # 2. Verbose tree output: includes live exchanges feed panel
        buf_verb = io.StringIO()
        with mock.patch("sys.stdout", buf_verb):
            args = parser.parse_args(["subagent", "tree", "--session", root, "--verbose", "--plain"])
            rc = args.func(args)
            self.assertEqual(rc, 0)
        out_verb = buf_verb.getvalue()
        self.assertIn("Session root-ses (main session)", out_verb)
        self.assertIn("⚡ Live Exchanges", out_verb)
        self.assertIn("Investigate performance", out_verb)

    def test_cli_list_verbose_text(self):
        root = "root-sess-list"
        child = "child-sess-list-12345678"
        write_test_rollout(self.sessions_dir, root, parent_id=None, nickname="Main")
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="WorkerOne")

        parser = cli.build_parser()

        # Standard list: truncated ID
        buf_std = io.StringIO()
        with mock.patch("sys.stdout", buf_std):
            args = parser.parse_args(["subagent", "list", "--session", root])
            rc = args.func(args)
            self.assertEqual(rc, 0)
        out_std = buf_std.getvalue()
        self.assertIn("WorkerOne", out_std)
        self.assertIn(child[:8], out_std)

        # Verbose list: full ID
        buf_verb = io.StringIO()
        with mock.patch("sys.stdout", buf_verb):
            args = parser.parse_args(["subagent", "list", "--session", root, "-v"])
            rc = args.func(args)
            self.assertEqual(rc, 0)
        out_verb = buf_verb.getvalue()
        self.assertIn("WorkerOne", out_verb)
        self.assertIn(child, out_verb)
class TestSubagentHooksAndStatusline(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test-subagent-hooks-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.env = mock.patch.dict(os.environ, {"CHARTER_STATE_DIR": str(self.tmp / "state")})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_subagentstart_and_subagentstop_hooks(self):
        payload_start = json.dumps({
            "session_id": "test-sid-1",
            "agent_nickname": "ScoutSubagent",
        })
        with mock.patch("sys.stdin", io.StringIO(payload_start)):
            rc = hooks.dispatch("subagentstart", None)
            self.assertEqual(rc, 0)

        live = inflight.live()
        self.assertIn("ScoutSubagent", live)

        payload_stop = json.dumps({
            "session_id": "test-sid-1",
            "agent_nickname": "ScoutSubagent",
        })
        with mock.patch("sys.stdin", io.StringIO(payload_stop)):
            rc = hooks.dispatch("subagentstop", None)
            self.assertEqual(rc, 0)

        live_after = inflight.live()
        self.assertNotIn("ScoutSubagent", live_after)

    def test_statusline_displays_subagent_news(self):
        sid = "sid-subagent-news"
        trace.record("subagent_start", sid, agent="Scout")
        news = statusline._session_news(sid)
        self.assertTrue(any("subagent" in item for item in news))

    def test_statusline_dashboard_scheme_a_and_b(self):
        sessions_dir = self.tmp / "sessions"
        now_ts = datetime.now(timezone.utc).timestamp()
        root = "dash-root-1"
        child = "dash-child-2"
        write_test_rollout(sessions_dir, root, parent_id=None, nickname="MainCoordinator", mtime=now_ts)
        write_test_rollout(sessions_dir, child, parent_id=root, nickname="WorkerAgent", mtime=now_ts)

        with mock.patch.dict(os.environ, {"CODEX_SESSIONS_PATH": str(sessions_dir)}):
            # 1. Scheme A: Standard width (80 columns) -> Stacked Subagents section
            with mock.patch("charter.tui.term_width", return_value=80):
                out_80 = statusline.render({"session_id": root})
                self.assertIn("subagents", out_80)
                self.assertIn("WorkerAgent", out_80)

            # 2. Scheme B: Wide screen (160 columns) -> 3-Column Layout
            with mock.patch("charter.tui.term_width", return_value=160):
                out_160 = statusline.render({"session_id": root})
                self.assertIn("subagents", out_160)
                self.assertIn("WorkerAgent", out_160)

            # 3. Clean classic layout when no subagents
            with mock.patch("charter.tui.term_width", return_value=80):
                out_clean = statusline.render({"session_id": "empty-sess-id"})
                self.assertNotIn("subagents", out_clean)
if __name__ == "__main__":
    unittest.main()
