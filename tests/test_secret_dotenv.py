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
