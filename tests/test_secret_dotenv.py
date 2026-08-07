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


if __name__ == "__main__":
    unittest.main()
