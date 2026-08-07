"""The Claude Code plugin, and the one place a hook is allowed to shout.

Hooks swallow exceptions by design — a tally must never break a turn. Version skew is the
exception: a stale CLI would silently stop firing the gate while everything still looked
installed, which is the failure this guard exists to prevent."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from charter import __version__, hooks

ROOT = Path(__file__).resolve().parents[1]


class TestPluginManifest(unittest.TestCase):
    def test_manifest_exists_and_names_the_plugin(self):
        m = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(m["name"], "charter")
        self.assertTrue(m.get("description"))

    def test_hooks_json_declares_every_event_the_engine_implements(self):
        h = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
        for event in ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse"):
            self.assertIn(event, h, event)

    def test_every_hook_command_invokes_the_installed_cli(self):
        """Hooks must call the CLI on PATH, not a path inside the plugin or the user's
        control plane — the plugin ships no Python."""
        h = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
        cmds = [c["command"] for ent in h.values() for e in ent for c in e["hooks"]]
        self.assertTrue(cmds)
        for c in cmds:
            self.assertIn("charter", c)
            self.assertIn("--plugin-version", c)

    def test_sessionstart_carries_no_matcher(self):
        """An absent matcher matches startup|resume|clear|compact|fork. Pinning it to
        `startup` would silently drop the persona digest after the first /compact."""
        h = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
        for entry in h["SessionStart"]:
            self.assertNotIn("matcher", entry)


class TestVersionSkew(unittest.TestCase):
    def test_a_matching_version_is_silent(self):
        self.assertIsNone(hooks.skew_message(__version__))

    def test_a_newer_plugin_than_cli_says_exactly_what_to_run(self):
        msg = hooks.skew_message("99.0.0")
        self.assertIsNotNone(msg)
        self.assertIn("charter", msg)
        self.assertIn("upgrade", msg.lower())

    def test_an_absent_plugin_version_is_silent(self):
        """Someone invoking the hook by hand must not be nagged."""
        self.assertIsNone(hooks.skew_message(None))

    def test_a_malformed_version_does_not_crash(self):
        self.assertIsNone(hooks.skew_message("not-a-version"))


if __name__ == "__main__":
    unittest.main()
