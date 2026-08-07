"""`charter init` — scaffold a control plane, additively.

The write policy is the whole point: it must be safe to run in a directory that already
has a .claude/ setup, a .gitignore, and a status line the user configured themselves."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from charter import commands, config, instance


class InitIso(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="charter-init-")).resolve()

    def _init(self, **kw):
        args = SimpleNamespace(forge=kw.get("forge", "gitlab"),
                               owner=kw.get("owner", "acme"),
                               host=kw.get("host", None))
        with mock.patch.object(config, "ROOT", self.root):
            return commands.cmd_init(args)


class TestFreshDirectory(InitIso):
    def test_creates_a_working_control_plane(self):
        self.assertEqual(self._init(), 0)
        self.assertTrue((self.root / "charter.toml").is_file())
        self.assertEqual(instance.drift(self.root), [])

    def test_the_written_config_is_valid_and_declares_the_forge(self):
        self._init(forge="github", owner="diazoxide")
        cfg = instance.load(self.root)
        self.assertEqual(cfg["schema"], instance.SCHEMA)
        self.assertEqual(cfg["forge"][0]["kind"], "github")
        self.assertEqual(cfg["forge"][0]["owner"], "diazoxide")

    def test_memory_defaults_to_local_in_the_written_config(self):
        """A fresh control plane must not publish agent notes by accident."""
        self._init()
        self.assertEqual(instance.share_of(instance.load(self.root)), "local")

    def test_gitignore_excludes_workspaces_and_the_secrets_home(self):
        self._init()
        body = (self.root / ".gitignore").read_text()
        self.assertIn("workspaces/", body)
        self.assertIn(".edm/", body)

    def test_writes_a_statusline_when_none_is_configured(self):
        self._init()
        s = json.loads((self.root / ".claude" / "settings.json").read_text())
        self.assertIn("charter statusline", s["statusLine"]["command"])


class TestAdditiveOnly(InitIso):
    def test_an_existing_charter_toml_is_never_overwritten(self):
        (self.root / "charter.toml").write_text('schema = 1\n# mine\n')
        self._init()
        self.assertIn("# mine", (self.root / "charter.toml").read_text())

    def test_an_existing_statusline_is_left_alone(self):
        d = self.root / ".claude"
        d.mkdir()
        (d / "settings.json").write_text(json.dumps(
            {"statusLine": {"type": "command", "command": "my-own-thing"},
             "permissions": {"allow": ["Bash(ls)"]}}))
        self._init()
        s = json.loads((d / "settings.json").read_text())
        self.assertEqual(s["statusLine"]["command"], "my-own-thing")

    def test_unrelated_settings_keys_survive(self):
        d = self.root / ".claude"
        d.mkdir()
        (d / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}}))
        self._init()
        s = json.loads((d / "settings.json").read_text())
        self.assertEqual(s["permissions"]["allow"], ["Bash(ls)"])

    def test_existing_gitignore_lines_are_kept(self):
        (self.root / ".gitignore").write_text("node_modules/\n")
        self._init()
        self.assertIn("node_modules/", (self.root / ".gitignore").read_text())

    def test_is_idempotent(self):
        self.assertEqual(self._init(), 0)
        self.assertEqual(self._init(), 0)

    def test_malformed_existing_settings_json_does_not_crash_or_clobber(self):
        d = self.root / ".claude"
        d.mkdir()
        (d / "settings.json").write_text("{ not json")
        self._init()
        self.assertEqual((d / "settings.json").read_text(), "{ not json")


if __name__ == "__main__":
    unittest.main()
