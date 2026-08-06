"""Persona roster-health stats mined from committed memory (the persona's activity trace):
usage (count/recency/status) + an in-corpus quality proxy (verification-marker ratio, near-dup
ratio). Deterministic via an injected ``today`` and controlled memory stamps."""

from __future__ import annotations

import datetime
import unittest

from charter import memstore, persona
from tests._isolation import PersonaIso

TODAY = datetime.date(2026, 7, 24)


class PersonaStatsCase(PersonaIso):
    def _mem(self, name, title, body, days_ago=0):
        stamp = datetime.datetime(2026, 7, 24, 12, 0) - datetime.timedelta(days=days_ago)
        memstore.write(persona.memory_dir(name), body, title, stamp=stamp)

    def test_dormant_persona(self):
        self.make_persona("dorm", role="D", vault="d")
        s = persona.stats("dorm", today=TODAY)
        self.assertEqual(s["count"], 0)
        self.assertEqual(s["status"], "dormant")
        self.assertIsNone(s["verify_pct"])
        self.assertIsNone(s["last"])

    def test_active_with_verification_ratio(self):
        self.make_persona("act", role="A", vault="a")
        self._mem("act", "one", "we CONFIRMED the fix on dev")   # verified
        self._mem("act", "two", "verified against the DB")        # verified
        self._mem("act", "three", "just a plain note")            # not
        s = persona.stats("act", today=TODAY)
        self.assertEqual(s["count"], 3)
        self.assertEqual(s["recent"], 3)
        self.assertEqual(s["status"], "active")
        self.assertEqual(s["verify_pct"], 67)   # 2 of 3
        self.assertEqual(s["last"], "2026-07-24")

    def test_idle_when_memories_are_old(self):
        self.make_persona("old", role="O", vault="o")
        self._mem("old", "stale", "something learned long ago", days_ago=60)
        s = persona.stats("old", recent_days=14, today=TODAY)
        self.assertEqual(s["count"], 1)
        self.assertEqual(s["recent"], 0)
        self.assertEqual(s["status"], "idle")
        self.assertEqual(s["last"], "2026-05-25")

    def test_recent_window_boundary(self):
        self.make_persona("b", role="B", vault="b")
        self._mem("b", "edge-in", "x", days_ago=14)    # inside (<=14)
        self._mem("b", "edge-out", "y", days_ago=15)   # outside
        s = persona.stats("b", recent_days=14, today=TODAY)
        self.assertEqual(s["count"], 2)
        self.assertEqual(s["recent"], 1)

    def test_dup_ratio_flags_near_duplicates(self):
        self.make_persona("dup", role="D", vault="d")
        self._mem("dup", "a", "the quick brown fox jumps over the lazy dog today")
        self._mem("dup", "b", "the quick brown fox jumps over the lazy dog today too")
        self._mem("dup", "c", "utterly unrelated content about kubernetes pods")
        s = persona.stats("dup", today=TODAY)
        self.assertGreaterEqual(s["dup_pct"], 66)   # a & b are near-dups → 2 of 3

    def test_activity_profile_overrides_dormant(self):
        # a memory-blind role (orchestrator/standby/advisory) with zero memory must NOT
        # read as 'dormant' — it reports its declared profile instead (no false prune signal)
        for prof in ("orchestrator", "standby", "advisory"):
            self.make_persona(f"p-{prof}", role="R", vault="v", activity=prof)
            s = persona.stats(f"p-{prof}", today=TODAY)
            self.assertEqual(s["status"], prof)
            self.assertEqual(s["activity"], prof)
            self.assertEqual(s["count"], 0)

    def test_no_profile_zero_memory_stays_dormant(self):
        # the real prune signal survives: no activity: profile + zero memory = dormant
        self.make_persona("plain", role="R", vault="v")
        s = persona.stats("plain", today=TODAY)
        self.assertEqual(s["status"], "dormant")
        self.assertIsNone(s["activity"])

    def test_unknown_activity_value_ignored(self):
        # a typo'd/unknown activity value doesn't suppress the dormant signal
        self.make_persona("weird", role="R", vault="v", activity="whatever")
        self.assertEqual(persona.stats("weird", today=TODAY)["status"], "dormant")

    def test_memory_date_parses_body_stamp_and_filename(self):
        # in-body stamp wins
        self.assertEqual(
            persona._memory_date("# t\n\n_2026-07-01 09:30 · persistent_\n\nx", "slug.md"),
            datetime.date(2026, 7, 1))
        # filename prefix fallback when no body stamp
        self.assertEqual(
            persona._memory_date("no stamp here", "20260305-120000-slug.md"),
            datetime.date(2026, 3, 5))
        self.assertIsNone(persona._memory_date("nothing", "slug.md"))


if __name__ == "__main__":
    unittest.main()
