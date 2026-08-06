"""Shared test helpers: filesystem-isolated persona/config paths + a hook runner.

Persona/memory/hook code reads well-known paths off :mod:`charter.config` at call time
(``config.PERSONAS_DIR``, ``PERSONA_STATE_DIR``, ``ACTIVE_PERSONA_FILE``,
``WORKSPACES_DIR``), so redirecting those module attributes to a tmp dir isolates a test
completely from the real repo. Not a ``test_*`` module, so discovery skips it.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from charter import config, persona

_PATCH = ("ROOT", "PERSONAS_DIR", "PERSONA_STATE_DIR", "EDM_HOME", "ACTIVE_PERSONA_FILE",
          "WORKSPACES_DIR", "SESSIONS_DIR", "TERMINALS_DIR")


class PersonaIso(unittest.TestCase):
    """Base case: every config path points into a throwaway tmp dir, restored after.
    ROOT is redirected too, so anything reading the repo (e.g. the git-based
    uncommitted-memory nudge) sees the tmp (a non-git dir), not the real checkout."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="edm-test-"))
        self._orig = {k: getattr(config, k) for k in _PATCH}
        config.ROOT = self.tmp
        config.EDM_HOME = self.tmp / ".edm"
        config.PERSONAS_DIR = self.tmp / "personas"
        config.PERSONA_STATE_DIR = config.EDM_HOME / "persona-state"
        config.ACTIVE_PERSONA_FILE = config.EDM_HOME / "active-persona"
        config.WORKSPACES_DIR = self.tmp / "workspaces"
        config.SESSIONS_DIR = config.EDM_HOME / "sessions"
        config.TERMINALS_DIR = config.EDM_HOME / "terminals"
        config.PERSONAS_DIR.mkdir(parents=True, exist_ok=True)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        for k, v in self._orig.items():
            setattr(config, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_persona(self, name: str, **meta) -> str:
        d = config.PERSONAS_DIR / name
        d.mkdir(parents=True, exist_ok=True)
        lines = "\n".join(f"{k}: {v}" for k, v in {"name": name, **meta}.items())
        (d / "persona.md").write_text(f"---\n{lines}\n---\n\n# {name}\n\ncharter body\n")
        persona.scaffold_memory(name)
        return name


def run_hook(fn, payload: dict):
    """Call a hook handler with ``payload`` on stdin; return parsed stdout JSON or None."""
    old = sys.stdin
    sys.stdin = io.StringIO(json.dumps(payload))
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn()
    finally:
        sys.stdin = old
    out = buf.getvalue().strip()
    return json.loads(out) if out else None
