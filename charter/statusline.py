"""Claude Code status-line renderer for the control plane.

Wired via ``.claude/settings.json`` → ``statusLine``. Claude Code pipes a JSON
payload on stdin (session/model/workspace context) and renders this command's
stdout in the footer on every turn.

Contract we honor (see docs/workspaces.md): read *all* of stdin, stay fast (no
git subprocess, no network — branches are read straight from ``.git/HEAD``),
never raise (fall back to a minimal string), and exit 0. ANSI colour and
multiple lines are supported.

This module only *gathers* content (repos, branches, CI, personas) and
declares the layout; all width math lives in :mod:`charter.tui`, whose nodes
guarantee that no emitted line ever exceeds the terminal width — overflow is
truncated with ``…``, never wrapped (a wrap shears every column below it).

Note: Claude Code does **not** pass the session's environment to the status
line, so an ``$CHARTER_WORKSPACE``-pinned session shows the active-file/default here
even though its commands honor the env var. Cosmetic only.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from . import config, tui

# ANSI — status lines render escape codes.
_R, _DIM, _BOLD, _UNDER = "\033[0m", "\033[2m", "\033[1m", "\033[4m"
_CYAN, _YELLOW, _MAGENTA, _GREEN = "\033[36m", "\033[33m", "\033[35m", "\033[32m"
_BLUE, _RED = "\033[34m", "\033[31m"

_TREE_MID, _TREE_END = "├─ ", "└─ "
# Bounds the TOTAL rows `_repo_rows` returns — repo rows + each repo's one-line worktree
# summary + the trailing "…(+N more)" — not merely the repo count: a repo with worktrees
# emits 2 lines, so counting repos alone let the footer grow past its budget.
_MAX_REPO_LINES = 14  # keep the footer from growing unbounded

# Fixed column widths for the strict-table repo view (visible chars).
_NAME_W, _BRANCH_W, _CI_W = 32, 34, 12
_MR_W = 6  # fixed MR cell, so a right-hand persona column stays aligned
_GAP = "  "  # between repo-table cells
_COL_SEP = f" {_DIM}│{_R} "  # divider between the repos and personas columns
# Visible width of the whole left/repo block: "  " + "├─ " + name + gaps + branch + ci + mr.
_LEFT_W = 2 + 3 + _NAME_W + 2 + _BRANCH_W + 2 + _CI_W + 2 + _MR_W
_RIGHT_MIN_W = 36  # a persona column narrower than this is not worth showing
_SAFETY = 2  # render to (COLUMNS − this); a line filling the last column can wrap
_BRAND_GAP = 3  # min blank columns between content and the right-aligned brand
# Extra headroom the brand demands beyond `width`. A real session cropped the brand to
# `⬢ charter 0.10…` — impossible from `_with_brand`, which fits-or-drops — so the pane
# gives ~1 column less than COLUMNS advertises. The brand is the one thing that must
# never be half-rendered, so it alone pays this margin.
_BRAND_MARGIN = 2

#: GitLab pipeline status → (colour, glyph, label). Glyphs are single-width so
#: columns stay aligned.
_CI_MARK = {
    "success": (_GREEN, "✓", "passed"),
    "failed": (_RED, "✗", "failed"),
    "running": (_CYAN, "●", "running"),
    "pending": (_YELLOW, "○", "pending"),
    "created": (_YELLOW, "○", "pending"),
    "preparing": (_YELLOW, "○", "pending"),
    "waiting_for_resource": (_YELLOW, "○", "queued"),
    "scheduled": (_YELLOW, "○", "scheduled"),
    "manual": (_DIM, "‖", "manual"),
    "canceled": (_DIM, "⊘", "canceled"),
    "skipped": (_DIM, "»", "skipped"),
}


def _ci_part(status: str | None) -> str:
    """Coloured ``<glyph> <label>`` markup for a pipeline status ('' if none)."""
    if not status:
        return ""
    color, glyph, label = _CI_MARK.get(status, (_DIM, "?", status))
    return f"{color}{glyph} {label}{_R}"

#: Distinct colours cycled per repo (magenta, blue, cyan, green, yellow, then
#: bright variants). Assigned by position so adjacent repos always differ; a
#: repo keeps its colour across renders as long as the workspace's set is stable.
_PALETTE = (
    "\033[35m", "\033[34m", "\033[36m", "\033[32m",
    "\033[33m", "\033[95m", "\033[94m", "\033[92m",
)
def _active(session_id: str | None = None) -> tuple[str, str]:
    from . import workspace
    return (workspace.resolve(session_id=session_id),
            workspace.source(session_id=session_id))


def _count_workspaces() -> int:
    from . import workspace
    return len(workspace.list_workspaces())


# Cache-trend detection. A single cold turn is normal (you just switched model, compacted, or
# the session is warming up) — only a SUSTAINED cold streak means the prefix is churning every
# turn, which is the expensive failure mode. Same cadence discipline as the memory nudge:
# silent by default, speaks only with evidence, and goes quiet the moment it recovers.
_COLD_BELOW = 50     # a turn whose input is <50% cache-read counts as cold
_COLD_STREAK = 3     # consecutive cold turns before we say anything
_TREND_KEEP = 16     # ring buffer: recent turns retained per session


def _usage_file(sid: str) -> Path:
    return config.SESSIONS_DIR / f"{sid}.usage"


def _record_turn(sid: str, hit: int, read: int, write: int) -> list[int]:
    """Append this turn's cache-hit % to the session's trend and return the recent history.

    The status line can render several times per turn, so a sample is only appended when the
    underlying API numbers CHANGE — the payload reflects the most recent API response, so an
    identical (read, write) pair is the same turn re-rendered, not a new one."""
    if not sid:
        return []
    f = _usage_file(sid)
    try:
        rows = [ln for ln in f.read_text().splitlines() if ln.strip()]
    except OSError:
        rows = []
    stamp = f"{read},{write},{hit}"
    if rows and rows[-1] == stamp:            # same API response → same turn, don't double-count
        return [int(r.rsplit(",", 1)[1]) for r in rows]
    rows.append(stamp)
    rows = rows[-_TREND_KEEP:]
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("\n".join(rows) + "\n")
    except OSError:
        pass
    return [int(r.rsplit(",", 1)[1]) for r in rows]


def _history(sid: str) -> list[tuple[int, int]]:
    """The session's recorded (cache_read, cache_write) pairs."""
    try:
        out = []
        for ln in _usage_file(sid).read_text().splitlines():
            p = ln.split(",")
            if len(p) == 3:
                out.append((int(p[0]), int(p[1])))
        return out
    except (OSError, ValueError):
        return []


