"""Shape-aware text layout.

Two things here decide whether output reads as typeset or as machine output:

* **Width varies with height.** A speech balloon is an ellipse, so a line near
  the top has far less room than one through the middle. Wrapping against the
  bounding *rectangle* is what produces text that visibly pokes out of round
  bubbles, or is shrunk far below what would fit.
* **The font size is searched, not assumed.** Translated Chinese is much
  shorter than English source text -- typically 40-60% of the character count
  -- so a fixed size leaves bubbles looking half-empty. We binary-search the
  largest size that still fits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from . import fonts, kinsoku


class WidthProfile(Protocol):
    """The horizontal span available to text at a given vertical band."""

    top: float
    bottom: float

    def span_at(self, y0: float, y1: float) -> tuple[float, float]:
        """Narrowest (left, right) over the band [y0, y1].

        Taking the *narrowest* point rather than the midpoint is what keeps
        glyphs inside the outline: a line is a box, not a hairline, and its
        widest row must still fit.
        """
        ...

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def height(self) -> float:
        return self.bottom - self.top


@dataclass
class RectProfile:
    """Constant-width region. Used for free-floating text and as a fallback."""

    left: float
    right: float
    top: float
    bottom: float

    def span_at(self, y0: float, y1: float) -> tuple[float, float]:
        return self.left, self.right

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def height(self) -> float:
        return self.bottom - self.top


@dataclass
class EllipseProfile:
    """Ellipse inscribed in a bounding box -- the speech-balloon model."""

    left: float
    right: float
    top: float
    bottom: float

    def _half_width_at(self, y: float) -> float:
        ry = (self.bottom - self.top) / 2
        if ry <= 0:
            return 0.0
        dy = abs(y - self.center_y) / ry
        if dy >= 1.0:
            return 0.0
        return (self.right - self.left) / 2 * math.sqrt(1.0 - dy * dy)

    def span_at(self, y0: float, y1: float) -> tuple[float, float]:
        # The band's narrowest row is whichever edge sits further from centre.
        half = min(self._half_width_at(y0), self._half_width_at(y1))
        cx = (self.left + self.right) / 2
        return cx - half, cx + half

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def height(self) -> float:
        return self.bottom - self.top


@dataclass
class PolygonProfile:
    """Exact profile from a traced bubble contour.

    Built from a boolean mask so oddly shaped balloons -- spiky shout bubbles,
    clouds, overlapping tails -- get real measurements instead of an ellipse
    approximation.
    """

    row_spans: list[tuple[float, float]]
    """Per-row (left, right) of the bubble interior, indexed from `top`."""
    top: float
    bottom: float

    def span_at(self, y0: float, y1: float) -> tuple[float, float]:
        i0 = max(0, int(y0 - self.top))
        i1 = min(len(self.row_spans), int(math.ceil(y1 - self.top)))
        if i0 >= i1:
            return 0.0, 0.0
        band = self.row_spans[i0:i1]
        left = max(s[0] for s in band)
        right = min(s[1] for s in band)
        return (left, right) if right > left else (0.0, 0.0)

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def height(self) -> float:
        return self.bottom - self.top


@dataclass
class LaidOutLine:
    text: str
    x: float
    """Left edge after alignment."""
    y: float
    """Top of the line box."""
    width: float


@dataclass
class LayoutResult:
    lines: list[LaidOutLine]
    size: int
    line_height: float
    overflow: bool
    """True when nothing in the searched range fit; the caller rendered the
    minimum size anyway rather than dropping the text."""

    @property
    def text_height(self) -> float:
        return len(self.lines) * self.line_height


def _is_word_char(ch: str) -> bool:
    """Latin-script character that must not be split mid-word."""
    return ch.isalnum() and ord(ch) < 0x2E80


def _fit_prefix(text: str, start: int, stop: int, limit: float, font: str, size: int) -> int:
    """Largest end index in [start, stop] with measure(text[start:end]) <= limit."""
    lo, hi, best = start, stop, start
    while lo <= hi:
        mid = (lo + hi) // 2
        if fonts.measure(font, size, text[start:mid]) <= limit:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best


def _ink_width(text: str, font: str, size: int) -> float:
    """Width a line occupies for fitting purposes.

    A trailing hangable character is excluded, because `wrap` deliberately
    lets it overhang the right margin (悬挂标点). Measuring the full advance
    here instead would reject the very line wrap just produced -- and since
    that rejection changes the line count, the centring loop oscillates and
    the whole size search becomes non-monotonic.
    """
    if len(text) > 1 and kinsoku.hangable(text[-1]):
        text = text[:-1]
    return fonts.measure(font, size, text)


def _choose_break(text: str, start: int, end: int) -> int:
    """Refine a raw width-based break into a legal one."""
    if end >= len(text):
        return end

    # Don't split a Latin word. Retreating to the last space is not enough:
    # a Latin word embedded in Chinese has no spaces around it, so "太sexy了"
    # broke as "太se" / "xy了". Fall back to the start of the word run itself.
    if _is_word_char(text[end]) and _is_word_char(text[end - 1]):
        word_start = end
        while word_start > start and _is_word_char(text[word_start - 1]):
            word_start -= 1
        space = text.rfind(" ", start, end)
        candidate = max(space + 1, word_start) if space > start else word_start
        # Only take it if something still fits on this line; a word longer than
        # the whole line has to break somewhere.
        if candidate > start:
            end = candidate

    return kinsoku.adjust_break(text, end, min_index=start + 1)


def wrap(
    text: str,
    font: str,
    size: int,
    profile: WidthProfile,
    top: float,
    line_height: float,
    max_lines: int = 64,
) -> list[tuple[str, float, tuple[float, float]]] | None:
    """Greedily break `text` into lines starting at `top`.

    Returns (line_text, line_top, span) triples, or None when the region is
    too narrow to hold even one character.
    """
    lines: list[tuple[str, float, tuple[float, float]]] = []
    y = top
    i = 0
    n = len(text)

    while i < n:
        if len(lines) >= max_lines:
            return None

        # Honour explicit breaks from the translator.
        hard = text.find("\n", i)
        segment_end = hard if hard != -1 else n

        span = profile.span_at(y, y + line_height)
        avail = span[1] - span[0]
        if avail <= 0:
            return None

        end = _fit_prefix(text, i, segment_end, avail, font, size)
        # Terminal punctuation may hang past the right margin.
        if end < segment_end and kinsoku.hangable(text[end]):
            end += 1

        if end <= i:
            # Region narrower than a single glyph. Only reachable at small
            # sizes, and the caller reads None as "this size does not fit".
            if avail < fonts.measure(font, size, text[i]):
                return None
            end = i + 1
        elif end < segment_end:
            end = _choose_break(text, i, end)

        lines.append((text[i:end].strip(), y, span))
        y += line_height

        i = end
        if i == hard:
            i += 1  # consume the explicit break
        while i < n and text[i] == " ":
            i += 1

    return lines


def layout_at_size(
    text: str,
    font: str,
    size: int,
    profile: WidthProfile,
    line_spacing: float,
    align: str,
) -> LayoutResult | None:
    """Lay out at a fixed size, or None if it does not fit.

    The block is vertically centred, but centring needs the line count, which
    needs the wrap, which needs the starting y. We iterate to a fixed point --
    in practice two or three passes.
    """
    line_height = size * line_spacing
    count = 1
    wrapped = None

    for _ in range(6):
        block_height = count * line_height
        top = profile.center_y - block_height / 2
        wrapped = wrap(text, font, size, profile, top, line_height)
        if wrapped is None:
            return None
        if len(wrapped) == count:
            break
        count = len(wrapped)
    else:
        # Oscillating between two line counts; the larger one is the safe pick.
        block_height = count * line_height
        top = profile.center_y - block_height / 2
        wrapped = wrap(text, font, size, profile, top, line_height)
        if wrapped is None:
            return None

    if count * line_height > profile.height:
        return None

    lines: list[LaidOutLine] = []
    for line_text, y, (left, right) in wrapped:
        width = fonts.measure(font, size, line_text)
        if _ink_width(line_text, font, size) > (right - left) + 1.0:
            return None
        if align == "left":
            x = left
        elif align == "right":
            x = right - width
        else:
            x = left + (right - left - width) / 2
        lines.append(LaidOutLine(text=line_text, x=x, y=y, width=width))

    return LayoutResult(lines=lines, size=size, line_height=line_height, overflow=False)


def fit(
    text: str,
    profile: WidthProfile,
    font: str = fonts.DEFAULT_FONT,
    line_spacing: float = 1.15,
    align: str = "center",
    min_size: int = 9,
    max_size: int | None = None,
) -> LayoutResult:
    """Largest size at which `text` fits inside `profile`.

    Always returns a result. If even `min_size` overflows, the text is laid out
    anyway with `overflow=True` -- dropping dialogue is never the right answer,
    and the editor surfaces the flag so a human can shorten the line.
    """
    text = text.strip()
    if not text:
        return LayoutResult(lines=[], size=min_size, line_height=0.0, overflow=False)

    if max_size is None:
        # Half the region height is an aesthetic ceiling, not a fitting one.
        # Without it a one-word bubble ("Huh?!") would be sized to the full
        # balloon height, which no typesetter would do.
        max_size = max(min_size, int(profile.height / 2))

    # Scanned from the top rather than binary-searched. Fitting is *not*
    # monotonic in size against a shaped profile: growing the font grows the
    # line height too, which moves each line into a different part of the
    # outline, so a larger size can fit where a smaller one did not. Binary
    # search assumes monotonicity and silently returns a size well below the
    # true maximum when that assumption breaks. The scan costs at most a few
    # hundred layout attempts, all of them hitting the measurement cache.
    for size in range(max_size, min_size - 1, -1):
        result = layout_at_size(text, font, size, profile, line_spacing, align)
        if result is not None:
            return result

    line_height = min_size * line_spacing
    top = profile.center_y - line_height / 2
    wrapped = wrap(text, font, min_size, profile, top, line_height) or []
    lines = [
        LaidOutLine(
            text=t,
            x=span[0],
            y=y,
            width=fonts.measure(font, min_size, t),
        )
        for t, y, span in wrapped
    ]
    if not lines:
        lines = [LaidOutLine(text=text, x=profile.span_at(top, top + line_height)[0], y=top,
                             width=fonts.measure(font, min_size, text))]
    return LayoutResult(lines=lines, size=min_size, line_height=line_height, overflow=True)
