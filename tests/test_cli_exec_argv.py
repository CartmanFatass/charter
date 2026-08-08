"""`secret exec … -- <command>` must parse identically on every supported Python.

This is the gap that let a flagship bug ship: every other secret-exec test calls
``cmd_secret_exec`` directly with a SimpleNamespace, so none of them crossed
argparse. CI was green on 3.11–3.14 while the actual CLI was broken on 3.11 —
charter's own declared floor.

On 3.11 argparse cannot hold a ``nargs="*"`` positional that follows repeated
optionals, so

    charter secret exec <vault> --env NAME=key -- kubectl get pods

died with "unrecognized arguments". 3.12 parses it. `_split_exec_command` peels
the trailing command off before argparse sees it, making every version agree.

Assert on the parsed argv, never on the Python version — a test that skips on
3.12+ would stop protecting the platform most people actually run.
"""

from __future__ import annotations

import unittest

from charter.cli import _split_exec_command, build_parser


def _parse(argv: list[str]):
    rest, tail = _split_exec_command(list(argv))
    ns = build_parser().parse_args(rest)
    if tail is not None:
        ns.command = tail
    return ns


class ExecArgvSplit(unittest.TestCase):
    def test_env_flag_before_a_double_dash_command(self):
        ns = _parse(["secret", "exec", "v", "--env", "A=b", "--", "printenv", "A"])
        self.assertEqual(ns.env, ["A=b"])
        self.assertEqual(ns.command, ["printenv", "A"])

    def test_dotenv_flag_before_a_double_dash_command(self):
        ns = _parse(["secret", "exec", "v", "--dotenv", "F=N:k", "--", "sh", "-c", "x"])
        self.assertEqual(ns.dotenv, ["F=N:k"])
        self.assertEqual(ns.command, ["sh", "-c", "x"])

    def test_several_flags_together(self):
        ns = _parse(["secret", "exec", "v", "--env", "A=b", "--env", "C=d",
                     "--dotenv", "F=N:k", "--", "run", "it"])
        self.assertEqual(ns.env, ["A=b", "C=d"])
        self.assertEqual(ns.dotenv, ["F=N:k"])
        self.assertEqual(ns.command, ["run", "it"])

    def test_the_child_keeps_its_own_flags(self):
        """Everything after the first -- belongs to the child, dashes included."""
        ns = _parse(["secret", "exec", "v", "--env", "A=b", "--",
                     "kubectl", "get", "pods", "--all-namespaces", "-o", "json"])
        self.assertEqual(ns.command,
                         ["kubectl", "get", "pods", "--all-namespaces", "-o", "json"])

    def test_a_child_double_dash_is_not_a_second_split(self):
        ns = _parse(["secret", "exec", "v", "--", "git", "log", "--", "path"])
        self.assertEqual(ns.command, ["git", "log", "--", "path"])

    def test_no_flags_still_works(self):
        self.assertEqual(_parse(["secret", "exec", "v", "--", "echo", "hi"]).command,
                         ["echo", "hi"])

    def test_persona_secret_exec_is_split_too(self):
        ns = _parse(["persona", "secret", "exec", "--env", "A=b", "--", "printenv", "A"])
        self.assertEqual(ns.env, ["A=b"])
        self.assertEqual(ns.command, ["printenv", "A"])

    def test_exec_mode_flag_survives_the_split(self):
        ns = _parse(["secret", "exec", "v", "--env", "A=b", "--exec", "--", "server"])
        self.assertTrue(ns.exec_mode)
        self.assertEqual(ns.command, ["server"])

    # --- the split must not touch anything else ---
    def test_unrelated_subcommands_are_untouched(self):
        for argv in (["persona", "list"], ["workspace", "create", "w", "--live"],
                     ["clone", "a", "b"], ["secret", "list", "v"]):
            with self.subTest(argv=argv):
                rest, tail = _split_exec_command(list(argv))
                self.assertEqual(rest, argv)
                self.assertIsNone(tail)

    def test_exec_without_a_double_dash_is_left_to_argparse(self):
        rest, tail = _split_exec_command(["secret", "exec", "v", "--env", "A=b"])
        self.assertIsNone(tail)
        self.assertEqual(rest, ["secret", "exec", "v", "--env", "A=b"])

    def test_a_double_dash_inside_the_prefix_is_not_a_split_point(self):
        """`--` must be looked for after the subcommand words, not before."""
        rest, tail = _split_exec_command(["secret", "--", "exec", "v"])
        self.assertIsNone(tail)
        self.assertEqual(rest, ["secret", "--", "exec", "v"])


if __name__ == "__main__":
    unittest.main()
