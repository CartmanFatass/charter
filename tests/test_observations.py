"""Tests for normalized observations, session indexing, and rollout record streaming."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from charter import subagent
from tests.test_subagent import write_test_rollout


class TestSessionIndexAndRolloutRecords(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test-obs-index-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.sessions_dir = self.tmp / "sessions"

    def test_duplicate_rollout_files_newest_modified_at_wins(self):
        root = "root-dup-01"
        t1 = 1787130000.0
        t2 = 1787130500.0

        dir_p = self.sessions_dir / "2026" / "08" / "19"
        dir_p.mkdir(parents=True, exist_ok=True)

        f1 = dir_p / f"rollout-2026-08-19T10-00-00-{root}.jsonl"
        meta1 = {
            "type": "session_meta",
            "payload": {
                "id": root,
                "parent_thread_id": None,
                "agent_nickname": "OlderFile",
                "timestamp": "2026-08-19T10:00:00Z",
            },
        }
        f1.write_text(json.dumps(meta1) + "\n", encoding="utf-8")
        os.utime(f1, (t1, t1))

        f2 = dir_p / f"rollout-2026-08-19T10-05-00-{root}.jsonl"
        meta2 = {
            "type": "session_meta",
            "payload": {
                "id": root,
                "parent_thread_id": None,
                "agent_nickname": "NewerFile",
                "timestamp": "2026-08-19T10:05:00Z",
            },
        }
        f2.write_text(json.dumps(meta2) + "\n", encoding="utf-8")
        os.utime(f2, (t2, t2))

        index = subagent.build_session_index(max_days_back=3, sessions_dir=self.sessions_dir)
        self.assertIn(root, index.links)
        self.assertEqual(index.links[root].name, "NewerFile")
        self.assertEqual(index.rollout_files[root], f2)

    def test_malformed_lines_are_skipped_without_aborting(self):
        dir_p = self.sessions_dir / "2026" / "08" / "19"
        dir_p.mkdir(parents=True, exist_ok=True)
        f = dir_p / "rollout-2026-08-19T10-00-00-sess-badlines.jsonl"
        lines = [
            json.dumps({"type": "session_meta", "payload": {"id": "sess-badlines", "timestamp": "2026-08-19T10:00:00Z"}}),
            "{malformed json line here",
            "",
            "   ",
            json.dumps({"type": "event_msg", "timestamp": "2026-08-19T10:01:00Z", "payload": {"type": "user_message", "message": "hello"}}),
            "another broken line } { [",
            json.dumps({"type": "event_msg", "timestamp": "2026-08-19T10:02:00Z", "payload": {"type": "task_complete", "summary": "done"}}),
        ]
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")

        records = list(subagent.iter_rollout_records(f))
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0].line_number, 1)
        self.assertEqual(records[0].entry_type, "session_meta")
        self.assertEqual(records[1].line_number, 5)
        self.assertEqual(records[1].raw_kind, "user_message")
        self.assertEqual(records[2].line_number, 7)
        self.assertEqual(records[2].raw_kind, "task_complete")

    def test_deterministic_line_numbers_and_timestamps(self):
        f = write_test_rollout(
            self.sessions_dir,
            "sess-records-01",
            nickname="TestWorker",
            timestamp="2026-08-19T10:00:00Z",
            collab_events=[
                {
                    "type": "event_msg",
                    "timestamp": "2026-08-19T10:01:00Z",
                    "payload": {"type": "user_message", "message": "Do task"},
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-08-19T10:02:00Z",
                    "payload": {
                        "type": "item_completed",
                        "item": {"type": "function_call", "name": "shell", "arguments": "{}"},
                    },
                },
            ],
        )

        records1 = list(subagent.iter_rollout_records(f))
        records2 = list(subagent.iter_rollout_records(f))
        self.assertEqual(len(records1), 3)
        self.assertEqual(records1, records2)

        self.assertEqual(records1[0].session_id, "sess-records-01")
        self.assertEqual(records1[0].file_path, f)
        self.assertEqual(records1[0].line_number, 1)
        self.assertEqual(records1[0].entry_type, "session_meta")
        self.assertEqual(records1[0].raw_kind, "session_meta")
        self.assertEqual(records1[0].timestamp, datetime(2026, 8, 19, 10, 0, 0, tzinfo=timezone.utc))

        self.assertEqual(records1[1].line_number, 2)
        self.assertEqual(records1[1].entry_type, "event_msg")
        self.assertEqual(records1[1].raw_kind, "user_message")

        self.assertEqual(records1[2].line_number, 3)
        self.assertEqual(records1[2].entry_type, "event_msg")
        self.assertEqual(records1[2].raw_kind, "shell")

    def test_root_child_grandchild_descendant_order(self):
        root = "root-hier-100"
        child1 = "child-hier-101"
        child2 = "child-hier-102"
        grand1 = "grand-hier-103"
        now_ts = datetime(2026, 8, 19, 10, 0, 0, tzinfo=timezone.utc).timestamp()

        write_test_rollout(self.sessions_dir, root, parent_id=None, nickname="Root", mtime=now_ts)
        write_test_rollout(self.sessions_dir, child1, parent_id=root, nickname="Child1", mtime=now_ts)
        write_test_rollout(self.sessions_dir, child2, parent_id=root, nickname="Child2", mtime=now_ts)
        write_test_rollout(self.sessions_dir, grand1, parent_id=child1, nickname="Grand1", mtime=now_ts)

        index = subagent.build_session_index(max_days_back=3, sessions_dir=self.sessions_dir)
        descendants = subagent.descendant_session_ids(index, root)

        # Root must come first, followed by descendants
        self.assertEqual(descendants[0], root)
        self.assertIn(child1, descendants)
        self.assertIn(child2, descendants)
        self.assertIn(grand1, descendants)
        self.assertEqual(len(descendants), 4)

        # Child1 must precede Grand1 in descendant traversal
        self.assertLess(descendants.index(child1), descendants.index(grand1))



class TestInflightSessionScoping(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test-inflight-scope-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.state_dir = self.tmp / "state"
        self.sessions_dir = self.tmp / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        from charter import config
        self.patcher = unittest.mock.patch.object(config, "STATE_DIR", self.state_dir)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_inflight_isolation_between_two_root_sessions(self):
        from charter import inflight

        root_a = "root-sess-alpha"
        root_b = "root-sess-beta"
        now_ts = datetime(2026, 8, 19, 10, 0, 0, tzinfo=timezone.utc).timestamp()

        write_test_rollout(self.sessions_dir, root_a, parent_id=None, nickname="MainAlpha", mtime=now_ts)
        write_test_rollout(self.sessions_dir, root_b, parent_id=None, nickname="MainBeta", mtime=now_ts)

        inflight.start("WorkerAlpha", session_id=root_a, agent_id="child-a-1", parent_id=root_a)
        inflight.start("WorkerBeta", session_id=root_b, agent_id="child-b-1", parent_id=root_b)

        tree_a = subagent.build_subagent_tree(root_a, sessions_dir=self.sessions_dir)
        names_a = [n.name for n in tree_a.nodes]
        self.assertIn("WorkerAlpha", names_a)
        self.assertNotIn("WorkerBeta", names_a)

        tree_b = subagent.build_subagent_tree(root_b, sessions_dir=self.sessions_dir)
        names_b = [n.name for n in tree_b.nodes]
        self.assertIn("WorkerBeta", names_b)
        self.assertNotIn("WorkerAlpha", names_b)

    def test_inflight_schema_1_backward_compatibility(self):
        from charter import inflight

        # Write a legacy schema-1 record directly into inflight directory
        d = inflight._dir()
        d.mkdir(parents=True, exist_ok=True)
        legacy_file = d / "ScoutLegacy.12345.json"
        legacy_file.write_text(json.dumps({"agent": "ScoutLegacy", "ts": 1787130000.0}), encoding="utf-8")

        records = inflight.live_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["agent"], "ScoutLegacy")
        self.assertEqual(records[0]["schema"], 1)
        self.assertIsNone(records[0]["session_id"])

    def test_inflight_finish_session_scoped_does_not_remove_other_session(self):
        from charter import inflight

        root_a = "root-a"
        root_b = "root-b"

        inflight.start("WorkerSameName", session_id=root_a, agent_id="worker-a")
        inflight.start("WorkerSameName", session_id=root_b, agent_id="worker-b")

        recs_before = inflight.live_records()
        self.assertEqual(len(recs_before), 2)

        # Finish only session A's worker
        inflight.finish("WorkerSameName", session_id=root_a)

        recs_after = inflight.live_records()
        self.assertEqual(len(recs_after), 1)
        self.assertEqual(recs_after[0]["session_id"], root_b)
        self.assertEqual(recs_after[0]["agent_id"], "worker-b")


class TestObservationSnapshotCollection(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test-obs-snap-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.sessions_dir = self.tmp / "sessions"

    def test_stable_event_ids_across_two_collections(self):
        from charter import observations

        root = "root-stable-id-1"
        child1 = "child-stable-id-2"
        child2 = "child-stable-id-3"
        t0 = "2026-08-19T10:00:00Z"

        spawn_event = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:01:00Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "receiver_agents": [
                        {"thread_id": child1, "agent_nickname": "Gauss"},
                        {"thread_id": child2, "agent_nickname": "Euler"},
                    ],
                },
            },
        }

        write_test_rollout(self.sessions_dir, root, parent_id=None, nickname="Main", timestamp=t0, collab_events=[spawn_event])
        write_test_rollout(self.sessions_dir, child1, parent_id=root, nickname="Gauss", timestamp="2026-08-19T10:01:05Z")
        write_test_rollout(self.sessions_dir, child2, parent_id=root, nickname="Euler", timestamp="2026-08-19T10:01:10Z")

        fixed_cap = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        snap1 = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir, captured_at=fixed_cap)
        snap2 = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir, captured_at=fixed_cap)

        self.assertEqual(snap1.to_dict(), snap2.to_dict())

    def test_event_ids_differ_for_two_receivers_from_same_spawn(self):
        from charter import observations

        root = "root-diff-receivers"
        child1 = "child-rx-1"
        child2 = "child-rx-2"

        spawn_event = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:01:00Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "receiver_agents": [
                        {"thread_id": child1, "agent_nickname": "Gauss"},
                        {"thread_id": child2, "agent_nickname": "Euler"},
                    ],
                },
            },
        }

        write_test_rollout(self.sessions_dir, root, parent_id=None, nickname="Main", collab_events=[spawn_event])
        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)

        spawn_events = [e for e in snap.events if e.kind == "actor_spawned"]
        self.assertEqual(len(spawn_events), 2)
        self.assertNotEqual(spawn_events[0].id, spawn_events[1].id)
        self.assertNotEqual(spawn_events[0].actor_id, spawn_events[1].actor_id)

    def test_snapshot_events_ordered_by_timestamp_and_id(self):
        from charter import observations

        root = "root-order-test"
        child = "child-order-test"

        events = [
            {"type": "event_msg", "timestamp": "2026-08-19T10:05:00Z", "payload": {"type": "agent_message", "message": "Later msg"}},
            {"type": "event_msg", "timestamp": "2026-08-19T10:01:00Z", "payload": {"type": "user_message", "message": "Earlier msg"}},
        ]

        write_test_rollout(self.sessions_dir, root, parent_id=None, timestamp="2026-08-19T10:00:00Z", collab_events=events)
        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)

        for i in range(len(snap.events) - 1):
            e1 = snap.events[i]
            e2 = snap.events[i + 1]
            self.assertTrue(
                (e1.observed_at < e2.observed_at) or (e1.observed_at == e2.observed_at and e1.id <= e2.id)
            )

    def test_source_path_and_line_number_survive_in_evidence(self):
        from charter import observations

        root = "root-evidence-test"
        collab_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:01:00Z",
            "payload": {"type": "task_complete", "summary": "Finished job"},
        }
        fpath = write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[collab_ev])
        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)

        ret_events = [e for e in snap.events if e.kind == "actor_returned"]
        self.assertEqual(len(ret_events), 1)
        ev = ret_events[0]
        self.assertEqual(len(ev.evidence), 1)
        self.assertEqual(ev.evidence[0].file_path, str(fpath))
        self.assertEqual(ev.evidence[0].line_number, 2)
        self.assertEqual(ev.evidence[0].raw_kind, "task_complete")
        self.assertEqual(ev.evidence[0].evidence_class, "mechanical")

    def test_free_text_containing_error_words_remains_message_sent(self):
        from charter import observations

        root = "root-freetext-test"
        msg_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:02:00Z",
            "payload": {
                "type": "agent_message",
                "message": "We encountered an error and blocked timeout, task done",
            },
        }
        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[msg_ev])
        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)

        # No incident_seen or actor_returned should be manufactured
        self.assertEqual([e for e in snap.events if e.kind == "incident_seen"], [])
        self.assertEqual([e for e in snap.events if e.kind == "actor_returned"], [])

        # It must only be message_sent
        msgs = [e for e in snap.events if e.kind == "message_sent"]
        self.assertEqual(len(msgs), 1)
        self.assertIn("error and blocked timeout", msgs[0].attributes["content"])


class TestWorkflowDeclarationParsing(unittest.TestCase):
    def test_valid_exact_declaration(self):
        from charter import observations

        text = (
            "Please perform this task:\n"
            "```charter-observe\n"
            "{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"SCDMP-B2\",\"title\":\"B2 closure review\",\"direction\":\"SCDMP\",\"actor_role\":\"EM\",\"owner_role\":\"ROOT\"}\n"
            "```\n"
            "Thank you."
        )
        decls = observations.parse_workflow_declarations(text)
        self.assertEqual(len(decls), 1)
        d = decls[0]
        self.assertEqual(d.schema, 1)
        self.assertEqual(d.event, "dispatch")
        self.assertEqual(d.work_id, "SCDMP-B2")
        self.assertEqual(d.title, "B2 closure review")
        self.assertEqual(d.direction, "SCDMP")
        self.assertEqual(d.actor_role, "EM")
        self.assertEqual(d.owner_role, "ROOT")

    def test_rejections_and_warnings(self):
        from charter import observations

        # 1. Malformed JSON
        warns1: list[str] = []
        d1 = observations.parse_workflow_declarations("```charter-observe\n{broken json\n```", warnings=warns1)
        self.assertEqual(d1, ())
        self.assertTrue(len(warns1) > 0)

        # 2. Duplicate keys
        warns2: list[str] = []
        d2 = observations.parse_workflow_declarations("```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"event\":\"intake\"}\n```", warnings=warns2)
        self.assertEqual(d2, ())
        self.assertTrue(len(warns2) > 0)

        # 3. Unknown schema
        warns3: list[str] = []
        d3 = observations.parse_workflow_declarations("```charter-observe\n{\"schema\":99,\"event\":\"dispatch\"}\n```", warnings=warns3)
        self.assertEqual(d3, ())
        self.assertTrue(len(warns3) > 0)

        # 4. Unknown field
        warns4: list[str] = []
        d4 = observations.parse_workflow_declarations("```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"extra_unknown\":true}\n```", warnings=warns4)
        self.assertEqual(d4, ())
        self.assertTrue(len(warns4) > 0)

        # 5. Unknown event
        warns5: list[str] = []
        d5 = observations.parse_workflow_declarations("```charter-observe\n{\"schema\":1,\"event\":\"unknown_op\"}\n```", warnings=warns5)
        self.assertEqual(d5, ())
        self.assertTrue(len(warns5) > 0)

        # 6. Missing work_id for intake
        warns6: list[str] = []
        d6 = observations.parse_workflow_declarations("```charter-observe\n{\"schema\":1,\"event\":\"intake\"}\n```", warnings=warns6)
        self.assertEqual(d6, ())
        self.assertTrue(len(warns6) > 0)

        # 7. Multiple declaration blocks
        warns7: list[str] = []
        mult = (
            "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\"}\n```\n"
            "and\n"
            "```charter-observe\n{\"schema\":1,\"event\":\"intake\",\"work_id\":\"W1\"}\n```"
        )
        d7 = observations.parse_workflow_declarations(mult, warnings=warns7)
        self.assertEqual(d7, ())
        self.assertTrue(len(warns7) > 0)

        # 8. Oversized block (>4096 bytes)
        warns8: list[str] = []
        huge_val = "x" * 5000
        oversized = f"```charter-observe\n{{\"schema\":1,\"event\":\"dispatch\",\"title\":\"{huge_val}\"}}\n```"
        d8 = observations.parse_workflow_declarations(oversized, warnings=warns8)
        self.assertEqual(d8, ())
        self.assertTrue(len(warns8) > 0)

    def test_ordinary_prose_is_ignored(self):
        from charter import observations

        prose = "I am declaring event dispatch and schema 1 with work_id SCDMP-B2 as EM role."
        self.assertEqual(observations.parse_workflow_declarations(prose), ())

    def test_mirrored_declaration_deduplication(self):
        from charter import observations

        root = "root-mirror-decl"
        child = "child-mirror-decl"
        decl_text = (
            "```charter-observe\n"
            "{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"SCDMP-B2\",\"title\":\"Closure review\",\"direction\":\"SCDMP\",\"actor_role\":\"EM\"}\n"
            "```\n"
            "Review task."
        )

        spawn_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:01:00Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": decl_text,
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Gauss"}],
                },
            },
        }

        user_msg_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:01:05Z",
            "payload": {
                "type": "user_message",
                "message": decl_text,
            },
        }

        tmp = Path(tempfile.mkdtemp(prefix="test-obs-mirror-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        sdir = tmp / "sessions"

        write_test_rollout(sdir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_ev])
        write_test_rollout(sdir, child, parent_id=root, nickname="Gauss", timestamp="2026-08-19T10:01:05Z", collab_events=[user_msg_ev])

        snap = observations.collect_observation_snapshot(root, sessions_dir=sdir)
        decl_events = [e for e in snap.events if e.kind == "workflow_declared"]

        # Must be deduplicated into 1 event with 2 evidence references
        self.assertEqual(len(decl_events), 1)
        self.assertEqual(len(decl_events[0].evidence), 2)
        self.assertEqual(decl_events[0].evidence[0].evidence_class, "declared")
        self.assertEqual(decl_events[0].evidence[1].evidence_class, "declared")

if __name__ == "__main__":
    unittest.main()
