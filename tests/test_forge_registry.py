"""Resolving which forge a control plane — or an individual repo — uses.

A control plane may declare several `[[forge]]` blocks and track repos from all of them,
because organisations genuinely drift across forges.

FINDING I6: `registry.for_host` (and its `TestForHost` case, formerly here) has been
DELETED — it had zero production callers (`resolve_host` below is what every real
caller uses) and still carried the substring-match bug fixed elsewhere in this module
(`probe.host in url`, so a known host string appearing anywhere in the URL — including
inside the PATH — misresolved the forge; see `TestResolveHostMatchesHostComponentNotSubstring`).
Dead code pinned by tests looks alive and invites a future caller to re-land the bug —
deleting both closes that door. Anything that used to reach for `for_host` should use
`resolve_host` instead."""
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


class TestHostOfParsesTheHostComponent(unittest.TestCase):
    """FINDING 1 — the primitive the fix rests on: pull the HOST out of a git remote
    URL, never a substring match against the whole string."""

    def test_scheme_url_shapes(self):
        for url, host in (
            ("https://github.com/a/b.git", "github.com"),
            ("https://GitHub.COM/a/b.git", "github.com"),          # case-folded
            ("ssh://git@gitlab.com/a/b.git", "gitlab.com"),
            ("https://git.internal:8443/a/b.git", "git.internal"),  # port excluded
            ("https://git.internal/a/b.git?x=1", "git.internal"),   # query excluded
            ("https://git.internal/a/b.git#frag", "git.internal"),  # fragment excluded
        ):
            self.assertEqual(registry._host_of(url), host, url)

    def test_scp_style_shapes(self):
        for url, host in (
            ("git@github.com:a/b.git", "github.com"),
            ("git@GITHUB.COM:a/b.git", "github.com"),
            ("git.internal:a/b.git", "git.internal"),  # no user@ prefix
        ):
            self.assertEqual(registry._host_of(url), host, url)

    def test_the_live_reproduction_host_is_the_scp_host_not_the_path_substring(self):
        self.assertEqual(
            registry._host_of("git@git.internal:gitlab.com-mirror/api.git"), "git.internal")

    def test_empty_and_unparseable_input_is_a_blank_host_never_a_raise(self):
        self.assertEqual(registry._host_of(""), "")
        self.assertEqual(registry._host_of(None), "")
        self.assertEqual(registry._host_of("not a url at all"), "")


class TestResolveHostMatchesHostComponentNotSubstring(unittest.TestCase):
    """FINDING 1 — `resolve_host` used to substring-match the WHOLE url (``host in
    url``), so any known host string appearing ANYWHERE — path, query string, a
    branch-shaped ref — misresolved the forge. Reproduced live: a control plane
    declaring ``host = "git.internal"`` resolved
    ``git@git.internal:gitlab.com-mirror/api.git`` as GitLab's OWN gitlab.com forge
    (because ``gitlab.com`` appears in the PATH), so GitLab's credential.helper +
    insteadOf got written while the real ``git@git.internal:`` prefix was never
    rewritten — `charter git-policy` printed a false '✓ token-only'."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="edm-hostcomp-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        (self.root / "charter.toml").write_text(
            '[[forge]]\nkind = "gitlab"\nhost = "git.internal"\ngroup = "acme"\n')

    def test_the_exact_live_reproduction(self):
        forge = registry.resolve_host("git@git.internal:gitlab.com-mirror/api.git", self.root)
        self.assertIsNotNone(forge)
        self.assertEqual(forge.host, "git.internal")

    def test_host_in_the_url_path_does_not_misresolve(self):
        forge = registry.resolve_host(
            "https://git.internal/gitlab.com-mirror/api.git", self.root)
        self.assertEqual(forge.host, "git.internal")

    def test_host_in_a_query_string_does_not_misresolve(self):
        forge = registry.resolve_host(
            "https://git.internal/acme/api.git?ref=gitlab.com-mirror", self.root)
        self.assertEqual(forge.host, "git.internal")

    def test_host_in_a_branch_like_fragment_does_not_misresolve(self):
        forge = registry.resolve_host(
            "https://git.internal/acme/api.git#gitlab.com-feature-branch", self.root)
        self.assertEqual(forge.host, "git.internal")

    def test_a_genuinely_unrelated_host_with_a_known_name_in_its_path_stays_unmanaged(self):
        """No self-hosted declaration involved — a completely unrelated host merely
        having 'gitlab.com' inside its PATH must not resolve to GitLab either (this is
        the default-host half of the same substring bug, independent of charter.toml)."""
        forge = registry.resolve_host("https://example.com/gitlab.com-mirror/api.git", self.root)
        self.assertIsNone(forge)

    def test_declared_hosts_are_checked_before_class_defaults(self):
        """A control plane that (unusually) redeclares a default host's name under its
        own [[forge]] block gets the DECLARED forge, not the class default silently
        winning instead."""
        (self.root / "charter.toml").write_text(
            '[[forge]]\nkind = "github"\nhost = "gitlab.com"\nowner = "acme"\n')
        forge = registry.resolve_host("https://gitlab.com/acme/api.git", self.root)
        self.assertEqual(forge.kind, "github")


class TestKnownForgesPartialFailure(unittest.TestCase):
    """FINDING 3 (registry half) — a bad `[[forge]]` block (typo'd kind, wrong shape)
    sitting next to a GOOD one must not drop the good block's host too. Before the fix,
    `forges_for` raised on the FIRST bad block and `known_forges`'s bare `except
    Exception: pass` swallowed that whole-list failure — every declared host vanished,
    including the good one right next to the bad one."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="edm-partialfail-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def _write(self):
        (self.root / "charter.toml").write_text(
            '[[forge]]\nkind = "gitlab"\nhost = "git.internal"\ngroup = "acme"\n\n'
            '[[forge]]\nkind = "bitbucket-typo"\nhost = "bad.example.com"\nowner = "x"\n')

    def test_a_bad_block_does_not_discard_a_good_sibling_blocks_host(self):
        self._write()
        forges = registry.known_forges(self.root)
        self.assertIn("git.internal", forges, "the good block's host must survive")
        self.assertNotIn("bad.example.com", forges)
        self.assertIn("gitlab.com", forges)   # class defaults are untouched too
        self.assertIn("github.com", forges)

    def test_known_forges_report_surfaces_the_bad_blocks_error(self):
        self._write()
        forges, errors = registry.known_forges_report(self.root)
        self.assertIn("git.internal", forges)
        self.assertTrue(errors)
        self.assertIn("bitbucket-typo", " ".join(errors))

    def test_two_good_blocks_both_survive(self):
        (self.root / "charter.toml").write_text(
            '[[forge]]\nkind = "gitlab"\nhost = "git.internal"\ngroup = "acme"\n\n'
            '[[forge]]\nkind = "github"\nhost = "ghe.acme.com"\nowner = "acme"\n')
        forges = registry.known_forges(self.root)
        self.assertIn("git.internal", forges)
        self.assertIn("ghe.acme.com", forges)

    def test_a_wholly_malformed_charter_toml_still_keeps_only_the_defaults(self):
        (self.root / "charter.toml").write_text("not [ valid toml")
        forges, errors = registry.known_forges_report(self.root)
        self.assertEqual(set(forges), {"gitlab.com", "github.com"})
        self.assertTrue(errors)


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
