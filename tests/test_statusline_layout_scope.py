"""The status line is grouped by **scope**: every item sits with the thing it describes.

Before this, one header line carried four different scopes at once — the active
workspace, a count of *all* workspaces, a count of *all* vaults, and two session
gauges — and the session's own news (denials, memories, dispatches) rendered *inside
the repo column*, where it read as repo news. Items that had nothing to do with each
other sat side by side, and items that belonged together were rows apart.

Four zones now, each answering one question:

    ⬢ umbrella-improvements · ws 9                      WHERE I am
    ◫ repos 2/38            │ ◈ personas 13 · vaults 6  what each column holds
    ├─ …repo rows…          │ …persona chips…           WHAT I'm on × WHO I am
    ctx 22% · ⚡100% · ⛊1 denied            ⬢ charter    THIS session · the tool

The rule to keep: a count lives next to what it counts.
"""

from __future__ import annotations

import re
import unittest

from charter import config, persona, statusline
from tests._isolation import PersonaIso


def _plain(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def _raw(payload=None, width=200):
    """Rendered lines exactly as emitted, frame included."""
    import os
    old = os.environ.get("COLUMNS")
    os.environ["COLUMNS"] = str(width)
    try:
        return [_plain(ln) for ln in statusline.render(payload or {}).split("\n")]
    finally:
        if old is None:
            os.environ.pop("COLUMNS", None)
        else:
            os.environ["COLUMNS"] = old


def _lines(payload=None, width=200):
    """Content lines with the frame stripped — these tests are about the zones inside
    the box, not the box."""
    out = []
    for ln in _raw(payload, width):
        if not ln.strip() or set(ln.strip()) <= set("+-"):
            continue                      # top/bottom rule
        if ln.startswith("| ") and ln.rstrip().endswith("|"):
            ln = ln[2:].rstrip()[:-1].rstrip()
        out.append(ln)
    return out


_USAGE = {"context_window": {"used_percentage": 22,
                             "current_usage": {"cache_read_input_tokens": 100,
                                               "cache_creation_input_tokens": 0}}}


class Zones(PersonaIso):
    def setUp(self) -> None:
        super().setUp()
        for n in ("alpha", "beta"):
            self.make_persona(n, role=n.title(), vault=n, **{"delegate-when": f"{n} work"})
        # `vaults N` is shown only when there is at least one — `vaults 0` is noise.
        config.VAULTS_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        config.VAULTS_REGISTRY.write_text(
            '{"vaults": {"alpha": {"provider": "plain-file", "config": {"file": "/x"}}}}')

    def test_the_top_line_is_identity_only(self):
        """Where am I, and how many other workspaces exist. Nothing else."""
        top = _lines(_USAGE)[0]
        self.assertNotIn("repos", top)
        self.assertNotIn("vaults", top)
        self.assertNotIn("ctx", top)
        self.assertNotIn("⚡", top)

    def test_repos_count_heads_the_repo_column(self):
        head = next(ln for ln in _lines() if "repos" in ln)
        self.assertTrue(head.lstrip().startswith("repos"), head)

    def test_the_left_column_introduces_no_unproven_glyph(self):
        """The left column is width-critical: it is padded to `_LEFT_W` using
        `tui.width`, so a font that draws any character wider than the Unicode tables
        claim makes that row overhang and its right-hand cell start late.

        A `◫` on the repo header did exactly that — the personas header rendered one
        space right of every chip below it. The rule that prevents a repeat: this column
        may only use glyphs that ALREADY appear in it (proven safe by the fact that its
        rows line up with each other). Decoration belongs in the right column, past the
        alignment point.
        """
        allowed = set("↳⑂✓✗●·⚡⛊✎◌⚠")          # already load-bearing in this column
        for ln in _lines(_USAGE):
            sep = ln.find("|", 40)      # the column separator, past the tree glyphs
            if sep < 0:
                continue                # full-width row (identity, alerts, strip):
                                        # nothing to its right, so width can't shear it
            for ch in ln[:sep]:
                if ord(ch) < 128 or ch.isspace():
                    continue
                self.assertIn(ch, allowed,
                              f"unproven glyph {ch!r} (U+{ord(ch):04X}) in the "
                              f"width-critical left column: {ln!r}")

    def test_personas_and_vaults_count_heads_the_persona_column(self):
        head = next(ln for ln in _lines() if "personas" in ln)
        self.assertIn("personas 2", head)
        self.assertIn("vaults", head)

    def test_neither_column_header_carries_a_decorative_glyph(self):
        """A header is the only row of its kind, so a glyph on it is exercised by
        nothing else — and `tui.width` only knows what the Unicode tables claim.

        Both mistakes shipped. `◫` on the repo header pushed the entire right-hand
        column one space over. `◈` on the personas header sat *past* the column's
        alignment point, so the divider and the bullets still lined up perfectly while
        the word "personas" rendered one space right of every chip title below it —
        bullets agreeing and titles disagreeing is the signature.

        Content rows are safe because they repeat: thirteen chip rows lining up with
        each other is what proves `◆`/`○`. A header has no sibling to expose drift, so
        it gets a plain label.
        """
        rows = [ln for ln in _lines(_USAGE) if ln.find("|", 40) > 0]
        head, content = rows[0], rows[1:]
        elsewhere = {ch for ln in content for ch in ln}
        for ch in head:
            if ord(ch) < 128 or ch.isspace():
                continue
            self.assertIn(ch, elsewhere,
                          f"glyph {ch!r} (U+{ord(ch):04X}) appears ONLY on the column "
                          f"header, so no sibling row can expose a font drawing it wide: "
                          f"{head!r}")

    def test_the_two_column_headers_share_one_row(self):
        ls = _lines()
        self.assertEqual([i for i, ln in enumerate(ls) if "repos" in ln],
                         [i for i, ln in enumerate(ls) if "personas" in ln])

    def test_session_gauges_live_on_the_last_content_line(self):
        ls = [ln for ln in _lines(_USAGE) if ln.strip()]
        self.assertIn("ctx", ls[-1])
        self.assertIn("⚡", ls[-1])

    def test_the_brand_sits_at_the_end_of_the_session_strip(self):
        ls = [ln for ln in _lines(_USAGE) if ln.strip()]
        self.assertIn("charter", ls[-1])
        self.assertIn("ctx", ls[-1], "brand must share the strip, not float on a chip row")

    def test_no_session_news_inside_the_repo_column(self):
        """`⛊ N denied` used to render under the repo tree, as if it were repo news."""
        ls = _lines(_USAGE)
        head = next(i for i, ln in enumerate(ls) if "repos" in ln)
        last = max(i for i, ln in enumerate(ls) if ln.strip())
        for ln in ls[head:last]:
            left = ln.split("|", 1)[0] if "|" in ln[40:] else ln
            for token in ("denied", "recorded", "dispatched", "ctx", "⚡"):
                self.assertNotIn(token, left, f"session news leaked into the repo column: {ln}")


class DegradesCleanly(PersonaIso):
    def setUp(self) -> None:
        super().setUp()
        self.make_persona("alpha", role="A", vault="a", **{"delegate-when": "x"})

    def test_a_narrow_pane_still_renders_every_zone(self):
        for w in (24, 40, 80, 100, 131, 200):
            with self.subTest(width=w):
                out = _lines(_USAGE, width=w)
                self.assertTrue(any(ln.strip() for ln in out))
                for ln in out:
                    self.assertLessEqual(statusline.tui.width(ln), w)

    def test_the_strip_is_absent_when_there_is_nothing_to_say(self):
        """No usage yet (fresh session / just compacted) and no news — the strip must
        not render an empty row just to hold the brand."""
        ls = [ln for ln in _lines({}) if ln.strip()]
        self.assertFalse(any(ln.startswith("ctx") for ln in ls))

    def test_it_never_raises_on_a_broken_payload(self):
        for bad in ({"context_window": None}, {"context_window": {"used_percentage": "x"}},
                    {"session_id": 12}):
            with self.subTest(payload=bad):
                statusline.render(bad)


class BrandFits(unittest.TestCase):
    """The brand must be present-and-correct or absent — never truncated.

    A real session rendered `⬢ charter 0.10…`: `_with_brand` fits-or-drops and never
    truncates, so the crop came from outside — the pane gave one column less than
    `COLUMNS` promised. The fit check now keeps a margin, so an off-by-one in the
    reserve (or in a terminal's idea of how wide `⬢` is) drops the brand instead of
    shearing it.
    """

    def test_the_brand_is_never_partially_rendered(self):
        for w in range(24, 220):
            with self.subTest(width=w):
                out = _plain(statusline._with_brand("x" * 10, w))
                if "charter" in out:
                    self.assertNotIn("…", out.split("charter")[1])

    def test_it_keeps_a_margin_beyond_the_declared_width(self):
        """Exactly-fits must NOT render: that is the case a one-column shortfall eats."""
        brand_w = statusline.tui.width(_plain(statusline._brand()))
        body = "x" * 10
        exact = 10 + statusline._BRAND_GAP + brand_w
        self.assertNotIn("charter", _plain(statusline._with_brand(body, exact)))
        self.assertIn("charter", _plain(statusline._with_brand(body, exact + statusline._BRAND_MARGIN)))

    def test_it_still_never_exceeds_the_width(self):
        for w in (24, 40, 80, 120, 200):
            for used in (0, 5, 20):
                with self.subTest(width=w, used=used):
                    out = statusline._with_brand("x" * used, w)
                    for line in out.split("\n"):
                        self.assertLessEqual(statusline.tui.width(_plain(line)), w)


if __name__ == "__main__":
    unittest.main()


class Framed(PersonaIso):
    """The box, and the alignment guarantee it makes visible.

    Everything up to a row's last alignment point is ASCII — the frame, the tree, the
    column divider, and the chip bullets — because `tui.width` only knows what the
    Unicode tables claim and a terminal that disagrees shifts that row alone. Decoration
    is fine after nothing on the row still has to line up.
    """

    def setUp(self) -> None:
        super().setUp()
        for n in ("alpha", "beta", "gamma"):
            self.make_persona(n, role=n.title(), vault=n, **{"delegate-when": f"{n} work"})

    def test_it_has_a_top_and_bottom_rule(self):
        rows = [ln for ln in _raw(_USAGE) if ln.strip()]
        self.assertTrue(set(rows[0]) <= set("+-"), rows[0])
        self.assertTrue(set(rows[-1]) <= set("+-"), rows[-1])

    def test_every_content_row_is_bounded_on_both_sides(self):
        rows = [ln for ln in _raw(_USAGE) if ln.strip()][1:-1]
        for ln in rows:
            self.assertTrue(ln.startswith("|"), ln)
            self.assertTrue(ln.endswith("|"), ln)

    def test_every_row_is_exactly_the_same_width(self):
        """The point of the right edge: a row that renders wider than counted pushes
        its own `|` out, so drift is visible instead of mysterious."""
        widths = {statusline.tui.width(ln) for ln in _raw(_USAGE) if ln.strip()}
        self.assertEqual(len(widths), 1, f"ragged frame: {sorted(widths)}")

    def test_the_frame_never_exceeds_the_pane(self):
        for w in (40, 80, 131, 160, 200, 240):
            with self.subTest(width=w):
                for ln in _raw(_USAGE, width=w):
                    self.assertLessEqual(statusline.tui.width(ln), w)

    def test_a_pane_too_narrow_to_frame_still_renders(self):
        out = _raw(_USAGE, width=24)
        self.assertTrue(any(ln.strip() for ln in out))

    def test_structure_up_to_the_divider_is_pure_ascii(self):
        """No font may get a vote on where the right column begins."""
        for ln in _lines(_USAGE):
            sep = ln.find("|", 40)
            if sep < 0:
                continue
            for ch in ln[:sep + 1]:
                self.assertLess(ord(ch), 128,
                                f"non-ASCII {ch!r} (U+{ord(ch):04X}) before the column "
                                f"divider: {ln!r}")

    def test_every_persona_name_starts_in_the_same_column_as_the_header(self):
        """The defect that survived three fixes: `personas` sat at column 98 while every
        chip name sat at 100, because a `◈` had been doing the indenting and removing it
        took the indent with it. Headers pad with SPACES now, so this cannot recur."""
        rows = [ln for ln in _lines(_USAGE) if ln.find("|", 40) > 0]
        starts = set()
        for ln in rows:
            right = ln[ln.find("|", 40) + 1:]
            m = re.search(r"[A-Za-z]", right)
            self.assertIsNotNone(m, right)
            starts.add(m.start())
        self.assertEqual(len(starts), 1,
                         f"right-column text starts at differing columns: {sorted(starts)}")

    def test_chip_bullets_are_ascii_and_uniform_width(self):
        chips = [ln for ln in _lines(_USAGE) if ln.find("|", 40) > 0][1:]
        for ln in chips:
            marker = ln[ln.find("|", 40) + 2]
            self.assertLess(ord(marker), 128, f"non-ASCII chip bullet {marker!r}: {ln!r}")

    def test_the_frame_leaves_headroom_for_the_host_crop(self):
        """The pane is narrower than `$COLUMNS` claims. Measured: a line ending at
        COLUMNS-2 lost its last character to the host's own `…`, so usable is
        COLUMNS-3. Every rendered row must stop short of that, or the frame's right
        edge becomes a column of `…` — which is exactly what shipped in 0.12.0.
        """
        for w in (80, 131, 160, 200, 240):
            with self.subTest(width=w):
                for ln in _raw(_USAGE, width=w):
                    if ln.strip():
                        self.assertLessEqual(statusline.tui.width(ln), w - 3,
                                             f"row reaches the host's crop zone: {ln!r}")
