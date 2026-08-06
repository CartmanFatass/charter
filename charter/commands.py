"""Command implementations behind the ``charter`` CLI subcommands."""

from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path

from . import config, doctor, inventory, render, util, workspace
from .forge import ForgeError
from .forge.gitlab import GitLabForge


def _git(args, cwd=None):
    """Run git without raising, so callers can branch on the return code."""
    return util.run(["git", *args], cwd=cwd, check=False)


# Route git auth through THAT REPO'S OWN FORGE's token over HTTPS — no SSH keys, no
# 1Password agent (which is where all the signing/permission pain comes from). One
# credential PER FORGE: `!glab auth git-credential` for GitLab, `!gh auth git-credential`
# for GitHub, each forge's own git credential helper — git appends the get/store/erase
# operation. `_cred_flag` resolves this per call (never a single hardcoded forge), so a
# GitHub-hosted clone — like this control plane itself — authenticates correctly instead
# of silently being handed GitLab's helper.
def _cred_flag(forge) -> list[str]:
    """``-c credential.helper=<forge's>`` — makes ONE git invocation use *that forge's*
    token-holding CLI, regardless of what (if anything) local config already has. Belt
    and braces for the very first ``git clone``, before `gitpolicy.apply` has had a
    chance to write the repo's own local config."""
    return ["-c", f"credential.helper={forge.credential_helper()}"]


def _https_url(r: dict) -> str:
    """HTTPS clone URL for a repo (so cloning uses that repo's own forge's token, never
    SSH) — rewritten via THAT forge's own SSH forms (`registry.for_repo`, from the
    inventory's ``forge`` stamp — see ``charter/forge/registry.py``), not a hardcoded
    gitlab.com prefix, so a GitHub-recorded repo's SSH ``ssh_url`` rewrites correctly too."""
    web = (r.get("web_url") or "").rstrip("/")
    if web.startswith("https://"):
        return web + ".git"
    ssh = r.get("ssh_url") or ""
    from .forge import registry
    https_base, ssh_forms = registry.for_repo(r).insteadof()
    for prefix in ssh_forms:
        if ssh.startswith(prefix):
            return https_base + ssh[len(prefix):]
    return ssh


def _origin_https(root) -> str | None:
    """The control plane's own ``origin`` as an HTTPS URL (rewriting an SSH one via ITS
    forge's own rewrite rule — ``registry.resolve_host`` + ``insteadof()``), or ``None``
    when there's no origin yet, or its host isn't a forge this charter knows about — a
    DEFAULT host (gitlab.com/github.com) or one DECLARED in this control plane's own
    ``charter.toml`` (see ``gitpolicy.forge_for``, which resolves the same way). An
    unrecognised host deliberately returns ``None`` rather than guessing: `commit_push`
    then warns and skips the push instead of silently trying (and failing) against the
    wrong forge."""
    url = _git(["remote", "get-url", "origin"], cwd=root).stdout.strip()
    if not url:
        return None
    from .forge import registry
    forge = registry.resolve_host(url, config.ROOT)
    if forge is None:
        return None
    https_base, ssh_forms = forge.insteadof()
    if url.startswith(https_base):
        return url
    for prefix in ssh_forms:
        if url.startswith(prefix):
            return https_base + url[len(prefix):]
    return None


# --------------------------------------------------------------------------- #
# discover                                                                     #
# --------------------------------------------------------------------------- #
def _build_repo(forge, p: dict, no_probe: bool) -> dict:
    stack = "unknown"
    if not no_probe:
        files = forge.repo_tree(p, p.get("default_branch"))
        stack = inventory.classify_stack(files)
    return {
        "name": p["name"],
        "path_with_namespace": p["path_with_namespace"],
        "ssh_url": p["ssh_url"],
        "default_branch": p.get("default_branch") or "main",
        "kind": inventory.classify_kind(p["name"]),
        "stack": stack,
        "description": (p.get("description") or "").strip(),
        "topics": p.get("topics") or [],
        "web_url": p.get("web_url", ""),
        # Which forge this record came from, so a mixed inventory stays unambiguous
        # once records from several forges are merged (see inventory.merge/find).
        "forge": p.get("forge") or forge.kind,
    }


def _build_batch(forge, projects: list[dict], no_probe: bool) -> list[dict]:
    if no_probe:
        return [_build_repo(forge, p, no_probe) for p in projects]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(lambda p: _build_repo(forge, p, no_probe), projects))


