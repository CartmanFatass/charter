"""Which forge does this control plane — or this repo — use?

A control plane declares one or more ``[[forge]]`` blocks in ``charter.toml``. Each repo
record carries a ``forge`` stamp so a merged, multi-forge inventory stays unambiguous.
"""
from __future__ import annotations

from .base import Forge
from .github import GitHubForge
from .gitlab import GitLabForge

KINDS: dict[str, type] = {"gitlab": GitLabForge, "github": GitHubForge}

#: Records written before the stamp existed are GitLab's, since that was the only backend.
_DEFAULT_KIND = "gitlab"


class CollisionError(Exception):
    """Two forges expose a repo with the same bare name."""


def _build(kind: str, host: str | None) -> Forge:
    cls = KINDS.get(kind)
    if cls is None:
        raise ValueError(
            f"unknown forge kind {kind!r} — known kinds: {', '.join(sorted(KINDS))}")
    return cls(host) if host else cls()


def forges_for(cfg: dict) -> list[tuple[Forge, str]]:
    """``(forge, owner)`` for every ``[[forge]]`` block in a control plane's config."""
    out = []
    for block in cfg.get("forge") or []:
        owner = block.get("group") or block.get("owner") or ""
        out.append((_build(block.get("kind") or _DEFAULT_KIND, block.get("host")), owner))
    return out


def for_repo(repo: dict) -> Forge:
    """The backend that owns a repo record, from its ``forge`` stamp."""
    return _build(repo.get("forge") or _DEFAULT_KIND, None)


def for_host(url: str) -> Forge | None:
    """Infer a backend from a remote URL, for a repo cloned outside the inventory."""
    for kind, cls in KINDS.items():
        probe = cls()
        if probe.host in (url or ""):
            return probe
    return None
