"""Per-instance configuration, read from the control plane's ``charter.toml``.

``GROUP``, ``EXCLUDE`` and the default workspace name used to be constants baked into the
engine — hardcoded to one organisation. Moving them here is what lets a single installed
``charter`` serve any number of control planes.

Uses stdlib ``tomllib`` (Python 3.11+), which is why the floor is 3.11: it keeps the
zero-dependency promise that YAML would have ended.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from .root import MARKER

#: Layout version this engine understands.
SCHEMA = 1


class SchemaTooNew(Exception):
    """The control plane was written by a newer charter than this one."""


def load(root: Path) -> dict:
    """Parse ``<root>/charter.toml``. Returns ``{}`` when there is no such file.

    A *newer* schema raises rather than being read on a best-effort basis: silently
    misreading a persona or workspace layout is worse than refusing to run.
    """
    p = Path(root) / MARKER
    try:
        raw = p.read_bytes()
    except OSError:
        return {}
    try:
        cfg = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"{p} is not valid TOML: {e}") from None
    found = cfg.get("schema", SCHEMA)
    if isinstance(found, int) and found > SCHEMA:
        raise SchemaTooNew(
            f"{p} declares schema {found}, but this charter understands {SCHEMA}. "
            f"Upgrade charter (`uv tool upgrade charter`)."
        )
    return cfg


def _first_forge(cfg: dict) -> dict:
    forges = cfg.get("forge") or []
    return forges[0] if forges else {}


def group_of(cfg: dict, fallback: str) -> str:
    """The group/org whose repos this control plane tracks."""
    return _first_forge(cfg).get("group") or fallback


def exclude_of(cfg: dict) -> set[str]:
    """Repo names that must never enter the inventory."""
    return set(_first_forge(cfg).get("exclude") or ())


def default_workspace_of(cfg: dict, fallback: str) -> str:
    return (cfg.get("workspace") or {}).get("default") or fallback
