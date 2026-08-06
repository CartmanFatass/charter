"""Static configuration and well-known paths for the umbrella."""

from __future__ import annotations

import os
from pathlib import Path

#: The GitLab group that owns every EasyDMARC repo.
GROUP = "easydmarc"

#: Umbrella repo root (this package lives at ``<root>/edm``).
ROOT = Path(__file__).resolve().parent.parent

#: Per-task workspaces live here: ``workspaces/<workspace>/<repo>`` (on-demand repo
#: clones) plus the workspace's own ``memory/`` and ``refs/``. Gitignored — a
#: workspace is a private, per-developer, per-task environment. (Renamed from the
#: old ``repos/``; ``workspace._ensure_layout`` migrates an existing ``repos/``.)
WORKSPACES_DIR = ROOT / "workspaces"

#: The always-present workspace used when none is selected.
DEFAULT_WORKSPACE = "default"

#: The durable source of truth: every repo in the group + metadata.
INVENTORY = ROOT / "inventory" / "repos.json"

#: Generated documentation.
DOCS_DIR = ROOT / "docs"

#: Repos that must never appear in the inventory (the umbrella itself).
EXCLUDE = {"easydmarc-umbrella"}

#: Per-developer secrets home — vault registry + plain-file vaults. Gitignored,
#: never committed. Override with ``$EDM_HOME`` (e.g. to share across clones).
EDM_HOME = Path(os.environ.get("EDM_HOME") or (ROOT / ".edm"))

#: Registry of configured vaults (name -> provider + config + persona).
VAULTS_REGISTRY = EDM_HOME / "vaults.json"

#: Default on-disk location for plain-file vaults created without an explicit path.
#: Note: secrets are intentionally **cross-workspace** (global to the developer),
#: so vaults live under EDM_HOME, not inside any workspace.
VAULTS_DIR = EDM_HOME / "vaults"

#: Legacy shared active-workspace pointer. No longer read by ``resolve`` (it caused
#: one task's selection to leak into every other session); kept only so old files
#: don't error. Selection now lives per-terminal + per-session (below).
ACTIVE_WORKSPACE_FILE = EDM_HOME / "active-workspace"

#: Per-Claude-session active-workspace pointers, keyed by session id, so parallel
#: sessions in one clone can each select a different workspace.
SESSIONS_DIR = EDM_HOME / "sessions"

#: Per-terminal active-workspace pointers, keyed by a stable terminal id
#: (``$TERM_SESSION_ID``/``$WINDOWID``/``$TMUX_PANE``/tty). Unlike the Claude session
#: id, a terminal pane survives closing and reopening Claude, so a pane keeps its own
#: workspace across restarts — without leaking into other panes.
TERMINALS_DIR = EDM_HOME / "terminals"

#: Persona definitions — **committed** and shared with the team (unlike vaults).
#: A persona is a directory ``personas/<name>/`` holding ``persona.md`` (the
#: definition), ``memory/`` and ``refs/`` (persistent, committed knowledge). The
#: legacy flat ``personas/<name>.md`` layout still resolves (see ``persona.py``).
PERSONAS_DIR = ROOT / "personas"

#: The cross-persona namespace: ``personas/_shared/{memory,refs}`` is knowledge
#: every persona can read/write. Not itself a persona (excluded from listings).
SHARED_PERSONA = "_shared"

#: Per-developer persona runtime state — **ephemeral** memory (session-scoped
#: scratch, auto-pruned) and the local activity log. Gitignored (under EDM_HOME),
#: never committed; this is the counterpart to the committed ``personas/*/memory``.
PERSONA_STATE_DIR = EDM_HOME / "persona-state"

#: Local pointer to the active persona (set by ``edm persona use``). Overridden
#: by ``$EDM_PERSONA`` and by a command's ``--persona``. Gitignored (in EDM_HOME).
ACTIVE_PERSONA_FILE = EDM_HOME / "active-persona"
