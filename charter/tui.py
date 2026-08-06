"""Tiny stdlib-only terminal-layout kit — "rich-lite" for charter's TUI output.

Why not rich/textual? The status line re-renders on *every* prompt, so it must
import in milliseconds and add no runtime dependency (`charter` ships stdlib-only).
This module is the small layout algebra that covers what charter draws —
ANSI-aware width math plus composable Text/Row/Stack/Columns nodes — so column
arithmetic lives in one tested place instead of ad-hoc padding at call sites.

The one hard guarantee, enforced by every node: **no rendered line ever
exceeds the requested width** (visible columns; ANSI SGR escapes count as
zero). Overflow is truncated with an ellipsis, never wrapped — a single
wrapped line shears every column below it. Rendered lines also never carry
trailing whitespace, even when it hides behind trailing colour escapes.

Vocabulary::

    width / truncate / pad    ANSI-aware string primitives
    Text                      markup line(s), clamped at render time
    Cell + Row                one line of fixed/natural-width cells
    Stack                     vertical composition of nodes
    Columns                   side-by-side columns of independent heights

Every node renders via ``node.render(width) -> list[str]``. Contents are
"markup": plain text freely interleaved with ANSI SGR colour escapes.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Sequence

RESET = "\x1b[0m"
ELLIPSIS = "…"

_SGR = re.compile(r"\x1b\[[0-9;]*m")
#: Trailing whitespace hiding *behind* trailing SGR escapes ("a \x1b[0m").
_HIDDEN_TRAIL = re.compile(r"[ \t]+((?:\x1b\[[0-9;]*m)+)$")


# --------------------------------------------------------------------------
# String primitives
# --------------------------------------------------------------------------

def strip_ansi(s: str) -> str:
    """Return *s* with ANSI SGR (colour/style) escapes removed."""
    return _SGR.sub("", s) if "\x1b" in s else s


def _char_width(ch: str) -> int:
    """Terminal cell width of one character (0 combining, 2 east-asian wide)."""
    if ch < "\x80":
        return 1
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def width(s: str) -> int:
    """Visible terminal width of markup *s* (ANSI stripped, wide chars = 2)."""
    s = strip_ansi(s)
    if s.isascii():  # fast path: the overwhelmingly common case
        return len(s)
    return sum(map(_char_width, s))


def truncate(s: str, w: int, ellipsis: str = ELLIPSIS) -> str:
    """Clamp markup *s* to at most *w* visible columns.

    Returns *s* unchanged when it already fits (fast path). When cut, ANSI
    escapes are carried through verbatim (so colour spans stay intact),
    *ellipsis* marks the cut, and a reset is appended so open styles never
    bleed into whatever is printed next.
    """
    if w <= 0:
        return ""
    if width(s) <= w:
        return s
    keep = max(0, w - width(ellipsis))
    out: list[str] = []
    vis = i = 0
    n = len(s)
    while i < n:
        if s[i] == "\x1b":
            m = _SGR.match(s, i)
            if m:  # copy the whole escape verbatim (zero visible width)
                out.append(m.group())
                i = m.end()
                continue
        cw = _char_width(s[i]) if s[i] >= "\x80" else 1
        if vis + cw > keep:
            break
        out.append(s[i])
        vis += cw
        i += 1
    out.append(ellipsis)
    if "\x1b" in s:
        out.append(RESET)
    return "".join(out)


def pad(s: str, w: int, align: str = "left") -> str:
    """Fit markup *s* to exactly *w* visible columns (truncate long, pad short).

    Padding is plain spaces appended outside any colour span, so styles such
    as underline never leak into the padding. *align* is ``left`` / ``right``
    / ``center``.
    """
    s = truncate(s, w)
    fill = w - width(s)
    if fill <= 0:
        return s
    if align == "right":
        return " " * fill + s
    if align == "center":
        left = fill // 2
        return " " * left + s + " " * (fill - left)
    return s + " " * fill


def term_width(default: int = 80, floor: int = 1) -> int:
    """Terminal width: ``$COLUMNS``, else the tty size, else *default*.

    Clamped to at least *floor*. Status-line style programs get their size via
    ``$COLUMNS`` because stdout is a pipe, hence the env-first order.
    """
    try:
        w = int(os.environ["COLUMNS"])
    except (KeyError, ValueError):
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = default
    return max(floor, w)


# Internal aliases: node methods take a ``width`` parameter by API contract,
# which shadows the module-level function inside them.
_width = width
_truncate = truncate
_pad = pad


def _finish(line: str) -> str:
    """Strip trailing whitespace, including any hiding behind trailing escapes."""
    line = line.rstrip(" \t")
    while True:
        cut = _HIDDEN_TRAIL.sub(r"\1", line)
        if cut == line:
            return line
        line = cut.rstrip(" \t")


# --------------------------------------------------------------------------
# Layout nodes
# --------------------------------------------------------------------------

class Node:
    """Base layout node.

    A node renders to a list of finished lines: each is guaranteed to be at
    most *width* visible columns (truncated with ``…``, never wrapped) and to
    carry no trailing whitespace.
    """

    __slots__ = ()

    def render(self, width: int) -> list[str]:
        """Render to lines, each clamped to *width* visible columns."""
        raise NotImplementedError


class Text(Node):
    """One or more lines of raw markup, clamped at render time.

    Embedded newlines split into multiple lines; each is clamped separately.
    """

    __slots__ = ("markup",)

    def __init__(self, markup: str = "") -> None:
        self.markup = markup

    def render(self, width: int) -> list[str]:
        return [_finish(_truncate(ln, width)) for ln in self.markup.split("\n")]


class Cell:
    """A :class:`Row` ingredient: markup with an optional fixed visible width.

    ``width=None`` keeps the natural width (no padding or truncation — the
    row still clamps the assembled line). A fixed ``width`` pads/truncates
    the cell to exactly that many columns, aligned by *align*.
    """

    __slots__ = ("markup", "width", "align")

    def __init__(self, markup: str, width: int | None = None,
                 align: str = "left") -> None:
        self.markup = markup
        self.width = width
        self.align = align


class Row(Node):
    """A single line of cells joined by *gap*, clamped to the render width.

    Cells may be :class:`Cell` instances or plain markup strings (treated as
    natural-width cells). Fixed-width cells keep a table's columns aligned
    across sibling rows without any call-site padding math.
    """

    __slots__ = ("cells", "gap")

    def __init__(self, *cells: Cell | str, gap: str = " ") -> None:
        self.cells = tuple(c if isinstance(c, Cell) else Cell(c) for c in cells)
        self.gap = gap

    def render(self, width: int) -> list[str]:
        parts = [
            c.markup if c.width is None else _pad(c.markup, c.width, c.align)
            for c in self.cells
        ]
        return [_finish(_truncate(self.gap.join(parts), width))]


class Stack(Node):
    """Vertical composition: children's lines concatenated top to bottom.

    Children may be nodes or plain markup strings (coerced to :class:`Text`).
    """

    __slots__ = ("children",)

    def __init__(self, *children: Node | str) -> None:
        self.children = tuple(
            c if isinstance(c, Node) else Text(c) for c in children
        )

    def render(self, width: int) -> list[str]:
        lines: list[str] = []
        for child in self.children:
            lines.extend(child.render(width))
        return lines


class Columns(Node):
    """Side-by-side columns of **independent heights**.

    ``columns`` is a sequence of ``(content, width)`` pairs — *content* a
    :class:`Node` or a plain list of markup lines, *width* a fixed visible
    column width or ``None`` for a flex column that shares whatever space
    remains after fixed columns and gaps. Shorter columns are blank-filled so
    rows stay aligned however tall each column is; every assembled row is
    clamped to the render width, so an over-budget spec degrades to truncated
    lines instead of wrapping.
    """

    __slots__ = ("columns", "gap")

    def __init__(self, columns: Sequence[tuple[Node | Sequence[str], int | None]],
                 gap: str = "  ") -> None:
        self.columns = list(columns)
        self.gap = gap

    def render(self, width: int) -> list[str]:
        if not self.columns:
            return []
        gap_w = _width(self.gap)
        fixed = sum(w for _, w in self.columns if w is not None)
        flexible = sum(1 for _, w in self.columns if w is None)
        spare = max(0, width - fixed - gap_w * (len(self.columns) - 1))
        share, extra = divmod(spare, flexible) if flexible else (0, 0)

        widths: list[int] = []
        for _, w in self.columns:
            if w is None:
                w = share + (1 if extra > 0 else 0)
                extra -= 1
            widths.append(w)

        blocks = [
            content.render(w) if isinstance(content, Node)
            else [_finish(_truncate(ln, w)) for ln in content]
            for (content, _), w in zip(self.columns, widths)
        ]
        height = max(map(len, blocks))
        out: list[str] = []
        for i in range(height):
            row = self.gap.join(
                _pad(block[i] if i < len(block) else "", w)
                for block, w in zip(blocks, widths)
            )
            out.append(_finish(_truncate(row, width)))
        return out
