"""Centralized memory retrieval — the single gate for fetching across every memory base.

There are several memory *stores*, on two independent axes: the active **workspace**'s task
journal, the active **persona**'s own role memory, the cross-role **shared** namespace, and
(opt-in) the persona's **ephemeral** session scratch. Storage stays per-base — they're
genuinely different axes — but *reading* is unified here: a caller asks once and gets ranked,
**source-labeled** hits, without needing to know which base a fact lives in.

This is the one place memory is fetched. It sits on the shared :mod:`charter.memstore` engine
(whose ``search`` already ranks across a list of dirs); this module adds the context-aware
assembly of *which* dirs are in scope and tags each result with where it came from.
"""

from __future__ import annotations

from pathlib import Path

from . import config, memstore

#: The selectable scopes, in the order they're assembled/displayed.
SCOPES = ("workspace", "persona", "shared", "ephemeral")
DEFAULT_SCOPES = ("workspace", "persona", "shared")  # the committed bases; ephemeral is opt-in


def sources(session_id: str | None = None, persona_name: str | None = None,
            workspace_name: str | None = None, scopes=DEFAULT_SCOPES) -> list[tuple[str, Path]]:
    """``[(source_label, memory_dir)]`` for the requested scopes in the current context.
    Resolves the active workspace/persona unless overridden. Skips a scope with no owner
    (e.g. no active persona → no persona/ephemeral rows)."""
    from . import workspace as ws_mod, persona as p_mod
    out: list[tuple[str, Path]] = []
    if "workspace" in scopes:
        ws = workspace_name or ws_mod.resolve(session_id=session_id)
        out.append((f"workspace:{ws}", ws_mod.memory_dir(ws)))
    name = persona_name or p_mod.resolve_active()
    if "persona" in scopes and name:
        out.append((f"persona:{name}", p_mod.memory_dir(name)))
    if "ephemeral" in scopes and name:
        out.append((f"persona:{name}:ephemeral", p_mod.ephemeral_dir(name, session=session_id)))
    if "shared" in scopes:
        out.append(("shared", p_mod.memory_dir(config.SHARED_PERSONA, shared=True)))
    return out


def _label_of(path: Path, srcs: list[tuple[str, Path]]) -> str:
    for lbl, d in srcs:
        try:
            Path(path).resolve().relative_to(Path(d).resolve())
            return lbl
        except (ValueError, OSError):
            continue
    return "?"


def recall(query: str | None = None, session_id: str | None = None, limit: int = 8,
           persona_name: str | None = None, workspace_name: str | None = None,
           scopes=DEFAULT_SCOPES) -> list[tuple[str, Path, str, int]]:
    """THE memory fetch gate. With a ``query`` → keyword-ranked hits across all in-scope
    bases (title hits weigh 3×, via the memstore engine). Without → every in-scope memory,
    newest-first. Returns ``[(source_label, path, title, score)]`` best-first (score 0 when
    listing). ``limit`` caps the results (``0`` = no cap)."""
    srcs = sources(session_id=session_id, persona_name=persona_name,
                   workspace_name=workspace_name, scopes=scopes)
    dirs = [d for _lbl, d in srcs]
    if query:
        hits = memstore.search(dirs, query, limit or 10_000)
        return [(_label_of(p, srcs), p, title, score) for p, title, score in hits]
    # no query → list everything in scope, newest-first by recorded date
    import datetime
    items = []
    for lbl, d in srcs:
        for p, title, text in memstore.entries(d):
            items.append((memstore.memory_date(text, p.name) or datetime.date.min, lbl, p, title))
    items.sort(key=lambda x: x[0], reverse=True)
    rows = [(lbl, p, title, 0) for _dt, lbl, p, title in items]
    return rows[:limit] if limit else rows
