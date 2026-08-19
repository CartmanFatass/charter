# observation is not authority

Charter visualizes and tracks agent execution across multiple harnesses. When agents spawn
subagents, collaborate, or complete tasks, there is a natural temptation for an observability
tool to infer progress, assume ownership, declare tasks completed or failed, retry stalled
workers, or automatically acknowledge returns.

This ADR draws a permanent boundary: **Charter observes facts, records exact declarations,
and computes deterministic derivations—it never authorizes decisions or transitions.**

## The four namespaces

To prevent conflating raw evidence with authoritative decisions, Charter separates its model
into four strictly separated namespaces:

1. **Observed fact (`mechanical`):** Raw mechanical evidence directly recorded from Codex
   rollout files, Hook trace events, or inflight tracker files. Examples include
   `session_meta`, `spawn_agent`, `user_message`, `agent_message`, `task_complete`,
   `subagent_start`, `subagent_stop`, and typed error fields in rollout payloads.
2. **Exact declaration (`declared`):** Explicit structured declarations enclosed in exact
   `charter-observe` fenced blocks authored by participants. Examples include a dispatching
   coordinator declaring work title/direction/role, an exact peer relationship, or a coordinator
   declaring intake or resolution.
3. **Derived projection (`derived`):** Deterministic, machine-explainable state reductions
   computed from observed facts and exact declarations. Examples include work item lifecycle
   phases (`dispatched`, `active`, `returned`), open obligations (`return_expected`, `intake_required`),
   and runtime condition (`starting`, `running`, `stopped`, `unknown`). Every derived item
   carries explicit machine-readable evidence references.
4. **Authorized decision (Refused by Charter):** Operational decisions such as retrying failed
   work, dispatching new subagents, waking parent sessions, acknowledging returns without an
   explicit declaration, reassigning ownership, or declaring work resolved.

**Charter implements the first three namespaces and strictly refuses the fourth.**

## Target lifecycle and reduction table

The work lifecycle is:

```text
DISPATCHED → ACTIVE → RETURNED → INTAKEN → RESOLVED
```

Runtime condition is orthogonal:

```text
STARTING | RUNNING | STOPPED | UNKNOWN
```

The deterministic state reduction rules are:

| Evidence | Runtime | Work phase | Obligation change |
|---|---|---|---|
| `spawn_agent` | starting / unknown | dispatched | Open `return_expected` owned by child |
| Activity / tool / start | running | active | Keep `return_expected` |
| Typed incident | Unchanged | Unchanged | Surfaced as incident evidence |
| Free-text "blocked/error/done" | Unchanged | Unchanged | Unchanged |
| `SubagentStop` without return | stopped | Unchanged | Keep `return_expected`; render "return not observed" |
| `task_complete` | stopped | returned | Close `return_expected`; open `intake_required` owned by coordinator |
| Coordinator-authored `event=intake` | Unchanged | intaken | Close `intake_required` |
| Coordinator-authored `event=resolve` | Unchanged | resolved | No generic open obligation |

## Non-inference rules

Charter enforces the following non-inference guarantees:

1. **No guessed ownership:** Owner, direction, and actor role may only be displayed when
   explicitly declared via structured metadata. Charter never guesses an owner from prompt
   prose, file paths, repository names, persona descriptions, or keywords.
2. **No organizational hierarchy from spawn topology:** Runtime parent-child spawn trees
   represent thread execution topology, not organizational reporting structure. Declared
   peer relationships (e.g. EM Gauss and CM Curie) coexist with runtime spawn edges without
   altering execution topology.
3. **Free-text prose has zero authority:** Words like `error`, `failed`, `blocked`, `timeout`,
   or `done` appearing in prompts, agent messages, or tool outputs never alter work lifecycle
   phase, runtime state, or obligations.
4. **Stop is not return:** A `SubagentStop` hook or process exit indicates only that the
   runtime thread stopped. It does not mean the work completed, succeeded, or failed.
   If no `task_complete` was emitted, the work remains in its prior phase with "return not observed".
5. **Return is not completion:** `task_complete` moves work to `RETURNED` and opens an
   `intake_required` obligation on the dispatching coordinator. It never marks work `COMPLETED`
   or `RESOLVED`.
6. **No silence or time-based verdicts:** No elapsed time threshold converts inactivity or
   silence into `failed`, `timed out`, `abandoned`, or `returned`. Age is displayed only as
   descriptive evidence.
7. **No child or third-party self-resolution:** An `intake` or `resolve` declaration is valid
   only when authored by the mechanically observed dispatching coordinator of that specific
   work item. Declarations from children or unrelated sessions are rejected as unbound.
8. **No resolve before intake:** A `resolve` declaration without prior `intake` does not advance
   work phase.
