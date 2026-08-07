# `charter secret exec --dotenv` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `--dotenv` mode to `charter secret exec` that writes several secrets into one 0600 temp file in dotenv format and points an env var at its path, so tools that consume a dotenv secrets file (e.g. `PLAYWRIGHT_MCP_SECRETS_FILE`) can be fed from a charter vault without any value entering the model's context.

**Architecture:** `--dotenv` mirrors the existing `--file` flag exactly — same temp-file lifetime, same 0600 mode, same `--exec` incompatibility, same redaction of resolved values from captured output. The only new logic is a pure encoder that renders one `KEY=value` line per secret in a form the `dotenv` parser round-trips byte-for-byte. Repeating the flag with the same env-var name merges entries into a single file.

**Tech Stack:** Python ≥3.11, stdlib only (`os`, `tempfile`, `subprocess`), stdlib `unittest`.

## Global Constraints

- **Zero runtime dependencies.** stdlib only — no third-party packages, ever.
- **No secret value may be printed, logged, or placed in argv.** Values are resolved from the provider and written only to the 0600 temp file.
- **Every resolved value must be added to `secret_values`** so `base.redact()` scrubs it from captured stdout/stderr.
- **Tests must be hermetic** — no real vault, no network, no forge API. Use the existing `_StubProvider` pattern.
- **`--dotenv` is incompatible with `--exec`**, for the same reason `--file` is: exec replaces the process, so the temp file would never be cleaned up.
- Temp files are created with `tempfile.mkstemp` and `os.chmod(path, 0o600)`, and unlinked in a `finally` via `_safe_unlink`.
- Public behaviour is shared by `charter secret exec` and `charter persona secret exec` — both use `_sa_exec` in `charter/cli.py`.

## Encoding rule (verified, do not change without re-testing)

The parser on the consuming side is the `dotenv` package (Playwright bundles it; `dotenvFileLoader` calls `dotenv.parse`). It was tested empirically at v17.4.2 across 34 cases. **`dotenv` does NOT unescape `\"` or `\\` inside a double-quoted value** — it only expands `\n` and `\r`. Naive backslash-escaping therefore corrupts any secret containing a quote or backslash.

The rule that round-trips exactly:

| Value contains | Encoding |
| --- | --- |
| `'`, LF, or CR | double-quote; replace real CR→`\r`, LF→`\n`; **no other escaping** |
| anything else | single-quote verbatim; **no escaping at all** |

`dotenv`'s quoted-value match is greedy to the last quote on the line, so an embedded `"` inside a double-quoted value survives intact.

## File Structure

- `charter/commands_secrets.py` — add `_dotenv_line()` (pure encoder) and the `--dotenv` branch inside `cmd_secret_exec()`. This file already owns every secret-consuming command; the new flag belongs beside `--file`.
- `charter/cli.py` — one `add_argument` in `_sa_exec()`, which is shared by both `secret exec` and `persona secret exec`.
- `tests/test_secret_dotenv.py` — new; covers the encoder round-trip and the command wiring.
- `tests/test_secret_exec.py` — modify: the `_args` defaults dict gains `"dotenv": None`.
- `README.md` — document the flag and the playwright-cli recipe.

---

### Task 1: The dotenv encoder

**Files:**
- Modify: `charter/commands_secrets.py` (add `_dotenv_line` next to `_safe_unlink`)
- Test: `tests/test_secret_dotenv.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `_dotenv_line(name: str, value: str) -> str` — renders one dotenv line, without a trailing newline. Raises `ValueError` if `name` is not a valid env-var identifier. Task 2 calls this.

- [ ] **Step 1: Write the failing test**

Create `tests/test_secret_dotenv.py`:

```python
"""`charter secret exec --dotenv`: render several secrets into one dotenv file.

The consuming parser is the `dotenv` package (Playwright's
`dotenvFileLoader` calls `dotenv.parse`). Verified empirically at dotenv
17.4.2: it does NOT unescape `\\"` or `\\\\` inside a double-quoted value — it
expands only `\\n` and `\\r`. So backslash-escaping a quote or backslash
corrupts the value. These tests pin the encoding that actually round-trips.
"""

from __future__ import annotations

import unittest

from charter import commands_secrets


