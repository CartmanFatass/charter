"""Tests for normalized observations, session indexing, and rollout record streaming."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

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

        today = datetime.now(timezone.utc)
        dir_p = self.sessions_dir / today.strftime("%Y") / today.strftime("%m") / today.strftime("%d")
        dir_p.mkdir(parents=True, exist_ok=True)

        f1 = dir_p / f"rollout-{today.strftime('%Y-%m-%dT10-00-00')}-{root}.jsonl"
        meta1 = {
            "type": "session_meta",
            "payload": {
                "id": root,
                "parent_thread_id": None,
                "agent_nickname": "OlderFile",
                "timestamp": f"{today.strftime('%Y-%m-%d')}T10:00:00Z",
            },
        }
        f1.write_text(json.dumps(meta1) + "\n", encoding="utf-8")
        os.utime(f1, (t1, t1))

        f2 = dir_p / f"rollout-{today.strftime('%Y-%m-%dT10-05-00')}-{root}.jsonl"
        meta2 = {
            "type": "session_meta",
            "payload": {
                "id": root,
                "parent_thread_id": None,
                "agent_nickname": "NewerFile",
                "timestamp": f"{today.strftime('%Y-%m-%d')}T10:05:00Z",
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
    def test_read_records_is_strictly_read_only_and_does_not_delete_stale(self):
        from charter import inflight

        d = inflight._dir()
        d.mkdir(parents=True, exist_ok=True)
        stale_file = d / "StaleWorker.99999.json"
        stale_file.write_text(json.dumps({"agent": "StaleWorker", "ts": 100.0}), encoding="utf-8")
        os.utime(stale_file, (100.0, 100.0))

        # live_records() must NOT delete stale files on disk
        recs = inflight.live_records()
        self.assertEqual(len(recs), 0)
        self.assertTrue(stale_file.exists(), "live_records() deleted a stale file on disk, violating read-only invariant!")

    def test_inflight_cross_root_unscoped_isolation(self):
        from charter import inflight

        root_a = "root-iso-a"
        root_b = "root-iso-b"
        write_test_rollout(self.sessions_dir, root_a, timestamp="2026-08-19T10:00:00Z")
        write_test_rollout(self.sessions_dir, root_b, timestamp="2026-08-19T10:00:00Z")

        # Real Hook path: start(agent, session_id=sid) without parent_id
        inflight.start("WorkerA", session_id=root_a)
        inflight.start("WorkerB", session_id=root_b)

        tree_a = subagent.build_subagent_tree(root_a, sessions_dir=self.sessions_dir)
        names_a = [n.name for n in tree_a.nodes]
        self.assertIn("WorkerA", names_a)
        self.assertNotIn("WorkerB", names_a)


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
    def test_causal_source_ordering_with_identical_timestamps(self):
        from charter import observations

        root = "root-causal-test"
        child = "child-causal-test"
        t_same = "2026-08-19T10:00:00Z"

        # Spawn, start, and return all with the exact same timestamp
        spawn_ev = {
            "type": "event_msg",
            "timestamp": t_same,
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"CAUSAL-1\"}\n```",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "CausalWorker"}],
                },
            },
        }
        ret_ev = {
            "type": "event_msg",
            "timestamp": t_same,
            "payload": {"type": "task_complete", "summary": "Done"},
        }

        write_test_rollout(self.sessions_dir, root, timestamp=t_same, collab_events=[spawn_ev])
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="CausalWorker", timestamp=t_same, collab_events=[ret_ev])

        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
        kinds = [e.kind for e in snap.events]

        # dispatch_sent must precede actor_started which must precede actor_returned
        self.assertIn("dispatch_sent", kinds)
        self.assertIn("actor_started", kinds)
        self.assertIn("actor_returned", kinds)
        disp_idx = kinds.index("dispatch_sent")
        start_idx = kinds.index("actor_started")
        ret_idx = kinds.index("actor_returned")

        self.assertLess(disp_idx, start_idx)
        self.assertLess(start_idx, ret_idx)

    def test_inflight_observation_events_are_present(self):
        from charter import observations, inflight, config

        root = "root-inflight-obs"
        state_tmp = self.tmp / "state"
        with mock.patch.object(config, "STATE_DIR", state_tmp):
            write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z")
            inflight.start("ActiveWorker", session_id=root)

            snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
            inflight_events = [e for e in snap.events if e.evidence and e.evidence[0].source == "inflight"]
            self.assertEqual(len(inflight_events), 1)
            self.assertEqual(inflight_events[0].actor_name, "ActiveWorker")
            self.assertTrue(inflight_events[0].id)


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

    def test_strict_schema_types(self):
        from charter import observations

        # Boolean schema True must be rejected
        warns_bool: list[str] = []
        d_bool = observations.parse_workflow_declarations("```charter-observe\n{\"schema\": true, \"event\": \"dispatch\"}\n```", warnings=warns_bool)
        self.assertEqual(d_bool, ())
        self.assertTrue(len(warns_bool) > 0)

        # Float schema 1.0 must be rejected
        warns_float: list[str] = []
        d_float = observations.parse_workflow_declarations("```charter-observe\n{\"schema\": 1.0, \"event\": \"dispatch\"}\n```", warnings=warns_float)
        self.assertEqual(d_float, ())
        self.assertTrue(len(warns_float) > 0)

    def test_unhashable_and_invalid_field_types(self):
        from charter import observations

        # List as event (must not crash with unhashable type)
        warns_list: list[str] = []
        d_list = observations.parse_workflow_declarations("```charter-observe\n{\"schema\": 1, \"event\": [\"dispatch\"]}\n```", warnings=warns_list)
        self.assertEqual(d_list, ())
        self.assertTrue(len(warns_list) > 0)

        # Dict as work_id (must be rejected, not coerced to str)
        warns_dict: list[str] = []
        d_dict = observations.parse_workflow_declarations("```charter-observe\n{\"schema\": 1, \"event\": \"dispatch\", \"work_id\": {\"bad\": \"type\"}}\n```", warnings=warns_dict)
        self.assertEqual(d_dict, ())
        self.assertTrue(len(warns_dict) > 0)

    def test_four_backticks_and_unanchored_fence_rejection(self):
        from charter import observations

        # 4 backticks outer wrapper (code block example)
        four_ticks = (
            "````markdown\n"
            "```charter-observe\n"
            "{\"schema\": 1, \"event\": \"dispatch\", \"work_id\": \"W1\"}\n"
            "```\n"
            "````"
        )
        self.assertEqual(observations.parse_workflow_declarations(four_ticks), ())
    def test_event_specific_schemas(self):
        from charter import observations

        # 1. Dispatch forbids relation
        w1: list[str] = []
        d1 = observations.parse_workflow_declarations("```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"relation\":\"peer\"}\n```", warnings=w1)
        self.assertEqual(d1, ())
        self.assertTrue(any("forbids field 'relation'" in msg for msg in w1))

        # 2. Intake requires non-empty work_id
        w2: list[str] = []
        d2 = observations.parse_workflow_declarations("```charter-observe\n{\"schema\":1,\"event\":\"intake\"}\n```", warnings=w2)
        self.assertEqual(d2, ())
        self.assertTrue(any("Missing or empty work_id" in msg for msg in w2))

        # 3. Intake forbids relation fields
        w3: list[str] = []
        d3 = observations.parse_workflow_declarations("```charter-observe\n{\"schema\":1,\"event\":\"intake\",\"work_id\":\"W1\",\"relation\":\"peer\"}\n```", warnings=w3)
        self.assertEqual(d3, ())
        self.assertTrue(any("forbids field 'relation'" in msg for msg in w3))

        # 4. Resolve requires non-empty work_id
        w4: list[str] = []
        d4 = observations.parse_workflow_declarations("```charter-observe\n{\"schema\":1,\"event\":\"resolve\",\"work_id\":\"   \"}\n```", warnings=w4)
        self.assertEqual(d4, ())
        self.assertTrue(any("Missing or empty work_id" in msg for msg in w4))

        # 5. Relation requires relation and related_actor_id
        w5: list[str] = []
        d5 = observations.parse_workflow_declarations("```charter-observe\n{\"schema\":1,\"event\":\"relation\",\"actor_id\":\"A\"}\n```", warnings=w5)
        self.assertEqual(d5, ())
        self.assertTrue(any("requires valid relation" in msg for msg in w5))

        w6: list[str] = []
        d6 = observations.parse_workflow_declarations("```charter-observe\n{\"schema\":1,\"event\":\"relation\",\"relation\":\"peer\",\"actor_id\":\"A\"}\n```", warnings=w6)
        self.assertEqual(d6, ())
        self.assertTrue(any("requires non-empty related_actor_id" in msg for msg in w6))

        # 6. Relation forbids work_id
        w7: list[str] = []
        d7 = observations.parse_workflow_declarations("```charter-observe\n{\"schema\":1,\"event\":\"relation\",\"relation\":\"peer\",\"related_actor_id\":\"B\",\"work_id\":\"W1\"}\n```", warnings=w7)
        self.assertEqual(d7, ())
        self.assertTrue(any("forbids field 'work_id'" in msg for msg in w7))

    def test_causal_full_lifecycle_with_identical_timestamps(self):
        from charter import observations, workflow_view

        root = "root-causal-full"
        child = "child-causal-full"
        ts = "2026-08-19T10:00:00Z"

        spawn_ev = {
            "type": "event_msg",
            "timestamp": ts,
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"FULL-1\",\"title\":\"Task Full\"}\n```",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Gauss"}],
                },
            },
        }

        task_complete_ev = {
            "type": "event_msg",
            "timestamp": ts,
            "payload": {
                "type": "task_complete",
                "summary": "Finished work",
            },
        }

        intake_msg = {
            "type": "event_msg",
            "timestamp": ts,
            "payload": {
                "type": "user_message",
                "message": "```charter-observe\n{\"schema\":1,\"event\":\"intake\",\"work_id\":\"FULL-1\"}\n```\nAccepted return.",
            },
        }

        resolve_msg = {
            "type": "event_msg",
            "timestamp": ts,
            "payload": {
                "type": "user_message",
                "message": "```charter-observe\n{\"schema\":1,\"event\":\"resolve\",\"work_id\":\"FULL-1\"}\n```\nResolution approved.",
            },
        }

        tmp = Path(tempfile.mkdtemp(prefix="test-causal-full-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        sdir = tmp / "sessions"

        write_test_rollout(sdir, root, timestamp=ts, collab_events=[spawn_ev])
        write_test_rollout(sdir, child, parent_id=root, nickname="Gauss", timestamp=ts, collab_events=[task_complete_ev, intake_msg, resolve_msg])

        snap = observations.collect_observation_snapshot(root, sessions_dir=sdir)
        proj = workflow_view.project_workflow(snap)

        # Event ordering must be: dispatch -> return -> intake -> resolve
        kinds = [(e.kind, e.attributes.get("declaration", {}).get("event")) for e in snap.events]
        # Must resolve to phase resolved without getting stuck at unbound!
        self.assertEqual(len(proj.work_items), 1)
        self.assertEqual(proj.work_items[0].phase, "resolved")
        self.assertEqual(proj.unbound_events, ())
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
    def test_distinct_relation_declarations_not_deduplicated(self):
        from charter import observations, workflow_view

        root = "root-multi-rel"
        r1_text = "```charter-observe\n{\"schema\":1,\"event\":\"relation\",\"relation\":\"reports_to\",\"related_actor_id\":\"Boss\"}\n```"
        r2_text = "```charter-observe\n{\"schema\":1,\"event\":\"relation\",\"relation\":\"owner\",\"related_actor_id\":\"TechLead\"}\n```"
        r3_text = "```charter-observe\n{\"schema\":1,\"event\":\"relation\",\"relation\":\"peer\",\"related_actor_id\":\"Reviewer\"}\n```"

        ev1 = {"type": "event_msg", "payload": {"type": "user_message", "message": r1_text}}
        ev2 = {"type": "event_msg", "payload": {"type": "user_message", "message": r2_text}}
        ev3 = {"type": "event_msg", "payload": {"type": "user_message", "message": r3_text}}

        tmp = Path(tempfile.mkdtemp(prefix="test-obs-rel-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        sdir = tmp / "sessions"

        write_test_rollout(sdir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[ev1, ev2, ev3])
        snap = observations.collect_observation_snapshot(root, sessions_dir=sdir)
        decl_events = [e for e in snap.events if e.kind == "workflow_declared"]

        # All 3 distinct relations must be recorded as 3 separate events!
        self.assertEqual(len(decl_events), 3)
        relations_recorded = [e.attributes["declaration"]["relation"] for e in decl_events]
        self.assertEqual(relations_recorded, ["reports_to", "owner", "peer"])

    def test_forbidden_fields_with_null_rejected(self):
        from charter import observations

        # 1. Dispatch with relation: null must be rejected
        w1: list[str] = []
        d1 = observations.parse_workflow_declarations("```charter-observe\n{\"schema\": 1, \"event\": \"dispatch\", \"relation\": null}\n```", warnings=w1)
        self.assertEqual(d1, ())
        self.assertTrue(any("forbids field 'relation'" in w for w in w1))

        # 2. Relation with work_id: null must be rejected
        w2: list[str] = []
        d2 = observations.parse_workflow_declarations("```charter-observe\n{\"schema\": 1, \"event\": \"relation\", \"relation\": \"peer\", \"related_actor_id\": \"B\", \"work_id\": null}\n```", warnings=w2)
        self.assertEqual(d2, ())
        self.assertTrue(any("forbids field 'work_id'" in w for w in w2))

        # 3. Intake with title: null must be rejected
        w3: list[str] = []
        d3 = observations.parse_workflow_declarations("```charter-observe\n{\"schema\": 1, \"event\": \"intake\", \"work_id\": \"W1\", \"title\": null}\n```", warnings=w3)
        self.assertEqual(d3, ())
        self.assertTrue(any("forbids field 'title'" in w for w in w3))

    def test_source_local_event_id_determinism_and_immutability(self):
        from charter import observations

        root = "root-id-stability"
        spawn_ev = {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"STAB-1\",\"title\":\"Task Stability\"}\n```",
                    "receiver_agents": [{"thread_id": "child-stab-1", "agent_nickname": "Gauss"}],
                },
            },
        }
        tmp = Path(tempfile.mkdtemp(prefix="test-id-stab-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        sdir = tmp / "sessions"

        write_test_rollout(sdir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_ev])
        snap1 = observations.collect_observation_snapshot(root, sessions_dir=sdir)
        ids_before = [e.id for e in snap1.events]
        self.assertTrue(len(ids_before) > 0)
        # Sub-events on the same line (spawn, dispatch, declaration) must have distinct SHA-256 IDs
        self.assertEqual(len(ids_before), len(set(ids_before)))

        # Append unrelated child session and later event
        write_test_rollout(sdir, "child-stab-1", parent_id=root, nickname="Gauss", timestamp="2026-08-19T10:01:00Z", collab_events=[{
            "type": "event_msg",
            "payload": {"type": "task_complete", "summary": "done"},
        }])
        snap2 = observations.collect_observation_snapshot(root, sessions_dir=sdir)
        ids_after = [e.id for e in snap2.events if e.session_id == root]

        # All pre-existing event IDs in root session MUST remain 100% identical!
        self.assertEqual(ids_before, ids_after)

    def test_negative_causal_order_intra_source_preservation(self):
        from charter import observations, workflow_view

        root = "root-neg-order"
        child = "child-neg-order"
        ts = "2026-08-19T10:00:00Z"

        spawn_ev = {
            "type": "event_msg",
            "timestamp": ts,
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"NEG-1\",\"title\":\"Task Negative\"}\n```",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Gauss"}],
                },
            },
        }

        task_complete_ev = {
            "type": "event_msg",
            "timestamp": ts,
            "payload": {"type": "task_complete", "summary": "Finished work"},
        }

        # Backwards declarations in coordinator: resolve (line 2) before intake (line 3)
        resolve_msg = {
            "type": "event_msg",
            "timestamp": ts,
            "payload": {
                "type": "user_message",
                "message": "```charter-observe\n{\"schema\":1,\"event\":\"resolve\",\"work_id\":\"NEG-1\"}\n```\nPremature resolve.",
            },
        }
        intake_msg = {
            "type": "event_msg",
            "timestamp": ts,
            "payload": {
                "type": "user_message",
                "message": "```charter-observe\n{\"schema\":1,\"event\":\"intake\",\"work_id\":\"NEG-1\"}\n```\nIntake after resolve.",
            },
        }

        tmp = Path(tempfile.mkdtemp(prefix="test-causal-neg-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        sdir = tmp / "sessions"

        write_test_rollout(sdir, root, timestamp=ts, collab_events=[spawn_ev])
        write_test_rollout(sdir, child, parent_id=root, nickname="Gauss", timestamp=ts, collab_events=[task_complete_ev, resolve_msg, intake_msg])

        snap = observations.collect_observation_snapshot(root, sessions_dir=sdir)
        proj = workflow_view.project_workflow(snap)

        # Because resolve was written before intake in source order, resolve was rejected as invalid_phase_for_resolve!
        # Then intake moved phase to intaken. So work item remains intaken, and resolve is in unbound_events.
        self.assertEqual(len(proj.work_items), 1)
        self.assertEqual(proj.work_items[0].phase, "intaken")
        self.assertEqual(len(proj.unbound_events), 1)
        self.assertIn("invalid_phase_for_resolve", proj.unbound_events[0].attributes.get("unbound_reason", ""))

if __name__ == "__main__":
    unittest.main()
