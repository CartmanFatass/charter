"""Claude Code status-line renderer for the umbrella.

Wired via ``.claude/settings.json`` → ``statusLine``. Claude Code pipes a JSON
payload on stdin (session/model/workspace context) and renders this command's
stdout in the footer on every turn.

Contract we honor (see docs/workspaces.md): read *all* of stdin, stay fast (no
git subprocess, no network — branches are read straight from ``.git/HEAD``),
never raise (fall back to a minimal string), and exit 0. ANSI colour and
multiple lines are supported.

This module only *gathers* content (repos, branches, CI, personas) and
declares the layout; all width math lives in :mod:`edm.tui`, whose nodes
guarantee that no emitted line ever exceeds the terminal width — overflow is
truncated with ``…``, never wrapped (a wrap shears every column below it).

Note: Claude Code does **not** pass the session's environment to the status
line, so an ``$EDM_WORKSPACE``-pinned session shows the active-file/default here
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
    (created by an older umbrella) → flag it with a reinit tip. Best-effort, fast."""
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
    cache_file = config.EDM_HOME / "cache" / "repostate.json"
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

        ├─ <repo>   <branch><markers>   <ci>   !<mr>

    repo in its own colour (current repo bold+underlined); dirty→branch yellow
    `*`; ahead `↑N` cyan; behind `↓N` blue; pipeline ✓/✗/●/… ; open MR `!N` green.

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
        mr = info.get("mr")
        mr_cell = tui.Cell(f"{_GREEN}!{mr}{_R}" if mr else "", _MR_W)

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
            return f"{_DIM}◆ persona none{_R} · {avail}{_DIM} · edm persona use <name>{_R}"
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
    """Compact vault mark for persona chips: ✓ healthy · ! unhealthy · · not set up."""
    try:
        from .secrets import registry
        if not vault or vault not in registry.vaults():
            return f" {_DIM}·{_R}"
        ok, _ = registry.provider_for(vault).health()
        return f" {_GREEN}✓{_R}" if ok else f" {_YELLOW}!{_R}"
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
        chips = []
        for n in order:
            dot = _vault_dot(persona.vault_of(n))
            badge = _mem_badge(_mem_count(n), _mem_count(n, ephemeral=True, session=session))
            if n == active:
                chips.append(f"{_MAGENTA}◆ {_BOLD}{n}{_R}{dot}{badge}")
            else:
                chips.append(f"{_DIM}○ {n}{_R}{dot}{badge}")
        return chips
    except Exception:
        return []


def render(payload: dict | None = None) -> str:
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
    except Exception:
        return f"{_CYAN}⬢{_R} edm"

    pin = f"{_YELLOW}*{_R}" if src == "$EDM_WORKSPACE" else ""
    # Reinit tip sits right after the name so it survives truncation on narrow panes.
    reinit = f"{_YELLOW}⚠ reinit: {_BOLD}edm ws reinit{_R}" if _stale_structure(active) else None
    summary = f"{_DIM} · {_R}".join(filter(None, [
        f"{_CYAN}⬢{_R} {_BOLD}{active}{_R}{pin}",
        reinit,
        f"{_DIM}repos{_R} {len(dirs)}{_DIM}/{avail}{_R}",
        f"{_DIM}ws{_R} {nws}",
        f"{_DIM}vaults{_R} {nv}" if nv else None,
        *_context_gauge(payload),
    ]))

    sid = payload.get("session_id")
    repo_lines = [r.render(_LEFT_W)[0] for r in _repo_rows(dirs, active, cur, states, branches, gl)]
    chips = _persona_chips(sid)

    try:
        # shared-namespace memory: persistent (committed) + ephemeral (this session)
        shared_badge = _mem_badge(_mem_count("_", shared=True),
                                  _mem_count("_", shared=True, ephemeral=True, session=sid))
        header = f"{_MAGENTA}◈{_R} {_DIM}personas{_R}" + (
            f"{_DIM} · shared{_R}{shared_badge}" if shared_badge else "")
        if repo_lines and chips and width >= _LEFT_W + _RIGHT_MIN_W:
            # Summary on its own full-width line; below it, two columns — repos left,
            # personas right. The header sits beside a full repo row (pairing it with
            # the short summary row misaligned it on some terminals), so it lines up
            # with the chips. When personas outnumber repos, continue the repo tree
            # with │ so no row is blank on the left (Claude Code collapses those to col 0).
            left = list(repo_lines)
            right = [header, *chips]
            if len(right) > len(left):
                if left:
                    left[-1] = left[-1].replace(_TREE_END, _TREE_MID, 1)  # tree keeps going
                while len(left) < len(right):
                    left.append(f"  {_DIM}│{_R}")
            body = tui.truncate(summary, width) + "\n" + _columns(left, right, width)
        elif chips:
            body = _columns([summary, *repo_lines, header, *chips], None, width)
        else:
            body = _columns([summary, *repo_lines], None, width)
    except Exception:
        # Never crash the status line if layout fails — plain truncated stack.
        plain = [summary, *repo_lines]
        p = _persona_line()
        if p:
            plain.append(p)
        body = "\n".join(tui.truncate(ln, width) for ln in plain)

    return body + "\n"  # trailing blank line for breathing room below the status line


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
    print(render(payload))
    return 0
