"""Lightweight local observability: an append-only, per-session trace of the
decisions and activity charter actually took — guard denials, persona tool
approvals, secret-scan warnings, memory writes, persona switches.

This is **not** OpenTelemetry — no collector, no network, no deps. It's a stdlib
JSONL record under gitignored ``.charter/persona-state/trace/<session>.jsonl`` so you can
answer "what did the agent/personas just do?" and feed the eval loop. Capture is
best-effort and never raises — observability must never break the thing it observes.
"""

from __future__ import annotations

import datetime
import json
import os

from . import config


def _session(session: str | None = None) -> str:
    return (session or os.environ.get("CLAUDE_CODE_SESSION_ID") or "nosession").strip() or "nosession"


def _file(session: str | None = None):
    return config.PERSONA_STATE_DIR / "trace" / f"{_session(session)}.jsonl"


def record(event: str, session: str | None = None, **fields) -> None:
    """Append one event to the current session's trace. Best-effort; swallows all errors."""
    try:
        f = _file(session)
        f.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "event": event}
        rec.update({k: v for k, v in fields.items() if v is not None})
        with f.open("a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read(session: str | None = None, n: int | None = None) -> list[dict]:
    f = _file(session)
    if not f.exists():
        return []
    out = []
    for ln in f.read_text().splitlines():
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out[-n:] if n else out


def for_persona(name: str, session: str | None = None, n: int | None = None) -> list[dict]:
    """This session's trace events attributed to a persona — its tool approvals,
    memory writes, switches, and manual `note`s. (The trace is the one activity
    record; `charter persona log` writes `note` events into it.)"""
    evs = [e for e in read(session) if e.get("persona") == name]
    return evs[-n:] if n else evs


def sessions() -> list[str]:
    d = config.PERSONA_STATE_DIR / "trace"
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.jsonl"))
