"""Persona inheritance: `extends:` merges a parent's charter + tools with the child's
own (charter concatenates, tools/uses union, scalar fields child-overrides-else-inherit),
with cycle + dangling-parent lint."""

from __future__ import annotations

import unittest

from charter import config, persona
from tests._isolation import PersonaIso


class InheritanceCase(PersonaIso):
    def _persona(self, name, body, **meta):
        d = config.PERSONAS_DIR / name
        d.mkdir(parents=True, exist_ok=True)
        fm = "\n".join(f"{k}: {v}" for k, v in {"name": name, **meta}.items())
        (d / "persona.md").write_text(f"---\n{fm}\n---\n\n{body}\n")
        persona.scaffold_memory(name)

    def test_charter_concatenates_parent_then_child(self):
        self._persona("base", "BASE CHARTER prompt.", role="Base", vault="base", tools="Bash, Read")
        self._persona("child", "CHILD ADDITIONS.", vault="child", extends="base", tools="Grep")
        c = persona.resolve("child")["charter"]
        self.assertIn("BASE CHARTER", c)
        self.assertIn("CHILD ADDITIONS", c)
        self.assertLess(c.index("BASE CHARTER"), c.index("CHILD ADDITIONS"))  # base first

    def test_tools_union(self):
        self._persona("base", "b", vault="base", tools="Bash, Read")
        self._persona("child", "c", vault="child", extends="base", tools="Grep")
        self.assertEqual(persona.tools_of("child"), {"Bash", "Read", "Grep"})

    def test_scalar_inherited_unless_overridden(self):
        self._persona("base", "b", role="Base", vault="base", **{"delegate-when": "base tasks"})
        self._persona("child", "c", vault="child", extends="base")
        m = persona.resolve("child")["meta"]
        self.assertEqual(m["role"], "Base")            # inherited (child didn't set it)
        self.assertEqual(m["delegate-when"], "base tasks")  # inherited
        self.assertEqual(m["vault"], "child")          # child's own
        self.assertEqual(persona.vault_of("child"), "child")

    def test_child_overrides_scalar(self):
        self._persona("base", "b", role="Base", vault="base")
        self._persona("child", "c", role="Child Role", vault="child", extends="base")
        self.assertEqual(persona.resolve("child")["meta"]["role"], "Child Role")

    def test_grandparent_chain(self):
        self._persona("root", "ROOT.", vault="root", tools="Bash")
        self._persona("mid", "MID.", vault="mid", extends="root", tools="Read")
        self._persona("leaf", "LEAF.", vault="leaf", extends="mid", tools="Grep")
        r = persona.resolve("leaf")
        self.assertEqual(r["lineage"], ["leaf", "mid", "root"])
        self.assertEqual(persona.tools_of("leaf"), {"Bash", "Read", "Grep"})
        for s in ("ROOT", "MID", "LEAF"):
            self.assertIn(s, r["charter"])

    def test_uses_union(self):
        self._persona("base", "b", vault="base", uses="devops")
        self._persona("child", "c", vault="child", extends="base", uses="qa")
        self.assertEqual(set(persona.uses_of("child")), {"devops", "qa"})

    def test_lint_dangling_extends(self):
        self._persona("child", "c", vault="child", extends="ghost")
        self.assertTrue(any("extends" in m and "no such persona" in m
                            for _lvl, m in persona.lint("child")))

    def test_lint_cycle(self):
        self._persona("a", "a", vault="a", extends="b")
        self._persona("b", "b", vault="b", extends="a")
        self.assertTrue(any("cycle" in m for _lvl, m in persona.lint("a")))
        self.assertEqual(persona.lineage("a"), ["a", "b"])  # cycle-safe (no infinite loop)

    def test_no_extends_resolves_to_self(self):
        self._persona("solo", "SOLO.", role="Solo", vault="solo")
        r = persona.resolve("solo")
        self.assertEqual(r["lineage"], ["solo"])
        self.assertEqual(r["charter"].strip(), "SOLO.")


if __name__ == "__main__":
    unittest.main()