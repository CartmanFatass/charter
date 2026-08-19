"""Subagent CLI commands for charter (tree, list, show, watch)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import subagent, tui, util


def _resolve_session_root(
    session_id: str | None = None,
    cwd: str | Path | None = None,
    days: int = 3,
) -> tuple[str, list[subagent.SubagentInfo]]:
    """Determine the session root ID and any known subagents from live rollouts."""
    from . import session

    target_cwd = cwd or os.getcwd()
    known: list[subagent.SubagentInfo] = []

    if session_id:
        root_id = subagent.find_root_session_id(session_id, max_days_back=days)
        rollouts = subagent.find_rollouts_in_days(max_days_back=days)
        for r in rollouts:
            if r[1] == root_id:
                known = subagent.parse_rollout_subagents(r[0])
                break
        return root_id, known

    # Try charter current session
    sid = session.current()
    if sid:
        root_id = subagent.find_root_session_id(sid, max_days_back=days)
        rollouts = subagent.find_rollouts_in_days(max_days_back=days)
        for r in rollouts:
            if r[1] == root_id:
                known = subagent.parse_rollout_subagents(r[0])
                break
        return root_id, known

    # Look up most recent rollout for the target cwd
    recent = subagent.find_most_recent_rollout(max_days_back=days, target_cwd=target_cwd)
    if recent:
        matched_id = recent[1]
        root_id = subagent.find_root_session_id(matched_id, max_days_back=days)
        rollouts = subagent.find_rollouts_in_days(max_days_back=days)
        for r in rollouts:
            if r[1] == root_id:
                known = subagent.parse_rollout_subagents(r[0])
                break
        return root_id, known

    return "", []

def cmd_subagent_tree(args) -> int:
    """Render the subagent hierarchy tree for a session."""
    session_id = getattr(args, "session", None)
    cwd = getattr(args, "cwd", None)
    watch_mode = getattr(args, "watch", False)
    interval = getattr(args, "interval", subagent.DEFAULT_WATCH_INTERVAL)
    days = getattr(args, "days", 3)
    plain = getattr(args, "plain", False)
    as_json = getattr(args, "json", False)
    verbose = getattr(args, "verbose", False)

    if watch_mode:
        return subagent.watch_subagent_tree(
            root_id=session_id,
            target_cwd=cwd or os.getcwd(),
            interval=interval,
            max_days_back=days,
            verbose=verbose,
        )

    root_id, known = _resolve_session_root(session_id=session_id, cwd=cwd, days=days)
    if not root_id:
        if as_json:
            print(json.dumps({"root_id": "", "total_count": 0, "nodes": []}))
            return 0
        util.info("No active or recent Codex session found.")
        return 0

    tree = subagent.build_subagent_tree(
        root_id=root_id,
        known_subagents=known,
        max_days_back=days,
    )

    if as_json:
        print(json.dumps(tree.to_dict(), indent=2))
        return 0

    exchanges = subagent.extract_subagent_exchanges(
        session_id=root_id,
        max_days_back=days,
    ) if root_id else []

    if plain:
        print(subagent.render_subagent_tree_page_plain(tree, recent_exchanges=exchanges, verbose=verbose))
    else:
        lines = subagent.render_subagent_tree_page(
            tree,
            color=True,
            recent_exchanges=exchanges,
            verbose=verbose,
        )
        print("\n".join(lines))
    return 0


def cmd_subagent_list(args) -> int:
    """List subagents in tabular or JSON format."""
    session_id = getattr(args, "session", None)
    cwd = getattr(args, "cwd", None)
    show_all = getattr(args, "all", False)
    days = getattr(args, "days", 3)
    as_json = getattr(args, "json", False)
    verbose = getattr(args, "verbose", False)

    if show_all:
        # Scan all rollouts and collect all subagents
        rollouts = subagent.find_rollouts_in_days(max_days_back=days)
        all_links = []
        for r in rollouts:
            link = subagent.peek_session_link(r[0], modified_at=r[3])
            if link and link.parent_id:
                all_links.append(link)

        if as_json:
            print(json.dumps([
                {
                    "id": l.id,
                    "parent_id": l.parent_id,
                    "name": l.name,
                    "started_at": l.started_at.isoformat() if l.started_at else None,
                    "modified_at": l.modified_at.isoformat(),
                }
                for l in all_links
            ], indent=2))
            return 0

        if not all_links:
            util.info("No subagents found in recent sessions.")
            return 0

        if verbose:
            print(f"{'SUBAGENT':<28}{'ID':<38}{'PARENT':<38}{'STARTED':<20} STATUS")
        else:
            print(f"{'SUBAGENT':<24}{'ID':<12}{'PARENT':<12}{'STARTED':<20} STATUS")

        now_ts = subagent.datetime.now(subagent.timezone.utc).timestamp()
        for l in all_links:
            st = "running" if (now_ts - l.modified_at.timestamp()) <= subagent.ACTIVE_WINDOW_SECONDS else "completed"
            st_col = "\033[36m" if st == "running" else "\033[32m"
            started_str = l.started_at.strftime("%Y-%m-%d %H:%M:%S") if l.started_at else "-"
            if verbose:
                print(f"{l.name:<28}{l.id:<38}{l.parent_id if l.parent_id else '-':<38}{started_str:<20} {st_col}{st}\033[0m")
            else:
                print(f"{l.name:<24}{l.id[:8]:<12}{l.parent_id[:8] if l.parent_id else '-':<12}{started_str:<20} {st_col}{st}\033[0m")
        return 0

    root_id, known = _resolve_session_root(session_id=session_id, cwd=cwd, days=days)
    if not root_id:
        if as_json:
            print(json.dumps([]))
            return 0
        util.info("No active or recent Codex session found.")
        return 0

    tree = subagent.build_subagent_tree(root_id=root_id, known_subagents=known, max_days_back=days)
    nodes = subagent.flatten_subagent_tree(tree.nodes)

    if as_json:
        print(json.dumps([n.to_dict() for n in nodes], indent=2))
        return 0

    if not nodes:
        util.info("No subagents in this session.")
        return 0

    if verbose:
        print(f"{'SUBAGENT':<28}{'ID':<38}{'DEPTH':<8}{'ELAPSED':<12} STATUS")
    else:
        print(f"{'SUBAGENT':<24}{'ID':<12}{'DEPTH':<8}{'ELAPSED':<12} STATUS")

    now = subagent.datetime.now(subagent.timezone.utc)
    for n in nodes:
        elapsed = subagent.format_elapsed(n.started_at, now) if n.started_at else "-"
        st_col = (
            "\033[32m" if n.status == "completed"
            else "\033[31m" if n.status == "error"
            else "\033[33m" if n.status == "starting"
            else "\033[36m"
        )
        if verbose:
            print(f"{n.name:<28}{n.id:<38}{n.depth:<8}{elapsed:<12} {st_col}{n.status}\033[0m")
        else:
            print(f"{n.name:<24}{n.id[:8]:<12}{n.depth:<8}{elapsed:<12} {st_col}{n.status}\033[0m")
    return 0

def cmd_subagent_log(args) -> int:
    """Show communication exchange history between session and subagents or among subagents."""
    session_id = getattr(args, "session", None)
    subagent_id = getattr(args, "subagent", None)
    cwd = getattr(args, "cwd", None)
    days = getattr(args, "days", 3)
    include_tools = getattr(args, "tool_calls", False)
    as_json = getattr(args, "json", False)

    target_sid = session_id
    if not target_sid and not subagent_id:
        target_sid, _ = _resolve_session_root(cwd=cwd, days=days)

    exchanges = subagent.extract_subagent_exchanges(
        session_id=target_sid,
        subagent_id=subagent_id,
        max_days_back=days,
        include_tool_calls=include_tools,
    )

    if as_json:
        print(json.dumps([e.to_dict() for e in exchanges], indent=2))
        return 0

    lines = subagent.render_exchange_timeline(exchanges, color=True)
    print("\n".join(lines))
    return 0


def cmd_subagent_show(args) -> int:
    """Show details for a specific subagent."""
    target = getattr(args, "id", "")
    days = getattr(args, "days", 30)
    as_json = getattr(args, "json", False)
    verbose = getattr(args, "verbose", False)

    if not target:
        util.err("Subagent ID or name required.")
        return 1
    rollouts = subagent.find_rollouts_in_days(max_days_back=days)
    matched_link = None
    matched_rollout = None

    for r in rollouts:
        link = subagent.peek_session_link(r[0], modified_at=r[3])
        if link and (link.id == target or link.id.startswith(target) or link.name.lower() == target.lower()):
            matched_link = link
            matched_rollout = r
            break

    if not matched_link:
        util.err(f"No subagent matching '{target}' found.")
        return 1

    now_ts = subagent.datetime.now(subagent.timezone.utc).timestamp()
    status = (
        "running"
        if (now_ts - matched_link.modified_at.timestamp()) <= subagent.ACTIVE_WINDOW_SECONDS
        else "completed"
    )

    exchanges = subagent.extract_subagent_exchanges(subagent_id=matched_link.id, max_days_back=days)

    data = {
        "id": matched_link.id,
        "name": matched_link.name,
        "parent_id": matched_link.parent_id,
        "status": status,
        "started_at": matched_link.started_at.isoformat() if matched_link.started_at else None,
        "modified_at": matched_link.modified_at.isoformat(),
        "cwd": matched_link.cwd,
        "rollout_file": str(matched_rollout[0]) if matched_rollout else None,
        "exchanges_count": len(exchanges),
        "exchanges": [e.to_dict() for e in exchanges],
    }

    if as_json:
        print(json.dumps(data, indent=2))
        return 0

    print(f"\033[1mSubagent:\033[0m {matched_link.name}")
    print(f"  ID:         {matched_link.id}")
    print(f"  Parent:     {matched_link.parent_id or '(none)'}")
    st_col = "\033[36m" if status == "running" else "\033[32m"
    print(f"  Status:     {st_col}{status}\033[0m")
    if matched_link.started_at:
        print(f"  Started:    {matched_link.started_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Last Event: {matched_link.modified_at.strftime('%Y-%m-%d %H:%M:%S')}")
    if matched_link.cwd:
        print(f"  CWD:        {matched_link.cwd}")
    if matched_rollout:
        print(f"  Rollout:    {matched_rollout[0]}")

    if exchanges:
        limit = 15 if verbose else 5
        print(f"\n\033[1mRecent Exchanges ({len(exchanges)}):\033[0m")
        tlines = subagent.render_exchange_timeline(exchanges[-limit:], color=True)
        for tl in tlines:
            print("  " + tl)

    return 0
