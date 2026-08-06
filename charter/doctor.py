"""Environment preflight checks for the umbrella (``edm doctor``).

Read-only: verifies the tools and auth a developer needs *before* they try to
discover or clone, and prints exact remediation steps for anything missing.
Nothing here changes the system.
"""

from __future__ import annotations

import platform
import shutil
import sys
from dataclasses import dataclass

from . import inventory, util

OK, WARN, FAIL = "ok", "warn", "fail"

_SYMBOL = {OK: ("32", "✓"), WARN: ("33", "!"), FAIL: ("31", "✗")}


def _color() -> bool:
    return sys.stdout.isatty()


@dataclass
class Result:
    name: str
    status: str
    detail: str = ""
    hint: str = ""

    def render(self) -> str:
        code, glyph = _SYMBOL[self.status]
        if _color():
            glyph = f"\033[{code}m{glyph}\033[0m"
        line = f"  {glyph}  {self.name:<16} {self.detail}".rstrip()
        if self.hint and self.status != OK:
            line += f"\n        → {self.hint}"
        return line


def _first_line(text: str) -> str:
    text = (text or "").strip()
    return text.splitlines()[0] if text else ""


def check_python() -> Result:
    ok = sys.version_info >= (3, 9)
    return Result(
        "python3",
        OK if ok else FAIL,
        detail=platform.python_version(),
        hint="" if ok else "Python 3.9+ is required.",
    )


def check_git() -> Result:
    if not shutil.which("git"):
        return Result("git", FAIL, hint="Install git: xcode-select --install (macOS) or brew install git.")
    return Result("git", OK, detail=_first_line(util.run(["git", "--version"], check=False).stdout))


def check_glab() -> Result:
    if not shutil.which("glab"):
        return Result(
            "glab",
            FAIL,
            hint="Install glab: brew install glab  (see https://gitlab.com/gitlab-org/cli).",
        )
    return Result("glab", OK, detail=_first_line(util.run(["glab", "--version"], check=False).stdout))


def check_glab_auth() -> Result:
    if not shutil.which("glab"):
        return Result("glab auth", FAIL, hint="Install glab first, then run: glab auth login.")
    proc = util.run(["glab", "auth", "status"], check=False)
    blob = (proc.stdout or "") + (proc.stderr or "")
    if "Logged in" in blob:
        summary = next(
            (ln.strip() for ln in blob.splitlines() if "Logged in" in ln),
            "authenticated",
        )
        return Result("glab auth", OK, detail=summary)
    return Result(
        "glab auth",
        FAIL,
        detail=_first_line(blob),
        hint="Run: glab auth login  (pick gitlab.com; choose TOKEN/HTTPS — the umbrella "
             "never uses SSH for git).",
    )


def check_ssh() -> Result:
    """Golden rule: **one credential** — the glab token over HTTPS. SSH is deliberately NOT
    used, so this no longer probes for a key (that was a contradictory hard requirement).
    Instead it verifies the umbrella's own repo carries the token-only git policy."""
    from . import config as _config, gitpolicy
    scope = gitpolicy.repos(_config.ROOT, _config.WORKSPACES_DIR)
    bad = [r for r in scope if gitpolicy.check(r)]
    if not bad:
        return Result("git auth", OK,
                      detail=f"token-only across {len(scope)} repo(s) (glab HTTPS; no SSH/signing)")
    names = ", ".join(r.name for r in bad[:3]) + (" …" if len(bad) > 3 else "")
    return Result(
        "git auth",
        WARN,
        detail=f"{len(bad)}/{len(scope)} repo(s) not token-only: {names}",
        hint="Apply the single-credential policy to every clone: edm git-policy --apply",
    )


def check_inventory() -> Result:
    n = inventory.load().get("count", 0)
    if n:
        return Result("inventory", OK, detail=f"{n} repos mapped")
    return Result("inventory", WARN, hint="Run: edm discover  (builds inventory/repos.json).")


def check_vaults() -> Result:
    # Kept non-fatal: no vaults is a perfectly valid state, and this check runs
    # from the SessionStart hook — it must never block a session.
    from .secrets import base, registry

    try:
        vs = registry.vaults()
    except base.VaultError as e:
        return Result("vaults", WARN, hint=str(e))
    if not vs:
        return Result("vaults", OK, detail="none configured")
    bad = []
    for name in vs:
        try:
            healthy, _ = registry.provider_for(name).health()
        except base.VaultError:
            healthy = False
        if not healthy:
            bad.append(name)
    if bad:
        return Result("vaults", WARN, detail=f"{len(vs)} configured",
                      hint="unhealthy: " + ", ".join(bad))
    return Result("vaults", OK, detail=f"{len(vs)} configured, all healthy")


def check_browser() -> Result:
    # Optional subsystem — WARN (never blocks) when its deps are missing.
    from .browser import runtime

    if not (shutil.which("node") and shutil.which("npx")):
        return Result("browser", WARN, hint="node/npx missing — needed for chrome-devtools-mcp "
                      "(`edm browser`). Optional.")
    chrome = runtime.chrome_binary()
    if not chrome:
        return Result("browser", WARN,
                      hint="no Chrome — `npx @puppeteer/browsers install chrome@stable` for `edm browser`.")
    if not runtime.chrome_for_testing() and not runtime.loads_extensions(chrome):
        return Result("browser", WARN, detail="branded Chrome only",
                      hint="secret autofill needs Chrome for Testing: "
                           "`npx @puppeteer/browsers install chrome@stable`.")
    return Result("browser", OK, detail="node + Chrome for Testing ready")


#: Order matters — cheap/local checks first, network checks last.
CHECKS = (
    check_python,
    check_git,
    check_glab,
    check_glab_auth,
    check_ssh,
    check_inventory,
    check_vaults,
    check_browser,
)


def run_all() -> list[Result]:
    return [check() for check in CHECKS]
