# Read-Only Workflow Observer

Charter provides a read-only workflow observation layer that tracks subagents, collaborative threads, obligations, incidents, and declared workflow roles across Codex and other harnesses.

Charter separates mechanical runtime observations from exact structured declarations and deterministic projections—**without acting as a supervisor, scheduler, retry engine, or transition authority.**

---

## 1. What `observe` Can and Cannot Know

| Category | What Charter Knows | What Charter Refuses to Guess |
|---|---|---|
| **Runtime Activity** | Process thread starts, stops, tool calls, and message events from rollout files, Hook traces, and inflight records. | Does not assume a stopped process succeeded, failed, returned work, or timed out. |
| **Work Lifecycle** | Reconstructs assignments from mechanical `spawn_agent` dispatches and exact `task_complete` returns. | Never infers completion or acceptance from green test output, elapsed time, or absence of errors. |
| **Obligations** | Deterministically projects open `return_expected` (child owes return) and `intake_required` (coordinator owes intake). | Does not automatically close or acknowledge obligations without exact declarations. |
| **Incidents** | Surfaces typed error fields and Hook incidents as observable evidence. | Free-text prose such as "blocked", "failed", or "error" in messages never alters workflow state. |
| **Relationships** | Displays runtime spawn topology alongside exact declared peer/reporting relationships. | Never turns a runtime spawn tree into an organizational reporting structure. |

---

## 2. Orthogonal Dimensions: Runtime vs Work vs Obligation vs Incident vs Relationship

Charter answers five distinct questions without collapsing them into a single ambiguous status field:

```text
1. Runtime:      STARTING | RUNNING | STOPPED | UNKNOWN
2. Work Phase:   DISPATCHED → ACTIVE → RETURNED → INTAKEN → RESOLVED
3. Obligation:   return_expected (owned by child) | intake_required (owned by coordinator)
4. Incident:     Typed error fields recorded as evidence beside work state
5. Relationship: Runtime spawn_parent vs declared peer / reports_to / owner
```

---

## 3. Exact Meanings of Core Lifecycle States

- **`RETURNED`**: The child thread explicitly emitted a Codex `task_complete` event. This closes the child's `return_expected` obligation and immediately opens `intake_required` for the dispatching coordinator. It does **not** mean the work is accepted, completed, or resolved.
- **`INTAKE_REQUIRED`**: The dispatching coordinator is expected to inspect the return and emit an exact `charter-observe` intake declaration.
- **`STOPPED / return not observed`**: The runtime thread stopped (e.g. process termination or Hook `SubagentStop`) without emitting `task_complete`. The work remains in its prior phase (`active` or `dispatched`) and `return_expected` remains open. Charter manufactures neither success nor failure.

---

## 4. Command Reference

### `charter observe` / `charter observe position`

Displays the position table of workflow items, assigned actors, runtime condition, lifecycle phase, next obligation, and age:

```bash
charter observe
charter observe position --direction SCDMP --role EM --active-only
charter observe position --json
charter observe position --watch --interval 5.0
```

### `charter observe obligations`

Displays all open obligations owed between coordinators and actors:

```bash
charter observe obligations
charter observe obligations --kind intake_required
charter observe obligations --owner "Operational Root" --json
```

### `charter observe actors`

Displays declared workflow structures side-by-side with runtime spawn topology:

```bash
charter observe actors
charter observe actors --plain
charter observe actors --json
```

### `charter observe timeline`

Displays chronological mechanical and declared observation events:

```bash
charter observe timeline
charter observe timeline --work SCDMP-B2 --tool-calls
charter observe timeline --include-content
```

### `charter observe explain <projection-id>`

Explains any work item, obligation, actor, or incident with its full machine-readable evidence trail:

```bash
charter observe explain SCDMP-B2
charter observe explain obl:work:a10f...:intake_required --json
```

---

## 5. Exact `charter-observe` Metadata Schema

Structured metadata is recognized only inside exact fenced blocks in prompts or messages:

````text
```charter-observe
{
  "schema": 1,
  "event": "dispatch",
  "work_id": "SCDMP-B2",
  "title": "B2 closure review",
  "direction": "SCDMP",
  "actor_role": "EM",
  "owner_role": "ROOT"
}
```
````

### Supported Events and Fields

- `schema`: Must be `1`.
- `event`: `"dispatch"`, `"intake"`, `"resolve"`, `"relation"`.
- `work_id`: Unique identifier for the assignment (required for `intake` and `resolve`).
- `title`: Short human-readable title.
- `direction`: Strategic direction or workstream name (e.g. `SCDMP`, `SGSP`).
- `actor_role`: Assigned role (e.g. `EM`, `CM`, `Scout`).
- `owner_role`: Coordinator role (e.g. `ROOT`).
- `relation`: `"peer"`, `"reports_to"`, `"owner"`.
- `related_actor_id`: Target actor ID for relations.

### Validation Rules

- Invalid JSON, duplicate keys, unknown schema, unknown fields, or multiple blocks per message produce no declaration and trigger a warning.
- Metadata blocks larger than 4096 bytes are ignored with a warning.
- `intake` and `resolve` must be authored by the mechanically observed dispatching coordinator. A child-authored intake declaration is ignored.
- `resolve` is invalid before `intake`.

---

## 6. Evidence Badges and `observe explain`

Every derived claim carries machine-readable evidence references:

- `[OBS]`: Raw mechanical observation from rollout metadata, rollout event, Hook trace, or inflight tracker.
- `[DECL]`: Exact structured declaration from a valid `charter-observe` block.
- `[DER]`: Deterministic derivation computed according to frozen lifecycle reduction rules.

Using `charter observe explain <id>` outputs the exact source file path, line number, timestamp, and raw event kind for every evidence reference.

---

## 7. Privacy and Content Behavior

- By default, `charter observe timeline` and `charter observe timeline --json` elide raw prompt/message content.
- Full message text is displayed only when `--include-content` is explicitly passed.
- Raw prompt bodies are never persisted by the observer.

---

## 8. Statusline Dashboard Integration and Fallback

- The main Charter statusline builds at most one observation snapshot and one workflow projection per render.
- If work items exist, the middle panel displays `▪ workflow` prioritizing open intake obligations, active work, and return-missing notices.
- If only raw subagent sessions exist without reconstructed work items, the dashboard seamlessly falls back to the classic `subagents` tree.
- If no subagents exist, the standard two-column repository/persona layout is preserved.

---

## 9. Non-Goals and Anti-Orchestration Rules

The workflow observer strictly avoids becoming an orchestrator:

- No autonomous process dispatching, retrying, waking, or killing.
- No mailbox, queue mutation, or message delivery protocols.
- No automatic intake or resolution of tasks.
- No heuristic ownership inference from prompt keywords or file paths.

---

## 10. Troubleshooting

- **Missing rollout file:** Ensure `$CODEX_SESSIONS_PATH` or `$CODEX_HOME` points to the directory containing session rollout JSONL files.
- **Ambiguous return binding:** If multiple dispatches occurred to the same subagent without temporal distinction, returns remain unbound to prevent misattribution.
- **Unbound declaration warning:** If a child agent or unrelated session attempts to emit an `intake` or `resolve` declaration, it is preserved in `unbound_events` with a warning because only the dispatching coordinator has intake authority.
