"""**Golden rule: one credential.** Every git operation in the umbrella — and in every repo
clone under it — authenticates with the **glab token over HTTPS**. No SSH keys, no 1Password
agent, no commit signing. A single credential the whole org shares, so an agent never stalls
on a key prompt, a signer hang, or a missing SSH config.

**SCOPE — the umbrella and its clones, nothing outside.** Everything here is written with
``git config --local``, so it applies to the umbrella repo and the clones under
``workspaces/``, and **never** to a developer's global/system git config. A developer's own
preferences (e.g. a global ``commit.gpgsign=true``) keep working everywhere else on their
machine; inside the umbrella the local value simply wins, which is exactly what stops an
agent stalling on a signer prompt. Do **not** widen this to ``--global`` / an ``includeIf``
stanza — tests assert the boundary (`test_git_policy.py`).

This module makes that **mechanically true** rather than merely documented, by writing three
things into a repo's *local* git config (never global — we don't touch a developer's machine):

1. ``credential.helper = !glab auth git-credential`` — git asks glab for the token.
2. ``commit.gpgsign=false`` / ``tag.gpgsign=false`` — signing can't hang the agent.
3. ``url.https://gitlab.com/.insteadOf`` for both SSH forms — so even a repo whose *remote is
   an SSH URL* transparently transports over HTTPS+token. This is what makes a plain
   ``git push`` typed by any agent (or any persona sub-agent) work without SSH.

With (3) in place the rule holds even for clones the umbrella didn't create; the PreToolUse
guard in :mod:`edm.hooks` then blocks the deliberate bypasses (explicit SSH remotes,
``GIT_SSH_COMMAND``, ``-S``/``--gpg-sign``).
"""

from __future__ import annotations

from pathlib import Path

from . import util

#: Simple ``key = value`` settings every repo gets.
POLICY: dict[str, str] = {
    "credential.helper": "!glab auth git-credential",
    "commit.gpgsign": "false",
    "tag.gpgsign": "false",
}

#: SSH URL prefixes rewritten to HTTPS (multi-valued ``insteadOf`` under one url key).
HTTPS_BASE = "https://gitlab.com/"
SSH_FORMS = ("git@gitlab.com:", "ssh://git@gitlab.com/")
_URL_KEY = f"url.{HTTPS_BASE}.insteadOf"


def _git(args, cwd):
    return util.run(["git", *args], cwd=cwd, check=False)


def is_git_repo(path: Path) -> bool:
    return (Path(path) / ".git").exists()


def _local_config(repo: Path) -> dict[str, list[str]]:
    """The repo's LOCAL config as ``{key: [values]}`` in **one** git call (git parses its own
    format, so this stays correct) — cheap enough to check every clone at preflight."""
    p = _git(["config", "--local", "--list", "-z"], cwd=repo)
    out: dict[str, list[str]] = {}
    if p.returncode != 0:
        return out
    for rec in (p.stdout or "").split("\0"):
        if not rec:
            continue
        key, _, val = rec.partition("\n")
        out.setdefault(key.strip().lower(), []).append(val)
    return out


def _get_all(repo: Path, key: str) -> list[str]:
    return _local_config(repo).get(key.lower(), [])


def check(repo: Path, cfg: dict[str, list[str]] | None = None) -> list[str]:
    """Policy settings that are MISSING/wrong in ``repo``'s local config (empty = compliant).
    Pass ``cfg`` to reuse an already-read config (avoids a second git call)."""
    cfg = _local_config(repo) if cfg is None else cfg
    drift = []
    for key, want in POLICY.items():
        got = cfg.get(key.lower(), [])
        if not got or got[-1] != want:
            drift.append(f"{key} != {want}")
    have = set(cfg.get(_URL_KEY.lower(), []))
    for ssh in SSH_FORMS:
        if ssh not in have:
            drift.append(f"{_URL_KEY} missing {ssh}")
    return drift


def non_compliant(root: Path, workspaces_dir: Path) -> list[Path]:
    """Every repo in scope (umbrella + clones) whose local config isn't token-only."""
    return [r for r in repos(root, workspaces_dir) if check(r)]


def apply(repo: Path) -> list[str]:
    """Write the token-only policy into ``repo``'s **local** config (idempotent).
    Returns the settings changed. Never touches global/system config."""
    repo = Path(repo)
    if not is_git_repo(repo):
        return []
    changed = []
    for key, want in POLICY.items():
        got = _get_all(repo, key)
        if not got or got[-1] != want:
            _git(["config", "--local", key, want], cwd=repo)
            changed.append(f"{key}={want}")
    have = set(_get_all(repo, _URL_KEY))
    for ssh in SSH_FORMS:
        if ssh not in have:
            _git(["config", "--local", "--add", _URL_KEY, ssh], cwd=repo)
            changed.append(f"rewrite {ssh} → {HTTPS_BASE}")
    return changed


def repos(root: Path, workspaces_dir: Path) -> list[Path]:
    """The umbrella itself plus every repo clone under ``workspaces/<ws>/<repo>``."""
    out = [Path(root)] if is_git_repo(root) else []
    if Path(workspaces_dir).exists():
        for ws in sorted(Path(workspaces_dir).iterdir()):
            if not ws.is_dir():
                continue
            for clone in sorted(ws.iterdir()):
                if clone.is_dir() and is_git_repo(clone):
                    out.append(clone)
    return out
