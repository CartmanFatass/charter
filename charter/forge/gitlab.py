"""GitLab, over the `glab` CLI."""
from __future__ import annotations

import json
import urllib.parse

from .. import util
from .base import CI_STATES, ForgeError

#: GitLab pipeline status → the neutral vocabulary. Anything unlisted becomes None
#: rather than being invented, so a new upstream state degrades to "unknown", not a lie.
_CI_MAP = {
    "success": "success", "failed": "failed", "running": "running",
    "canceled": "canceled", "skipped": "skipped", "manual": "manual",
    "pending": "pending", "created": "pending", "preparing": "pending",
    "waiting_for_resource": "pending", "scheduled": "pending",
}


class GitLabForge:
    kind = "gitlab"
    cli = "glab"
    change_sigil = "!"

    def __init__(self, host: str = "gitlab.com") -> None:
        self.host = host

    # --- plumbing -----------------------------------------------------------------
    def _glab(self, args, check: bool = True):
        return util.run([self.cli, *args], check=check)

    def _api(self, path: str):
        """Best-effort JSON GET. Returns None on any failure — callers feed the status
        line, which renders every turn and must never crash."""
        p = self._glab(["api", path], check=False)
        if p.returncode != 0:
            return None
        try:
            return json.loads(p.stdout) if p.stdout.strip() else None
        except ValueError:
            return None

    # --- protocol -----------------------------------------------------------------
    def check_auth(self) -> None:
        p = self._glab(["auth", "status"], check=False)
        if p.returncode != 0:
            raise ForgeError(
                f"glab is not authenticated for {self.host}. Run: glab auth login")

    def list_repos(self, owner: str) -> list[dict]:
        enc = urllib.parse.quote(owner, safe="")
        out, page = [], 1
        while True:
            batch = self._api(
                f"groups/{enc}/projects?per_page=100&page={page}"
                "&include_subgroups=true&archived=false") or []
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return [self._normalize(p) for p in out]

    def _normalize(self, p: dict) -> dict:
        return {
            "id": p.get("id"),
            "name": p.get("path") or p.get("name"),
            "path_with_namespace": p.get("path_with_namespace"),
            "default_branch": p.get("default_branch"),
            "description": p.get("description") or "",
            "web_url": p.get("web_url") or "",
            "ssh_url": p.get("ssh_url_to_repo") or "",
            "topics": p.get("topics") or [],
            "forge": self.kind,
        }

    def repo_tree(self, repo: dict, ref: str | None = None) -> list[str]:
        rid = repo.get("id")
        q = f"projects/{rid}/repository/tree?per_page=100"
        if ref:
            q += f"&ref={urllib.parse.quote(str(ref), safe='')}"
        return [e.get("name", "") for e in (self._api(q) or [])]

    def open_change(self, path: str, branch: str) -> int | None:
        enc = urllib.parse.quote(path, safe="")
        br = urllib.parse.quote(branch, safe="")
        arr = self._api(
            f"projects/{enc}/merge_requests?state=opened&source_branch={br}&per_page=1")
        return arr[0].get("iid") if arr else None

    def ci_status(self, path: str, branch: str) -> str | None:
        enc = urllib.parse.quote(path, safe="")
        br = urllib.parse.quote(branch, safe="")
        arr = self._api(f"projects/{enc}/pipelines?ref={br}&per_page=1")
        if not arr:
            return None
        state = _CI_MAP.get(arr[0].get("status") or "")
        return state if state in CI_STATES else None

    def credential_helper(self) -> str:
        return f"!{self.cli} auth git-credential"

    def insteadof(self) -> tuple[str, tuple[str, ...]]:
        return (f"https://{self.host}/",
                (f"git@{self.host}:", f"ssh://git@{self.host}/"))
