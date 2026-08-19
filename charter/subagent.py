"""Subagent tracking and tree visualization for Codex and other harnesses.

This module provides data models, rollout scanners, hierarchy builders, and
renderers for tracking subagents (such as Codex multi-agent/collab threads,
hierarchical dispatches, and spawned subagents).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal, Mapping

from . import tui

SubagentStatus = Literal["starting", "running", "completed", "error"]
RuntimeState = Literal["starting", "running", "stopped", "unknown"]


def legacy_status_to_runtime_state(status: SubagentStatus) -> RuntimeState:
    """Map legacy status value to neutral runtime state."""
    if status == "starting":
        return "starting"
    if status == "running":
        return "running"
    if status in ("completed", "error"):
        return "stopped"
    return "unknown"
ACTIVE_WINDOW_SECONDS = 45.0
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_WATCH_INTERVAL = 10.0
COMM_ACTIVE_WINDOW_SECONDS = 15.0
COMM_BLAZING_WINDOW_SECONDS = 3.5

# Branch animation frames for information flow (strictly 3 columns wide)
BRANCH_FLOW_DOWN_FRAMES = ["├▸ ", "├► ", "├➔ ", "├▼ "]
BRANCH_FLOW_DOWN_LAST_FRAMES = ["└▸ ", "└► ", "└➔ ", "└▼ "]

BRANCH_FLOW_UP_FRAMES = ["├◂ ", "├◄ ", "├◀ ", "├▲ "]
BRANCH_FLOW_UP_LAST_FRAMES = ["└◂ ", "└◄ ", "└◀ ", "└▲ "]

BRANCH_FLOW_TOOL_FRAMES = ["├⚙ ", "├⚡ ", "├⚙ ", "├⚡ "]
BRANCH_FLOW_TOOL_LAST_FRAMES = ["└⚙ ", "└⚡ ", "└⚙ ", "└⚡ "]

BRANCH_FLOW_PEER_FRAMES = ["├➔ ", "├⇄ ", "├➔ ", "├⇄ "]
BRANCH_FLOW_PEER_LAST_FRAMES = ["└➔ ", "└⇄ ", "└➔ ", "└⇄ "]
_ROLLOUT_FILE_RE = re.compile(
    r"^rollout-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})-([A-Za-z0-9._-]+)\.jsonl$"
)
# ANSI styling
_R, _DIM, _BOLD = "\033[0m", "\033[2m", "\033[1m"
_CYAN, _YELLOW, _MAGENTA, _GREEN = "\033[36m", "\033[33m", "\033[35m", "\033[32m"
_BLUE, _RED = "\033[34m", "\033[31m"

# Animation frames
SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
PULSE_FRAMES = ["●", "◉", "○", "◌"]

# Icons
ICON_COMPLETED = "✔"
ICON_ERROR = "✖"
ICON_STARTING = "○"
ICON_RUNNING = "▶"
ICON_MSG = "💬"
ICON_FLOW = "➔"
ICON_TOOL = "🔧"
@dataclass
class SubagentInfo:
    """Information about a known subagent."""

    id: str
    name: str
    status: SubagentStatus
    started_at: datetime | None = None
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "runtime_state": legacy_status_to_runtime_state(self.status),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "model": self.model,
        }

@dataclass
class SubagentTreeNode:
    """A node in the hierarchical subagent tree."""

    id: str
    name: str
    status: SubagentStatus
    depth: int = 1
    children: list[SubagentTreeNode] = field(default_factory=list)
    started_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "runtime_state": legacy_status_to_runtime_state(self.status),
            "depth": self.depth,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "children": [c.to_dict() for c in self.children],
        }

@dataclass
class SubagentTree:
    """Root container for a session's subagent hierarchy."""

    root_id: str
    nodes: list[SubagentTreeNode] = field(default_factory=list)
    total_count: int = 0
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_id": self.root_id,
            "total_count": self.total_count,
            "updated_at": self.updated_at.isoformat(),
            "nodes": [n.to_dict() for n in self.nodes],
        }


@dataclass
class SessionLink:
    """Link between a rollout session and its parent."""

    id: str
    parent_id: str | None
    name: str
    started_at: datetime | None
    modified_at: datetime
    cwd: str | None = None


@dataclass(frozen=True)
class RolloutRecord:
    session_id: str
    file_path: Path
    line_number: int
    entry_type: str
    raw_kind: str
    timestamp: datetime | None
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class SessionIndex:
    links: Mapping[str, SessionLink]
    rollout_files: Mapping[str, Path]
    children: Mapping[str, tuple[str, ...]]

@dataclass
class SubagentExchange:
    """A communication exchange between session and subagent, or between subagents."""

    sender_id: str
    sender_name: str
    receiver_id: str
    receiver_name: str
    kind: str  # 'dispatch', 'prompt', 'response', 'collab_msg', 'tool_call', 'tool_result', 'task_complete'
    content: str
    timestamp: datetime
    tool_name: str | None = None
    turn_id: str | None = None
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "receiver_id": self.receiver_id,
            "receiver_name": self.receiver_name,
            "kind": self.kind,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "tool_name": self.tool_name,
            "turn_id": self.turn_id,
            "duration_ms": self.duration_ms,
        }

# --------------------------------------------------------------------------
# Codex Home & Sessions Path Discovery
# --------------------------------------------------------------------------

def get_codex_home() -> Path:
    """Return the resolved directory where Codex stores configuration and sessions.

    Honours $CODEX_HOME if set, otherwise falls back to ~/.codex or ~/.codex_home.
    """
    env_home = os.environ.get("CODEX_HOME")
    if env_home:
        p = Path(env_home).expanduser().resolve()
        return p

    home_dir = _safe_home_dir()
    for candidate in (home_dir / ".codex", home_dir / ".codex_home"):
        try:
            if candidate.is_dir():
                return candidate.resolve()
        except OSError:
            pass

    return home_dir / ".codex"


def get_sessions_dir() -> Path:
    """Return the resolved sessions directory for Codex rollouts."""
    env_sess = os.environ.get("CODEX_SESSIONS_PATH")
    if env_sess:
        p = Path(env_sess).expanduser().resolve()
        return p
    return get_codex_home() / "sessions"


def _safe_home_dir() -> Path:
    """Determine home directory safely without throwing on stripped environments."""
    try:
        return Path.home()
    except Exception:
        raw = os.environ.get("USERPROFILE") or os.environ.get("HOME") or "."
        return Path(raw).resolve()


def _normalize_path(p: str | Path | None) -> str | None:
    if not p:
        return None
    try:
        return str(Path(p).expanduser().resolve())
    except Exception:
        return str(p)


def parse_datetime(val: Any) -> datetime | None:
    """Parse various timestamp formats into a datetime object."""
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, tz=timezone.utc)
    if isinstance(val, str):
        val = val.strip()
        try:
            # Handle ISO string with Z or offset
            norm = val.replace("Z", "+00:00")
            return datetime.fromisoformat(norm)
        except Exception:
            pass
    return None


# --------------------------------------------------------------------------
# Rollout Discovery & Peeking
# --------------------------------------------------------------------------

