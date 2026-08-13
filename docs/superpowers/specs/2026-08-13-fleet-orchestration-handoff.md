# Handoff — fleet orchestration

**Date:** 2026-08-13 · **Repo state:** `main` @ `ee83471`, v0.26.0 · **Status:** pre-spec. Do not implement.

**Pick up with `/grill-with-docs`.** This file is input to that interview, not a design.
Two of the four sub-problems changed shape in the last five minutes of the previous
session, which is the evidence that the vocabulary is not settled yet.

---

## The ask

Make agent development with charter **more autonomous, more reliable, smoother** for
**any team using charter on their own repos** (not charter's own development — that was
asked and answered).

Scope narrowed by interview to **parallel fleets working one task**, covering all four
failure modes rather than picking one:

1. **Assignment** — nothing claims work. Two agents take the same piece and clobber each
   other; a third piece is taken by nobody.
2. **Visibility** — the status line shows worktrees exist, not who is working on what,
   since when, or whether one is stuck.
3. **Integration** — N worktrees produce N branches. Nothing sequences the merges,
   detects that two touched the same file, or rolls the result into one PR. Fan-out is
   easy; fan-in is manual.
4. **Failure** — an agent hits a denial, times out, or exits mid-task. Nothing marks the
   piece failed or reassignable, so a fleet completes 7 of 8 and reports success.

Plus a stated requirement, in the user's words: charter should

> take smart control … but simultaneously not have hard orchestration logic … we don't
> need one hardcoded orchestration scenario that will work always, but dynamic
> orchestration planning.

And: **"nothing will drift and be missed."**

---

## The hard constraint that shapes everything

**charter has no model.** Stdlib-only, zero runtime dependencies, no LLM calls, by
design. charter *cannot* understand requirements.

So "dynamic orchestration" cannot be intelligence inside charter. It has to sit at a
seam: **the agent plans; charter enforces.** The agent decides what the pieces are;
charter guarantees that once declared, no piece is duplicated, lost, or silently failed.

This is the posture charter already takes with personas — a human writes the charter,
charter enforces `draft:` gating and generates the sub-agent. No orchestration logic for
charter to keep current.

**Open question for grilling:** is that seam correct, or does it just relocate the
problem? If the agent authors the plan, what stops a *bad* plan (overlapping pieces,
missing pieces) — and is catching that charter's job or not?

---

## The finding that stopped the previous session

Read `charter/worktree.py` lines 1–20 before designing anything.

**1. charter already has the noun.** Worktrees live at
`workspaces/<ws>/.worktrees/<repo>/<piece>`. **"piece" is literally the path segment.**
A fleet piece *is* a worktree. The previous session was about to invent a second noun
for a concept the domain model already carries.

**2. The module forbids the obvious design.** Its opening docstring:

> **Git is the only registry.** Nothing here writes state: every read shells out to
> `git worktree list --porcelain` and parses it, so a worktree created by hand with plain
> git is visible to `charter`, and one removed by hand cannot leave `charter` reporting a
> stale entry. The alternative — recording worktrees in workspace state — would introduce
> a marker that can disagree with reality, a failure mode this repo has already been
> bitten by.

A claims ledger in workspace state is exactly that marker. The recommended approach
(below, Approach A) contradicts the documented stance of the module it would extend.

---

## Approaches considered (none accepted)

**A. Declared plan + claims ledger.** Agent authors a plan (N pieces) into the workspace;
claims and outcomes are append-only records with a lease; `charter fleet status`
reconciles plan vs claims vs outcomes. Reuses the `dispatch.py` concurrency pattern
(`O_APPEND`, hostname in filename, no locks — see `charter/dispatch.py:17,74`).
**Blocked by the finding above.**

**B. Extend todos into work items.** Add owner/state/claim to workspace todos. Cheapest,
no new subsystem. **Conflicts with ADR 0004** (intent is its own store) and **ADR 0005**
(no persona todos) — a todo is a note about the future, a piece has an owner and a
lifecycle. Better as a *seam*: a todo spawns a fleet, intent above, execution below.

**C. Git-native — branches are the ledger.** Derive everything from git; zero new state.
Cannot represent "claimed but nothing committed yet" and cannot heartbeat, so a dead
agent is indistinguishable from a slow one — it fails at exactly sub-problem 4.

**D. Git refs as claims (emerged from the constraint, least explored, most promising).**
Git has native atomic compare-and-swap on refs:
`git update-ref refs/charter/claim/<piece> <new> <expected>` succeeds for exactly one
racer. Distributed, no lock, no corruptible file, and pushing the ref makes the claim
visible on every machine. Reflog timestamps could serve as heartbeat; outcomes could be
refs or notes. **"Git is the only registry" survives intact.**

Unexamined risks in D: ref namespace pollution; whether reflog is a legitimate heartbeat
or an abuse of it; what happens with no remote (LOCAL workspaces); whether a stale claim
can be broken safely; and whether `git worktree list --porcelain` plus refs really covers
visibility without any charter-side state.

---

## Vocabulary to settle first (this is why it needs grilling)

- Is a **piece** a worktree with an owner, or a distinct concept that *maps* to a worktree?
- Does **fleet** earn a place beside workspace / worktree / persona — or is a fleet just
  "a workspace with N claimed pieces," needing no new noun?
- What is the relationship between a **todo** (intent) and a **piece** (execution)?
- Does a piece have a **persona**, or does a persona claim a piece?
- What is the terminal state set for a piece, and who writes it?

`/domain-modeling` is the right tool if these stay fuzzy. Note this repo already keeps
ADRs (`docs/adr/0001`–`0009`); a decision like "git remains the only registry for claims"
is ADR material.

---

## Route from here (Matt's map)

Main flow: **`/grill-with-docs`** → **`/to-spec`** → **`/to-tickets`** → `/implement` per
ticket, `/clear` between. Keep grilling → spec → tickets in **one unbroken context
window** (the ~150k smart zone); the previous session was well past it, which is why it
stopped here rather than pushing on degraded.

`/grill-with-docs` rather than `/grill-me`: working directory present, stateful, leaves a
`CONTEXT.md` paper trail.

---

## Context worth carrying

- **Existing substrate to build on:** hooks on all six lifecycle points (SessionStart,
  UserPromptSubmit, PreToolUse, PostToolUse, Stop, SubagentStop); `trace.py` records
  `deny`/`ask`/`allow`/`secret-warn`/`dispatch`/`config-update`/`commitment-gate`/`memory`/`note`
  per session; `dispatch.py` tallies agent name + date.
- **What is absent everywhere:** *outcome*. Nothing records whether a run succeeded, what
  it changed, or how to resume it. That is the spine of both autonomy and reliability.
- **Posture to preserve:** `charter worktree remove` refuses to lose uncommitted or
  unpushed work. Integration should report overlap and hand the decision back — never
  auto-resolve a conflict.
- **Open issues from the same session, unrelated to this design but touching worktrees:**
  #90 (vault guard is Bash-only), #91 (`workspace remove` destroys worktrees silently),
  #92 (doctor hint names a command that cannot fix workspace bases), #93 (no workspace
  curation path), #94 (`recall` returns neither path nor body). **#91 is worth fixing
  before fleets** — a fleet multiplies worktrees, and that bug destroys them silently.
