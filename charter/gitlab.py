"""Thin wrappers over the ``glab`` CLI for GitLab REST access.

We shell out to ``glab api`` rather than hitting HTTP ourselves so the CLI
reuses the developer's existing ``glab`` auth (token, host, SSO) with zero
configuration and no third-party dependencies.
"""

from __future__ import annotations

import json

from . import util


def _glab(args, check: bool = True):
    return util.run(["glab", *args], check=check)


def check_auth() -> None:
    """Fail fast with a helpful message if ``glab`` is not logged in."""
    proc = _glab(["auth", "status"], check=False)
    blob = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 or "Logged in" not in blob:
        raise SystemExit(
            "glab is not authenticated. Run `glab auth login` (host: gitlab.com) "
            "and retry.\n" + blob.strip()
        )


def api(path: str):
    """GET ``path`` from the GitLab API and parse the JSON body."""
    out = _glab(["api", path]).stdout.strip()
    return json.loads(out) if out else None


def api_paginated(path: str, per_page: int = 100) -> list:
    """Follow keyset-free pagination, concatenating every page."""
    sep = "&" if "?" in path else "?"
    results: list = []
    page = 1
    while True:
        chunk = api(f"{path}{sep}per_page={per_page}&page={page}")
        if not chunk:
            break
        results.extend(chunk)
        if len(chunk) < per_page:
            break
        page += 1
    return results


def list_group_projects(group: str) -> list:
    """Every non-archived project in the group, including nested subgroups."""
    return api_paginated(
        f"groups/{util.urlenc(group)}/projects"
        "?include_subgroups=true&archived=false&order_by=path&sort=asc"
    )


def repo_tree(project_id, ref: str | None = None) -> list[str]:
    """Root-level file/dir names of a repo (used to classify its stack).

    Returns an empty list for empty repos or on any access error, so a single
    unreachable repo never breaks a discovery run.
    """
    path = f"projects/{project_id}/repository/tree"
    if ref:
        path += f"?ref={util.urlenc(ref)}"
    try:
        items = api_paginated(path)
    except (util.ProcError, ValueError):
        return []
    return [it.get("name", "") for it in (items or [])]
