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


class TestDiscoverMultiForge(PersonaIso):
    """`discover` across several declared `[[forge]]` blocks — the F1 discipline
    (a forge failure must never masquerade as "zero repos" and wipe the inventory)
    extended to the multi-forge case, plus per-block `exclude` scoping."""

    def setUp(self) -> None:
        super().setUp()
        # PersonaIso isolates config.INVENTORY (see tests/_isolation.py); this class
        # also needs its OWN charter.toml on the isolated ROOT — the real fix for
        # cmd_discover is to re-read charter.toml fresh from config.ROOT each call
        # (see commands._instance_load_root) rather than trust the module-level
        # config._cfg cached at real-process import time, which a test redirecting
        # config.ROOT can never see.
        (self.tmp / "charter.toml").write_text(
            'schema = 1\n\n'
            '[[forge]]\nkind = "gitlab"\ngroup = "acme"\nexclude = ["shared-name"]\n\n'
            '[[forge]]\nkind = "github"\nowner = "acme-gh"\n'
        )

    @staticmethod
    def _gitlab_payload():
        return json.dumps([
            {"id": 1, "name": "api", "path": "api", "path_with_namespace": "acme/api",
             "default_branch": "main", "description": "", "web_url": "https://gitlab.com/acme/api",
             "ssh_url_to_repo": "git@gitlab.com:acme/api.git", "topics": []},
            {"id": 2, "name": "shared-name", "path": "shared-name",
             "path_with_namespace": "acme/shared-name", "default_branch": "main",
             "description": "", "web_url": "https://gitlab.com/acme/shared-name",
             "ssh_url_to_repo": "git@gitlab.com:acme/shared-name.git", "topics": []},
        ])

    @staticmethod
    def _github_payload():
        return json.dumps([
            {"id": 10, "name": "site", "full_name": "acme-gh/site", "default_branch": "main",
             "description": "", "html_url": "https://github.com/acme-gh/site",
             "ssh_url": "git@github.com:acme-gh/site.git", "topics": []},
            {"id": 11, "name": "shared-name", "full_name": "acme-gh/shared-name",
             "default_branch": "main", "description": "", "html_url": "https://github.com/acme-gh/shared-name",
             "ssh_url": "git@github.com:acme-gh/shared-name.git", "topics": []},
        ])

    def test_one_forge_failing_means_nothing_is_saved(self):
        """Even though gitlab (the first block) succeeds and builds a full batch,
        github (the second) failing must still refuse the WHOLE write — a partial
        multi-forge save is the same data-loss bug (F1's inventory-wipe) wearing a
        different hat: half the repos would silently vanish instead of all of them."""
        def side_effect(cmd, **kwargs):
            if cmd[0] == "glab":
                if "status" in cmd:
                    return _proc("✓ Logged in to gitlab.com as me\n")
                return _proc(self._gitlab_payload())
            if cmd[0] == "gh":
                return _proc("", rc=1)  # gh auth status fails outright
            return _proc("", rc=1)

        with mock.patch("charter.util.run", side_effect=side_effect), \
             mock.patch("charter.inventory.save") as saved:
            with self.assertRaises(SystemExit):
                commands.cmd_discover(_args())
        saved.assert_not_called()

    def test_each_forges_exclude_only_applies_to_its_own_batch(self):
        """gitlab's block excludes "shared-name"; github's block excludes nothing. If
        gitlab's exclude leaked onto github's batch (or vice versa), github's
        "shared-name" would be wrongly dropped too. It must survive, stamped `github`
        — and gitlab's own (excluded) copy must not collide with it."""
        def side_effect(cmd, **kwargs):
            if cmd[0] == "glab":
                if "status" in cmd:
                    return _proc("✓ Logged in to gitlab.com as me\n")
                return _proc(self._gitlab_payload())
            if cmd[0] == "gh":
                if "status" in cmd:
                    return _proc("✓ Logged in to github.com as me\n")
                return _proc(self._github_payload())
            return _proc("", rc=1)

        with mock.patch("charter.util.run", side_effect=side_effect):
            rc = commands.cmd_discover(_args())
        self.assertEqual(rc, 0)

        from charter import inventory
        doc = inventory.load()
        by_name = {r["name"]: r["forge"] for r in doc["repos"]}
        self.assertEqual(by_name, {"api": "gitlab", "site": "github", "shared-name": "github"})
        self.assertEqual(doc["count"], 3)


class TestDiscoverUnknownForgeKind(PersonaIso):
    """A typo'd `kind` in `[[forge]]` is a config mistake, not a crash — it must exit
    cleanly (SystemExit, message naming the bad kind), never surface as a raw
    ValueError traceback, and — like every other failure path here — never reach
    inventory.save."""

    def setUp(self) -> None:
        super().setUp()
        (self.tmp / "charter.toml").write_text(
            'schema = 1\n\n[[forge]]\nkind = "bitbucket"\nowner = "acme"\n'
        )

    def test_unknown_kind_exits_cleanly_without_saving(self):
        with mock.patch("charter.inventory.save") as saved:
            with self.assertRaises(SystemExit) as cm:
                commands.cmd_discover(_args())
        self.assertIn("bitbucket", str(cm.exception))
        saved.assert_not_called()


if __name__ == "__main__":
    unittest.main()