def _forges_to_query(cfg: dict):
    """``[(forge, owner, exclude), …]`` for every declared ``[[forge]]`` block, or a
    single back-compat GitLab default (``config.GROUP``/``config.EXCLUDE``) when the
    control plane declares none — the shape every control plane had before multi-forge
    support existed, and still what a fresh `charter init` produces."""
    from . import instance as _instance
    from .forge import registry

    pairs = registry.forges_for(cfg)
    if pairs:
        return [(forge, owner, _instance.exclude_of(cfg, i))
                for i, (forge, owner) in enumerate(pairs)]
    return [(GitLabForge(), config.GROUP, config.EXCLUDE)]


def cmd_discover(args) -> int:
    from .forge import registry

    try:
        cfg = _instance_load_root()
    except Exception as e:
        raise SystemExit(str(e))

    try:
        # An unknown `kind` in a `[[forge]]` block (e.g. a typo, or a forge charter
        # doesn't support) is a config mistake, not a crash — same "clear error, not a
        # traceback" discipline as the CollisionError handling below.
        to_query = _forges_to_query(cfg)
    except ValueError as e:
        raise SystemExit(str(e))

    batches: list[list[dict]] = []
    for forge, owner, exclude in to_query:
        util.info(f"Querying {forge.kind} group `{owner}` …")
        try:
            forge.check_auth()
        except ForgeError as e:
            raise SystemExit(str(e))
        try:
            projects = [p for p in forge.list_repos(owner) if p["name"] not in exclude]
        except ForgeError as e:
            raise SystemExit(str(e))
        util.info(
            f"Found {len(projects)} project(s) on {forge.kind}. "
            + ("Skipping stack probe." if args.no_probe else "Probing repo stacks …")
        )
        # Built (and appended) immediately after a successful list_repos, but nothing
        # is saved until every declared forge has succeeded — see the merge/save step
        # below. A forge failing here raises before inventory.save is ever reached, so
        # a partial multi-forge failure can never wipe (or half-write) the inventory,
        # the same discipline the single-forge `_api_strict` split enforces.
        batches.append(_build_batch(forge, projects, args.no_probe))

    try:
        merged = inventory.merge(batches)
    except registry.CollisionError as e:
        raise SystemExit(str(e))

    before = {r["name"] for r in inventory.repos()}
    doc = inventory.save(merged)
    after = {r["name"] for r in merged}

    rel = config.INVENTORY.relative_to(config.ROOT)
    util.ok(f"Wrote {doc['count']} repos to {rel}")
    added, removed = sorted(after - before), sorted(before - after)
    if added:
        util.info("New in group: " + ", ".join(added))
    if removed:
        util.warn("No longer in group: " + ", ".join(removed))

    if not args.no_docs:
        cmd_docs(args)
    return 0


def _instance_load_root() -> dict:
    """Re-parse ``charter.toml`` against the *current* ``config.ROOT`` rather than
    trusting the module-level ``config._cfg`` cached at import time. Necessary for
    ``cmd_discover`` specifically: it's the one command whose forge list must reflect
    whatever ``config.ROOT`` names right now, including in tests that redirect ROOT to
    an isolated tmp dir (``PersonaIso``) after import already happened — reading the
    stale cached ``_cfg`` would silently see the real process's charter.toml (or lack
    of one) instead of the tmp control plane a test just set up."""
    from . import instance as _instance
    return _instance.load(config.ROOT)


# --------------------------------------------------------------------------- #
# docs                                                                         #
# --------------------------------------------------------------------------- #
def cmd_docs(args) -> int:
    doc = inventory.load()
    if not doc.get("repos"):
        util.warn("Inventory is empty — run `charter discover` first.")
        return 1

    config.DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (config.DOCS_DIR / "topology.md").write_text(render.topology_md(doc) + "\n")
    util.ok(f"Generated docs/topology.md ({len(doc['repos'])} repos)")

    if refresh_readme_personas():
        util.ok("Refreshed the persona roster block in README.md")
    return 0


def refresh_readme_personas() -> bool:
    """Rewrite the generated persona-roster block in README.md. Returns True when the
    file actually changed, so callers (and git) stay quiet on a no-op. Best-effort: a
    README without the markers, or an unreadable one, is left exactly as it is."""
    p = config.ROOT / "README.md"
    try:
        cur = p.read_text()
    except OSError:
        return False
    new = render.splice_personas(cur)
    if new is None or new == cur:
        return False
    try:
        p.write_text(new)
    except OSError:
        return False
    return True


