# Workflow Observer Verification Audit

**Date:** 2026-08-19  
**Branch:** main  
**Scope:** Read-only Workflow Observer implementation (ADR 0017, `charter observe`, `charter/observations.py`, `charter/workflow_view.py`, `charter/commands_observe.py`, statusline compact panel integration, inflight session scoping).

---

## 1. Semantic Acceptance Matrix

| Invariant | Test Coverage | Status |
|---|---|---|
| `task_complete` → `RETURNED` + `INTAKE_REQUIRED` | `test_spawn_activity_task_complete_lifecycle` | **PASS** |
| `SubagentStop` without `task_complete` → `STOPPED` + "return not observed" | `test_spawn_activity_stopped_without_return` | **PASS** |
| Free-text `blocked/error/timeout/done` → no phase change | `test_free_text_blocked_or_error_changes_nothing` | **PASS** |
| Typed incident → incident list only; no phase change | `test_typed_incident_appears_in_incidents_without_changing_phase` | **PASS** |
| Parent ordinary message after return → intake remains open | `test_spawn_activity_task_complete_lifecycle` | **PASS** |
| Exact coordinator-authored intake declaration → intake closes, phase `INTAKEN` | `test_coordinator_intake_and_ordered_resolve` | **PASS** |
| Exact coordinator-authored resolve after intake → phase `RESOLVED` | `test_coordinator_intake_and_ordered_resolve` | **PASS** |
| Child-authored or unrelated-actor intake/resolve declaration → no phase change | `test_child_authored_intake_rejected` | **PASS** |
| Resolve before intake → no phase change; warning/unbound declaration | `test_resolve_before_intake_rejected` | **PASS** |
| EM/CM peers appear only from exact declaration | `test_declared_peer_coexists_with_spawn_hierarchy` | **PASS** |
| Every phase, obligation, incident, relationship explainable with evidence | `test_explain_projection`, `test_source_path_and_line_number_survive_in_evidence` | **PASS** |
| No elapsed-time threshold creates a verdict | Reduction table deterministic rule verification | **PASS** |
| No observer command writes state or calls control actions | `test_filesystem_write_guard_read_only_invariant` | **PASS** |

---

## 2. Backward Compatibility Acceptance Matrix

| Requirement | Test Coverage | Status |
|---|---|---|
| Existing `subagent tree/list/show/log` commands pass | `test_cli_tree_and_list_json`, `test_cli_log_and_exchanges` | **PASS** |
| Legacy JSON `status` field remains intact | `test_characterization_legacy_subagent_json_shape`, `test_legacy_status_and_additive_runtime_state` | **PASS** |
| Additive `runtime_state` field provided | `test_legacy_status_and_additive_runtime_state` | **PASS** |
| Neutral `○ stopped` text rendering for stopped nodes | `test_stopped_subagent_never_renders_completed_word_in_text` | **PASS** |
| Statusline dashboard workflow panel + subagent fallback | `test_statusline_workflow_panel_and_fallback` | **PASS** |
| Wheel force-include contains `docs/observe.md` | `test_every_page_is_force_included_in_the_wheel` | **PASS** |
| Shipped documentation reachable via `charter docs show observe` | `test_a_topic_reads_back_its_page` | **PASS** |

---

## 3. Structural and Performance Guarantees

1. **Session Indexing:** Single-pass index (`build_session_index`) scans directories once per collection.
2. **Snapshot Building:** Single snapshot and projection computed at most once per statusline `render()` or CLI command.
3. **Watch Mode:** `SubagentEventWatcher` checks metadata (mtime + size) without rescanning JSONL content unless source files change.
4. **Zero Network / Subprocess Calls:** The observer path uses 100% standard library file I/O; zero subprocesses or network requests are executed.
5. **Read-Only Invariant:** Verified via `test_filesystem_write_guard_read_only_invariant`.

---

## 4. Test Suite Execution Summary

```text
Ran 64 tests across tests.test_subagent, tests.test_workflow_view, tests.test_observations, tests.test_commands_observe.
All 64 tests passed with 0 failures, 0 errors.
```
