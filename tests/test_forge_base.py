"""The Forge protocol's shared vocabulary.

Two forges describe the same ideas with different words — GitLab has merge requests and
one pipeline per commit; GitHub has pull requests and N check-runs. The protocol fixes a
NEUTRAL vocabulary so nothing above it has to care, while `change_sigil` keeps each
forge's native rendering (`!42` vs `#42`) so neither audience sees foreign jargon."""
import unittest

from charter.forge import base


class TestVocabulary(unittest.TestCase):
    def test_ci_states_are_the_neutral_set(self):
        self.assertEqual(
            base.CI_STATES,
            frozenset({"success", "failed", "running", "pending",
                       "manual", "canceled", "skipped"}))

    def test_protocol_declares_every_capability_the_engine_needs(self):
        for name in ("check_auth", "list_repos", "repo_tree", "open_change",
                     "ci_status", "credential_helper", "insteadof"):
            self.assertTrue(hasattr(base.Forge, name), name)


if __name__ == "__main__":
    unittest.main()
