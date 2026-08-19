# Workflow Observer Verification Audit

**Date:** 2026-08-19  
**Branch:** main  
**Scope:** Read-only Workflow Observer implementation and external audit review fixes (ADR 0017, `charter observe`, `charter/observations.py`, `charter/workflow_view.py`, `charter/commands_observe.py`, `charter/subagent.py`, `charter/statusline.py`, and causal lifecycle verification).

---

## 1. Executive Summary

Charter's Codex subagent visualization has been extended into a strictly read-only workflow observer. The observer parses exact `charter-observe` declarations and correlates them with mechanical rollout events, Hook traces, and inflight records without guessing ownership, altering state, or exercising orchestration authority.

All issues identified across rounds 1 through 6 of external review have been completely resolved:
1. **Initial-Message-Constrained One-Shot Dispatch Mirror Deduplication (Gate 2)**:
   - Deduplication is strictly constrained to the spawned child's **first** `user_message`. Mirror eligibility for that child is immediately expired after inspecting the first message.
   - Any late repeated `dispatch` declarations (even with identical payload) arriving after an initial plain child message are preserved as distinct `ObservedEvent`s and never merged.
   - Tested by: `test_late_repeated_dispatch_declaration_after_plain_initial_message` and `test_mirrored_declaration_deduplication`.
2. **Premature Intake Rejection and Strict Causal DAG (Gate 4)**:
   - Causal edges (`return_i` $\to$ `intake_i`) are **only** added when `intake.observed_at >= return.observed_at`. Intake declarations observably earlier than the child return receive no causal edge and are evaluated in natural order, correctly triggering `invalid_phase_for_intake` rejection in `unbound_events`.
   - Tested by: `test_premature_intake_followed_by_identical_valid_intake` (asserting that `unbound_events[0].id` exactly matches the premature 10:00:02 intake event).
3. **Assignment-Segment Activity Binding in Reducer (Gate 1 & 4)**:
   - In `project_workflow`, activity, stop, incident, and return events bind strictly to the single earliest still-open (FIFO) assignment for the child.
   - During WORK-1, WORK-2 remains in phase `dispatched` and receives zero activity evidence. When WORK-1 returns, WORK-2 becomes active upon segment 2 activity.
   - Under coarse or identical timestamps, WORK-1's timeline contains only tool 1, and WORK-2's timeline contains only tool 2, with zero crosstalk.
   - Tested by: `test_same_child_two_dispatches_identical_timestamps` and `test_work_timeline_multi_assignment_same_child_session_isolation`.
4. **Statusline Fallback Full Fidelity & Zero Scans (Gate 5)**:
   - Subagent tree is reconstructed 100% in-memory from `snapshot.sessions` and `snapshot.events`, including inflight-only and event-only actors, with chronological last-event-wins runtime reduction.
   - Tested by: `test_statusline_fallback_zero_additional_rollout_scans`, `test_statusline_fallback_inflight_only_actor_included`, and `test_statusline_fallback_returned_child_later_active_wins`.
5. **Inflight Event ID Stability (Gate 3)**:
   - Fixed token-based discriminator with `ordinal=0` ensures absolute immutability of existing inflight event IDs across record removals.
   - Tested by: `test_inflight_event_id_immutability_on_record_removal`.

---

## 2. Gate Verification Scorecard

| Gate | Verdict | Evidence |
| :--- | :---: | :--- |
| **Gate 1: Authority Boundary & Lifecycle Orthogonality** | **PASS** | Runtime state and work lifecycle phase are completely decoupled. Sequential assignments to the same child actor have strictly isolated lifecycles, bases, and timelines. |
| **Gate 2: Exact Metadata & Parser Strictness** | **PASS** | `parse_workflow_declarations` strictly validates schemas. Mirror deduplication is one-shot and strictly initial-message constrained. |
| **Gate 3: Read-Only & Deterministic Identity** | **PASS** | All observer paths and inflight reads are 100% read-only (`prune=False`). Inflight event IDs are strictly immutable. |
| **Gate 4: Session Isolation & Causal Ordering** | **PASS** | Assignment-specific topological sort preserves exact physical file order and enforces cross-session causal dependencies without artificially reordering premature declarations. |
| **Gate 5: Dashboard & Crash Resilience** | **PASS** | Statusline displays single `▪ workflow` header and reuses snapshot in-memory on fallback with zero additional filesystem scans while faithfully representing inflight-only actors and last-event-wins active status. |
| **Gate 6: Backward Compatibility & Test Integrity** | **PASS** | Legacy `charter subagent` JSON/text outputs, status fields, additive runtime states, and neutral stopped text rendering are 100% preserved. Exact 32-choice top-level CLI command test passes. |

---

## 3. Test Execution Summary

```text
Ran 114 tests across:
  - tests.test_workflow_view
  - tests.test_observations
  - tests.test_commands_observe
  - tests.test_subagent
  - tests.test_docs_show

Result: 114 passed, 0 failed, 0 errors (1 platform symlink test skipped on Windows).
```
