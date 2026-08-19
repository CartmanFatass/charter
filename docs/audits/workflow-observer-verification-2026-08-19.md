# Workflow Observer Verification Audit

**Date:** 2026-08-19  
**Branch:** main  
**Scope:** Read-only Workflow Observer implementation and external audit review fixes (ADR 0017, `charter observe`, `charter/observations.py`, `charter/workflow_view.py`, `charter/commands_observe.py`, `charter/subagent.py`, `charter/statusline.py`, and causal lifecycle verification).

---

## 1. Executive Summary

Charter's Codex subagent visualization has been extended into a strictly read-only workflow observer. The observer parses exact `charter-observe` declarations and correlates them with mechanical rollout events, Hook traces, and inflight records without guessing ownership, altering state, or exercising orchestration authority.

All four residual gaps identified in the external review report (`local://paste-1.md`) have been completely resolved:
1. **Canonical Declaration Deduplication (Gate 2)**: Deduplication keys now explicitly include both `declared_actor_id` (`d.actor_id`) and `bound_actor_id` (`rx_id` for spawn prompt, `sid` for child messages). Distinct relation declarations (e.g. `EM-A reports_to ROOT` vs `CM-B reports_to ROOT`) in the same session produce distinct canonical keys and are never merged, while `spawn_agent.prompt` and matching initial `user_message` correctly deduplicate evidence references.
2. **Deterministic Causal Topological Sort (Gate 4)**: Replaced scalar session high-watermark with a Kahn's topological sort driven by a min-heap priority queue (`observed_at`, `_event_priority`, `ordinal`, `id`). Intra-source stream line order is strictly preserved via mandatory intra-source edges ($E_i \to E_{i+1}$), while cross-session causal edges (`dispatch_sent` $\to$ child start/activity, child `actor_returned` $\to$ coordinator `intake`) are strictly enforced. In coordinator sessions, resolve of prior work followed by dispatch of new work in the same second strictly preserves `dispatch` $\to$ child `actor_started` causality.
3. **Inflight Event ID Stability (Gate 3)**: Inflight event IDs now use a fixed ordinal `0` and the unique `token` discriminator instead of dynamic list indices (`irec_idx`). Removal of earlier inflight records leaves subsequent existing inflight records' event IDs 100% immutable and unchanged.
4. **Statusline Fallback Zero Redundant Scans (Gate 5)**: `_workflow_section` passes the pre-collected `ObservationSnapshot` to `_subagent_section`. Fallback to the subagent tree when no work items exist constructs the tree and exchanges 100% in-memory from `snapshot.sessions` and `snapshot.events`, eliminating all redundant rollout discovery and file reads.
5. **Multi-Assignment Timeline Isolation (Gate 1 & 4)**: Work timeline filtering strictly relies on `matched_source_ids` and work item basis evidence. Multiple sequential work items dispatched to the exact same child session are strictly isolated with zero crosstalk.

---

## 2. Gate Verification Scorecard

| Gate | Verdict | Evidence |
| :--- | :---: | :--- |
| **Gate 1: Authority Boundary & Lifecycle Orthogonality** | **PASS** | Runtime state (`starting`, `running`, `stopped`, `unknown`) and work lifecycle phase (`dispatched`, `active`, `returned`, `intaken`, `resolved`) are completely decoupled. Sequential work items in the same child session have strictly isolated lifecycles and timelines. |
| **Gate 2: Exact Metadata & Parser Strictness** | **PASS** | `parse_workflow_declarations` strictly validates schemas. Canonical dedupe key preserves distinct declared `actor_id`s in the same session. |
| **Gate 3: Read-Only & Deterministic Identity** | **PASS** | All observer paths and inflight reads are 100% read-only (`prune=False`). Inflight event IDs are strictly immutable across record additions and removals. |
| **Gate 4: Session Isolation & Causal Ordering** | **PASS** | Topological causal sort preserves exact intra-source file line order and enforces cross-session causal dependencies (`dispatch` $\to$ `start`, `return` $\to$ `intake`). |
| **Gate 5: Dashboard & Crash Resilience** | **PASS** | Statusline displays single `▪ workflow` header and reuses snapshot in-memory on fallback with zero additional filesystem scans. `watch_workflow_view` degrades gracefully inside `try/except/finally`. |
| **Gate 6: Backward Compatibility & CLI Integrity** | **PASS** | Legacy `charter subagent` JSON/text outputs, status fields, additive runtime states, and neutral stopped text rendering are 100% preserved. Exact 32-choice top-level CLI command test passes. |

---

## 3. Test Execution Summary

```text
Ran 106 tests across:
  - tests.test_workflow_view
  - tests.test_observations
  - tests.test_commands_observe
  - tests.test_subagent
  - tests.test_docs_show

Result: 106 passed, 0 failed, 0 errors (1 platform symlink test skipped on Windows).
```
