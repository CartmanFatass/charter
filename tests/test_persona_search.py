import unittest

from tests._isolation import PersonaIso
from charter import persona


class TestMemorySearch(PersonaIso):
    def setUp(self):
        super().setUp()
        self.make_persona("dev", role="Dev", vault="dev")

    def test_ranks_title_matches_higher(self):
        persona.remember("dev", "the cluster kubeconfig lives in the vault", title="kubeconfig location")
        persona.remember("dev", "unrelated note about pipelines", title="pipelines")
        hits = persona.search_memories("dev", "kubeconfig")
        self.assertTrue(hits)
        self.assertIn("kubeconfig", hits[0][1].lower())  # top hit is the title match

    def test_empty_query_returns_nothing(self):
        persona.remember("dev", "x", title="x")
        self.assertEqual(persona.search_memories("dev", "  "), [])

    def test_no_match(self):
        persona.remember("dev", "kubernetes deploys", title="deploys")
        self.assertEqual(persona.search_memories("dev", "zebra"), [])

    def test_includes_shared_namespace(self):
        persona.remember("dev", "a shared convention about xyzzy widgets", title="conv", shared=True)
        self.assertTrue(persona.search_memories("dev", "xyzzy"))

    def test_dedupe_finds_near_duplicates(self):
        persona.remember("dev", "account service publishes dev packages on every push to gitlab", title="a")
        persona.remember("dev", "account service publishes dev packages on every push to gitlab ci", title="b")
        dupes = persona.find_duplicates("dev", threshold=0.5)
        self.assertTrue(dupes)
        self.assertGreaterEqual(dupes[0][0], 0.5)

    def test_dedupe_ignores_distinct(self):
        persona.remember("dev", "kubernetes cluster access via kubeconfig", title="a")
        persona.remember("dev", "gitlab pipeline retry semantics for flaky jobs", title="b")
        self.assertEqual(persona.find_duplicates("dev", threshold=0.5), [])


class TestPersonaLint(PersonaIso):
    def test_flags_dangling_uses(self):
        self.make_persona("child", role="C", vault="c", uses="ghost")
        issues = persona.lint("child")
        self.assertTrue(any(lvl == "error" and "ghost" in msg for lvl, msg in issues))

    def test_clean_persona_has_no_errors(self):
        self.make_persona("ok", role="OK", vault="ok", **{"delegate-when": "things"})
        self.assertEqual([i for i in persona.lint("ok") if i[0] == "error"], [])

    def test_missing_delegate_when_is_warn_not_error(self):
        self.make_persona("bare", role="Bare", vault="bare")
        issues = persona.lint("bare")
        self.assertTrue(any(lvl == "warn" and "delegate-when" in msg for lvl, msg in issues))
        self.assertEqual([i for i in issues if i[0] == "error"], [])


if __name__ == "__main__":
    unittest.main()
