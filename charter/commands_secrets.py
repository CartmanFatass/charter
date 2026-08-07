"""``charter vault`` and ``charter secret`` command implementations.

The overriding rule here: **secret values must not leak into the model.**
- write paths take values via stdin/file, never argv (argv shows in ps/history);
- ``list`` shows keys only, ``get`` is masked by default;
- ``get --reveal`` refuses a non-interactive stdout (an agent) unless ``--force``;
- the real consumption paths are ``exec`` (inject + redact) and ``cp`` (0600 file).
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from . import config, util
from .secrets import base, registry


# --------------------------------------------------------------------------- #
# vault management                                                             #
# --------------------------------------------------------------------------- #
def cmd_vault_add(args) -> int:
    cfg: dict = {}
    if args.provider == "plain-file":
        cfg["file"] = args.file or str(config.VAULTS_DIR / f"{args.name}.json")
    elif args.file:
        cfg["file"] = args.file
    try:
        registry.add_vault(args.name, args.provider, cfg, persona=args.persona)
    except base.VaultError as e:
        util.err(str(e))
        return 1
    tag = f", persona: {args.persona}" if args.persona else ""
    util.ok(f"Vault '{args.name}' registered (provider: {args.provider}{tag}).")
    prov = registry.provider_for(args.name)
    ok, detail = prov.health()
    (util.info if ok else util.warn)(f"  {detail}")
    if not prov.available:
        util.warn(f"  provider '{args.provider}' is not implemented yet — registered for later use.")
    elif args.provider == "plain-file":
        util.info(f"  add secrets with: charter secret set {args.name} <key> --stdin")
    return 0


def cmd_vault_list(args) -> int:
    doc = registry.load_registry()
    vs = registry.vaults(doc)
    if not vs:
        util.info("No vaults configured. Add one: "
                  "charter vault add <name> --provider plain-file --file <path>")
        return 0
    fmt = "{:<18} {:<16} {:<12} {}"
    print(fmt.format("VAULT", "PROVIDER", "PERSONA", "STATUS"))
    print(fmt.format("-" * 18, "-" * 16, "-" * 12, "-" * 28))
    for name in sorted(vs):
        try:
            ok, detail = registry.provider_for(name, doc).health()
        except base.VaultError as e:
            detail = str(e)
        persona = vs[name].get("persona") or "—"
        print(fmt.format(name, vs[name].get("provider", "?"), persona, detail))
    return 0


def cmd_vault_remove(args) -> int:
    try:
        registry.remove_vault(args.name)
    except base.VaultError as e:
        util.err(str(e))
        return 1
    util.ok(f"Vault '{args.name}' removed from the registry. "
            "(Any underlying file is left on disk untouched.)")
    return 0


# --------------------------------------------------------------------------- #
# secret read / write                                                          #
# --------------------------------------------------------------------------- #
def _provider(name: str):
    prov = registry.provider_for(name)
    if not prov.available:
        raise base.ProviderUnavailable(
            f"vault '{name}' uses provider '{prov.id}', which is not implemented yet."
        )
    return prov


def _read_value(args) -> str:
    """Obtain a secret value without exposing it on the command line."""
    if args.from_file:
        return Path(args.from_file).expanduser().read_text()
    if args.value is not None:
        util.warn("Value passed via --value is visible in shell history / process list; "
                  "prefer --stdin or --from-file.")
        return args.value
    if args.stdin or not sys.stdin.isatty():
        data = sys.stdin.read()
        return data[:-1] if data.endswith("\n") else data  # strip one trailing newline
    import getpass
    return getpass.getpass(f"Value for '{args.key}' (hidden): ")


def cmd_secret_set(args) -> int:
    try:
        prov = _provider(args.vault)
        value = _read_value(args)
        if value == "":
            util.warn("Storing an empty value.")
        prov.set(args.key, value)
    except base.VaultError as e:
        util.err(str(e))
        return 1
    util.ok(f"Set '{args.key}' in vault '{args.vault}' ({len(value)} bytes). Value not shown.")
    return 0


def cmd_secret_list(args) -> int:
    try:
        keys = _provider(args.vault).keys()
    except base.VaultError as e:
        util.err(str(e))
        return 1
    if not keys:
        util.info(f"Vault '{args.vault}' has no secrets.")
        return 0
    for k in keys:
        print(k)
    return 0


def cmd_secret_audit(args) -> int:
    """Flag secrets older than --days as stale (rotation hygiene). Providers that manage
    rotation externally (1Password, Vault) report nothing to do here."""
    try:
        prov = _provider(args.vault)
    except base.VaultError as e:
        util.err(str(e))
        return 1
    ages_fn = getattr(prov, "ages", None)
    if not callable(ages_fn):
        util.info(f"vault '{args.vault}' ({prov.id}) manages rotation externally — no age tracking.")
        return 0
    ages = ages_fn()
    if not ages:
        util.info(f"vault '{args.vault}' has no secrets.")
        return 0
    stale = sorted(((k, d) for k, d in ages.items() if d is not None and d >= args.days),
                   key=lambda x: -x[1])
    unknown = sorted(k for k, d in ages.items() if d is None)
    for k, d in stale:
        util.warn(f"{args.vault}/{k}: {d} days old — consider rotating")
    if not stale:
        util.ok(f"no secrets in '{args.vault}' older than {args.days} days.")
    if unknown:
        util.info(f"age unknown (set before tracking): {', '.join(unknown)}")
    return 1 if stale else 0


def cmd_secret_get(args) -> int:
    try:
        value = _provider(args.vault).get(args.key)
    except base.VaultError as e:
        util.err(str(e))
        return 1

    if not args.reveal:
        digest = hashlib.sha256(value.encode()).hexdigest()[:12]
        print(f"{args.vault}/{args.key}: present · {len(value)} bytes · sha256:{digest}")
        print("(value hidden — use `charter secret exec`/`cp` to consume it, or --reveal to print)")
        return 0

    # --reveal: only for a human at an interactive terminal. Refuse the exact
    # channel (piped stdout) through which a secret would reach an agent's context.
    if not sys.stdout.isatty() and not args.force:
        util.err(
            "Refusing to print a secret to non-interactive stdout — this is how it would "
            "leak into an agent's context. Use `charter secret exec`/`cp` to consume it safely, "
            "or pass --force if you truly intend to print it."
        )
        return 2
    util.warn("Revealing secret plaintext to this terminal.")
    sys.stdout.write(value if value.endswith("\n") else value + "\n")
    return 0


def cmd_secret_rm(args) -> int:
    try:
        _provider(args.vault).delete(args.key)
    except base.VaultError as e:
        util.err(str(e))
        return 1
    util.ok(f"Removed '{args.key}' from vault '{args.vault}'.")
    return 0


def cmd_secret_cp(args) -> int:
    """Materialize a secret to a file at 0600 (e.g. a kubeconfig). Prints path only."""
    try:
        value = _provider(args.vault).get(args.key)
    except base.VaultError as e:
        util.err(str(e))
        return 1
    dest = Path(args.dest).expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value)
    os.chmod(dest, 0o600)
    util.ok(f"Wrote '{args.vault}/{args.key}' to {dest} (0600). Value not shown.")
    return 0


def cmd_secret_exec(args) -> int:
    """Run a command with secrets injected as env vars and/or files, then redact.

    The model constructs the command using env-var *names* and never sees any
    value; charter resolves the secrets at runtime and scrubs them from the output.

    With ``--exec`` the command *replaces* this process (``os.execvpe``) instead
    of being captured. Capturing buffers stdout until exit, which deadlocks any
    long-running or streaming child — an MCP stdio server never completes its
    handshake. Exec'ing inherits fds 0/1/2 so the stream is untouched; the
    trade-off is that nothing is captured, so nothing can be redacted.
    """
    try:
        prov = _provider(args.vault)
    except base.VaultError as e:
        util.err(str(e))
        return 1

    command = list(args.command or [])
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        util.err("No command given. Usage: "
                 "charter secret exec <vault> --env NAME=key -- <command...>")
        return 2

    exec_mode = bool(getattr(args, "exec_mode", False))
    dotenv_specs = list(getattr(args, "dotenv", None) or [])
    if exec_mode and ((args.file or []) or dotenv_specs):
        flags = " and ".join(f for f, on in (("--file", args.file or []),
                                             ("--dotenv", dotenv_specs)) if on)
        util.err(f"{flags} cannot be combined with --exec: exec replaces this "
                 "process, so the temp file would never be cleaned up. "
                 "Use --env for an exec'd command.")
        return 2

    env = dict(os.environ)
    secret_values: list[str] = []
    tmpfiles: list[str] = []
    try:
        for spec in args.env or []:
            name, sep, key = spec.partition("=")
            if not sep or not name:
                util.err(f"--env expects NAME=key, got '{spec}'")
                return 2
            val = prov.get(key)
            env[name] = val
            secret_values.append(val)
        for spec in args.file or []:
            name, sep, key = spec.partition("=")
            if not sep or not name:
                util.err(f"--file expects ENVVAR=key, got '{spec}'")
                return 2
            val = prov.get(key)
            secret_values.append(val)
            fd, path = tempfile.mkstemp(prefix=f"charter-{args.vault}-{key}-")
            os.write(fd, val.encode())
            os.close(fd)
            os.chmod(path, 0o600)
            env[name] = path
            tmpfiles.append(path)

        # --dotenv ENVVAR=NAME:key (repeatable). Entries sharing an ENVVAR are
        # merged into one file, in flag order, so a consumer that wants several
        # secrets (e.g. PLAYWRIGHT_MCP_SECRETS_FILE) gets exactly one path.
        # Every early return here must unlink tmpfiles itself: the `finally`
        # that cleans them up guards only the subprocess.run call below, which
        # these returns never reach. A leaked 0600 secrets file is the exact
        # failure this feature exists to prevent.
        grouped: dict[str, list[tuple[str, str]]] = {}
        for spec in dotenv_specs:
            envvar, sep, entry = spec.partition("=")
            name, csep, key = entry.partition(":")
            if not sep or not envvar or not csep or not name or not key:
                for p in tmpfiles:
                    _safe_unlink(p)
                util.err(f"--dotenv expects ENVVAR=NAME:key, got '{spec}'")
                return 2
            if any(n == name for n, _ in grouped.get(envvar, ())):
                for p in tmpfiles:
                    _safe_unlink(p)
                util.err(f"--dotenv defines '{name}' twice for {envvar}; "
                         "which value wins would be up to the reader of the "
                         "file. Use one entry per name.")
                return 2
            grouped.setdefault(envvar, []).append((name, key))

        for envvar, entries in grouped.items():
            lines = []
            for name, key in entries:
                val = prov.get(key)
                secret_values.append(val)
                try:
                    lines.append(_dotenv_line(name, val))
                except ValueError as e:
                    for p in tmpfiles:
                        _safe_unlink(p)
                    util.err(str(e))
                    return 2
            fd, path = tempfile.mkstemp(prefix=f"charter-{args.vault}-dotenv-")
            os.write(fd, ("\n".join(lines) + "\n").encode())
            os.close(fd)
            os.chmod(path, 0o600)
            env[envvar] = path
            tmpfiles.append(path)
    except base.VaultError as e:
        for p in tmpfiles:
            _safe_unlink(p)
        util.err(str(e))
        return 1

    if exec_mode:
        # Replaces this process: stdio is inherited untouched, so a streaming
        # child (MCP stdio server, REPL, tail -f) works. Never returns on success.
        try:
            os.execvpe(command[0], command, env)
        except OSError:
            util.err(f"command not found: {command[0]}")
            return 127
        return 0  # unreachable: execvpe replaces the process or raises. Never
                  # fall through to the capturing path below — that would run
                  # the command a second time.

    try:
        proc = subprocess.run(command, env=env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        util.err(f"command not found: {command[0]}")
        return 127
    finally:
        for p in tmpfiles:
            _safe_unlink(p)

    out = base.redact(proc.stdout, secret_values)
    err = base.redact(proc.stderr, secret_values)
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err)
    return proc.returncode


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ESCAPE_SEQ_RE = re.compile(r"\\[nr]")


def _dotenv_line(name: str, value: str) -> str:
    """Render one ``KEY=value`` dotenv line that ``dotenv.parse`` round-trips.

    The consuming parser is the ``dotenv`` package (Playwright's
    ``dotenvFileLoader`` calls ``dotenv.parse``). Verified empirically against
    dotenv 17.4.2 over 50 cases. Two of its properties drive this encoding:

    * It does **not** unescape ``\\"`` or ``\\\\`` anywhere, and processes no
      escape at all inside a *single*-quoted value.
    * It matches a quoted value greedily to the last quote on the line, so an
      embedded quote of either kind survives.

    Single-quoting is therefore the safe default: it carries ``'``, ``"``,
    ``\\`` and a literal ``\\n`` through untouched. Only a real newline forces
    the double-quoted form, and there the ``\\n`` substitution is **not
    injective** — a value already containing the two characters ``\\`` + ``n``
    would come back as a real newline. That case is genuinely unrepresentable
    in dotenv, so it raises rather than silently corrupting a credential.
    """
    if not _ENV_NAME_RE.match(name):
        raise ValueError(
            f"'{name}' is not a valid environment-variable name "
            "(expected [A-Za-z_][A-Za-z0-9_]*)")
    if "\n" not in value and "\r" not in value:
        return f"{name}='{value}'"
    if _ESCAPE_SEQ_RE.search(value):
        raise ValueError(
            f"the secret for '{name}' contains both a real newline and a "
            "literal '\\n'/'\\r' sequence, which dotenv cannot represent "
            "unambiguously (it has no backslash escape). Store the value "
            "base64-encoded, or without the literal sequence.")
    body = value.replace("\r", "\\r").replace("\n", "\\n")
    return f'{name}="{body}"'
