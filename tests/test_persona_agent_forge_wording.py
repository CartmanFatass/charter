"""F2 — the forge abstraction must not leak into a generated sub-agent's wording.

`commands_persona._render_agent` used to hardcode `glab`/GitLab prose (git = "the glab
token over HTTPS", "`glab auth status` checks the credential") into every generated
`.claude/agents/<name>.md`, regardless of which forge(s) the control plane actually
declares in its own `charter.toml` (`[[forge]]` blocks, resolved via
`charter.forge.registry`). Those files land in the USER'S OWN repo — a GitHub-only
control plane's generated sub-agent must never tell the reader about `glab`, a tool it
never uses (and a GitLab-only one must never mention `gh`). Only charter's own
generated prose is in scope here: a persona's own `tools:` declaration (e.g. `tools:
kubectl, glab`) is THAT persona's choice and must be preserved as written — untouched
by this fix.
"""
from __future__ import annotations

import re
import unittest

from tests._isolation import PersonaIso
from charter import commands_persona, config


def _mentions(word: str, text: str) -> bool:
    """Whole-word match only — `gh` must not false-positive on `through`/`right`/…"""
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def _agent_text(name: str) -> str:
    return (config.ROOT / ".claude" / "agents" / f"{name}.md").read_text()


class TestGeneratedAgentForgeWordingMatchesDeclaredForges(PersonaIso):
    def _declare(self, toml: str) -> None:
        (config.ROOT / "charter.toml").write_text(toml)

    def test_github_only_control_plane_never_mentions_glab(self):
        self._declare('[[forge]]\nkind = "github"\nowner = "acme"\n')
        self.make_persona("dev", role="Developer", vault="dev")
        self.assertEqual(commands_persona._write_agent("dev"), "written")
        text = _agent_text("dev")
        self.assertFalse(_mentions("glab", text), text)
        self.assertIn("gh auth status", text)

    def test_gitlab_only_control_plane_never_mentions_gh(self):
        self._declare('[[forge]]\nkind = "gitlab"\ngroup = "acme"\n')
        self.make_persona("dev", role="Developer", vault="dev")
        self.assertEqual(commands_persona._write_agent("dev"), "written")
        text = _agent_text("dev")
        self.assertFalse(_mentions("gh", text), text)
        self.assertIn("glab auth status", text)

    def test_no_charter_toml_falls_back_to_the_historical_gitlab_wording(self):
        """Back-compat: no `[[forge]]` blocks at all (the shape every control plane had
        before multi-forge support existed) still gets the original glab wording."""
        self.make_persona("dev", role="Developer", vault="dev")
        self.assertEqual(commands_persona._write_agent("dev"), "written")
        text = _agent_text("dev")
        self.assertIn("glab auth status", text)
        self.assertFalse(_mentions("gh", text), text)

    def test_mixed_forge_control_plane_mentions_both_clis(self):
        self._declare(
            '[[forge]]\nkind = "gitlab"\ngroup = "acme"\n\n'
            '[[forge]]\nkind = "github"\nowner = "acme"\n'
        )
        self.make_persona("dev", role="Developer", vault="dev")
        self.assertEqual(commands_persona._write_agent("dev"), "written")
        text = _agent_text("dev")
        self.assertTrue(_mentions("glab", text), text)
        self.assertTrue(_mentions("gh", text), text)

    def test_a_personas_own_tools_declaration_is_preserved_verbatim(self):
        """Only charter's OWN generated prose adapts to the declared forge — a persona
        that names a specific CLI in `tools:` (echoed into the generated description,
        "Runs kubectl, glab and pulls credentials from …") keeps that literal
        declaration untouched, even for a GitHub-only control plane where charter's own
        credential-rule prose has switched to `gh`."""
        self._declare('[[forge]]\nkind = "github"\nowner = "acme"\n')
        self.make_persona("dev", role="Developer", vault="dev", tools="kubectl, glab")
        self.assertEqual(commands_persona._write_agent("dev"), "written")
        text = _agent_text("dev")
        self.assertIn("Runs kubectl, glab and pulls credentials", text)
        # charter's own generated credential-rule prose still adapted correctly:
        self.assertIn("gh auth status", text)


if __name__ == "__main__":
    unittest.main()
