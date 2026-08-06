"""``charter doctor``'s git-identity check. ``commit_push`` (behind reactive memory,
workspace snapshots, and `charter save`) runs git with ``check=False`` and swallows a
failed commit, so a machine with no ``user.name``/``user.email`` configured silently
loses saves. This is the visibility for that — verified hermetically (HOME + both git
config files pointed at /dev/null, cwd outside any repo carrying real config) so the
test doesn't depend on the developer's own global git config."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from charter import doctor

_HERMETIC_ENV = {"HOME": "/nonexistent", "GIT_CONFIG_GLOBAL": "/dev/null",
                  "GIT_CONFIG_SYSTEM": "/dev/null"}


class TestGitIdentityCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="edm-gitid-"))
        self._orig_cwd = os.getcwd()
        os.chdir(self.tmp)
        patcher = mock.patch.dict(os.environ, _HERMETIC_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(os.chdir, self._orig_cwd)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_fail_when_no_identity_configured(self):
        # cwd isn't a git repo at all, and both global/system config are /dev/null →
        # `git config --get` has nowhere to resolve user.name/user.email from.
        result = doctor.check_git_identity()
        self.assertEqual(result.status, doctor.FAIL)
        self.assertIn("user.name", result.detail)
        self.assertIn("user.email", result.detail)
        self.assertIn("git config --global user.email", result.hint)
        self.assertIn("git config --global user.name", result.hint)

    def test_ok_when_identity_set_locally(self):
        subprocess.run(["git", "init", "-q", str(self.tmp)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.tmp), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(self.tmp), "config", "user.name", "t"], check=True)
        result = doctor.check_git_identity()
        self.assertEqual(result.status, doctor.OK)
        self.assertEqual(result.detail, "t <t@t>")


if __name__ == "__main__":
    unittest.main()
