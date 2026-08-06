"""The package must be importable and expose a version + a CLI entry point.

Guards the one property everything else rests on: `charter` is an installable
package, not a directory that happens to sit next to its data."""
import subprocess
import sys
import unittest


class TestPackaging(unittest.TestCase):
    def test_version_is_exposed(self):
        import charter
        self.assertRegex(charter.__version__, r"^\d+\.\d+\.\d+")

    def test_module_entry_point_runs(self):
        p = subprocess.run([sys.executable, "-m", "charter", "--version"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("charter", p.stdout.lower())

    def test_runtime_has_zero_dependencies(self):
        """Stdlib-only is the product's cleanest promise — assert it mechanically."""
        import tomllib
        from pathlib import Path
        cfg = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
        self.assertEqual(cfg["project"].get("dependencies", []), [])


if __name__ == "__main__":
    unittest.main()
