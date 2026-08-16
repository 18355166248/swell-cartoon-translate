"""Stage 0 -- long-strip slicing.

Webtoon pages run to 14000px tall. Feeding that to a detector is what makes
naive pipelines thrash a 4GB card. This module cuts the strip into chunks no
taller than `max_height` *before any model runs*, and it does so with plain
numpy in milliseconds.

The cut points are panel gutters. The key invariant that makes this safe:

    A run of rows that is uniform across the full image width cannot contain
    text -- glyphs would break the uniformity.

So a cut placed inside such a run can never bisect a speech bubble. When no
gutter exists in range (dense full-bleed artwork), we fall back to a forced
cut with overlap, and `geometry.dedup_blocks` merges the duplicate detections
that produces.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class Slice:
    """A horizontal band of the source image.

    `y0` doubles as the offset used to map detections back to original-image
    coordinates.
    """

    y0: int
    y1: int
    forced: bool
    """True when this band's lower edge is not a gutter, so it overlaps the
    next band and its detections need deduping."""

    @property
    def height(self) -> int:
        return self.y1 - self.y0


def row_ink_fraction(
    image: np.ndarray,
    deviation: int = 40,
    edge_trim: float = 0.02,
) -> np.ndarray:
    """Per-row fraction of pixels that deviate from that row's median.

    This is a direct "does this row contain ink?" test, and it beats a plain
    standard deviation on two counts:

    * Gradient backgrounds -- ubiquitous in webtoons -- have a high std but no
      ink. The per-row median tracks the local background, so they read clean.
    * The threshold is interpretable. A row of black text on white is roughly
      5% dark pixels; 0.2% corresponds to about one stray pixel across a 720px
      row, which is the JPEG-noise floor rather than a glyph.

    The outer `edge_trim` of columns is ignored: scanlation groups stamp
    watermarks and borders down the sides, and a single stray dark column
    would otherwise disqualify every gutter in the image.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    trim = int(gray.shape[1] * edge_trim)
    if trim > 0:
        gray = gray[:, trim:-trim]
    median = np.median(gray, axis=1, keepdims=True)
    return (np.abs(gray.astype(np.int16) - median) > deviation).mean(axis=1)


def find_gutters(
    image: np.ndarray,
    max_ink: float = 0.002,
    min_run: int = 8,
) -> list[tuple[int, int]]:
    """Find [start, end) row ranges that carry no ink across the full width.

    `min_run` rejects the isolated clean rows that occur incidentally inside
    artwork (e.g. the gap between two lines of text).

    Note that a *flat-filled* row reads as ink-free whatever its colour, so a
    solid black panel qualifies as readily as a white margin. That is the
    intended behaviour -- the guarantee we need is "no text crosses this row",
    and a uniform row satisfies it regardless of tone.
    """
    uniform = row_ink_fraction(image) <= max_ink
    if not uniform.any():
        return []

    # Locate run boundaries via the diff of the padded boolean mask.
    padded = np.concatenate(([False], uniform, [False]))
    edges = np.diff(padded.astype(np.int8))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)

    return [(int(s), int(e)) for s, e in zip(starts, ends) if e - s >= min_run]


def plan_slices(
    image: np.ndarray,
    target_height: int = 2000,
    max_height: int = 2500,
    min_height: int = 800,
    overlap_ratio: float = 0.15,
) -> list[Slice]:
    """Compute the slice plan for one image.

    Walks top to bottom. At each step it prefers the gutter closest to
    `target_height` within the legal window, and only forces a blind cut when
    the window contains no gutter at all.
    """
    height = image.shape[0]
    if height <= max_height:
        return [Slice(y0=0, y1=height, forced=False)]

    gutter_centers = [(s + e) // 2 for s, e in find_gutters(image)]
    overlap = int(max_height * overlap_ratio)

    slices: list[Slice] = []
    y = 0
    while y < height:
        if height - y <= max_height:
            slices.append(Slice(y0=y, y1=height, forced=False))
            break

        window_lo, window_hi = y + min_height, y + max_height
        preferred = y + target_height
        candidates = [c for c in gutter_centers if window_lo <= c <= window_hi]

        if candidates:
            cut = min(candidates, key=lambda c: abs(c - preferred))
            slices.append(Slice(y0=y, y1=cut, forced=False))
            y = cut
        else:
            cut = window_hi
            slices.append(Slice(y0=y, y1=cut, forced=True))
            # Step back so the next band re-covers whatever the blind cut split.
            y = cut - overlap

    return slices


def iter_slices(image: np.ndarray, **kwargs) -> list[tuple[Slice, np.ndarray]]:
    """Slice plan paired with the corresponding image crops."""
    return [(s, image[s.y0 : s.y1]) for s in plan_slices(image, **kwargs)]
