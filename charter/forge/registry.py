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
    """Infer a backend from a remote URL, for a repo cloned outside the inventory.

    Only recognises each registered kind's DEFAULT host (``gitlab.com``, ``github.com``)
    — a control plane that declares a self-hosted forge needs :func:`resolve_host`
    instead, which also consults ``charter.toml``. Kept pure/parameterless (no control
    plane to read) so it stays usable without a `root`, and so this exact behaviour
    (used as the base case by ``resolve_host``) stays pinned by its own tests."""
    for kind, cls in KINDS.items():
        probe = cls()
        if probe.host in (url or ""):
            return probe
    return None


def known_forges(root) -> dict[str, Forge]:
    """``host -> Forge`` for every host this control plane's one-credential policy
    covers: every registered kind's DEFAULT host (from :data:`KINDS`, so a new forge kind
    is covered automatically the day it's registered — never a hardcoded literal),
    widened by *root*'s own ``charter.toml`` ``[[forge]]`` blocks. That widening is what
    recognises a DECLARED self-hosted host (``host = "git.internal"``), which no class's
    default host can ever match on its own.

    Shared by the SSH guard (``hooks._known_forges``) and forge resolution
    (:func:`resolve_host`, used by ``gitpolicy.forge_for`` / ``commands._origin_https``)
    so the same host set backs both the denial and the "is this repo compliant" check —
    they can never drift apart.

    Best-effort: reading ``charter.toml`` never raises. This runs on every Bash
    PreToolUse call and every ``git-policy`` check, both of which must degrade to "just
    the default hosts" — never raise or block — on a missing/unreadable/malformed file.
    """
    forges: dict[str, Forge] = {cls().host: cls() for cls in KINDS.values()}
    try:
        from .. import instance
        cfg = instance.load(root)
        for forge, _owner in forges_for(cfg):
            forges[forge.host] = forge
    except Exception:
        pass
    return forges


def resolve_host(url: str, root) -> Forge | None:
    """Like :func:`for_host`, but ALSO recognises a host DECLARED in *root*'s own
    ``charter.toml`` (see :func:`known_forges`) — not just a registered kind's default
    host. This is what lets a self-hosted forge (GitLab Enterprise, GHE) resolve to its
    own real policy instead of silently falling through to another forge's.

    Returns ``None`` when *url*'s host matches neither a default nor a declared forge —
    genuinely unrecognised. Callers MUST treat that as **unmanaged**, never silently
    apply another forge's policy: doing so is what let a self-hosted clone report a
    false-green "token-only" while its SSH remote was never actually rewritten off SSH
    (see ``gitpolicy.forge_for``)."""
    for host, forge in known_forges(root).items():
        if host in (url or ""):
            return forge
    return None