# A cache REBUILD is the expensive event, and it is invisible in the hit *ratio*: in steady
# state only the new exchange is written (~1–3k tok against a ~700k cached prefix), so the ratio
# sits at ~100% and a rebuild shows up as a single dipped turn you can easily miss. Measured on
# real sessions: one rebuild cost 696,088 tokens — ~139× everything charter/prompt trimming
# saves in a whole session. So track rebuilds explicitly and cumulatively.
# Signature: the read collapses (the prefix no longer matched) AND a large write replaces it.
_REBUILD_MIN_WRITE = 15_000   # tokens; normal turns write ~1–4k
_REBUILD_READ_DROP = 0.5      # read fell to <50% of the previous turn
_REBUILD_LOUD = 200_000       # cumulative rebuild cost that earns an explanation


def _rebuilds(rows: list[tuple[int, int]]) -> tuple[int, int]:
    """(count, total tokens) of prefix rebuilds in a session's (read, write) history."""
    n = cost = 0
    for i, (read, write) in enumerate(rows):
        if write < _REBUILD_MIN_WRITE:
            continue
        prev = rows[i - 1][0] if i else 0
        # a big write with a collapsed read = the prefix was rebuilt, not merely appended to
        # (reading a huge file also writes a lot, but the read stays high — not a rebuild)
        if i == 0 or read < prev * _REBUILD_READ_DROP:
            n += 1
            cost += write
    return n, cost


def _fmt_tok(n: int) -> str:
    return f"{n/1_000_000:.1f}M" if n >= 1_000_000 else (f"{n//1000}k" if n >= 1000 else str(n))


def _cold_streak(trend: list[int]) -> int:
    """How many of the most recent turns in a row were cache-cold."""
    n = 0
    for hit in reversed(trend):
        if hit < _COLD_BELOW:
            n += 1
        else:
            break
    return n


def _cache_hint(streak: int) -> str | None:
    """One short, actionable line — only once the cache has been cold for several turns."""
    if streak < _COLD_STREAK:
        return None
    return (f"{_RED}⚠ cache cold {streak} turns{_R}{_DIM} — model/effort switch or MCP toggle "
            f"churns the prefix; prefer {_R}{_BOLD}/rewind{_R}{_DIM} over /compact{_R}")