# --------------------------------------------------------------------------- #
# clone                                                                        #
# --------------------------------------------------------------------------- #
def cmd_clone(args) -> int:
    doc = inventory.load()
    if not doc.get("repos"):
        util.err("Inventory is empty — run `charter discover` first.")
        return 1

    targets = _resolve_targets(args, doc)
    if not targets:
        util.err("No matching repos. Give one or more repo names/paths from the inventory "
                 "(see them: `charter status`; refresh from GitLab: `charter discover`).")
        return 1

    ws = workspace.resolve(getattr(args, "workspace", None))
    workspace.banner(ws, getattr(args, "workspace", None))
    try:
        wd = workspace.ensure(ws)
    except ValueError as e:
        util.err(str(e))
        return 1

    from .forge import registry

    failures = 0
    for r in targets:
        dest = wd / r["name"]
        if dest.exists():
            util.info(f"{r['name']}: already cloned in '{ws}'")
            _hint_repo_docs(dest, r)
            continue
        forge = registry.for_repo(r)
        util.info(f"Cloning {r['name']} ({r['default_branch']}) into '{ws}' via {forge.cli} (HTTPS) …")
        proc = _git([*_cred_flag(forge), "clone", "--branch", r["default_branch"], _https_url(r), str(dest)])
        if proc.returncode != 0:
            failures += 1
            util.err(
                f"{r['name']}: clone failed — no access, network, or {forge.cli} isn't authed "
                f"(`{forge.cli} auth status`). Skipping.\n" + (proc.stderr or "").strip()
            )
            continue
        # Golden rule 0: every git op from this clone uses ITS FORGE's token over HTTPS —
        # credential helper + signing off + SSH→HTTPS rewrites (see charter/gitpolicy.py).
        from . import gitpolicy
        gitpolicy.apply(dest)
        util.ok(f"{r['name']} → {dest.relative_to(config.ROOT)}")
        _hint_repo_docs(dest, r)
    return 1 if failures else 0


