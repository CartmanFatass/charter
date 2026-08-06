"""Committed dispatch tally — how often each persona is actually *used*.

Roster health used to be judged by memory volume alone, which is blind to the failure
that matters: a persona that exists, advertises a `delegate-when`, lints green, and is
**never dispatched** while the work it owns routes to a generic sub-agent. Memory counts
can't see that; only dispatch counts can.

Shape of the store::

    personas/_dispatch/<YYYY-MM>.<host>.jsonl     one JSON object per line

Three properties matter:

* **Counts and dates only.** A line is ``{"ts": …, "agent": …}`` — never the prompt, the
  description, or any tool input. There is no secret surface to scan.
* **Parallel-writer safe.** Lines are small and opened ``O_APPEND``, so concurrent
  dispatches (a fan-out of 8 sub-agents) append atomically without a lock.
* **Conflict-free across engineers.** The filename carries the host, so two people
  recording the same month never touch the same file — the committed tally merges by
  addition instead of conflicting.

Generic agents (``general-purpose``, ``Explore``) are tallied too, under their own name:
the ratio of persona-to-generic dispatch is the whole point, and dropping it would hide
the very gap this store exists to expose.
"""
from __future__ import annotations

import json
import os
import re
import socket
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

DIR_NAME = "_dispatch"
#: Agents that aren't personas — tallied so the persona-vs-generic ratio stays visible.
GENERIC = ("general-purpose", "Explore", "claude", "Plan")


def _dir() -> Path:
    return config.PERSONAS_DIR / DIR_NAME


def _host() -> str:
    """Short, filename-safe hostname — keeps each engineer on their own file."""
    raw = (socket.gethostname() or "unknown").split(".")[0]
    return re.sub(r"[^A-Za-z0-9_-]", "", raw)[:32] or "unknown"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def path_for(when: datetime | None = None) -> Path:
    when = when or _now()
    return _dir() / f"{when:%Y-%m}.{_host()}.jsonl"


def record(agent: str, when: datetime | None = None) -> Path | None:
    """Append one dispatch. Best-effort: a tally must never break a turn, so any failure
    is swallowed and reported as None."""
    agent = (agent or "").strip()
    if not agent:
        return None
    when = when or _now()
    p = path_for(when)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": when.isoformat(timespec="seconds"), "agent": agent},
                          sort_keys=True) + "\n"
        # O_APPEND: concurrent sub-agent dispatches interleave safely without a lock.
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode())
        finally:
            os.close(fd)
        return p
    except OSError:
        return None


def _read_all() -> list[dict]:
    d = _dir()
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("*.jsonl")):
        try:
            for ln in f.read_text(errors="replace").splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    o = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(o, dict) and o.get("agent"):
                    out.append(o)
        except OSError:
            continue
    return out


def _ts(o: dict) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(o.get("ts", "")))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def tally(days: int | None = None) -> Counter:
    """agent → dispatch count, optionally limited to the last *days*."""
    rows = _read_all()
    if days is not None:
        cutoff = _now() - timedelta(days=days)
        rows = [o for o in rows if (t := _ts(o)) and t >= cutoff]
    return Counter(o["agent"] for o in rows)


def last_seen(agent: str) -> str | None:
    """ISO date of the most recent dispatch of *agent*, or None if never dispatched."""
    best = None
    for o in _read_all():
        if o.get("agent") != agent:
            continue
        t = _ts(o)
        if t and (best is None or t > best):
            best = t
    return f"{best:%Y-%m-%d}" if best else None


def generic_share(days: int | None = None) -> tuple[int, int]:
    """(dispatches to generic agents, total dispatches) — the headline routing ratio."""
    t = tally(days)
    return sum(v for k, v in t.items() if k in GENERIC), sum(t.values())


# --------------------------------------------------------------------------- #
# Backfill — seed the tally from past sessions so the baseline exists TODAY     #
# instead of a week from now. Transcripts already record every dispatch; this   #
# just reads what is there. Writes to `*.backfill.jsonl`, kept separate from    #
# live records so a re-run replaces its own output instead of double-counting.  #
# --------------------------------------------------------------------------- #
BACKFILL_SUFFIX = ".backfill.jsonl"


def _transcript_dir() -> Path:
    """Claude Code stores a project's transcripts under ~/.claude/projects/<slug>, where
    the slug is the project path with every separator replaced by '-'."""
    slug = str(config.ROOT).replace("/", "-").replace("\\", "-")
    return Path.home() / ".claude" / "projects" / slug


def _earliest_live() -> datetime | None:
    """Start of the live-tallied period. Backfill must not import anything at or after
    it, or a session that was both live-recorded and transcribed would count twice."""
    best = None
    d = _dir()
    if not d.exists():
        return None
    for f in d.glob("*.jsonl"):
        if f.name.endswith(BACKFILL_SUFFIX):
            continue
        for ln in f.read_text(errors="replace").splitlines():
            try:
                t = _ts(json.loads(ln))
            except ValueError:
                continue
            if t and (best is None or t < best):
                best = t
    return best


def scan_transcripts() -> list[dict]:
    """Every sub-agent dispatch recorded in this project's transcripts, as tally rows.
    Reads only the tool name, subagent_type and timestamp — never prompt text."""
    d = _transcript_dir()
    if not d.exists():
        return []
    rows = []
    for f in sorted(d.glob("*.jsonl")):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for ln in text.splitlines():
            try:
                o = json.loads(ln)
            except ValueError:
                continue
            if o.get("type") != "assistant":
                continue
            content = (o.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                if b.get("name") not in ("Task", "Agent"):
                    continue
                agent = ((b.get("input") or {}).get("subagent_type") or "").strip()
                if agent:
                    rows.append({"ts": (o.get("timestamp") or "")[:19], "agent": agent})
    return rows


def backfill() -> tuple[int, int]:
    """Seed the tally from transcripts. Returns (imported, skipped-as-already-live).
    Idempotent: rewrites only the backfill files it owns."""
    cutoff = _earliest_live()
    keep: dict[str, list[str]] = {}
    imported = skipped = 0
    for row in scan_transcripts():
        t = _ts(row)
        if not t:
            continue
        if cutoff and t >= cutoff:
            skipped += 1
            continue
        name = f"{t:%Y-%m}.{_host()}{BACKFILL_SUFFIX}"
        keep.setdefault(name, []).append(
            json.dumps({"ts": t.isoformat(timespec="seconds"), "agent": row["agent"]},
                       sort_keys=True))
        imported += 1
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    for f in d.glob(f"*{BACKFILL_SUFFIX}"):  # replace, never append — keeps re-runs clean
        f.unlink()
    for name, lines in keep.items():
        (d / name).write_text("\n".join(sorted(lines)) + "\n")
    return imported, skipped
