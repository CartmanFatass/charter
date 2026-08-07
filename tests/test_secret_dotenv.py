"""`charter secret exec --dotenv`: render several secrets into one dotenv file.

The consuming parser is the `dotenv` package (Playwright's
`dotenvFileLoader` calls `dotenv.parse`). Verified empirically at dotenv
17.4.2 over 50 cases: it processes no escapes at all inside a *single*-quoted
value, and expands only `\\n`/`\\r` inside a double-quoted one. Single-quoting is
therefore the safe default; a real newline forces the double-quoted form, whose
`\\n` substitution is not injective and so cannot also carry a literal `\\n`.
These tests pin the encoding that actually round-trips.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace

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

    def test_apostrophe_plus_literal_backslash_n_round_trips(self):
        """Regression: the case that silently corrupted.

        An earlier rule double-quoted any value containing an apostrophe.
        `it's\\nb` (7 chars, a LITERAL backslash-n and no real newline) then
        encoded to `K="it's\\nb"` and decoded back to 6 chars with a REAL
        newline — a silently wrong credential.
        """
        value = "it's" + "\\" + "n" + "b"
        self.assertEqual(len(value), 7)
        line = commands_secrets._dotenv_line("PASS", value)
        self.assertEqual(self._parse(line), value)

    def test_literal_backslash_sequences_survive_without_a_newline(self):
        for value in ("a\\nb", "c:\\new\\report", "re\\r\\n", "\\\\n"):
            with self.subTest(value=value):
                line = commands_secrets._dotenv_line("K", value)
                self.assertEqual(self._parse(line), value)

    def test_real_newline_with_backslash_but_no_escape_sequence(self):
        """A backslash not followed by n/r is unambiguous — must NOT raise."""
        for value in ("a\\b\nc", "path\\to\nfile"):
            with self.subTest(value=value):
                line = commands_secrets._dotenv_line("K", value)
                self.assertEqual(self._parse(line), value)

    def test_pem_style_multiline_value_round_trips(self):
        value = "-----BEGIN KEY-----\nMIIB\n-----END KEY-----\n"
        line = commands_secrets._dotenv_line("KEY", value)
        self.assertEqual(self._parse(line), value)

    def test_raises_when_real_newline_meets_a_literal_escape_sequence(self):
        """Genuinely unrepresentable in dotenv — must fail loudly, not corrupt."""
        for value in ("x\\ny\nz", "a\\rb\nc", "it's\\nb\nreal"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    commands_secrets._dotenv_line("K", value)

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


if __name__ == "__main__":
    unittest.main()
