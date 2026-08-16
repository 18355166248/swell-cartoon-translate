"""Dragging a block must move the translation without moving the erase anchor."""

import numpy as np
import pytest

from ctt import inpaint, mask
from ctt.typeset import fonts, profile_for_block
from ctt.types import Block, BlockKind, Box, TextStyle

pytestmark = pytest.mark.skipif(
    not fonts.available(fonts.DEFAULT_FONT), reason="no CJK font installed"
)


def bubble_page() -> tuple[np.ndarray, Block]:
    """White balloon on a dark ground, with black lettering inside."""
    image = np.full((400, 400, 3), 40, dtype=np.uint8)
    import cv2

    cv2.circle(image, (200, 200), 150, (255, 255, 255), -1)
    cv2.putText(image, "HELLO", (110, 215), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 0, 0), 6)

    block = Block(
        id="b0",
        kind=BlockKind.TEXT_BUBBLE,
        box=Box(x1=100, y1=170, x2=300, y2=230),
        bubble_box=Box(x1=60, y1=60, x2=340, y2=340),
        source_text="HELLO",
        target_text="你好世界",
        style=TextStyle(),
    )
    return image, block


class TestOffsetSeparatesEraseFromRender:
    def test_offset_moves_the_layout_region(self):
        _, block = bubble_page()
        before = profile_for_block(block)
        block.offset = (30.0, -20.0)
        after = profile_for_block(block)

        assert after.center_y == pytest.approx(before.center_y - 20.0, abs=1.0)
        b0, a0 = before.span_at(before.center_y - 5, before.center_y + 5), None
        a0 = after.span_at(after.center_y - 5, after.center_y + 5)
        assert a0[0] == pytest.approx(b0[0] + 30.0, abs=1.0)

    def test_offset_does_not_move_the_erase_anchor(self):
        """The regression this module exists for.

        Folding a drag into `box` relocates the erase off the source
        lettering, which then ghosts back through above the repositioned
        translation.
        """
        image, block = bubble_page()
        anchor = block.box.model_copy()

        block.offset = (60.0, 40.0)
        erased, stats = inpaint.erase(image, [block], trace_polygons=False)

        assert block.box == anchor, "drag must not touch the source-text box"
        assert stats.flat_fills == 1

        # The source lettering is gone from where it actually was. Tested by
        # counting genuinely dark pixels rather than by `min()`: the fill is
        # feathered at the mask rim, so a handful of pixels sit slightly below
        # paper white without any glyph surviving. The lettering was pure
        # black, so a real ghost shows up far below this threshold.
        x1, y1, x2, y2 = anchor.to_int()
        region = erased[y1:y2, x1:x2]
        dark = (region.min(axis=2) < 150).sum()
        assert dark == 0, f"{dark} dark pixels remain -- source text ghosting through"

    def test_zero_offset_is_a_no_op(self):
        _, block = bubble_page()
        plain = profile_for_block(block)
        block.offset = (0.0, 0.0)
        assert profile_for_block(block).center_y == plain.center_y

    def test_offset_survives_serialisation(self):
        _, block = bubble_page()
        block.offset = (12.5, -7.25)
        assert Block.model_validate_json(block.model_dump_json()).offset == (12.5, -7.25)
