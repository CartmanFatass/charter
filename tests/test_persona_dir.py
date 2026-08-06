import os
import unittest
from unittest import mock

from tests._isolation import PersonaIso
from charter import persona, config


class TestDefaultPersona(PersonaIso):
    def _no_env(self):
        return mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "EDM_PERSONA"}, clear=True)

    def test_default_resolves_when_nothing_else_set(self):
        self.make_persona("steward", role="Steward", vault="steward")
        (config.PERSONAS_DIR / ".default").write_text("steward\n")
        with self._no_env():
            self.assertEqual(persona.resolve_active(), "steward")
            self.assertEqual(persona.source(), "committed-default")

    def test_local_active_overrides_default(self):
        self.make_persona("steward", role="S", vault="s")
        self.make_persona("dev", role="D", vault="d")
        (config.PERSONAS_DIR / ".default").write_text("steward\n")
        persona.set_active("dev")
        with self._no_env():
            self.assertEqual(persona.resolve_active(), "dev")

    def test_stale_default_is_ignored(self):
        (config.PERSONAS_DIR / ".default").write_text("ghost\n")  # no such persona
        with self._no_env():
            self.assertIsNone(persona.resolve_active())
            self.assertEqual(persona.source(), "none")


class TestPersonaDirLayout(PersonaIso):
    def _flat(self, name, extra="role: X\nvault: v"):
        (config.PERSONAS_DIR / f"{name}.md").write_text(
            f"---\nname: {name}\n{extra}\n---\n\n# {name}\n\ncharter\n")

    def test_dir_layout_resolves(self):
        self.make_persona("new", role="R", vault="v")
        self.assertTrue(persona.is_dir_layout("new"))
        self.assertEqual(persona.def_path("new").name, "persona.md")
        self.assertIn("new", persona.list_personas())
        self.assertEqual(persona.load("new")["meta"]["role"], "R")

    def test_flat_legacy_resolves(self):
        self._flat("old")
        self.assertFalse(persona.is_dir_layout("old"))
        self.assertEqual(persona.def_path("old").name, "old.md")
        self.assertIn("old", persona.list_personas())
        self.assertEqual(persona.load("old")["meta"]["role"], "X")

    def test_dir_layout_wins_over_flat(self):
        self._flat("dup", "role: FLAT\nvault: v")
        self.make_persona("dup", role="DIR", vault="v")  # also writes dir persona.md
        self.assertEqual(persona.load("dup")["meta"]["role"], "DIR")

    def test_shared_and_readme_excluded(self):
        self.make_persona("a", role="R", vault="v")
        (config.PERSONAS_DIR / "_shared" / "memory").mkdir(parents=True, exist_ok=True)
        (config.PERSONAS_DIR / "README.md").write_text("# readme")
        names = persona.list_personas()
        self.assertIn("a", names)
        self.assertNotIn("_shared", names)
        self.assertNotIn("README", names)
        self.assertNotIn("readme", names)

    def test_migrate_flat_to_dir(self):
        self._flat("mig")
        self.assertEqual(persona.migrate("mig"), "migrated")
        self.assertTrue((config.PERSONAS_DIR / "mig" / "persona.md").exists())
        self.assertFalse((config.PERSONAS_DIR / "mig.md").exists())
        self.assertTrue((config.PERSONAS_DIR / "mig" / "memory" / "MEMORY.md").exists())
        self.assertTrue((config.PERSONAS_DIR / "mig" / "refs" / "README.md").exists())
        self.assertEqual(persona.migrate("mig"), "already")
        self.assertEqual(persona.migrate("ghost"), "missing")

    def test_effective_tools_unions_uses(self):
        self.make_persona("base", role="B", vault="b", tools="glab")
        self.make_persona("child", role="C", vault="c", tools="kubectl", uses="base")
        self.assertEqual(persona.effective_tools("child"), {"kubectl", "glab"})


if __name__ == "__main__":
    unittest.main()