def _spawn_bg_push(root) -> None:
    """Fire a detached background push of HEAD (best-effort) so a slow push never blocks
    the turn — the same mechanism the workspace Stop-hook auto-save uses."""
    import subprocess
    import sys
    try:
        subprocess.Popen([sys.executable, "-m", "charter", "workspace", "_pushbg"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, start_new_session=True, cwd=str(root))
    except Exception:
        pass


def commit_memory_reactive(paths: list[str], title: str) -> int:
    """**Reactive memory**: commit the just-written memory file(s) locally (scoped +
    secret-scanned) and push in the BACKGROUND — so a memory reaches the shared repo the
    moment it's recorded, without blocking the turn. Best-effort. Returns commit_push's rc
    (0 = committed / nothing to do, 1 = a secret-shaped value was refused)."""
    return commit_push(config.ROOT, ["add", "--", *paths], f"memory: {title}"[:100],
                       background=True)


def commit_push(root, add_cmd: list, message: str | None,
                sign: bool = False, no_push: bool = False, background: bool = False) -> int:
    """Stage (``add_cmd``) → secret-scan staged memory/refs → commit → push via the
    control plane's OWN FORGE's token (`gitpolicy.forge_for`; rebase-retry on non-ff).
    Shared by `charter save` and `charter workspace save` so the secret-guard + no-SSH
    push path is identical everywhere, on whichever forge the control plane lives on."""
    _git(add_cmd, cwd=root)
    if _git(["diff", "--cached", "--quiet"], cwd=root).returncode == 0:
        util.info("Nothing to save — the control-plane working tree is clean.")
        return 0
    staged = [ln for ln in _git(["diff", "--cached", "--name-only"], cwd=root).stdout.splitlines() if ln.strip()]

    # Secret guard: refuse if a staged memory/ref file looks like it holds a secret.
    from .hooks import _secret_kind
    flagged = []
    for p in staged:
        if "/memory/" in p or "/refs/" in p:
            try:
                kind = _secret_kind((root / p).read_text())
            except OSError:
                kind = None
            if kind:
                flagged.append((p, kind))
    if flagged:
        util.err("Refusing to save — a secret-shaped value in a memory/ref file:")
        for p, k in flagged:
            util.err(f"  {p}  ({k})")
        util.info("Secrets belong in the vault (`charter persona secret set`). Remove it, then retry.")
        return 1

    msg = message or f"charter save: {len(staged)} file(s)"
    # Unsigned by default so the 1Password signer never hangs; --sign to opt in.
    signcfg = [] if sign else ["-c", "commit.gpgsign=false"]
    _git([*signcfg, "commit", "-q", "-m", msg], cwd=root)
    if _git(["diff", "--cached", "--quiet"], cwd=root).returncode != 0:  # signed commit failed
        _git(["commit", "--no-gpg-sign", "-q", "-m", msg], cwd=root)
    short = _git(["rev-parse", "--short", "HEAD"], cwd=root).stdout.strip()
    util.ok(f"Committed {short}: {msg}  ({len(staged)} file(s))")

    if no_push:
        util.info("Skipped push (--no-push).")
        return 0
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root).stdout.strip()
    https = _origin_https(root)
    if not https:
        util.warn("origin isn't on a forge charter knows (gitlab.com/github.com/…) — "
                  "committed locally; push manually.")
        return 0
    if background:
        _spawn_bg_push(root)
        util.info("→ pushing to the control plane in the background.")
        return 0

    from . import gitpolicy
    forge = gitpolicy.forge_for(root)
    cred = _cred_flag(forge)

    def push():
        return _git([*cred, "push", https, f"HEAD:{branch}"], cwd=root)

    p = push()
    if p.returncode != 0 and any(s in (p.stderr or "") for s in ("fetch first", "non-fast-forward", "rejected")):
        util.info("remote moved — fetching + rebasing, then retrying …")
        _git([*cred, "fetch", https, branch], cwd=root)
        if _git(["rebase", "FETCH_HEAD"], cwd=root).returncode != 0:
            _git(["rebase", "--abort"], cwd=root)
            util.warn("Committed locally, but rebase hit a conflict — resolve manually, then `charter save`.")
            return 0
        p = push()
    if p.returncode == 0:
        _git(["update-ref", f"refs/remotes/origin/{branch}", "HEAD"], cwd=root)  # sync tracking
        util.ok(f"Pushed {branch} via {forge.cli} (HTTPS token — no SSH, no 1Password).")
    else:
        util.warn(f"Committed, but the {forge.cli} push failed:")
        for ln in (p.stderr or p.stdout or "").splitlines()[-4:]:
            util.warn("  " + ln)
        util.info(f"Check `{forge.cli} auth status`.")
    return 0


def cmd_save(args) -> int:
    """Commit + push the CONTROL PLANE's own changes in one step, via ITS OWN FORGE's
    HTTPS token — no SSH keys, no 1Password signing hang. (For a *clone's* changes, work
    in a repo-rooted session; this is only the control plane's orchestration files.)"""
    return commit_push(config.ROOT, ["add", "-A"], args.message,
                       sign=getattr(args, "sign", False), no_push=getattr(args, "no_push", False))


def _resolve_targets(args, doc) -> list:
    out = []
    all_repos = inventory.repos(doc)
    for name in args.repos:
        r = inventory.find(all_repos, name)
        if r:
            out.append(r)
        else:
            util.warn(f"Unknown repo (not in inventory): {name} — check the name (`charter status`) "
                      "or `charter discover` if it's new to the group.")
    return out


def _hint_repo_docs(dest: Path, r: dict) -> None:
    for fname in ("CLAUDE.md", "AGENTS.md", "README.md"):
        if (dest / fname).exists():
            util.info(f"  ↳ {r['name']} ships its own {fname} — read it before working there.")
            return


# --------------------------------------------------------------------------- #
# sync                                                                         #
# --------------------------------------------------------------------------- #
def cmd_sync(args) -> int:
    if getattr(args, "all", False):
        names = workspace.list_workspaces()
        targets = [(n, d) for n in names for d in workspace.clones(n)]
        scope = f"all workspaces ({len(names)})"
    else:
        ws = workspace.resolve(getattr(args, "workspace", None))
        workspace.banner(ws, getattr(args, "workspace", None))
        targets = [(ws, d) for d in workspace.clones(ws)]
        scope = f"workspace '{ws}'"
    if not targets:
        util.warn(f"No cloned repos to sync in {scope}.")
        return 0
    for name, d in targets:
        _sync_one(d, name)
    return 0


