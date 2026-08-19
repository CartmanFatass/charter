"""Tests for deterministic workflow projections, obligation management, and safe rendering."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from charter import observations, tui, workflow_view
from tests.test_subagent import write_test_rollout


class TestWorkflowProjectionReducer(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test-wf-view-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.sessions_dir = self.tmp / "sessions"

    def test_spawn_activity_task_complete_lifecycle(self):
        root = "root-life-01"
        child = "child-gauss-01"
        now = datetime(2026, 8, 19, 10, 0, 0, tzinfo=timezone.utc)

        spawn_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"SCDMP-B2\",\"title\":\"B2 review\",\"direction\":\"SCDMP\",\"actor_role\":\"EM\",\"owner_role\":\"ROOT\"}\n```\nReview task.",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Gauss"}],
                },
            },
        }

        ret_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:05:00Z",
            "payload": {"type": "task_complete", "summary": "Review done."},
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_ev])
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="Gauss", timestamp="2026-08-19T10:00:10Z", collab_events=[ret_ev])

        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
        proj = workflow_view.project_workflow(snap)

        self.assertEqual(len(proj.work_items), 1)
        wi = proj.work_items[0]
        self.assertEqual(wi.external_id, "SCDMP-B2")
        self.assertEqual(wi.actor_name, "Gauss")
        self.assertEqual(wi.actor_role, "EM")
        self.assertEqual(wi.direction, "SCDMP")
        self.assertEqual(wi.phase, "returned")
        self.assertEqual(wi.runtime_state, "stopped")
        self.assertTrue(wi.return_observed)

        # Obligations: 1 intake_required, 0 return_expected
        self.assertEqual(len(wi.obligations), 1)
        self.assertEqual(wi.obligations[0].kind, "intake_required")
        self.assertEqual(len(proj.open_obligations), 1)
        self.assertEqual(proj.open_obligations[0].kind, "intake_required")

    def test_spawn_activity_stopped_without_return(self):
        root = "root-stop-01"
        child = "child-euler-01"

        spawn_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T11:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Euler"}],
                },
            },
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T11:00:00Z", collab_events=[spawn_ev])
        # Child has no task_complete, but Hook trace records subagent_stop
        from charter import trace, config
        state_tmp = self.tmp / "state"
        with unittest.mock.patch.object(config, "PERSONA_STATE_DIR", state_tmp):
            trace.record("subagent_start", child, agent="Euler", ts="2026-08-19T11:01:00Z")
            trace.record("subagent_stop", child, agent="Euler", ts="2026-08-19T11:02:00Z")
            snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
            proj = workflow_view.project_workflow(snap)

            self.assertEqual(len(proj.work_items), 1)
            wi = proj.work_items[0]
            self.assertEqual(wi.actor_name, "Euler")
            self.assertEqual(wi.runtime_state, "stopped")
            self.assertIn(wi.phase, ("active", "dispatched"))
            self.assertFalse(wi.return_observed)

            # return_expected remains open!
            self.assertEqual(len(wi.obligations), 1)
            self.assertEqual(wi.obligations[0].kind, "return_expected")

    def test_typed_incident_appears_in_incidents_without_changing_phase(self):
        root = "root-incident-01"
        child = "child-inc-01"

        spawn_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "WorkerInc"}],
                    "agents_states": {child: {"error": "OOM occurred in worker"}},
                },
            },
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_ev])
        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
        proj = workflow_view.project_workflow(snap)

        self.assertEqual(len(proj.incidents), 1)
        self.assertIn("OOM occurred", proj.incidents[0].summary)
        self.assertEqual(len(proj.work_items), 1)
        # Phase remains dispatched/active, not failed
        self.assertIn(proj.work_items[0].phase, ("dispatched", "active"))

    def test_free_text_blocked_or_error_changes_nothing(self):
        root = "root-text-01"
        child = "child-text-01"

        spawn_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Noether"}],
                },
            },
        }

        msg_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:02:00Z",
            "payload": {"type": "agent_message", "message": "I am blocked by timeout, task done, error occurred."},
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_ev])
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="Noether", timestamp="2026-08-19T10:00:10Z", collab_events=[msg_ev])

        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
        proj = workflow_view.project_workflow(snap)

        self.assertEqual(len(proj.incidents), 0)
        self.assertEqual(proj.work_items[0].phase, "active")
        self.assertFalse(proj.work_items[0].return_observed)

    def test_coordinator_intake_and_ordered_resolve(self):
        root = "root-intake-resolve"
        child = "child-ir-01"

        spawn_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"W-100\"}\n```\nDo work.",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "WorkerIR"}],
                },
            },
        }

        ret_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:05:00Z",
            "payload": {"type": "task_complete", "summary": "Finished."},
        }

        intake_msg = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:06:00Z",
            "payload": {
                "type": "user_message",
                "message": "```charter-observe\n{\"schema\":1,\"event\":\"intake\",\"work_id\":\"W-100\"}\n```\nIntake acknowledged.",
            },
        }

        resolve_msg = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:07:00Z",
            "payload": {
                "type": "user_message",
                "message": "```charter-observe\n{\"schema\":1,\"event\":\"resolve\",\"work_id\":\"W-100\"}\n```\nAll criteria met.",
            },
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_ev, intake_msg, resolve_msg])
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="WorkerIR", timestamp="2026-08-19T10:00:10Z", collab_events=[ret_ev])

        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
        proj = workflow_view.project_workflow(snap)

        wi = proj.work_items[0]
        self.assertEqual(wi.phase, "resolved")
        self.assertEqual(len(wi.obligations), 0)
        self.assertEqual(len(proj.open_obligations), 0)

    def test_child_authored_intake_rejected(self):
        root = "root-child-auth"
        child = "child-self-auth"

        spawn_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"W-200\"}\n```\nDo work.",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "WorkerSelf"}],
                },
            },
        }

        ret_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:05:00Z",
            "payload": {"type": "task_complete", "summary": "Finished."},
        }

        # Child tries to self-intake in an agent_message
        child_intake_msg = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:06:00Z",
            "payload": {
                "type": "agent_message",
                "message": "```charter-observe\n{\"schema\":1,\"event\":\"intake\",\"work_id\":\"W-200\"}\n```\nI self-intake.",
            },
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_ev])
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="WorkerSelf", timestamp="2026-08-19T10:00:10Z", collab_events=[ret_ev, child_intake_msg])

        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
        proj = workflow_view.project_workflow(snap)

        wi = proj.work_items[0]
        # Child intake rejected -> phase remains returned, intake_required remains open!
        self.assertEqual(wi.phase, "returned")
        self.assertEqual(len(wi.obligations), 1)
        self.assertEqual(wi.obligations[0].kind, "intake_required")
        self.assertTrue(len(proj.unbound_events) > 0)

    def test_resolve_before_intake_rejected(self):
        root = "root-resolve-early"
        child = "child-re-01"

        spawn_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"W-300\"}\n```\nDo work.",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "WorkerRE"}],
                },
            },
        }

        # Coordinator attempts to resolve immediately without intake
        resolve_early = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:02:00Z",
            "payload": {
                "type": "user_message",
                "message": "```charter-observe\n{\"schema\":1,\"event\":\"resolve\",\"work_id\":\"W-300\"}\n```\nEarly resolve.",
            },
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_ev, resolve_early])
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="WorkerRE", timestamp="2026-08-19T10:00:10Z")

        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
        proj = workflow_view.project_workflow(snap)

        wi = proj.work_items[0]
        self.assertNotEqual(wi.phase, "resolved")
        self.assertTrue(len(proj.unbound_events) > 0)

    def test_declared_peer_coexists_with_spawn_hierarchy(self):
        root = "root-peer-01"
        gauss = "child-gauss-02"
        curie = "child-curie-02"

        spawn_gauss = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "receiver_agents": [{"thread_id": gauss, "agent_nickname": "Gauss"}],
                },
            },
        }
        spawn_curie = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:10Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "receiver_agents": [{"thread_id": curie, "agent_nickname": "Curie"}],
                },
            },
        }
        peer_decl = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:01:00Z",
            "payload": {
                "type": "user_message",
                "message": f"```charter-observe\n{{\"schema\":1,\"event\":\"relation\",\"actor_id\":\"{gauss}\",\"relation\":\"peer\",\"related_actor_id\":\"{curie}\"}}\n```",
            },
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_gauss, spawn_curie, peer_decl])
        write_test_rollout(self.sessions_dir, gauss, parent_id=root, nickname="Gauss", timestamp="2026-08-19T10:00:15Z")
        write_test_rollout(self.sessions_dir, curie, parent_id=root, nickname="Curie", timestamp="2026-08-19T10:00:20Z")

        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
        proj = workflow_view.project_workflow(snap)

        spawn_rels = [r for r in proj.relations if r.kind == "spawn_parent"]
        peer_rels = [r for r in proj.relations if r.kind == "peer"]

        self.assertEqual(len(spawn_rels), 2)
        self.assertEqual(len(peer_rels), 1)
        self.assertTrue(peer_rels[0].declared)
        self.assertEqual(peer_rels[0].source_actor_id, gauss)
        self.assertEqual(peer_rels[0].target_actor_id, curie)
    def test_root_cannot_bypass_nested_coordinator_intake_authority(self):
        root = "root-multi-tier"
        coord = "coord-tier-a"
        worker = "worker-tier-b"

        # 1. Root spawns Coordinator
        spawn_coord = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "receiver_agents": [{"thread_id": coord, "agent_nickname": "CoordA"}],
                },
            },
        }
        # 2. Coordinator spawns Worker for work NESTED-1
        spawn_worker = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:01:00Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"NESTED-1\"}\n```\nExecute nested slice.",
                    "receiver_agents": [{"thread_id": worker, "agent_nickname": "WorkerB"}],
                },
            },
        }
        # 3. Worker returns task_complete
        ret_worker = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:05:00Z",
            "payload": {"type": "task_complete", "summary": "Nested work complete"},
        }
        # 4. Root attempts unauthorized intake
        root_unauth_intake = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:06:00Z",
            "payload": {
                "type": "user_message",
                "message": "```charter-observe\n{\"schema\":1,\"event\":\"intake\",\"work_id\":\"NESTED-1\"}\n```\nRoot attempts bypass intake.",
            },
        }
        # 5. Coordinator performs authorized intake
        coord_auth_intake = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:07:00Z",
            "payload": {
                "type": "user_message",
                "message": "```charter-observe\n{\"schema\":1,\"event\":\"intake\",\"work_id\":\"NESTED-1\"}\n```\nCoordA intakes work.",
            },
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_coord, root_unauth_intake])
        write_test_rollout(self.sessions_dir, coord, parent_id=root, nickname="CoordA", timestamp="2026-08-19T10:00:10Z", collab_events=[spawn_worker])
        write_test_rollout(self.sessions_dir, worker, parent_id=coord, nickname="WorkerB", timestamp="2026-08-19T10:01:05Z", collab_events=[ret_worker, coord_auth_intake])

        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
        proj = workflow_view.project_workflow(snap)

        # Find NESTED-1 work item
        nested_wi = next(w for w in proj.work_items if w.external_id == "NESTED-1")
        self.assertEqual(nested_wi.coordinator_actor_id, coord)
        # Proves root's attempt was rejected (in unbound_events) and only coord's intake was accepted
        self.assertEqual(nested_wi.phase, "intaken")
        unbound_reasons = [e.attributes.get("unbound_reason", "") for e in proj.unbound_events]
        self.assertTrue(any("author_not_dispatching_coordinator" in r for r in unbound_reasons))

    def test_intake_before_return_rejected(self):
        root = "root-intake-early"
        child = "child-ie-01"

        spawn_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"EARLY-1\"}\n```\nExecute slice.",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "WorkerEarly"}],
                },
            },
        }
        intake_early = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:02:00Z",
            "payload": {
                "type": "user_message",
                "message": "```charter-observe\n{\"schema\":1,\"event\":\"intake\",\"work_id\":\"EARLY-1\"}\n```\nEarly intake before task_complete.",
            },
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_ev, intake_early])
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="WorkerEarly", timestamp="2026-08-19T10:00:10Z")

        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
        proj = workflow_view.project_workflow(snap)

        wi = proj.work_items[0]
        # Intake must be rejected because phase is dispatched/active (not returned)
        self.assertIn(wi.phase, ("dispatched", "active"))
        self.assertNotEqual(wi.phase, "intaken")
        unbound_reasons = [e.attributes.get("unbound_reason", "") for e in proj.unbound_events]
        self.assertTrue(any("invalid_phase_for_intake" in r for r in unbound_reasons))

    def test_reports_to_and_owner_relations_and_explain(self):
        root = "root-rel-test"
        lead = "lead-agent-01"
        dev = "dev-agent-02"

        spawn_lead = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "receiver_agents": [{"thread_id": lead, "agent_nickname": "Lead"}],
                },
            },
        }
        spawn_dev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:10Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "receiver_agents": [{"thread_id": dev, "agent_nickname": "Dev"}],
                },
            },
        }
        rel_reports = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:01:00Z",
            "payload": {
                "type": "user_message",
                "message": f"```charter-observe\n{{\"schema\":1,\"event\":\"relation\",\"actor_id\":\"{dev}\",\"relation\":\"reports_to\",\"related_actor_id\":\"{lead}\"}}\n```",
            },
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_lead, spawn_dev, rel_reports])
        write_test_rollout(self.sessions_dir, lead, parent_id=root, nickname="Lead", timestamp="2026-08-19T10:00:15Z")
        write_test_rollout(self.sessions_dir, dev, parent_id=root, nickname="Dev", timestamp="2026-08-19T10:00:20Z")

        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
        proj = workflow_view.project_workflow(snap)

        reports_rels = [r for r in proj.relations if r.kind == "reports_to"]
        self.assertEqual(len(reports_rels), 1)
        rel = reports_rels[0]
        self.assertEqual(rel.source_actor_id, dev)
        self.assertEqual(rel.target_actor_id, lead)

        # Explain relation
        exp = workflow_view.explain_projection(proj, rel.id)
        self.assertIsNotNone(exp)
        self.assertEqual(exp["type"], "relation")
        self.assertEqual(exp["kind"], "reports_to")
        self.assertEqual(exp["source"], dev)
        self.assertEqual(exp["target"], lead)

    def test_actor_view_side_by_side_and_flags(self):
        root = "root-actor-view"
        gauss = "child-gauss-act"
        curie = "child-curie-act"

        spawn_gauss = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"W-G\",\"direction\":\"SCDMP\",\"actor_role\":\"EM\"}\n```",
                    "receiver_agents": [{"thread_id": gauss, "agent_nickname": "Gauss"}],
                },
            },
        }
        spawn_curie = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:10Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"W-C\",\"direction\":\"SCDMP\",\"actor_role\":\"CM\"}\n```",
                    "receiver_agents": [{"thread_id": curie, "agent_nickname": "Curie"}],
                },
            },
        }
        peer_decl = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:01:00Z",
            "payload": {
                "type": "user_message",
                "message": f"```charter-observe\n{{\"schema\":1,\"event\":\"relation\",\"actor_id\":\"{gauss}\",\"relation\":\"peer\",\"related_actor_id\":\"{curie}\"}}\n```",
            },
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_gauss, spawn_curie, peer_decl])
        write_test_rollout(self.sessions_dir, gauss, parent_id=root, nickname="Gauss", timestamp="2026-08-19T10:00:15Z")
        write_test_rollout(self.sessions_dir, curie, parent_id=root, nickname="Curie", timestamp="2026-08-19T10:00:20Z")

        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
        proj = workflow_view.project_workflow(snap)

        # 1. Side by side (default)
        lines_sbs = workflow_view.render_actor_view(proj, width=120)
        text_sbs = "\n".join(lines_sbs)
        self.assertIn("DECLARED WORKFLOW", text_sbs)
        self.assertIn("RUNTIME TOPOLOGY", text_sbs)
        self.assertIn("Gauss ── peer ── Curie", text_sbs)

        # 2. Runtime tree only
        lines_rt = workflow_view.render_actor_view(proj, width=120, runtime_tree_only=True)
        text_rt = "\n".join(lines_rt)
        self.assertNotIn("DECLARED WORKFLOW", text_rt)
        self.assertIn("RUNTIME TOPOLOGY", text_rt)

        # 3. Declared relations only
        lines_decl = workflow_view.render_actor_view(proj, width=120, declared_relations_only=True)
        text_decl = "\n".join(lines_decl)
        self.assertIn("DECLARED WORKFLOW", text_decl)
        self.assertNotIn("RUNTIME TOPOLOGY", text_decl)

    def test_timeline_work_and_actor_filtering(self):
        root = "root-timeline-filter"
        child1 = "child-tl-gauss"
        child2 = "child-tl-euler"

        spawn_g = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"WORK-G\"}\n```",
                    "receiver_agents": [{"thread_id": child1, "agent_nickname": "Gauss"}],
                },
            },
        }
        spawn_e = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:10Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"WORK-E\"}\n```",
                    "receiver_agents": [{"thread_id": child2, "agent_nickname": "Euler"}],
                },
            },
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_g, spawn_e])
        write_test_rollout(self.sessions_dir, child1, parent_id=root, nickname="Gauss", timestamp="2026-08-19T10:00:15Z")
        write_test_rollout(self.sessions_dir, child2, parent_id=root, nickname="Euler", timestamp="2026-08-19T10:00:20Z")

        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
        proj = workflow_view.project_workflow(snap)

        # Filter by work WORK-G
        lines_g = workflow_view.render_observation_timeline(snap, proj, width=120, work_filter="WORK-G")
        text_g = "\n".join(lines_g)
        self.assertIn("Gauss", text_g)
        self.assertNotIn("Euler", text_g)

        # Filter by actor Euler
        lines_e = workflow_view.render_observation_timeline(snap, proj, width=120, actor_filter="Euler")
        text_e = "\n".join(lines_e)
        self.assertIn("Euler", text_e)
        self.assertNotIn("Gauss", text_e)


class TestWorkflowRenderersAndExplain(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test-wf-render-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.sessions_dir = self.tmp / "sessions"

    def test_render_position_table_widths(self):
        root = "root-render-pos"
        child = "child-render-gauss"

        spawn_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"SCDMP-B2\",\"title\":\"B2 review\",\"direction\":\"SCDMP\",\"actor_role\":\"EM\"}\n```",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Gauss"}],
                },
            },
        }
        ret_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:05:00Z",
            "payload": {"type": "task_complete", "summary": "Done"},
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_ev])
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="Gauss", timestamp="2026-08-19T10:00:10Z", collab_events=[ret_ev])

        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
        proj = workflow_view.project_workflow(snap)

        now_dt = datetime(2026, 8, 19, 10, 7, 0, tzinfo=timezone.utc)
        for w in (60, 80, 116, 160):
            lines = workflow_view.render_position_table(proj, width=w, now_dt=now_dt)
            for line in lines:
                self.assertLessEqual(tui.width(line), w)

        # Returned work must not say completed
        full_text = "\n".join(workflow_view.render_position_table(proj, width=120, now_dt=now_dt))
        self.assertNotIn("completed", full_text)
        self.assertIn("returned", full_text)

    def test_render_obligations_table(self):
        root = "root-render-obl"
        child = "child-render-obl"

        spawn_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"SGSP-B3\"}\n```",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Noether"}],
                },
            },
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_ev])
        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
        proj = workflow_view.project_workflow(snap)

        now_dt = datetime(2026, 8, 19, 10, 8, 0, tzinfo=timezone.utc)
        lines = workflow_view.render_obligations(proj, width=100, now_dt=now_dt)
        text = "\n".join(lines)
        self.assertIn("Noether", text)
        self.assertIn("return to", text)
        self.assertIn("[DER] spawn_agent", text)

    def test_explain_projection(self):
        root = "root-explain"
        child = "child-exp-01"

        spawn_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"EX-1\",\"direction\":\"SCDMP\",\"actor_role\":\"EM\"}\n```",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Gauss"}],
                },
            },
        }

        fpath = write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_ev])
        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
        proj = workflow_view.project_workflow(snap)

        exp = workflow_view.explain_projection(proj, "EX-1")
        self.assertIsNotNone(exp)
        self.assertEqual(exp["type"], "work_item")
        self.assertEqual(exp["external_id"], "EX-1")
        self.assertEqual(exp["actor_role"], "EM")
        self.assertEqual(exp["direction"], "SCDMP")
        self.assertTrue(len(exp["evidence"]) > 0)
        self.assertEqual(exp["evidence"][0]["file_path"], str(fpath))



class TestStatuslineWorkflowIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test-statusline-wf-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.sessions_dir = self.tmp / "sessions"

        from charter import statusline
        self.env = unittest.mock.patch.dict(os.environ, {"CODEX_SESSIONS_PATH": str(self.sessions_dir)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_statusline_workflow_panel_and_fallback(self):
        from charter import statusline

        root = "root-statusline-wf"
        child = "child-statusline-wf"
        spawn_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"WF-STAT\"}\n```",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Gauss"}],
                },
            },
        }

        # 1. With work dispatch -> header is workflow
        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_ev])
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="Gauss", timestamp="2026-08-19T10:00:10Z")

        out = statusline.render({"session_id": root})
        self.assertIn("workflow", out)
        self.assertIn("WF-STAT", out)

        # 2. Fallback when only session_meta child exists without dispatch event
        shutil.rmtree(self.sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        write_test_rollout(self.sessions_dir, "root-raw", timestamp="2026-08-19T10:00:00Z")
        write_test_rollout(self.sessions_dir, "child-raw", parent_id="root-raw", nickname="RawSubagent", timestamp="2026-08-19T10:00:10Z")

        out_fallback = statusline.render({"session_id": "root-raw"})
        self.assertIn("subagents", out_fallback)
        self.assertIn("RawSubagent", out_fallback)

    def test_statusline_returned_item_shown_as_intake_not_completed(self):
        from charter import statusline

        root = "root-ret-status"
        child = "child-ret-status"
        spawn_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"INTAKE-ONLY\"}\n```",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Gauss"}],
                },
            },
        }
        ret_ev = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:05:00Z",
            "payload": {"type": "task_complete", "summary": "Finished review."},
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_ev])
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="Gauss", timestamp="2026-08-19T10:00:10Z", collab_events=[ret_ev])

        out = statusline.render({"session_id": root})
        self.assertIn("intake", out)
        self.assertNotIn("completed", out)

    def test_work_timeline_multi_assignment_and_incident_filtering(self):
        root = "root-multi-assign"
        child_a = "child-work-a"
        child_b = "child-work-b"

        spawn_a = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:00Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"WORK-AAA\",\"title\":\"Task A\"}\n```",
                    "receiver_agents": [{"thread_id": child_a, "agent_nickname": "Gauss"}],
                },
            },
        }
        spawn_b = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:01:00Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"WORK-BBB\",\"title\":\"Task B\"}\n```",
                    "receiver_agents": [{"thread_id": child_b, "agent_nickname": "Gauss"}],
                },
            },
        }
        tool_a = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:10Z",
            "payload": {
                "type": "item_completed",
                "item": {"type": "custom_tool_call", "name": "tool_a_execute"},
            },
        }
        inc_a = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:15Z",
            "payload": {
                "type": "item_completed",
                "item": {"type": "CollabAgentToolCall", "tool": "wait", "agents_states": {child_a: {"error": "Disk full"}}},
            },
        }
        ret_a = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:20Z",
            "payload": {"type": "task_complete", "summary": "Finished A"},
        }

        tool_b = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:01:10Z",
            "payload": {
                "type": "item_completed",
                "item": {"type": "custom_tool_call", "name": "tool_b_execute"},
            },
        }
        ret_b = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:01:20Z",
            "payload": {"type": "task_complete", "summary": "Finished B"},
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_a, spawn_b])
        write_test_rollout(self.sessions_dir, child_a, parent_id=root, nickname="Gauss", timestamp="2026-08-19T10:00:05Z", collab_events=[tool_a, inc_a, ret_a])
        write_test_rollout(self.sessions_dir, child_b, parent_id=root, nickname="Gauss", timestamp="2026-08-19T10:01:05Z", collab_events=[tool_b, ret_b])
        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir, include_tool_calls=True)
        proj = workflow_view.project_workflow(snap)

        # Filter for WORK-AAA
        tl_a = workflow_view.filter_timeline_events(snap, proj, work_filter="WORK-AAA")
        summaries_a = [e.summary for e in tl_a]
        self.assertTrue(any("WORK-AAA" in s or "Task A" in s for s in summaries_a))
        self.assertTrue(any("tool_a_execute" in s for s in summaries_a))
        self.assertTrue(any("Disk full" in s for s in summaries_a))
        # Must have zero crosstalk from WORK-BBB!
        self.assertFalse(any("WORK-BBB" in s or "Task B" in s for s in summaries_a))
        self.assertFalse(any("tool_b_execute" in s for s in summaries_a))
        self.assertFalse(any("Finished B" in s for s in summaries_a))

        # Filter for WORK-BBB
        tl_b = workflow_view.filter_timeline_events(snap, proj, work_filter="WORK-BBB")
        summaries_b = [e.summary for e in tl_b]
        self.assertTrue(any("WORK-BBB" in s or "Task B" in s for s in summaries_b))
        self.assertTrue(any("tool_b_execute" in s for s in summaries_b))
        self.assertTrue(any("Finished B" in s for s in summaries_b))
        # Must have zero crosstalk from WORK-AAA!
        self.assertFalse(any("WORK-AAA" in s or "Task A" in s for s in summaries_b))
        self.assertFalse(any("tool_a_execute" in s for s in summaries_b))
        self.assertFalse(any("Disk full" in s for s in summaries_b))
    def test_work_timeline_multi_assignment_same_child_session_isolation(self):
        """Verify that multiple sequential work items in the EXACT SAME child session are strictly isolated."""
        from charter import observations, workflow_view
        root = "root-same-sess-multi"
        child = "child-single-worker"

        # Coordinator dispatches WORK-111, child does work 1 and completes
        # Coordinator then dispatches WORK-222, child does work 2 and completes
        spawn_1 = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:00Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"WORK-111\",\"title\":\"Task 1\"}\n```",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Gauss"}],
                },
            },
        }
        tool_1 = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {"type": "custom_tool_call", "name": "step_one_action"},
            },
        }
        ret_1 = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:10Z",
            "payload": {"type": "task_complete", "summary": "Finished 1"},
        }
        spawn_2 = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:01:00Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"WORK-222\",\"title\":\"Task 2\"}\n```",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Gauss"}],
                },
            },
        }
        tool_2 = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:01:05Z",
            "payload": {
                "type": "item_completed",
                "item": {"type": "custom_tool_call", "name": "step_two_action"},
            },
        }
        ret_2 = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:01:10Z",
            "payload": {"type": "task_complete", "summary": "Finished 2"},
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_1, spawn_2])
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="Gauss", timestamp="2026-08-19T10:00:05Z", collab_events=[tool_1, ret_1, tool_2, ret_2])

        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir, include_tool_calls=True)
        proj = workflow_view.project_workflow(snap)

        self.assertEqual(len(proj.work_items), 2)

        # Filter for WORK-111
        tl_1 = workflow_view.filter_timeline_events(snap, proj, work_filter="WORK-111")
        summaries_1 = [e.summary for e in tl_1]
        self.assertTrue(any("WORK-111" in s or "Task 1" in s for s in summaries_1))
        self.assertTrue(any("step_one_action" in s for s in summaries_1))
        # Must have zero crosstalk from WORK-222!
        self.assertFalse(any("WORK-222" in s or "Task 2" in s for s in summaries_1))
        self.assertFalse(any("step_two_action" in s for s in summaries_1))

        # Filter for WORK-222
        tl_2 = workflow_view.filter_timeline_events(snap, proj, work_filter="WORK-222")
        summaries_2 = [e.summary for e in tl_2]
        self.assertTrue(any("WORK-222" in s or "Task 2" in s for s in summaries_2))
        self.assertTrue(any("step_two_action" in s for s in summaries_2))
        # Must have zero crosstalk from WORK-111!
        self.assertFalse(any("WORK-111" in s or "Task 1" in s for s in summaries_2))
        self.assertFalse(any("step_one_action" in s for s in summaries_2))

    def test_statusline_fallback_zero_additional_rollout_scans(self):
        """Verify that statusline fallback to subagents tree reuses snapshot and does zero additional rollout scans."""
        from unittest.mock import patch
        from charter import statusline, observations, workflow_view, subagent
        root = "root-fallback-scan"
        child = "child-fallback-worker"
        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z")
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="Worker", timestamp="2026-08-19T10:00:05Z")
        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
        proj = workflow_view.project_workflow(snap)

        # Proj has 0 work items (plain prompt), so it falls back to subagents tree
        self.assertEqual(len(proj.work_items), 0)

        with patch("charter.subagent.find_rollouts_in_days") as mock_find:
            sub_head, sub_lines = statusline._workflow_section(proj, tick=0, sid=root, effective_sid=root, snapshot=snap)
            self.assertIsNotNone(sub_head)
            self.assertTrue(any("Worker" in ln for ln in sub_lines))
            # Must NOT call find_rollouts_in_days because snapshot was reused in-memory!
            mock_find.assert_not_called()

    def test_same_child_two_dispatches_identical_timestamps(self):
        """Verify that two dispatches to the same child with identical timestamps return cleanly with 0 unbound events."""
        from charter import observations, workflow_view
        root = "root-same-ts-multi"
        child = "child-same-ts-worker"
        ts = "2026-08-19T10:00:00Z"

        spawn_1 = {
            "type": "event_msg",
            "timestamp": ts,
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"WORK-1\",\"title\":\"Task 1\"}\n```",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Gauss"}],
                },
            },
        }
        spawn_2 = {
            "type": "event_msg",
            "timestamp": ts,
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"WORK-2\",\"title\":\"Task 2\"}\n```",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Gauss"}],
                },
            },
        }
        tool_1 = {
            "type": "event_msg",
            "timestamp": ts,
            "payload": {
                "type": "item_completed",
                "item": {"type": "custom_tool_call", "name": "tool_1"},
            },
        }
        ret_1 = {
            "type": "event_msg",
            "timestamp": ts,
            "payload": {"type": "task_complete", "summary": "Done 1"},
        }
        tool_2 = {
            "type": "event_msg",
            "timestamp": ts,
            "payload": {
                "type": "item_completed",
                "item": {"type": "custom_tool_call", "name": "tool_2"},
            },
        }
        ret_2 = {
            "type": "event_msg",
            "timestamp": ts,
            "payload": {"type": "task_complete", "summary": "Done 2"},
        }

        write_test_rollout(self.sessions_dir, root, timestamp=ts, collab_events=[spawn_1, spawn_2])
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="Gauss", timestamp=ts, collab_events=[tool_1, ret_1, tool_2, ret_2])

        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir, include_tool_calls=True)
        proj = workflow_view.project_workflow(snap)
        self.assertEqual(len(proj.work_items), 2)
        self.assertEqual(proj.work_items[0].phase, "returned")
        self.assertEqual(proj.work_items[1].phase, "returned")
        self.assertEqual(len(proj.unbound_events), 0, f"Expected 0 unbound events, got: {[e.attributes.get('unbound_reason') for e in proj.unbound_events]}")

        # Timeline isolation under identical timestamps
        tl_1 = workflow_view.filter_timeline_events(snap, proj, work_filter="WORK-1")
        summaries_1 = [e.summary for e in tl_1]
        self.assertTrue(any("tool_1" in s for s in summaries_1))
        self.assertFalse(any("tool_2" in s for s in summaries_1), "WORK-1 timeline must not contain tool_2")

        tl_2 = workflow_view.filter_timeline_events(snap, proj, work_filter="WORK-2")
        summaries_2 = [e.summary for e in tl_2]
        self.assertTrue(any("tool_2" in s for s in summaries_2))
        self.assertFalse(any("tool_1" in s for s in summaries_2), "WORK-2 timeline must not contain tool_1")

    def test_same_child_return_a_intake_a_dispatch_b_return_b(self):
        """Verify complete causal lifecycle across sequential assignments to the same child."""
        from charter import observations, workflow_view
        root = "root-seq-lifecycle"
        child = "child-seq-worker"
        ts = "2026-08-19T10:00:00Z"

        spawn_a = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:01Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"WORK-A\",\"title\":\"Task A\"}\n```",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Gauss"}],
                },
            },
        }
        ret_a = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {"type": "task_complete", "summary": "Finished A"},
        }
        intake_a = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:06Z",
            "payload": {
                "type": "user_message",
                "message": "```charter-observe\n{\"schema\":1,\"event\":\"intake\",\"work_id\":\"WORK-A\"}\n```",
            },
        }
        spawn_b = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:10Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"WORK-B\",\"title\":\"Task B\"}\n```",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Gauss"}],
                },
            },
        }
        ret_b = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:15Z",
            "payload": {"type": "task_complete", "summary": "Finished B"},
        }

        write_test_rollout(self.sessions_dir, root, timestamp=ts, collab_events=[spawn_a, intake_a, spawn_b])
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="Gauss", timestamp=ts, collab_events=[ret_a, ret_b])

        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
        proj = workflow_view.project_workflow(snap)

        self.assertEqual(len(proj.work_items), 2)
        item_a = next(w for w in proj.work_items if w.external_id == "WORK-A")
        item_b = next(w for w in proj.work_items if w.external_id == "WORK-B")
        self.assertEqual(item_a.phase, "intaken")
        self.assertEqual(item_b.phase, "returned")
        self.assertEqual(len(proj.unbound_events), 0)

    def test_same_child_multi_assignment_incident_bound_strictly_to_work_b(self):
        """Verify that an incident occurring during WORK-B in the same child actor NEVER contaminates WORK-A timeline."""
        from charter import observations, workflow_view
        root = "root-inc-bound"
        child = "child-inc-worker"

        spawn_a = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:00Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"WORK-AAA\",\"title\":\"Task A\"}\n```",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Gauss"}],
                },
            },
        }
        ret_a = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {"type": "task_complete", "summary": "Done A"},
        }
        spawn_b = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:01:00Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "```charter-observe\n{\"schema\":1,\"event\":\"dispatch\",\"work_id\":\"WORK-BBB\",\"title\":\"Task B\"}\n```",
                    "receiver_agents": [{"thread_id": child, "agent_nickname": "Gauss"}],
                },
            },
        }
        inc_b = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:01:05Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CollabAgentToolCall",
                    "tool": "wait",
                    "agents_states": {child: {"error": "Disk exhausted during Task B"}},
                },
            },
        }
        ret_b = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:01:10Z",
            "payload": {"type": "task_complete", "summary": "Done B"},
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z", collab_events=[spawn_a, spawn_b])
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="Gauss", timestamp="2026-08-19T10:00:02Z", collab_events=[ret_a, inc_b, ret_b])

        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
        proj = workflow_view.project_workflow(snap)

        # Filter for WORK-AAA
        tl_a = workflow_view.filter_timeline_events(snap, proj, work_filter="WORK-AAA")
        summaries_a = [e.summary for e in tl_a]
        self.assertFalse(any("Disk exhausted" in s for s in summaries_a), "WORK-AAA timeline must not contain WORK-BBB incident")

        # Filter for WORK-BBB
        tl_b = workflow_view.filter_timeline_events(snap, proj, work_filter="WORK-BBB")
        summaries_b = [e.summary for e in tl_b]
        self.assertTrue(any("Disk exhausted" in s for s in summaries_b), "WORK-BBB timeline must contain its incident")

    def test_statusline_fallback_inflight_only_actor_included(self):
        """Verify that an inflight-only actor appears in the snapshot-backed fallback subagents tree."""
        from charter import statusline, observations, workflow_view, inflight, config
        root = "root-fallback-inflight"
        orig_state = config.STATE_DIR
        config.STATE_DIR = self.tmp / "inflight-state"
        self.addCleanup(setattr, config, "STATE_DIR", orig_state)

        # Start inflight actor that has no rollout file yet
        tok = inflight.start("InflightWorker", session_id=root, agent_id="inf-worker-1")

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z")
        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir)
        proj = workflow_view.project_workflow(snap)

        sub_head, sub_lines = statusline._workflow_section(proj, tick=0, sid=root, effective_sid=root, snapshot=snap)
        self.assertIsNotNone(sub_head)
        self.assertTrue(any("InflightWorker" in ln for ln in sub_lines))
        self.assertTrue(any("active" in sub_head for _ in [1]))

    def test_statusline_fallback_returned_child_later_active_wins(self):
        """Verify that a child that previously returned but has subsequent running activity renders as running in fallback."""
        from charter import statusline, observations, workflow_view
        root = "root-fallback-reuse"
        child = "child-fallback-reuse-worker"

        ret_1 = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:05Z",
            "payload": {"type": "task_complete", "summary": "Done 1"},
        }
        tool_2 = {
            "type": "event_msg",
            "timestamp": "2026-08-19T10:00:10Z",
            "payload": {
                "type": "item_completed",
                "item": {"type": "custom_tool_call", "name": "active_tool"},
            },
        }

        write_test_rollout(self.sessions_dir, root, timestamp="2026-08-19T10:00:00Z")
        write_test_rollout(self.sessions_dir, child, parent_id=root, nickname="ReusedWorker", timestamp="2026-08-19T10:00:01Z", collab_events=[ret_1, tool_2])

        snap = observations.collect_observation_snapshot(root, sessions_dir=self.sessions_dir, include_tool_calls=True)
        proj = workflow_view.project_workflow(snap)

        sub_head, sub_lines = statusline._workflow_section(proj, tick=0, sid=root, effective_sid=root, snapshot=snap)
        self.assertIsNotNone(sub_head)
        self.assertTrue(any("ReusedWorker" in ln for ln in sub_lines))
        # Active tag must be present because the child is currently active (running)
        self.assertIn("active", sub_head)

if __name__ == "__main__":
    unittest.main()