def _context_gauge(payload: dict) -> list[str]:
    """Live **context + prompt-cache health** from the status-line payload.

    Token efficiency is mostly decided by *prompt caching*: Claude Code re-sends the whole
    request each turn, and the API serves the unchanged prefix from cache at ~10% of the input
    rate. So the number that matters isn't how big the prompt is — it's what share of it is
    **read from cache** rather than re-written. A high read:write ratio means the prefix is
    stable; if cache *creation* stays high turn after turn, something keeps changing the prefix
    (a model/effort switch, an MCP server connecting, a plugin toggle, `/compact`).

    Renders ``ctx NN%`` (context window used) and ``⚡NN%`` (share of this turn's input served
    from cache). Both are absent early in a session and right after `/compact`, when the payload
    has no usage yet — we simply show nothing rather than a misleading 0."""
    cw = (payload or {}).get("context_window") or {}
    out: list[str] = []
    pct = cw.get("used_percentage")
    if isinstance(pct, (int, float)):
        col = _GREEN if pct < 50 else (_YELLOW if pct < 80 else _RED)
        out.append(f"{_DIM}ctx{_R} {col}{int(pct)}%{_R}")
    cu = cw.get("current_usage") or {}
    read = cu.get("cache_read_input_tokens") or 0
    write = cu.get("cache_creation_input_tokens") or 0
    if read or write:
        hit = round(100 * read / (read + write))
        # <50% sustained = the prefix is churning; that's the expensive failure mode.
        col = _GREEN if hit >= 80 else (_YELLOW if hit >= 50 else _RED)
        out.append(f"{col}⚡{hit}%{_R}")
        try:
            sid = payload.get("session_id") or ""
            trend = _record_turn(sid, hit, read, write)
            # Rebuilds are the dominant cost and are invisible in the ratio — surface them
            # cumulatively so the price of a mid-task switch stays on screen.
            n, cost = _rebuilds(_history(sid))
            if n:
                col = _RED if cost >= _REBUILD_LOUD else _YELLOW
                out.append(f"{col}↻{n} {_fmt_tok(cost)}{_R}")
            if cost >= _REBUILD_LOUD:
                out.append(f"{_DIM}rebuilt prefix — model/effort switch, MCP toggle or a resumed "
                           f"session; pick model+effort at session start{_R}")
            else:
                hint = _cache_hint(_cold_streak(trend))
                if hint:
                    out.append(hint)
        except Exception:
            pass          # diagnostics must never break the footer
    return out


def _stale_structure(ws: str) -> bool:
    """True if the active workspace's on-disk structure is behind the current layout
    (created by an older version of charter) → flag it with a reinit tip. Best-effort, fast."""
    try:
        from . import workspace
        return workspace.needs_reinit(ws)
    except Exception:
        return False


def _clone_dirs(ws: str) -> list[Path]:
    from . import workspace
    return workspace.clones(ws)


def _available() -> int:
    try:
        return json.loads(config.INVENTORY.read_text()).get("count", 0)
    except Exception:
        return 0


def _vaults() -> int:
    try:
        return len(json.loads(config.VAULTS_REGISTRY.read_text()).get("vaults", {}))
    except Exception:
        return 0


def _branch(repo_dir: Path) -> str:
    """Current branch read straight from .git/HEAD (no git subprocess)."""
    try:
        txt = (repo_dir / ".git" / "HEAD").read_text().strip()
    except Exception:
        return "?"
    if txt.startswith("ref:"):
        return txt.split("/", 2)[-1] or "?"  # refs/heads/<branch> (keeps slashes)
    return txt[:7] if txt else "?"           # detached HEAD → short sha


_STATE_TTL = 5.0  # seconds a cached repo-state is trusted before re-checking


def _repo_states(dirs: list[Path]) -> dict:
    """Map repo dir -> {dirty, ahead, behind}, cached with a short TTL so
    `git status` runs at most once per repo per few seconds, not every render."""
    cache_file = config.STATE_DIR / "cache" / "repostate.json"
    try:
        cache = json.loads(cache_file.read_text())
    except Exception:
        cache = {}
    now = time.time()
    out, changed = {}, False
    for d in dirs:
        key = str(d)
        ent = cache.get(key)
        if ent and (now - ent.get("ts", 0)) < _STATE_TTL:
            out[d] = ent
        else:
            st = _run_state(d)
            st["ts"] = now
            cache[key] = st
            out[d] = st
            changed = True
    if changed:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(cache))
        except Exception:
            pass
    return out


