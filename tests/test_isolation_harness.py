"""The test-isolation harness itself: `PersonaIso` must redirect EVERY `charter.config`
attribute that path-sensitive code might read, so a test never leaks ambient process
state (the real repo's ROOT/HAS_CONTROL_PLANE/etc.) through an un-patched attribute.

Not exercised via PersonaIso subclassing — this drives `PersonaIso.setUp`/cleanup
directly, so it can control the "ambient" value that must NOT leak through, and (for the
second case) the exact temp directory the harness installs.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from charter import config, root
from tests._isolation import PersonaIso, _PATCH


class TestIsolationHarnessHasControlPlane(unittest.TestCase):
    def test_patch_tuple_includes_has_control_plane(self):
        """Pins the landmine directly: a future attribute added to config.py without a
        matching harness entry is exactly this bug shape, recurring."""
        self.assertIn("HAS_CONTROL_PLANE", _PATCH)

    def test_setup_derives_has_control_plane_from_the_temp_root_not_ambient_state(self):
        original = config.HAS_CONTROL_PLANE
        # Simulate the ambient/real process believing it's inside a control plane — the
        # opposite of what a fresh temp ROOT (no charter.toml yet) should say. If
        # PersonaIso.setUp forgot to re-derive the flag, this ambient True would leak
        # straight through and the assertion below would catch it.
        config.HAS_CONTROL_PLANE = True
        case = PersonaIso()
        case.setUp()
        try:
            self.assertIs(config.HAS_CONTROL_PLANE, False)
            # Not just False by luck — actually derived from the harness's own ROOT.
            self.assertEqual(config.HAS_CONTROL_PLANE, (config.ROOT / root.MARKER).is_file())
        finally:
            case._restore()
            config.HAS_CONTROL_PLANE = original

    def test_a_control_plane_marker_in_the_temp_root_is_reflected_as_true(self):
        """A test that drops charter.toml into its own temp ROOT (e.g. to exercise
        instance-config code) must see HAS_CONTROL_PLANE come back True — proving the
        flag is derived from the harness's temp config.ROOT, not a boolean copied from
        ambient process state at import time (which is False in this checkout)."""
        original = config.HAS_CONTROL_PLANE
        fixed_tmp = Path(tempfile.mkdtemp(prefix="charter-iso-fixture-"))
        (fixed_tmp / root.MARKER).write_text("schema = 1\n")
        case = PersonaIso()
        try:
            with mock.patch("tests._isolation.tempfile.mkdtemp", return_value=str(fixed_tmp)):
                case.setUp()
            self.assertEqual(config.ROOT, fixed_tmp)
            self.assertTrue(config.HAS_CONTROL_PLANE)
        finally:
            case._restore()  # also removes fixed_tmp, since case.tmp == fixed_tmp
            config.HAS_CONTROL_PLANE = original


if __name__ == "__main__":
    unittest.main()
