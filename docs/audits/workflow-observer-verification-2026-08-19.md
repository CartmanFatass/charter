# Workflow Observer Verification Audit

**Date:** 2026-08-19  
**Branch:** main  
**Scope:** Read-only Workflow Observer implementation and external audit review fixes (ADR 0017, `charter observe`, `charter/observations.py`, `charter/workflow_view.py`, `charter/commands_observe.py`, statusline integration, and causal lifecycle verification).

---

## 1. Executive Summary

Charter's Codex subagent visualization has been extended into a strictly read-only workflow observer. The observer parses exact `charter-observe` declarations and correlates them with mechanical rollout events, Hook traces, and inflight records without guessing ownership, altering state, or exercising orchestration authority.

All issues identified across rounds 1 and 2 of external code review have been resolved:
- **Zero Orphan Tests**: `tests/test_openai_developer_mcp.py` removed.
- **Strict Canonical Deduplication (HIGH 1)**: Distinct relation declarations (`reports_to`, `owner`, `peer`) within the same session maintain distinct canonical keys and are never swallowed. Mirrored declarations between `spawn_agent.prompt` and the child's initial `user_message` are deduplicated into a single event with multiple evidence references.
- **Source-Order Preserving Causal Ordering (HIGH 2)**: Within a session/source stream, the exact source sequence is strictly preserved (intra-source line order is never rewritten or "healed"). Cross-session causal dependencies (`dispatch` -> `start` -> `return` -> `intake` -> `resolve`) are deterministically resolved when timestamps are identical.
- **Stable Source-Local Event IDs (HIGH 3)**: `make_event_id` incorporates source-local ordinals and discriminators into SHA-256 event IDs, guaranteeing determinism, distinctness for sub-events on the same line, and immutability when subsequent events/sessions are added.
- **Incident & Work Timeline Isolation (MEDIUM 1)**: Work item timeline filtering strictly maps to the assigned work item's session, source evidence, and attached incidents, with zero crosstalk across multiple assignments to the same actor name.
- **Full Try-Except-Finally Watch Protection (MEDIUM 2)**: `watch_workflow_view` wraps initialization, observation gathering, and terminal cursor restoration inside robust exception boundaries, preventing watch loop errors from generating crash drafts.
- **Statusline Redundant Scan Elimination (MEDIUM 3)**: `_subagent_section` and `_workflow_section` pass through `effective_sid`, eliminating duplicate root discovery and rollout filesystem scans.
- **Strict Forbidden-Field Presence Checking (LOW 1)**: Schema validation rejects declarations containing forbidden keys even if their value is explicit `null`.
- **Dynamic Date Pathing in Tests (LOW 2)**: `test_duplicate_rollout_files_newest_modified_at_wins` dynamically builds date paths using current UTC time.
- **Strict 32-Choice CLI Exact Equality**: Top-level command set integrity characterization test asserts exact equality across all registered top-level commands, aliases, and internal entrypoints.

---

## 2. Gate Verification Scorecard

| Gate | Verdict | Evidence |
| :--- | :---: | :--- |
| **Gate 1: Authority Boundary & Lifecycle Orthogonality** | **PASS** | Runtime state (`starting`, `running`, `stopped`, `unknown`) and work lifecycle phase (`dispatched`, `active`, `returned`, `intaken`, `resolved`) are completely decoupled. Return without intake remains `intake_required`. Intake/resolve require dispatching coordinator authority. |
| **Gate 2: Exact Metadata & Parser Strictness** | **PASS** | `parse_workflow_declarations` strictly validates schemas for `dispatch`, `intake`, `resolve`, and `relation`. Forbidden fields with `null` are rejected. Deduplication uses full canonical tuples. |
| **Gate 3: Read-Only & Deterministic Identity** | **PASS** | All observer paths and inflight reads are 100% read-only (`prune=False` by default; zero file modifications). Event IDs are deterministic SHA-256 digests incorporating source-local ordinals. |
| **Gate 4: Session Isolation & Causal Ordering** | **PASS** | Cross-session causal priorities (`dispatch: 20` -> `start: 30` -> `return: 80` -> `intake: 82` -> `resolve: 85` -> `stop: 90`) resolve cross-session dependencies. Monotonic intra-session ordering preserves source line order. |
| **Gate 5: Dashboard & Crash Resilience** | **PASS** | Statusline displays single `▪ workflow` header and falls back safely to subagents tree. `watch_workflow_view` degrades gracefully on error without crashing or generating local crash drafts. |
| **Gate 6: Backward Compatibility & CLI Integrity** | **PASS** | Legacy `charter subagent` JSON/text outputs, status fields, additive runtime states, and neutral stopped text rendering are 100% preserved. Exact 32-choice top-level CLI command test passes. |

---

## 3. Test Execution Summary

```text
Ran 101 tests across:
  - tests.test_workflow_view
  - tests.test_observations
  - tests.test_commands_observe
  - tests.test_subagent
  - tests.test_docs_show

Result: 101 passed, 0 failed, 0 errors (1 platform symlink test skipped on Windows).
```
