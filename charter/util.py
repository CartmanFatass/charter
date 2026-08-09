"""Process execution, coloured logging, and small helpers.

Everything here is stdlib-only so the CLI runs with a bare ``python3``.
"""

from __future__ import annotations

import subprocess
import sys
import urllib.parse
from typing import Sequence

_USE_COLOR = sys.stderr.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def info(msg: str) -> None:
    print(_c("36", "•") + " " + msg, file=sys.stderr)


def ok(msg: str) -> None:
    print(_c("32", "✓") + " " + msg, file=sys.stderr)


def warn(msg: str) -> None:
    print(_c("33", "!") + " " + msg, file=sys.stderr)


def err(msg: str) -> None:
    print(_c("31", "✗") + " " + msg, file=sys.stderr)


class ProcError(RuntimeError):
    """A subprocess exited non-zero while ``check=True``."""

    def __init__(self, cmd: Sequence[str], returncode: int, stderr: str) -> None:
        self.cmd = list(cmd)
        self.returncode = returncode
        self.stderr = (stderr or "").strip()
        super().__init__(
            f"command failed ({returncode}): {' '.join(self.cmd)}\n{self.stderr}"
        )


def run(
    cmd: Sequence[str], cwd=None, check: bool = True, capture: bool = True,
    input: str | None = None,
) -> subprocess.CompletedProcess:
    """Run ``cmd``. Raises :class:`ProcError` on failure when ``check``.

    ``input`` is written to the child's stdin. That is how a secret reaches a CLI
    without ever appearing in argv — where `ps` and the shell's history can see it.
    """
    proc = subprocess.run(
        list(cmd),
        cwd=cwd,
        text=True,
        input=input,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if check and proc.returncode != 0:
        raise ProcError(cmd, proc.returncode, proc.stderr if capture else "")
    return proc


def urlenc(s: str) -> str:
    """URL-encode a path segment (e.g. a group path with slashes)."""
    return urllib.parse.quote(s, safe="")
