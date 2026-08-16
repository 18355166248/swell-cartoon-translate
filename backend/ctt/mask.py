"""Stage 2 -- pixel masks for text strokes and bubble interiors.

Detection gives boxes; erasing and typesetting need pixels. Both jobs are
handled here with classical CV rather than a model:

* Text inside a balloon is high-contrast lettering on a flat ground, which is
  precisely the case Otsu's method was designed for. A segmentation network
  costs VRAM and a download to do the same job no better.
* The balloon *outline* traced here feeds `PolygonProfile`, letting the layout
  engine wrap against the real shape instead of an inscribed ellipse.

`ctt.detect` falls back to a learned segmenter when `looks_flat` reports the
region is too busy for thresholding to be trustworthy.
"""

from __future__ import annotations

import cv2
import numpy as np

from .types import Box


def _crop(image: np.ndarray, box: Box) -> tuple[np.ndarray, int, int]:
    x1, y1, x2, y2 = box.to_int()
    h, w = image.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    return image[y1:y2, x1:x2], x1, y1


def _to_gray(crop: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop


def _border_pixels(arr: np.ndarray, width: int = 2) -> np.ndarray:
    """Pixels around the rim of a 2-D crop -- a reliable background sample."""
    if arr.shape[0] <= 2 * width or arr.shape[1] <= 2 * width:
        return arr.ravel()
    return np.concatenate([
        arr[:width].ravel(),
        arr[-width:].ravel(),
        arr[:, :width].ravel(),
        arr[:, -width:].ravel(),
    ])


def _border_pixels_2d(arr: np.ndarray, width: int = 2) -> np.ndarray:
    """Same, preserving the colour channel: returns (N, C)."""
    channels = arr.shape[2]
    if arr.shape[0] <= 2 * width or arr.shape[1] <= 2 * width:
        return arr.reshape(-1, channels)
    return np.concatenate([
        arr[:width].reshape(-1, channels),
        arr[-width:].reshape(-1, channels),
        arr[:, :width].reshape(-1, channels),
        arr[:, -width:].reshape(-1, channels),
    ])


def text_mask(
    image: np.ndarray,
    box: Box,
    tolerance: int = 28,
    dilate_ratio: float = 0.04,
    min_dilate: int = 3,
    limit: np.ndarray | None = None,
) -> tuple[np.ndarray, int, int]:
    """Binary mask (0/255) of glyph strokes inside `box`.

    Returns (mask, x_offset, y_offset) in original-image coordinates.

    Pixels are selected by *distance from the background colour*, not by an
    Otsu split. Otsu puts its threshold midway between the ink and paper modes,
    which leaves every glyph's anti-aliased rim classified as background -- the
    fill then lands inside that rim and the source text survives as a legible
    grey ghost. Thresholding against the background instead captures the rim
    with the stroke.

    Over-masking is close to free *inside a flat region*: `inpaint.flat_fill`
    paints the background colour, so covering extra background pixels is a
    no-op. That asymmetry is why the tolerance is generous and the dilation
    scales with text size rather than being a fixed few pixels.

    It stops being free the moment the box leaves that region. Text boxes are
    rectangular and balloons are round, so a box's corners routinely overhang
    the outline -- and there, "deviates from the background" selects the
    artwork outside the balloon and the fill paints white over it. Pass
    `limit` (a mask in full-image coordinates, e.g. from `bubble_interior`) to
    confine the result to the balloon.
    """
    crop, ox, oy = _crop(image, box)
    if crop.size == 0:
        return np.zeros((0, 0), np.uint8), ox, oy

    if crop.ndim == 3:
        border = _border_pixels_2d(crop)
        background = np.median(border.reshape(-1, crop.shape[2]), axis=0)
        distance = np.abs(crop.astype(np.int16) - background).max(axis=2)
    else:
        background = float(np.median(_border_pixels(crop)))
        distance = np.abs(crop.astype(np.int16) - background)

    mask = (distance > tolerance).astype(np.uint8) * 255

    # Scale dilation with the text: a 12px caption and a 60px shout need very
    # different margins, and a fixed kernel under-covers the large case.
    size = max(min_dilate, int(min(crop.shape[:2]) * dilate_ratio) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    mask = cv2.dilate(mask, kernel)

    if limit is not None:
        # Clip after dilating, so growth cannot push past the balloon either.
        mask &= limit[oy : oy + mask.shape[0], ox : ox + mask.shape[1]]

    return mask, ox, oy


def _surrounding_ring(image: np.ndarray, box: Box, ring_ratio: float = 0.12) -> np.ndarray:
    """Pixels in a band just *outside* the text box, as (N, C).

    Sampled outside rather than inside because `text_mask` selects by distance
    from the background: its complement is uniform by construction, so any
    statistic taken over `mask == 0` would report the region flat no matter
    what actually lies behind the glyphs.
    """
    h, w = image.shape[:2]
    pad = max(3, int(min(box.width, box.height) * ring_ratio))
    crop, _, _ = _crop(image, box.expanded(pad, bounds=(w, h)))
    if crop.size == 0:
        return np.zeros((0, image.shape[2] if image.ndim == 3 else 1), np.uint8)
    return _border_pixels_2d(crop, width=pad) if crop.ndim == 3 else _border_pixels(crop, pad)[:, None]


def looks_flat(
    image: np.ndarray,
    box: Box,
    mask: np.ndarray | None = None,
    tolerance: int = 18,
    min_agreement: float = 0.85,
) -> bool:
    """Is the ground behind the text a single flat colour?

    Decides between the cheap fill and the LaMa fallback in `ctt.inpaint`.
    `mask` is accepted and ignored, for call-site symmetry with the helpers
    either side of it.

    Measured as "what share of the ring agrees with its own median" rather
    than as a standard deviation. A detector box that runs a little wide
    catches a slice of the drawn bubble outline, and under a std test those
    few hundred black pixels flip a perfectly flat balloon to textured --
    sending it down the LaMa path for no reason. An agreement fraction shrugs
    off a minority of outliers.
    """
    ring = _surrounding_ring(image, box)
    if ring.size == 0:
        return True
    median = np.median(ring, axis=0)
    agrees = np.abs(ring.astype(np.int16) - median).max(axis=1) <= tolerance
    return float(agrees.mean()) >= min_agreement


def background_color(image: np.ndarray, box: Box, mask: np.ndarray | None = None) -> np.ndarray:
    """Colour to paint over the erased glyphs.

    Median of the ring outside the text box. Median rather than mean so a
    stray dark pixel -- a bubble outline clipped by a loose detector box --
    cannot drag the fill toward grey and leave a visible smudge.
    """
    ring = _surrounding_ring(image, box)
    if ring.size == 0:
        channels = image.shape[2] if image.ndim == 3 else 1
        return np.full(channels, 255, dtype=np.uint8)
    return np.median(ring, axis=0).astype(np.uint8)


def _trace_bubble(
    image: np.ndarray,
    bubble_box: Box,
    text_box: Box,
    tolerance: int = 20,
    min_area_ratio: float = 0.12,
) -> tuple[np.ndarray, int, int] | None:
    """Contour of the balloon interior, as (contour, ox, oy).

    The interior is found by colour distance from the balloon's own fill,
    sampled via `background_color` on the ring around the text box -- a region
    guaranteed to be inside the balloon. Thresholding on grey level instead
    (Otsu) fails whenever the surrounding art is merely *light* rather than
    dark: a white balloon on a pale background merges with it, and the traced
    contour swallows both. That inflates the region the layout engine thinks
    it has and lets the eraser paint over artwork.

    Returns None when nothing convincing is found. Rejecting a bad trace
    matters more than salvaging one -- callers fall back to an inscribed
    ellipse, which is conservative in the right direction.
    """
    crop, ox, oy = _crop(image, bubble_box)
    if crop.size == 0 or crop.ndim != 3:
        return None

    fill = background_color(image, text_box).astype(np.int16)
    distance = np.abs(crop.astype(np.int16) - fill).max(axis=2)
    interior = (distance <= tolerance).astype(np.uint8) * 255

    # Absorb the lettering so what we trace is the balloon, not the gaps
    # between glyphs. The kernel scales with the balloon; a fixed one is
    # narrower than the strokes on a large-format page.
    # Kept modest on purpose: a large kernel bridges the balloon to any
    # similarly-toned highlight nearby (pale artwork beside a white balloon),
    # and the merged region then reads as interior.
    ksize = max(7, int(min(crop.shape[:2]) * 0.035) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksize, ksize))
    interior = cv2.morphologyEx(interior, cv2.MORPH_CLOSE, kernel)

    # Keep only the region connected to the text, so a same-coloured patch
    # elsewhere in the box cannot join the contour.
    seed_x = int(np.clip((text_box.center[0] - ox), 0, crop.shape[1] - 1))
    seed_y = int(np.clip((text_box.center[1] - oy), 0, crop.shape[0] - 1))
    count, labels = cv2.connectedComponents(interior)
    if count < 2:
        return None
    seed_label = labels[seed_y, seed_x]
    if seed_label == 0:
        return None
    interior = (labels == seed_label).astype(np.uint8) * 255

    contours, _ = cv2.findContours(interior, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < min_area_ratio * crop.shape[0] * crop.shape[1]:
        return None
    return contour, ox, oy


def bubble_interior(image: np.ndarray, bubble_box: Box, text_box: Box) -> np.ndarray | None:
    """Full-image 0/255 mask of the balloon interior.

    Passed to `text_mask` as `limit` so erasing cannot spill past the outline.
    Eroded slightly so the drawn border itself is never painted over.
    """
    traced = _trace_bubble(image, bubble_box, text_box)
    if traced is None:
        return None
    contour, ox, oy = traced

    h, w = image.shape[:2]
    filled = np.zeros((h, w), np.uint8)
    cv2.fillPoly(filled, [contour + [ox, oy]], color=255)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    return cv2.erode(filled, kernel)


def bubble_polygon(
    image: np.ndarray,
    bubble_box: Box,
    text_box: Box,
) -> list[tuple[float, float]] | None:
    """Trace the balloon outline, in original-image coordinates.

    Returns None when no convincing outline is found, in which case the layout
    engine falls back to an inscribed ellipse.
    """
    traced = _trace_bubble(image, bubble_box, text_box)
    if traced is None:
        return None
    contour, ox, oy = traced

    # Simplify: the layout engine rasterises this per row, and a contour with
    # thousands of vertices buys nothing over a smooth approximation.
    epsilon = 0.005 * cv2.arcLength(contour, True)
    simplified = cv2.approxPolyDP(contour, epsilon, True)
    if len(simplified) < 3:
        return None

    return [(float(p[0][0] + ox), float(p[0][1] + oy)) for p in simplified]
