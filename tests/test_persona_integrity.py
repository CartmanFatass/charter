"""Deterministic eval over the REAL committed personas — config-correctness invariants
that must hold for the routing/vault/reuse model to work. Read-only; not isolated."""
import unittest

from charter import persona


class TestPersonaIntegrity(unittest.TestCase):
    def setUp(self):
        self.names = persona.list_personas()

    def test_personas_exist(self):
        self.assertTrue(self.names, "no personas found")

    def test_each_loads_with_role_and_vault(self):
        for n in self.names:
            d = persona.load(n)
            self.assertIsNotNone(d, f"{n} failed to load")
            self.assertTrue(d["meta"].get("role"), f"{n} has no role")
            self.assertTrue(persona.vault_of(n), f"{n} has no vault")

    def test_uses_resolve_no_dangling(self):
        allnames = set(self.names)
        for n in self.names:
            for u in persona.uses_of(n):
                self.assertIn(u, allnames, f"persona '{n}' uses missing persona '{u}'")

    def test_no_persona_uses_itself(self):
        for n in self.names:
            self.assertNotIn(n, persona.uses_of(n), f"{n} uses itself")

    def test_lint_reports_no_errors(self):
        for n in self.names:
            errs = [m for lvl, m in persona.lint(n) if lvl == "error"]
            self.assertEqual(errs, [], f"{n}: {errs}")


if __name__ == "__main__":
    unittest.main()
