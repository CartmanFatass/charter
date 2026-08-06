"""The generated persona-roster block in README.md.

Two properties keep this from becoming a nuisance: it only ever rewrites the *marked*
region (a hand-written README is never appended to by surprise), and re-rendering
unchanged state is a no-op (so the once-per-session refresh doesn't churn the file).
"""
from __future__ import annotations

import unittest

from tests._isolation import PersonaIso
from charter import commands, config, dispatch, render


class TestPersonaBlock(PersonaIso):
    def _readme(self, body: str = "# Umbrella\n\nintro\n") -> None:
        (config.ROOT / "README.md").write_text(
            f"{body}\n{render.PERSONAS_BEGIN}\n{render.PERSONAS_END}\n\n## After\n")

    def test_block_reports_dispatches_and_flags_the_unused(self):
        self.make_persona("devops", tools="kubectl, glab")
        self.make_persona("billing-master", tools="kubectl")
        dispatch.record("devops")
        dispatch.record("devops")
        md = render.personas_md()
        self.assertIn("`devops`", md)
        self.assertIn("`billing-master` ⚑", md)  # never dispatched
        self.assertNotIn("`devops` ⚑", md)

    def test_generic_share_drives_the_chart(self):
        self.make_persona("devops", tools="kubectl")
        dispatch.record("devops")
        dispatch.record("general-purpose")
        md = render.personas_md()
        self.assertIn("```mermaid", md)
        self.assertIn('"dispatched to a persona" : 1', md)
        self.assertIn('"dispatched to a generic agent" : 1', md)

    def test_no_dispatches_yet_renders_a_note_not_a_broken_chart(self):
        self.make_persona("devops", tools="kubectl")
        md = render.personas_md()
        self.assertIn("No dispatches recorded yet", md)
        self.assertNotIn("```mermaid", md)
        self.assertNotIn("⚑", md)  # nothing is "never dispatched" before any data exists

    def test_vault_health_is_never_rendered(self):
        """Vaults are gitignored and per-engineer — committing their state would churn
        the README and conflict between checkouts."""
        self.make_persona("devops", tools="kubectl", vault="devops")
        md = render.personas_md()
        self.assertNotIn("vault", md.lower())

    def test_splice_replaces_only_the_marked_region(self):
        self._readme()
        self.make_persona("devops", tools="kubectl")
        dispatch.record("devops")
        out = render.splice_personas((config.ROOT / "README.md").read_text())
        self.assertIn("# Umbrella", out)
        self.assertIn("## After", out)
        self.assertIn("`devops`", out)
        self.assertEqual(out.count(render.PERSONAS_BEGIN), 1)
        self.assertEqual(out.count(render.PERSONAS_END), 1)

    def test_missing_markers_leave_the_readme_untouched(self):
        (config.ROOT / "README.md").write_text("# Umbrella\n\nhand written\n")
        self.assertIsNone(render.splice_personas("# Umbrella\n\nhand written\n"))
        self.assertFalse(commands.refresh_readme_personas())
        self.assertEqual((config.ROOT / "README.md").read_text(), "# Umbrella\n\nhand written\n")

    def test_refresh_is_idempotent(self):
        """The once-per-session hook must not rewrite the file when nothing changed."""
        self._readme()
        self.make_persona("devops", tools="kubectl")
        dispatch.record("devops")
        self.assertTrue(commands.refresh_readme_personas())   # first render changes it
        self.assertFalse(commands.refresh_readme_personas())  # second is a no-op

    def test_a_new_dispatch_does_change_the_block(self):
        self._readme()
        self.make_persona("devops", tools="kubectl")
        dispatch.record("devops")
        commands.refresh_readme_personas()
        dispatch.record("devops")
        self.assertTrue(commands.refresh_readme_personas())


if __name__ == "__main__":
    unittest.main()
