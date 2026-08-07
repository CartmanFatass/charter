"""Documentation is a shipping requirement, so a few claims are pinned by tests.

Not prose review — just the facts that would actively mislead a new consumer if they
drifted: the install command, the config keys, and the two framings that protect people
(the vault is not a password manager; memory defaults to local)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()


class TestReadme(unittest.TestCase):
    def test_says_what_it_is_in_the_first_paragraph(self):
        head = README[:600].lower()
        self.assertIn("claude code", head)
        for word in ("persona", "workspace", "repo"):
            self.assertIn(word, head, word)

    def test_shows_a_real_install_command(self):
        self.assertRegex(README, r"(uv tool install|pipx install|pip install)\s+charter")

    def test_documents_both_artifacts(self):
        """Install is the CLI *and* the plugin — omitting the plugin leaves hooks dead."""
        self.assertIn("plugin", README.lower())

    def test_frames_the_vault_honestly(self):
        """It stores plaintext at 0600. Saying anything that implies encryption at rest
        would be the single most harmful inaccuracy in these docs."""
        low = README.lower() + (ROOT / "docs" / "secrets.md").read_text().lower()
        self.assertTrue("not a password manager" in low
                        or "not a secrets manager" in low)
        self.assertIn("transcript", low)

    def test_states_the_memory_default(self):
        low = README.lower() + (ROOT / "docs" / "control-plane.md").read_text().lower()
        self.assertIn("local", low)
        self.assertIn("share", low)

    def test_explains_the_one_credential_rule(self):
        """A user who does not know it reads a guard denial as a bug."""
        low = README.lower()
        self.assertIn("https", low)
        self.assertTrue("ssh" in low)

    def test_is_not_a_stub(self):
        self.assertGreater(len(README.splitlines()), 60)


class TestConfigDocs(unittest.TestCase):
    def test_every_charter_toml_key_is_documented(self):
        body = (ROOT / "docs" / "control-plane.md").read_text()
        for key in ("schema", "forge", "kind", "host", "group", "owner",
                    "exclude", "memory", "share", "workspace"):
            self.assertIn(key, body, key)

    def test_shows_a_multi_forge_example(self):
        body = (ROOT / "docs" / "control-plane.md").read_text()
        self.assertGreaterEqual(len(re.findall(r"\[\[forge\]\]", body)), 2,
                                "a mixed-forge example is the non-obvious case")


if __name__ == "__main__":
    unittest.main()