def _sync_one(d: Path, ws: str) -> None:
    label = f"{ws}/{d.name}"
    if _git(["status", "--porcelain"], cwd=d).stdout.strip():
        util.warn(f"{label}: uncommitted changes — skipping (your work is left untouched).")
        return
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=d).stdout.strip()
    if _git(["fetch", "--prune"], cwd=d).returncode != 0:
        util.err(f"{label}: fetch failed (access or network) — skipping.")
        return
    ff = _git(["merge", "--ff-only", f"origin/{branch}"], cwd=d)
    if ff.returncode == 0:
        util.ok(f"{label}: up to date on {branch}")
    else:
        util.warn(f"{label}: {branch} won't fast-forward (diverged/local commits) — left as-is.")


# --------------------------------------------------------------------------- #
# status                                                                       #
# --------------------------------------------------------------------------- #
def cmd_status(args) -> int:
    doc = inventory.load()
    inv_by_name = {r["name"]: r for r in doc.get("repos", [])}
    all_ws = workspace.list_workspaces()
    explicit = getattr(args, "workspace", None)
    active = workspace.resolve(explicit)

    print(f"{config.GROUP}: {len(inv_by_name)} repos in inventory · "
          f"{len(all_ws)} workspace(s) · active: {active} (via {workspace.source(explicit)})\n")

    if all_ws:
        for n in all_ws:
            mark = "*" if n == active else " "
            print(f"  {mark} {n}  ({len(workspace.clones(n))} cloned)")
        print()

    which = all_ws if getattr(args, "all", False) else [active]
    for ws in which:
        _status_for_workspace(ws, inv_by_name, active)

    legacy = workspace.legacy_flat_clones()
    if legacy:
        util.warn(
            "Legacy clones sit directly under workspaces/ (pre-workspace layout): "
            + ", ".join(d.name for d in legacy)
            + f". Move them into workspaces/{config.DEFAULT_WORKSPACE}/ or re-clone with a workspace."
        )
    print(f"({len(inv_by_name)} repos available to clone — see docs/topology.md)")
    return 0


def _status_for_workspace(ws: str, inv_by_name: dict, active: str) -> None:
    clones = {d.name: d for d in workspace.clones(ws)}
    marker = " (active)" if ws == active else ""
    print(f"— workspace: {ws}{marker} · {len(clones)} cloned —")
    if not clones:
        print(f"  (empty; `charter clone <repo> --workspace {ws}` to populate)\n")
        return
    fmt = "  {:<38} {:<12} {}"
    print(fmt.format("REPO", "STACK", "BRANCH / NOTE"))
    for name, d in sorted(clones.items()):
        stack = inv_by_name.get(name, {}).get("stack", "?")
        print(fmt.format(name, stack, _clone_note(d)))
    print()


def _clone_note(d: Path) -> str:
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=d).stdout.strip()
    dirty = "dirty" if _git(["status", "--porcelain"], cwd=d).stdout.strip() else "clean"
    return f"{branch} · {dirty}"


# --------------------------------------------------------------------------- #
# gl-refresh (populate the status-line's forge state: open change + last CI)  #
# --------------------------------------------------------------------------- #
def cmd_gl_refresh(args) -> int:
    from . import glstate

    ws = workspace.resolve(getattr(args, "workspace", None))
    dirs = workspace.clones(ws)
    if not dirs:
        util.info(f"No clones in workspace '{ws}'.")
        return 0
    cache = glstate.refresh(dirs)
    util.ok(f"Refreshed forge state for {len(dirs)} repo(s) in '{ws}'.")
    for d in dirs:
        ent = cache.get(str(d), {})
        bits = []
        if ent.get("change"):
            bits.append(f"{ent.get('sigil') or '!'}{ent['change']}")
        if ent.get("ci"):
            bits.append(f"pipeline:{ent['ci']}")
        if bits:
            util.info(f"  {d.name}: {' · '.join(bits)}")
    return 0


# --------------------------------------------------------------------------- #
# doctor                                                                       #
# --------------------------------------------------------------------------- #
def cmd_doctor(args) -> int:
    """Preflight the environment. Exit non-zero if any hard requirement fails."""
    results = doctor.run_all()

    if getattr(args, "json", False):
        print(json.dumps(
            [{"name": r.name, "status": r.status, "detail": r.detail, "hint": r.hint}
             for r in results],
            indent=2,
        ))
    else:
        print("charter preflight:\n")
        for r in results:
            print(r.render())
        print()

    failed = [r for r in results if r.status == doctor.FAIL]
    warned = [r for r in results if r.status == doctor.WARN]
    if getattr(args, "json", False):
        return 1 if failed else 0

    if failed:
        print("✗ " + f"{len(failed)} blocker(s): " + ", ".join(r.name for r in failed)
              + ". Fix the → hints above, then re-run `charter doctor`.")
        return 1
    if warned:
        print("! " + f"{len(warned)} optional item(s) pending — see hints above.")
    else:
        print("✓ All set — you can discover and clone repos.")
    return 0


