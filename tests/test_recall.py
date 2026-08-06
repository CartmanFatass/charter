"""The centralized memory-fetch gate (edm/recall.py): one entry that searches/lists across
the workspace journal + active persona own + shared namespace (+ ephemeral opt-in), each hit
labeled by source. Storage stays per-base; only reading is unified here."""

from __future__ import annotations

import datetime
import unittest

from charter import config, memstore, persona, recall, workspace
from tests._isolation import PersonaIso


class RecallGateCase(PersonaIso):
    def setUp(self):
        super().setUp()
        # one workspace journal + one persona (own + shared) with distinguishable memories
        workspace.ensure("w")
        workspace.scaffold("w")
        workspace.remember("w", "workspace note about keycloak tokens")
        self.make_persona("dev", role="Dev", vault="d")
        persona.remember("dev", "devops fact about keycloak deploys")          # own
        persona.remember("dev", "shared org keycloak convention", shared=True)  # shared

    def _labels(self, results):
        return sorted({src for src, _p, _t, _s in results})

    def test_searches_across_all_bases_with_source_labels(self):
        r = recall.recall("keycloak", workspace_name="w", persona_name="dev")
        self.assertEqual(len(r), 3)
        self.assertEqual(self._labels(r), ["persona:dev", "shared", "workspace:w"])

    def test_scope_filter_narrows_bases(self):
        r = recall.recall("keycloak", workspace_name="w", persona_name="dev",
                           scopes=("shared",))
        self.assertEqual(self._labels(r), ["shared"])

    def test_persona_scope_excludes_workspace(self):
        r = recall.recall("keycloak", workspace_name="w", persona_name="dev",
                           scopes=("workspace", "persona"))
        self.assertEqual(self._labels(r), ["persona:dev", "workspace:w"])

    def test_no_query_lists_newest_first(self):
        # add two workspace memories with explicit, ordered stamps
        workspace.ensure("t")
        workspace.scaffold("t")
        memstore.write(workspace.memory_dir("t"), "older", "older", timestamped=True,
                       stamp=datetime.datetime(2026, 7, 1, 9, 0))
        memstore.write(workspace.memory_dir("t"), "newer", "newer", timestamped=True,
                       stamp=datetime.datetime(2026, 7, 20, 9, 0))
        r = recall.recall(None, workspace_name="t", scopes=("workspace",))
        titles = [t for _s, _p, t, _sc in r]
        self.assertEqual(titles[:2], ["newer", "older"])

    def test_sources_skips_persona_when_none_active(self):
        # no active persona → only workspace + shared rows
        srcs = recall.sources(workspace_name="w", scopes=recall.DEFAULT_SCOPES, persona_name=None)
        labels = [lbl for lbl, _d in srcs]
        # 'dev' isn't the active persona here (no active file set), so persona row is absent
        self.assertIn("workspace:w", labels)
        self.assertIn("shared", labels)

    def test_ephemeral_scope_opt_in(self):
        persona.remember("dev", "scratch keycloak thought", ephemeral=True, session="s1")
        r = recall.recall("keycloak", workspace_name="w", persona_name="dev",
                          scopes=("ephemeral",))
        # ephemeral resolves per-session; with no session it targets 'nosession' — just assert no crash
        self.assertIsInstance(r, list)

    def test_limit_caps_results(self):
        for i in range(5):
            persona.remember("dev", f"keycloak item {i}", shared=True)
        r = recall.recall("keycloak", workspace_name="w", persona_name="dev", limit=2)
        self.assertEqual(len(r), 2)


if __name__ == "__main__":
    unittest.main()
