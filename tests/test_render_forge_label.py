"""FINDING I3 (part 3) — `render.topology_md` hardcoded "GitLab group" in the generated
topology heading, so a GitHub (or mixed-forge) control plane's `docs/topology.md` said
"48 repos in the `acme` GitLab group" even when every repo listed is on GitHub. GitLab
has "groups"; GitHub has "orgs"/"users" — the heading must reflect which forge(s) the
inventory actually came from."""
from __future__ import annotations

import unittest

from charter import config, render


def _repo(name: str, forge: str) -> dict:
    return {"name": name, "kind": "app", "stack": "python", "default_branch": "main",
            "description": "", "forge": forge}


class TestTopologyHeadingIsForgeAccurate(unittest.TestCase):
    def setUp(self):
        self._orig_group = config.GROUP
        config.GROUP = "acme"
        self.addCleanup(lambda: setattr(config, "GROUP", self._orig_group))

    def test_an_all_gitlab_inventory_still_says_gitlab_group(self):
        """Back-compat: the historical single-forge (GitLab) wording is unchanged."""
        doc = {"repos": [_repo("api", "gitlab")]}
        md = render.topology_md(doc)
        self.assertIn("GitLab group", md)
        self.assertNotIn("GitHub", md)

    def test_an_all_github_inventory_says_github_org_not_gitlab_group(self):
        doc = {"repos": [_repo("api", "github")]}
        md = render.topology_md(doc)
        self.assertIn("GitHub org", md)
        self.assertNotIn("GitLab group", md)

    def test_a_mixed_forge_inventory_stays_neutral_rather_than_naming_the_wrong_one(self):
        doc = {"repos": [_repo("api", "gitlab"), _repo("site", "github")]}
        md = render.topology_md(doc)
        self.assertNotIn("GitLab group", md)
        self.assertNotIn("GitHub org", md)

    def test_an_empty_inventory_does_not_crash(self):
        md = render.topology_md({"repos": []})
        self.assertIn("0 repos", md)


if __name__ == "__main__":
    unittest.main()
