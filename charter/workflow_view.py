"""Deterministic workflow projection layer, state reduction, and safe renderers.

Projects raw observation snapshots into workflow views (positions, obligations,
actors, timeline, and evidence explanations) without authorizing transitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Sequence

from . import tui
from .observations import (
    EvidenceClass,
    EvidenceRef,
    ObservationSnapshot,
    ObservedEvent,
    WorkflowDeclaration,
)
from .subagent import RuntimeState

WorkPhase = Literal[
    "dispatched",
    "active",
    "returned",
    "intaken",
    "resolved",
    "unknown",
]
ObligationKind = Literal["return_expected", "intake_required"]
RelationKind = Literal["spawn_parent", "peer", "reports_to", "owner"]


@dataclass(frozen=True)
class IncidentProjection:
    """Observed abnormal or error evidence."""

    id: str
    work_id: str | None
    actor_id: str | None
    kind: str
    summary: str
    observed_at: datetime
    basis: tuple[EvidenceRef, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "work_id": self.work_id,
            "actor_id": self.actor_id,
            "kind": self.kind,
            "summary": self.summary,
            "observed_at": self.observed_at.isoformat(),
            "basis": [b.to_dict() for b in self.basis],
        }


@dataclass(frozen=True)
class ObligationProjection:
    """An open expectation for an actor to return or intake work."""

    id: str
    kind: ObligationKind
    work_id: str
    owner_actor_id: str | None
    owner_label: str
    counterparty_actor_id: str | None
    counterparty_label: str
    opened_at: datetime
    basis: tuple[EvidenceRef, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "work_id": self.work_id,
            "owner_actor_id": self.owner_actor_id,
            "owner_label": self.owner_label,
            "counterparty_actor_id": self.counterparty_actor_id,
            "counterparty_label": self.counterparty_label,
            "opened_at": self.opened_at.isoformat(),
            "basis": [b.to_dict() for b in self.basis],
        }


@dataclass(frozen=True)
class WorkItemProjection:
    """Lifecycle projection for a single assignment."""

    id: str
    external_id: str | None
    root_id: str
    title: str
    direction: str | None
    actor_id: str | None
    actor_name: str | None
    actor_role: str | None
    coordinator_actor_id: str | None
    coordinator_name: str | None
    declared_owner_role: str | None
    runtime_state: RuntimeState
    phase: WorkPhase
    last_observed_at: datetime
    return_observed: bool
    obligations: tuple[ObligationProjection, ...]
    incidents: tuple[IncidentProjection, ...]
    basis: tuple[EvidenceRef, ...]

    @property
    def display_label(self) -> str:
        return self.external_id or self.id

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "external_id": self.external_id,
            "root_id": self.root_id,
            "title": self.title,
            "direction": self.direction,
            "actor_id": self.actor_id,
            "actor_name": self.actor_name,
            "actor_role": self.actor_role,
            "coordinator_actor_id": self.coordinator_actor_id,
            "coordinator_name": self.coordinator_name,
            "declared_owner_role": self.declared_owner_role,
            "runtime_state": self.runtime_state,
            "phase": self.phase,
            "last_observed_at": self.last_observed_at.isoformat(),
            "return_observed": self.return_observed,
            "obligations": [o.to_dict() for o in self.obligations],
            "incidents": [i.to_dict() for i in self.incidents],
            "basis": [b.to_dict() for b in self.basis],
        }


@dataclass(frozen=True)
class ActorProjection:
    """An actor participant in the workflow."""

    id: str
    name: str
    runtime_parent_id: str | None
    declared_role: str | None
    declared_direction: str | None
    runtime_state: RuntimeState
    basis: tuple[EvidenceRef, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "runtime_parent_id": self.runtime_parent_id,
            "declared_role": self.declared_role,
            "declared_direction": self.declared_direction,
            "runtime_state": self.runtime_state,
            "basis": [b.to_dict() for b in self.basis],
        }


@dataclass(frozen=True)
class RelationProjection:
    """A relationship between actors (runtime spawn parent or declared peer/reports_to/owner)."""

    id: str
    source_actor_id: str
    target_actor_id: str
    kind: RelationKind
    declared: bool
    basis: tuple[EvidenceRef, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_actor_id": self.source_actor_id,
            "target_actor_id": self.target_actor_id,
            "kind": self.kind,
            "declared": self.declared,
            "basis": [b.to_dict() for b in self.basis],
        }


@dataclass(frozen=True)
class WorkflowProjection:
    """Root projection container computed deterministically from an ObservationSnapshot."""

    root_id: str
    captured_at: datetime
    actors: tuple[ActorProjection, ...]
    relations: tuple[RelationProjection, ...]
    work_items: tuple[WorkItemProjection, ...]
    open_obligations: tuple[ObligationProjection, ...]
    incidents: tuple[IncidentProjection, ...]
    unbound_events: tuple[ObservedEvent, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_id": self.root_id,
            "captured_at": self.captured_at.isoformat(),
            "actors": [a.to_dict() for a in self.actors],
            "relations": [r.to_dict() for r in self.relations],
            "work_items": [w.to_dict() for w in self.work_items],
            "open_obligations": [o.to_dict() for o in self.open_obligations],
            "incidents": [i.to_dict() for i in self.incidents],
            "unbound_events": [e.to_dict() for e in self.unbound_events],
        }


class _MutableWorkItem:
    def __init__(
        self,
        id: str,
        external_id: str | None,
        root_id: str,
        title: str,
        direction: str | None,
        actor_id: str | None,
        actor_name: str | None,
        actor_role: str | None,
        coordinator_actor_id: str | None,
        coordinator_name: str | None,
        declared_owner_role: str | None,
        runtime_state: RuntimeState,
        phase: WorkPhase,
        last_observed_at: datetime,
        return_observed: bool,
        basis: list[EvidenceRef],
    ):
        self.id = id
        self.external_id = external_id
        self.root_id = root_id
        self.title = title
        self.direction = direction
        self.actor_id = actor_id
        self.actor_name = actor_name
        self.actor_role = actor_role
        self.coordinator_actor_id = coordinator_actor_id
        self.coordinator_name = coordinator_name
        self.declared_owner_role = declared_owner_role
        self.runtime_state = runtime_state
        self.phase = phase
        self.dispatched_at = last_observed_at
        self.last_observed_at = last_observed_at
        self.return_observed = return_observed
        self.obligations: list[ObligationProjection] = []
        self.incidents: list[IncidentProjection] = []
        self.basis = list(basis)
    def freeze(self) -> WorkItemProjection:
        return WorkItemProjection(
            id=self.id,
            external_id=self.external_id,
            root_id=self.root_id,
            title=self.title,
            direction=self.direction,
            actor_id=self.actor_id,
            actor_name=self.actor_name,
            actor_role=self.actor_role,
            coordinator_actor_id=self.coordinator_actor_id,
            coordinator_name=self.coordinator_name,
            declared_owner_role=self.declared_owner_role,
            runtime_state=self.runtime_state,
            phase=self.phase,
            last_observed_at=self.last_observed_at,
            return_observed=self.return_observed,
            obligations=tuple(self.obligations),
            incidents=tuple(self.incidents),
            basis=tuple(self.basis),
        )


def _mark_unbound(ev: ObservedEvent, reason: str) -> ObservedEvent:
    attrs = dict(ev.attributes)
    attrs["unbound_reason"] = reason
    return ObservedEvent(
        id=ev.id,
        root_id=ev.root_id,
        session_id=ev.session_id,
        kind=ev.kind,
        observed_at=ev.observed_at,
        actor_id=ev.actor_id,
        actor_name=ev.actor_name,
        peer_id=ev.peer_id,
        peer_name=ev.peer_name,
        author_actor_id=ev.author_actor_id,
        summary=ev.summary,
        attributes=attrs,
        evidence=ev.evidence,
        ordinal=ev.ordinal,
    )


def project_workflow(snapshot: ObservationSnapshot) -> WorkflowProjection:
    """Project an immutable ObservationSnapshot into deterministic WorkflowProjection views."""
    root_id = snapshot.root_id
    cap_time = snapshot.captured_at

    actors_map: dict[str, dict[str, Any]] = {}
    relations_list: list[RelationProjection] = []
    work_items_map: dict[str, _MutableWorkItem] = {}
    incidents_list: list[IncidentProjection] = []
    unbound_events: list[ObservedEvent] = []

    # Map of child_session_id -> list of work item IDs dispatched to that child
    child_dispatches: dict[str, list[str]] = {}

    # Initialize actors from snapshot sessions
    for sid, s_obs in snapshot.sessions.items():
        actors_map[sid] = {
            "id": sid,
            "name": s_obs.name,
            "runtime_parent_id": s_obs.parent_id,
            "declared_role": None,
            "declared_direction": None,
            "runtime_state": "running" if not s_obs.parent_id else "unknown",
            "basis": list(s_obs.basis),
        }
        if s_obs.parent_id:
            rel_id = f"rel:{s_obs.parent_id}:{sid}:spawn"
            relations_list.append(RelationProjection(
                id=rel_id,
                source_actor_id=s_obs.parent_id,
                target_actor_id=sid,
                kind="spawn_parent",
                declared=False,
                basis=s_obs.basis,
            ))

    # Also include any inflight-only or event-mentioned actors not yet in snapshot.sessions
    for ev in snapshot.events:
        act_id = ev.actor_id
        if act_id and act_id not in actors_map:
            act_name = ev.actor_name or act_id[:8]
            actors_map[act_id] = {
                "id": act_id,
                "name": act_name,
                "runtime_parent_id": ev.peer_id or snapshot.root_id,
                "declared_role": None,
                "declared_direction": None,
                "runtime_state": "running",
                "basis": list(ev.evidence),
            }

    # Pre-index declared metadata
    declarations_by_work_id: dict[str, WorkflowDeclaration] = {}
    declared_relations: list[tuple[WorkflowDeclaration, ObservedEvent]] = []

    for ev in snapshot.events:
        if ev.kind == "workflow_declared":
            decl_dict = ev.attributes.get("declaration")
            if isinstance(decl_dict, dict):
                decl = WorkflowDeclaration(**decl_dict)
                if decl.work_id:
                    declarations_by_work_id[decl.work_id] = decl
                if decl.event == "relation":
                    declared_relations.append((decl, ev))

    # Process all declared relations (peer, reports_to, owner)
    for decl, ev in declared_relations:
        src = decl.actor_id or ev.author_actor_id or ev.session_id
        tgt = decl.related_actor_id
        rel_kind = decl.relation
        if src and tgt and rel_kind:
            rel_id = f"rel:{src}:{tgt}:{rel_kind}"
            relations_list.append(RelationProjection(
                id=rel_id,
                source_actor_id=src,
                target_actor_id=tgt,
                kind=rel_kind,
                declared=True,
                basis=ev.evidence,
            ))
    # Main reduction loop over ordered events
    for ev in snapshot.events:
        ts = ev.observed_at

        if ev.kind == "actor_spawned":
            pass

        elif ev.kind == "dispatch_sent":
            rx_id = ev.actor_id or ""
            rx_name = ev.actor_name or (snapshot.sessions[rx_id].name if rx_id in snapshot.sessions else rx_id[:8])
            coord_id = ev.author_actor_id or ev.peer_id or ev.session_id
            coord_name = snapshot.sessions[coord_id].name if coord_id in snapshot.sessions else (coord_id[:8] if coord_id else "Operational Root")

            ev_hash = ev.id[:16]
            rx_hash = rx_id[:8] if rx_id else "subagent"
            internal_work_id = f"work:{ev_hash}:{rx_hash}"

            # Check prompt for declaration - strictly bound to this exact dispatch event
            decl_dict = ev.attributes.get("declaration")
            decl: WorkflowDeclaration | None = None
            if isinstance(decl_dict, dict):
                decl = WorkflowDeclaration(**decl_dict)
            elif ev.attributes.get("prompt"):
                from .observations import parse_workflow_declarations
                p_decls = parse_workflow_declarations(str(ev.attributes["prompt"]))
                if p_decls:
                    decl = p_decls[0]
            ext_id = decl.work_id if decl else None
            title = (decl.title if decl else None) or ev.summary or f"Dispatch to {rx_name}"
            direction = decl.direction if decl else None
            actor_role = decl.actor_role if decl else None
            owner_role = decl.owner_role if decl else None

            # Update actor record with declared role/direction
            if rx_id in actors_map:
                if actor_role:
                    actors_map[rx_id]["declared_role"] = actor_role
                if direction:
                    actors_map[rx_id]["declared_direction"] = direction
                actors_map[rx_id]["runtime_state"] = "starting"
                actors_map[rx_id]["basis"].extend(ev.evidence)

            m_item = _MutableWorkItem(
                id=internal_work_id,
                external_id=ext_id,
                root_id=root_id,
                title=title,
                direction=direction,
                actor_id=rx_id,
                actor_name=rx_name,
                actor_role=actor_role,
                coordinator_actor_id=coord_id,
                coordinator_name=coord_name,
                declared_owner_role=owner_role,
                runtime_state="starting",
                phase="dispatched",
                last_observed_at=ts,
                return_observed=False,
                basis=list(ev.evidence),
            )

            # Open return_expected obligation
            obl_id = f"obl:{internal_work_id}:return_expected"
            obl_basis = (EvidenceRef(
                source=ev.evidence[0].source if ev.evidence else "rollout_event",
                source_id=ev.id,
                raw_kind="spawn_agent",
                observed_at=ts,
                evidence_class="derived",
                file_path=ev.evidence[0].file_path if ev.evidence else None,
                line_number=ev.evidence[0].line_number if ev.evidence else None,
            ),)
            obl = ObligationProjection(
                id=obl_id,
                kind="return_expected",
                work_id=internal_work_id,
                owner_actor_id=rx_id,
                owner_label=rx_name,
                counterparty_actor_id=coord_id,
                counterparty_label=coord_name or "Operational Root",
                opened_at=ts,
                basis=obl_basis,
            )
            m_item.obligations.append(obl)

            work_items_map[internal_work_id] = m_item
            child_dispatches.setdefault(rx_id, []).append(internal_work_id)

        elif ev.kind in ("actor_started", "tool_started", "tool_finished"):
            target_actor = ev.actor_id or ev.session_id
            if target_actor in actors_map:
                actors_map[target_actor]["runtime_state"] = "running"
                actors_map[target_actor]["basis"].extend(ev.evidence)

            # Bind strictly to the single current assignment (earliest still-open work item)
            open_wids = [
                wid for wid in child_dispatches.get(target_actor, [])
                if work_items_map[wid].phase in ("dispatched", "active")
                and ts >= work_items_map[wid].dispatched_at
            ]
            if open_wids:
                wi = work_items_map[open_wids[0]]
                if wi.phase == "dispatched":
                    wi.phase = "active"
                wi.runtime_state = "running"
                wi.last_observed_at = ts
                wi.basis.extend(ev.evidence)
                wi.basis.append(EvidenceRef(
                    source=ev.evidence[0].source if ev.evidence else "rollout_event",
                    source_id=ev.id,
                    raw_kind=ev.kind,
                    observed_at=ts,
                    evidence_class="mechanical",
                ))
        elif ev.kind == "actor_stopped":
            target_actor = ev.actor_id or ev.session_id
            if target_actor in actors_map:
                actors_map[target_actor]["runtime_state"] = "stopped"
                actors_map[target_actor]["basis"].extend(ev.evidence)

            open_wids = [
                wid for wid in child_dispatches.get(target_actor, [])
                if work_items_map[wid].phase in ("dispatched", "active")
                and ts >= work_items_map[wid].dispatched_at
            ]
            if open_wids:
                wi = work_items_map[open_wids[0]]
                wi.runtime_state = "stopped"
                wi.return_observed = False
                wi.last_observed_at = ts
                wi.basis.extend(ev.evidence)
                wi.basis.append(EvidenceRef(
                    source=ev.evidence[0].source if ev.evidence else "rollout_event",
                    source_id=ev.id,
                    raw_kind=ev.kind,
                    observed_at=ts,
                    evidence_class="mechanical",
                ))
        elif ev.kind == "actor_returned":
            target_child = ev.actor_id or ev.session_id
            if target_child in actors_map:
                actors_map[target_child]["runtime_state"] = "stopped"
                actors_map[target_child]["basis"].extend(ev.evidence)

            # In sequential assignments to the same child, bind to the earliest active/dispatched work item
            candidate_ids = [
                wid for wid in child_dispatches.get(target_child, [])
                if work_items_map[wid].phase in ("dispatched", "active")
                and ts >= work_items_map[wid].dispatched_at
            ]
            if candidate_ids:
                wid = candidate_ids[0]
                wi = work_items_map[wid]
                wi.phase = "returned"
                wi.runtime_state = "stopped"
                wi.return_observed = True
                wi.last_observed_at = ts
                wi.basis.extend(ev.evidence)
                wi.basis.append(EvidenceRef(
                    source=ev.evidence[0].source if ev.evidence else "rollout_event",
                    source_id=ev.id,
                    raw_kind=ev.kind,
                    observed_at=ts,
                    evidence_class="mechanical",
                ))
                # Close return_expected obligation
                wi.obligations = [o for o in wi.obligations if o.kind != "return_expected"]

                # Open intake_required obligation
                coord_id = wi.coordinator_actor_id
                coord_name = wi.coordinator_name or (snapshot.sessions[coord_id].name if coord_id in snapshot.sessions else "Operational Root")
                rx_name = wi.actor_name or target_child[:8]
                obl_id = f"obl:{wid}:intake_required"
                obl_basis = (EvidenceRef(
                    source=ev.evidence[0].source if ev.evidence else "rollout_event",
                    source_id=ev.id,
                    raw_kind="task_complete",
                    observed_at=ts,
                    evidence_class="derived",
                    file_path=ev.evidence[0].file_path if ev.evidence else None,
                    line_number=ev.evidence[0].line_number if ev.evidence else None,
                ),)
                intake_obl = ObligationProjection(
                    id=obl_id,
                    kind="intake_required",
                    work_id=wid,
                    owner_actor_id=coord_id,
                    owner_label=coord_name or "Operational Root",
                    counterparty_actor_id=target_child,
                    counterparty_label=rx_name,
                    opened_at=ts,
                    basis=obl_basis,
                )
                wi.obligations.append(intake_obl)
            else:
                unbound_events.append(_mark_unbound(ev, "no_matching_active_dispatch_for_child"))
        elif ev.kind == "incident_seen":
            bound_wid: str | None = None
            target_actor = ev.actor_id or ev.session_id
            if target_actor in child_dispatches:
                for cand_wid in child_dispatches[target_actor]:
                    cand_wi = work_items_map[cand_wid]
                    if cand_wi.phase in ("dispatched", "active") and ts >= cand_wi.dispatched_at:
                        bound_wid = cand_wid
                        break

            inc_id = f"inc:{ev.id[:16]}"
            inc_proj = IncidentProjection(
                id=inc_id,
                work_id=bound_wid,
                actor_id=target_actor,
                kind=str(ev.attributes.get("error") or "incident"),
                summary=ev.summary,
                observed_at=ts,
                basis=ev.evidence,
            )
            incidents_list.append(inc_proj)
            if bound_wid and bound_wid in work_items_map:
                work_items_map[bound_wid].incidents.append(inc_proj)
                work_items_map[bound_wid].basis.extend(ev.evidence)
        elif ev.kind == "workflow_declared":
            decl_dict = ev.attributes.get("declaration")
            if isinstance(decl_dict, dict):
                decl = WorkflowDeclaration(**decl_dict)
                author = ev.author_actor_id or ev.session_id

                if decl.event == "intake":
                    # Match work item by external_id or internal id
                    matched_items = [
                        wi for wi in work_items_map.values()
                        if (decl.work_id and wi.external_id == decl.work_id) or (decl.work_id and wi.id == decl.work_id)
                    ]

                    if len(matched_items) == 1:
                        matched_item = matched_items[0]
                        # Authority check: author must be the mechanically observed dispatching coordinator
                        # Phase check: work item must be in 'returned' phase!
                        is_coordinator = author == matched_item.coordinator_actor_id
                        if is_coordinator and matched_item.phase == "returned":
                            matched_item.phase = "intaken"
                            matched_item.last_observed_at = ts
                            matched_item.basis.extend(ev.evidence)
                            matched_item.obligations = [o for o in matched_item.obligations if o.kind != "intake_required"]
                        elif not is_coordinator:
                            unbound_events.append(_mark_unbound(ev, f"author_not_dispatching_coordinator: author '{author}' != coordinator '{matched_item.coordinator_actor_id}'"))
                        else:
                            unbound_events.append(_mark_unbound(ev, f"invalid_phase_for_intake: work phase is '{matched_item.phase}', expected 'returned'"))
                    elif len(matched_items) > 1:
                        unbound_events.append(_mark_unbound(ev, f"ambiguous_duplicate_work_id: '{decl.work_id}' matches {len(matched_items)} items"))
                    else:
                        unbound_events.append(_mark_unbound(ev, f"work_item_not_found: '{decl.work_id}'"))

                elif decl.event == "resolve":
                    matched_items = [
                        wi for wi in work_items_map.values()
                        if (decl.work_id and wi.external_id == decl.work_id) or (decl.work_id and wi.id == decl.work_id)
                    ]

                    if len(matched_items) == 1:
                        matched_item = matched_items[0]
                        is_coordinator = author == matched_item.coordinator_actor_id
                        # Only coordinator can resolve, and only after intake!
                        if is_coordinator and matched_item.phase == "intaken":
                            matched_item.phase = "resolved"
                            matched_item.last_observed_at = ts
                            matched_item.basis.extend(ev.evidence)
                        elif not is_coordinator:
                            unbound_events.append(_mark_unbound(ev, f"author_not_dispatching_coordinator: author '{author}' != coordinator '{matched_item.coordinator_actor_id}'"))
                        else:
                            unbound_events.append(_mark_unbound(ev, f"invalid_phase_for_resolve: work phase is '{matched_item.phase}', expected 'intaken'"))
                    elif len(matched_items) > 1:
                        unbound_events.append(_mark_unbound(ev, f"ambiguous_duplicate_work_id: '{decl.work_id}' matches {len(matched_items)} items"))
                    else:
                        unbound_events.append(_mark_unbound(ev, f"work_item_not_found: '{decl.work_id}'"))

    # Freeze actors
    frozen_actors = tuple(
        ActorProjection(
            id=a["id"],
            name=a["name"],
            runtime_parent_id=a["runtime_parent_id"],
            declared_role=a["declared_role"],
            declared_direction=a["declared_direction"],
            runtime_state=a["runtime_state"],
            basis=tuple(a["basis"]),
        )
        for a in actors_map.values()
    )

    # Freeze work items
    frozen_work_items = tuple(w.freeze() for w in work_items_map.values())

    # Collect all open obligations sorted: intake_required first, then return_expected, oldest first
    all_open_obls: list[ObligationProjection] = []
    for wi in frozen_work_items:
        all_open_obls.extend(wi.obligations)

    def obl_sort_key(o: ObligationProjection) -> tuple[int, float]:
        kind_prio = 0 if o.kind == "intake_required" else 1
        return (kind_prio, o.opened_at.timestamp())

    all_open_obls.sort(key=obl_sort_key)

    return WorkflowProjection(
        root_id=root_id,
        captured_at=cap_time,
        actors=frozen_actors,
        relations=tuple(relations_list),
        work_items=frozen_work_items,
        open_obligations=tuple(all_open_obls),
        incidents=tuple(incidents_list),
        unbound_events=tuple(unbound_events),
    )


def work_item_by_id(
    projection: WorkflowProjection,
    value: str,
) -> WorkItemProjection | None:
    """Find a WorkItemProjection by internal ID, external work_id, or prefix."""
    if not value:
        return None
    val_norm = value.strip()
    # 1. Exact match on external_id
    for w in projection.work_items:
        if w.external_id and w.external_id == val_norm:
            return w
    # 2. Exact match on internal id
    for w in projection.work_items:
        if w.id == val_norm:
            return w
    # 3. Prefix match on external_id or internal id
    for w in projection.work_items:
        if w.external_id and w.external_id.lower().startswith(val_norm.lower()):
            return w
        if w.id.lower().startswith(val_norm.lower()):
            return w
    return None


def explain_projection(
    projection: WorkflowProjection,
    projection_id: str,
) -> dict[str, Any] | None:
    """Explain a projection item (work item, actor, obligation, incident, or relation) with full evidence trail."""
    target_id = projection_id.strip()

    # 1. Check work items
    wi = work_item_by_id(projection, target_id)
    if wi:
        return {
            "type": "work_item",
            "id": wi.id,
            "external_id": wi.external_id,
            "title": wi.title,
            "phase": wi.phase,
            "runtime_state": wi.runtime_state,
            "actor": f"{wi.actor_name} ({wi.actor_id})" if wi.actor_id else None,
            "actor_role": wi.actor_role,
            "direction": wi.direction,
            "coordinator": f"{wi.coordinator_name} ({wi.coordinator_actor_id})" if wi.coordinator_actor_id else None,
            "return_observed": wi.return_observed,
            "obligations": [o.to_dict() for o in wi.obligations],
            "incidents": [i.to_dict() for i in wi.incidents],
            "evidence": [b.to_dict() for b in wi.basis],
        }

    # 2. Check obligations
    for obl in projection.open_obligations:
        if obl.id == target_id or obl.work_id == target_id:
            return {
                "type": "obligation",
                "id": obl.id,
                "kind": obl.kind,
                "work_id": obl.work_id,
                "owner": obl.owner_label,
                "counterparty": obl.counterparty_label,
                "opened_at": obl.opened_at.isoformat(),
                "evidence": [b.to_dict() for b in obl.basis],
            }

    # 3. Check incidents
    for inc in projection.incidents:
        if inc.id == target_id:
            return {
                "type": "incident",
                "id": inc.id,
                "kind": inc.kind,
                "summary": inc.summary,
                "observed_at": inc.observed_at.isoformat(),
                "evidence": [b.to_dict() for b in inc.basis],
            }

    # 4. Check relations
    for r in projection.relations:
        if r.id == target_id or target_id in (r.id, f"{r.source_actor_id}:{r.target_actor_id}"):
            return {
                "type": "relation",
                "id": r.id,
                "kind": r.kind,
                "source": r.source_actor_id,
                "target": r.target_actor_id,
                "declared": r.declared,
                "evidence": [b.to_dict() for b in r.basis],
            }

    # 5. Check actors
    for a in projection.actors:
        if a.id == target_id or a.name.lower() == target_id.lower():
            return {
                "type": "actor",
                "id": a.id,
                "name": a.name,
                "role": a.declared_role,
                "direction": a.declared_direction,
                "runtime_state": a.runtime_state,
                "parent_id": a.runtime_parent_id,
                "evidence": [b.to_dict() for b in a.basis],
            }

    return None


def _format_age(dt: datetime, now_dt: datetime) -> str:
    sec = max(0.0, (now_dt - dt).total_seconds())
    if sec < 60:
        return f"{int(sec)}s"
    mins = int(sec / 60)
    if mins < 60:
        return f"{mins}m"
    hours = int(mins / 60)
    if hours < 24:
        return f"{hours}h"
    return f"{int(hours / 24)}d"


def render_position_table(
    projection: WorkflowProjection,
    *,
    width: int = 120,
    color: bool = False,
    now_dt: datetime | None = None,
) -> list[str]:
    """Render the workflow position table."""
    now = now_dt or datetime.now(timezone.utc)
    if not projection.work_items:
        return ["  (no workflow assignments observed)"]

    hdr = f"{'DIRECTION':<10} {'WORK':<16} {'ACTOR/ROLE':<16} {'RUNTIME':<9} {'PHASE':<10} {'NEXT OBLIGATION':<26} {'AGE':>5}"
    lines = [tui.truncate(hdr, width)]

    for w in projection.work_items:
        dir_str = w.direction or "—"
        work_str = w.display_label
        actor_str = w.actor_name or (w.actor_id[:8] if w.actor_id else "—")
        if w.actor_role:
            actor_str = f"{actor_str} / {w.actor_role}"

        rt_str = w.runtime_state
        phase_str = w.phase

        next_str = "—"
        if w.obligations:
            top_obl = w.obligations[0]
            if top_obl.kind == "intake_required":
                next_str = f"{top_obl.owner_label} intake required"
            elif top_obl.kind == "return_expected":
                next_str = f"{top_obl.owner_label} return expected"
        elif w.phase == "active" and not w.return_observed:
            next_str = f"{actor_str} return expected"

        age_str = _format_age(w.last_observed_at, now)

        row = f"{dir_str:<10} {work_str:<16} {actor_str:<16} {rt_str:<9} {phase_str:<10} {next_str:<26} {age_str:>5}"
        lines.append(tui.truncate(row, width))

        if w.runtime_state == "stopped" and not w.return_observed and w.phase in ("dispatched", "active"):
            sub_note = " " * 44 + "↳ return not observed"
            lines.append(tui.truncate(sub_note, width))

    return lines


def render_obligations(
    projection: WorkflowProjection,
    *,
    width: int = 120,
    color: bool = False,
    now_dt: datetime | None = None,
) -> list[str]:
    """Render the workflow obligations table."""
    now = now_dt or datetime.now(timezone.utc)
    if not projection.open_obligations:
        return ["  (no open obligations)"]

    hdr = f"{'OWNER':<18} {'OWES / EXPECTS':<30} {'WORK':<16} {'OPENED':>6} {'BASIS':<12}"
    lines = [tui.truncate(hdr, width)]

    for o in projection.open_obligations:
        owner = o.owner_label
        if o.kind == "intake_required":
            owes = f"intake from {o.counterparty_label}"
        else:
            owes = f"return to {o.counterparty_label}"
        work_label = o.work_id
        for wi in projection.work_items:
            if wi.id == o.work_id:
                work_label = wi.display_label
                break

        opened_age = _format_age(o.opened_at, now)
        raw_basis_kind = o.basis[0].raw_kind if o.basis else "derived"
        basis_str = f"[DER] {raw_basis_kind}"

        row = f"{owner:<18} {owes:<30} {work_label:<16} {opened_age:>6} {basis_str:<12}"
        lines.append(tui.truncate(row, width))

    return lines


def render_actor_view(
    projection: WorkflowProjection,
    *,
    width: int = 120,
    color: bool = False,
    runtime_tree_only: bool = False,
    declared_relations_only: bool = False,
) -> list[str]:
    """Render actors: declared workflow structure side-by-side with runtime topology."""
    actors_by_id = {a.id: a for a in projection.actors}

    # Build runtime tree lines
    runtime_lines = ["RUNTIME TOPOLOGY", f"Root session ({projection.root_id[:8]})"]
    children_map: dict[str, list[str]] = {}
    for r in projection.relations:
        if not r.declared and r.kind == "spawn_parent":
            children_map.setdefault(r.source_actor_id, []).append(r.target_actor_id)

    def walk_rt(actor_id: str, prefix: str) -> None:
        cids = children_map.get(actor_id, [])
        for idx, cid in enumerate(cids):
            is_last = idx == len(cids) - 1
            branch = "└─ " if is_last else "├─ "
            child_prefix = prefix + ("   " if is_last else "│  ")
            name = actors_by_id[cid].name if cid in actors_by_id else cid[:8]
            runtime_lines.append(f"{prefix}{branch}{name}")
            walk_rt(cid, child_prefix)

    walk_rt(projection.root_id, "")

    if runtime_tree_only:
        return [tui.truncate(ln, width) for ln in runtime_lines]

    has_declarations = any(a.declared_role or a.declared_direction for a in projection.actors) or any(r.declared for r in projection.relations)
    if not has_declarations and not declared_relations_only:
        return [tui.truncate(ln, width) for ln in runtime_lines]

    # Build declared workflow lines
    declared_lines = ["DECLARED WORKFLOW", "Portfolio", "└─ Operational Root"]
    declared_by_dir: dict[str, list[ActorProjection]] = {}
    for a in projection.actors:
        d = a.declared_direction or "Direct"
        declared_by_dir.setdefault(d, []).append(a)

    declared_edges = [
        (r.source_actor_id, r.target_actor_id, r.kind)
        for r in projection.relations
        if r.declared
    ]

    for d, d_actors in declared_by_dir.items():
        declared_lines.append(f"   ├─ {d}")
        for act in d_actors:
            role_prefix = f"{act.declared_role} " if act.declared_role else ""
            rel_tags = []
            for s, t, k in declared_edges:
                if s == act.id:
                    t_name = actors_by_id[t].name if t in actors_by_id else t[:8]
                    if k == "peer":
                        rel_tags.append(f"── peer ── {t_name}")
                    elif k == "reports_to":
                        rel_tags.append(f"── reports_to ──> {t_name}")
                    elif k == "owner":
                        rel_tags.append(f"── owner ──> {t_name}")
                elif t == act.id and k == "peer":
                    s_name = actors_by_id[s].name if s in actors_by_id else s[:8]
                    rel_tags.append(f"── peer ── {s_name}")
            if rel_tags:
                declared_lines.append(f"   │  ├─ {role_prefix}{act.name} {' '.join(rel_tags)}")
            else:
                declared_lines.append(f"   │  ├─ {role_prefix}{act.name}")

    if declared_relations_only:
        return [tui.truncate(ln, width) for ln in declared_lines]

    # Side by side composition with wide-character aware width handling
    half_w = max(30, int((width - 4) / 2))
    max_h = max(len(declared_lines), len(runtime_lines))
    while len(declared_lines) < max_h:
        declared_lines.append("")
    while len(runtime_lines) < max_h:
        runtime_lines.append("")

    combined = []
    for dl, rl in zip(declared_lines, runtime_lines):
        d_w = tui.width(dl)
        if d_w > half_w:
            d_pad = tui.truncate(dl, half_w)
            d_pad += " " * max(0, half_w - tui.width(d_pad))
        else:
            d_pad = dl + " " * (half_w - d_w)
        r_pad = tui.truncate(rl, half_w)
        combined.append(f"{d_pad}  {r_pad}")

    return [tui.truncate(ln, width) for ln in combined]


def filter_timeline_events(
    snapshot: ObservationSnapshot,
    projection: WorkflowProjection,
    *,
    work_filter: str | None = None,
    actor_filter: str | None = None,
) -> tuple[ObservedEvent, ...]:
    """Filter snapshot events for a specific work item or actor matching exact evidence references."""
    events = list(snapshot.events)
    if not work_filter and not actor_filter:
        return tuple(events)

    if actor_filter:
        act_lower = actor_filter.lower()
        events = [
            e for e in events
            if (e.actor_id and act_lower in e.actor_id.lower())
            or (e.actor_name and act_lower in e.actor_name.lower())
            or (e.author_actor_id and act_lower in e.author_actor_id.lower())
            or (e.peer_id and act_lower in e.peer_id.lower())
            or (e.peer_name and act_lower in e.peer_name.lower())
        ]

    if work_filter:
        wf_query = work_filter.strip().casefold()

        # Stage 1: Exact matches take absolute precedence
        exact_matches = [
            w for w in projection.work_items
            if (w.external_id and wf_query == w.external_id.casefold())
            or wf_query == w.id.casefold()
        ]
        if exact_matches:
            selected_work_items = exact_matches
        else:
            # Stage 2: Prefix / substring fallback only when no exact match exists
            selected_work_items = [
                w for w in projection.work_items
                if (w.external_id and wf_query in w.external_id.casefold())
                or (len(wf_query) >= 4 and wf_query != "work" and wf_query in w.id.casefold())
            ]

        matched_internal_work_ids: set[str] = set()
        matched_external_work_ids: set[str] = set()
        matched_source_ids: set[str] = set()
        matched_actor_ids: set[str] = set()

        for w in selected_work_items:
            matched_internal_work_ids.add(w.id)
            if w.external_id:
                matched_external_work_ids.add(w.external_id)
            if w.actor_id:
                matched_actor_ids.add(w.actor_id)
            for b in w.basis:
                matched_source_ids.add(b.source_id)
            for o in w.obligations:
                for ob in o.basis:
                    matched_source_ids.add(ob.source_id)
        for inc in projection.incidents:
            if inc.work_id and inc.work_id in matched_internal_work_ids:
                for b in inc.basis:
                    matched_source_ids.add(b.source_id)

        events = [
            e for e in events
            if e.id in matched_source_ids
            or any(ref.source_id in matched_source_ids for ref in e.evidence)
            or (e.attributes.get("work_id") and e.attributes.get("work_id") in matched_internal_work_ids)
            or (isinstance(e.attributes.get("declaration"), dict) and e.attributes.get("declaration", {}).get("work_id") in matched_external_work_ids)
        ]
    return tuple(events)


def render_observation_timeline(
    snapshot: ObservationSnapshot,
    projection: WorkflowProjection,
    *,
    width: int = 120,
    color: bool = False,
    include_content: bool = False,
    work_filter: str | None = None,
    actor_filter: str | None = None,
) -> list[str]:
    """Render observation timeline with optional work/actor filters."""
    events = list(filter_timeline_events(snapshot, projection, work_filter=work_filter, actor_filter=actor_filter))
    if not events:
        return ["  (no matching observed events)"]

    lines: list[str] = [f"OBSERVATION TIMELINE ({len(events)} events)"]
    for ev in events:
        ts_str = ev.observed_at.strftime("%H:%M:%S")
        kind_str = ev.kind
        summary = ev.summary
        if not include_content and "attributes" in ev.to_dict() and "content" in ev.attributes:
            summary = summary or "message"

        basis_badge = "[OBS]"
        if ev.evidence and ev.evidence[0].evidence_class == "declared":
            basis_badge = "[DECL]"

        line = f"  {ts_str} {basis_badge} {kind_str:<18} {summary}"
        lines.append(tui.truncate(line, width))

        if include_content and "content" in ev.attributes:
            content_str = str(ev.attributes["content"]).strip()
            for cl in content_str.splitlines()[:5]:
                lines.append(tui.truncate(f"      | {cl}", width))

    return lines
