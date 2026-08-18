# relations-and-delegations

> **Living charter** for this workspace — its north star and shared context.
> Keep it current as the work evolves (edit this file, or `charter workspace vision "…"`).
> It's committed + shared for LIVE workspaces, and a fork inherits it — so anyone
> can pick up the task with full context. Never put secrets here (vault only).

## Vision

Two gaps in the control plane: (1) a session knows nothing about the OTHER workspaces — their recent activity and deliveries — so a change made elsewhere arrives as a surprise; give it that as read-only knowledge, never as logic. (2) delegation is advisory-only: delegate-when is prose in a generated agent file, steward is a per-developer gitignored active-file with no committed plane default, and the tally shows 3 dispatches ever (statusline/release/forge: zero) — decide what layer, if any, a consumer plane can use to CONTROL how much the front door delegates.

## Context & decisions

<!-- Key facts, constraints, and design/architecture decisions found while working —
     the durable "why", not a chronological log. Grow this as you learn. -->

**Point 2 (delegation) is DESIGNED, not built** — see `design-delegation.md` in this
workspace for the settled tree, the rejected branches and the five-increment rollout.
Grilled with the engineer 2026-08-18 over five rounds; every decision there is confirmed.

Headline: **the persona is the only authority on delegation.** charter learns one neutral
fact — which persona a plane declares as its default (`charter.toml` `[persona] default`) —
and nothing about `steward`. No plane-level routing policy: the level is only ever read
from the one acting persona per session, so a plane floor would reach personas that never
asked for it. The default arrives from the *generated* front-door template instead.

Load-bearing constraint: **charter never guesses which persona owns a prompt.** It injects
the roster (name, `delegate-when`, last dispatched) and states only what it can prove;
the model routes. A keyword matcher would have charter asserting a conclusion it cannot
justify (ADR 0009).

Facts found while scouting, expensive to rediscover:
* `charter init` seeds **zero** personas — `personas/steward/` is charter's own file.
* A committed team-wide default already exists: `personas/.default`, written by
  `charter persona default <name>`. **Undocumented** and unused even in this plane.
* Persona selection is **plane-wide**, not per-session/per-terminal like workspaces.
* `uses:` grants vault read + tool auto-approval + sub-agent delegation in one word; the
  tool grant is what makes not-delegating the cheapest path.
* `charter persona stats`: 3 dispatches ever, all `reddit`; statusline/release/forge zero.

**Point 1 (cross-workspace awareness) is PARKED with its shape agreed** — see the todo:
a SessionStart digest of the *other* workspaces (name + `workspace.md` vision line +
last-worked timestamp + open-todo count), read-only knowledge, never logic.

## Settled design — delegation control

Grilled with the engineer 2026-08-18 (`mattpocock-skills:grilling`, five rounds).
Every line below is a confirmed decision, not a proposal. Lives here rather than in a
separate file on purpose: `workspace._live_block` un-ignores only `workspace.json`,
`workspace.md`, `memory/**` and `todos/**`, so a stray `.md` beside them never travels —
not even when the workspace is LIVE.

## The question

Should charter hardcode steward-shaped routing, or should each persona declare its own
delegation strategy?

**Answer: the persona is the only authority.** charter owns no routing constant and no
plane-level routing policy. It knows one neutral fact — *which persona this plane declares
as its default* — and nothing about `steward`.

### Evidence that started it

* `charter init` seeds **zero** personas. `personas/steward/` is charter's own file, not a
  product default.
* The local active-persona file is gitignored **and plane-wide** — one file for every pane
  and every session (unlike workspaces, which have per-session *and* per-terminal pointers).
* A committed default *does* exist and was missed on first pass: `personas/.default`,
  written by `charter persona default <name>`, read after the local file. It is
  **undocumented** (absent from `README.md` and `docs/personas.md`) and **unused even
  here** — no such file in charter's own plane.
* `delegate-when` is inbound-only: an advert other agents read. Nothing anywhere expresses
  what a persona should hand **off**.
* `uses:` grants three things at once — read that persona's vault, **run its tools with
  auto-approval** (`persona.effective_tools`), and delegate to its sub-agent. The tool
  grant makes not-delegating the cheapest path.
