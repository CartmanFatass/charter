"""Per-instance config, read from charter.toml.

These three values were hardcoded to one organisation (`GROUP = "easydmarc"`), which is
the other half of what made the engine unshippable."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from charter import instance


class InstanceIso(unittest.TestCase):
    def setUp(self) -> None:
        self.plane = Path(tempfile.mkdtemp(prefix="charter-inst-")).resolve()

    def write(self, body: str) -> Path:
        (self.plane / "charter.toml").write_text(body)
        return self.plane


class TestLoad(InstanceIso):
    def test_reads_group_exclude_and_default_workspace(self):
        p = self.write('schema = 1\n\n[[forge]]\nkind = "gitlab"\ngroup = "acme"\n'
                       'exclude = ["acme-control"]\n\n[workspace]\ndefault = "scratch"\n')
        cfg = instance.load(p)
        self.assertEqual(cfg["forge"][0]["group"], "acme")
        self.assertEqual(cfg["forge"][0]["exclude"], ["acme-control"])
        self.assertEqual(cfg["workspace"]["default"], "scratch")

    def test_missing_file_is_empty_not_an_error(self):
        """`charter --version` and `charter init` must work outside a control plane."""
        self.assertEqual(instance.load(self.plane), {})

    def test_malformed_toml_raises_with_the_path_named(self):
        p = self.write("this is not = valid = toml\n")
        with self.assertRaises(Exception) as cm:
            instance.load(p)
        self.assertIn("charter.toml", str(cm.exception))

    def test_a_newer_schema_is_refused_loudly(self):
        """Never silently misread a layout written by a newer charter."""
        p = self.write(f"schema = {instance.SCHEMA + 1}\n")
        with self.assertRaises(instance.SchemaTooNew) as cm:
            instance.load(p)
        self.assertIn("upgrade", str(cm.exception).lower())

    def test_current_schema_is_accepted(self):
        p = self.write(f"schema = {instance.SCHEMA}\n")
        self.assertEqual(instance.load(p)["schema"], instance.SCHEMA)


class TestConfigWiring(InstanceIso):
    def test_defaults_apply_when_the_file_says_nothing(self):
        from charter import config
        self.assertEqual(instance.group_of({}, config.GROUP_FALLBACK), config.GROUP_FALLBACK)

    def test_group_and_exclude_come_from_the_file(self):
        cfg = {"forge": [{"kind": "gitlab", "group": "acme", "exclude": ["x"]}]}
        self.assertEqual(instance.group_of(cfg, "unused"), "acme")
        self.assertEqual(instance.exclude_of(cfg), {"x"})

    def test_group_and_exclude_are_resolvable_per_forge_block(self):
        """A multi-forge control plane gives each `[[forge]]` block its own group and
        exclude list — `index` picks which block, so `discover` can apply each block's
        excludes only to that block's own repos rather than to every forge's batch."""
        cfg = {"forge": [
            {"kind": "gitlab", "group": "acme", "exclude": ["acme-control"]},
            {"kind": "github", "owner": "diazoxide", "exclude": ["sandbox"]},
        ]}
        self.assertEqual(instance.group_of(cfg, "unused", index=0), "acme")
        self.assertEqual(instance.exclude_of(cfg, index=0), {"acme-control"})
        # github's block uses `owner`, not `group` — group_of only reads `group`
        # (forges_for is what accepts either key; see test_forge_registry.py).
        self.assertEqual(instance.group_of(cfg, "unused", index=1), "unused")
        self.assertEqual(instance.exclude_of(cfg, index=1), {"sandbox"})

    def test_an_out_of_range_index_falls_back_to_empty(self):
        cfg = {"forge": [{"kind": "gitlab", "group": "acme", "exclude": ["x"]}]}
        self.assertEqual(instance.group_of(cfg, "fallback", index=5), "fallback")
        self.assertEqual(instance.exclude_of(cfg, index=5), set())


if __name__ == "__main__":
    unittest.main()
