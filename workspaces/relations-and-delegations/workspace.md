# relations-and-delegations

> **Living charter** for this workspace — its north star and shared context.
> Keep it current as the work evolves (edit this file, or `charter workspace vision "…"`).
> It's committed + shared for LIVE workspaces, and a fork inherits it — so anyone
> can pick up the task with full context. Never put secrets here (vault only).

## Vision

DELIVERED in charter 0.44.0 — both gaps this workspace was opened for are closed, and the problem statement is kept below as the why.

THE PROBLEM (as of 2026-08-18, charter 0.43.2). (1) A session knew nothing about the OTHER workspaces, so a change made elsewhere arrived as a surprise with nothing to connect it to. (2) Delegation was advisory-only: delegate-when was prose in a generated agent file, no plane could declare its front door anywhere a consumer would look, and the tally showed 3 dispatches EVER, with statusline/release/forge at zero — while 'uses:' silently granted another persona's tools, so doing their work cost less than handing it over.

WHAT WAS DECIDED (grilled over five rounds, 2026-08-18). The persona is the only authority on delegation. charter learns exactly one neutral fact — which persona a plane declares as its default — and nothing about 'steward'. There is NO plane-level routing policy: the level is read from the one acting persona per session, so a floor would reach personas that never asked for it; the default a new plane wants arrives instead in a generated file the consumer owns. And charter never guesses which persona owns a prompt (ADR 0016) — it presents the roster and lets the reader route.

