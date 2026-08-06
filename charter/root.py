"""Locate the control plane this invocation operates on.

The engine used to find its data by its own file location
(``ROOT = Path(__file__).parent.parent``), which is exactly what made it unshippable:
move the code and the data goes with it. Instead, a control plane is marked by a
``charter.toml`` file, and ``charter`` walks up from the working directory to find it —
the same contract git, cargo and npm use, so ``cd`` anywhere inside a control plane and
commands simply work.
"""
from __future__ import annotations

import os
from pathlib import Path

#: The file whose presence marks a directory as a control plane.
MARKER = "charter.toml"

#: Environment override, for scripts, CI, and hooks invoked from elsewhere.
ENV_VAR = "CHARTER_ROOT"


class ControlPlaneNotFound(Exception):
    """No ``charter.toml`` above the starting directory (or at ``$CHARTER_ROOT``)."""


def _explain(where: str) -> str:
    return (f"no {MARKER} found {where}. Run `charter init` to create a control plane "
            f"here, or set ${ENV_VAR} to point at an existing one.")


def find_root(start: Path | None = None) -> Path:
    """The control plane's directory. Raises :class:`ControlPlaneNotFound`.

    ``$CHARTER_ROOT`` wins outright when set — and a bad value raises rather than falling
    back to a walk, because silently operating on a *different* control plane than the one
    the user named is worse than failing.
    """
    env = os.environ.get(ENV_VAR)
    if env:
        p = Path(env).expanduser()
        try:
            p = p.resolve()
        except OSError:
            raise ControlPlaneNotFound(_explain(f"at ${ENV_VAR}={env}")) from None
        if not (p / MARKER).is_file():
            raise ControlPlaneNotFound(_explain(f"at ${ENV_VAR}={env}"))
        return p

    cur = (start or Path.cwd()).resolve()
    for d in (cur, *cur.parents):
        if (d / MARKER).is_file():      # is_file, not exists: a directory is not a marker
            return d
    raise ControlPlaneNotFound(_explain(f"in {cur} or any parent"))


def find_root_or_cwd(start: Path | None = None) -> Path:
    """Like :func:`find_root`, but falls back to the starting directory.

    Import-time path building must never explode — ``charter --version`` and
    ``charter init`` have to work outside a control plane. Commands that genuinely need
    one check :data:`charter.config.HAS_CONTROL_PLANE` and fail with a clear message.

    ``find_root`` itself keeps raising (its own docstring and callers rely on that): this
    function is the one place that guarantees no exception escapes, however hostile the
    environment.
    """
    try:
        return find_root(start)
    except (ControlPlaneNotFound, OSError, RuntimeError):
        # ControlPlaneNotFound is the expected "no charter.toml anywhere" case.
        # OSError/RuntimeError cover find_root's walk hitting something environmental
        # instead: Path.cwd() raising FileNotFoundError (the cwd was deleted out from
        # under the process), .resolve() raising RuntimeError (a symlink loop), or
        # is_file() propagating PermissionError on an inaccessible ancestor directory
        # (it only swallows ENOENT/ENOTDIR/EBADF/ELOOP, not EACCES). None of that is
        # find_root's contract to hide, but it IS this function's contract to survive.
        pass

    try:
        return (start or Path.cwd()).resolve()
    except (OSError, RuntimeError):
        # The plain fallback can itself fail the same way (e.g. cwd deleted, start
        # unresolvable). Prefer the caller's own `start` unresolved when they gave one —
        # still a perfectly usable Path, just not canonicalized, and better than nothing.
        # With no `start` at all, fall back to Path(".") rather than re-touching
        # Path.cwd(): "." is a relative reference that pathlib constructs without any
        # syscall, so it can't raise here even though the process's own working
        # directory is unusable — a sensible "still runs" answer beats raising at
        # import time.
        return start if start is not None else Path(".")
