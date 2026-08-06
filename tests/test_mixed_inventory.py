"""discover across several forges.

Repos stay keyed by BARE NAME so every existing doc, skill and habit keeps working. When
two forges expose the same name the command refuses and asks for qualification, rather
than silently picking one — the on-disk workspace path is derived from that name, so a
wrong guess would clone two different repos over each other."""
from __future__ import annotations

import unittest

from charter import inventory
from charter.forge import registry


def _r(name, forge, ns=None):
    return {"name": name, "path_with_namespace": ns or f"acme/{name}",
            "default_branch": "main", "description": "", "web_url": "",
            "ssh_url": "", "topics": [], "id": 1, "forge": forge}


class TestMerge(unittest.TestCase):
    def test_records_from_two_forges_merge(self):
        merged = inventory.merge([[_r("api", "gitlab")], [_r("site", "github")]])
        self.assertEqual(sorted(r["name"] for r in merged), ["api", "site"])

    def test_each_record_keeps_its_forge_stamp(self):
        merged = inventory.merge([[_r("api", "gitlab")], [_r("site", "github")]])
        by = {r["name"]: r["forge"] for r in merged}
        self.assertEqual(by, {"api": "gitlab", "site": "github"})

    def test_a_name_collision_refuses_and_names_the_qualification(self):
        with self.assertRaises(registry.CollisionError) as cm:
            inventory.merge([[_r("api", "gitlab")], [_r("api", "github")]])
        msg = str(cm.exception)
        self.assertIn("api", msg)
        self.assertIn("github:api", msg)   # the exact thing to type instead

    def test_the_same_repo_from_one_forge_is_not_a_collision(self):
        merged = inventory.merge([[_r("api", "gitlab"), _r("web", "gitlab")]])
        self.assertEqual(len(merged), 2)

    # --- FINDING C1 (CRITICAL): identity is `path_with_namespace`, not bare name -----
    # `include_subgroups=true` means two GitLab subgroups (`acme/team-a/api` and
    # `acme/team-b/api`) both have bare name `api`; the old merge keyed purely on bare
    # name and only compared `forge`, so a SAME-forge collision silently let the second
    # sighting overwrite the first — the loser vanished from the durable map with
    # `discover` still exiting 0.
    def test_same_forge_different_namespace_same_bare_name_is_refused(self):
        with self.assertRaises(registry.CollisionError) as cm:
            inventory.merge([
                [_r("api", "gitlab", ns="acme/team-a/api")],
                [_r("api", "gitlab", ns="acme/team-b/api")],
            ])
        msg = str(cm.exception)
        self.assertIn("team-a/api", msg)
        self.assertIn("team-b/api", msg)

    def test_reproduction_two_forge_blocks_of_the_same_kind_also_refused(self):
        """Two `[[forge]]` blocks of the same kind (two GitHub orgs) hit the identical
        shape: same bare name, different path_with_namespace, same forge stamp."""
        with self.assertRaises(registry.CollisionError):
            inventory.merge([
                [_r("api", "github", ns="org-one/api")],
                [_r("api", "github", ns="org-two/api")],
            ])

    def test_identical_record_seen_twice_is_a_dedupe_not_a_collision(self):
        """The SAME repo (identical forge + path_with_namespace) turning up twice — e.g.
        an overlapping `include_subgroups` sweep re-listing a project — must merge to one
        record, not be refused as a collision."""
        merged = inventory.merge([
            [_r("api", "gitlab", ns="acme/api")],
            [_r("api", "gitlab", ns="acme/api")],
        ])
        self.assertEqual(len(merged), 1)

    def test_no_repo_vanishes_silently_on_a_refused_collision(self):
        """The bug wasn't just "raises" — it's "the loser used to vanish with no error
        at all". Assert the exception actually fires (not swallowed) for the exact
        C1 reproduction from the review."""
        batches = [
            [{"name": "api", "path_with_namespace": "acme/team-a/api", "forge": "gitlab",
              "default_branch": "main", "description": "", "web_url": "", "ssh_url": "",
              "topics": [], "id": 1}],
            [{"name": "api", "path_with_namespace": "acme/team-b/api", "forge": "gitlab",
              "default_branch": "main", "description": "", "web_url": "", "ssh_url": "",
              "topics": [], "id": 2}],
        ]
        with self.assertRaises(registry.CollisionError):
            inventory.merge(batches)


class TestFindQualified(unittest.TestCase):
    def test_a_bare_name_still_resolves(self):
        repos = [_r("api", "gitlab")]
        self.assertEqual(inventory.find(repos, "api")["name"], "api")

    def test_a_qualified_name_selects_the_right_forge(self):
        repos = [_r("api", "gitlab"), dict(_r("api", "github"), name="api")]
        self.assertEqual(inventory.find(repos, "github:api")["forge"], "github")
        self.assertEqual(inventory.find(repos, "gitlab:api")["forge"], "gitlab")

    def test_an_unknown_qualifier_finds_nothing(self):
        self.assertIsNone(inventory.find([_r("api", "gitlab")], "github:api"))


if __name__ == "__main__":
    unittest.main()
