# Frozen Read-Only Workflow Observer Semantic Fixtures

These fixtures capture rollout and trace data for the key semantic edge cases defined in ADR 0017 and the workflow observer plan.

## 1. `returned-needs-intake.jsonl`

- **Scenario:** A parent session dispatches work (`SCDMP-B2`) to a child agent (`Gauss`). The child performs actions and emits `task_complete`.
- **Expected semantics:**
  - Runtime state: `stopped`
  - Work phase: `returned`
  - Open obligation: `intake_required` owned by the dispatching coordinator (`ROOT`)
  - Closed obligation: `return_expected` from Gauss is fulfilled and closed
  - Authority check: Work is **not** `completed` or `resolved`; it requires coordinator intake.

## 2. `stopped-without-return.jsonl`

- **Scenario:** A parent session dispatches work to `Euler`. Euler runs tools, but the runtime stops (e.g. `SubagentStop` or process termination) without emitting `task_complete`.
- **Expected semantics:**
  - Runtime state: `stopped`
  - Work phase: `active`
  - `return_observed`: `false` (renders as "return not observed")
  - Open obligation: `return_expected` remains open (owned by Euler)
  - Authority check: Charter must **not** manufacture success or failure; the stop is a runtime observation only.

## 3. `free-text-blocked-is-not-state.jsonl`

- **Scenario:** An active subagent (`Noether`) sends an `agent_message` containing free-text prose such as `"blocked by timeout, task done, error in dependencies"`. No typed error field and no `task_complete` event exist.
- **Expected semantics:**
  - Runtime state: `running`
  - Work phase: `active` (unchanged)
  - Incidents: empty (no typed incident field)
  - Open obligation: `return_expected` remains open
  - Authority check: Free-text prose never changes workflow state or creates incidents.