def parse_rollout_filename(filename: str) -> tuple[datetime, str] | None:
    """Extract (timestamp, session_id) from rollout filename."""
    m = _ROLLOUT_FILE_RE.match(filename)
    if not m:
        return None
    stamp_raw, session_id = m.group(1), m.group(2)
    # Convert YYYY-MM-DDTHH-MM-SS to YYYY-MM-DDTHH:MM:SS
    # Replace last two dashes with colons
    parts = stamp_raw.split("T")
    if len(parts) == 2:
        date_part, time_part = parts[0], parts[1]
        time_part_fixed = time_part.replace("-", ":")
        stamp_str = f"{date_part}T{time_part_fixed}+00:00"
    else:
        stamp_str = stamp_raw
    try:
        dt = datetime.fromisoformat(stamp_str)
        return dt, session_id
    except Exception:
        return None


def read_first_line(file_path: Path, max_bytes: int = 1024 * 1024) -> str | None:
    """Read the first line of a file efficiently."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            line = f.readline(max_bytes)
            return line.strip() if line else None
    except OSError:
        return None


def _parent_id_from_payload(payload: Any) -> str | None:
    """Extract parent session/thread id from payload, including nested source fields."""
    if not isinstance(payload, dict):
        return None
    pid = payload.get("parent_thread_id") or payload.get("parent_id") or payload.get("parent_session_id")
    if pid and isinstance(pid, str):
        return pid.strip()
    source = payload.get("source")
    if isinstance(source, dict):
        sub = source.get("subagent")
        if isinstance(sub, dict):
            pid = sub.get("parent_thread_id") or sub.get("parent_id") or sub.get("parent_session_id")
            if pid and isinstance(pid, str):
                return pid.strip()
            sp = sub.get("thread_spawn")
            if isinstance(sp, dict):
                pid = sp.get("parent_thread_id") or sp.get("parent_id") or sp.get("parent_session_id")
                if pid and isinstance(pid, str):
                    return pid.strip()
    return None


def _nickname_from_source(source: Any) -> str | None:
    """Extract subagent nickname from source dict."""
    if not isinstance(source, dict):
        return None
    sub = source.get("subagent")
    if isinstance(sub, dict):
        for key in ("agent_nickname", "nickname", "name"):
            val = sub.get(key)
            if val and isinstance(val, str):
                return val.strip()
        sp = sub.get("thread_spawn")
        if isinstance(sp, dict):
            for key in ("agent_nickname", "nickname", "name"):
                val = sp.get(key)
                if val and isinstance(val, str):
                    return val.strip()
    return None


def peek_session_link(file_path: Path, modified_at: datetime | None = None) -> SessionLink | None:
    """Read session_meta from the first line of a rollout file."""
    try:
        first_line = read_first_line(file_path)
        if not first_line:
            return None
        data = json.loads(first_line)
        if data.get("type") != "session_meta" or not isinstance(data.get("payload"), dict):
            return None
        payload = data["payload"]
        sid = payload.get("id") or payload.get("session_id")
        if not sid:
            return None
        parent_id = _parent_id_from_payload(payload)
        name = (
            payload.get("agent_nickname")
            or _nickname_from_source(payload.get("source"))
            or sid[:8]
        )
        started_at = parse_datetime(payload.get("timestamp"))
        if modified_at is None:
            try:
                mtime = file_path.stat().st_mtime
                modified_at = datetime.fromtimestamp(mtime, tz=timezone.utc)
            except OSError:
                modified_at = datetime.now(timezone.utc)
        cwd = payload.get("cwd")
        return SessionLink(
            id=sid,
            parent_id=parent_id,
            name=name,
            started_at=started_at,
            modified_at=modified_at,
            cwd=cwd,
        )
    except Exception:
        return None

def peek_rollout_cwd(file_path: Path) -> str | None:
    """Read and normalize CWD from the first line of a rollout file."""
    link = peek_session_link(file_path)
    return _normalize_path(link.cwd) if link and link.cwd else None


def find_rollouts_in_dir(dir_path: Path) -> list[tuple[Path, str, datetime, datetime]]:
    """Find all rollout files in a date directory.

    Returns: list of (file_path, session_id, timestamp, modified_at)
    """
    results: list[tuple[Path, str, datetime, datetime]] = []
    if not dir_path.is_dir():
        return results
    try:
        for entry in dir_path.iterdir():
            if not entry.name.startswith("rollout-") or not entry.name.endswith(".jsonl"):
                continue
            parsed = parse_rollout_filename(entry.name)
            if not parsed:
                continue
            stamp, sid = parsed
            try:
                stat = entry.stat()
                mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                results.append((entry, sid, stamp, mtime))
            except OSError:
                continue
    except OSError:
        pass
    return results


def find_rollouts_in_days(
    max_days_back: int = DEFAULT_LOOKBACK_DAYS,
    sessions_dir: Path | None = None,
) -> list[tuple[Path, str, datetime, datetime]]:
    """Find all rollout files within the last N days."""
    s_dir = sessions_dir or get_sessions_dir()
    if not s_dir.is_dir():
        return []
    now = datetime.now(timezone.utc)
    results: list[tuple[Path, str, datetime, datetime]] = []
    # Search backwards from today
    for days_ago in range(max_days_back + 1):
        target_date = now.date() if days_ago == 0 else (now - __import__("datetime").timedelta(days=days_ago)).date()
        year = str(target_date.year)
        month = f"{target_date.month:02d}"
        day = f"{target_date.day:02d}"
        day_dir = s_dir / year / month / day
        results.extend(find_rollouts_in_dir(day_dir))
    return results


def find_most_recent_rollout(
    max_days_back: int = DEFAULT_LOOKBACK_DAYS,
    target_cwd: str | Path | None = None,
    sessions_dir: Path | None = None,
) -> tuple[Path, str, datetime, datetime] | None:
    """Find the most recently modified rollout file, optionally filtering by cwd."""
    rollouts = find_rollouts_in_days(max_days_back, sessions_dir=sessions_dir)
    if not rollouts:
        return None
    rollouts.sort(key=lambda r: r[3], reverse=True)
    if target_cwd:
        norm_target = _normalize_path(target_cwd)
        if norm_target:
            # 1. Exact match
            exact = [r for r in rollouts if peek_rollout_cwd(r[0]) == norm_target]
            if exact:
                return exact[0]
            # 2. Parent or subdirectory match
            prefix_matched = []
            for r in rollouts:
                rcwd = peek_rollout_cwd(r[0])
                if rcwd and (norm_target.startswith(rcwd) or rcwd.startswith(norm_target)):
                    prefix_matched.append(r)
            if prefix_matched:
                return prefix_matched[0]
    return rollouts[0]

def find_active_rollouts(
    within_seconds: float = 60.0,
    target_cwd: str | Path | None = None,
    max_days_back: int = 3,
    sessions_dir: Path | None = None,
) -> list[tuple[Path, str, datetime, datetime]]:
    """Find rollouts modified within the last N seconds."""
    rollouts = find_rollouts_in_days(max_days_back, sessions_dir=sessions_dir)
    now = datetime.now(timezone.utc)
    cutoff = now.timestamp() - within_seconds
    active = [r for r in rollouts if r[3].timestamp() >= cutoff]
    norm_cwd = _normalize_path(target_cwd) if target_cwd else None
    if norm_cwd:
        active = [r for r in active if peek_rollout_cwd(r[0]) == norm_cwd]
    active.sort(key=lambda r: r[3], reverse=True)
    return active


def iter_rollout_records(file_path: Path) -> Iterator[RolloutRecord]:
    """Stream raw RolloutRecord objects from a rollout JSONL file with stable line numbers."""
    if not file_path.is_file():
        return

    parsed_fn = parse_rollout_filename(file_path.name)
    default_sid = parsed_fn[1] if parsed_fn else ""
    cached_sid = default_sid

    line_number = 0
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for raw_line in f:
                line_number += 1
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if not isinstance(entry, dict):
                    continue

                entry_type = str(entry.get("type") or "")
                raw_payload = entry.get("payload")
                payload: Mapping[str, Any] = raw_payload if isinstance(raw_payload, dict) else entry

                sid = cached_sid
                if entry_type == "session_meta" and isinstance(raw_payload, dict):
                    meta_sid = raw_payload.get("id") or raw_payload.get("session_id")
                    if meta_sid:
                        cached_sid = str(meta_sid)
                        sid = cached_sid
                elif not sid:
                    if isinstance(raw_payload, dict):
                        sid = str(raw_payload.get("session_id") or raw_payload.get("thread_id") or "")
                    if not sid:
                        sid = cached_sid

                ts_val = entry.get("timestamp")
                if not ts_val and isinstance(raw_payload, dict):
                    ts_val = raw_payload.get("timestamp")
                ts = parse_datetime(ts_val)

                if entry_type == "session_meta":
                    raw_kind = "session_meta"
                elif isinstance(raw_payload, dict):
                    p_type = raw_payload.get("type")
                    if p_type == "item_completed":
                        item = raw_payload.get("item")
                        if isinstance(item, dict):
                            raw_kind = str(item.get("tool") or item.get("name") or item.get("type") or "item_completed")
                        else:
                            raw_kind = "item_completed"
                    elif p_type:
                        raw_kind = str(p_type)
                    else:
                        raw_kind = entry_type or "unknown"
                else:
                    raw_kind = entry_type or "unknown"

                yield RolloutRecord(
                    session_id=sid,
                    file_path=file_path,
                    line_number=line_number,
                    entry_type=entry_type,
                    raw_kind=raw_kind,
                    timestamp=ts,
                    payload=payload,
                )
    except OSError:
        return


def build_session_index(
    max_days_back: int = DEFAULT_LOOKBACK_DAYS,
    sessions_dir: Path | None = None,
) -> SessionIndex:
    """Build a unified index mapping session IDs to links, rollout files, and children."""
    rollouts = find_rollouts_in_days(max_days_back, sessions_dir=sessions_dir)
    links: dict[str, SessionLink] = {}
    rollout_files: dict[str, Path] = {}

    for file_path, _, _, mod_time in rollouts:
        link = peek_session_link(file_path, modified_at=mod_time)
        if not link:
            continue
        existing = links.get(link.id)
        if not existing or link.modified_at > existing.modified_at:
            links[link.id] = link
            rollout_files[link.id] = file_path

    children_map: dict[str, list[str]] = {}
    for link in sorted(links.values(), key=lambda l: (l.started_at or datetime.min.replace(tzinfo=timezone.utc), l.id)):
        if link.parent_id:
            children_map.setdefault(link.parent_id, []).append(link.id)

    children: dict[str, tuple[str, ...]] = {
        k: tuple(v) for k, v in children_map.items()
    }

    return SessionIndex(
        links=links,
        rollout_files=rollout_files,
        children=children,
    )


def descendant_session_ids(index: SessionIndex, root_id: str) -> tuple[str, ...]:
    """Return root_id and all its descendant session IDs in deterministic hierarchical order."""
    if not root_id:
        return ()
    result: list[str] = [root_id]
    visited: set[str] = {root_id}
    queue: list[str] = [root_id]
    while queue:
        curr = queue.pop(0)
        for child_id in index.children.get(curr, ()):
            if child_id not in visited:
                visited.add(child_id)
                result.append(child_id)
                queue.append(child_id)
    return tuple(result)


# --------------------------------------------------------------------------
# Rollout Event Parsing (CollabAgentToolCall etc.)
# --------------------------------------------------------------------------

def infer_subagent_status(state: Any, tool: str | None = None) -> SubagentStatus:
    """Infer subagent status from state and tool name."""
    if tool == "close_agent":
        return "completed"
    if isinstance(state, dict):
        if state.get("error") or state.get("failed"):
            return "error"
        if state.get("completed"):
            return "completed"
        if state.get("running"):
            return "running"
    if state == "pending_init":
        return "starting"
    if tool in ("wait", "spawn_agent"):
        return "running"
    return "running"


def parse_rollout_subagents(file_path: Path) -> list[SubagentInfo]:
    """Parse rollout JSONL file to extract known CollabAgentToolCall subagents."""
    subagents: dict[str, SubagentInfo] = {}
    if not file_path.is_file():
        return []
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("type") != "event_msg":
                    continue
                payload = entry.get("payload")
                if not isinstance(payload, dict):
                    continue
                if payload.get("type") == "item_completed":
                    item = payload.get("item")
                    if isinstance(item, dict) and item.get("type") == "CollabAgentToolCall":
                        timestamp = parse_datetime(entry.get("timestamp")) or datetime.now(timezone.utc)
                        agents = item.get("receiver_agents") or []
                        ids = []
                        if agents:
                            ids = [a.get("thread_id") for a in agents if isinstance(a, dict) and a.get("thread_id")]
                        else:
                            ids = [i for i in (item.get("receiver_thread_ids") or []) if i]
                        states = item.get("agents_states") or {}
                        tool = item.get("tool")
                        model = item.get("model")
                        for sid in ids:
                            if not sid:
                                continue
                            listed = next((a for a in agents if isinstance(a, dict) and a.get("thread_id") == sid), None)
                            prev = subagents.get(sid)
                            name = (
                                (listed.get("agent_nickname") if listed else None)
                                or (prev.name if prev else None)
                                or sid[:8]
                            )
                            state = states.get(sid) if isinstance(states, dict) else None
                            status = infer_subagent_status(state, tool)
                            started_at = prev.started_at if prev and prev.started_at else timestamp
                            cur_model = model or (prev.model if prev else None)
                            subagents[sid] = SubagentInfo(
                                id=sid,
                                name=name,
                                status=status,
                                started_at=started_at,
                                model=cur_model,
                            )
    except OSError:
        pass
    return list(subagents.values())


# --------------------------------------------------------------------------
# Subagent Tree Building
# --------------------------------------------------------------------------

def resolve_status(
    known: SubagentInfo | None,
    modified_at: datetime,
    now_ts: float,
) -> SubagentStatus:
    if known:
        return known.status
    if (now_ts - modified_at.timestamp()) <= ACTIVE_WINDOW_SECONDS:
        return "running"
    return "completed"


def count_nodes(nodes: list[SubagentTreeNode]) -> int:
    """Return total count of nodes including all descendants."""
    return sum(1 + count_nodes(n.children) for n in nodes)


def collect_nodes_by_depth(nodes: list[SubagentTreeNode]) -> list[list[SubagentTreeNode]]:
    """Group subagent tree nodes by depth level."""
    levels: list[list[SubagentTreeNode]] = []

    def walk(items: list[SubagentTreeNode], depth: int) -> None:
        if not items:
            return
        while len(levels) <= depth:
            levels.append([])
        levels[depth].extend(items)
        for item in items:
            walk(item.children, depth + 1)

    walk(nodes, 0)
    return levels


def flatten_subagent_tree(nodes: list[SubagentTreeNode]) -> list[SubagentTreeNode]:
    """Return flat list of all nodes in depth-first order."""
    out: list[SubagentTreeNode] = []
    for n in nodes:
        out.append(n)
        if n.children:
            out.extend(flatten_subagent_tree(n.children))
    return out


def find_root_session_id(
    target_id: str,
    max_days_back: int = DEFAULT_LOOKBACK_DAYS,
    sessions_dir: Path | None = None,
) -> str:
    """Traverse parent links upwards to find the top-level root session ID."""
    if not target_id:
        return ""
    rollouts = find_rollouts_in_days(max_days_back, sessions_dir=sessions_dir)
    if not rollouts:
        return target_id
    links: dict[str, SessionLink] = {}
    for file_path, _, _, mod_time in rollouts:
        link = peek_session_link(file_path, modified_at=mod_time)
        if link:
            existing = links.get(link.id)
            if not existing or link.modified_at > existing.modified_at:
                links[link.id] = link
    curr = links.get(target_id)
    seen = {target_id}
    while curr and curr.parent_id and curr.parent_id in links:
        parent_sid = curr.parent_id
        if parent_sid in seen:
            break
        seen.add(parent_sid)
        curr = links[parent_sid]
    return curr.id if curr else target_id


def build_subagent_tree(
    root_id: str,
    known_subagents: list[SubagentInfo] | None = None,
    max_days_back: int = 3,
    now_ts: float | datetime | None = None,
    sessions_dir: Path | None = None,
) -> SubagentTree:
    """Build a multi-level subagent tree from rollout session_meta parent links.

    root_id: session id of the root session.
    known_subagents: list of SubagentInfo from live rollout event parsing.
    """
    if now_ts is None:
        now_dt = datetime.now(timezone.utc)
        now_seconds = now_dt.timestamp()
    elif isinstance(now_ts, datetime):
        now_dt = now_ts
        now_seconds = now_dt.timestamp()
    else:
        now_seconds = float(now_ts)
        now_dt = datetime.fromtimestamp(now_seconds, tz=timezone.utc)

    known_map = {agent.id: agent for agent in (known_subagents or [])}
    links: dict[str, SessionLink] = {}

    for file_path, _, _, modified_at in find_rollouts_in_days(max_days_back, sessions_dir=sessions_dir):
        link = peek_session_link(file_path, modified_at=modified_at)
        if not link:
            continue
        existing = links.get(link.id)
        if not existing or link.modified_at > existing.modified_at:
            links[link.id] = link

    children: dict[str, list[str]] = {}
    for link in links.values():
        if not link.parent_id:
            continue
        children.setdefault(link.parent_id, []).append(link.id)

    # Known collab-spawned agents not yet peeked
    for agent in known_subagents or []:
        if agent.id in links:
            continue
        siblings = children.setdefault(root_id, [])
        if agent.id not in siblings:
            siblings.append(agent.id)

    # Discover in-flight dispatches from charter.inflight
    try:
        from . import inflight
        candidate_roots = {lid for lid, l in links.items() if not l.parent_id}
        for rec in inflight.live_records():
            rec_sid = rec.get("session_id")
            rec_pid = rec.get("parent_id")
            if rec_sid is not None:
                if rec_sid != root_id and rec_pid != root_id and rec_sid not in links:
                    continue
            else:
                if len(candidate_roots) > 1:
                    continue

            agent_name = rec["agent"]
            token = rec["token"]
            target_parent = rec_pid if (rec_pid and rec_pid in links) else root_id
            target_cids = children.get(target_parent, [])
            target_names = {links[cid].name for cid in target_cids if cid in links}

            if agent_name not in target_names and token not in target_cids:
                pseudo_id = f"inflight-{token}"
                start_dt = datetime.fromtimestamp(rec["ts"], tz=timezone.utc)
                known_map[pseudo_id] = SubagentInfo(
                    id=pseudo_id,
                    name=agent_name,
                    status="running",
                    started_at=start_dt,
                )
                children.setdefault(target_parent, []).append(pseudo_id)
    except Exception:
        pass
    visiting: set[str] = set()

    def build_node(node_id: str, depth: int) -> SubagentTreeNode:
        visiting.add(node_id)
        link = links.get(node_id)
        known_agent = known_map.get(node_id)
        child_ids = [cid for cid in children.get(node_id, []) if cid not in visiting]
        name = (
            (known_agent.name if known_agent else None)
            or (link.name if link else None)
            or node_id[:8]
        )
        mod_time = link.modified_at if link else datetime.fromtimestamp(0, tz=timezone.utc)
        st = resolve_status(known_agent, mod_time, now_seconds)
        start_time = (known_agent.started_at if known_agent else None) or (link.started_at if link else None)
        node = SubagentTreeNode(
            id=node_id,
            name=name,
            status=st,
            depth=depth,
            started_at=start_time,
            children=[build_node(cid, depth + 1) for cid in child_ids],
        )
        visiting.remove(node_id)
        return node

    root_children = children.get(root_id, [])
    top_nodes = [build_node(cid, 1) for cid in root_children]
    return SubagentTree(
        root_id=root_id,
        nodes=top_nodes,
        total_count=count_nodes(top_nodes),
        updated_at=now_dt,
    )

# --------------------------------------------------------------------------
# Rendering & Formatting
# --------------------------------------------------------------------------

def format_elapsed(start_time: datetime | None, now: datetime | None = None) -> str:
    """Format elapsed duration (e.g. 12s, 3m15s, 1h05m)."""
    if not start_time:
        return ""
    if now is None:
        now = datetime.now(timezone.utc)
    t_start = start_time.timestamp()
    t_now = now.timestamp()
    diff_sec = max(0, int(t_now - t_start))
    if diff_sec < 60:
        return f"{diff_sec}s"
    diff_min = diff_sec // 60
    if diff_min < 60:
        sec_rem = diff_sec % 60
        return f"{diff_min}m{sec_rem:02d}s"
    diff_hr = diff_min // 60
    min_rem = diff_min % 60
    return f"{diff_hr}h{min_rem:02d}m"


def render_branch_connector(
    is_last: bool,
    latest_exchange: SubagentExchange | None = None,
    node: SubagentTreeNode | None = None,
    parent_id: str | None = None,
    now: datetime | None = None,
    color: bool = True,
    tick: int = 0,
) -> tuple[str, str]:
    """Render the branch character string with animated directional flow indicators and fading.

    Returns:
        (branch_str, flow_tag_str)
    """
    dim = _DIM if color else ""
    r = _R if color else ""
    cyan = _CYAN if color else ""
    green = _GREEN if color else ""
    yellow = _YELLOW if color else ""
    bold = _BOLD if color else ""

    default_branch = "└── " if is_last else "├── "
    if not latest_exchange or not node or node.status not in ("running", "starting"):
        return f"{dim}{'└─ ' if is_last else '├─ '}{r}", ""

    now_dt = now or datetime.now(timezone.utc)
    now_ts = now_dt.timestamp()
    if latest_exchange.timestamp:
        diff_sec = now_ts - latest_exchange.timestamp.timestamp()
    else:
        diff_sec = 0.0

    if diff_sec > COMM_ACTIVE_WINDOW_SECONDS or diff_sec < 0:
        return f"{dim}{'└─ ' if is_last else '├─ '}{r}", ""

    # Determine flow direction
    if latest_exchange.tool_name or latest_exchange.kind in ("tool_call", "tool_result"):
        direction = "tool"
        flow_color = yellow
    elif latest_exchange.sender_id == node.id and (
        latest_exchange.receiver_id == parent_id or latest_exchange.kind in ("response", "task_complete")
    ):
        direction = "up"
        flow_color = green
    elif latest_exchange.receiver_id == node.id and (
        latest_exchange.sender_id == parent_id or latest_exchange.kind in ("dispatch", "prompt", "collab_msg")
    ):
        direction = "down"
        flow_color = cyan
    elif latest_exchange.sender_id == node.id:
        direction = "peer_out"
        flow_color = cyan
    else:
        direction = "peer_in"
        flow_color = cyan

    is_blazing = diff_sec <= COMM_BLAZING_WINDOW_SECONDS and tick > 0
    frame_idx = tick % 4

    if direction == "down":
        if is_blazing:
            branch_text = BRANCH_FLOW_DOWN_LAST_FRAMES[frame_idx] if is_last else BRANCH_FLOW_DOWN_FRAMES[frame_idx]
            branch_styled = f"{flow_color}{bold}{branch_text}{r}"
            flow_glyph = "▼"
        else:
            branch_text = "└➔ " if is_last else "├➔ "
            branch_styled = f"{flow_color}{branch_text}{r}"
            flow_glyph = "▼"
        target_label = f"{latest_exchange.sender_name} ➔ {node.name}"

    elif direction == "up":
        if is_blazing:
            branch_text = BRANCH_FLOW_UP_LAST_FRAMES[frame_idx] if is_last else BRANCH_FLOW_UP_FRAMES[frame_idx]
            branch_styled = f"{flow_color}{bold}{branch_text}{r}"
            flow_glyph = "▲"
        else:
            branch_text = "└▲ " if is_last else "├▲ "
            branch_styled = f"{flow_color}{branch_text}{r}"
            flow_glyph = "▲"
        target_label = f"{node.name} ➔ {latest_exchange.receiver_name}"

    elif direction == "tool":
        if is_blazing:
            branch_text = BRANCH_FLOW_TOOL_LAST_FRAMES[frame_idx] if is_last else BRANCH_FLOW_TOOL_FRAMES[frame_idx]
            branch_styled = f"{flow_color}{bold}{branch_text}{r}"
            flow_glyph = "🔧"
        else:
            branch_text = "└⚡ " if is_last else "├⚡ "
            branch_styled = f"{flow_color}{branch_text}{r}"
            flow_glyph = "🔧"
        target_label = f"tool: {latest_exchange.tool_name or 'exec'}"

    else:  # peer
        if is_blazing:
            branch_text = BRANCH_FLOW_PEER_LAST_FRAMES[frame_idx] if is_last else BRANCH_FLOW_PEER_FRAMES[frame_idx]
            branch_styled = f"{flow_color}{bold}{branch_text}{r}"
            flow_glyph = "⇄"
        else:
            branch_text = "└➔ " if is_last else "├➔ "
            branch_styled = f"{flow_color}{branch_text}{r}"
            flow_glyph = "➔"
        target_label = f"{latest_exchange.sender_name} ➔ {latest_exchange.receiver_name}"

    tag_pulse = f"{bold}⚡ {r}" if is_blazing else ""
    flow_tag = f"  {flow_color}{tag_pulse}{flow_glyph} [{target_label}]{r}"
    return branch_styled, flow_tag


def render_chip(
    node: SubagentTreeNode,
    now: datetime | None = None,
    color: bool = True,
    tick: int = 0,
    latest_exchange: SubagentExchange | None = None,
    is_communicating: bool | None = None,
    flow_tag: str = "",
    verbose: bool = False,
) -> str:
    """Render a colored/plain chip for a single subagent node with optional dynamic animation and bubble."""
    r = _R if color else ""
    dim = _DIM if color else ""
    cyan = _CYAN if color else ""
    yellow = _YELLOW if color else ""
    green = _GREEN if color else ""
    red = _RED if color else ""

    # Determine active communication status if not explicitly provided
    if is_communicating is None:
        if latest_exchange is not None:
            now_dt = now or datetime.now(timezone.utc)
            now_ts = now_dt.timestamp()
            if latest_exchange.timestamp:
                ex_ts = latest_exchange.timestamp.timestamp()
                is_communicating = (now_ts - ex_ts) <= COMM_ACTIVE_WINDOW_SECONDS
            else:
                is_communicating = True
        else:
            is_communicating = False

    if node.status == "completed":
        icon = "○"
        col = dim
        display_status = "stopped"
    elif node.status == "error":
        icon = ICON_ERROR
        col = red
        display_status = "stopped"
    elif node.status == "starting":
        icon = SPINNER_FRAMES[tick % len(SPINNER_FRAMES)] if (tick > 0 and is_communicating) else ICON_STARTING
        col = yellow
        display_status = "starting"
    else:
        icon = SPINNER_FRAMES[tick % len(SPINNER_FRAMES)] if (tick > 0 and is_communicating) else ICON_RUNNING
        col = cyan
        display_status = "running"

    elapsed = ""
    if node.started_at:
        el_str = format_elapsed(node.started_at, now)
        if el_str:
            elapsed = f" {dim}{el_str}{r}"

    name_label = f"{node.name} [{node.id[:8]}]" if (verbose and node.id) else node.name
    base = f"{col}{icon} {name_label}{r} {dim}{display_status}{r}{elapsed}"
    # Live communication bubble (only when running/starting and actively communicating)
    if latest_exchange and is_communicating and node.status in ("running", "starting"):
        snippet = latest_exchange.content.strip().replace("\n", " ")
        max_snippet_len = 64 if verbose else 32
        if len(snippet) > max_snippet_len:
            snippet = snippet[:max_snippet_len] + "…"
        if flow_tag:
            bubble = f"{flow_tag}{dim}: \"{snippet}\"{r}"
        elif latest_exchange.tool_name:
            bubble = f"  {cyan}{ICON_TOOL}{r} {dim}{latest_exchange.tool_name}{r}"
        elif latest_exchange.sender_id == node.id:
            bubble = f"  {cyan}{ICON_FLOW} {latest_exchange.receiver_name}{r}{dim}: \"{snippet}\"{r}"
        else:
            bubble = f"  {yellow}{ICON_MSG}{r} {dim}\"{snippet}\"{r}"
        base += bubble

    return base


def render_directory_tree(
    nodes: list[SubagentTreeNode],
    prefix: str = "",
    parent_id: str | None = None,
    now: datetime | None = None,
    color: bool = True,
    tick: int = 0,
    exchanges_by_node: dict[str, SubagentExchange] | None = None,
    verbose: bool = False,
) -> list[str]:
    """Render hierarchical tree lines with branch characters, animated flow, and live bubbles."""
    lines: list[str] = []
    dim = _DIM if color else ""
    r = _R if color else ""
    for index, node in enumerate(nodes):
        is_last = index == len(nodes) - 1
        latest_ex = exchanges_by_node.get(node.id) if exchanges_by_node else None
        branch_styled, flow_tag = render_branch_connector(
            is_last=is_last,
            latest_exchange=latest_ex,
            node=node,
            parent_id=parent_id,
            now=now,
            color=color,
            tick=tick,
        )
        chip = render_chip(
            node,
            now=now,
            color=color,
            tick=tick,
            latest_exchange=latest_ex,
            flow_tag=flow_tag,
            verbose=verbose,
        )
        lines.append(f"{dim}{prefix}{r}{branch_styled}{chip}")
        if node.children:
            next_prefix = prefix + ("   " if is_last else "│  ")
            lines.extend(
                render_directory_tree(
                    node.children,
                    next_prefix,
                    parent_id=node.id,
                    now=now,
                    color=color,
                    tick=tick,
                    exchanges_by_node=exchanges_by_node,
                    verbose=verbose,
                )
            )
    return lines


def render_subagent_tree_page(
    tree: SubagentTree,
    now: datetime | None = None,
    color: bool = True,
    tick: int = 0,
    recent_exchanges: list[SubagentExchange] | None = None,
    show_feed: bool | None = None,
    verbose: bool = False,
) -> list[str]:
    """Render full interactive/TUI page for subagents with live animation and exchange feed."""
    r = _R if color else ""
    dim = _DIM if color else ""
    bold_cyan = (_BOLD + _CYAN) if color else ""
    green = _GREEN if color else ""
    bold = _BOLD if color else ""
    yellow = _YELLOW if color else ""
    magenta = _MAGENTA if color else ""

    time_str = (now or datetime.now(timezone.utc)).strftime("%H:%M:%S")
    pulse = PULSE_FRAMES[tick % len(PULSE_FRAMES)] if tick > 0 else "●"
    live_tag = f"  {green}[{pulse} LIVE {time_str}]{r}" if tick > 0 else ""

    lines = [
        f"{bold_cyan}Subagent tree{r}  {dim}live · q / Esc / Ctrl+C  close{r}{live_tag}",
        "",
    ]

    root_label = tree.root_id[:8] if tree.root_id else "main"
    root_line = f"{magenta}{bold}⬢ Session {root_label}{r} {dim}(main session){r}"
    lines.append(root_line)

    exchanges_by_node: dict[str, SubagentExchange] = {}
    if recent_exchanges:
        for ex in recent_exchanges:
            if ex.sender_id:
                exchanges_by_node[ex.sender_id] = ex
            if ex.receiver_id:
                exchanges_by_node[ex.receiver_id] = ex

    if not tree.nodes:
        lines.append(f"  {dim}└─ (no subagents in this session){r}")
    else:
        lines.extend(
            render_directory_tree(
                tree.nodes,
                prefix="  ",
                parent_id=tree.root_id,
                now=now,
                color=color,
                tick=tick,
                exchanges_by_node=exchanges_by_node,
                verbose=verbose,
            )
        )

    # Active Communication Feed panel
    effective_show_feed = show_feed if show_feed is not None else verbose
    if effective_show_feed and recent_exchanges:
        lines.append("")
        lines.append(f"{dim}{'─' * 68}{r}")
        lines.append(f"{bold}{yellow}⚡ Live Exchanges ({len(recent_exchanges[-3:])}):{r}")
        feed_lines = render_exchange_timeline(recent_exchanges[-3:], max_content_len=180, color=color)
        for fl in feed_lines:
            if fl.strip():
                lines.append("  " + fl)
        lines.append(f"{dim}{'─' * 68}{r}")

    all_nodes = flatten_subagent_tree(tree.nodes)
    running_n = sum(1 for n in all_nodes if n.status in ("running", "starting"))
    active_str = f" · {running_n} active" if running_n > 0 else ""
    lines.append(f"{dim}{tree.total_count} node(s){active_str}{r}")
    return lines


def render_subagent_tree_page_plain(
    tree: SubagentTree,
    now: datetime | None = None,
    recent_exchanges: list[SubagentExchange] | None = None,
    verbose: bool = False,
) -> str:
    """Render plain-text stripped string of the subagent tree page."""
    return "\n".join(
        tui.strip_ansi(ln)
        for ln in render_subagent_tree_page(
            tree,
            now=now,
            color=False,
            recent_exchanges=recent_exchanges,
            verbose=verbose,
        )
    )
def render_parallel_chips(
    nodes: list[SubagentTreeNode],
    width: int = 80,
    color: bool = True,
) -> list[str]:
    """Render compact first-level subagent chips for status lines."""
    if not nodes or width <= 0:
        return []
    chips = []
    dim = _DIM if color else ""
    r = _R if color else ""
    for n in nodes:
        arrow = f"{dim} ▾{r}" if n.children else ""
        chips.append(render_chip(n, color=color) + arrow)
    line = ("  ").join(chips)
    return [tui.truncate(line, width)]


def subagent_summary(tree: SubagentTree | None, color: bool = True) -> str:
    """Return concise one-line summary (e.g. '2 subagents (1 running, 1 completed)')."""
    if not tree or tree.total_count == 0:
        return ""
    all_nodes = flatten_subagent_tree(tree.nodes)
    running = sum(1 for n in all_nodes if n.status in ("running", "starting"))
    completed = sum(1 for n in all_nodes if n.status == "completed")
    errors = sum(1 for n in all_nodes if n.status == "error")
    parts = []
    if running > 0:
        parts.append(f"{running} running")
    if completed > 0:
        parts.append(f"{completed} stopped")
        parts.append(f"{errors} error")
    details = f" ({', '.join(parts)})" if parts else ""
    dim = _DIM if color else ""
    r = _R if color else ""
    cyan = _CYAN if color else ""
    return f"{cyan}⇢ {tree.total_count} subagent{'s' if tree.total_count != 1 else ''}{r}{dim}{details}{r}"


# --------------------------------------------------------------------------
# Subagent Communication & Exchanges Extraction
# --------------------------------------------------------------------------

def extract_subagent_exchanges(
    session_id: str | None = None,
    subagent_id: str | None = None,
    max_days_back: int = DEFAULT_LOOKBACK_DAYS,
    include_tool_calls: bool = False,
    sessions_dir: Path | None = None,
) -> list[SubagentExchange]:
    """Extract communication history between session and subagents, or among subagents."""
    s_dir = sessions_dir or get_sessions_dir()
    rollouts = find_rollouts_in_days(max_days_back, sessions_dir=s_dir)
    if not rollouts:
        return []

    links: dict[str, SessionLink] = {}
    rollout_files: dict[str, Path] = {}
    for file_path, sid, _, mod_time in rollouts:
        link = peek_session_link(file_path, modified_at=mod_time)
        if link:
            existing = links.get(link.id)
            if not existing or link.modified_at > existing.modified_at:
                links[link.id] = link
                rollout_files[link.id] = file_path

    # Determine relevant sessions
    relevant_ids: set[str] = set()
    if subagent_id:
        matched = [sid for sid, l in links.items() if sid == subagent_id or sid.startswith(subagent_id) or l.name.lower() == subagent_id.lower()]
        if matched:
            target_id = matched[0]
            relevant_ids.add(target_id)
            if links[target_id].parent_id:
                relevant_ids.add(links[target_id].parent_id)
    elif session_id:
        # Find root and all children
        relevant_ids.add(session_id)
        children_map: dict[str, list[str]] = {}
        for l in links.values():
            if l.parent_id:
                children_map.setdefault(l.parent_id, []).append(l.id)
        stack = [session_id]
        while stack:
            curr = stack.pop()
            for child in children_map.get(curr, []):
                if child not in relevant_ids:
                    relevant_ids.add(child)
                    stack.append(child)
    else:
        # All sessions with parent_id or their parents
        for l in links.values():
            if l.parent_id:
                relevant_ids.add(l.id)
                relevant_ids.add(l.parent_id)

    exchanges: list[SubagentExchange] = []
    seen_keys: set[str] = set()

    for sid in relevant_ids:
        fpath = rollout_files.get(sid)
        if not fpath or not fpath.is_file():
            continue
        current_link = links.get(sid)
        current_name = current_link.name if current_link else sid[:8]
        parent_id = current_link.parent_id if current_link else None
        parent_name = (
            links[parent_id].name if parent_id and parent_id in links
            else (parent_id[:8] if parent_id else "Coordinator")
        )

        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue
                    ts = parse_datetime(entry.get("timestamp")) or datetime.now(timezone.utc)
                    entry_type = entry.get("type")
                    payload = entry.get("payload") or {}

                    if entry_type == "event_msg" and isinstance(payload, dict):
                        p_type = payload.get("type")
                        if p_type == "item_completed":
                            item = payload.get("item") or {}
                            if isinstance(item, dict) and item.get("type") == "CollabAgentToolCall":
                                prompt = item.get("prompt") or ""
                                tool = item.get("tool") or "collab"
                                agents = item.get("receiver_agents") or []
                                rec_ids = (
                                    [a.get("thread_id") for a in agents if isinstance(a, dict) and a.get("thread_id")]
                                    if agents
                                    else [i for i in (item.get("receiver_thread_ids") or []) if i]
                                )
                                for rid in rec_ids:
                                    r_name = (
                                        links[rid].name if rid in links
                                        else next((a.get("agent_nickname") for a in agents if isinstance(a, dict) and a.get("thread_id") == rid), rid[:8])
                                    )
                                    key = f"collab-{ts.isoformat()}-{sid}-{rid}-{prompt[:30]}"
                                    if key not in seen_keys:
                                        seen_keys.add(key)
                                        exchanges.append(SubagentExchange(
                                            sender_id=sid,
                                            sender_name=current_name,
                                            receiver_id=rid,
                                            receiver_name=r_name,
                                            kind="collab_msg" if tool != "spawn_agent" else "dispatch",
                                            content=prompt or f"Tool: {tool}",
                                            timestamp=ts,
                                            tool_name=tool,
                                        ))
                        elif p_type == "user_message" and parent_id:
                            msg = payload.get("message") or ""
                            if msg and not msg.startswith("# AGENTS.md instructions"):
                                key = f"user_msg-{ts.isoformat()}-{parent_id}-{sid}-{msg[:30]}"
                                if key not in seen_keys:
                                    seen_keys.add(key)
                                    exchanges.append(SubagentExchange(
                                        sender_id=parent_id,
                                        sender_name=parent_name,
                                        receiver_id=sid,
                                        receiver_name=current_name,
                                        kind="dispatch",
                                        content=msg,
                                        timestamp=ts,
                                    ))
                        elif p_type == "agent_message" and parent_id:
                            msg = payload.get("message") or ""
                            if msg:
                                key = f"agent_msg-{ts.isoformat()}-{sid}-{parent_id}-{msg[:30]}"
                                if key not in seen_keys:
                                    seen_keys.add(key)
                                    exchanges.append(SubagentExchange(
                                        sender_id=sid,
                                        sender_name=current_name,
                                        receiver_id=parent_id,
                                        receiver_name=parent_name,
                                        kind="response",
                                        content=msg,
                                        timestamp=ts,
                                    ))
                        elif p_type == "task_complete" and parent_id:
                            msg = payload.get("last_agent_message") or "Task completed"
                            dur = payload.get("duration_ms")
                            key = f"task_complete-{ts.isoformat()}-{sid}-{parent_id}"
                            if key not in seen_keys:
                                seen_keys.add(key)
                                exchanges.append(SubagentExchange(
                                    sender_id=sid,
                                    sender_name=current_name,
                                    receiver_id=parent_id,
                                    receiver_name=parent_name,
                                    kind="task_complete",
                                    content=msg,
                                    timestamp=ts,
                                    duration_ms=dur,
                                ))
                    elif entry_type == "response_item" and isinstance(payload, dict):
                        p_type = payload.get("type")
                        if include_tool_calls:
                            if p_type in ("function_call", "custom_tool_call"):
                                name = payload.get("name") or "tool"
                                args_val = payload.get("arguments") or ""
                                if isinstance(args_val, (dict, list)):
                                    args_val = json.dumps(args_val)
                                key = f"tool_call-{ts.isoformat()}-{sid}-{name}"
                                if key not in seen_keys:
                                    seen_keys.add(key)
                                    exchanges.append(SubagentExchange(
                                        sender_id=sid,
                                        sender_name=current_name,
                                        receiver_id=sid,
                                        receiver_name=name,
                                        kind="tool_call",
                                        content=str(args_val),
                                        timestamp=ts,
                                        tool_name=name,
                                    ))
                            elif p_type in ("function_call_output", "custom_tool_call_output"):
                                output_val = payload.get("output") or ""
                                key = f"tool_output-{ts.isoformat()}-{sid}"
                                if key not in seen_keys:
                                    seen_keys.add(key)
                                    exchanges.append(SubagentExchange(
                                        sender_id=sid,
                                        sender_name=current_name,
                                        receiver_id=sid,
                                        receiver_name=current_name,
                                        kind="tool_result",
                                        content=str(output_val),
                                        timestamp=ts,
                                    ))
        except OSError:
            pass

    exchanges.sort(key=lambda e: e.timestamp)
    return exchanges


def render_exchange_timeline(
    exchanges: list[SubagentExchange],
    max_content_len: int = 240,
    color: bool = True,
) -> list[str]:
    """Render a clean conversation/exchange timeline between subagents and sessions."""
    if not exchanges:
        dim = _DIM if color else ""
        r = _R if color else ""
        return [f"{dim}No communication exchanges recorded for this session.{r}"]

    dim = _DIM if color else ""
    r = _R if color else ""
    bold = _BOLD if color else ""
    cyan = _CYAN if color else ""
    green = _GREEN if color else ""
    yellow = _YELLOW if color else ""
    magenta = _MAGENTA if color else ""

    lines: list[str] = []
    for ex in exchanges:
        time_str = ex.timestamp.strftime("%H:%M:%S")
        kind_col = (
            green if ex.kind == "response"
            else yellow if ex.kind == "dispatch"
            else cyan if ex.kind == "collab_msg"
            else magenta if ex.kind == "task_complete"
            else dim
        )
        extra_info = ""
        if ex.tool_name:
            extra_info += f" {dim}(tool: {ex.tool_name}){r}"
        if ex.duration_ms:
            dur_sec = ex.duration_ms / 1000.0
            extra_info += f" {dim}(duration: {dur_sec:.1f}s){r}"

        header = (
            f"{dim}[{time_str}]{r} "
            f"{bold}{ex.sender_name}{r} "
            f"{cyan}➔{r} "
            f"{bold}{ex.receiver_name}{r} "
            f"{kind_col}[{ex.kind}]{r}{extra_info}"
        )
        lines.append(header)

        # Format content preview
        raw_text = ex.content.strip()
        if len(raw_text) > max_content_len:
            raw_text = raw_text[:max_content_len] + "…"
        # Indent content lines
        content_lines = raw_text.split("\n")
        for cl in content_lines[:4]:
            lines.append(f"  {dim}│{r} {cl}")
        if len(content_lines) > 4:
            lines.append(f"  {dim}│ … ({len(content_lines) - 4} more lines){r}")
        lines.append("")

    return lines

# --------------------------------------------------------------------------
# Live Watch Mode
# --------------------------------------------------------------------------

class SubagentEventWatcher:
    """Event-driven filesystem watcher for subagent rollouts and in-flight dispatches.

    Monitors rollout directories, active session files, and inflight state directories
    using fast metadata snapshot checks, detecting changes within milliseconds without
    unnecessary CPU overhead.
    """

    def __init__(
        self,
        sessions_dir: Path | None = None,
        max_days_back: int = 3,
        extra_paths: list[Path] | None = None,
    ):
        self.sessions_dir = sessions_dir or get_sessions_dir()
        self.max_days_back = max_days_back
        self.extra_paths = extra_paths or []
        self._last_snapshot: dict[str, tuple[float, int]] = self.poll_snapshot()
    def get_watched_paths(self) -> list[Path]:
        """Collect all relevant directories and files to monitor."""
        paths: list[Path] = []
        if self.sessions_dir.is_dir():
            paths.append(self.sessions_dir)
            now = datetime.now(timezone.utc)
            for days_ago in range(min(self.max_days_back + 1, 4)):
                target_date = (
                    now.date()
                    if days_ago == 0
                    else (now - __import__("datetime").timedelta(days=days_ago)).date()
                )
                day_dir = (
                    self.sessions_dir
                    / str(target_date.year)
                    / f"{target_date.month:02d}"
                    / f"{target_date.day:02d}"
                )
                if day_dir.is_dir():
                    paths.append(day_dir)

        # Monitor inflight dispatches directory
        try:
            from . import inflight
            inflight_dir = inflight._dir()
            if inflight_dir.exists():
                paths.append(inflight_dir)
        except Exception:
            pass

        # Monitor trace directory
        try:
            from . import config
            trace_dir = config.PERSONA_STATE_DIR / "trace"
            if trace_dir.exists():
                paths.append(trace_dir)
        except Exception:
            pass

        for ep in self.extra_paths:
            if ep and ep.exists():
                paths.append(ep)
        return paths

    def poll_snapshot(self) -> dict[str, tuple[float, int]]:
        """Snapshot mtime and size of all watched directories and their files."""
        snapshot: dict[str, tuple[float, int]] = {}
        for p in self.get_watched_paths():
            try:
                st = p.stat()
                snapshot[str(p)] = (st.st_mtime, st.st_size if p.is_file() else 0)
                if p.is_dir():
                    for child in p.iterdir():
                        try:
                            c_st = child.stat()
                            snapshot[str(child)] = (c_st.st_mtime, c_st.st_size)
                        except OSError:
                            pass
            except OSError:
                pass
        return snapshot

    def check_for_changes(self) -> bool:
        """Return True if any watched file or directory has changed since the last check."""
        current = self.poll_snapshot()
        if current != self._last_snapshot:
            self._last_snapshot = current
            return True
        return False


def watch_subagent_tree(
    root_id: str | None = None,
    target_cwd: str | None = None,
    interval: float = DEFAULT_WATCH_INTERVAL,
    max_days_back: int = 3,
    verbose: bool = False,
) -> int:
    """Watch subagent tree with flicker-free event-triggered repainting and live animation until Ctrl-C."""
    clear_screen_once = "\033[2J\033[H"
    hide_cursor, show_cursor = "\033[?25l", "\033[?25h"
    out = sys.stdout
    out.write(hide_cursor + clear_screen_once)
    out.flush()

    tick = 0
    watcher = SubagentEventWatcher(max_days_back=max_days_back)
    cached_tree: SubagentTree = SubagentTree(root_id="", nodes=[], total_count=0)
    cached_exchanges: list[SubagentExchange] = []
    last_heartbeat = 0.0
    last_repaint_time = 0.0
    min_repaint_interval = 0.08  # Throttle rapid bursts to avoid flicker
    prev_line_count = 0

    def repaint(lines: list[str]) -> None:
        nonlocal prev_line_count, last_repaint_time
        buffer = ["\033[H"]
        for line in lines:
            buffer.append(f"{line}\033[K\n")
        if prev_line_count > len(lines):
            for _ in range(prev_line_count - len(lines)):
                buffer.append("\033[K\n")
            buffer.append("\033[J")
        prev_line_count = len(lines)
        out.write("".join(buffer))
        out.flush()
        last_repaint_time = time.time()

    def reload_data() -> None:
        nonlocal cached_tree, cached_exchanges
        effective_root = root_id
        known: list[SubagentInfo] = []
        if not effective_root:
            recent = find_most_recent_rollout(max_days_back=max_days_back, target_cwd=target_cwd)
            if recent:
                effective_root = find_root_session_id(recent[1], max_days_back=max_days_back)
                rollouts = find_rollouts_in_days(max_days_back=max_days_back)
                for r in rollouts:
                    if r[1] == effective_root:
                        known = parse_rollout_subagents(r[0])
                        break
        else:
            effective_root = find_root_session_id(effective_root, max_days_back=max_days_back)

        cached_tree = (
            build_subagent_tree(effective_root, known_subagents=known, max_days_back=max_days_back)
            if effective_root
            else SubagentTree(root_id="", nodes=[], total_count=0)
        )
        cached_exchanges = (
            extract_subagent_exchanges(session_id=effective_root, max_days_back=max_days_back)
            if effective_root
            else []
        )

    def is_any_communicating() -> bool:
        now_dt = datetime.now(timezone.utc)
        now_ts = now_dt.timestamp()
        all_nodes = flatten_subagent_tree(cached_tree.nodes)
        for n in all_nodes:
            if n.status not in ("running", "starting"):
                continue
            for ex in cached_exchanges:
                if ex.sender_id == n.id or ex.receiver_id == n.id:
                    if ex.timestamp:
                        if (now_ts - ex.timestamp.timestamp()) <= COMM_ACTIVE_WINDOW_SECONDS:
                            return True
                    else:
                        return True
        return False

    try:
        # Initial load and paint
        reload_data()
        initial_lines = render_subagent_tree_page(
            cached_tree,
            color=True,
            tick=tick,
            recent_exchanges=cached_exchanges,
            verbose=verbose,
        )
        repaint(initial_lines)
        last_heartbeat = time.time()

        while True:
            # Event-driven change check
            has_event = watcher.check_for_changes()
            now_time = time.time()
            needs_repaint = False

            if has_event:
                reload_data()
                needs_repaint = True
                last_heartbeat = now_time

            communicating = is_any_communicating()

            if communicating:
                # Active communication -> spinner animation frame update
                tick += 1
                needs_repaint = True
                sleep_duration = 0.2
            else:
                # Idle state: repaint only when heartbeat interval elapsed (to update elapsed times)
                if now_time - last_heartbeat >= interval:
                    needs_repaint = True
                    last_heartbeat = now_time
                sleep_duration = 0.1

            if needs_repaint:
                # Throttle burst repaints if triggered too rapidly
                elapsed_since_last = now_time - last_repaint_time
                if elapsed_since_last < min_repaint_interval and has_event:
                    time.sleep(min_repaint_interval - elapsed_since_last)
                
                lines = render_subagent_tree_page(
                    cached_tree,
                    color=True,
                    tick=tick,
                    recent_exchanges=cached_exchanges,
                    verbose=verbose,
                )
                repaint(lines)

            time.sleep(sleep_duration)

    except KeyboardInterrupt:
        pass
    finally:
        out.write(show_cursor)
        out.flush()
    return 0
