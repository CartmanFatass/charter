"""Umbrella *freshness*: is a running Claude session behind the committed umbrella
config? A session bakes ``CLAUDE.md`` + the system prompt into its context at start and
only a *fresh* (non-resumed) session re-reads them, so when new features/prompts land the
best we can do is *append* an awareness signal to the running session (a UserPromptSubmit
nudge) — see :func:`edm.hooks.userpromptsubmit`.

"Behavior version" = the count of commits touching paths that change how a session
behaves (``CLAUDE.md``, ``edm/``, ``.claude/``, ``docs/``). Persona/workspace *memory*
churn is deliberately excluded — it doesn't change behavior, so it never triggers a nudge.
Everything here is best-effort and never raises (git may be absent/shallow)."""

from __future__ import annotations

import subprocess

from . import config

# Paths whose commits change how a session behaves (memory/refs churn excluded).
_BEHAVIOR_PATHS = ["CLAUDE.md", "edm", ".claude", "docs"]
# The subset a running session CANNOT pick up live → needs a fresh (non-resumed) session.
# (CLAUDE.md is baked into context; settings.json hook wiring + generated sub-agents load
# at startup. edm/ CLI+hook-logic and .claude/skills/ are live, so they're not here.)
_RESTART_PATHS = ["CLAUDE.md", ".claude/settings.json", ".claude/agents"]


def _git(args, root=None):
    try:
        return subprocess.run(
            ["git", "-C", str(root or config.ROOT), *args],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None


def head_sha(root=None) -> str:
    r = _git(["rev-parse", "HEAD"], root)
    return r.stdout.strip() if r and r.returncode == 0 else ""


def behavior_count(ref: str = "HEAD", root=None) -> int:
    """How many behavior-affecting commits lead up to ``ref`` — the 'version' number."""
    r = _git(["rev-list", "--count", ref, "--", *_BEHAVIOR_PATHS], root)
    try:
        return int(r.stdout.strip()) if r and r.returncode == 0 else 0
    except (ValueError, AttributeError):
        return 0


def behavior_delta(since_sha: str, root=None) -> list[str]:
    """Commit subjects in ``since_sha..HEAD`` that touch a behavior path (newest first).
    Empty when only memory/other non-behavior files changed."""
    r = _git(["log", "--format=%s", f"{since_sha}..HEAD", "--", *_BEHAVIOR_PATHS], root)
    if not r or r.returncode != 0:
        return []
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def needs_fresh_session(since_sha: str, root=None) -> bool:
    """True if the delta touched something a running session can't pick up live
    (CLAUDE.md / settings.json wiring / generated sub-agents) → a fresh session helps."""
    r = _git(["log", "--oneline", f"{since_sha}..HEAD", "--", *_RESTART_PATHS], root)
    return bool(r and r.returncode == 0 and r.stdout.strip())
