"""The forge-agnostic contract.

`charter` talks to a code-hosting forge for five things: authentication, enumerating an
owner's repos, reading a repo's file list, and — per branch — the open change and the CI
result. Everything above this module is written against the protocol, so adding a forge
means adding one file rather than touching call sites.

Vocabulary is deliberately neutral. GitLab says "merge request" and has exactly one
pipeline per commit; GitHub says "pull request" and has N check-runs with no inherent
single value. `ci_status` therefore returns one of :data:`CI_STATES` from either, and
`change_sigil` carries each forge's native rendering so neither audience reads the
other's jargon.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

#: The neutral CI vocabulary every implementation maps onto.
CI_STATES = frozenset({"success", "failed", "running", "pending",
                       "manual", "canceled", "skipped"})


class ForgeError(Exception):
    """A forge CLI is missing, unauthenticated, or returned something unusable."""


#: Keys every implementation must produce for each repo from :meth:`Forge.list_repos`.
#: ``forge`` names the implementation that produced it, so a mixed inventory stays
#: unambiguous once records from several forges are merged.
REPO_KEYS = ("name", "path_with_namespace", "default_branch", "description",
             "web_url", "ssh_url", "topics", "id", "forge")


@runtime_checkable
class Forge(Protocol):
    """What `charter` needs from a code-hosting forge."""

    kind: str            #: "gitlab" | "github"
    host: str            #: "gitlab.com", "github.com", or a self-hosted host
    cli: str             #: the CLI binary that holds the credential
    change_sigil: str    #: "!" for a GitLab MR, "#" for a GitHub PR

    def check_auth(self) -> None:
        """Raise :class:`ForgeError` unless the CLI is installed and logged in."""

    def list_repos(self, owner: str) -> list[dict]:
        """Every repo under *owner* (a GitLab group, or a GitHub org/user), each record
        carrying :data:`REPO_KEYS`."""

    def repo_tree(self, repo: dict, ref: str | None = None) -> list[str]:
        """Top-level file names in *repo*, used to detect its stack."""

    def open_change(self, path: str, branch: str) -> int | None:
        """The open MR/PR number for *branch*, or None."""

    def ci_status(self, path: str, branch: str) -> str | None:
        """The branch's CI result as one of :data:`CI_STATES`, or None."""

    def credential_helper(self) -> str:
        """The git ``credential.helper`` value that makes git use this forge's token."""

    def insteadof(self) -> tuple[str, tuple[str, ...]]:
        """``(https_base, ssh_forms)`` — the SSH prefixes git must rewrite to HTTPS, so a
        repo whose remote is an SSH URL still transports over HTTPS with a token."""