class DotenvLine(unittest.TestCase):
    """Every value must survive a dotenv parse byte-for-byte."""

    @staticmethod
    def _parse(line: str) -> str:
        """A faithful reimplementation of dotenv's single-line parse.

        Mirrors dotenv 17.x: a value wrapped in matching quotes is unwrapped
        greedily to the last quote; only inside double quotes are `\\n` and
        `\\r` expanded; no other escape is processed.
        """
        _, _, raw = line.partition("=")
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            body = raw[1:-1]
            if raw[0] == '"':
                body = body.replace("\\n", "\n").replace("\\r", "\r")
            return body
        return raw.strip()

    def test_plain_value_round_trips(self):
        line = commands_secrets._dotenv_line("PASS", "hunter2")
        self.assertEqual(self._parse(line), "hunter2")

    def test_value_with_double_quote_round_trips(self):
        """The case naive backslash-escaping corrupts."""
        line = commands_secrets._dotenv_line("PASS", 'a"b')
        self.assertEqual(self._parse(line), 'a"b')

    def test_value_with_backslash_round_trips(self):
        line = commands_secrets._dotenv_line("PASS", "a\\b")
        self.assertEqual(self._parse(line), "a\\b")

    def test_value_with_single_quote_round_trips(self):
        line = commands_secrets._dotenv_line("PASS", "it's")
        self.assertEqual(self._parse(line), "it's")

    def test_value_with_newline_round_trips(self):
        line = commands_secrets._dotenv_line("KEY", "line1\nline2")
        self.assertEqual(self._parse(line), "line1\nline2")

    def test_value_mixing_both_quotes_and_newline_round_trips(self):
        value = "a'b\"c\nd"
        line = commands_secrets._dotenv_line("KEY", value)
        self.assertEqual(self._parse(line), value)

    def test_empty_and_whitespace_values_round_trip(self):
        for value in ("", " ", "  lead", "trail  "):
            with self.subTest(value=value):
                line = commands_secrets._dotenv_line("K", value)
                self.assertEqual(self._parse(line), value)

    def test_special_characters_are_not_expanded(self):
        """`$`, `#` and `=` must stay literal, not be treated as syntax."""
        for value in ("a$bc", "${VAR}", "a#b", "a=b"):
            with self.subTest(value=value):
                line = commands_secrets._dotenv_line("K", value)
                self.assertEqual(self._parse(line), value)

    def test_line_has_no_trailing_newline(self):
        self.assertEqual(commands_secrets._dotenv_line("K", "v").count("\n"), 0)

    def test_rejects_invalid_env_var_name(self):
        for name in ("has-dash", "1leading", "has space", "", "has=eq"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    commands_secrets._dotenv_line(name, "v")

    def test_accepts_valid_env_var_names(self):
        for name in ("A", "_x", "PLAYWRIGHT_MCP_SECRETS_FILE", "K1_2"):
            with self.subTest(name=name):
                commands_secrets._dotenv_line(name, "v")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Users/aharon/IdeaProjects/charter && python3 -m unittest tests.test_secret_dotenv -v`

Expected: FAIL — `AttributeError: module 'charter.commands_secrets' has no attribute '_dotenv_line'`

- [ ] **Step 3: Write the implementation**

In `charter/commands_secrets.py`, add near `_safe_unlink` (keep `import re` with the other stdlib imports at the top of the file):

```python
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _dotenv_line(name: str, value: str) -> str:
    """Render one ``KEY=value`` dotenv line that ``dotenv.parse`` round-trips.

    The consuming parser is the ``dotenv`` package (Playwright's
    ``dotenvFileLoader`` calls ``dotenv.parse``). Verified empirically against
    dotenv 17.4.2 over 34 cases: it does **not** unescape ``\\"`` or ``\\\\``
    inside a double-quoted value — it expands only ``\\n`` and ``\\r``. So a
    value holding a quote or backslash must not be double-quoted with
    backslash escapes; it would parse back with the escapes intact and the
    credential would be silently wrong.

    The rule:
      * value contains ``'``, LF or CR -> double-quote it, encoding real
        CR as ``\\r`` and LF as ``\\n`` and nothing else;
      * otherwise -> single-quote it verbatim, with no escape processing.

    dotenv matches a quoted value greedily to the last quote on the line, so
    an embedded ``"`` inside a double-quoted value survives.
    """
    if not _ENV_NAME_RE.match(name):
        raise ValueError(
            f"'{name}' is not a valid environment-variable name "
            "(expected [A-Za-z_][A-Za-z0-9_]*)")
    if "'" in value or "\n" in value or "\r" in value:
        body = value.replace("\r", "\\r").replace("\n", "\\n")
        return f'{name}="{body}"'
    return f"{name}='{value}'"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /Users/aharon/IdeaProjects/charter && python3 -m unittest tests.test_secret_dotenv -v`

Expected: PASS — 11 tests OK

- [ ] **Step 5: Commit**

```bash
cd /Users/aharon/IdeaProjects/charter
git add charter/commands_secrets.py tests/test_secret_dotenv.py
git -c commit.gpgsign=false commit -m "feat(secrets): add dotenv line encoder that round-trips through dotenv.parse"
```

---

### Task 2: Wire `--dotenv` into `secret exec`

**Files:**
- Modify: `charter/commands_secrets.py` — `cmd_secret_exec()`
- Modify: `charter/cli.py` — `_sa_exec()`
- Modify: `tests/test_secret_exec.py` — `_args` defaults
- Test: `tests/test_secret_dotenv.py` (append a second test class)

**Interfaces:**
- Consumes: `_dotenv_line(name, value) -> str` from Task 1.
- Produces: the `--dotenv ENVVAR=NAME:key` flag. Repeating it with the **same** `ENVVAR` merges all entries into one file, in flag order. Different `ENVVAR`s produce separate files. Task 3 documents it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_secret_dotenv.py`. Add these to the imports at the top of the file:

```python
import io
import os
import tempfile
from contextlib import redirect_stderr
from types import SimpleNamespace
```

```python
class _StubProvider:
    """Stands in for a vault; records which keys were asked for."""

    def __init__(self, values: dict[str, str]):
        self.values = values
        self.asked: list[str] = []

    def get(self, key: str) -> str:
        self.asked.append(key)
        return self.values[key]


class DotenvExec(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = _StubProvider({"pw-user": "svc_qa",
                                       "pw-pass": 'p"ass\\word'})
        self._orig = commands_secrets._provider
        commands_secrets._provider = lambda _name: self.provider
        self.addCleanup(lambda: setattr(commands_secrets, "_provider", self._orig))
        self._td = tempfile.TemporaryDirectory()
        self.tmpdir = self._td.name
        self.addCleanup(self._td.cleanup)

    @staticmethod
    def _args(**kw):
        base = {"vault": "qa", "env": None, "file": None, "dotenv": None,
                "command": [], "exec_mode": False}
        base.update(kw)
        return SimpleNamespace(**base)

    def test_writes_one_file_with_all_entries(self):
        """Two --dotenv flags sharing an ENVVAR produce a single merged file."""
        rc = commands_secrets.cmd_secret_exec(self._args(
            dotenv=["SECRETS=USER:pw-user", "SECRETS=PASS:pw-pass"],
            command=["python3", "-c",
                     "import os;print(open(os.environ['SECRETS']).read(), end='')"]))
        self.assertEqual(rc, 0)
        self.assertEqual(self.provider.asked, ["pw-user", "pw-pass"])

    def test_file_contents_round_trip_and_are_0600(self):
        """The file parses back to the real values and is mode 0600.

        The child cannot print the values (they would be redacted, and
        printing a secret is exactly what this feature prevents), so it
        writes a SHA-256 of each parsed value to a scratch file the test
        owns. Comparing digests proves the round-trip without exposing
        anything.
        """
        import hashlib
        import json
        import tempfile as _tf

        out = os.path.join(self.tmpdir, "probe.json")
        child = (
            "import os,json,stat,hashlib\n"
            "p=os.environ['SECRETS']\n"
            "vals={}\n"
            "for line in open(p):\n"
            "    line=line.rstrip('\\n')\n"
            "    if not line: continue\n"
            "    k,_,raw=line.partition('=')\n"
            "    if len(raw)>=2 and raw[0]==raw[-1] and raw[0] in '\\\"\\'':\n"
            "        body=raw[1:-1]\n"
            "        if raw[0]=='\\\"': body=body.replace('\\\\n','\\n').replace('\\\\r','\\r')\n"
            "    else:\n"
            "        body=raw.strip()\n"
            "    vals[k]=hashlib.sha256(body.encode()).hexdigest()\n"
            f"json.dump({{'vals':vals,'mode':stat.S_IMODE(os.stat(p).st_mode)}},open({out!r},'w'))\n"
        )
        rc = commands_secrets.cmd_secret_exec(self._args(
            dotenv=["SECRETS=USER:pw-user", "SECRETS=PASS:pw-pass"],
            command=["python3", "-c", child]))
        self.assertEqual(rc, 0)
        probe = json.load(open(out))
        self.assertEqual(probe["mode"], 0o600)
        self.assertEqual(probe["vals"]["USER"],
                         hashlib.sha256(b"svc_qa").hexdigest())
        self.assertEqual(probe["vals"]["PASS"],
                         hashlib.sha256('p"ass\\word'.encode()).hexdigest())

    def test_temp_file_is_deleted_after_the_command(self):
        """No secrets file may outlive the child process.

        The *path* is not a secret, so the child may print it; the value
        inside is what redaction protects.
        """
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = commands_secrets.cmd_secret_exec(self._args(
                dotenv=["SECRETS=USER:pw-user"],
                command=["python3", "-c",
                         "import os;print(os.environ['SECRETS'])"]))
        self.assertEqual(rc, 0)
        path = buf.getvalue().strip()
        self.assertTrue(path, "child did not report the secrets-file path")
        self.assertFalse(os.path.exists(path),
                         f"secrets file survived the command: {path}")

    def test_rejects_spec_without_colon(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = commands_secrets.cmd_secret_exec(self._args(
                dotenv=["SECRETS=nocolon"], command=["true"]))
        self.assertEqual(rc, 2)
        self.assertIn("ENVVAR=NAME:key", buf.getvalue())

    def test_rejects_spec_without_equals(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = commands_secrets.cmd_secret_exec(self._args(
                dotenv=["NAME:key"], command=["true"]))
        self.assertEqual(rc, 2)
        self.assertIn("ENVVAR=NAME:key", buf.getvalue())

    def test_rejects_invalid_entry_name(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = commands_secrets.cmd_secret_exec(self._args(
                dotenv=["SECRETS=bad-name:pw-user"], command=["true"]))
        self.assertEqual(rc, 2)
        self.assertIn("bad-name", buf.getvalue())

    def test_refuses_to_combine_with_exec(self):
        """--exec would leak the temp file: the process is replaced."""
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = commands_secrets.cmd_secret_exec(self._args(
                dotenv=["SECRETS=USER:pw-user"], exec_mode=True,
                command=["true"]))
        self.assertEqual(rc, 2)
        self.assertIn("--dotenv", buf.getvalue())

    def test_values_are_redacted_from_output(self):
        """A child echoing the secret must not leak it to stdout."""
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = commands_secrets.cmd_secret_exec(self._args(
                dotenv=["SECRETS=PASS:pw-pass"],
                command=["python3", "-c",
                         "import os;print(open(os.environ['SECRETS']).read())"]))
        self.assertEqual(rc, 0)
        self.assertNotIn('p"ass\\word', buf.getvalue())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Users/aharon/IdeaProjects/charter && python3 -m unittest tests.test_secret_dotenv -v`

Expected: FAIL — the `DotenvExec` tests fail because `cmd_secret_exec` ignores `args.dotenv` (`test_rejects_spec_without_colon` returns 0, not 2).

- [ ] **Step 3: Implement the `--dotenv` branch**

In `charter/commands_secrets.py`, inside `cmd_secret_exec()`, change the `--exec` incompatibility guard to cover `--dotenv` too. Replace:

```python
    exec_mode = bool(getattr(args, "exec_mode", False))
    if exec_mode and (args.file or []):
        util.err("--file cannot be combined with --exec: exec replaces this "
                 "process, so the temp file would never be cleaned up. "
                 "Use --env for an exec'd command.")
        return 2
```

with:

```python
    exec_mode = bool(getattr(args, "exec_mode", False))
    dotenv_specs = list(getattr(args, "dotenv", None) or [])
    if exec_mode and ((args.file or []) or dotenv_specs):
        flag = "--file" if (args.file or []) else "--dotenv"
        util.err(f"{flag} cannot be combined with --exec: exec replaces this "
                 "process, so the temp file would never be cleaned up. "
                 "Use --env for an exec'd command.")
        return 2
```

Then, immediately after the existing `for spec in args.file or []:` loop and still inside the same `try:` block, add:

```python
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
```

Write this block exactly as shown — the `_safe_unlink` loops in the early returns are required, not optional.

- [ ] **Step 4: Add the CLI flag**

In `charter/cli.py`, inside `_sa_exec()`, after the `--file` argument:

```python
    p.add_argument("--dotenv", action="append", metavar="ENVVAR=NAME:key",
                   help="Add secret <key> as NAME to a temp 0600 dotenv file; set "
                        "ENVVAR to its path (repeatable — repeats sharing an "
                        "ENVVAR merge into one file). For tools that read a "
                        "dotenv secrets file, e.g. PLAYWRIGHT_MCP_SECRETS_FILE.")
```

- [ ] **Step 5: Update the existing exec tests' defaults**

In `tests/test_secret_exec.py`, in the `_args` static method, change the base dict to include the new key:

```python
        base = {"vault": "elastic-logs-master", "env": None, "file": None,
                "dotenv": None, "command": [], "exec_mode": False}
```

- [ ] **Step 6: Run the full suite**

Run: `cd /Users/aharon/IdeaProjects/charter && python3 -m unittest discover -s tests -q`

Expected: OK, with the new tests included and no regressions in `test_secret_exec`.

- [ ] **Step 7: Verify the flag end-to-end by hand**

Run:

```bash
cd /Users/aharon/IdeaProjects/charter
python3 -c "
from types import SimpleNamespace
from charter import commands_secrets as c
class P:
    def get(self, k): return {'u':'svc','p':'a\"b\\\\c'}[k]
c._provider = lambda _n: P()
rc = c.cmd_secret_exec(SimpleNamespace(vault='v', env=None, file=None,
    dotenv=['S=USER:u','S=PASS:p'], exec_mode=False,
    command=['python3','-c','import os;print(open(os.environ[\"S\"]).read(),end=\"\")']))
print('rc', rc)
"
```

Expected: prints `rc 0` and two redacted lines (`USER=<...>` / `PASS=<...>` with values replaced by the redaction marker) — confirming the file was written, read by the child, and the values scrubbed from captured output.

- [ ] **Step 8: Commit**

```bash
cd /Users/aharon/IdeaProjects/charter
git add charter/commands_secrets.py charter/cli.py tests/test_secret_dotenv.py tests/test_secret_exec.py
git -c commit.gpgsign=false commit -m "feat(secrets): secret exec --dotenv writes a merged 0600 dotenv file"
```

---

### Task 3: Document the flag and the playwright-cli recipe

**Files:**
- Modify: `README.md` (the secrets section)

**Interfaces:**
- Consumes: the `--dotenv ENVVAR=NAME:key` flag from Task 2.
- Produces: nothing code-facing.

- [ ] **Step 1: Find the secrets section**

Run: `cd /Users/aharon/IdeaProjects/charter && grep -n "secret exec" README.md`

Expected: one or more line numbers in the secrets documentation.

- [ ] **Step 2: Add the `--dotenv` documentation**

Add to `README.md` immediately after the existing `secret exec` documentation:

````markdown
#### Feeding a tool that wants a dotenv secrets file

Some tools take a *file* of secrets rather than env vars. `--dotenv` writes one
0600 temp file containing every entry you name, points an env var at its path,
and deletes it when the command exits — so no value is ever printed, stored, or
placed in argv.

```bash
charter secret exec qa \
  --dotenv PLAYWRIGHT_MCP_SECRETS_FILE=EASYDMARC_USER:platform-user \
  --dotenv PLAYWRIGHT_MCP_SECRETS_FILE=EASYDMARC_PASS:platform-pass \
  -- npx @playwright/cli@0.1.18 -s=login fill e3 EASYDMARC_PASS
```

Repeats sharing an env-var name merge into a single file, in flag order.
Different names produce separate files.

The value is never typed by the caller: the tool refers to the secret by the
**name** you gave it (`EASYDMARC_PASS`), and resolves it from the file. Any
value that does appear in captured output is redacted.

`--dotenv` cannot be combined with `--exec` — exec replaces this process, so
the temp file would never be cleaned up. Use `--env` for an exec'd command.
````

- [ ] **Step 3: Verify the documented command parses**

Run:

```bash
cd /Users/aharon/IdeaProjects/charter
python3 -m charter secret exec --help | grep -A4 -- --dotenv
```

Expected: the `--dotenv ENVVAR=NAME:key` entry appears with its help text.

- [ ] **Step 4: Commit**

```bash
cd /Users/aharon/IdeaProjects/charter
git add README.md
git -c commit.gpgsign=false commit -m "docs: document secret exec --dotenv and the playwright-cli recipe"
```

---

## Self-Review

**Spec coverage:** The one requirement — bridge a charter vault into a dotenv-consuming tool without exposing values — is covered by Task 1 (correct encoding), Task 2 (the flag, merging, cleanup, redaction, `--exec` guard), Task 3 (docs). Both `charter secret exec` and `charter persona secret exec` get the flag, since `_sa_exec` is shared.

**Placeholder scan:** No TBDs. Every code step carries complete code; every run step carries an exact command and expected output.

**Type consistency:** `_dotenv_line(name: str, value: str) -> str` is defined in Task 1 and called with that signature in Task 2. The flag spelling `ENVVAR=NAME:key` is identical in the parser, the error messages, the CLI help, and the README.

**Test strength:** every test asserts an observable outcome. The two that cannot read values directly (redaction would scrub them, and printing a secret is the thing this feature prevents) use side channels that expose no plaintext: a SHA-256 digest written to a test-owned scratch file proves the round-trip and the 0600 mode; the temp-file path — which is not itself a secret — proves cleanup. **Do not weaken redaction to make a test easier.**
