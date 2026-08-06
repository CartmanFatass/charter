"""Per-repo GitLab state (open MR + last CI/CD pipeline) for the status line,
cached + refreshed in the background so a render never blocks on the network.

The status line reads the cache (``read_for``) and, if it's stale, kicks off a
**detached** ``charter gl-refresh`` (``maybe_spawn``) that queries GitLab via
``glab`` and rewrites the cache. A SessionStart hook seeds it with a full
``glab`` environment; ``charter gl-refresh`` runs it on demand.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

from . import config

DISPLAY_TTL = 7200    # show a cached value for up to 2h
REFRESH_TTL = 300     # try to refresh entries older than 5 min
SPAWN_COOLDOWN = 120  # at most one background refresh per this many seconds


def _cache_file() -> Path:
    return config.EDM_HOME / "cache" / "glstate.json"


def _lock_file() -> Path:
    return config.EDM_HOME / "cache" / "glstate.refreshing"


def load() -> dict:
    try:
        return json.loads(_cache_file().read_text())
    except Exception:
        return {}


def _save(cache: dict) -> None:
    f = _cache_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(cache))
    except Exception:
        pass


def read_for(dirs, branches: dict) -> dict:
    """{dir: {"mr": iid|None, "ci": status|None}} for fresh, branch-matching entries."""
    cache = load()
    now = time.time()
    out = {}
    for d in dirs:
        ent = cache.get(str(d))
        if not ent or ent.get("branch") != branches.get(d):
            continue
        if now - ent.get("ts", 0) > DISPLAY_TTL:
            continue
        out[d] = {"mr": ent.get("mr"), "ci": ent.get("ci")}
    return out


def maybe_spawn(dirs, workspace: str | None = None) -> None:
    """Kick off a detached background refresh if the cache is stale. Non-blocking.

    ``workspace`` is passed to the child explicitly (via ``--workspace``) because
    the status line's env is scrubbed — the child couldn't otherwise resolve the
    session's active workspace on its own.
    """
    now = time.time()
    lock = _lock_file()
    try:
        if lock.exists() and now - lock.stat().st_mtime < SPAWN_COOLDOWN:
            return
    except Exception:
        return
    cache = load()
    stale = any(
        str(d) not in cache or now - cache[str(d)].get("ts", 0) > REFRESH_TTL
        for d in dirs
    )
    if not stale:
        return
    # Target the installed package via `-m`, not a path inside the control plane —
    # a control plane has no `bin/edm` (that script lived in the old monorepo the
    # engine was extracted from); `-m charter` resolves through the same
    # interpreter/venv that is already running this process.
    cmd = [sys.executable, "-m", "charter", "gl-refresh"]
    if workspace:
        cmd += ["--workspace", workspace]
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True, env=os.environ.copy(),
        )
    except Exception:
        # Spawn never happened — don't start the cooldown, so a transient failure
        # (rather than a real refresh) doesn't suppress the next render's retry.
        return
    try:
        lock.touch()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# refresh (runs in the background / on demand — network allowed here)          #
# --------------------------------------------------------------------------- #
def refresh(dirs) -> dict:
    """For each clone's current branch, fetch its open MR + last pipeline status."""
    cache = load()
    now = time.time()
    for d in dirs:
        branch = _branch(d)
        pwn = _remote_path(d)
        mr = ci = None
        if pwn and branch and branch != "?":
            mr = _open_mr_iid(pwn, branch)
            ci = _last_pipeline_status(pwn, branch)
        cache[str(d)] = {"branch": branch, "mr": mr, "ci": ci, "ts": now}
    _save(cache)
    return cache


def _api(path: str):
    try:
        out = subprocess.run(
            ["glab", "api", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=15,
        ).stdout.strip()
        return json.loads(out) if out else None
    except Exception:
        return None


def _open_mr_iid(pwn: str, branch: str):
    enc, br = urllib.parse.quote(pwn, safe=""), urllib.parse.quote(branch, safe="")
    arr = _api(f"projects/{enc}/merge_requests?state=opened&source_branch={br}&per_page=1")
    return arr[0].get("iid") if arr else None


def _last_pipeline_status(pwn: str, branch: str):
    enc, br = urllib.parse.quote(pwn, safe=""), urllib.parse.quote(branch, safe="")
    arr = _api(f"projects/{enc}/pipelines?ref={br}&per_page=1")
    return arr[0].get("status") if arr else None


def _branch(d: Path) -> str:
    try:
        txt = (d / ".git" / "HEAD").read_text().strip()
    except Exception:
        return "?"
    if txt.startswith("ref:"):
        return txt.split("/", 2)[-1] or "?"
    return txt[:7] if txt else "?"


def _remote_path(d: Path):
    """path_with_namespace from the clone's origin remote (ssh or https)."""
    try:
        url = subprocess.run(
            ["git", "-C", str(d), "remote", "get-url", "origin"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=3,
        ).stdout.strip()
    except Exception:
        return None
    if not url:
        return None
    if url.endswith(".git"):
        url = url[:-4]
    if "://" in url:
        return urllib.parse.urlparse(url).path.lstrip("/") or None
    if ":" in url:  # scp-like: git@host:group/sub/repo
        return url.split(":", 1)[1] or None
    return None
