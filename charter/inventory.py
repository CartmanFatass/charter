"""The repo inventory: model, classification, and JSON load/save.

``inventory/repos.json`` is the durable source of truth for what exists in the
group. It is tracked in git and stays complete even when zero repos are cloned.
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


def find(name_or_path: str, doc: dict | None = None):
    """Look a repo up by short name or full ``path_with_namespace``."""
    for r in repos(doc):
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
            "Source of truth for repos in the easydmarc group. "
            "Regenerate with `edm discover`; do not hand-edit."
        ),
        "repos": repo_list,
    }
    config.INVENTORY.parent.mkdir(parents=True, exist_ok=True)
    config.INVENTORY.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    return doc
