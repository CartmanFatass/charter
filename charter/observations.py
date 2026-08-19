"""Normalized observation layer and exact workflow metadata parser.

Charter observes facts from Codex rollouts, Hook traces, and inflight state,
recording exact declarations and evidence references without making supervisor decisions.
"""

from __future__ import annotations

import hashlib
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

_MAX_DECLARATION_BLOCK_BYTES = 4096
_CHARTER_OBSERVE_FENCE_RE = re.compile(r"```charter-observe[ \t]*\r?\n(.*?)\r?\n```", re.DOTALL)
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
    """An immutable observed event with machine-readable evidence basis."""

    id: str
    root_id: str
    session_id: str
    kind: ObservationKind
    observed_at: datetime
    actor_id: str | None = None
    actor_name: str | None = None
    peer_id: str | None = None
    peer_name: str | None = None
    summary: str = ""
    attributes: Mapping[str, Any] = field(default_factory=dict)
    evidence: tuple[EvidenceRef, ...] = ()

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
            "summary": self.summary,
            "attributes": dict(self.attributes),
            "evidence": [e.to_dict() for e in self.evidence],
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
) -> str:
    """Generate a deterministic 64-hex SHA-256 event ID."""
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
    if discriminator:
        parts.append(str(discriminator))
    raw_string = "|".join(parts)
    return hashlib.sha256(raw_string.encode("utf-8")).hexdigest()


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
    if schema_val != 1:
        if warnings is not None:
            warnings.append(f"Unsupported schema version: {schema_val} (expected 1)")
        return ()

    event_val = data.get("event")
    if event_val not in _ALLOWED_EVENTS:
        if warnings is not None:
            warnings.append(f"Unsupported event type: '{event_val}'")
        return ()

    work_id_val = data.get("work_id")
    if event_val in ("intake", "resolve") and not work_id_val:
        if warnings is not None:
            warnings.append(f"Missing work_id for '{event_val}' event declaration")
        return ()

    relation_val = data.get("relation")
    if relation_val is not None and relation_val not in _ALLOWED_RELATIONS:
        if warnings is not None:
            warnings.append(f"Unsupported relation kind: '{relation_val}'")
        return ()

    decl = WorkflowDeclaration(
        schema=1,
        event=event_val,
        work_id=str(work_id_val).strip() if work_id_val else None,
        title=str(data.get("title")).strip() if data.get("title") else None,
        direction=str(data.get("direction")).strip() if data.get("direction") else None,
        actor_role=str(data.get("actor_role")).strip() if data.get("actor_role") else None,
        owner_role=str(data.get("owner_role")).strip() if data.get("owner_role") else None,
        actor_id=str(data.get("actor_id")).strip() if data.get("actor_id") else None,
        relation=relation_val,
        related_actor_id=str(data.get("related_actor_id")).strip() if data.get("related_actor_id") else None,
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
    # If root_id is a child, resolve root
    if root_id in index.links and index.links[root_id].parent_id:
        effective_root = subagent.find_root_session_id(root_id, max_days_back=max_days_back, sessions_dir=s_dir)

    descendants = subagent.descendant_session_ids(index, effective_root)
    if not descendants:
        descendants = (effective_root,)

    warnings: list[str] = []
    sessions_obs: dict[str, SessionObservation] = {}
    events: list[ObservedEvent] = []

    # Map of (work_id, actor_id, event, schema) -> existing declared event to deduplicate mirrored prompt/user_msg
    seen_declarations: dict[tuple[Any, ...], ObservedEvent] = {}

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
        sess_start = link.started_at if link else None
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

        # Iterate records in the rollout file
        for rec in subagent.iter_rollout_records(rollout_file):
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
                # If this session has a parent, record actor_started
                if sess_parent:
                    ev_id = make_event_id("rollout_meta", file_path_str, rec.line_number, "session_meta", sid, actor_id=sid)
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
                        summary=f"Subagent {sess_name} started",
                        evidence=(ev_ref,),
                    ))
                else:
                    ev_id = make_event_id("rollout_meta", file_path_str, rec.line_number, "session_meta", sid)
                    events.append(ObservedEvent(
                        id=ev_id,
                        root_id=effective_root,
                        session_id=sid,
                        kind="session_seen",
                        observed_at=ts,
                        summary=f"Session {sess_name} seen",
                        evidence=(ev_ref,),
                    ))

            elif rec.entry_type == "event_msg" and isinstance(rec.payload, dict):
                p_type = rec.payload.get("type")
                if p_type == "item_completed":
                    item = rec.payload.get("item")
                    if isinstance(item, dict):
                        item_type = item.get("type")
                        if item_type == "CollabAgentToolCall" and item.get("tool") == "spawn_agent":
                            receivers = item.get("receiver_agents") or []
                            prompt = str(item.get("prompt") or "")
                            states = item.get("agents_states") or {}

                            decls = parse_workflow_declarations(prompt, warnings=warnings)
                            for rx in receivers:
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

                                ev_spawn_id = make_event_id("rollout_event", file_path_str, rec.line_number, "spawn_agent", sid, actor_id=rx_id, discriminator="spawn")
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
                                    summary=f"Spawned subagent {rx_name}",
                                    evidence=(ev_ref,),
                                ))

                                disp_attrs: dict[str, Any] = {"prompt": prompt}
                                if decls:
                                    disp_attrs["declaration"] = decls[0].to_dict()

                                ev_disp_id = make_event_id("rollout_event", file_path_str, rec.line_number, "spawn_agent", sid, actor_id=rx_id, discriminator="dispatch")
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
                                    summary=f"Dispatch sent to {rx_name}",
                                    attributes=disp_attrs,
                                    evidence=(ev_ref,),
                                ))

                                # Check exact declarations in spawn prompt
                                for d in decls:
                                    decl_key = (d.schema, d.event, d.work_id, rx_id or d.actor_id)
                                    decl_ev_ref = EvidenceRef(
                                        source="rollout_event",
                                        source_id=f"{sid}:{rec.line_number}:decl",
                                        raw_kind="workflow_declared",
                                        observed_at=ts,
                                        evidence_class="declared",
                                        file_path=file_path_str,
                                        line_number=rec.line_number,
                                    )
                                    decl_ev_id = make_event_id("rollout_event", file_path_str, rec.line_number, "workflow_declared", sid, actor_id=rx_id, discriminator="decl")
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
                                        summary=f"Workflow declared: {d.event} ({d.work_id or d.title or ''})",
                                        attributes={"declaration": d.to_dict()},
                                        evidence=(decl_ev_ref,),
                                    )
                                    seen_declarations[decl_key] = decl_event
                                    events.append(decl_event)

                                # Check typed incident in agents_states
                                st_entry = states.get(rx_id)
                                if isinstance(st_entry, dict) and (st_entry.get("error") or st_entry.get("failed")):
                                    inc_id = make_event_id("rollout_event", file_path_str, rec.line_number, "incident", sid, actor_id=rx_id)
                                    err_detail = str(st_entry.get("error") or st_entry.get("failed") or "Agent error state")
                                    events.append(ObservedEvent(
                                        id=inc_id,
                                        root_id=effective_root,
                                        session_id=sid,
                                        kind="incident_seen",
                                        observed_at=ts,
                                        actor_id=rx_id,
                                        actor_name=rx_name,
                                        summary=f"Agent error observed: {err_detail}",
                                        attributes={"error": err_detail},
                                        evidence=(ev_ref,),
                                    ))

                        elif include_tool_calls and item_type in ("function_call", "custom_tool_call"):
                            t_name = str(item.get("name") or item.get("tool") or "tool")
                            ev_t_id = make_event_id("rollout_event", file_path_str, rec.line_number, "tool_started", sid, discriminator=t_name)
                            events.append(ObservedEvent(
                                id=ev_t_id,
                                root_id=effective_root,
                                session_id=sid,
                                kind="tool_started",
                                observed_at=ts,
                                actor_id=sid,
                                actor_name=sess_name,
                                summary=f"Tool call: {t_name}",
                                attributes={"tool_name": t_name},
                                evidence=(ev_ref,),
                            ))

                elif p_type == "user_message":
                    msg_text = str(rec.payload.get("message") or rec.payload.get("content") or "")
                    ev_msg_id = make_event_id("rollout_event", file_path_str, rec.line_number, "user_message", sid)
                    events.append(ObservedEvent(
                        id=ev_msg_id,
                        root_id=effective_root,
                        session_id=sid,
                        kind="message_sent",
                        observed_at=ts,
                        actor_id=sess_parent,
                        peer_id=sid,
                        summary="Parent message sent",
                        attributes={"content": msg_text},
                        evidence=(ev_ref,),
                    ))

                    # Check mirrored declarations
                    decls = parse_workflow_declarations(msg_text, warnings=warnings)
                    for d in decls:
                        decl_key = (d.schema, d.event, d.work_id, sid or d.actor_id)
                        decl_ev_ref = EvidenceRef(
                            source="rollout_event",
                            source_id=f"{sid}:{rec.line_number}:decl",
                            raw_kind="workflow_declared",
                            observed_at=ts,
                            evidence_class="declared",
                            file_path=file_path_str,
                            line_number=rec.line_number,
                        )
                        if decl_key in seen_declarations:
                            # Merge evidence reference into existing declaration event
                            orig_event = seen_declarations[decl_key]
                            idx = events.index(orig_event)
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
                                summary=orig_event.summary,
                                attributes=orig_event.attributes,
                                evidence=orig_event.evidence + (decl_ev_ref,),
                            )
                            events[idx] = updated_event
                            seen_declarations[decl_key] = updated_event
                        else:
                            decl_ev_id = make_event_id("rollout_event", file_path_str, rec.line_number, "workflow_declared", sid, actor_id=sid, discriminator="decl")
                            decl_event = ObservedEvent(
                                id=decl_ev_id,
                                root_id=effective_root,
                                session_id=sid,
                                kind="workflow_declared",
                                observed_at=ts,
                                actor_id=sid,
                                actor_name=sess_name,
                                summary=f"Workflow declared: {d.event} ({d.work_id or d.title or ''})",
                                attributes={"declaration": d.to_dict()},
                                evidence=(decl_ev_ref,),
                            )
                            seen_declarations[decl_key] = decl_event
                            events.append(decl_event)

                elif p_type == "agent_message":
                    msg_text = str(rec.payload.get("message") or rec.payload.get("content") or "")
                    ev_msg_id = make_event_id("rollout_event", file_path_str, rec.line_number, "agent_message", sid)
                    events.append(ObservedEvent(
                        id=ev_msg_id,
                        root_id=effective_root,
                        session_id=sid,
                        kind="message_sent",
                        observed_at=ts,
                        actor_id=sid,
                        actor_name=sess_name,
                        peer_id=sess_parent,
                        summary="Agent message sent",
                        attributes={"content": msg_text},
                        evidence=(ev_ref,),
                    ))

                    # Check declarations in agent messages
                    decls = parse_workflow_declarations(msg_text, warnings=warnings)
                    for d in decls:
                        decl_key = (d.schema, d.event, d.work_id, sid or d.actor_id)
                        decl_ev_ref = EvidenceRef(
                            source="rollout_event",
                            source_id=f"{sid}:{rec.line_number}:decl",
                            raw_kind="workflow_declared",
                            observed_at=ts,
                            evidence_class="declared",
                            file_path=file_path_str,
                            line_number=rec.line_number,
                        )
                        decl_ev_id = make_event_id("rollout_event", file_path_str, rec.line_number, "workflow_declared", sid, actor_id=sid, discriminator="decl")
                        decl_event = ObservedEvent(
                            id=decl_ev_id,
                            root_id=effective_root,
                            session_id=sid,
                            kind="workflow_declared",
                            observed_at=ts,
                            actor_id=sid,
                            actor_name=sess_name,
                            summary=f"Workflow declared: {d.event} ({d.work_id or d.title or ''})",
                            attributes={"declaration": d.to_dict()},
                            evidence=(decl_ev_ref,),
                        )
                        seen_declarations[decl_key] = decl_event
                        events.append(decl_event)
                elif p_type == "task_complete":
                    sum_text = str(rec.payload.get("summary") or "Task complete")
                    ev_ret_id = make_event_id("rollout_event", file_path_str, rec.line_number, "task_complete", sid)
                    events.append(ObservedEvent(
                        id=ev_ret_id,
                        root_id=effective_root,
                        session_id=sid,
                        kind="actor_returned",
                        observed_at=ts,
                        actor_id=sid,
                        actor_name=sess_name,
                        peer_id=sess_parent,
                        summary=f"Task returned: {sum_text}",
                        attributes={"summary": sum_text},
                        evidence=(ev_ref,),
                    ))

    # Read hook traces for descendant sessions
    try:
        from . import trace
        for sid in list(sessions_obs.keys()):
            trace_events = trace.read(sid)
            for idx, tev in enumerate(trace_events, 1):
                tev_type = tev.get("event")
                ts_str = tev.get("ts")
                ts = subagent.parse_datetime(ts_str) or cap_time
                agent_label = tev.get("agent") or tev.get("persona") or sid[:8]
                t_file = trace._file(sid)
                t_file_str = str(t_file) if t_file else None

                ts = _ensure_utc(subagent.parse_datetime(ts_str)) if ts_str else cap_time
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
                    t_ev_id = make_event_id("hook_trace", t_file_str, idx, "subagent_start", sid, actor_id=sid)
                    events.append(ObservedEvent(
                        id=t_ev_id,
                        root_id=effective_root,
                        session_id=sid,
                        kind="actor_started",
                        observed_at=ts,
                        actor_id=sid,
                        actor_name=agent_label,
                        summary=f"Hook observed subagent_start: {agent_label}",
                        evidence=(t_ref,),
                    ))
                elif tev_type == "subagent_stop":
                    t_ev_id = make_event_id("hook_trace", t_file_str, idx, "subagent_stop", sid, actor_id=sid)
                    events.append(ObservedEvent(
                        id=t_ev_id,
                        root_id=effective_root,
                        session_id=sid,
                        kind="actor_stopped",
                        observed_at=ts,
                        actor_id=sid,
                        actor_name=agent_label,
                        summary=f"Hook observed subagent_stop: {agent_label}",
                        evidence=(t_ref,),
                    ))
    except Exception:
        pass

    # Read inflight records for descendant sessions
    try:
        from . import inflight
        for sid in list(sessions_obs.keys()):
            in_recs = inflight.live_records(session_id=sid)
            for irec in in_recs:
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
                events.append(ObservedEvent(
                    id=in_ev_id,
                    root_id=effective_root,
                    session_id=sid,
                    kind="actor_started",
                    observed_at=start_dt,
                    actor_id=irec.get("agent_id") or f"inflight-{token}",
                    actor_name=agent_name,
                    summary=f"Inflight subagent active: {agent_name}",
                    evidence=(in_ref,),
                ))
    except Exception:
        pass

    # Sort events deterministically by observed_at and stable ID
    events.sort(key=lambda e: (e.observed_at, e.id))

    return ObservationSnapshot(
        root_id=effective_root,
        captured_at=cap_time,
        sessions=sessions_obs,
        events=tuple(events),
        warnings=tuple(warnings),
    )
