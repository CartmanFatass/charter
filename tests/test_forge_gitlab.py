"""GitLab over `glab`. The API is stubbed: CI has no credentials, and a test that hits
the network is a flaky test."""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest import mock

from charter.forge import base
from charter.forge.gitlab import GitLabForge


def _proc(stdout="", rc=0):
    return SimpleNamespace(stdout=stdout, stderr="", returncode=rc)


class TestGitLabForge(unittest.TestCase):
    def setUp(self) -> None:
        self.f = GitLabForge()

    def test_identity(self):
        self.assertEqual((self.f.kind, self.f.host, self.f.cli, self.f.change_sigil),
                         ("gitlab", "gitlab.com", "glab", "!"))

    def test_owner_noun_is_group(self):
        """FINDING I3 (part 3) — GitLab has 'groups'; GitHub has 'orgs'/'users'. Naming
        the wrong one is user-visible (a status message, a generated doc heading)."""
        self.assertEqual(self.f.owner_noun, "group")

    def test_satisfies_the_protocol(self):
        self.assertIsInstance(self.f, base.Forge)

    def test_credential_helper_uses_glab_not_ssh(self):
        h = self.f.credential_helper()
        self.assertIn("glab", h)
        self.assertNotIn("ssh", h.lower())

    def test_insteadof_covers_both_ssh_forms(self):
        https, ssh_forms = self.f.insteadof()
        self.assertEqual(https, "https://gitlab.com/")
        self.assertEqual(set(ssh_forms),
                         {"git@gitlab.com:", "ssh://git@gitlab.com/"})

    def test_list_repos_normalizes_and_stamps_the_forge(self):
        payload = json.dumps([{
            "id": 7, "name": "api", "path": "api",
            "path_with_namespace": "acme/api", "default_branch": "main",
            "description": "d", "web_url": "w", "ssh_url_to_repo": "s",
            "topics": ["t"],
        }])
        with mock.patch("charter.util.run", return_value=_proc(payload)):
            repos = self.f.list_repos("acme")
        self.assertEqual(len(repos), 1)
        for k in base.REPO_KEYS:
            self.assertIn(k, repos[0], k)
        self.assertEqual(repos[0]["forge"], "gitlab")
        self.assertEqual(repos[0]["path_with_namespace"], "acme/api")

    def test_list_repos_returns_empty_for_a_genuinely_empty_group(self):
        """A group with zero projects is a legal, successful result — must NOT be
        confused with a failed call (that's F1)."""
        with mock.patch("charter.util.run", return_value=_proc("[]")):
            self.assertEqual(self.f.list_repos("acme"), [])

    def test_list_repos_raises_on_page1_failure_rather_than_returning_empty(self):
        """F1: a transient failure on the very first page must never look like "the
        group has no repos" — that's how an expired token wipes the inventory."""
        with mock.patch("charter.util.run", return_value=_proc("", rc=1)):
            with self.assertRaises(base.ForgeError):
                self.f.list_repos("acme")

    def test_list_repos_error_names_the_owner(self):
        with mock.patch("charter.util.run", return_value=_proc("", rc=1)):
            with self.assertRaises(base.ForgeError) as cm:
                self.f.list_repos("acme-group")
        self.assertIn("acme-group", str(cm.exception))

    def test_list_repos_raises_on_mid_pagination_failure_rather_than_truncating(self):
        """F1: page 1 succeeds (a full 100-item page, so pagination continues), page 2
        fails — the old `or []` behavior would silently stop and return only page 1."""
        page1 = json.dumps([
            {"id": i, "name": f"repo{i}", "path": f"repo{i}",
             "path_with_namespace": f"acme/repo{i}", "default_branch": "main"}
            for i in range(100)
        ])

        def side_effect(cmd, **kwargs):
            # NB: match on "&page=1" (not "page=1") — "per_page=100" also contains the
            # substring "page=1" (from "page=100"), which would otherwise make every
            # page look like page 1 and loop forever.
            if "&page=1" in cmd[-1]:
                return _proc(page1)
            return _proc("", rc=1)  # page 2 fails

        with mock.patch("charter.util.run", side_effect=side_effect):
            with self.assertRaises(base.ForgeError):
                self.f.list_repos("acme")

    def test_list_repos_raises_on_malformed_json(self):
        with mock.patch("charter.util.run", return_value=_proc("{not json")):
            with self.assertRaises(base.ForgeError):
                self.f.list_repos("acme")

    def test_repo_tree_paginates_beyond_one_page(self):
        """F2: a repo with >100 root entries must not be silently truncated to the
        first page — that's how stack detection goes wrong for a big monorepo."""
        page1 = json.dumps([{"name": f"f{i}"} for i in range(100)])
        page2 = json.dumps([{"name": "nx.json"}])

        def side_effect(cmd, **kwargs):
            # See the note in test_list_repos_raises_on_mid_pagination_failure...: match
            # on "&page=N", not "page=N" — "per_page=100" would otherwise self-match.
            if "&page=1" in cmd[-1]:
                return _proc(page1)
            if "&page=2" in cmd[-1]:
                return _proc(page2)
            return _proc("[]")

        with mock.patch("charter.util.run", side_effect=side_effect):
            names = self.f.repo_tree({"id": 7})
        self.assertEqual(len(names), 101)
        self.assertIn("nx.json", names)

    def test_repo_tree_single_short_page_needs_no_second_call(self):
        with mock.patch("charter.util.run",
                        return_value=_proc(json.dumps([{"name": "README.md"}]))) as m:
            names = self.f.repo_tree({"id": 7})
        self.assertEqual(names, ["README.md"])
        self.assertEqual(m.call_count, 1)

    # --- FINDING I5: `repo_tree` is permissive (degrades to [] on failure) BY
    # --- CONTRACT — it feeds discover's stack probe, but also anything else that wants
    # --- a best-effort file listing. `repo_tree_strict` is the sibling discover's probe
    # --- actually uses: it raises on failure instead of degrading, so `_build_repo` can
    # --- tell "the probe failed" apart from "this repo genuinely has no recognised
    # --- stack file" — both used to look identical (an empty list either way).
    def test_repo_tree_strict_raises_on_failure_rather_than_degrading(self):
        with mock.patch("charter.util.run", return_value=_proc("", rc=1)):
            with self.assertRaises(base.ForgeError):
                self.f.repo_tree_strict({"id": 7})

    def test_repo_tree_strict_returns_the_same_names_as_repo_tree_on_success(self):
        payload = json.dumps([{"name": "nx.json"}, {"name": "README.md"}])
        with mock.patch("charter.util.run", return_value=_proc(payload)):
            self.assertEqual(sorted(self.f.repo_tree_strict({"id": 7})),
                             ["README.md", "nx.json"])

    def test_repo_tree_strict_paginates_like_repo_tree(self):
        page1 = json.dumps([{"name": f"f{i}"} for i in range(100)])
        page2 = json.dumps([{"name": "nx.json"}])

        def side_effect(cmd, **kwargs):
            if "&page=1" in cmd[-1]:
                return _proc(page1)
            if "&page=2" in cmd[-1]:
                return _proc(page2)
            return _proc("[]")

        with mock.patch("charter.util.run", side_effect=side_effect):
            names = self.f.repo_tree_strict({"id": 7})
        self.assertEqual(len(names), 101)
        self.assertIn("nx.json", names)

    def test_repo_tree_strict_raises_on_a_genuine_empty_tree_is_not_confused(self):
        """A genuinely empty tree is a legal, successful result: [] with no error."""
        with mock.patch("charter.util.run", return_value=_proc("[]")):
            self.assertEqual(self.f.repo_tree_strict({"id": 7}), [])

    def test_open_change_returns_the_iid(self):
        with mock.patch("charter.util.run",
                        return_value=_proc(json.dumps([{"iid": 42}]))):
            self.assertEqual(self.f.open_change("acme/api", "feat"), 42)

    def test_open_change_is_none_when_there_is_no_open_mr(self):
        with mock.patch("charter.util.run", return_value=_proc("[]")):
            self.assertIsNone(self.f.open_change("acme/api", "feat"))

    def test_ci_status_maps_into_the_neutral_vocabulary(self):
        for raw, want in (("success", "success"), ("failed", "failed"),
                          ("running", "running"), ("canceled", "canceled"),
                          ("manual", "manual"), ("skipped", "skipped"),
                          ("created", "pending"), ("waiting_for_resource", "pending")):
            with mock.patch("charter.util.run",
                            return_value=_proc(json.dumps([{"status": raw}]))):
                got = self.f.ci_status("acme/api", "main")
            self.assertEqual(got, want, raw)
            self.assertIn(got, base.CI_STATES)

    def test_an_unknown_ci_state_is_none_not_a_crash(self):
        with mock.patch("charter.util.run",
                        return_value=_proc(json.dumps([{"status": "invented"}]))):
            self.assertIsNone(self.f.ci_status("acme/api", "main"))

    def test_a_failing_cli_degrades_to_none_rather_than_raising(self):
        """These feed the status line, which renders every turn and must never crash."""
        with mock.patch("charter.util.run", return_value=_proc("", rc=1)):
            self.assertIsNone(self.f.open_change("acme/api", "main"))
            self.assertIsNone(self.f.ci_status("acme/api", "main"))

    def test_check_auth_raises_ForgeError_when_logged_out(self):
        with mock.patch("charter.util.run", return_value=_proc("", rc=1)):
            with self.assertRaises(base.ForgeError):
                self.f.check_auth()

    def test_check_auth_raises_when_exit_zero_but_not_actually_logged_in(self):
        """F3: `glab auth status` can exit 0 while its output says something other than
        "Logged in" (e.g. a config/host mismatch). `doctor.check_forge_auth` treats that
        as failure via the `"Logged in" in blob` check; `check_auth` must agree."""
        with mock.patch("charter.util.run", return_value=_proc("not logged in to any hosts")):
            with self.assertRaises(base.ForgeError):
                self.f.check_auth()

    def test_check_auth_passes_when_logged_in_reported(self):
        with mock.patch("charter.util.run",
                        return_value=_proc("✓ Logged in to gitlab.com as me\n")):
            self.f.check_auth()  # must not raise

    # --- FINDING I2: a declared self-hosted GitLab must query ITS OWN host, not the
    # --- glab default (gitlab.com) — `GitHubForge` already passes `--hostname` on every
    # --- call; `GitLabForge._glab` silently dropped it, so a self-hosted control plane
    # --- actually queried gitlab.com, and `check_auth` reported success for the
    # --- self-hosted host when only logged into gitlab.com (a false green).
    def test_hostname_reaches_the_list_repos_call(self):
        f = GitLabForge(host="git.internal")
        calls = []

        def fake(cmd, **kw):
            calls.append(cmd)
            return _proc("[]")

        with mock.patch("charter.util.run", side_effect=fake):
            f.list_repos("acme")
        self.assertTrue(calls)
        for cmd in calls:
            self.assertIn("--hostname", cmd)
            self.assertIn("git.internal", cmd)

    def test_hostname_reaches_the_check_auth_call(self):
        f = GitLabForge(host="git.internal")
        calls = []

        def fake(cmd, **kw):
            calls.append(cmd)
            return _proc("✓ Logged in to git.internal as me\n")

        with mock.patch("charter.util.run", side_effect=fake):
            f.check_auth()
        self.assertEqual(len(calls), 1)
        self.assertIn("--hostname", calls[0])
        self.assertIn("git.internal", calls[0])

    def test_default_host_also_passes_hostname_explicitly(self):
        """Not just the self-hosted case — every call is explicit about the host it
        means, so `glab`'s own ambient default host can never silently substitute."""
        calls = []

        def fake(cmd, **kw):
            calls.append(cmd)
            return _proc("[]")

        with mock.patch("charter.util.run", side_effect=fake):
            self.f.list_repos("acme")
        self.assertIn("--hostname", calls[0])
        self.assertIn("gitlab.com", calls[0])

    def test_check_auth_reports_failure_for_the_declared_host_even_if_only_logged_into_default(self):
        """The false-green this finding names: only logged into gitlab.com, but the
        control plane declares `git.internal` — `check_auth` must actually query
        git.internal (and see it's not logged in there), not silently report success
        because glab's ambient default happens to be authenticated."""
        f = GitLabForge(host="git.internal")

        def fake(cmd, **kw):
            self.assertIn("git.internal", cmd)
            return _proc("not logged in to any hosts")

        with mock.patch("charter.util.run", side_effect=fake):
            with self.assertRaises(base.ForgeError):
                f.check_auth()


if __name__ == "__main__":
    unittest.main()
