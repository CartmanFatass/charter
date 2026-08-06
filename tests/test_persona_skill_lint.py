"""Lint guard: a persona charter that names a `plugin:skill` for AGENT use must resolve to
an installed, MODEL-invokable skill. Human-only skills (disable-model-invocation) belong in
the /slash form, not a sub-agent brief — this is the exact rot that shipped un-caught before
(the steward charter naming the human-only `mattpocock-skills:ask-matt`)."""

from __future__ import annotations

import unittest

from charter import persona


class SkillRefLintCase(unittest.TestCase):
    def setUp(self):
        self._orig_sk = persona._installed_skills
        self._orig_pl = persona._enabled_plugin_names
        # controlled world: two enabled plugins; one model-ok skill, two human-only.
        persona._installed_skills = lambda: {
            "test-driven-development": True, "grilling": True,
            "ask-matt": False, "to-spec": False,
        }
        persona._enabled_plugin_names = lambda: {"superpowers", "mattpocock-skills"}
        self.addCleanup(self._restore)

    def _restore(self):
        persona._installed_skills = self._orig_sk
        persona._enabled_plugin_names = self._orig_pl

    def test_model_invokable_ok(self):
        self.assertEqual(
            persona._skill_ref_issues("use `superpowers:test-driven-development` first"), [])

    def test_human_only_is_flagged(self):
        issues = persona._skill_ref_issues("front it with `mattpocock-skills:ask-matt`")
        self.assertTrue(any("human-only" in m for _l, m in issues), issues)

    def test_unknown_skill_is_flagged(self):
        issues = persona._skill_ref_issues("use `superpowers:does-not-exist`")
        self.assertTrue(any("not an installed skill" in m for _l, m in issues), issues)

    def test_non_enabled_plugin_ignored(self):
        # `edm:foo` / `random:bar` don't reference an enabled plugin → not a skill ref
        self.assertEqual(persona._skill_ref_issues("`edm:foo` and `random:bar`"), [])

    def test_slash_and_bare_forms_not_checked(self):
        # /to-spec (human slash) and `to-spec` (no plugin: prefix) are human steps, not agent refs
        self.assertEqual(
            persona._skill_ref_issues("run /to-spec, then `to-spec` and `/grill-with-docs`"), [])

    def test_no_cache_skips_gracefully(self):
        persona._installed_skills = lambda: None
        self.assertEqual(persona._skill_ref_issues("`mattpocock-skills:ask-matt`"), [])


class RealCharterCase(unittest.TestCase):
    """End-to-end against the real installed plugin cache (no monkeypatch)."""

    def test_real_steward_charter_is_clean(self):
        if persona._installed_skills() is None:
            self.skipTest("no plugin cache in this environment")
        errs = [m for lvl, m in persona.lint("steward") if lvl == "error"]
        self.assertEqual(errs, [], f"steward charter has skill-ref lint errors: {errs}")


if __name__ == "__main__":
    unittest.main()
