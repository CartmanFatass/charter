"""The repo inventory: model, classification, and JSON load/save.

``inventory/repos.json`` is the durable source of truth for what exists in the
group. It is tracked in git and stays complete even when zero repos are cloned.

A control plane may span several forges (``[[forge]]`` blocks in ``charter.toml``);
:func:`merge` combines their per-forge repo lists into one inventory, keyed by bare
name, and :func:`find` accepts an optional ``<forge>:`` qualifier to disambiguate a
name two forges both expose. See ``charter/forge/registry.py`` for how a forge block
resolves to a backend.
"""

from __future__ import annotations

import json

from . import config


def classify_kind(name: str) -> str:
    """Coarse role inferred from the repo name (descriptive metadata)."""
    n = name.lower()
    if n.endswith("-workspace"):
        return "workspace"
    if n.endswith("-docs"):
        return "docs"
    if n.endswith("-frontend") or "-ui-" in n or n.endswith("-ui"):
        return "frontend"
    if n.endswith("-service") or n.endswith("-services") or n.endswith("-engine"):
        return "service"
    if n.endswith("-api") or "gateway" in n:
        return "api"
    if n.endswith("-core"):
        return "core"
    return "app"


def classify_stack(files) -> str:
    """Detect the primary build stack from root-level file names."""
    fs = set(files)
    if "nx.json" in fs:
        return "nx"
    if "pom.xml" in fs or ".mvn" in fs:
        return "java-maven"
    if {"build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"} & fs:
        return "java-gradle"
    if "go.mod" in fs:
        return "go"
    if "Cargo.toml" in fs:
        return "rust"
    if {"pyproject.toml", "requirements.txt", "Pipfile", "setup.py"} & fs:
        return "python"
    if "pnpm-workspace.yaml" in fs:
        return "node-monorepo"
    if "package.json" in fs:
        return "node"
    if "composer.json" in fs:
        return "php"
    if "Gemfile" in fs:
        return "ruby"
    if {"Chart.yaml", "helmfile.yaml"} & fs:
        return "helm"
    if any(f.endswith(".tf") for f in files):
        return "terraform"
    if "Dockerfile" in fs:
        return "docker"
    return "unknown"


def load() -> dict:
    """Load the inventory document, or an empty skeleton if none exists."""
    if not config.INVENTORY.exists():
        return {"group": config.GROUP, "count": 0, "repos": []}
    return json.loads(config.INVENTORY.read_text())


def repos(doc: dict | None = None) -> list:
    return (doc or load()).get("repos", [])


def merge(batches: list[list[dict]]) -> list[dict]:
    """Combine per-forge repo lists into one inventory.

    Repos are keyed by BARE NAME so every existing command, doc and habit keeps working.
    A collision across forges is REFUSED rather than resolved by guessing: the workspace
    path on disk is derived from that name, so picking one silently would let two
    different repos clone over each other.
    """
    from .forge.registry import CollisionError
    seen: dict[str, dict] = {}
    for batch in batches:
        for r in batch:
            prev = seen.get(r["name"])
            if prev is not None and prev.get("forge") != r.get("forge"):
                raise CollisionError(
                    f"both {prev.get('forge')} and {r.get('forge')} expose a repo named "
                    f"{r['name']!r}. Qualify it — e.g. `{r.get('forge')}:{r['name']}` — "
                    f"or exclude one in charter.toml.")
            seen[r["name"]] = r
    return sorted(seen.values(), key=lambda r: r["name"])


def find(repos: list, name_or_path: str):
    """Look a repo up by short name, full ``path_with_namespace``, or a
    ``<forge>:<name>``-qualified name for disambiguating a cross-forge collision.

    Takes the caller's repo list explicitly (rather than the whole inventory doc) so it
    composes with :func:`merge` — both operate on plain ``list[dict]``.

    The known-kinds check is a deferred import of ``registry.KINDS`` rather than a
    duplicated literal: nothing under ``charter.forge`` imports back into ``inventory``
    or ``config`` (they only reach ``charter.util`` and stdlib), so there is no import
    cycle to dodge — a plain (if deferred, to keep this module cheap to import before
    any forge backend is needed) import is all that's required.
    """
    from .forge import registry
    kind, sep, bare = (name_or_path or "").partition(":")
    if sep and kind in registry.KINDS:
        for r in repos:
            if r.get("forge") == kind and (r["name"] == bare
                                           or r["path_with_namespace"] == bare):
                return r
        return None
    for r in repos:
        if r["name"] == name_or_path or r["path_with_namespace"] == name_or_path:
            return r
    return None


def save(repo_list: list) -> dict:
    """Write the inventory, sorted stably so diffs reflect real changes only.

    Deliberately carries no generated-at timestamp: this file is tracked, and a
    volatile timestamp would churn git history on every discover.
    """
    repo_list = sorted(repo_list, key=lambda r: r["name"])
    doc = {
        "group": config.GROUP,
        "count": len(repo_list),
        "note": (
            f"Source of truth for repos in the {config.GROUP} group. "
            "Regenerate with `charter discover`; do not hand-edit."
        ),
        "repos": repo_list,
    }
    config.INVENTORY.parent.mkdir(parents=True, exist_ok=True)
    config.INVENTORY.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    return doc