WHAT SHIPPED. Front door in charter.toml + per-session/per-terminal persona pointers (#259) · per-persona routing: off|advise|require, routes-to:, the roster block, fired-vs-followed measurement (#260) · init --front-door and its generic template (#267) · require's tool-time ask, never a deny (#263) · borrows: splitting what uses: overloaded (#264) · the other-workspaces digest, knowledge and never instructions (#265). Three adjacent bugs found on the way: the leak guard denying writes that merely mention a guarded path (#266), doctor reading 'enabled' as 'loaded' (#268), and workspace selection leaving no trace at all (#269, out of #254 — which was NOT a defect).

WHAT WAS DELIBERATELY NOT DONE. Neighbour DELIVERIES (commits, PRs) are not reported: that costs a git log per workspace on every session start to answer a question the reader can now ask themselves. No plane-level [routing] section, and no [charter] version lock on this plane.

WHAT IS STILL UNKNOWN. Whether any of it works. The mechanism shipped; the verdict is a number that does not exist yet — 'charter persona stats' now prints 'routing advice fired N · M dispatches followed'. Advice that fires and is never followed means the roster block failed, not that the roster is wrong. Read it that way before adding more personas.

## Context & decisions

<!-- Key facts, constraints, and design/architecture decisions found while working —
     the durable "why", not a chronological log. Grow this as you learn. -->

**Everything here shipped in charter 0.44.0.** This section is the durable *why* — the
facts that were expensive to establish and the decisions that rest on them. The Vision above
says what was delivered; the settled design tree, with its rejected branches, is below.

Facts found while scouting, expensive to rediscover:
* `charter init` seeded ZERO personas — `personas/steward/` was charter's own file, never a
  product default. (Fixed: `init --front-door` now scaffolds one generic persona.)
* A committed team-wide default already existed — `personas/.default` — **undocumented and
  unused even in this plane**. The gap was findability, not capability.
* Persona selection was plane-wide, not per-session/per-terminal like workspaces, so
  `charter persona use` in one pane changed every pane and every future session.
* `uses:` granted vault read + tool auto-approval + delegation in one word; the tool grant
  is what made not-delegating the cheapest path.
* `charter persona stats` on 2026-08-18: 3 dispatches ever, all `reddit`; statusline,
  release and forge zero — while all three linted green with correct adverts.

Load-bearing constraint, recorded as ADR 0016 before the mechanism existed: **charter
presents the roster and never guesses the owner.** A keyword matcher over `delegate-when`
is the obvious next improvement and the one thing that would break it — charter would be
asserting a conclusion it cannot justify, and the first confident wrong answer costs the
block its reader. Consequence accepted: `require` can only ever say "the roster was shown
and nothing was dispatched", never name a culprit.

One deviation from the design as settled: a dangling front-door declaration is `doctor`
**WARN**, not FAIL. This repo reserves FAIL for "you cannot work", and a plane with no
persona still clones and still reaches its forge. Loud was the requirement; a blocker was
not.

## Settled design — delegation control

Grilled with the engineer 2026-08-18 (`mattpocock-skills:grilling`, five rounds).
Every line below is a confirmed decision, not a proposal. Lives here rather than in a
separate file on purpose: `workspace._live_block` un-ignores only `workspace.json`,
`workspace.md`, `memory/**` and `todos/**`, so a stray `.md` beside them never travels —
not even when the workspace is LIVE.

### The question

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

### Status — shipped

All of it is on `main` and published in **charter 0.44.0** (tag `v0.44.0`, PyPI 200,
2550 tests green). Merged in this order:

| PR | What |
| --- | --- |
| #259 | `[persona] default`, per-session/per-terminal persona pointers, ADR 0016 |
| #260 | `routing:` levels, `routes-to:`, the roster block, fired-vs-followed |
| #267 | `init --front-door` and the generic template |
| #263 | `require`'s tool-time ask |
| #264 | `borrows:` splits `uses:` |
| #265 | the other-workspaces digest |
| #266 · #268 · #269 | the three adjacent bugs found on the way |
| #270 | the 0.44.0 release |

Issues closed: #254 (not a defect), #255, #256, #257, #258, #261.

## What the rollout actually taught

The five increments were planned as one PR each, and that held. Three things did not, and
they are worth carrying to the next stacked change:

* **Branch BEFORE committing the next increment.** Increment 2 was committed onto the
  increment-1 branch and pushed, which silently put work into PR #259 that its body did not
  describe. Fixed with a branch plus a `--force-with-lease` reset.
* **A squash merge rewrites the parent's commits**, so every child in a stack must be
  rebased with `git rebase --onto origin/main <old-parent-tip>`. A plain rebase re-applies
  them and conflicts.
* **Never delete a merged branch while an open PR still targets it.** GitHub closes that PR
  and it cannot be reopened once the base ref is gone — #262 died that way and had to be
  refiled as #267. Retarget the whole chain to `main` up front.

Method used throughout, and worth repeating: `superpowers:test-driven-development` (every
test written first and watched fail for the right reason), then
`superpowers:verification-before-completion` — each increment driven against a real scratch
plane, not only the suite. Implementation in a **clone**, never the plane root (ADR 0008).

## Glossary

<!-- Task/domain vocabulary so a teammate or a fork isn't lost: `term` — definition. -->

* **front door** — the persona a session adopts by default. A role, not a name: charter
  reads it from `[persona] default`, never from a constant.
* **inbound vs outbound** — `delegate-when` is inbound (what I accept when routed to);
  `routing:` is outbound (when I hand work away). Opposite directions, deliberately
  different words.
* **`routing:`** — per-persona level, `off | advise | require`. Absent means `off`.
* **`routes-to:`** — personas this one considers first. Priority, never restriction.
* **`borrows:`** — opt-in tool/vault borrowing, splitting what `uses:` overloaded.
  `borrows: none` borrows nothing; absent keeps the legacy grant.
* **roster block** — the prompt-time list of personas + their adverts, injected into the
  existing commitment-point gate. Facts only; no owner is named.
* **fired-vs-followed** — routing advice shown vs dispatches that followed, printed by
  `charter persona stats`. The number that can falsify this whole design, and the one
  thing here that is still unknown.

## Log

Chronological "what was done" lives in the task memo — `memory/notes.md`
(append with `charter workspace note "…"`).
