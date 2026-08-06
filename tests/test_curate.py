"""Deterministic memory-curation engine (edm/curate.py): finds exact/near dups, stale,
index drift, and charter-worthy rule candidates; auto-applies ONLY tier-1 safe/reversible
ops (exact-dup collapse via archive + index repair) and returns the rest as proposals.
No LLM, fully deterministic — the semantic judgment stays in the steward agent layer."""

from __future__ import annotations

import datetime
import shutil
import tempfile
import unittest
from pathlib import Path

from charter import curate, memstore

TODAY = datetime.date(2026, 7, 24)


class CurateCase(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="edm-curate-"))
        self.addCleanup(lambda: shutil.rmtree(self.d, ignore_errors=True))

    def _w(self, title, body, days_ago=0):
        stamp = datetime.datetime(2026, 7, 24, 12, 0) - datetime.timedelta(days=days_ago)
        return memstore.write(self.d, body, title, timestamped=True, stamp=stamp)

    # --- body normalization (exact-dup basis) ---
    def test_body_strips_header_and_normalizes(self):
        a = "# Title A\n\n_2026-07-01 09:00 · persistent_\n\nthe   FACT here\n"
        b = "# Different Title\n\n_2026-07-20 15:30 · persistent_\n\nThe fact   here\n"
        self.assertEqual(memstore.body(a), memstore.body(b))  # same fact, diff title/stamp

    # --- exact duplicates ---
    def test_exact_dups_detected_and_grouped(self):
        self._w("first", "identical content", days_ago=5)
        self._w("second", "identical content", days_ago=1)
        self._w("unique", "something else")
        rep = curate.report(self.d, today=TODAY)
        self.assertEqual(len(rep["exact_dups"]), 1)
        self.assertEqual(len(rep["exact_dups"][0]), 2)

    def test_apply_safe_archives_redundant_keeps_one(self):
        self._w("first", "identical content", days_ago=5)
        self._w("second", "identical content", days_ago=1)
        before = len(memstore.files(self.d))
        actions = curate.apply_safe(self.d)
        self.assertTrue(any("archived exact-duplicate" in a for a in actions))
        # one active memory remains; the other moved to archive/ (reversible, still on disk)
        self.assertEqual(len(memstore.files(self.d)), before - 1)
        self.assertTrue((self.d / "archive").exists())
        self.assertEqual(len(list((self.d / "archive").glob("*.md"))), 1)

    def test_archived_memory_invisible_to_search(self):
        self._w("dup", "shared body text", days_ago=2)
        self._w("dup2", "shared body text", days_ago=1)
        curate.apply_safe(self.d)
        # search only sees the surviving copy, not the archived one
        hits = memstore.search([self.d], "shared body text")
        self.assertEqual(len(hits), 1)

    # --- near dups (proposal, not auto) ---
    def test_near_dups_are_proposed_not_applied(self):
        self._w("a", "the quick brown fox jumps over the lazy dog in the yard")
        self._w("b", "the quick brown fox jumps over the lazy dog in the field")
        rep = curate.report(self.d, today=TODAY)
        self.assertTrue(rep["near_dups"])
        # apply_safe must NOT touch near-dups
        n = len(memstore.files(self.d))
        curate.apply_safe(self.d)
        self.assertEqual(len(memstore.files(self.d)), n)

    # --- stale (proposal, age-based, never auto) ---
    def test_stale_flagged_but_not_archived(self):
        self._w("old", "an old fact", days_ago=200)
        self._w("fresh", "a new fact", days_ago=1)
        rep = curate.report(self.d, stale_days=90, today=TODAY)
        self.assertEqual(len(rep["stale"]), 1)
        self.assertEqual(rep["stale"][0][2], 200)  # age in days
        curate.apply_safe(self.d)  # must not archive by age
        self.assertEqual(len(memstore.files(self.d)), 2)

    # --- charter rule nomination ---
    def test_rule_candidate_scores_standing_rule_high(self):
        self._w("rule", "STANDING RULE: authz work is always backend; never trust the client")
        self._w("plain", "we deployed the service to dev today")
        rep = curate.report(self.d, today=TODAY)
        names = [r[0] for r in rep["rules"]]
        self.assertEqual(len(rep["rules"]), 1)   # only the rule-ish one
        self.assertIn("rule", names[0])

    def test_transient_snapshot_never_nominated(self):
        # deploy snapshots must NOT be charter candidates even with rule-ish words
        self._w("snap", "Deployed state as of today: the gate must always pass; never skip")
        rep = curate.report(self.d, today=TODAY)
        self.assertEqual(rep["rules"], [])

    # --- index health ---
    def test_index_missing_detected_and_repaired(self):
        p = self._w("indexed", "content")
        # write a memory file directly WITHOUT indexing it
        memstore.write(self.d, "orphan body", "unindexed", index=False)
        rep = curate.report(self.d, today=TODAY)
        self.assertEqual(len(rep["index"]["missing"]), 1)
        curate.apply_safe(self.d)
        self.assertEqual(curate.report(self.d, today=TODAY)["index"]["missing"], [])

    def test_index_orphan_ignores_urlish_titles(self):
        # a title containing `](…md` must not be mistaken for a listed file
        self._w("api", "GET /api/x returns ](weird.md fragment in the title text")
        rep = curate.report(self.d, today=TODAY)
        self.assertEqual(rep["index"]["orphans"], [])

    def test_proposals_lists_tier2_only(self):
        self._w("a", "the quick brown fox jumps over the lazy dog here now")
        self._w("b", "the quick brown fox jumps over the lazy dog here today")
        self._w("rule", "STANDING RULE: always verify before claiming done")
        props = curate.proposals(curate.report(self.d, today=TODAY))
        self.assertTrue(any("merge near-duplicates" in p for p in props))
        self.assertTrue(any("promote to charter" in p for p in props))


if __name__ == "__main__":
    unittest.main()
