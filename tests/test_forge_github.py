"""GitHub over `gh`. Stubbed — CI has no credentials, and a test that hits the network
is a flaky test."""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest import mock

from charter.forge import base
from charter.forge.github import GitHubForge


def _proc(stdout="", rc=0, stderr=""):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=rc)


#: A real `gh api` 404 body/stderr, captured live against github.com (2026-08-06) —
#: `gh api orgs/diazoxide/repos` (diazoxide is a personal account, not an org).
_REAL_404_STDOUT = json.dumps({
    "message": "Not Found",
    "documentation_url": "https://docs.github.com/rest/repos/repos#list-organization-repositories",
    "status": "404",
})
_REAL_404_STDERR = "gh: Not Found (HTTP 404)"


class TestGitHubForge(unittest.TestCase):
    def setUp(self) -> None:
        self.f = GitHubForge()

    def test_identity(self):
        self.assertEqual((self.f.kind, self.f.host, self.f.cli, self.f.change_sigil),
                         ("github", "github.com", "gh", "#"))

    def test_owner_noun_is_org(self):
        """FINDING I3 (part 3) — GitHub has orgs/users, not GitLab's 'groups'."""
        self.assertEqual(self.f.owner_noun, "org")

    def test_satisfies_the_protocol(self):
        self.assertIsInstance(self.f, base.Forge)

    def test_credential_helper_uses_gh_not_ssh(self):
        h = self.f.credential_helper()
        self.assertIn("gh", h)
        self.assertNotIn("ssh", h.lower())

    def test_insteadof_covers_both_ssh_forms(self):
        https, ssh_forms = self.f.insteadof()
        self.assertEqual(https, "https://github.com/")
        self.assertEqual(set(ssh_forms),
                         {"git@github.com:", "ssh://git@github.com/"})

    # --- list_repos: the normal (org) path -----------------------------------------

    def test_list_repos_normalizes_and_stamps_the_forge(self):
        payload = json.dumps([{
            "name": "api", "full_name": "acme/api", "default_branch": "main",
            "description": "d", "html_url": "w", "ssh_url": "s", "topics": ["t"],
            "id": 7,
        }])
        with mock.patch("charter.util.run", return_value=_proc(payload)) as m:
            repos = self.f.list_repos("acme")
        for k in base.REPO_KEYS:
            self.assertIn(k, repos[0], k)
        self.assertEqual(repos[0]["forge"], "github")
        self.assertEqual(repos[0]["path_with_namespace"], "acme/api")
        # An org that resolves directly must not also probe the user endpoint.
        self.assertTrue(all("users/" not in " ".join(c.args[0]) for c in m.call_args_list))

    def test_list_repos_returns_empty_for_a_genuinely_empty_org(self):
        """An org with zero repos is a legal, successful result — must NOT be confused
        with a failed call."""
        with mock.patch("charter.util.run", return_value=_proc("[]")):
            self.assertEqual(self.f.list_repos("acme"), [])

    # --- list_repos: the org->user fallback, and its two failure modes -------------

    def test_list_repos_falls_back_from_org_to_user_on_a_real_404(self):
        """A personal account is not an org: /orgs/{o}/repos 404s (verified live)
        where /users/{u}/repos works. The 404 must trigger a fallback, not a
        ForgeError."""
        payload = json.dumps([{"name": "api", "full_name": "diazoxide/api",
                               "default_branch": "main", "id": 1}])
        calls = []

        def fake(cmd, **kw):
            calls.append(" ".join(cmd))
            if "orgs/" in " ".join(cmd):
                return _proc(_REAL_404_STDOUT, rc=1, stderr=_REAL_404_STDERR)
            return _proc(payload)

        with mock.patch("charter.util.run", side_effect=fake):
            repos = self.f.list_repos("diazoxide")
        self.assertEqual(len(repos), 1)
        self.assertTrue(any("orgs/" in c for c in calls), "should have tried org first")
        self.assertTrue(any("users/" in c for c in calls), "should have fallen back")

    def test_list_repos_raises_when_the_user_endpoint_fails_after_a_404_fallback(self):
        """The org 404s as expected (fallback triggers) — but if /users/{u}/repos then
        fails too, that is a genuine failure, not "zero repos". Losing this distinction
        is exactly the shape that let a `list_repos` failure wipe the inventory."""
        def fake(cmd, **kw):
            joined = " ".join(cmd)
            if "orgs/" in joined:
                return _proc(_REAL_404_STDOUT, rc=1, stderr=_REAL_404_STDERR)
            return _proc("", rc=1, stderr="error connecting to github.com")

        with mock.patch("charter.util.run", side_effect=fake):
            with self.assertRaises(base.ForgeError):
                self.f.list_repos("diazoxide")

    def test_list_repos_raises_when_the_org_endpoint_fails_for_a_reason_other_than_404(self):
        """A non-404 failure of the org endpoint (auth, network, rate limit, ...) is NOT
        evidence the owner is a personal account — it must raise directly rather than
        silently falling back to the user endpoint (which would be guessing)."""
        calls = []

        def fake(cmd, **kw):
            calls.append(" ".join(cmd))
            return _proc("", rc=1, stderr="error connecting to github.com")

        with mock.patch("charter.util.run", side_effect=fake):
            with self.assertRaises(base.ForgeError):
                self.f.list_repos("acme")
        self.assertFalse(any("users/" in c for c in calls),
                          "a non-404 org failure must not trigger the user fallback")

    def test_list_repos_error_names_the_owner(self):
        with mock.patch("charter.util.run",
                        return_value=_proc("", rc=1, stderr="error connecting to github.com")):
            with self.assertRaises(base.ForgeError) as cm:
                self.f.list_repos("acme-org")
        self.assertIn("acme-org", str(cm.exception))

    def test_list_repos_raises_on_mid_pagination_failure_rather_than_truncating(self):
        """FINDING M8: this test used to claim it exercised "a 404 that far in", but its
        stub returned a generic connection error — which raises regardless of the
        `page == 1` guard, so it never actually stressed the boundary the docstring
        describes. Fixed to return a REAL 404 (the same live body/stderr captured in
        `_REAL_404_STDOUT`/`_REAL_404_STDERR`) on page 2. `_paged_strict`'s org_probe
        fallback only special-cases a 404 on page 1 (evidence *owner* might be a
        personal account, not an org) — a 404 THIS far into pagination is a genuine
        failure (page 1 already proved the org exists), not a signal to fall back to the
        user endpoint. A 404 mid-pagination is a failure, not an org/user fallback
        signal, so this must raise rather than silently stopping and returning a
        truncated list."""
        page1 = json.dumps([
            {"id": i, "name": f"repo{i}", "full_name": f"acme/repo{i}",
             "default_branch": "main"}
            for i in range(100)
        ])

        def side_effect(cmd, **kwargs):
            joined = " ".join(cmd)
            if "&page=1" in joined:
                return _proc(page1)
            return _proc(_REAL_404_STDOUT, rc=1, stderr=_REAL_404_STDERR)

        with mock.patch("charter.util.run", side_effect=side_effect):
            with self.assertRaises(base.ForgeError):
                self.f.list_repos("acme")

    def test_list_repos_raises_on_malformed_json(self):
        with mock.patch("charter.util.run", return_value=_proc("{not json")):
            with self.assertRaises(base.ForgeError):
                self.f.list_repos("acme")

    # --- repo_tree: permissive, degrades rather than raises -------------------------

    def test_repo_tree_returns_top_level_entries_only(self):
        payload = json.dumps({"tree": [
            {"path": "README.md"}, {"path": "src/main.py"}, {"path": "pyproject.toml"},
        ]})
        with mock.patch("charter.util.run", return_value=_proc(payload)):
            names = self.f.repo_tree({"path_with_namespace": "acme/api",
                                       "default_branch": "main"})
        self.assertEqual(sorted(names), ["README.md", "pyproject.toml"])

    def test_repo_tree_degrades_to_empty_on_failure_rather_than_raising(self):
        with mock.patch("charter.util.run", return_value=_proc("", rc=1)):
            self.assertEqual(
                self.f.repo_tree({"path_with_namespace": "acme/api",
                                   "default_branch": "main"}), [])

    # --- FINDING I5: `repo_tree` stays permissive by contract (see the test above and
    # --- gitlab.py's twin) — `repo_tree_strict` is the sibling discover's stack probe
    # --- actually calls, which RAISES on failure instead of degrading to [], so
    # --- `_build_repo` can distinguish "the probe failed" from "this repo genuinely has
    # --- no recognised root-level stack file" (both used to look identical).
    def test_repo_tree_strict_raises_on_failure_rather_than_degrading(self):
        with mock.patch("charter.util.run", return_value=_proc("", rc=1)):
            with self.assertRaises(base.ForgeError):
                self.f.repo_tree_strict({"path_with_namespace": "acme/api",
                                         "default_branch": "main"})

    def test_repo_tree_strict_returns_the_same_names_as_repo_tree_on_success(self):
        payload = json.dumps({"tree": [{"path": "README.md"}, {"path": "src/main.py"}]})
        with mock.patch("charter.util.run", return_value=_proc(payload)):
            names = self.f.repo_tree_strict({"path_with_namespace": "acme/api",
                                             "default_branch": "main"})
        self.assertEqual(names, ["README.md"])

    def test_repo_tree_strict_a_genuine_empty_tree_is_not_confused_with_failure(self):
        with mock.patch("charter.util.run", return_value=_proc(json.dumps({"tree": []}))):
            self.assertEqual(
                self.f.repo_tree_strict({"path_with_namespace": "acme/api",
                                         "default_branch": "main"}), [])

    def test_repo_tree_strict_raises_on_malformed_json(self):
        with mock.patch("charter.util.run", return_value=_proc("{not json")):
            with self.assertRaises(base.ForgeError):
                self.f.repo_tree_strict({"path_with_namespace": "acme/api",
                                         "default_branch": "main"})

    # --- open_change / ci_status: permissive, feed the status line -----------------

    def test_open_change_returns_the_pr_number(self):
        with mock.patch("charter.util.run",
                        return_value=_proc(json.dumps([{"number": 42}]))):
            self.assertEqual(self.f.open_change("acme/api", "feat"), 42)

    def test_open_change_is_none_when_there_is_no_open_pr(self):
        with mock.patch("charter.util.run", return_value=_proc("[]")):
            self.assertIsNone(self.f.open_change("acme/api", "feat"))

    # --- FINDING M7: `github.py` didn't URL-encode owner/name/branch/ref, unlike
    # --- `gitlab.py` (which uses `urllib.parse.quote` throughout). A branch named
    # --- `feature/foo` produced a broken `git/trees/feature/foo` path (extra path
    # --- segments instead of one ref) and `head=owner:feature/foo` (an unencoded slash
    # --- in a query value).
    def test_open_change_encodes_a_slashed_branch(self):
        calls = []

        def fake(cmd, **kw):
            calls.append(cmd)
            return _proc("[]")

        with mock.patch("charter.util.run", side_effect=fake):
            self.f.open_change("acme/api", "feature/foo")
        joined = " ".join(calls[0])
        self.assertIn("head=acme:feature%2Ffoo", joined)
        self.assertNotIn("head=acme:feature/foo", joined)

    def test_repo_tree_encodes_a_slashed_ref(self):
        calls = []

        def fake(cmd, **kw):
            calls.append(cmd)
            return _proc(json.dumps({"tree": []}))

        with mock.patch("charter.util.run", side_effect=fake):
            self.f.repo_tree({"path_with_namespace": "acme/api"}, ref="feature/foo")
        joined = " ".join(calls[0])
        self.assertIn("git/trees/feature%2Ffoo", joined)
        self.assertNotIn("git/trees/feature/foo", joined)

    def test_repo_tree_strict_encodes_a_slashed_ref(self):
        calls = []

        def fake(cmd, **kw):
            calls.append(cmd)
            return _proc(json.dumps({"tree": []}))

        with mock.patch("charter.util.run", side_effect=fake):
            self.f.repo_tree_strict({"path_with_namespace": "acme/api"}, ref="feature/foo")
        joined = " ".join(calls[0])
        self.assertIn("git/trees/feature%2Ffoo", joined)
        self.assertNotIn("git/trees/feature/foo", joined)

    def test_ci_status_uses_githubs_own_rollup(self):
        """GitHub has N check-runs with no inherent single value — but it computes a
        rollup itself. Use it rather than inventing an aggregation."""
        for raw, want in (("SUCCESS", "success"), ("FAILURE", "failed"),
                          ("ERROR", "failed"), ("PENDING", "pending"),
                          ("EXPECTED", "pending")):
            body = json.dumps({"data": {"repository": {"ref": {"target": {
                "statusCheckRollup": {"state": raw}}}}}})
            with mock.patch("charter.util.run", return_value=_proc(body)):
                got = self.f.ci_status("acme/api", "main")
            self.assertEqual(got, want, raw)
            self.assertIn(got, base.CI_STATES)

    def test_no_rollup_at_all_is_none(self):
        body = json.dumps({"data": {"repository": {"ref": {"target": {
            "statusCheckRollup": None}}}}})
        with mock.patch("charter.util.run", return_value=_proc(body)):
            self.assertIsNone(self.f.ci_status("acme/api", "main"))

    def test_a_failing_cli_degrades_to_none_rather_than_raising(self):
        """These feed the status line, which renders every turn and must never crash."""
        with mock.patch("charter.util.run", return_value=_proc("", rc=1)):
            self.assertIsNone(self.f.open_change("acme/api", "main"))
            self.assertIsNone(self.f.ci_status("acme/api", "main"))

    # --- auth -------------------------------------------------------------------

    def test_check_auth_raises_ForgeError_when_logged_out(self):
        with mock.patch("charter.util.run", return_value=_proc("", rc=1)):
            with self.assertRaises(base.ForgeError):
                self.f.check_auth()

    def test_check_auth_passes_when_logged_in(self):
        with mock.patch("charter.util.run",
                        return_value=_proc("✓ Logged in to github.com as me\n")):
            self.f.check_auth()  # must not raise


if __name__ == "__main__":
    unittest.main()
