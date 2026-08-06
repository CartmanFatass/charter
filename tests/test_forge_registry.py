"""Resolving which forge a control plane — or an individual repo — uses.

A control plane may declare several `[[forge]]` blocks and track repos from all of them,
because organisations genuinely drift across forges."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from charter.forge import registry
from charter.forge.github import GitHubForge
from charter.forge.gitlab import GitLabForge


class TestForgesFor(unittest.TestCase):
    def test_reads_every_declared_forge_with_its_owner(self):
        cfg = {"forge": [
            {"kind": "gitlab", "host": "gitlab.com", "group": "acme"},
            {"kind": "github", "host": "github.com", "owner": "diazoxide"},
        ]}
        got = registry.forges_for(cfg)
        self.assertEqual([(f.kind, owner) for f, owner in got],
                         [("gitlab", "acme"), ("github", "diazoxide")])

    def test_group_and_owner_are_both_accepted(self):
        """GitLab calls it a group, GitHub an org or user — accept either key."""
        for key in ("group", "owner"):
            got = registry.forges_for({"forge": [{"kind": "gitlab", key: "acme"}]})
            self.assertEqual(got[0][1], "acme")

    def test_self_hosted_host_is_honoured(self):
        cfg = {"forge": [{"kind": "gitlab", "host": "git.internal", "group": "x"}]}
        self.assertEqual(registry.forges_for(cfg)[0][0].host, "git.internal")

    def test_no_forge_declared_is_empty_not_an_error(self):
        self.assertEqual(registry.forges_for({}), [])

    def test_an_unknown_kind_is_refused_by_name(self):
        with self.assertRaises(ValueError) as cm:
            registry.forges_for({"forge": [{"kind": "bitbucket", "owner": "x"}]})
        self.assertIn("bitbucket", str(cm.exception))


class TestForRepo(unittest.TestCase):
    def test_picks_the_backend_from_the_records_stamp(self):
        self.assertIsInstance(registry.for_repo({"forge": "github"}), GitHubForge)
        self.assertIsInstance(registry.for_repo({"forge": "gitlab"}), GitLabForge)

    def test_a_record_with_no_stamp_defaults_to_gitlab_for_back_compat(self):
        """Inventories written before the forge stamp existed have no `forge` key."""
        self.assertIsInstance(registry.for_repo({}), GitLabForge)


class TestForHost(unittest.TestCase):
    def test_infers_from_https_and_ssh_remotes(self):
        for url, kind in (
            ("https://github.com/a/b.git", "github"),
            ("git@github.com:a/b.git", "github"),
            ("https://gitlab.com/a/b.git", "gitlab"),
            ("ssh://git@gitlab.com/a/b.git", "gitlab"),
        ):
            self.assertEqual(registry.for_host(url).kind, kind, url)

    def test_an_unknown_host_is_none(self):
        self.assertIsNone(registry.for_host("https://example.com/a/b.git"))


class TestKnownForges(unittest.TestCase):
    """`known_forges`/`resolve_host` are the shared helper behind BOTH the SSH guard
    (`hooks._known_forges`) and forge resolution (`gitpolicy.forge_for`,
    `commands._origin_https`) — this is what lets a control plane's DECLARED self-hosted
    forge (not just gitlab.com/github.com) be recognised everywhere consistently, instead
    of a self-hosted host silently falling back to another forge's policy."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="edm-knownforges-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def test_with_no_charter_toml_only_the_registered_defaults_are_known(self):
        forges = registry.known_forges(self.root)
        self.assertEqual(set(forges), {"gitlab.com", "github.com"})

    def test_a_declared_self_hosted_host_widens_the_set(self):
        (self.root / "charter.toml").write_text(
            '[[forge]]\nkind = "gitlab"\nhost = "git.internal"\ngroup = "acme"\n')
        forges = registry.known_forges(self.root)
        self.assertIn("git.internal", forges)
        self.assertEqual(forges["git.internal"].kind, "gitlab")

    def test_a_malformed_charter_toml_never_raises_and_keeps_the_defaults(self):
        (self.root / "charter.toml").write_text("not [ valid toml")
        forges = registry.known_forges(self.root)   # must not raise
        self.assertEqual(set(forges), {"gitlab.com", "github.com"})

    def test_resolve_host_recognises_a_declared_self_hosted_host(self):
        (self.root / "charter.toml").write_text(
            '[[forge]]\nkind = "github"\nhost = "ghe.acme.com"\nowner = "acme"\n')
        forge = registry.resolve_host("https://ghe.acme.com/acme/api.git", self.root)
        self.assertIsNotNone(forge)
        self.assertEqual(forge.kind, "github")

    def test_resolve_host_still_recognises_the_registered_defaults(self):
        forge = registry.resolve_host("https://github.com/acme/api.git", self.root)
        self.assertEqual(forge.kind, "github")

    def test_resolve_host_is_none_for_a_genuinely_unknown_host(self):
        self.assertIsNone(
            registry.resolve_host("https://bitbucket.example.org/a/b.git", self.root))


class TestKnownKindsAgreeWithInventory(unittest.TestCase):
    def test_inventory_find_recognizes_every_registry_kind(self):
        """`inventory.find` accepts a `<forge>:` qualifier only for a *known* kind — it
        gets that list straight from `registry.KINDS` (a deferred import inside `find`,
        not a duplicated literal: nothing in the forge package imports back into
        `inventory` or `config`, so there is no cycle to avoid — see `find`'s own
        docstring in `charter/inventory.py`). This pins that every registry kind
        resolves as a qualifier, one repo record per kind."""
        from charter import inventory
        repos = [{"name": "api", "path_with_namespace": "acme/api", "forge": kind}
                 for kind in registry.KINDS]
        for kind in registry.KINDS:
            got = inventory.find(repos, f"{kind}:api")
            self.assertIsNotNone(got, f"{kind}: not recognized as a qualifier by inventory.find")
            self.assertEqual(got["forge"], kind)


if __name__ == "__main__":
    unittest.main()