def cmd_recall(args) -> int:
    """The single memory-fetch gate: search (or list) across every relevant memory base —
    the active workspace's journal, the active persona's own memory, and the shared
    namespace (+ ephemeral with --ephemeral) — with each hit labeled by its source."""
    from . import recall as rc
    scopes = list(rc.DEFAULT_SCOPES)
    if getattr(args, "scope", None):
        scopes = [s.strip() for s in args.scope.split(",") if s.strip() in rc.SCOPES]
        if not scopes:
            util.err(f"invalid --scope; choose from {', '.join(rc.SCOPES)}")
            return 1
    if getattr(args, "ephemeral", False) and "ephemeral" not in scopes:
        scopes.append("ephemeral")
    results = rc.recall(query=getattr(args, "query", None), limit=getattr(args, "limit", 8),
                        persona_name=getattr(args, "persona", None),
                        workspace_name=getattr(args, "workspace", None), scopes=scopes)
    if not results:
        q = getattr(args, "query", None)
        util.info(f"No memories {'match ' + repr(q) if q else 'yet'} across {', '.join(scopes)}.")
        return 0
    width = max((len(s) for s, _p, _t, _sc in results), default=8)
    for source, path, title, score in results:
        tag = f"  ({score})" if score else ""
        print(f"  {source:<{width}}  {title}{tag}")
    util.info(f"{len(results)} memory(ies) across {', '.join(scopes)}. "
              f"Read one: find the file under its base (workspaces/… or personas/…).")
    return 0


def cmd_git_policy(args) -> int:
    """**Golden rule 0: one credential — PER FORGE.** Report (or `--apply`) the token-only
    git policy on the control plane and every clone, resolved per repo from ITS OWN forge
    (`gitpolicy.forge_for`): that forge's own credential helper over HTTPS, signing off, and
    SSH→HTTPS URL rewrites so even an SSH remote transports over the token. Local config only —
    a developer's global git config is never touched."""
    from . import gitpolicy
    targets = gitpolicy.repos(config.ROOT, config.WORKSPACES_DIR)
    if not targets:
        util.info("No git repos found (control plane + workspace clones).")
        return 0
    apply = getattr(args, "apply", False)
    drifted = fixed = unmanaged = 0
    for repo in targets:
        try:
            rel = repo.relative_to(config.ROOT)
        except ValueError:
            rel = repo
        name = str(rel) if str(rel) != "." else "control plane"
        drift = gitpolicy.check(repo)
        if not drift:
            continue
        if drift == [gitpolicy.UNMANAGED_FORGE]:
            # Honest "can't tell" — never silently reported green, and never guessed at
            # via `apply` either (see gitpolicy.forge_for / UNMANAGED_FORGE).
            unmanaged += 1
            util.warn(f"{name}: {gitpolicy.UNMANAGED_FORGE}")
            continue
        drifted += 1
        if apply:
            changes = gitpolicy.apply(repo)
            fixed += 1
            util.ok(f"{name}: applied {len(changes)} setting(s) — token-only")
        else:
            util.warn(f"{name}: {len(drift)} setting(s) not token-only")
            for d in drift[:4]:
                util.info(f"    {d}")
    if not drifted and not unmanaged:
        util.ok(f"All {len(targets)} repo(s) are token-only (each forge's own HTTPS token, "
                f"no SSH, no signing).")
    else:
        if apply:
            util.ok(f"Applied the single-credential policy to {fixed} of {len(targets)} repo(s).")
        elif drifted:
            util.info(f"{drifted} of {len(targets)} repo(s) drifted — fix: charter git-policy --apply")
        if unmanaged:
            util.warn(f"{unmanaged} repo(s) have an unrecognised forge — not covered by "
                      f"any policy. Declare the host in charter.toml's [[forge]] to bring "
                      f"{'it' if unmanaged == 1 else 'them'} under management.")
    return 0
