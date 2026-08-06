"""F1 (critical): a transient forge failure must never masquerade as "zero repos" and
wipe `inventory/repos.json`. Before this fix, `GitLabForge._api` returned `None` on any
failure, `list_repos` degraded that to `[]`, and `cmd_discover` happily wrote an empty
inventory and exited 0. This module pins the fix at the `cmd_discover` boundary: on a
forge failure, `inventory.save` must never be called at all.
"""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest import mock

from tests._isolation import PersonaIso
from charter import commands


def _proc(stdout="", stderr="", rc=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=rc)


def _args(**over):
    base = dict(no_probe=True, no_docs=True)
    base.update(over)
    return SimpleNamespace(**base)


class TestDiscoverNeverWipesOnFailure(PersonaIso):
    def setUp(self) -> None:
        super().setUp()
        # PersonaIso redirects config.ROOT etc. to a tmp dir, but not config.INVENTORY
        # (no existing test needed it). cmd_discover reads/writes it directly, so point
        # it at the same tmp tree — otherwise it resolves to the real repo's inventory.
        from charter import config
        self._orig_inventory = config.INVENTORY
        config.INVENTORY = self.tmp / "inventory" / "repos.json"
        self.addCleanup(setattr, config, "INVENTORY", self._orig_inventory)

    def test_cmd_discover_does_not_save_when_list_repos_fails(self):
        """The one that matters most: patch `inventory.save` directly and prove it is
        never reached when the forge call fails."""
        def side_effect(cmd, **kwargs):
            if "status" in cmd:
                return _proc("✓ Logged in to gitlab.com as me\n")  # auth is fine
            return _proc("", rc=1)  # the projects listing call fails

        with mock.patch("charter.util.run", side_effect=side_effect), \
             mock.patch("charter.inventory.save") as saved:
            with self.assertRaises(SystemExit):
                commands.cmd_discover(_args())
        saved.assert_not_called()

    def test_cmd_discover_leaves_an_existing_inventory_file_untouched(self):
        from charter import config
        inv = config.INVENTORY
        inv.parent.mkdir(parents=True, exist_ok=True)
        original = json.dumps({"group": "acme", "count": 1,
                               "repos": [{"name": "keep-me"}]})
        inv.write_text(original)

        def side_effect(cmd, **kwargs):
            if "status" in cmd:
                return _proc("✓ Logged in to gitlab.com as me\n")
            return _proc("", rc=1)

        with mock.patch("charter.util.run", side_effect=side_effect):
            with self.assertRaises(SystemExit):
                commands.cmd_discover(_args())
        self.assertEqual(inv.read_text(), original)

    def test_cmd_discover_still_saves_on_a_genuinely_empty_group(self):
        """The flip side: a real empty result is legal and must still write."""
        def side_effect(cmd, **kwargs):
            if "status" in cmd:
                return _proc("✓ Logged in to gitlab.com as me\n")
            return _proc("[]")  # zero projects, but a *successful* call

        with mock.patch("charter.util.run", side_effect=side_effect), \
             mock.patch("charter.inventory.save") as saved:
            rc = commands.cmd_discover(_args())
        self.assertEqual(rc, 0)
        saved.assert_called_once()

    def test_cmd_discover_saves_the_repos_on_success(self):
        payload = json.dumps([{
            "id": 1, "name": "svc", "path": "svc",
            "path_with_namespace": "acme/svc", "default_branch": "main",
            "description": "", "web_url": "https://gitlab.com/acme/svc",
            "ssh_url_to_repo": "git@gitlab.com:acme/svc.git", "topics": [],
        }])

        def side_effect(cmd, **kwargs):
            if "status" in cmd:
                return _proc("✓ Logged in to gitlab.com as me\n")
            return _proc(payload)

        with mock.patch("charter.util.run", side_effect=side_effect):
            rc = commands.cmd_discover(_args())
        self.assertEqual(rc, 0)

        from charter import inventory
        doc = inventory.load()
        self.assertEqual(doc["count"], 1)
        self.assertEqual(doc["repos"][0]["name"], "svc")


if __name__ == "__main__":
    unittest.main()
