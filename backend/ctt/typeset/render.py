"""Rasterise laid-out text onto a page.

Pillow does the drawing. It handles CJK faces and stroked text natively, which
covers everything v1 needs; skia-python is the upgrade path if gradient fills
become necessary for the v2 sound-effects pass.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
from PIL import Image, ImageDraw

from ..types import Block
from . import fonts
from .layout import (
    EllipseProfile,
    LayoutResult,
    PolygonProfile,
    RectProfile,
    WidthProfile,
    fit,
)

BUBBLE_INSET = 0.10
"""Margin for the *ellipse* path, as a fraction of the box's smaller side.

Detector boxes hug the balloon outline, and text set flush against a drawn
border reads as cramped. A tenth is roughly what hand-typesetters leave.
"""

POLYGON_INSET = 0.05
"""Margin for the *polygon* path.

Half the ellipse figure, because the two paths are not comparable. The ellipse
path insets the bounding box and then inscribes an ellipse inside it, so it is
already conservative twice over; the polygon is the balloon's true boundary
and an erosion off it is the only margin applied. Reusing the ellipse figure
here shrinks the usable region enough to drop the fitted size by a third.
"""

FREE_TEXT_INSET = 0.02


def polygon_row_spans(
    polygon: list[tuple[float, float]],
    top: int,
    bottom: int,
    inset: float = 0.0,
) -> list[tuple[float, float]]:
    """Rasterise a polygon and read off its horizontal extent per row.

    `inset` pulls the usable region in from the outline by that many pixels,
    applied as an erosion so the margin is uniform in every direction -- not
    just horizontally, which is what shrinking the spans alone would give.
    Without it, polygon-backed layout runs text flush against the drawn
    balloon border while the ellipse path keeps a tenth of a margin.

    Rows the polygon misses collapse to a zero-width span, which the layout
    engine treats as unusable.
    """
    points = np.asarray(polygon, dtype=np.float64)
    pad = int(math.ceil(inset)) + 1
    x0 = int(np.floor(points[:, 0].min()))
    x1 = int(np.ceil(points[:, 0].max()))
    width = max(1, x1 - x0)
    height = max(1, bottom - top)

    mask = np.zeros((height, width), dtype=np.uint8)
    shifted = np.round(points - [x0, top]).astype(np.int32)
    cv2.fillPoly(mask, [shifted], color=1)

    if inset > 0:
        k = 2 * int(round(inset)) + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        mask = cv2.erode(mask, kernel)

    spans: list[tuple[float, float]] = []
    for row in mask:
        cols = np.flatnonzero(row)
        if cols.size:
            spans.append((float(x0 + cols[0]), float(x0 + cols[-1] + 1)))
        else:
            spans.append((0.0, 0.0))
    return spans


def profile_for_block(block: Block) -> WidthProfile:
    """Pick the region model that best describes where this text may go.

    A traced outline beats an ellipse, and an ellipse beats a rectangle. The
    rectangle is only right for text painted straight onto artwork, which has
    no container to respect.
    """
    dx, dy = block.offset

    if block.polygon:
        polygon = [(x + dx, y + dy) for x, y in block.polygon]
        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        top, bottom = int(min(ys)), int(max(ys))
        inset = min(max(xs) - min(xs), bottom - top) * POLYGON_INSET
        return PolygonProfile(
            row_spans=polygon_row_spans(polygon, top, bottom, inset=inset),
            top=float(top),
            bottom=float(bottom),
        )

    box = block.layout_box.translated(dx, dy)
    if block.bubble_box is not None:
        inset = min(box.width, box.height) * BUBBLE_INSET
        return EllipseProfile(
            left=box.x1 + inset,
            right=box.x2 - inset,
            top=box.y1 + inset,
            bottom=box.y2 - inset,
        )

    inset = min(box.width, box.height) * FREE_TEXT_INSET
    return RectProfile(
        left=box.x1 + inset,
        right=box.x2 - inset,
        top=box.y1 + inset,
        bottom=box.y2 - inset,
    )


def layout_block(block: Block) -> LayoutResult:
    """Lay out one block, honouring a size the editor pinned by hand."""
    profile = profile_for_block(block)
    style = block.style
    if not style.auto_size and style.size > 0:
        from .layout import layout_at_size

        result = layout_at_size(
            block.target_text.strip(),
            style.font,
            int(style.size),
            profile,
            style.line_spacing,
            style.align,
        )
        if result is not None:
            return result
        # Pinned size no longer fits (the user edited the text); fall through
        # to the search rather than silently clipping.

    return fit(
        block.target_text,
        profile,
        font=style.font,
        line_spacing=style.line_spacing,
        align=style.align,
    )


def draw_block(draw: ImageDraw.ImageDraw, block: Block, result: LayoutResult) -> None:
    font = fonts.load(block.style.font, result.size)
    ascent, _ = font.getmetrics()
    # Centre the glyphs within the line box: `line_height` includes leading,
    # and anchoring at the box top would push text visibly low.
    baseline_offset = (result.line_height - result.size) / 2

    stroke_width = int(round(block.style.stroke_width))
    stroke_fill = tuple(block.style.stroke_color) if block.style.stroke_color else None

    for line in result.lines:
        if not line.text:
            continue
        draw.text(
            (line.x, line.y + baseline_offset),
            line.text,
            font=font,
            fill=tuple(block.style.color),
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )


def render_page(image: np.ndarray, blocks: list[Block]) -> tuple[np.ndarray, list[Block]]:
    """Draw every translated block onto a copy of `image` (BGR in, BGR out).

    Returns the rendered page plus the blocks whose text overflowed, so the
    caller can flag them for human review instead of shipping clipped dialogue.
    """
    pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil)

    overflowed: list[Block] = []
    for block in blocks:
        if not block.target_text.strip():
            continue
        result = layout_block(block)
        if result.overflow:
            overflowed.append(block)
        block.style.size = float(result.size)
        draw_block(draw, block, result)

    return cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR), overflowed
