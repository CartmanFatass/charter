# plane-shape

> **Living charter** for this workspace — its north star and shared context.
> Keep it current as the work evolves (edit this file, or `charter workspace vision "…"`).
> It's committed + shared for LIVE workspaces, and a fork inherits it — so anyone
> can pick up the task with full context. Never put secrets here (vault only).

## Vision

Remove the embedded plane shape entirely so charter has one flow: fleet, always. A workspace holds clones; the plane root holds the control plane and nothing you work in. Removal alone does not fix the branch-thrashing that prompted it — fleet keeps a root tree too — so the work also makes the plane root a place charter discourages working in, via the status line and doctor.

## Context & decisions

<!-- Key facts, constraints, and design/architecture decisions found while working —
     the durable "why", not a chronological log. Grow this as you learn. -->

**One flow, chosen for uniformity — not because embedded was unsound.** This is worth
recording precisely, because the reasoning that prompted the change and the reasoning that
justifies it are different. The symptom was two workspaces thrashing each other's branches.
The cause was not the embedded shape: `own_tree` already gives every embedded workspace its
own worktree, and that fix predates this work. The cause was two holes underneath it — the
`default` workspace owns the plane root outright, and selecting a workspace does not move
the session into its tree, it only prints `cd … && claude` and trusts you. A session that
never cds edits the root no matter which workspace it claims.

**The removal closes the first hole and not the second, and an early reading of this had it
wrong.** `own_tree` is what made `default` the plane root, so deleting it ends that. And a
fleet plane's `repo_trees` is its clones and nothing else — `root_tree()` returns `None` in
any shape but embedded, verified by running it against a scratch fleet plane rather than by
reading the branch. So afterwards charter never *presents* the plane root as a tree. What it
still cannot do is *prevent* a session from working there, because the plane root is a real
git repo in a real directory regardless of what charter lists. Not presenting is not
preventing, and that gap is the whole of ADR 0008.

**Therefore the removal ships with plane-root protection, or it changes nothing the user
would notice.** The status line marks the root as infrastructure rather than drawing it as
a peer of the repos you edit, and `doctor` gains a check that the root is clean and on its
default branch. Warning first, prevention later: refusing outright needs care about which
commands count, so the sequence is honest rather than timid.

**No backward compatibility.** The `shape` key and the whole `[plane]` section are deleted
rather than read-to-refuse; an existing `shape = "embedded"` becomes an ignored unknown key.
No migration path, no enumeration of materialised worktrees, no deprecation window. This
plane is itself embedded, so the cost lands here first — `charter.toml` loses `[plane]` and
its self-exclude, and the live worktree becomes unmanaged. It holds nothing unique, which
is what makes the bluntness affordable.

**charter develops itself through a clone of itself.** The self-exclude in `charter.toml`
exists only because "the repo is already here, as the plane's root tree", which stops being
true. Worth stating as a gain rather than a cost: under this design the session that
designed it would have made every branch inside a workspace clone, and the plane root would
have stayed on `main` throughout — which is exactly the outcome that was wanted.

**The solo-user path gets one step longer, deliberately.** `own_tree` promises today that
"a solo user's experience stays `charter init` and work in your repo". That promise dies:
one person with one repo must now clone it into a workspace. `charter init` inside a repo
offers to make that first clone immediately, so the step is met once during setup rather
than discovered as "where did my code go?" on first use.

**Worktrees are not embedded machinery and stay.** `charter/worktree.py` and
`commands_worktree.py` contain no shape checks at all — a worktree of a clone is as valid in
fleet as anywhere. The 57 `embedded` mentions overstate the removal.

### Constraints found in the code

- **`repo_trees` branches on shape; everything downstream does not.** Fleet returns
  `root_tree() + clones(ws)`, embedded returns `own_tree(ws) + clones(ws)`. Collapsing to
  the fleet arm deletes `own_tree` and the concept of a workspace *being* a tree.
- **`doctor` has two embedded paths**: it skips the inventory check entirely for embedded
  planes, and `check_embedded_worktrees` guards against worktrees nesting inside the
  codebase where nx/jest/maven recurse into them. The first becomes unconditional; the
  second has no fleet equivalent and is replaced by the plane-root check.
- **`charter init` auto-detects**: `shape = shape or ("embedded" if detected else "fleet")`.
  With one shape the detection goes; being inside a git repo stops changing what init does.
- **~966 lines of embedded-specific tests** across three modules, some of which convert to
  fleet tests rather than being deleted.

## Glossary

<!-- Task/domain vocabulary so a teammate or a fork isn't lost: `term` — definition. -->

- `plane root` — the directory holding `charter.toml`. After this work it holds the control
  plane and nothing anyone works in.
- `root tree` — the plane root regarded as a git repo. It still exists and is still listed;
  the change is that it is marked as infrastructure rather than drawn as a work target.
- `workspace tree` — a tree a workspace owns. After this work, always a clone; today it can
  also be a worktree, or (for `default` in an embedded plane) the plane root itself.
- `fleet` — the surviving shape, and the user's "multi-repo approach": the plane is its own
  directory and workspaces hold clones. Once it is the only shape the word may fade, but it
  is the right word while both still exist in the reader's memory.
- `embedded` — the shape being removed: charter serving the codebase it sits inside, with
  workspaces holding worktrees of it.
- **"main repo" is not used.** It was doing two jobs in the original request — the plane
  root, and the repo a workspace works in. Those are `plane root` and `workspace tree`.

## Log

Chronological "what was done" lives in the task memo — `memory/notes.md`
(append with `charter workspace note "…"`).
