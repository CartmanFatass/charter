"""Guards on `tui.term_width` — the one input the status line cannot sanity-check."""

from __future__ import annotations

import unittest

class TermWidthGuards(unittest.TestCase):
    """`COLUMNS` is not always a usable number.

    A real environment in this project exports `COLUMNS=0`. `int("0")` parses fine, so
    the env-first branch accepted it and `max(floor, 0)` clamped the whole status line
    down to the 24-column floor — a legible value produced from a meaningless one. Only
    a POSITIVE width is a width; anything else must fall through to the tty size.
    """

    def _with_columns(self, value):
        import os
        from charter import tui
        old = os.environ.get("COLUMNS")
        if value is None:
            os.environ.pop("COLUMNS", None)
        else:
            os.environ["COLUMNS"] = value
        try:
            return tui.term_width(default=80, floor=24)
        finally:
            if old is None:
                os.environ.pop("COLUMNS", None)
            else:
                os.environ["COLUMNS"] = old

    def test_zero_columns_falls_through_instead_of_clamping(self):
        self.assertNotEqual(self._with_columns("0"), 24)

    def test_negative_columns_falls_through(self):
        self.assertNotEqual(self._with_columns("-5"), 24)

    def test_a_real_width_is_still_honoured(self):
        self.assertEqual(self._with_columns("137"), 137)

    def test_garbage_still_falls_through(self):
        self._with_columns("wide")      # must not raise

    def test_blank_still_falls_through(self):
        self._with_columns("")          # must not raise
