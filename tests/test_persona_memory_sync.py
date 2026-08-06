"""memory-sync: commits pending persona memory, refuses on a secret. Uses a real tmp
git repo (config.ROOT is redirected to it)."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from charter import config, persona, commands_persona

_KEYS = ("ROOT", "PERSONAS_DIR", "EDM_HOME", "PERSONA_STATE_DIR", "ACTIVE_PERSONA_FILE")


def _git(root, *a):
    return subprocess.run(["git", "-C", str(root), *a], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True)


class _Args:
    def __init__(self, no_push=True):
        self.no_push = no_push


class TestMemorySync(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="edm-memsync-"))
        _git(self.tmp, "init", "-q")
        _git(self.tmp, "config", "user.email", "t@t")
        _git(self.tmp, "config", "user.name", "t")
        _git(self.tmp, "config", "commit.gpgsign", "false")
        self._orig = {k: getattr(config, k) for k in _KEYS}
        config.ROOT = self.tmp
        config.PERSONAS_DIR = self.tmp / "personas"
        config.EDM_HOME = self.tmp / ".edm"
        config.PERSONA_STATE_DIR = config.EDM_HOME / "persona-state"
        config.ACTIVE_PERSONA_FILE = config.EDM_HOME / "active-persona"
        d = config.PERSONAS_DIR / "qa"
        d.mkdir(parents=True)
        (d / "persona.md").write_text("---\nname: qa\nrole: QA\nvault: qa\n---\n\n# QA\n")
        persona.scaffold_memory("qa")
        _git(self.tmp, "add", "-A")
        _git(self.tmp, "commit", "-q", "-m", "init")
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._orig.items():
            setattr(config, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pending(self):
        return _git(self.tmp, "status", "--porcelain", "--", "personas").stdout.strip()

    def test_nothing_to_sync(self):
        self.assertEqual(commands_persona.cmd_persona_memory_sync(_Args()), 0)

    def test_commits_pending_memory(self):
        persona.remember("qa", "a durable fact about routing coverage", title="routing fact")
        self.assertNotEqual(self._pending(), "")  # dirty before
        self.assertEqual(commands_persona.cmd_persona_memory_sync(_Args(no_push=True)), 0)
        self.assertEqual(self._pending(), "")      # clean after

    def test_refuses_on_secret(self):
        persona.remember("qa", "the api_key = sk-abcdef123456 lives here", title="leak")
        self.assertEqual(commands_persona.cmd_persona_memory_sync(_Args(no_push=True)), 1)
        self.assertIn("leak", self._pending())     # left uncommitted


if __name__ == "__main__":
    unittest.main()