def _run_state(d: Path) -> dict:
    """One `git status --porcelain --branch` → dirty flag + ahead/behind counts."""
    try:
        r = subprocess.run(
            ["git", "-C", str(d), "status", "--porcelain=v1", "--branch"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=3,
        )
    except Exception:
        return {"dirty": False, "ahead": 0, "behind": 0}
    dirty, ahead, behind = False, 0, 0
    for ln in r.stdout.splitlines():
        if ln.startswith("## "):
            m = re.search(r"\[([^\]]*)\]", ln)
            if m:
                for part in m.group(1).split(","):
                    part = part.strip()
                    if part.startswith("ahead "):
                        ahead = _int(part[6:])
                    elif part.startswith("behind "):
                        behind = _int(part[7:])
        elif ln.strip():
            dirty = True
    return {"dirty": dirty, "ahead": ahead, "behind": behind}


def _int(s: str) -> int:
    try:
        return int(s)
    except Exception:
        return 0


def _markers(state: dict) -> tuple[str, str, bool]:
    """(plain, coloured, is_dirty) suffix: `*` dirty (yellow), `↑N` ahead/unpushed
    (cyan), `↓N` behind (blue)."""
    dirty = bool(state.get("dirty"))
    ahead, behind = int(state.get("ahead") or 0), int(state.get("behind") or 0)
    plain = coloured = ""
    if dirty:
        plain += "*"; coloured += f"{_YELLOW}*{_R}"
    if ahead:
        plain += f"↑{ahead}"; coloured += f"{_CYAN}↑{ahead}{_R}"
    if behind:
        plain += f"↓{behind}"; coloured += f"{_BLUE}↓{behind}{_R}"
    return plain, coloured, dirty


def _current(payload: dict) -> tuple[str, str] | None:
    """(workspace, repo) that the session's cwd is inside, if any."""
    ws = payload.get("workspace") or {}
    cwd = ws.get("current_dir") or payload.get("cwd") or ""
    if not cwd:
        return None
    try:
        parts = Path(cwd).resolve().relative_to(config.WORKSPACES_DIR.resolve()).parts
    except Exception:
        return None
    return (parts[0], parts[1]) if len(parts) >= 2 else None


def _repo_rows(dirs, active, cur, states, branches, gl) -> list[tui.Node]:
    """One table row per clone, nested under the workspace like a tree:

        ├─ <repo>   <branch><markers>   <ci>   <sigil><change>

    repo in its own colour (current repo bold+underlined); dirty→branch yellow
    `*`; ahead `↑N` cyan; behind `↓N` blue; pipeline ✓/✗/●/… ; open change `!N`/`#N`
    green, in that clone's own forge's notation (GitLab `!`, GitHub `#`).

    Column widths are declared per cell; the kit pads/truncates so sibling
    rows stay aligned and nothing ever exceeds the render width.

    Bounded by `_MAX_REPO_LINES` TOTAL rows, not by repo count: repos are prioritised
    over worktree rows (every repo gets its own row before any repo's worktrees get
    nested rows), so a repo is never dropped just because an earlier repo's worktrees
    ate the budget.
    """
    if not dirs:
        return []
    cur_repo = cur[1] if (cur and cur[0] == active) else None
    n = len(dirs)
    capped = n > _MAX_REPO_LINES
    show = dirs[: _MAX_REPO_LINES - 1] if capped else dirs
    # What's left of the total-row budget after every shown repo gets its own row (and,
    # if capped, the trailing "…(+N more)" line) — spent on nested worktree rows below.
    wt_budget = _MAX_REPO_LINES - len(show) - (1 if capped else 0)

    rows: list[tui.Node] = []
    for i, d in enumerate(show):
        is_last = (not capped) and (i == len(show) - 1)
        tree = _TREE_END if is_last else _TREE_MID
        color = _PALETTE[i % len(_PALETTE)]
        emph = f"{_BOLD}{_UNDER}" if d.name == cur_repo else ""

        # worktrees: a ⑂N badge here, the newest few nested below (see after the row)
        try:
            from . import worktree as _wt
            wts = _wt.dirs_for(active, d.name)
        except Exception:
            wts = []
        badge = f"{_DIM} ⑂{len(wts)}{_R}" if wts else ""

        # indent + tree + name (padding lands outside the style, not underlined)
        name = tui.Cell(f"  {_DIM}{tree}{_R}{emph}{color}{d.name}{_R}{badge}",
                        2 + 3 + _NAME_W)

        # branch + markers: truncate the *branch* so the markers always survive
        marks_plain, marks_col, is_dirty = _markers(states.get(d, {}))
        br = tui.truncate(branches.get(d, "?"),
                          max(1, _BRANCH_W - tui.width(marks_plain)))
        branch_color = _YELLOW if is_dirty else _DIM
        branch = tui.Cell(f"{branch_color}{br}{_R}{marks_col}", _BRANCH_W)

        info = gl.get(d, {})
        ci = tui.Cell(_ci_part(info.get("ci")), _CI_W)
        change = info.get("change")
        sigil = info.get("sigil") or "!"  # old caches (pre-forge-protocol) carry no sigil
        mr_cell = tui.Cell(f"{_GREEN}{sigil}{change}{_R}" if change else "", _MR_W)

        rows.append(tui.Row(name, branch, ci, mr_cell, gap=_GAP))

        # ONE summary line per repo, newest piece first — a fixed cost no matter how many
        # worktrees exist, so the footer can't grow with them (the ⑂N badge above carries
        # the true total, and overflow truncates with … rather than dropping pieces
        # silently). The lead glyph is VISIBLE: Claude Code's footer collapses a
        # whitespace-only prefix to column 0 — the same trap the persona column works
        # around below — which would drop this line to the left margin and stop it
        # reading as a child of its repo.
        if wts and wt_budget > 0:
            wt_budget -= 1
            lead = f"  {_DIM}│{_R}  {_DIM}↳ {_R}"
            pieces = tui.truncate(" · ".join(w.name for w in wts),
                                  max(1, _LEFT_W - tui.width(f"  │  ↳ ")))
            rows.append(tui.Text(f"{lead}{_DIM}{pieces}{_R}"))

    if capped:
        rows.append(tui.Text(f"  {_DIM}{_TREE_END}…(+{n - len(show)} more){_R}"))
    return rows


def _persona_line() -> str | None:
    """Footer rows for personas: the *active* (adopted) persona with its vault,
    then a roster of every persona — each is also dispatchable as a sub-agent.

    Returns None when no personas are defined, so non-persona projects stay to
    one line.
    """
    try:
        from . import persona
        names = persona.list_personas()
        if not names:
            return None
        names = sorted(names)
        active = persona.resolve_active()
        if not active:
            avail = f"{_DIM} · {_R}".join(f"{_DIM}{n}{_R}" for n in names)
            return f"{_DIM}◆ persona none{_R} · {avail}{_DIM} · charter persona use <name>{_R}"
        # Active (adopted) persona: name + vault health (the role reads as noise —
        # the name already says it).
        seg = f"{_MAGENTA}◆{_R} {_BOLD}{active}{_R}"
        vault = persona.vault_of(active)
        if vault:
            seg += f"{_DIM} · vault {_R}{vault}{_vault_glyph(vault)}"
        # The other personas, on the same line — each dispatchable as a sub-agent.
        others = [n for n in names if n != active]
        if others:
            chips = f"{_DIM} · {_R}".join(f"{_DIM}{n}{_R}" for n in others)
            seg += f"{_DIM} · ◇ agents {_R}{chips}"
        return seg
    except Exception:
        return None


def _vault_glyph(vault: str) -> str:
    try:
        from .secrets import registry
        if vault not in registry.vaults():
            return f" {_YELLOW}(set up){_R}"
        ok, _ = registry.provider_for(vault).health()
        return f" {_GREEN}✓{_R}" if ok else f" {_YELLOW}!{_R}"
    except Exception:
        return ""


def _vault_dot(vault: str | None) -> str:
    """Compact vault mark for persona chips — four states, matching what
    ``charter persona list`` reports in words:

    * ``✓`` healthy — registered, readable, holding secrets;
    * ``◦`` registered, but the file does not exist yet;
    * ``!`` registered and unhealthy (unreadable, bad provider config);
    * ``·`` not set up locally at all — the *normal* state across most of a committed
      roster, since personas are committed and vaults are private.

    The ``◦`` state used to render as a green ``✓``: ``plain-file.health()`` returns
    ``ok=True`` with the detail "not created yet (<path>)", and this read only ``ok``.
    `persona list` prints the detail and so was honest; the status line was claiming a
    vault existed when it did not.
    """
    try:
        from .secrets import registry
        if not vault or vault not in registry.vaults():
            return f" {_DIM}·{_R}"
        ok, detail = registry.provider_for(vault).health()
        if not ok:
            return f" {_YELLOW}!{_R}"
        if "not created yet" in (detail or ""):
            return f" {_DIM}◦{_R}"
        return f" {_GREEN}✓{_R}"
    except Exception:
        return ""


def _health_mark(name: str, known: set[str] | None = None) -> str:
    """Health mark for a persona chip — **only when something is wrong**.

    ``⚑`` the charter is a draft, so charter generates no sub-agent for it and it
    cannot be dispatched; ``✗`` its config is broken (dangling ``extends:``/``uses:``,
    or an inheritance cycle). A healthy persona gets nothing: a row of ✓s becomes
    furniture within a day, and then a real ✗ inside it draws no more attention than a
    zero would.

    Soft findings — no role, no ``delegate-when`` — deliberately do not appear here.
    They are real, and `lint`/`doctor` have room to explain them; a chip does not.

    Cost is why this calls :func:`persona.structural_errors` rather than `lint`: the
    full lint walks the plugin cache, and even `lint(deep=False)` pays for the vault,
    role, delegate-when and unknown-key checks (plus an import of `commands_persona`)
    that produce nothing a chip can show. This renders on every single turn.
    """
    try:
        from . import persona
        if persona.is_draft(name):
            return f" {_YELLOW}⚑{_R}"
        if persona.structural_errors(name, known=known):
            return f" {_RED}✗{_R}"
        return ""
    except Exception:
        return ""


def _mem_count(name: str, shared: bool = False, ephemeral: bool = False,
               session: str | None = None) -> int:
    """Count a persona's (or the shared namespace's) memories in one quadrant.
    Cheap: one dir glob. Best-effort — never breaks the status line."""
    try:
        from . import persona
        return len(persona.memories(name, shared=shared, ephemeral=ephemeral, session=session))
    except Exception:
        return 0


def _mem_badge(persistent: int, ephemeral: int = 0) -> str:
    """Coloured memory-count badge: ``✎N`` persistent (green, committed) + ``◌N``
    ephemeral (yellow, session scratch). '' when both are zero."""
    parts = []
    if persistent:
        parts.append(f"{_GREEN}✎{persistent}{_R}")
    if ephemeral:
        parts.append(f"{_YELLOW}◌{ephemeral}{_R}")
    return (" " + " ".join(parts)) if parts else ""


def _persona_chips(session: str | None = None) -> list[str]:
    """One chip per persona (active first) for the status-line right column, each
    tagged with its memory counts (``✎`` persistent + ``◌`` ephemeral). Every
    persona is also dispatchable as a sub-agent."""
    try:
        from . import persona
        names = sorted(persona.list_personas())
        if not names:
            return []
        active = persona.resolve_active()
        order = ([active] if active in names else []) + [n for n in names if n != active]
        known = set(names)   # computed once for the whole column, not once per persona
        chips = []
        for n in order:
            dot = _vault_dot(persona.vault_of(n))
            badge = _mem_badge(_mem_count(n), _mem_count(n, ephemeral=True, session=session))
            health = _health_mark(n, known=known)
            if n == active:
                chips.append(f"{_MAGENTA}◆ {_BOLD}{n}{_R}{dot}{badge}{health}")
            else:
                chips.append(f"{_DIM}○ {n}{_R}{dot}{badge}{health}")
        return chips
    except Exception:
        return []


def _session_news(sid: str | None) -> list[str]:
    """Counters for what has happened **in this session** — in flight, denied,
    recorded, dispatched.

    Deliberately silent when nothing is happening. A counter that renders every turn
    becomes furniture within a day, and then a real guard denial appearing in it gets
    no more attention than a zero would. Presence IS the signal.

    Returns the pieces, not a line: they join the context gauges on the session strip,
    because they answer the same question (*what is happening right now*) — which is
    exactly what they did NOT do while they rendered inside the repo column, where
    "⛊ 1 denied" sat under a repo tree and read as news about a repo.

    Everything here is already computed elsewhere and costs well under a millisecond;
    the status line renders on every turn, so nothing may be added that reads the
    network or walks a repo.
    """
    out: list[str] = []
    try:
        from . import inflight
        live = inflight.live()
        if live:
            who = ", ".join(live[:3]) + (", …" if len(live) > 3 else "")
            out.append(f"{_YELLOW}⚡{_R}{_DIM}in flight{_R} {len(live)} {_DIM}· {who}{_R}")
    except Exception:
        pass

    try:
        from . import trace
        ev = trace.read(sid) if sid else []
        kinds: dict[str, int] = {}
        for e in ev:
            k = e.get("event")
            if k:
                kinds[k] = kinds.get(k, 0) + 1
        if kinds.get("deny"):
            out.append(f"{_RED}⛊ {kinds['deny']} denied{_R}")
        if kinds.get("memory"):
            out.append(f"{_GREEN}✎ {kinds['memory']}{_R}{_DIM} recorded{_R}")
        if kinds.get("dispatch"):
            out.append(f"{_DIM}⇢ {kinds['dispatch']} dispatched{_R}")
    except Exception:
        pass
    return out


def _alerts(active: str) -> list[str]:
    """Full-width alert lines — a pinned-version mismatch, workspaces needing reinit.

    Kept off the session strip and out of both columns: these are not telemetry but
    *actionable* problems that carry the command that fixes them, and they are about
    the control plane rather than this session's activity. They render only when real,
    so they cost no rows on a healthy control plane.
    """
    out: list[str] = []
    try:
        from . import __version__, config, instance as _instance, workspace as _ws
        locked = _instance.locked_version(_instance.load(config.ROOT))
        if locked and locked != __version__:
            out.append(f"{_YELLOW}⚠{_R} {_DIM}charter{_R} {__version__} {_DIM}→ pinned{_R} "
                       f"{locked}{_DIM} · charter version sync{_R}")
        stale = [w for w in _ws.list_workspaces() if w != active and _ws.needs_reinit(w)]
        if stale:
            out.append(f"{_YELLOW}⚠{_R} {_DIM}reinit{_R} {len(stale)} {_DIM}ws · "
                       f"charter ws reinit --all{_R}")
    except Exception:
        pass
    return out


def _session_strip(payload: dict, sid: str | None) -> str:
    """The bottom zone: everything true of **this session**, on one line.

    ``ctx``/``⚡`` (context + cache health) sit here rather than in the top line
    because they describe the session, not the workspace — the top line answers
    *where am I*, and mixing a session gauge into it was most of why the old header
    read as unrelated items in a row.

    Empty string when there is nothing to report (a fresh session or one just past
    ``/compact`` has no usage yet): the brand alone does not justify a row.
    """
    parts = [*_context_gauge(payload), *_session_news(sid)]
    return f"{_DIM} · {_R}".join(parts) if parts else ""


def _brand() -> str:
    """`⬢ charter x.y.z`, plus `↑ a.b.c` when a newer release is cached.

    Read-only and offline: the version is this process's own, and the "newer?"
    answer comes from a cache another process fills. `update.maybe_spawn` may fork
    a detached child, but nothing here ever waits on the network — the status line
    renders on every turn.
    """
    from . import __version__, update
    out = f"{_DIM}⬢ charter {__version__}{_R}"
    try:
        update.maybe_spawn()
        newer = update.newer_than(__version__)
    except Exception:
        newer = None
    if newer:
        out += f" {_YELLOW}↑{newer}{_R}"
    return out


def _with_brand(body: str, width: int) -> str:
    """Right-align the brand on the last line, if it fits without crowding.

    Appended after layout rather than inside it: the columns are width-constrained
    and threading a right-hand chunk through them would push real content out.
    Dropped entirely on a narrow pane — branding must never cost a repo row.

    ``_BRAND_MARGIN`` is why the check is not simply "does it fit in ``width``". A real
    session rendered ``⬢ charter 0.10…``, which this function cannot produce — it fits
    or it drops, it never truncates. So the crop came from outside: the pane gave one
    column less than ``COLUMNS`` promised. Rather than guess the exact reserve, keep a
    margin, so an off-by-one anywhere (the pane's, or a terminal that draws ``⬢`` two
    cells wide) costs the brand instead of shearing it into nonsense.
    """
    try:
        brand = _brand()
        lines = body.split("\n")
        if not lines:
            return body
        last = lines[-1]
        used, need = tui.width(last), tui.width(brand)
        if used + need + _BRAND_GAP + _BRAND_MARGIN > width:
            return body                      # no room: content wins
        lines[-1] = last + " " * (width - used - need) + brand
        return "\n".join(lines)
    except Exception:
        return body


def render(payload: dict | None = None) -> str:
    """FINDING M9: the module docstring promises this NEVER crashes — that guarantee
    lives entirely in this function's two `try/except` blocks, so EVERY step that can
    fail must sit inside one of them. Before this, `_repo_rows`/`_persona_chips` (and
    the summary/pin/reinit assembly) ran in the GAP between the two — a bad repo row (or
    a broken persona) crashed `render()` itself, the exact thing this docstring says can
    never happen. Everything through `chips` now shares the FIRST try/except, so a
    failure anywhere in data-gathering falls back to the same minimal string."""
    payload = payload or {}
    try:
        active, src = _active(payload.get("session_id"))
        nws = _count_workspaces()
        dirs = _clone_dirs(active)
        avail = _available()
        nv = _vaults()
        cur = _current(payload)
        # Render a hair under COLUMNS (which Claude Code sets to the pane width) so a
        # line never fills the last column (which the terminal would wrap).
        width = max(24, tui.term_width(default=80, floor=24) - _SAFETY)
        states = _repo_states(dirs)
        branches = {d: _branch(d) for d in dirs}
        from . import glstate
        gl = glstate.read_for(dirs, branches)
        glstate.maybe_spawn(dirs, active)

        pin = f"{_YELLOW}*{_R}" if src == "$CHARTER_WORKSPACE" else ""
        # Reinit tip sits right after the name so it survives truncation on narrow panes.
        reinit = f"{_YELLOW}⚠ reinit: {_BOLD}charter ws reinit{_R}" if _stale_structure(active) else None
        # Zone 1 — WHERE I am. Identity and navigation only: which workspace is active,
        # and how many others exist to switch to. Everything that used to ride along
        # here (repo count, vault count, ctx/⚡) described something else and now sits
        # with the thing it describes.
        summary = f"{_DIM} · {_R}".join(filter(None, [
            f"{_CYAN}⬢{_R} {_BOLD}{active}{_R}{pin}",
            reinit,
            f"{_DIM}ws{_R} {nws}",
        ]))

        sid = payload.get("session_id")
        repo_lines = [r.render(_LEFT_W)[0] for r in _repo_rows(dirs, active, cur, states, branches, gl)]
        chips = _persona_chips(sid)
    except Exception:
        return f"{_CYAN}⬢{_R} charter"

    try:
        # Zone 2 — one header row, one head per column, each stating what its own
        # column holds. `repos N/M` used to sit in the top line and `personas`/`vaults`
        # in the right column, so two structurally identical facts rendered in two
        # different places; a count now always sits next to what it counts.
        shared_badge = _mem_badge(_mem_count("_", shared=True),
                                  _mem_count("_", shared=True, ephemeral=True, session=sid))
        # NEITHER header carries a decorative glyph, deliberately, and the reason is the
        # same for both: a header is the only row of its kind, so a glyph on it is
        # exercised by nothing else. `tui.width` trusts the Unicode tables; a font that
        # draws a character wider than its table claims shifts everything after it on
        # that row — and a header's row is the one row with no neighbour to reveal the
        # drift. Content rows are safe by contrast precisely because they repeat: the
        # thirteen chip rows line up with each other, which is what proves `◆`/`○`, and
        # the repo rows prove `├ ─ │ ⑂ ✓ ✗ ·`.
        #
        # Both mistakes shipped. A `◫` on this line pushed the whole right-hand column
        # one space over. Then a `◈` on the personas header — past the column's own
        # alignment point, so the divider and bullets still lined up perfectly — pushed
        # the *word* "personas" one space right of every chip title below it, which is
        # the tell: the bullets agreed and the titles did not.
        #
        # So: labels only up here. Decoration lives on the content rows, where a
        # sibling would expose it.
        left_head = f"{_DIM}repos{_R} {len(dirs)}{_DIM}/{avail}{_R}"
        header = f"{_DIM}personas{_R} {len(chips)}" + (
            f"{_DIM} · vaults{_R} {nv}" if nv else "") + (
            f"{_DIM} · shared{_R}{shared_badge}" if shared_badge else "")
        strip = _session_strip(payload, sid)
        alerts = _alerts(active)
        # `left_head` means the left column is never empty, so two columns no longer
        # require a cloned repo — a fresh workspace still gets the grouped layout with
        # an honest `◫ repos 0/38`.
        if chips and width >= _LEFT_W + _RIGHT_MIN_W:
            # Summary on its own full-width line; below it, two columns — repos left,
            # personas right. The header sits beside a full repo row (pairing it with
            # the short summary row misaligned it on some terminals), so it lines up
            # with the chips. When personas outnumber repos, continue the repo tree
            # with │ so no row is blank on the left (Claude Code collapses those to col 0).
            # News goes in the rows the repo tree already leaves blank — free
            # vertically, so it costs no width and works at 80 columns.
            left = [left_head, *repo_lines]
            right = [header, *chips]
            if len(right) > len(left):
                # The tree keeps going below the last repo, so its `└─` must become
                # `├─`. Find the row that actually carries the elbow — `left[-1]` is
                # not it whenever the last repo emitted a worktree summary line
                # underneath, which left a `└─` sitting above a column of `│`.
                for i in range(len(left) - 1, -1, -1):
                    if _TREE_END in left[i]:
                        left[i] = left[i].replace(_TREE_END, _TREE_MID, 1)
                        break
                while len(left) < len(right):
                    left.append(f"  {_DIM}│{_R}")
            rows = [tui.truncate(summary, width), _columns(left, right, width)]
            rows += [tui.truncate(a, width) for a in alerts]
            if strip:
                rows.append(tui.truncate(strip, width))
            body = "\n".join(rows)
        elif chips:
            body = _columns([summary, left_head, *repo_lines, header, *chips,
                             *alerts, *([strip] if strip else [])], None, width)
        else:
            body = _columns([summary, left_head, *repo_lines,
                             *alerts, *([strip] if strip else [])], None, width)
    except Exception:
        # Never crash the status line if layout fails — plain truncated stack.
        plain = [summary, *repo_lines]
        p = _persona_line()
        if p:
            plain.append(p)
        body = "\n".join(tui.truncate(ln, width) for ln in plain)

    return _with_brand(body, width) + "\n"  # trailing blank line below the status line


def _columns(left_lines: list[str], right_lines: list[str] | None, width: int) -> str:
    """Compose one or two columns with the stdlib tui kit, clamped to *width*
    (overflow cropped with …, never wrapped; no trailing whitespace)."""
    if right_lines:
        node: tui.Node = tui.Columns([(list(left_lines), _LEFT_W),
                                       (list(right_lines), None)], gap=_COL_SEP)
    else:
        node = tui.Stack(*left_lines)
    return "\n".join(node.render(width))


def main(argv=None) -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    # Defense in depth (FINDING M9): `render()` itself is guarded end-to-end, but this
    # is the outermost boundary of the whole subprocess — it must never crash even if a
    # future bug (or a monkeypatch, or an import-time surprise) reaches past render's
    # own guard, since a raise here takes the entire status-line subprocess down.
    try:
        out = render(payload)
    except Exception:
        out = f"{_CYAN}⬢{_R} charter"
    print(out)
    return 0
