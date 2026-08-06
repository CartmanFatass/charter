"""config.py must survive a malformed charter.toml at import time.

``config`` is imported (transitively) by every subcommand, including ``--version``
and ``doctor``. ``instance.load`` deliberately keeps raising on malformed TOML or a
too-new schema (its own tests in test_instance.py pin that) — but config.py must not
let that exception escape import: it catches it, records the message in
``CONFIG_ERROR``, and falls back to empty/default values so the CLI stays usable.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestConfigErrorResilience(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="charter-cfg-"))
        (self.tmp / "charter.toml").write_text("this is not = valid = toml\n")
        self.env = dict(os.environ)
        self.env["CHARTER_ROOT"] = str(self.tmp)
        self.env["EDM_HOME"] = str(self.tmp / ".edm")
        self.env.pop("FORCE_COLOR", None)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, *args],
            cwd=REPO_ROOT,
            env=self.env,
            text=True,
            capture_output=True,
            timeout=30,
        )

    def test_malformed_charter_toml_is_recorded_not_raised(self):
        proc = self._run("-c", "from charter import config; print(config.CONFIG_ERROR)")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("charter.toml", proc.stdout)
        self.assertIn("not valid TOML", proc.stdout)

    def test_defaults_still_apply_alongside_the_recorded_error(self):
        proc = self._run(
            "-c",
            "from charter import config; print(repr(config.GROUP)); "
            "print(config.EXCLUDE); print(config.DEFAULT_WORKSPACE)",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout.splitlines()
        self.assertEqual(out[0], repr(""))
        self.assertEqual(out[1], "set()")
        self.assertEqual(out[2], "default")

    def test_version_still_works_with_a_malformed_charter_toml(self):
        proc = self._run("-m", "charter", "--version")
        combined = (proc.stdout or "") + (proc.stderr or "")
        self.assertNotIn("Traceback", combined)
        self.assertEqual(proc.returncode, 0, combined)
        self.assertIn("charter", proc.stdout)


class TestDoctorSurfacesConfigError(unittest.TestCase):
    """``doctor`` is where a user would look after `--version` quietly no-ops instead
    of crashing — it must name the broken charter.toml explicitly."""

    def setUp(self) -> None:
        from charter import config

        self._orig = (config.CONFIG_ERROR, config.HAS_CONTROL_PLANE)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        from charter import config

        config.CONFIG_ERROR, config.HAS_CONTROL_PLANE = self._orig

    def test_reports_fail_with_the_recorded_message_when_set(self):
        from charter import config, doctor

        config.CONFIG_ERROR = "/tmp/x/charter.toml is not valid TOML: boom"
        result = doctor.check_control_plane_config()
        self.assertEqual(result.status, doctor.FAIL)
        self.assertIn("charter.toml", result.detail)

    def test_reports_ok_when_no_error_is_recorded(self):
        from charter import config, doctor

        config.CONFIG_ERROR = None
        result = doctor.check_control_plane_config()
        self.assertEqual(result.status, doctor.OK)


if __name__ == "__main__":
    unittest.main()