* `charter persona stats`: **3 dispatches ever**, all to `reddit`. `statusline`, `release`,
  `forge` — never dispatched.

### Decisions

1. **Outbound axis.** Inbound is solved (`delegate-when` → the generated agent
   description). The new expression is *when I hand work away*.
2. **No new matching vocabulary.** No path/glob ownership map — steward's own charter
   already rejects it in prose (*"route on the work, not the file"*), and a second
   ownership map drifts from the adverts. `routes-to:` exists only to **prioritise**
   (never to restrict — a restriction silently hides personas created later).
3. **charter never guesses the owner.** When a work-shaped prompt arrives, the hook injects
   the **roster** (name · `delegate-when` · last-dispatched) and states only facts it owns:
   *these personas exist, this is what they claim, none was dispatched.* The model routes.
   No keyword matcher, no `triggers:` field — ADR 0009 (*errors classify, they do not
   guess*) and `curate.py`'s stdlib-only stance both point the same way.
4. **`charter.toml` gains exactly one key**, `[persona] default` — *who* the front door is,
   never *how* it behaves. `personas/.default` stays honoured for a release; `doctor` names
   the migration. `charter persona default <name>` writes the TOML.
5. **A dangling default is named, never silent.** Today `default_persona()` returns `None`
   for a missing name with no message anywhere. It becomes a `doctor` error plus a
   statusline finding. Silence is why `personas/.default` shipped and was never used.
6. **Persona selection mirrors workspaces** — per-session and per-terminal pointers, same
   precedence machinery. Today `charter persona use forge` in one pane changes the persona
   in every pane and every future session.
7. **Levels: `off | advise | require`**, declared per persona as `routing:`.
   * `off` — silent. **This is the default when the key is absent.**
   * `advise` — prompt-time roster block, ignorable.
   * `require` — the same, plus a tool-time **ask** on the first edit with no dispatch this
     turn. It **asks**, never denies: `toolgate.py` promises it never denies, and *hooks
     never break a turn* is a stated convention.
   Three, not four: the gap between `ask` and `require` is one nobody can restate without
   the docs, and a level nobody can distinguish gets set wrong.
8. **No plane-level routing policy.** The level is only ever read from **one** persona per
   session (the acting one — the gate is silent inside sub-agents), so a plane floor would
   apply to personas that never asked for it. The default arrives instead from the
   **generated front-door template**, which ships `routing: advise` in its own frontmatter —
   config carried by a file the consumer owns, deletes or rewrites.
9. **`init --front-door <name>`** (default `steward`) generates a **generic** front door and
   sets it as the plane default. The name lives in a flag, never in code. The template must
   teach *"here is how you route, and you have nobody to route to yet"* — a template that
   implies doing the work yourself is normal ships the failure this design exists to fix.
10. **`uses:` splits, per persona.** A persona that declares `borrows:` takes its tools from
    `borrows:` and `uses:` becomes a routing edge *for that persona only*; a persona that
    declares nothing keeps today's behaviour exactly. `borrows: none` opts out entirely
    (`vault: none` is the established spelling). No plane flag — one persona's opt-in must
    never change another's behaviour.
11. **Silence rules.** The block never fires when: the roster minus the acting persona is
    empty · the level is `off` · inside a sub-agent (a persona already routed to must not be
    told to route) · the shared cooldown is active.
12. **One gate, two sections.** The roster block extends the existing commitment-point nudge
    on `UserPromptSubmit` — same trigger, same 3-prompt cooldown. Two blocks on one prompt is
    how wallpaper is manufactured; that hook's own comment records the lesson.
13. **Measure fired-vs-followed.** An `advise`/`require` event goes into the existing
    dispatch store (counts and dates only, no prompt text) and `persona stats` prints
    *"routing advice fired N · dispatched M"*. The whole design is a bet that visibility
    changes routing; shipping without the number that could falsify it repeats the gap
    `dispatch.py` was written about.
14. **Nothing new in the status line.** A column showing the same word for months is a glyph
    people stop reading. `doctor` reports config; `stats` reports the ratio.
