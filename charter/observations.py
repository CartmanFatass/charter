"""Normalized observation layer and exact workflow metadata parser.

Charter observes facts from Codex rollouts, Hook traces, and inflight state,
recording exact declarations and evidence references without making supervisor decisions.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Mapping, Sequence

from . import config, subagent

EvidenceClass = Literal["mechanical", "declared", "derived"]
ObservationSource = Literal[
    "rollout_meta",
    "rollout_event",
    "hook_trace",
    "inflight",
]
ObservationKind = Literal[
    "session_seen",
    "actor_spawned",
    "actor_started",
    "dispatch_sent",
    "message_sent",
    "tool_started",
    "tool_finished",
    "actor_returned",
    "actor_stopped",
    "incident_seen",
    "workflow_declared",
]

_KIND_PRIORITY: dict[str, int] = {
    "session_seen": 0,
    "actor_spawned": 10,
    "dispatch_sent": 20,
    "workflow_declared": 25,
    "actor_started": 30,
    "message_sent": 40,
    "tool_started": 50,
    "tool_finished": 60,
    "incident_seen": 70,
    "actor_returned": 80,
    "actor_stopped": 90,
}


def _event_priority(e: ObservedEvent) -> int:
    """Determine deterministic causal priority for event ordering."""
    if e.kind == "workflow_declared":
        decl_dict = e.attributes.get("declaration")
        decl_ev = ""
        if isinstance(decl_dict, dict):
            decl_ev = decl_dict.get("event") or ""
        elif isinstance(decl_dict, WorkflowDeclaration):
            decl_ev = decl_dict.event
        if decl_ev == "dispatch":
            return 20
        elif decl_ev == "intake":
            return 82
        elif decl_ev == "resolve":
            return 85
        elif decl_ev == "relation":
            return 25
        return 25
    return _KIND_PRIORITY.get(e.kind, 50)

def _topological_causal_sort(
    events: list[ObservedEvent],
    warnings: list[str],
) -> list[ObservedEvent]:
    """Perform deterministic topological causal sort preserving intra-source stream order."""
    if not events:
        return []

    n = len(events)
    in_degree: list[int] = [0] * n
    adj: list[list[int]] = [[] for _ in range(n)]

    # 1. Intra-source physical stream edges (unify physical rollout file lines regardless of meta/event)
    streams: dict[tuple[Any, ...], list[int]] = {}
    for idx, ev in enumerate(events):
        fpath = ev.evidence[0].file_path if ev.evidence else None
        stream_key = (ev.session_id, fpath)
        streams.setdefault(stream_key, []).append(idx)

    for stream_indices in streams.values():
        for i in range(len(stream_indices) - 1):
            u = stream_indices[i]
            v = stream_indices[i + 1]
            adj[u].append(v)
            in_degree[v] += 1

    # 2. Assignment-specific cross-session causal dependencies:
    # Partition each child session into activity segments demarcated by actor_returned
    child_segments: dict[str, list[list[int]]] = {}
    for idx, ev in enumerate(events):
        if ev.session_id:
            cs_segs = child_segments.setdefault(ev.session_id, [[]])
            cs_segs[-1].append(idx)
            if ev.kind == "actor_returned":
                cs_segs.append([])

    # Clean empty trailing segment if any
    for sid, segs in child_segments.items():
        if len(segs) > 1 and not segs[-1]:
            segs.pop()

    # Collect dispatch_sent events per child in order
    dispatches_by_child: dict[str, list[int]] = {}
    for idx, ev in enumerate(events):
        if ev.kind == "dispatch_sent" and ev.actor_id:
            dispatches_by_child.setdefault(ev.actor_id, []).append(idx)

    # Connect dispatch_i -> first event of segment_i in that child session
    for child_sid, disp_indices in dispatches_by_child.items():
        segs = child_segments.get(child_sid, [])
        for seg_idx, disp_idx in enumerate(disp_indices):
            if seg_idx < len(segs) and segs[seg_idx]:
                first_in_seg = segs[seg_idx][0]
                if first_in_seg != disp_idx and first_in_seg not in adj[disp_idx]:
                    adj[disp_idx].append(first_in_seg)
                    in_degree[first_in_seg] += 1

    # Map each dispatch instance to its corresponding child return event
    dispatch_to_return: dict[int, int] = {}
    for child_sid, disp_indices in dispatches_by_child.items():
        segs = child_segments.get(child_sid, [])
        for seg_idx, disp_idx in enumerate(disp_indices):
            if seg_idx < len(segs):
                for e_idx in segs[seg_idx]:
                    if events[e_idx].kind == "actor_returned":
                        dispatch_to_return[disp_idx] = e_idx
                        break

    # Connect assignment-specific child return -> coordinator intake
    for idx, ev in enumerate(events):
        if ev.kind == "workflow_declared":
            decl_dict = ev.attributes.get("declaration")
            if isinstance(decl_dict, dict) and decl_dict.get("event") == "intake":
                w_id = decl_dict.get("work_id")
                if w_id:
                    for d_idx, d_ev in enumerate(events):
                        if d_ev.kind == "dispatch_sent":
                            d_decl = d_ev.attributes.get("declaration")
                            if isinstance(d_decl, dict) and d_decl.get("work_id") == w_id:
                                ret_idx = dispatch_to_return.get(d_idx)
                                if ret_idx is not None and ret_idx != idx and idx not in adj[ret_idx]:
                                    if events[ret_idx].session_id != ev.session_id:
                                        # ONLY add causal edge if intake is NOT observably earlier than child return
                                        if ev.observed_at >= events[ret_idx].observed_at:
                                            adj[ret_idx].append(idx)
                                            in_degree[idx] += 1
                                break
    # Min-heap priority queue: (observed_at, priority, ordinal, id, node_index)
    heap: list[tuple[datetime, int, int, str, int]] = []
    for idx in range(n):
        if in_degree[idx] == 0:
            ev = events[idx]
            heapq.heappush(
                heap,
                (ev.observed_at, _event_priority(ev), ev.ordinal, ev.id, idx),
            )

    sorted_events: list[ObservedEvent] = []
    visited = [False] * n

    while heap:
        _, _, _, _, u = heapq.heappop(heap)
        if visited[u]:
            continue
        visited[u] = True
        sorted_events.append(events[u])

        for v in adj[u]:
            in_degree[v] -= 1
            if in_degree[v] == 0 and not visited[v]:
                ev_v = events[v]
                heapq.heappush(
                    heap,
                    (ev_v.observed_at, _event_priority(ev_v), ev_v.ordinal, ev_v.id, v),
                )

    if len(sorted_events) < n:
        warnings.append("Causal ordering cycle detected; falling back to source ordinal for remaining events")
        remaining = [
            (events[i].observed_at, events[i].ordinal, events[i].id, i)
            for i in range(n)
            if not visited[i]
        ]
        remaining.sort()
        for _, _, _, i in remaining:
            sorted_events.append(events[i])

    return sorted_events

_MAX_DECLARATION_BLOCK_BYTES = 4096

# Exact line-anchored fenced block: requires ```charter-observe on its own line and ``` on its own line.
# Rejects lines wrapped by 4 or more backticks.
_CHARTER_OBSERVE_FENCE_RE = re.compile(
    r"(?m)^[ \t]*```charter-observe[ \t]*\r?\n(.*?)\r?\n[ \t]*```[ \t]*$",
    re.DOTALL,
)

_ALLOWED_DECLARATION_FIELDS = {
    "schema",
    "event",
    "work_id",
    "title",
    "direction",
    "actor_role",
    "owner_role",
    "actor_id",
    "relation",
    "related_actor_id",
}
_ALLOWED_EVENTS = {"dispatch", "intake", "resolve", "relation"}
_ALLOWED_RELATIONS = {"peer", "reports_to", "owner"}


@dataclass(frozen=True)
class EvidenceRef:
    """A reference to an exact source file, line number, or trace entry."""

    source: ObservationSource
    source_id: str
    raw_kind: str
    observed_at: datetime
    evidence_class: EvidenceClass
    file_path: str | None = None
    line_number: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_id": self.source_id,
            "raw_kind": self.raw_kind,
            "observed_at": self.observed_at.isoformat(),
            "evidence_class": self.evidence_class,
            "file_path": self.file_path,
            "line_number": self.line_number,
        }


@dataclass(frozen=True)
class SessionObservation:
    """Observed facts about a single rollout session/thread."""

    id: str
    parent_id: str | None
    name: str
    started_at: datetime | None
    cwd: str | None
    rollout_file: str | None
    basis: tuple[EvidenceRef, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "name": self.name,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "cwd": self.cwd,
            "rollout_file": self.rollout_file,
            "basis": [b.to_dict() for b in self.basis],
        }


@dataclass(frozen=True)
class ObservedEvent:
    """An immutable observed event with machine-readable evidence basis and causal ordinal."""

    id: str
    root_id: str
    session_id: str
    kind: ObservationKind
    observed_at: datetime
    actor_id: str | None = None
    actor_name: str | None = None
    peer_id: str | None = None
    peer_name: str | None = None
    author_actor_id: str | None = None
    summary: str = ""
    attributes: Mapping[str, Any] = field(default_factory=dict)
    evidence: tuple[EvidenceRef, ...] = ()
    ordinal: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "root_id": self.root_id,
            "session_id": self.session_id,
            "kind": self.kind,
            "observed_at": self.observed_at.isoformat(),
            "actor_id": self.actor_id,
            "actor_name": self.actor_name,
            "peer_id": self.peer_id,
            "peer_name": self.peer_name,
            "author_actor_id": self.author_actor_id,
            "summary": self.summary,
            "attributes": dict(self.attributes),
            "evidence": [e.to_dict() for e in self.evidence],
            "ordinal": self.ordinal,
        }


@dataclass(frozen=True)
class WorkflowDeclaration:
    """An exact structured declaration from a charter-observe metadata block."""

    schema: int
    event: Literal["dispatch", "intake", "resolve", "relation"]
    work_id: str | None = None
    title: str | None = None
    direction: str | None = None
    actor_role: str | None = None
    owner_role: str | None = None
    actor_id: str | None = None
    relation: Literal["peer", "reports_to", "owner"] | None = None
    related_actor_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "event": self.event,
            "work_id": self.work_id,
            "title": self.title,
            "direction": self.direction,
            "actor_role": self.actor_role,
            "owner_role": self.owner_role,
            "actor_id": self.actor_id,
            "relation": self.relation,
            "related_actor_id": self.related_actor_id,
        }


@dataclass(frozen=True)
class ObservationSnapshot:
    """An immutable point-in-time observation snapshot across a root session and its descendants."""

    root_id: str
    captured_at: datetime
    sessions: Mapping[str, SessionObservation]
    events: tuple[ObservedEvent, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_id": self.root_id,
            "captured_at": self.captured_at.isoformat(),
            "sessions": {k: v.to_dict() for k, v in self.sessions.items()},
            "events": [e.to_dict() for e in self.events],
            "warnings": list(self.warnings),
        }


def make_event_id(
    source: str,
    file_path: str | Path | None,
    line_number: int | None,
    raw_kind: str,
    session_id: str | None,
    actor_id: str | None = None,
    peer_id: str | None = None,
    discriminator: str | None = None,
    ordinal: int | None = None,
) -> str:
    """Generate a deterministic 64-hex SHA-256 event ID incorporating source ordinal."""
    norm_path = ""
    if file_path:
        norm_path = Path(file_path).as_posix()
    line_str = str(line_number) if line_number is not None else ""
    parts = [
        str(source or ""),
        norm_path,
        line_str,
        str(raw_kind or ""),
        str(session_id or ""),
        str(actor_id or ""),
        str(peer_id or ""),
    ]
    if ordinal is not None:
        parts.append(str(ordinal))
    if discriminator:
        parts.append(str(discriminator))
    raw_string = "|".join(parts)
    return hashlib.sha256(raw_string.encode("utf-8")).hexdigest()


def sanitize_event_for_public_view(
    event: ObservedEvent,
    *,
    include_content: bool = False,
) -> dict[str, Any]:
    """Produce a public dictionary representation of an event with sensitive content elided by default."""
    d = event.to_dict()
    if not include_content:
        attrs = dict(d.get("attributes", {}))
        for k in ("content", "prompt", "arguments", "output", "raw_message"):
            if k in attrs:
                attrs[k] = f"[{k} elided; pass --include-content to view]"
        d["attributes"] = attrs
    return d

def _no_duplicate_keys_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for k, v in pairs:
        if k in res:
            raise ValueError(f"Duplicate key '{k}' in JSON declaration")
        res[k] = v
    return res


def parse_workflow_declarations(
    text: str,
    warnings: list[str] | None = None,
) -> tuple[WorkflowDeclaration, ...]:
    """Parse exact charter-observe metadata blocks with strict validation."""
    if not text or "```charter-observe" not in text:
        return ()

    # Check for four-backtick blocks that might enclose three backticks
    if "````" in text:
        # If it's an outer 4-backtick block, ignore internal 3-backtick pseudo matches
        clean_text = re.sub(r"(?m)^[ \t]*````+.*?\r?\n.*?\r?\n[ \t]*````+[ \t]*$", "", text, flags=re.DOTALL)
        if "```charter-observe" not in clean_text:
            return ()
        text = clean_text

    blocks = _CHARTER_OBSERVE_FENCE_RE.findall(text)
    if not blocks:
        return ()

    if len(blocks) > 1:
        if warnings is not None:
            warnings.append(f"Multiple declaration blocks found in message ({len(blocks)} blocks); ignored.")
        return ()

    raw_block = blocks[0].strip()
    raw_bytes = raw_block.encode("utf-8")
    if len(raw_bytes) > _MAX_DECLARATION_BLOCK_BYTES:
        if warnings is not None:
            warnings.append(f"Declaration block exceeds maximum allowed size of {_MAX_DECLARATION_BLOCK_BYTES} bytes; ignored.")
        return ()

    try:
        data = json.loads(raw_block, object_pairs_hook=_no_duplicate_keys_hook)
    except Exception as exc:
        if warnings is not None:
            warnings.append(f"Malformed JSON in charter-observe block: {exc}")
        return ()

    if not isinstance(data, dict):
        if warnings is not None:
            warnings.append("charter-observe block must be a single JSON object")
        return ()

    # Check unknown fields
    unknown_keys = set(data.keys()) - _ALLOWED_DECLARATION_FIELDS
    if unknown_keys:
        if warnings is not None:
            warnings.append(f"Unknown field(s) in charter-observe block: {sorted(unknown_keys)}")
        return ()

    schema_val = data.get("schema")
    # Strict integer 1 check: reject True (bool) and 1.0 (float)
    if type(schema_val) is not int or schema_val != 1:
        if warnings is not None:
            warnings.append(f"Unsupported schema version: {schema_val} (expected integer 1)")
        return ()

    event_val = data.get("event")
    if not isinstance(event_val, str) or event_val not in _ALLOWED_EVENTS:
        if warnings is not None:
            warnings.append(f"Unsupported or non-string event type: '{event_val}'")
        return ()

    # Validate string fields (reject list, dict, bool, int passed into string fields)
    for str_field in ("work_id", "title", "direction", "actor_role", "owner_role", "actor_id", "related_actor_id", "relation"):
        if str_field in data:
            val = data[str_field]
            if val is not None and not isinstance(val, str):
                if warnings is not None:
                    warnings.append(f"Field '{str_field}' must be a string, got {type(val).__name__}")
                return ()

    # Event-specific schema validation
    if event_val == "dispatch":
        for forbidden in ("relation", "related_actor_id"):
            if forbidden in data:
                if warnings is not None:
                    warnings.append(f"Event 'dispatch' forbids field '{forbidden}'")
                return ()
    elif event_val in ("intake", "resolve"):
        work_id_val = data.get("work_id")
        if not work_id_val or not isinstance(work_id_val, str) or not str(work_id_val).strip():
            if warnings is not None:
                warnings.append(f"Missing or empty work_id for '{event_val}' event declaration")
            return ()
        for forbidden in ("relation", "related_actor_id", "title", "direction", "actor_role", "owner_role"):
            if forbidden in data:
                if warnings is not None:
                    warnings.append(f"Event '{event_val}' forbids field '{forbidden}'")
                return ()
    elif event_val == "relation":
        relation_val = data.get("relation")
        if not relation_val or not isinstance(relation_val, str) or relation_val not in _ALLOWED_RELATIONS:
            if warnings is not None:
                warnings.append(f"Event 'relation' requires valid relation in {sorted(_ALLOWED_RELATIONS)}, got '{relation_val}'")
            return ()
        rel_actor = data.get("related_actor_id")
        if not rel_actor or not isinstance(rel_actor, str) or not rel_actor.strip():
            if warnings is not None:
                warnings.append("Event 'relation' requires non-empty related_actor_id")
            return ()
        for forbidden in ("work_id", "title", "direction", "actor_role", "owner_role"):
            if forbidden in data:
                if warnings is not None:
                    warnings.append(f"Event 'relation' forbids field '{forbidden}'")
                return ()

    work_id_val = data.get("work_id")
    relation_val = data.get("relation")

    decl = WorkflowDeclaration(
        schema=1,
        event=event_val,
        work_id=str(work_id_val).strip() if work_id_val else None,
        title=str(data["title"]).strip() if data.get("title") else None,
        direction=str(data["direction"]).strip() if data.get("direction") else None,
        actor_role=str(data["actor_role"]).strip() if data.get("actor_role") else None,
        owner_role=str(data["owner_role"]).strip() if data.get("owner_role") else None,
        actor_id=str(data["actor_id"]).strip() if data.get("actor_id") else None,
        relation=relation_val,
        related_actor_id=str(data["related_actor_id"]).strip() if data.get("related_actor_id") else None,
    )
    return (decl,)

def _ensure_utc(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def collect_observation_snapshot(
    root_id: str,
    *,
    max_days_back: int = 3,
    sessions_dir: Path | None = None,
    include_tool_calls: bool = False,
    captured_at: datetime | None = None,
) -> ObservationSnapshot:
    """Collect an immutable observation snapshot across root_id and all its descendants."""
    cap_time = _ensure_utc(captured_at)
    if not root_id:
        return ObservationSnapshot(
            root_id="",
            captured_at=cap_time,
            sessions={},
            events=(),
            warnings=(),
        )

    s_dir = sessions_dir or subagent.get_sessions_dir()
    index = subagent.build_session_index(max_days_back=max_days_back, sessions_dir=s_dir)
    effective_root = root_id
    if root_id in index.links and index.links[root_id].parent_id:
        effective_root = subagent.find_root_session_id(root_id, max_days_back=max_days_back, sessions_dir=s_dir)

    descendants = subagent.descendant_session_ids(index, effective_root)
    if not descendants:
        descendants = (effective_root,)

    warnings: list[str] = []
    sessions_obs: dict[str, SessionObservation] = {}
    events: list[ObservedEvent] = []
    ordinal_counter = 0

    pending_dispatch_mirrors: dict[tuple[Any, ...], ObservedEvent] = {}
    initial_user_message_seen: set[str] = set()
    for sid in descendants:
        link = index.links.get(sid)
        rollout_file = index.rollout_files.get(sid)
        file_path_str = str(rollout_file) if rollout_file else None

        base_evidence: list[EvidenceRef] = []
        if rollout_file and rollout_file.is_file():
            base_ev = EvidenceRef(
                source="rollout_meta",
                source_id=sid,
                raw_kind="session_meta",
                observed_at=_ensure_utc(link.started_at or link.modified_at if link else cap_time),
                evidence_class="mechanical",
                file_path=file_path_str,
                line_number=1,
            )
            base_evidence.append(base_ev)

        sess_name = link.name if link else sid[:8]
        sess_parent = link.parent_id if link else None
        sess_start = _ensure_utc(link.started_at) if link and link.started_at else None
        sess_cwd = link.cwd if link else None

        sessions_obs[sid] = SessionObservation(
            id=sid,
            parent_id=sess_parent,
            name=sess_name,
            started_at=sess_start,
            cwd=sess_cwd,
            rollout_file=file_path_str,
            basis=tuple(base_evidence),
        )

        if not rollout_file or not rollout_file.is_file():
            continue

        for rec in subagent.iter_rollout_records(rollout_file):
            ordinal_counter += 1
            ts = _ensure_utc(rec.timestamp) if rec.timestamp else cap_time
            ev_ref = EvidenceRef(
                source="rollout_meta" if rec.entry_type == "session_meta" else "rollout_event",
                source_id=f"{sid}:{rec.line_number}",
                raw_kind=rec.raw_kind,
                observed_at=ts,
                evidence_class="mechanical",
                file_path=file_path_str,
                line_number=rec.line_number,
            )

            if rec.entry_type == "session_meta":
                if sess_parent:
                    ev_id = make_event_id("rollout_meta", file_path_str, rec.line_number, "session_meta", sid, actor_id=sid, ordinal=0)
                    events.append(ObservedEvent(
                        id=ev_id,
                        root_id=effective_root,
                        session_id=sid,
                        kind="actor_started",
                        observed_at=ts,
                        actor_id=sid,
                        actor_name=sess_name,
                        peer_id=sess_parent,
                        peer_name=index.links[sess_parent].name if sess_parent in index.links else None,
                        author_actor_id=sid,
                        summary=f"Subagent {sess_name} started",
                        evidence=(ev_ref,),
                        ordinal=ordinal_counter,
                    ))
                else:
                    ev_id = make_event_id("rollout_meta", file_path_str, rec.line_number, "session_meta", sid, ordinal=0)
                    events.append(ObservedEvent(
                        id=ev_id,
                        root_id=effective_root,
                        session_id=sid,
                        kind="session_seen",
                        observed_at=ts,
                        author_actor_id=sid,
                        summary=f"Session {sess_name} seen",
                        evidence=(ev_ref,),
                        ordinal=ordinal_counter,
                    ))

            elif rec.entry_type in ("event_msg", "response_item") and isinstance(rec.payload, dict):
                p_type = rec.payload.get("type") or rec.entry_type
                if p_type == "item_completed" or rec.entry_type == "response_item":
                    item = rec.payload.get("item") if p_type == "item_completed" else rec.payload
                    if isinstance(item, dict):
                        item_type = item.get("type") or item.get("tool")
                        if item_type == "CollabAgentToolCall" or item.get("tool") in ("spawn_agent", "wait", "close_agent"):
                            tool_name = item.get("tool")
                            states = item.get("agents_states") or {}

                            if tool_name == "spawn_agent":
                                receivers = item.get("receiver_agents") or []
                                if not receivers and item.get("receiver_thread_ids"):
                                    receivers = [
                                        {"thread_id": tid, "agent_nickname": str(tid)[:8]}
                                        for tid in item["receiver_thread_ids"] if tid
                                    ]
                                prompt = str(item.get("prompt") or "")

                                decls = parse_workflow_declarations(prompt, warnings=warnings)
                                for rx_idx, rx in enumerate(receivers):
                                    rx_id = rx.get("thread_id") or ""
                                    rx_name = rx.get("agent_nickname") or rx_id[:8] or "subagent"

                                    if rx_id and rx_id not in sessions_obs:
                                        sessions_obs[rx_id] = SessionObservation(
                                            id=rx_id,
                                            parent_id=sid,
                                            name=rx_name,
                                            started_at=ts,
                                            cwd=sess_cwd,
                                            rollout_file=None,
                                            basis=(ev_ref,),
                                        )

                                    ev_spawn_id = make_event_id("rollout_event", file_path_str, rec.line_number, "spawn_agent", sid, actor_id=rx_id, discriminator=f"spawn:{rx_id}", ordinal=rx_idx)
                                    events.append(ObservedEvent(
                                        id=ev_spawn_id,
                                        root_id=effective_root,
                                        session_id=sid,
                                        kind="actor_spawned",
                                        observed_at=ts,
                                        actor_id=rx_id,
                                        actor_name=rx_name,
                                        peer_id=sid,
                                        peer_name=sess_name,
                                        author_actor_id=sid,
                                        summary=f"Spawned subagent {rx_name}",
                                        evidence=(ev_ref,),
                                        ordinal=ordinal_counter,
                                    ))

                                    disp_attrs: dict[str, Any] = {"prompt": prompt}
                                    if decls:
                                        disp_attrs["declaration"] = decls[0].to_dict()

                                    ev_disp_id = make_event_id("rollout_event", file_path_str, rec.line_number, "spawn_agent", sid, actor_id=rx_id, discriminator=f"dispatch:{rx_id}", ordinal=rx_idx)
                                    events.append(ObservedEvent(
                                        id=ev_disp_id,
                                        root_id=effective_root,
                                        session_id=sid,
                                        kind="dispatch_sent",
                                        observed_at=ts,
                                        actor_id=rx_id,
                                        actor_name=rx_name,
                                        peer_id=sid,
                                        peer_name=sess_name,
                                        author_actor_id=sid,
                                        summary=f"Dispatch sent to {rx_name}",
                                        attributes=disp_attrs,
                                        evidence=(ev_ref,),
                                        ordinal=ordinal_counter,
                                    ))

                                    # Check exact declarations in spawn prompt
                                    for d_idx, d in enumerate(decls):
                                        decl_ev_ref = EvidenceRef(
                                            source="rollout_event",
                                            source_id=f"{sid}:{rec.line_number}:decl",
                                            raw_kind="workflow_declared",
                                            observed_at=ts,
                                            evidence_class="declared",
                                            file_path=file_path_str,
                                            line_number=rec.line_number,
                                        )
                                        decl_ev_id = make_event_id("rollout_event", file_path_str, rec.line_number, "workflow_declared", sid, actor_id=rx_id, discriminator=f"decl:{d.event}:{d.work_id or rx_id}:{d_idx}", ordinal=d_idx)
                                        decl_event = ObservedEvent(
                                            id=decl_ev_id,
                                            root_id=effective_root,
                                            session_id=sid,
                                            kind="workflow_declared",
                                            observed_at=ts,
                                            actor_id=rx_id,
                                            actor_name=rx_name,
                                            peer_id=sid,
                                            peer_name=sess_name,
                                            author_actor_id=sid,
                                            summary=f"Workflow declared: {d.event} ({d.work_id or d.title or ''})",
                                            attributes={"declaration": d.to_dict()},
                                            evidence=(decl_ev_ref,),
                                            ordinal=ordinal_counter,
                                        )
                                        events.append(decl_event)
                                        if d.event == "dispatch":
                                            mirror_key = (
                                                d.schema,
                                                "dispatch",
                                                d.work_id,
                                                d.actor_id,
                                                rx_id,
                                                d.title,
                                                d.direction,
                                                d.actor_role,
                                                d.owner_role,
                                            )
                                            pending_dispatch_mirrors[mirror_key] = decl_event
                            # Check typed incidents in agents_states on ANY collab tool call
                            for aid_idx, (aid, astate) in enumerate(states.items()):
                                if isinstance(astate, dict) and (astate.get("error") or astate.get("failed")):
                                    inc_err = str(astate.get("error") or astate.get("failed") or "Agent error state")
                                    inc_id = make_event_id("rollout_event", file_path_str, rec.line_number, "incident", sid, actor_id=aid, discriminator=f"incident:{aid}", ordinal=aid_idx)
                                    events.append(ObservedEvent(
                                        id=inc_id,
                                        root_id=effective_root,
                                        session_id=sid,
                                        kind="incident_seen",
                                        observed_at=ts,
                                        actor_id=aid,
                                        actor_name=index.links[aid].name if aid in index.links else str(aid)[:8],
                                        author_actor_id=sid,
                                        summary=f"Agent error observed: {inc_err}",
                                        attributes={"error": inc_err},
                                        evidence=(ev_ref,),
                                        ordinal=ordinal_counter,
                                    ))

                        elif include_tool_calls and item_type in ("function_call", "custom_tool_call"):
                            t_name = str(item.get("name") or item.get("tool") or "tool")
                            ev_t_id = make_event_id("rollout_event", file_path_str, rec.line_number, "tool_started", sid, discriminator=f"tool_start:{t_name}", ordinal=0)
                            events.append(ObservedEvent(
                                id=ev_t_id,
                                root_id=effective_root,
                                session_id=sid,
                                kind="tool_started",
                                observed_at=ts,
                                actor_id=sid,
                                actor_name=sess_name,
                                author_actor_id=sid,
                                summary=f"Tool call: {t_name}",
                                attributes={"tool_name": t_name},
                                evidence=(ev_ref,),
                                ordinal=ordinal_counter,
                            ))
                        elif include_tool_calls and item_type in ("function_call_output", "custom_tool_call_output"):
                            ev_tf_id = make_event_id("rollout_event", file_path_str, rec.line_number, "tool_finished", sid, discriminator="tool_finish", ordinal=0)
                            events.append(ObservedEvent(
                                id=ev_tf_id,
                                root_id=effective_root,
                                session_id=sid,
                                kind="tool_finished",
                                observed_at=ts,
                                actor_id=sid,
                                actor_name=sess_name,
                                author_actor_id=sid,
                                summary="Tool finished",
                                evidence=(ev_ref,),
                                ordinal=ordinal_counter,
                            ))

                elif p_type == "user_message":
                    msg_text = str(rec.payload.get("message") or rec.payload.get("content") or "")
                    ev_msg_id = make_event_id("rollout_event", file_path_str, rec.line_number, "user_message", sid, discriminator="user_msg", ordinal=0)
                    events.append(ObservedEvent(
                        id=ev_msg_id,
                        root_id=effective_root,
                        session_id=sid,
                        kind="message_sent",
                        observed_at=ts,
                        actor_id=sess_parent,
                        peer_id=sid,
                        author_actor_id=sess_parent,
                        summary="Parent message sent",
                        attributes={"content": msg_text},
                        evidence=(ev_ref,),
                        ordinal=ordinal_counter,
                    ))

                    # Check mirrored declarations in user_message (first user_message only!)
                    is_first_user_msg = (sid not in initial_user_message_seen)
                    initial_user_message_seen.add(sid)
                    decls = parse_workflow_declarations(msg_text, warnings=warnings)
                    for d_idx, d in enumerate(decls):
                        decl_ev_ref = EvidenceRef(
                            source="rollout_event",
                            source_id=f"{sid}:{rec.line_number}:decl",
                            raw_kind="workflow_declared",
                            observed_at=ts,
                            evidence_class="declared",
                            file_path=file_path_str,
                            line_number=rec.line_number,
                        )
                        mirror_key = (
                            d.schema,
                            "dispatch",
                            d.work_id,
                            d.actor_id,
                            sid,
                            d.title,
                            d.direction,
                            d.actor_role,
                            d.owner_role,
                        )
                        if is_first_user_msg and d.event == "dispatch" and mirror_key in pending_dispatch_mirrors:
                            orig_event = pending_dispatch_mirrors.pop(mirror_key)
                            for e_idx, ev_item in enumerate(events):
                                if ev_item.id == orig_event.id:
                                    updated_event = ObservedEvent(
                                        id=orig_event.id,
                                        root_id=orig_event.root_id,
                                        session_id=orig_event.session_id,
                                        kind=orig_event.kind,
                                        observed_at=orig_event.observed_at,
                                        actor_id=orig_event.actor_id,
                                        actor_name=orig_event.actor_name,
                                        peer_id=orig_event.peer_id,
                                        peer_name=orig_event.peer_name,
                                        author_actor_id=orig_event.author_actor_id,
                                        summary=orig_event.summary,
                                        attributes=orig_event.attributes,
                                        evidence=orig_event.evidence + (decl_ev_ref,),
                                        ordinal=orig_event.ordinal,
                                    )
                                    events[e_idx] = updated_event
                                    break
                        else:
                            decl_author = sess_parent or sid
                            decl_ev_id = make_event_id("rollout_event", file_path_str, rec.line_number, "workflow_declared", sid, actor_id=decl_author, discriminator=f"decl:{d.event}:{d.work_id or sid}:{d_idx}", ordinal=d_idx)
                            decl_event = ObservedEvent(
                                id=decl_ev_id,
                                root_id=effective_root,
                                session_id=sid,
                                kind="workflow_declared",
                                observed_at=ts,
                                actor_id=decl_author,
                                actor_name=index.links[decl_author].name if decl_author in index.links else decl_author[:8],
                                peer_id=sid if sess_parent else None,
                                author_actor_id=decl_author,
                                summary=f"Workflow declared: {d.event} ({d.work_id or d.title or ''})",
                                attributes={"declaration": d.to_dict()},
                                evidence=(decl_ev_ref,),
                                ordinal=ordinal_counter,
                            )
                            events.append(decl_event)

                    # Expire all pending dispatch mirrors for this child after its first user_message
                    if is_first_user_msg:
                        for k in list(pending_dispatch_mirrors.keys()):
                            if len(k) >= 5 and k[4] == sid:
                                pending_dispatch_mirrors.pop(k, None)
                elif p_type == "agent_message":
                    msg_text = str(rec.payload.get("message") or rec.payload.get("content") or "")
                    ev_msg_id = make_event_id("rollout_event", file_path_str, rec.line_number, "agent_message", sid, discriminator="agent_msg", ordinal=0)
                    events.append(ObservedEvent(
                        id=ev_msg_id,
                        root_id=effective_root,
                        session_id=sid,
                        kind="message_sent",
                        observed_at=ts,
                        actor_id=sid,
                        actor_name=sess_name,
                        peer_id=sess_parent,
                        author_actor_id=sid,
                        summary="Agent message sent",
                        attributes={"content": msg_text},
                        evidence=(ev_ref,),
                        ordinal=ordinal_counter,
                    ))
                    # Declarations in agent messages are always distinct observed events
                    decls = parse_workflow_declarations(msg_text, warnings=warnings)
                    for d_idx, d in enumerate(decls):
                        decl_ev_ref = EvidenceRef(
                            source="rollout_event",
                            source_id=f"{sid}:{rec.line_number}:decl",
                            raw_kind="workflow_declared",
                            observed_at=ts,
                            evidence_class="declared",
                            file_path=file_path_str,
                            line_number=rec.line_number,
                        )
                        decl_ev_id = make_event_id("rollout_event", file_path_str, rec.line_number, "workflow_declared", sid, actor_id=sid, discriminator=f"decl:{d.event}:{d.work_id or sid}:{d_idx}", ordinal=d_idx)
                        decl_event = ObservedEvent(
                            id=decl_ev_id,
                            root_id=effective_root,
                            session_id=sid,
                            kind="workflow_declared",
                            observed_at=ts,
                            actor_id=sid,
                            actor_name=sess_name,
                            author_actor_id=sid,
                            summary=f"Workflow declared: {d.event} ({d.work_id or d.title or ''})",
                            attributes={"declaration": d.to_dict()},
                            evidence=(decl_ev_ref,),
                            ordinal=ordinal_counter,
                        )
                        events.append(decl_event)
                elif p_type == "task_complete":
                    sum_text = str(rec.payload.get("summary") or "Task complete")
                    ev_ret_id = make_event_id("rollout_event", file_path_str, rec.line_number, "task_complete", sid, discriminator="task_complete", ordinal=0)
                    events.append(ObservedEvent(
                        id=ev_ret_id,
                        root_id=effective_root,
                        session_id=sid,
                        kind="actor_returned",
                        observed_at=ts,
                        actor_id=sid,
                        actor_name=sess_name,
                        peer_id=sess_parent,
                        author_actor_id=sid,
                        summary=f"Task returned: {sum_text}",
                        attributes={"summary": sum_text},
                        evidence=(ev_ref,),
                        ordinal=ordinal_counter,
                    ))

    # Read hook traces for known sessions
    try:
        from . import trace
        for sid in list(sessions_obs.keys()):
            trace_events = trace.read(sid)
            for idx, tev in enumerate(trace_events, 1):
                ordinal_counter += 1
                tev_type = tev.get("event")
                ts_str = tev.get("ts")
                ts = _ensure_utc(subagent.parse_datetime(ts_str)) if ts_str else cap_time
                agent_label = tev.get("agent") or tev.get("persona") or sid[:8]
                t_file = trace._file(sid)
                t_file_str = str(t_file) if t_file else None

                t_ref = EvidenceRef(
                    source="hook_trace",
                    source_id=f"{sid}:{idx}",
                    raw_kind=str(tev_type),
                    observed_at=ts,
                    evidence_class="mechanical",
                    file_path=t_file_str,
                    line_number=idx,
                )

                if tev_type == "subagent_start":
                    t_ev_id = make_event_id("hook_trace", t_file_str, idx, "subagent_start", sid, actor_id=sid, discriminator="subagent_start", ordinal=0)
                    events.append(ObservedEvent(
                        id=t_ev_id,
                        root_id=effective_root,
                        session_id=sid,
                        kind="actor_started",
                        observed_at=ts,
                        actor_id=sid,
                        actor_name=agent_label,
                        author_actor_id=sid,
                        summary=f"Hook observed subagent_start: {agent_label}",
                        evidence=(t_ref,),
                        ordinal=ordinal_counter,
                    ))
                elif tev_type == "subagent_stop":
                    t_ev_id = make_event_id("hook_trace", t_file_str, idx, "subagent_stop", sid, actor_id=sid, discriminator="subagent_stop", ordinal=0)
                    events.append(ObservedEvent(
                        id=t_ev_id,
                        root_id=effective_root,
                        session_id=sid,
                        kind="actor_stopped",
                        observed_at=ts,
                        actor_id=sid,
                        actor_name=agent_label,
                        author_actor_id=sid,
                        summary=f"Hook observed subagent_stop: {agent_label}",
                        evidence=(t_ref,),
                        ordinal=ordinal_counter,
                    ))
    except (OSError, ValueError) as exc:
        warnings.append(f"Failed to read trace file: {exc}")

    # Read inflight records strictly read-only
    try:
        from . import inflight
        for sid in list(sessions_obs.keys()):
            in_recs = inflight.read_records(session_id=sid, prune=False)
            for irec_idx, irec in enumerate(in_recs):
                ordinal_counter += 1
                start_dt = _ensure_utc(datetime.fromtimestamp(irec["ts"], tz=timezone.utc))
                token = irec["token"]
                agent_name = irec["agent"]
                in_ref = EvidenceRef(
                    source="inflight",
                    source_id=token,
                    raw_kind="inflight_record",
                    observed_at=start_dt,
                    evidence_class="mechanical",
                )
                in_ev_id = make_event_id(
                    "inflight",
                    None,
                    None,
                    "inflight_record",
                    sid,
                    actor_id=irec.get("agent_id") or token,
                    discriminator=token,
                    ordinal=0,
                )
                events.append(ObservedEvent(
                    id=in_ev_id,
                    root_id=effective_root,
                    session_id=sid,
                    kind="actor_started",
                    observed_at=start_dt,
                    actor_id=irec.get("agent_id") or f"inflight-{token}",
                    actor_name=agent_name,
                    author_actor_id=sid,
                    summary=f"Inflight subagent active: {agent_name}",
                    evidence=(in_ref,),
                    ordinal=ordinal_counter,
                ))
    except (OSError, ValueError) as exc:
        warnings.append(f"Failed to read inflight records: {exc}")

    events = _topological_causal_sort(events, warnings)
    return ObservationSnapshot(
        root_id=effective_root,
        captured_at=cap_time,
        sessions=sessions_obs,
        events=tuple(events),
        warnings=tuple(warnings),
    )
