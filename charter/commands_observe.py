"""Read-only workflow observation CLI commands for charter observe."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Literal

from . import observations, subagent, tui, util, workflow_view


def _resolve_session_root_id(
    session_id: str | None = None,
    cwd: str | Path | None = None,
    days: int = 3,
) -> tuple[str, bool]:
    """Resolve the session root ID without generating CLI output.

    Returns: (root_id, explicit_session_not_found)
    """
    from . import session

    target_cwd = cwd or os.getcwd()

    if session_id:
        root_id = subagent.find_root_session_id(session_id, max_days_back=days)
        if root_id:
            return root_id, False
        return session_id, False

    sid = session.current()
    if sid:
        root_id = subagent.find_root_session_id(sid, max_days_back=days)
        if root_id:
            return root_id, False

    recent = subagent.find_most_recent_rollout(max_days_back=days, target_cwd=target_cwd)
    if recent:
        matched_id = recent[1]
        root_id = subagent.find_root_session_id(matched_id, max_days_back=days)
        return root_id, False

    return "", False


def _emit_json(
    view: str,
    root_id: str,
    data: Any,
    warnings: list[str] | tuple[str, ...] = (),
    captured_at: str | None = None,
) -> None:
    payload = {
        "schema": 1,
        "root_id": root_id,
        "captured_at": captured_at or datetime_now_iso(),
        "view": view,
        "data": data,
        "warnings": list(warnings),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def datetime_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def cmd_observe_position(args) -> int:
    """Render position view of workflow items and their immediate obligations."""
    session_id = getattr(args, "session", None)
    cwd = getattr(args, "cwd", None)
    days = getattr(args, "days", 3)
    as_json = getattr(args, "json", False)
    plain = getattr(args, "plain", False)
    watch_mode = getattr(args, "watch", False)
    interval = getattr(args, "interval", 10.0)

    direction_filter = getattr(args, "direction", None)
    role_filter = getattr(args, "role", None)
    active_only = getattr(args, "active_only", False)

    if watch_mode:
        return watch_workflow_view(
            view="position",
            root_id=session_id,
            target_cwd=str(cwd) if cwd else None,
            interval=interval,
            max_days_back=days,
            plain=plain,
            direction=direction_filter,
            role=role_filter,
            active_only=active_only,
        )

    try:
        root_id, not_found = _resolve_session_root_id(session_id=session_id, cwd=cwd, days=days)
        if not root_id:
            if as_json:
                _emit_json("position", "", [])
                return 0
            util.info("No active or recent Codex session found.")
            return 0

        snapshot = observations.collect_observation_snapshot(root_id, max_days_back=days)
        projection = workflow_view.project_workflow(snapshot)

        work_items = list(projection.work_items)
        if direction_filter:
            work_items = [w for w in work_items if w.direction and w.direction.lower() == direction_filter.lower()]
        if role_filter:
            work_items = [w for w in work_items if w.actor_role and w.actor_role.lower() == role_filter.lower()]
        if active_only:
            work_items = [w for w in work_items if w.phase in ("dispatched", "active", "returned")]

        filtered_projection = workflow_view.WorkflowProjection(
            root_id=projection.root_id,
            captured_at=projection.captured_at,
            actors=projection.actors,
            relations=projection.relations,
            work_items=tuple(work_items),
            open_obligations=projection.open_obligations,
            incidents=projection.incidents,
            unbound_events=projection.unbound_events,
        )

        if as_json:
            _emit_json(
                view="position",
                root_id=root_id,
                data=[w.to_dict() for w in filtered_projection.work_items],
                warnings=snapshot.warnings,
                captured_at=snapshot.captured_at.isoformat(),
            )
            return 0

        width = tui.term_width() if not plain else 120
        lines = workflow_view.render_position_table(filtered_projection, width=width, color=not plain)
        print("\n".join(lines))
        return 0
    except Exception as exc:
        if as_json:
            _emit_json("position", session_id or "", [], warnings=[f"Observation error: {exc}"])
            return 0
        util.info(f"Observation temporarily unavailable: {exc}")
        return 0


def cmd_observe_obligations(args) -> int:
    """Render open workflow obligations."""
    session_id = getattr(args, "session", None)
    cwd = getattr(args, "cwd", None)
    days = getattr(args, "days", 3)
    as_json = getattr(args, "json", False)
    plain = getattr(args, "plain", False)
    watch_mode = getattr(args, "watch", False)
    interval = getattr(args, "interval", 10.0)

    owner_filter = getattr(args, "owner", None)
    kind_filter = getattr(args, "kind", None)

    if watch_mode:
        return watch_workflow_view(
            view="obligations",
            root_id=session_id,
            target_cwd=str(cwd) if cwd else None,
            interval=interval,
            max_days_back=days,
            plain=plain,
            owner=owner_filter,
            kind=kind_filter,
        )

    try:
        root_id, not_found = _resolve_session_root_id(session_id=session_id, cwd=cwd, days=days)
        if not root_id:
            if as_json:
                _emit_json("obligations", "", [])
                return 0
            util.info("No active or recent Codex session found.")
            return 0

        snapshot = observations.collect_observation_snapshot(root_id, max_days_back=days)
        projection = workflow_view.project_workflow(snapshot)

        obls = list(projection.open_obligations)
        if owner_filter:
            obls = [o for o in obls if owner_filter.lower() in o.owner_label.lower()]
        if kind_filter:
            obls = [o for o in obls if o.kind == kind_filter]

        filtered_projection = workflow_view.WorkflowProjection(
            root_id=projection.root_id,
            captured_at=projection.captured_at,
            actors=projection.actors,
            relations=projection.relations,
            work_items=projection.work_items,
            open_obligations=tuple(obls),
            incidents=projection.incidents,
            unbound_events=projection.unbound_events,
        )

        if as_json:
            _emit_json(
                view="obligations",
                root_id=root_id,
                data=[o.to_dict() for o in filtered_projection.open_obligations],
                warnings=snapshot.warnings,
                captured_at=snapshot.captured_at.isoformat(),
            )
            return 0

        width = tui.term_width() if not plain else 120
        lines = workflow_view.render_obligations(filtered_projection, width=width, color=not plain)
        print("\n".join(lines))
        return 0
    except Exception as exc:
        if as_json:
            _emit_json("obligations", session_id or "", [], warnings=[f"Observation error: {exc}"])
            return 0
        util.info(f"Observation temporarily unavailable: {exc}")
        return 0


def cmd_observe_actors(args) -> int:
    """Render workflow actors and relationships."""
    session_id = getattr(args, "session", None)
    cwd = getattr(args, "cwd", None)
    days = getattr(args, "days", 3)
    as_json = getattr(args, "json", False)
    plain = getattr(args, "plain", False)
    watch_mode = getattr(args, "watch", False)
    interval = getattr(args, "interval", 10.0)
    runtime_tree = getattr(args, "runtime_tree", False)
    declared_relations = getattr(args, "declared_relations", False)

    if watch_mode:
        return watch_workflow_view(
            view="actors",
            root_id=session_id,
            target_cwd=str(cwd) if cwd else None,
            interval=interval,
            max_days_back=days,
            plain=plain,
            runtime_tree=runtime_tree,
            declared_relations=declared_relations,
        )

    try:
        root_id, not_found = _resolve_session_root_id(session_id=session_id, cwd=cwd, days=days)
        if not root_id:
            if as_json:
                _emit_json("actors", "", {"actors": [], "relations": []})
                return 0
            util.info("No active or recent Codex session found.")
            return 0

        snapshot = observations.collect_observation_snapshot(root_id, max_days_back=days)
        projection = workflow_view.project_workflow(snapshot)

        if as_json:
            _emit_json(
                view="actors",
                root_id=root_id,
                data={
                    "actors": [a.to_dict() for a in projection.actors],
                    "relations": [r.to_dict() for r in projection.relations],
                },
                warnings=snapshot.warnings,
                captured_at=snapshot.captured_at.isoformat(),
            )
            return 0

        width = tui.term_width() if not plain else 120
        lines = workflow_view.render_actor_view(
            projection,
            width=width,
            color=not plain,
            runtime_tree_only=runtime_tree,
            declared_relations_only=declared_relations,
        )
        print("\n".join(lines))
        return 0
    except Exception as exc:
        if as_json:
            _emit_json("actors", session_id or "", {"actors": [], "relations": []}, warnings=[f"Observation error: {exc}"])
            return 0
        util.info(f"Observation temporarily unavailable: {exc}")
        return 0


def cmd_observe_timeline(args) -> int:
    """Render observation event timeline."""
    session_id = getattr(args, "session", None)
    cwd = getattr(args, "cwd", None)
    days = getattr(args, "days", 3)
    as_json = getattr(args, "json", False)
    plain = getattr(args, "plain", False)
    watch_mode = getattr(args, "watch", False)
    interval = getattr(args, "interval", 10.0)

    work_filter = getattr(args, "work", None)
    actor_filter = getattr(args, "actor", None)
    include_tool_calls = getattr(args, "tool_calls", False)
    include_content = getattr(args, "include_content", False)

    if watch_mode:
        return watch_workflow_view(
            view="timeline",
            root_id=session_id,
            target_cwd=str(cwd) if cwd else None,
            interval=interval,
            max_days_back=days,
            plain=plain,
            work=work_filter,
            actor=actor_filter,
            tool_calls=include_tool_calls,
            include_content=include_content,
        )

    try:
        root_id, not_found = _resolve_session_root_id(session_id=session_id, cwd=cwd, days=days)
        if not root_id:
            if as_json:
                _emit_json("timeline", "", [])
                return 0
            util.info("No active or recent Codex session found.")
            return 0

        snapshot = observations.collect_observation_snapshot(
            root_id,
            max_days_back=days,
            include_tool_calls=include_tool_calls,
        )
        projection = workflow_view.project_workflow(snapshot)

        if as_json:
            # Respect safe-by-default content behavior and filters
            filtered_events = list(snapshot.events)
            if actor_filter:
                act_lower = actor_filter.lower()
                filtered_events = [
                    e for e in filtered_events
                    if (e.actor_id and act_lower in e.actor_id.lower())
                    or (e.actor_name and act_lower in e.actor_name.lower())
                    or (e.author_actor_id and act_lower in e.author_actor_id.lower())
                ]
            if work_filter:
                wf_lower = work_filter.lower()
                matched_event_ids: set[str] = set()
                for w in projection.work_items:
                    if (w.external_id and wf_lower in w.external_id.lower()) or (wf_lower in w.id.lower()):
                        for b in w.basis:
                            matched_event_ids.add(b.source_id)
                filtered_events = [
                    e for e in filtered_events
                    if e.id in matched_event_ids
                    or f"{e.session_id}" in matched_event_ids
                    or (e.attributes.get("declaration", {}).get("work_id") and wf_lower in str(e.attributes.get("declaration", {}).get("work_id")).lower())
                    or (wf_lower in e.summary.lower())
                ]

            raw_events = []
            for e in filtered_events:
                ed = e.to_dict()
                if not include_content and "attributes" in ed and "content" in ed["attributes"]:
                    ed["attributes"]["content"] = "[elided; pass --include-content to view]"
                raw_events.append(ed)

            _emit_json(
                view="timeline",
                root_id=root_id,
                data=raw_events,
                warnings=snapshot.warnings,
                captured_at=snapshot.captured_at.isoformat(),
            )
            return 0

        width = tui.term_width() if not plain else 120
        lines = workflow_view.render_observation_timeline(
            snapshot,
            projection,
            width=width,
            color=not plain,
            include_content=include_content,
            work_filter=work_filter,
            actor_filter=actor_filter,
        )
        print("\n".join(lines))
        return 0
    except Exception as exc:
        if as_json:
            _emit_json("timeline", session_id or "", [], warnings=[f"Observation error: {exc}"])
            return 0
        util.info(f"Observation temporarily unavailable: {exc}")
        return 0


def cmd_observe_explain(args) -> int:
    """Explain a projection item with full machine-readable evidence basis."""
    target_id = getattr(args, "id", None)
    session_id = getattr(args, "session", None)
    cwd = getattr(args, "cwd", None)
    days = getattr(args, "days", 3)
    as_json = getattr(args, "json", False)
    plain = getattr(args, "plain", False)

    if not target_id:
        if as_json:
            _emit_json("explain", "", None, warnings=["No projection ID supplied"])
            return 1
        util.fail("No projection ID supplied to explain.")
        return 1

    try:
        root_id, not_found = _resolve_session_root_id(session_id=session_id, cwd=cwd, days=days)
        if not root_id:
            if as_json:
                _emit_json("explain", "", None, warnings=["No active or recent session found"])
                return 1
            util.fail("No active or recent session found.")
            return 1

        snapshot = observations.collect_observation_snapshot(root_id, max_days_back=days, include_tool_calls=True)
        projection = workflow_view.project_workflow(snapshot)

        explanation = workflow_view.explain_projection(projection, target_id)
        if not explanation:
            if as_json:
                _emit_json("explain", root_id, None, warnings=[f"Item '{target_id}' not found in workflow projection"])
                return 1
            util.fail(f"Item '{target_id}' not found in workflow projection for session {root_id[:8]}.")
            return 1

        if as_json:
            _emit_json(
                view="explain",
                root_id=root_id,
                data=explanation,
                warnings=snapshot.warnings,
                captured_at=snapshot.captured_at.isoformat(),
            )
            return 0

        print(f"EXPLANATION: {explanation.get('type', 'item').upper()} '{target_id}'")
        print(f"  Root Session: {root_id}")
        for k, v in explanation.items():
            if k in ("evidence", "type"):
                continue
            if v is not None:
                print(f"  {k:<16}: {v}")

        ev_list = explanation.get("evidence", [])
        if ev_list:
            print(f"\n  EVIDENCE BASIS ({len(ev_list)} references):")
            for ev in ev_list:
                cls_badge = f"[{ev.get('evidence_class', 'mechanical').upper()[:4]}]"
                loc = f"{ev.get('file_path') or 'state'}:{ev.get('line_number') or '-'}"
                print(f"    • {cls_badge} {ev.get('source', '')} / {ev.get('raw_kind', '')} at {loc} ({ev.get('observed_at', '')})")

        return 0
    except Exception as exc:
        if as_json:
            _emit_json("explain", session_id or "", None, warnings=[f"Observation error: {exc}"])
            return 1
        util.fail(f"Observation error: {exc}")
        return 1


def watch_workflow_view(
    *,
    view: Literal["position", "obligations", "actors", "timeline"],
    root_id: str | None,
    target_cwd: str | None,
    interval: float,
    max_days_back: int,
    plain: bool,
    direction: str | None = None,
    role: str | None = None,
    active_only: bool = False,
    owner: str | None = None,
    kind: str | None = None,
    work: str | None = None,
    actor: str | None = None,
    tool_calls: bool = False,
    include_content: bool = False,
    runtime_tree: bool = False,
    declared_relations: bool = False,
) -> int:
    """Watch workflow view, re-rendering only when source data changes or on heartbeat."""
    from . import trace, inflight

    effective_root, _ = _resolve_session_root_id(session_id=root_id, cwd=target_cwd, days=max_days_back)
    s_dir = subagent.get_sessions_dir()
    watcher = subagent.SubagentEventWatcher(sessions_dir=s_dir, max_days_back=max_days_back)

    try:
        inflight_dir = inflight._dir()
        if inflight_dir.exists():
            watcher.extra_paths.append(inflight_dir)
        if effective_root:
            t_file = trace._file(effective_root)
            if t_file.exists():
                watcher.extra_paths.append(t_file)
    except Exception:
        pass

    poll_interval = 0.5
    last_repaint = 0.0
    last_projection: workflow_view.WorkflowProjection | None = None
    last_snapshot: observations.ObservationSnapshot | None = None

    try:
        if not plain:
            sys.stdout.write("\033[?25l")
            sys.stdout.flush()

        while True:
            now = time.time()
            changed = watcher.check_for_changes()
            need_rebuild = changed or (last_projection is None)
            need_repaint = need_rebuild or (now - last_repaint >= interval)

            if need_rebuild:
                if not effective_root:
                    effective_root, _ = _resolve_session_root_id(session_id=root_id, cwd=target_cwd, days=max_days_back)
                if effective_root:
                    last_snapshot = observations.collect_observation_snapshot(
                        effective_root,
                        max_days_back=max_days_back,
                        include_tool_calls=tool_calls,
                    )
                    last_projection = workflow_view.project_workflow(last_snapshot)

            if need_repaint and last_projection is not None and last_snapshot is not None:
                width = tui.term_width() if not plain else 120
                if view == "position":
                    items = list(last_projection.work_items)
                    if direction:
                        items = [w for w in items if w.direction and w.direction.lower() == direction.lower()]
                    if role:
                        items = [w for w in items if w.actor_role and w.actor_role.lower() == role.lower()]
                    if active_only:
                        items = [w for w in items if w.phase in ("dispatched", "active", "returned")]
                    sub_proj = workflow_view.WorkflowProjection(
                        root_id=last_projection.root_id,
                        captured_at=last_projection.captured_at,
                        actors=last_projection.actors,
                        relations=last_projection.relations,
                        work_items=tuple(items),
                        open_obligations=last_projection.open_obligations,
                        incidents=last_projection.incidents,
                        unbound_events=last_projection.unbound_events,
                    )
                    lines = workflow_view.render_position_table(sub_proj, width=width, color=not plain)
                elif view == "obligations":
                    obls = list(last_projection.open_obligations)
                    if owner:
                        obls = [o for o in obls if owner.lower() in o.owner_label.lower()]
                    if kind:
                        obls = [o for o in obls if o.kind == kind]
                    sub_proj = workflow_view.WorkflowProjection(
                        root_id=last_projection.root_id,
                        captured_at=last_projection.captured_at,
                        actors=last_projection.actors,
                        relations=last_projection.relations,
                        work_items=last_projection.work_items,
                        open_obligations=tuple(obls),
                        incidents=last_projection.incidents,
                        unbound_events=last_projection.unbound_events,
                    )
                    lines = workflow_view.render_obligations(sub_proj, width=width, color=not plain)
                elif view == "actors":
                    lines = workflow_view.render_actor_view(
                        last_projection,
                        width=width,
                        color=not plain,
                        runtime_tree_only=runtime_tree,
                        declared_relations_only=declared_relations,
                    )
                elif view == "timeline":
                    lines = workflow_view.render_observation_timeline(
                        last_snapshot,
                        last_projection,
                        width=width,
                        color=not plain,
                        include_content=include_content,
                        work_filter=work,
                        actor_filter=actor,
                    )
                else:
                    lines = ["(unknown view)"]

                if not plain:
                    sys.stdout.write("\033[H\033[J")
                print("\n".join(lines))
                last_repaint = now

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        pass
    finally:
        if not plain:
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()
    return 0
