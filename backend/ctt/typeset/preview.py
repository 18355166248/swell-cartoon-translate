"""Render sample balloons so typeset settings can be judged before a run.

Deliberately built on the real `layout_block` / `draw_block` path rather than
approximating it. The parameters being tuned here -- the binary-searched font
size, shape-aware line breaking, the Chinese line-break rules -- have no CSS
equivalent, so a mocked-up preview would look fine while the actual output
still overflowed.

Sample texts span the range that makes those settings visible: one word (where
the size ceiling bites), a normal line, one long enough to wrap several times,
and one that cannot fit at all (where `min_size` decides between shrinking and
flagging for review).
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageDraw

from ..types import Block, BlockKind, Box, TextStyle
from .render import draw_block, layout_block
from .settings import Typeset, using

SAMPLES: list[tuple[str, str]] = [
    ("短句", "住手！"),
    ("常规", "我回来了。你还好吗？"),
    ("多行", "看到母亲和儿子像动物一样交配，真是太性感了……"),
    ("超长", "我在这里坐着做什么？我想我可能小睡了一会儿，"
             "但看起来她还在玩得很开心，所以应该没睡多久。"),
    ("标点", "「等一下！」他喊道……然后呢？（没有下文。）"),
]


def _bubble(width: int, height: int, background: tuple[int, int, int],
            outline: tuple[int, int, int]) -> np.ndarray:
    """A drawn balloon, so the inset is visible against a real border."""
    canvas = np.full((height, width, 3), background, dtype=np.uint8)
    centre = (width // 2, height // 2)
    axes = (int(width * 0.46), int(height * 0.42))
    cv2.ellipse(canvas, centre, axes, 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(canvas, centre, axes, 0, 0, 360, outline, 2)
    return canvas


def render_samples(
    settings: Typeset,
    width: int = 320,
    height: int = 220,
    texts: list[tuple[str, str]] | None = None,
    dark: bool = False,
) -> tuple[np.ndarray, list[dict]]:
    """Draw one balloon per sample. Returns the strip and per-sample facts.

    The facts matter as much as the picture: the fitted size and the overflow
    flag are what the settings actually control, and reading them off an image
    is guesswork.
    """
    samples = texts if texts is not None else SAMPLES
    style = TextStyle(
        font=settings.font,
        line_spacing=settings.line_spacing,
        align=settings.align,
    )
    background = (32, 32, 32) if dark else (235, 235, 235)
    outline = (200, 200, 200) if dark else (40, 40, 40)

    tiles: list[np.ndarray] = []
    facts: list[dict] = []

    # The whole loop runs under the previewed settings so the insets and
    # `min_size` take effect too, not only the three style fields.
    with using(settings):
        for label, text in samples:
            canvas = _bubble(width, height, background, outline)
            # The ellipse the layout engine sees must match the one drawn above,
            # or the preview lies about where text can go.
            box = Box(
                x1=width / 2 - width * 0.46,
                y1=height / 2 - height * 0.42,
                x2=width / 2 + width * 0.46,
                y2=height / 2 + height * 0.42,
            )
            block = Block(
                id=label,
                kind=BlockKind.TEXT_BUBBLE,
                box=box,
                bubble_box=box,
                target_text=text,
                style=style.model_copy(deep=True),
            )

            result = layout_block(block)
            pil = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
            draw_block(ImageDraw.Draw(pil), block, result)
            tiles.append(cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR))

            facts.append({
                "label": label,
                "text": text,
                "size": result.size,
                "lines": len(result.lines),
                "overflow": result.overflow,
            })

    strip = np.hstack(tiles) if tiles else np.zeros((height, width, 3), np.uint8)
    return strip, facts
