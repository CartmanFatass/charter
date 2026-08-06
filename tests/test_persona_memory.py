import os
import time
import unittest

from tests._isolation import PersonaIso
from charter import persona, config


class TestPersonaMemory(PersonaIso):
    def setUp(self):
        super().setUp()
        self.make_persona("dev", role="Dev", vault="dev")

    def test_remember_persistent_own(self):
        p = persona.remember("dev", "cluster is X", title="cluster")
        self.assertTrue(p.exists())
        self.assertIn("personas/dev/memory", str(p).replace("\\", "/"))
        self.assertEqual([m.name for m in persona.memories("dev")], ["cluster.md"])
        idx = persona.index_of(persona.memory_dir("dev")).read_text()
        self.assertIn("[cluster](cluster.md)", idx)

    def test_remember_shared_goes_to_shared_ns(self):
        persona.remember("dev", "org convention", title="conv", shared=True)
        self.assertEqual(len(persona.memories("dev", shared=True)), 1)
        self.assertEqual(len(persona.memories("dev")), 0)  # not in the persona's own

    def test_remember_ephemeral_is_session_scoped_and_unindexed(self):
        p = persona.remember("dev", "scratch", title="s", ephemeral=True, session="sess1")
        self.assertIn("persona-state/ephemeral/sess1/dev", str(p).replace("\\", "/"))
        self.assertEqual(len(persona.memories("dev")), 0)  # persistent store untouched
        self.assertEqual(len(persona.memories("dev", ephemeral=True, session="sess1")), 1)
        self.assertFalse(persona.index_of(persona.ephemeral_dir("dev", session="sess1")).exists())

    def test_dedup_slug(self):
        a = persona.remember("dev", "one", title="same")
        b = persona.remember("dev", "two", title="same")
        self.assertNotEqual(a.name, b.name)
        self.assertEqual(len(persona.memories("dev")), 2)

    def test_forget(self):
        persona.remember("dev", "x", title="gone")
        self.assertTrue(persona.forget("dev", "gone"))
        self.assertEqual(len(persona.memories("dev")), 0)
        self.assertNotIn("gone.md", persona.index_of(persona.memory_dir("dev")).read_text())
        self.assertFalse(persona.forget("dev", "nope"))

    def test_gc_prunes_ended_sessions_only(self):
        root = config.PERSONA_STATE_DIR / "ephemeral"
        stale = persona.ephemeral_dir("dev", session="dead")
        stale.mkdir(parents=True)
        (stale / "x.md").write_text("s")
        persona.ephemeral_dir("dev", session="live").mkdir(parents=True)  # current, fresh
        old = time.time() - 8 * 3600
        for p in (root / "dead", stale, stale / "x.md"):
            os.utime(p, (old, old))
        self.assertEqual(persona.gc_ephemeral(current="live"), 1)
        self.assertFalse((root / "dead").exists())
        self.assertTrue((root / "live").exists())

    def test_remember_rejects_empty(self):
        with self.assertRaises(ValueError):
            persona.remember("dev", "   ")


if __name__ == "__main__":
    unittest.main()
