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


def _lines(payload=None, width=200):
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
        self.assertTrue(head.lstrip().startswith("◫"), head)

    def test_personas_and_vaults_count_heads_the_persona_column(self):
        head = next(ln for ln in _lines() if "personas" in ln)
        self.assertIn("personas 2", head)
        self.assertIn("vaults", head)

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
            left = ln.split("│")[0]
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
