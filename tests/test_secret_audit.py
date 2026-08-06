import datetime
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from charter.secrets.plain_file import PlainFileProvider


class TestSecretAge(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="edm-secret-"))
        self.prov = PlainFileProvider("t", {"file": str(self.tmp / "v.json")})
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_set_records_age_zero(self):
        self.prov.set("k", "val")
        self.assertEqual(self.prov.ages(), {"k": 0})

    def test_backdated_age(self):
        self.prov.set("k", "val")
        old = (datetime.date.today() - datetime.timedelta(days=100)).isoformat()
        self.prov._meta_path.write_text(json.dumps({"k": {"set_at": old}}))
        self.assertEqual(self.prov.ages()["k"], 100)

    def test_untracked_key_age_none(self):
        (self.tmp / "v.json").write_text(json.dumps({"legacy": "x"}))
        self.assertIsNone(self.prov.ages()["legacy"])

    def test_delete_clears_meta(self):
        self.prov.set("k", "val")
        self.prov.delete("k")
        self.assertEqual(self.prov.ages(), {})

    def test_meta_file_is_0600(self):
        import stat
        self.prov.set("k", "val")
        mode = stat.S_IMODE(self.prov._meta_path.stat().st_mode)
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
