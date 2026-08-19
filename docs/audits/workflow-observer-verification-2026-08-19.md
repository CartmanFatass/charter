# Workflow Observer Verification Audit

**Date:** 2026-08-19  
**Branch:** main  
**Scope:** Read-only Workflow Observer implementation and external audit review fixes (ADR 0017, `charter observe`, `charter/observations.py`, `charter/workflow_view.py`, `charter/commands_observe.py`, statusline compact panel integration, inflight session scoping).

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
| Nested coordinator authority isolation (Root cannot bypass coordinator) | `test_root_cannot_bypass_nested_coordinator_intake_authority` | **PASS** |
| Intake before return rejected (`invalid_phase_for_intake`) | `test_intake_before_return_rejected` | **PASS** |
| Resolve before intake rejected (`invalid_phase_for_resolve`) | `test_resolve_before_intake_rejected` | **PASS** |
| Event-specific declaration schema enforcement (forbid relation in dispatch, forbid lifecycle in relation) | `test_event_specific_schemas` | **PASS** |
| Full lifecycle causal ordering with identical timestamps (`dispatch` -> `return` -> `intake` -> `resolve`) | `test_causal_full_lifecycle_with_identical_timestamps` | **PASS** |
| Declared `peer`, `reports_to`, `owner` relations coexist with runtime spawn hierarchy | `test_declared_peer_coexists_with_spawn_hierarchy`, `test_reports_to_and_owner_relations_and_explain` | **PASS** |
| Every phase, obligation, incident, relationship explainable with evidence | `test_explain_projection`, `test_reports_to_and_owner_relations_and_explain` | **PASS** |
| No elapsed-time threshold creates a verdict | Reduction table deterministic rule verification | **PASS** |
| Strict read-only invariant (zero file creations, modifications, deletions) | `test_filesystem_write_guard_read_only_invariant`, `test_read_records_is_strictly_read_only_and_does_not_delete_stale` | **PASS** |

---

## 2. Backward Compatibility & CLI Acceptance Matrix

| Requirement | Test Coverage | Status |
|---|---|---|
| Complete 25 top-level CLI command & alias set exact equality | `test_top_level_command_set_integrity` | **PASS** |
| Existing `subagent tree/list/show/log` commands pass | `test_cli_tree_and_list_json`, `test_cli_log_and_exchanges` | **PASS** |
| Legacy JSON `status` field remains intact | `test_characterization_legacy_subagent_json_shape`, `test_legacy_status_and_additive_runtime_state` | **PASS** |
| Additive `runtime_state` field provided | `test_legacy_status_and_additive_runtime_state` | **PASS** |
| Neutral `○ stopped` text rendering for stopped nodes | `test_stopped_subagent_never_renders_completed_word_in_text` | **PASS** |
| Statusline dashboard workflow panel + subagent fallback | `test_statusline_workflow_panel_and_fallback` | **PASS** |
| Header padding clean marker (no double `▪ ▪ workflow`) | `test_statusline_workflow_panel_and_fallback` | **PASS** |
| Wheel force-include contains `docs/observe.md` | `test_every_page_is_force_included_in_the_wheel` | **PASS** |
| Shipped documentation reachable via `charter docs show observe` | `test_a_topic_reads_back_its_page` | **PASS** |
| Side-by-side actor view rendering & wide-char safety (`--runtime-tree`, `--declared-relations`) | `test_actor_view_side_by_side_and_flags`, `test_observe_actors_json_filtering_and_mutually_exclusive_flags` | **PASS** |
| Timeline work and actor filtering (`--work`, `--actor`) | `test_timeline_work_and_actor_filtering` | **PASS** |
| Timeline JSON privacy sanitization (`prompt`, `content`, `arguments`, `output` elided by default) | `test_timeline_json_privacy_sanitizes_prompt_and_content` | **PASS** |
| Watch mode error degradation without crashing | `test_watch_mode_handles_anomaly_without_crashing` | **PASS** |

---

## 3. Structural and Performance Guarantees

1. **Strict Metadata Validation:** Line-anchored fence matching, integer `schema=1` validation, event-specific schema enforcement, string type constraints, non-line-anchored and 4-backtick rejection (`test_strict_schema_types`, `test_unhashable_and_invalid_field_types`, `test_four_backticks_and_unanchored_fence_rejection`, `test_event_specific_schemas`).
2. **Causal Source Event Ordering:** Multi-level event sorting with declaration subtype priorities (`dispatch: 20`, `start: 30`, `return: 80`, `intake: 82`, `resolve: 85`, `stop: 90`) and source ordinals incorporated in deterministic SHA-256 event IDs (`test_causal_source_ordering_with_identical_timestamps`, `test_causal_full_lifecycle_with_identical_timestamps`).
3. **Session Inflight Isolation:** Inflight dispatch records strictly isolated by root session scope and `tree_scope` (`test_inflight_isolation_between_two_root_sessions`, `test_inflight_cross_root_unscoped_isolation`).
4. **Clean Read-Only Operations:** All inflight observation queries (`live()`, `read_records()`, `live_records()`) default `prune=False` and never delete or modify files during reads (`test_read_records_is_strictly_read_only_and_does_not_delete_stale`, `test_filesystem_write_guard_read_only_invariant`).
5. **Zero Network / Subprocess Calls:** The observer path uses 100% standard library file I/O; zero subprocesses or network requests are executed.

---

## 4. Test Suite Execution Summary

```text
Ran 82 tests across tests.test_subagent, tests.test_workflow_view, tests.test_observations, tests.test_commands_observe.
All 82 tests passed with 0 failures, 0 errors.
```
