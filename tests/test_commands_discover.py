"""F1 (critical): a transient forge failure must never masquerade as "zero repos" and
wipe `inventory/repos.json`. Before this fix, `GitLabForge._api` returned `None` on any
failure, `list_repos` degraded that to `[]`, and `cmd_discover` happily wrote an empty
inventory and exited 0. This module pins the fix at the `cmd_discover` boundary: on a
forge failure, `inventory.save` must never be called at all.
"""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr
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


class TestDiscoverStackProbeFailureIsVisible(PersonaIso):
    """FINDING I5 (Important) — `repo_tree` is the permissive best-effort API, so any
    probe failure (network hiccup, expired token, a GitHub secondary rate limit — 8
    repos probe concurrently, exactly what trips it) silently rewrote a repo's `stack`
    to "unknown", indistinguishable from a repo genuinely having no recognised
    root-level stack file. `discover` then saved the degraded inventory and exited 0
    with no visibility at all.

    CHOICE: warn loudly with an exact count rather than aborting the whole save — stack
    is best-effort DESCRIPTIVE metadata (unlike `list_repos`, which is load-bearing:
    losing a repo from the map is unacceptable, so F1 aborts on that). Aborting
    `discover` entirely on a handful of rate-limited probes (in a large org, especially
    on GitHub, under the existing 8-way concurrency) would make the tool unusable
    exactly when it's needed most. So: still save, but never let it be silent."""

    def test_probe_failure_still_saves_but_is_not_silently_unknown_and_undetectable(self):
        payload = json.dumps([{
            "id": 1, "name": "svc", "path": "svc",
            "path_with_namespace": "acme/svc", "default_branch": "main",
            "description": "", "web_url": "https://gitlab.com/acme/svc",
            "ssh_url_to_repo": "git@gitlab.com:acme/svc.git", "topics": [],
        }])

        def side_effect(cmd, **kwargs):
            if "status" in cmd:
                return SimpleNamespace(stdout="✓ Logged in to gitlab.com as me\n",
                                       stderr="", returncode=0)
            if "repository/tree" in cmd[-1]:
                return _proc("", rc=1)          # the stack probe itself fails
            return _proc(payload)               # the projects listing succeeds

        buf = io.StringIO()
        with mock.patch("charter.util.run", side_effect=side_effect), redirect_stderr(buf):
            rc = commands.cmd_discover(_args(no_probe=False))
        self.assertEqual(rc, 0)   # still saves — a chosen degradation, not an abort

        from charter import inventory
        doc = inventory.load()
        self.assertEqual(doc["count"], 1)
        self.assertEqual(doc["repos"][0]["stack"], "unknown")

        out = buf.getvalue()
        self.assertIn("1", out)
        self.assertTrue(any(w in out.lower() for w in ("probe", "fail")),
                        f"no loud warning about the probe failure in: {out!r}")

    def test_a_repo_that_genuinely_has_no_recognised_stack_prints_no_failure_warning(self):
        """The flip side — a SUCCESSFUL probe that legitimately finds nothing
        recognisable must not be confused with a failure."""
        payload = json.dumps([{
            "id": 1, "name": "svc", "path": "svc",
            "path_with_namespace": "acme/svc", "default_branch": "main",
            "description": "", "web_url": "https://gitlab.com/acme/svc",
            "ssh_url_to_repo": "git@gitlab.com:acme/svc.git", "topics": [],
        }])

        def side_effect(cmd, **kwargs):
            if "status" in cmd:
                return SimpleNamespace(stdout="✓ Logged in to gitlab.com as me\n",
                                       stderr="", returncode=0)
            if "repository/tree" in cmd[-1]:
                return _proc("[]")               # a real, successful, empty tree
            return _proc(payload)

        buf = io.StringIO()
        with mock.patch("charter.util.run", side_effect=side_effect), redirect_stderr(buf):
            rc = commands.cmd_discover(_args(no_probe=False))
        self.assertEqual(rc, 0)
        self.assertNotIn("probe failed", buf.getvalue().lower())
        self.assertNotIn("probe fail", buf.getvalue().lower())

    def test_build_repo_reports_probe_failed_distinct_from_a_genuine_unknown_stack(self):
        from types import SimpleNamespace as NS

        class FailingForge:
            kind = "gitlab"

            def repo_tree_strict(self, repo, ref=None):
                from charter.forge import ForgeError
                raise ForgeError("boom")

        class SucceedingButEmptyForge:
            kind = "gitlab"

            def repo_tree_strict(self, repo, ref=None):
                return []

        p = {"name": "x", "path_with_namespace": "acme/x", "ssh_url": "s",
             "default_branch": "main", "description": "", "topics": [], "web_url": ""}
        rec, failed = commands._build_repo(FailingForge(), p, no_probe=False)
        self.assertTrue(failed)
        self.assertEqual(rec["stack"], "unknown")

        rec2, failed2 = commands._build_repo(SucceedingButEmptyForge(), p, no_probe=False)
        self.assertFalse(failed2)
        self.assertEqual(rec2["stack"], "unknown")


class TestDiscoverProgressWordingIsForgeAccurate(PersonaIso):
    """FINDING I3 (part 3) — `cmd_discover` printed "Querying github group `acme`" —
    GitHub has orgs/users, not GitLab's groups. The progress line must say the right
    word for the forge actually being queried."""

    def setUp(self) -> None:
        super().setUp()
        (self.tmp / "charter.toml").write_text(
            'schema = 1\n\n[[forge]]\nkind = "github"\nowner = "acme-gh"\n')

    def test_querying_a_github_owner_says_org_not_group(self):
        def side_effect(cmd, **kwargs):
            if "status" in cmd:
                return SimpleNamespace(stdout="✓ Logged in to github.com as me\n",
                                       stderr="", returncode=0)
            return SimpleNamespace(stdout="[]", stderr="", returncode=0)

        buf = io.StringIO()
        with mock.patch("charter.util.run", side_effect=side_effect), redirect_stderr(buf):
            commands.cmd_discover(_args())
        out = buf.getvalue()
        self.assertIn("Querying github org `acme-gh`", out)
        self.assertNotIn("Querying github group", out)


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
