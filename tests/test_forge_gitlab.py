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


if __name__ == "__main__":
    unittest.main()
