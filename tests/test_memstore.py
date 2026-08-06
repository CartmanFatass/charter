"""The shared per-file memory engine: one file per fact + MEMORY.md index, optional
timestamp-prefixed filenames for chronological ordering, keyword search, near-dup
detection, and forget-by-slug (matching the timestamp prefix too)."""

from __future__ import annotations

import datetime
import shutil
import tempfile
import unittest
from pathlib import Path

from charter import memstore


class MemstoreCase(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="edm-memstore-"))
        self.addCleanup(lambda: shutil.rmtree(self.d, ignore_errors=True))

    def _stamp(self, s):
        return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")

    def test_write_slug_only(self):
        p = memstore.write(self.d, "a cluster fact", "Cluster")
        self.assertEqual(p.name, "cluster.md")
        self.assertIn("# Cluster", p.read_text())
        self.assertIn("[Cluster](cluster.md)", memstore.index_path(self.d).read_text())

    def test_write_timestamped_prefix_orders(self):
        a = memstore.write(self.d, "first", "aaa", timestamped=True,
                           stamp=self._stamp("2026-07-22 09:00:00"))
        b = memstore.write(self.d, "second", "bbb", timestamped=True,
                           stamp=self._stamp("2026-07-22 10:00:00"))
        self.assertTrue(a.name.startswith("20260722-090000-"))
        self.assertTrue(b.name.startswith("20260722-100000-"))
        # sorted() on the store lists chronologically because the prefix sorts
        self.assertEqual([p.name for p in memstore.files(self.d)], [a.name, b.name])

    def test_dedup_same_title(self):
        a = memstore.write(self.d, "one", "same")
        b = memstore.write(self.d, "two", "same")
        self.assertNotEqual(a.name, b.name)
        self.assertEqual(len(memstore.files(self.d)), 2)

    def test_no_index_when_disabled(self):
        memstore.write(self.d, "x", "t", index=False)
        self.assertFalse(memstore.index_path(self.d).exists())

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            memstore.write(self.d, "   ")

    def test_search_ranks_title_higher(self):
        memstore.write(self.d, "mentions keycloak once in body", "unrelated")
        memstore.write(self.d, "body", "keycloak token")
        res = memstore.search([self.d], "keycloak")
        self.assertEqual(res[0][1], "keycloak token")   # title hit ranks first

    def test_search_empty_query(self):
        memstore.write(self.d, "x", "t")
        self.assertEqual(memstore.search([self.d], "   "), [])

    def test_duplicates_flags_overlap(self):
        memstore.write(self.d, "the quick brown fox jumps over lazy dogs", "a")
        memstore.write(self.d, "the quick brown fox jumps over lazy dogs again", "b")
        dupes = memstore.duplicates([self.d], threshold=0.5)
        self.assertTrue(dupes and dupes[0][0] >= 0.5)

    def test_forget_by_slug_and_by_prefixed_name(self):
        memstore.write(self.d, "x", "plain")
        memstore.write(self.d, "y", "stamped", timestamped=True,
                       stamp=self._stamp("2026-07-22 12:00:00"))
        self.assertTrue(memstore.forget(self.d, "plain"))            # slug → plain.md
        self.assertTrue(memstore.forget(self.d, "stamped"))         # slug → <ts>-stamped.md
        self.assertEqual(memstore.files(self.d), [])
        idx = memstore.index_path(self.d).read_text()
        self.assertNotIn("plain.md", idx)
        self.assertNotIn("stamped.md", idx)

    def test_forget_missing(self):
        self.assertFalse(memstore.forget(self.d, "nope"))


if __name__ == "__main__":
    unittest.main()
