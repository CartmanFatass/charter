# Workflow Observer Verification Audit

**Date:** 2026-08-19 
**Branch:** main 
**Scope:** Read-only Workflow Observer implementation and external audit review fixes (ADR 0017, `charter observe`, `charter/observations.py`, `charter/workflow_view.py`, `charter/commands_observe.py`, `charter/subagent.py`, `charter/inflight.py`, `charter/statusline.py`, `charter/hooks.py`).

---

## 1. Executive Summary

Charter's read-only workflow observer is fully implemented, verified, and strictly isolated across all 6 core audit gates. It surfaces exact runtime facts and declarations from Codex rollouts, Hook traces, and inflight records without ever becoming an orchestrator, assuming transition authority, mutating state, or guessing ownership.

All residual audit items across Rounds 1 through 8 have been completely resolved and verified by comprehensive unit and integration tests.

---

## 2. Itemized Verification of Audit Gates

### Gate 1: Authority & Orthogonality
- **Runtime / Lifecycle Orthogonality:** Runtime states (`starting`, `running`, `stopped`, `unknown`) and work lifecycle phases (`dispatched`, `active`, `returned`, `intaken`, `resolved`) are completely orthogonal. A child stopping never implies work resolution.
- **Strict FIFO Assignment-Segment Activity & Incident Binding:** Activity (`actor_started`, `tool_started`, `tool_finished`), `actor_stopped`, typed incidents (`incident_seen`), and returns bind strictly to the single earliest still-open (FIFO) assignment for the child (`open_wids[0]`), and only if the event timestamp is at or after that work item's dispatch timestamp (`ts >= work_item.dispatched_at`). Incidents occurring with timestamps earlier than a work item's dispatch are never attached to that work item, even under non-monotonic physical streams.
- **Exact-First Work Filter Resolution:** Work filter matching operates in two strict stages: Stage 1 selects exact matches on external or internal work IDs with absolute precedence, completely suppressing prefix/substring matches. Stage 2 (substring fallback) is evaluated only when zero exact matches exist. Incidents are matched strictly against `matched_internal_work_ids`, eliminating false-positive leakage on short external IDs like `A` or `WORK`.
- *Tests:* `test_same_child_two_dispatches_identical_timestamps`, `test_same_child_multi_assignment_incident_bound_strictly_to_work_b`, `test_return_a_incident_or_stop_later_dispatch_b_causal_isolation`, `test_short_external_work_id_incident_substring_isolation`, `test_physically_later_incident_timestamped_earlier_than_dispatch_b`, `test_exact_first_work_filter_collisions`.

### Gate 2: Exact Metadata & Parser Strictness
- **Strict Line-Anchored Fences:** Fenced declaration blocks require exact line-anchored ```` ```charter-observe ```` and ```` ``` ```` without extra wrapping.
- **Temporally-Exact Mirror Candidate Queue:** Pending dispatch mirrors are tracked in per-child ordered queues (`pending_dispatch_mirrors[rx_id]`). On the child's first `user_message`, only dispatch candidates with `dispatch.observed_at <= message.observed_at` are eligible. The earliest matching candidate in source order is merged, and all remaining pending mirrors for that child are expired. Subsequent parent dispatches remain distinct events and never absorb past child evidence.
- *Tests:* `test_mirrored_declaration_deduplication`, `test_late_repeated_dispatch_declaration_after_plain_initial_message`, `test_identical_dispatch_a_child_first_mirror_identical_later_dispatch_b`.

### Gate 3: Read-Only & Deterministic Identity
- **Non-Pruning Observation Snapshot:** Observation snapshots read inflight records with `prune=False`, ensuring zero disk writes.
- **Token-Stable Inflight Event IDs:** Inflight event IDs use `token` as discriminator with fixed `ordinal=0`, guaranteeing 100% SHA-256 ID immutability when other inflight records are added or removed.
- *Tests:* `test_inflight_event_id_immutability_on_record_removal`, `test_filesystem_write_guard_read_only_invariant`.

### Gate 4: Session Isolation & Causal Ordering
- **Physical Source Stream Invariant:** Physical source line order within each rollout file is preserved across all events.
- **Deterministic Causal DAG:** `_topological_causal_sort` adds cross-session causal edges (`dispatch -> child activity`, `child return -> intake`). Intake declarations earlier than return are never artificially delayed, properly triggering `invalid_phase_for_intake` in `unbound_events`.
- **Causal-Cycle Recovery with Physical Order Preservation:** If cross-session edges induce a cycle, optional cross-session causal edges are discarded, and Kahn's algorithm is rerun over mandatory intra-source linear chains, preserving 100% of physical file order without timestamp reordering.
- *Tests:* `test_causal_cycle_with_non_monotonic_source_timestamps_preserves_physical_order`, `test_premature_intake_followed_by_identical_valid_intake`, `test_negative_causal_order_intra_source_preservation`.

### Gate 5: Dashboard & Crash Resilience
- **Zero-Scan In-Memory Fallback:** Statusline fallback reconstructs `SubagentTreeNode` hierarchies directly from pre-collected `ObservationSnapshot.sessions` and `ObservationSnapshot.events` in memory, executing 0 filesystem rollout searches.
- **Last-Event-Wins Active Status & Cycle Protection:** Fallback determines live actor status using last-event-wins across observation events and inflight records. Tree construction includes path-local `seen` cycle protection.
- **Nested Inflight Parentage:** Inflight records propagate `parent_id` into `peer_id`, correctly placing nested inflight subagents under their parent coordinator.
- *Tests:* `test_statusline_fallback_zero_additional_rollout_scans`, `test_statusline_fallback_inflight_only_actor_included`, `test_nested_inflight_actor_under_non_root_coordinator`.

### Gate 6: Backward Compatibility & Test Integrity
- All existing `charter subagent` commands, Hook signatures, and statusline contracts remain fully backward compatible.
- All 121 observer, subagent, and docs tests pass cleanly.

---

## 3. Test Execution Summary

```bash
python -m unittest tests.test_workflow_view tests.test_observations tests.test_commands_observe tests.test_subagent tests.test_docs_show -v

Ran 121 tests in 6.807s
OK (skipped=1)
```
