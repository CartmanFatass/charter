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


def _hooks_json() -> dict:
    return json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]


def _flat_hooks() -> list[tuple[str, dict]]:
    """(event, hook-dict) for every individual hook command declared in hooks.json —
    flattened across every matcher group of every event."""
    h = _hooks_json()
    return [(event, hook) for event, entries in h.items()
            for entry in entries for hook in entry["hooks"]]


class TestPluginManifest(unittest.TestCase):
    def test_manifest_exists_and_names_the_plugin(self):
        m = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(m["name"], "charter")
        self.assertTrue(m.get("description"))

    def test_hooks_json_declares_every_event_the_engine_implements(self):
        h = _hooks_json()
        for event in ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse"):
            self.assertIn(event, h, event)

    def test_every_hook_command_invokes_the_installed_cli(self):
        """Hooks must call the CLI on PATH, not a path inside the plugin or the user's
        control plane — the plugin ships no Python."""
        cmds = [hook["command"] for _, hook in _flat_hooks()]
        self.assertTrue(cmds)
        for c in cmds:
            self.assertIn("charter", c)

    def test_hook_engine_commands_carry_plugin_version(self):
        """Only the ``charter hook <name>`` dispatch path is skew-checked (see
        hooks.skew_message) — the other five commands (workspace/persona/doctor/
        gl-refresh) are raw CLI subcommands, not routed through that engine, and their
        subparsers don't accept --plugin-version at all, so tagging it on would make the
        hook fail outright."""
        engine_cmds = [hook["command"] for _, hook in _flat_hooks()
                       if "charter hook " in hook["command"]]
        self.assertEqual(len(engine_cmds), 5, engine_cmds)
        for c in engine_cmds:
            self.assertIn("--plugin-version", c)

    def test_sessionstart_carries_no_matcher(self):
        """An absent matcher matches startup|resume|clear|compact|fork. Pinning it to
        `startup` would silently drop the persona digest after the first /compact."""
        h = _hooks_json()
        for entry in h["SessionStart"]:
            self.assertNotIn("matcher", entry)

    def test_plugin_declares_all_ten_commands_the_control_plane_used_to_wire_by_hand(self):
        """Parity check against the pre-plugin wiring, enumerated explicitly rather than
        read from a sibling `easydmarc-umbrella` checkout's .claude/settings.json: charter
        is a public repo (github.com/diazoxide/charter) whose CI and other contributors
        never have that sibling checkout, so a test that stat()s another repo's absolute
        path would be unrunnable (or silently vacuous) everywhere but this machine. This
        list is that repo's 10 hook entries, translated 1:1 — 5 already went through the
        versioned `charter hook <name>` engine; this closes the other 5, which used to be
        raw `$CLAUDE_PROJECT_DIR/bin/edm <cmd>` invocations with no charter equivalent."""
        commands = [hook["command"] for _, hook in _flat_hooks()]
        expected_substrings = [
            "charter workspace _reconcile",          # SessionStart: reconcile workspace lock/pointer
            "charter persona _gc",                    # SessionStart: prune ended-session scratch memory
            "charter hook sessionstart --plugin-version",  # SessionStart: memory digest injection
            "charter doctor",                          # SessionStart: preflight, message only on failure
            "charter gl-refresh",                       # SessionStart: refresh forge state
            "charter hook userpromptsubmit --plugin-version",
            "charter hook pretooluse --plugin-version",
            "charter hook posttooluse --plugin-version",
            "charter hook posttooluse-dispatch --plugin-version",
            "charter workspace _autosave",              # Stop: debounced auto-save
        ]
        self.assertEqual(len(commands), len(expected_substrings), commands)
        for substr in expected_substrings:
            self.assertEqual(sum(1 for c in commands if substr in c), 1, substr)

    def test_async_hooks_stay_async(self):
        """persona _gc and gl-refresh are network-touching background calls at session
        start; losing `async` would make a session wait on them synchronously."""
        async_cmds = [hook["command"] for _, hook in _flat_hooks() if hook.get("async") is True]
        self.assertEqual(len(async_cmds), 2, async_cmds)
        self.assertTrue(any("persona _gc" in c for c in async_cmds))
        self.assertTrue(any("gl-refresh" in c for c in async_cmds))
        # and neither async command also carries a competing timeout
        for _, hook in _flat_hooks():
            if hook.get("async") is True:
                self.assertNotIn("timeout", hook)

    def test_timed_hooks_keep_their_original_timeout(self):
        """Losing a timeout can hang session start on a stuck subprocess."""
        expected = {
            "charter workspace _reconcile >/dev/null 2>&1": 5,
            "charter hook sessionstart --plugin-version 0.1.0": 5,
            "charter hook userpromptsubmit --plugin-version 0.1.0": 5,
            "charter hook pretooluse --plugin-version 0.1.0": 10,
            "charter hook posttooluse --plugin-version 0.1.0": 5,
            "charter hook posttooluse-dispatch --plugin-version 0.1.0": 5,
            "charter workspace _autosave >/dev/null 2>&1": 15,
        }
        by_cmd = {hook["command"]: hook for _, hook in _flat_hooks()}
        for cmd, timeout in expected.items():
            self.assertIn(cmd, by_cmd)
            self.assertEqual(by_cmd[cmd].get("timeout"), timeout, cmd)

    def test_doctor_preflight_keeps_its_fallback_and_status_message(self):
        """Quiet on success, prints the captured output only on failure (the `||`
        fallback) — and carries the statusMessage the umbrella showed during the check."""
        doctor = [hook for event, hook in _flat_hooks()
                 if event == "SessionStart" and "charter doctor" in hook["command"]]
        self.assertEqual(len(doctor), 1)
        cmd = doctor[0]["command"]
        self.assertIn("||", cmd)
        self.assertIn("charter doctor", cmd)
        self.assertEqual(doctor[0].get("timeout"), 20)
        self.assertTrue(doctor[0].get("statusMessage"))


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