15. **The no-front-door hole, accepted knowingly.** A plane with personas but no declared
    default and no active persona gets no routing advice at all. `doctor` (not every prompt)
    reports *"N personas, no declared default — routing advice is inert"*.

### Rejected, with reasons

* **Plane-level `[routing]` floor + persona override** — action at a distance over personas
  that never declared anything; the "20 personas, 20 files" argument for it was wrong,
  because only a session-identity persona ever has its level read.
* **`[routing] borrow = legacy|explicit`** — a plane flag whose flip changes every persona;
  made per-persona instead (decision 10).
* **charter keyword-matching prompts to `delegate-when`** — charter asserting a conclusion it
  cannot justify; the first confident wrong answer discredits the whole block.
* **A `triggers:` field** — a second source of truth for "what I own", drifting from the prose
  advert nobody deletes.
* **Path/glob ownership** — contradicts the routing rule steward's charter already states.
* **`require` denying** — trades the one property protected everywhere else in this repo for
  enforcement that gets switched to `off` the first time a cross-cutting change is blocked.
* **Undeclared level defaulting to `advise`** — puts the default back in charter's code; the
  generated template carries it instead.

### Status

**All five increments are built** — a stack of PRs, each based on the one before:
#259 → #260 → #262 → #263 → #264. Merge in that order; GitHub retargets each as its
base merges. 2502 tests green at the tip of the stack. Point 1 (cross-workspace awareness) is built too — PR #265, based on the tail of the
stack. Both halves of this workspace's remit are now delivered; nothing is parked.

**Increment 1** — branch `persona-default-in-charter-toml` in this workspace's
clone (0d9db69, f0f5d0e, 19a2a2d), 2430 tests green, pushed. Issues #255–#258 are filed.
Increments 2–5 are the todos below. Point 1 (cross-workspace awareness) is still parked.

One deliberate deviation from the design as settled: a dangling front-door declaration is
`doctor` **WARN**, not FAIL. This repo reserves FAIL for "you cannot work", and a plane
with no persona still clones and still reaches its forge. Loud was the requirement; a
blocker was not.

## Rollout — five increments, one PR each

1. **ADR + `[persona] default` + per-session/per-terminal persona pointers + docs.** The ADR
   (*charter presents the roster; it never guesses the owner*) comes first — it is what stops
   increment 2 from quietly regrowing a keyword matcher later. Docs must cover
   `personas/.default`, which was never documented.
2. **`routing:` levels + the roster section inside the existing gate + tally event + `stats`
   line.**
3. **`init --front-door <name>` and the generic template** carrying `routing: advise`.
4. **`require` + the tool-time ask** (sub-agent exclusion verified by running it, not by
   reasoning about it).
5. **`borrows:`.**

Method for each: `superpowers:test-driven-development`, then
`superpowers:verification-before-completion`. Implementation happens in a **clone**
(`charter clone charter --workspace relations-and-delegations`) — ADR 0008, the plane root
is not a work tree.

## Glossary

<!-- Task/domain vocabulary so a teammate or a fork isn't lost: `term` — definition. -->

* **front door** — the persona a session adopts by default. A role, not a name: charter
  reads it from `[persona] default`, never from a constant.
* **inbound vs outbound** — `delegate-when` is inbound (what I accept when routed to);
  `routing:` is outbound (when I hand work away). Opposite directions, deliberately
  different words.
* **`routing:`** — per-persona level, `off | advise | require`. Absent means `off`.
* **`routes-to:`** — personas this one considers first. Priority, never restriction.
* **`borrows:`** — opt-in tool/vault borrowing, splitting today's overloaded `uses:`.
  `borrows: none` borrows nothing.
* **roster block** — the prompt-time list of personas + their adverts, injected into the
  existing commitment-point gate. Facts only; no owner is named.
* **fired-vs-followed** — routing advice shown vs dispatches that followed. The number
  that can falsify this whole design.

## Log

Chronological "what was done" lives in the task memo — `memory/notes.md`
(append with `charter workspace note "…"`).
