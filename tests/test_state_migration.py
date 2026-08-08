"""Migrating the legacy ``.edm/`` state directory to ``.charter/``.

The state directory holds real credentials (0600 plain-file vaults), so this
migration is the one place where a bug looks exactly like "charter lost my
secrets". Each guard below exists because the alternative is silent data loss:

* the rename must preserve permissions — a 0644 vault is a leak;
* two directories must never be merged — the user has to decide;
* ``vaults.json`` stores **absolute** paths, so moving the directory without
  rewriting them leaves every vault reporting "not created yet" while the
  files sit safely in the new location. That shipped in 0.2.0 and is the
  regression pinned by ``test_vault_registry_is_repointed``.
"""

from __future__ import annotations

import json
import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from charter import config


class StateDirMigration(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self._orig = os.environ.get("CHARTER_HOME")
        os.environ.pop("CHARTER_HOME", None)
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        if self._orig is None:
            os.environ.pop("CHARTER_HOME", None)
        else:
            os.environ["CHARTER_HOME"] = self._orig

    def _legacy(self, *, with_registry: bool = False) -> Path:
        legacy = self.root / ".edm"
        (legacy / "vaults").mkdir(parents=True)
        secret = legacy / "vaults" / "devops.json"
        secret.write_text('{"k":"v"}')
        os.chmod(secret, 0o600)
        if with_registry:
            reg = legacy / "vaults.json"
            reg.write_text(json.dumps({"vaults": {
                "devops": {"provider": "plain-file",
                           "config": {"file": str(secret)}},
                # deliberately outside the state dir — must NOT be rewritten
                "elsewhere": {"provider": "plain-file",
                              "config": {"file": "/opt/shared/vault.json"}},
            }}))
            os.chmod(reg, 0o600)
        return legacy

    def test_legacy_only_is_renamed(self):
        self._legacy()
        got = config._migrate_state_dir(self.root)
        self.assertEqual(got, self.root / ".charter")
        self.assertTrue((self.root / ".charter" / "vaults" / "devops.json").exists())
        self.assertFalse((self.root / ".edm").exists())

    def test_vault_file_permissions_survive(self):
        """A 0644 vault after migration would be a credential leak."""
        self._legacy()
        config._migrate_state_dir(self.root)
        mode = stat.S_IMODE((self.root / ".charter" / "vaults" / "devops.json").stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_vault_registry_is_repointed(self):
        """Regression (shipped in 0.2.0): absolute paths must follow the move.

        Without this the files are moved correctly but every vault reports
        "not created yet" — indistinguishable, to the user, from losing them.
        """
        self._legacy(with_registry=True)
        config._migrate_state_dir(self.root)
        doc = json.loads((self.root / ".charter" / "vaults.json").read_text())
        moved = doc["vaults"]["devops"]["config"]["file"]
        self.assertEqual(moved, str(self.root / ".charter" / "vaults" / "devops.json"))
        self.assertTrue(Path(moved).exists(), "registry points at a file that isn't there")

    def test_registry_paths_outside_the_state_dir_are_left_alone(self):
        """A vault deliberately stored elsewhere is not ours to relocate."""
        self._legacy(with_registry=True)
        config._migrate_state_dir(self.root)
        doc = json.loads((self.root / ".charter" / "vaults.json").read_text())
        self.assertEqual(doc["vaults"]["elsewhere"]["config"]["file"],
                         "/opt/shared/vault.json")

    def test_repointed_registry_stays_0600(self):
        self._legacy(with_registry=True)
        config._migrate_state_dir(self.root)
        mode = stat.S_IMODE((self.root / ".charter" / "vaults.json").stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_both_present_are_never_merged(self):
        legacy = self._legacy()
        new = self.root / ".charter"
        new.mkdir()
        (new / "marker").write_text("new")
        got = config._migrate_state_dir(self.root)
        self.assertEqual(got, new)
        self.assertTrue(legacy.exists(), "legacy dir must be left for the user to inspect")
        self.assertTrue((new / "marker").exists())
        self.assertFalse((new / "vaults").exists(), "contents must not be merged")

    def test_explicit_home_skips_migration_entirely(self):
        legacy = self._legacy()
        chosen = self.root / "chosen"
        os.environ["CHARTER_HOME"] = str(chosen)
        got = config._migrate_state_dir(self.root)
        self.assertEqual(got, chosen)
        self.assertTrue(legacy.exists(), "an explicit CHARTER_HOME must not move anything")

    def test_neither_present_yields_the_new_default(self):
        got = config._migrate_state_dir(self.root)
        self.assertEqual(got, self.root / ".charter")

    def test_unreadable_registry_does_not_abort_the_migration(self):
        """The files are already moved; a bad registry must not crash charter."""
        legacy = self._legacy()
        (legacy / "vaults.json").write_text("{not json")
        got = config._migrate_state_dir(self.root)
        self.assertEqual(got, self.root / ".charter")
        self.assertTrue((self.root / ".charter" / "vaults" / "devops.json").exists())


if __name__ == "__main__":
    unittest.main()
