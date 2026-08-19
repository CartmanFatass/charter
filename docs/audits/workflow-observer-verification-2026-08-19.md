# Workflow Observer Verification Audit

**Date:** 2026-08-19  
**Branch:** main  
**Scope:** Read-only Workflow Observer implementation and external audit review fixes (ADR 0017, `charter observe`, `charter/observations.py`, `charter/workflow_view.py`, `charter/commands_observe.py`, `charter/subagent.py`, `charter/statusline.py`, and causal lifecycle verification).

---

## 1. Executive Summary

Charter's Codex subagent visualization has been extended into a strictly read-only workflow observer. The observer parses exact `charter-observe` declarations and correlates them with mechanical rollout events, Hook traces, and inflight records without guessing ownership, altering state, or exercising orchestration authority.

All issues identified across rounds 1 through 5 of external review have been completely resolved:
1. **One-Shot Dispatch-Only Mirror Deduplication (Gate 2)**:
   - Deduplication is strictly constrained to the one-shot mirror between `spawn_agent.prompt` and the spawned child's initial `user_message` for `event == "dispatch"`.
   - Any later declarations—including repeated or retried `intake`, `resolve`, `relation`, or subsequent dispatches—are always preserved as distinct observed events.
   - Tested by: `test_premature_intake_followed_by_identical_valid_intake` and `test_distinct_relation_actor_ids_not_deduplicated`.
2. **Assignment-Specific Topological Causal Ordering (Gate 4)**:
   - Intra-source file ordering unifies `session_meta` and all subsequent lines into single physical streams keyed by `(session_id, file_path)`, guaranteeing physical line order is never rewritten or inverted.
   - Cross-session causal edges link each specific `dispatch_sent_i` to the first event of child activity segment `i` demarcated by returns.
   - Child return `i` is strictly linked to its corresponding coordinator `intake_i`.
   - Sequential assignments to the same child with identical timestamps return cleanly with 0 unbound events.
   - Tested by: `test_session_meta_timestamp_later_than_task_complete_preserves_order`, `test_same_child_two_dispatches_identical_timestamps`, and `test_same_child_return_a_intake_a_dispatch_b_return_b`.
3. **Strict Work Incident Isolation (Gate 1 & 4)**:
   - Work timeline filtering matches incidents strictly by `work_id`, with no actor-fallback leakage across multiple assignments to the same actor.
   - Tested by: `test_same_child_multi_assignment_incident_bound_strictly_to_work_b`.
4. **In-Memory Fallback Full Fidelity (Gate 5)**:
   - Snapshot-backed fallback includes inflight-only and event-only actors not yet present in `snapshot.sessions`.
   - Runtime status uses chronological last-event-wins reduction, properly reflecting active status when a previously returned child becomes active again.
   - Tested by: `test_statusline_fallback_zero_additional_rollout_scans`, `test_statusline_fallback_inflight_only_actor_included`, and `test_statusline_fallback_returned_child_later_active_wins`.
5. **Inflight Event ID Stability (Gate 3)**:
   - Fixed token-based discriminator with `ordinal=0` ensures absolute immutability of existing inflight event IDs across record removals.
   - Tested by: `test_inflight_event_id_immutability_on_record_removal`.

---

## 2. Gate Verification Scorecard

| Gate | Verdict | Evidence |
| :--- | :---: | :--- |
| **Gate 1: Authority Boundary & Lifecycle Orthogonality** | **PASS** | Runtime state and work lifecycle phase are completely decoupled. Sequential work items in the same child session have strictly isolated lifecycles and timelines. Work timeline incidents are strictly isolated per work item. |
| **Gate 2: Exact Metadata & Parser Strictness** | **PASS** | `parse_workflow_declarations` strictly validates schemas. Mirror deduplication is one-shot and dispatch-specific; repeated or retried declarations remain distinct events. |
| **Gate 3: Read-Only & Deterministic Identity** | **PASS** | All observer paths and inflight reads are 100% read-only (`prune=False`). Inflight event IDs are strictly immutable. |
| **Gate 4: Session Isolation & Causal Ordering** | **PASS** | Assignment-specific topological sort preserves exact intra-source file line order and enforces cross-session causal dependencies (`dispatch_i` $\to$ `segment_i`, `return_i` $\to$ `intake_i`). |
| **Gate 5: Dashboard & Crash Resilience** | **PASS** | Statusline displays single `▪ workflow` header and reuses snapshot in-memory on fallback with zero additional filesystem scans while faithfully representing inflight-only actors and last-event-wins active status. |
| **Gate 6: Backward Compatibility & CLI Integrity** | **PASS** | Legacy `charter subagent` JSON/text outputs, status fields, additive runtime states, and neutral stopped text rendering are 100% preserved. Exact 32-choice top-level CLI command test passes. |

---

## 3. Test Execution Summary

```text
Ran 113 tests across:
  - tests.test_workflow_view
  - tests.test_observations
  - tests.test_commands_observe
  - tests.test_subagent
  - tests.test_docs_show

Result: 113 passed, 0 failed, 0 errors (1 platform symlink test skipped on Windows).
```
