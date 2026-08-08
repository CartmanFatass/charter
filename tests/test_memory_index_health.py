"""MEMORY.md must agree with the files beside it — `memstore.index_drift` + `doctor`.

Both failure shapes were found in a real control plane, and neither needed a
concurrency bug. MEMORY.md is append-heavy and edited by many agents and humans
at once:

* **dangling** — a hand-written commit added two index lines but created only one
  of the two files, so `charter recall` could surface a hit nobody can read;
* **unindexed** — a merge conflict on MEMORY.md was resolved by taking one side
  wholesale, dropping the other side's line while its file survived.

`doctor` therefore WARNs rather than FAILs: drift is hygiene, and the check runs
from the SessionStart hook, which must never block a session.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from charter import doctor, memstore


class IndexDrift(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.d = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _mem(self, name: str, body: str = "a fact") -> Path:
        p = self.d / name
        p.write_text(f"# {name[:-3]}\n\n_2026-08-08 · persistent_\n\n{body}\n")
        return p

    def _index(self, *links: str) -> None:
        body = "# Memory Index\n\n" + "".join(f"- [T]({l})\n" for l in links)
        (self.d / "MEMORY.md").write_text(body)

    def test_clean_base_has_no_drift(self):
        self._mem("a.md"); self._index("a.md")
        self.assertEqual(memstore.index_drift(self.d), {"dangling": [], "unindexed": []})

    def test_dangling_link_is_reported(self):
        self._mem("a.md"); self._index("a.md", "never-written.md")
        self.assertEqual(memstore.index_drift(self.d)["dangling"], ["never-written.md"])

    def test_unindexed_file_is_reported(self):
        self._mem("a.md"); self._mem("b.md"); self._index("a.md")
        self.assertEqual(memstore.index_drift(self.d)["unindexed"], ["b.md"])

    def test_both_at_once(self):
        self._mem("a.md"); self._index("gone.md")
        self.assertEqual(memstore.index_drift(self.d),
                         {"dangling": ["gone.md"], "unindexed": ["a.md"]})

    def test_memory_index_itself_is_never_counted(self):
        self._mem("a.md"); self._index("a.md")
        self.assertNotIn("MEMORY.md", memstore.index_drift(self.d)["unindexed"])

    def test_a_url_ish_title_is_not_mistaken_for_a_link(self):
        """Titles carry API paths; only a bare slug is a filename.

        Without this, `- [GET /v1/x.md](a.md)` would register a phantom link and
        report drift that isn't there.
        """
        self._mem("a.md")
        (self.d / "MEMORY.md").write_text(
            "# Memory Index\n\n- [GET /api/v1/thing.md returns 404](a.md)\n")
        self.assertEqual(memstore.index_drift(self.d), {"dangling": [], "unindexed": []})

    def test_missing_index_reports_every_file_as_unindexed(self):
        self._mem("a.md"); self._mem("b.md")
        self.assertEqual(memstore.index_drift(self.d)["unindexed"], ["a.md", "b.md"])

    def test_absent_directory_is_not_an_error(self):
        self.assertEqual(memstore.index_drift(self.d / "nope"),
                         {"dangling": [], "unindexed": []})


class DoctorCheck(unittest.TestCase):
    """The check must be able to FAIL — one that only ever reports OK is worthless.

    It shipped that way for a moment: a broad `except Exception` swallowed a
    NameError and returned OK, so it silently checked nothing.
    """

    def test_check_returns_a_named_result(self):
        r = doctor.check_memory_indexes()
        self.assertEqual(r.name, "memory indexes")

    def test_check_never_fails_a_session(self):
        """Runs from SessionStart — WARN at worst, never FAIL."""
        self.assertIn(doctor.check_memory_indexes().status, (doctor.OK, doctor.WARN))

    def test_check_is_wired_into_run_all(self):
        self.assertIn("memory indexes", [r.name for r in doctor.run_all()])

    def test_ok_result_states_how_many_bases_were_checked(self):
        """'ok' with no detail would hide a check that examined nothing."""
        r = doctor.check_memory_indexes()
        if r.status == doctor.OK and "not checked" not in (r.detail or ""):
            self.assertRegex(r.detail or "", r"\d+ base\(s\)")


if __name__ == "__main__":
    unittest.main()
