"""Workspace memory as a per-file DB (mirrors persona memory): remember writes one
timestamp-prefixed file + indexes it; recall searches/lists; forget removes; `note` is an
alias; a legacy notes.md is grandfathered into the index."""

from __future__ import annotations

from types import SimpleNamespace

from charter import workspace
from charter import commands_workspace as cw
from tests._isolation import PersonaIso


class WorkspaceMemoryCase(PersonaIso):
    def _mk(self, name="w"):
        workspace.ensure(name)
        workspace.scaffold(name)
        return name

    def test_remember_writes_one_indexed_timestamped_file(self):
        self._mk()
        p = workspace.remember("w", "SPI token verified on dev")
        self.assertTrue(p.exists())
        self.assertRegex(p.name, r"^\d{8}-\d{6}-")            # timestamp prefix
        self.assertEqual(len(workspace.memories("w")), 1)
        self.assertIn(f"({p.name})", workspace.memory_index("w").read_text())

    def test_note_is_alias_for_remember(self):
        self._mk()
        p = workspace.note("w", "via the note alias")
        self.assertTrue(p.exists())
        self.assertEqual(len(workspace.memories("w")), 1)

    def test_recall_search_and_list(self):
        self._mk()
        workspace.remember("w", "keycloak token flow")
        workspace.remember("w", "unrelated build note")
        hits = workspace.recall("w", "keycloak")
        self.assertEqual(len(hits), 1)
        self.assertIn("keycloak", hits[0][1].lower())
        self.assertEqual(len(workspace.recall("w")), 2)      # no query → list all

    def test_forget(self):
        self._mk()
        p = workspace.remember("w", "temporary", title="temp")
        self.assertTrue(workspace.forget_memory("w", "temp"))
        self.assertEqual(workspace.memories("w"), [])
        self.assertFalse(workspace.forget_memory("w", "temp"))

    def test_scaffold_creates_index_not_notes(self):
        self._mk()
        self.assertTrue(workspace.memory_index("w").exists())
        self.assertFalse(workspace.notes_file("w").exists())

    def test_legacy_notes_grandfathered_into_index(self):
        workspace.ensure("w")
        # simulate a pre-v2 workspace: a lone notes.md, no index
        workspace.memory_dir("w").mkdir(parents=True, exist_ok=True)
        workspace.notes_file("w").write_text("# old memo\n\n- did stuff\n")
        workspace.scaffold_memory("w")
        idx = workspace.memory_index("w").read_text()
        self.assertIn("(notes.md)", idx)                     # legacy file discoverable
        self.assertIn("notes.md", [p.name for p in workspace.memories("w")])

    # --- command layer ---
    def test_command_remember_then_recall(self):
        self._mk()
        rc = cw.cmd_workspace_remember(
            SimpleNamespace(workspace="w", text="a fact", title=None, message=None, query=None))
        self.assertEqual(rc, 0)
        self.assertEqual(len(workspace.memories("w")), 1)
        self.assertEqual(cw.cmd_workspace_recall(
            SimpleNamespace(workspace="w", query=None)), 0)

    def test_command_forget_missing_returns_1(self):
        self._mk()
        self.assertEqual(cw.cmd_workspace_forget(
            SimpleNamespace(workspace="w", slug="ghost")), 1)


if __name__ == "__main__":
    unittest.main()
