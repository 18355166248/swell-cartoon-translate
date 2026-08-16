from ctt.geometry import assign_bubbles
from ctt.types import Block, BlockKind, Box


def text_block(id_: str, x1, y1, x2, y2) -> Block:
    return Block(id=id_, kind=BlockKind.TEXT_BUBBLE, box=Box(x1=x1, y1=y1, x2=x2, y2=y2))


class TestAssignBubbles:
    def test_text_gets_its_enclosing_bubble(self):
        text = text_block("t", 110, 110, 190, 190)
        bubble = Box(x1=100, y1=100, x2=200, y2=200)
        assign_bubbles([text], [bubble])
        assert text.bubble_box == bubble

    def test_text_outside_every_bubble_gets_none(self):
        text = text_block("t", 500, 500, 560, 560)
        assign_bubbles([text], [Box(x1=0, y1=0, x2=100, y2=100)])
        assert text.bubble_box is None

    def test_nested_bubbles_resolve_to_the_tighter_one(self):
        text = text_block("t", 110, 110, 140, 140)
        inner = Box(x1=105, y1=105, x2=150, y2=150)
        outer = Box(x1=100, y1=100, x2=300, y2=300)
        assign_bubbles([text], [outer, inner])
        assert text.bubble_box == inner

    def test_only_one_block_may_claim_a_bubble(self):
        # The regression: a name caption beside a balloon was pulled into it
        # and laid out at balloon-filling size across the dialogue.
        dialogue = text_block("dialogue", 110, 110, 190, 190)
        caption = text_block("caption", 150, 195, 210, 215)
        bubble = Box(x1=100, y1=100, x2=200, y2=220)

        assign_bubbles([dialogue, caption], [bubble])

        owners = [b for b in (dialogue, caption) if b.bubble_box is not None]
        assert len(owners) == 1

    def test_the_better_contained_block_wins_the_bubble(self):
        fully_inside = text_block("inside", 110, 110, 190, 190)
        half_outside = text_block("edge", 150, 190, 260, 240)
        bubble = Box(x1=100, y1=100, x2=200, y2=200)

        assign_bubbles([fully_inside, half_outside], [bubble])

        assert fully_inside.bubble_box == bubble
        assert half_outside.bubble_box is None

    def test_loser_falls_back_to_its_own_box_for_layout(self):
        dialogue = text_block("dialogue", 110, 110, 190, 190)
        caption = text_block("caption", 150, 192, 198, 210)
        bubble = Box(x1=100, y1=100, x2=200, y2=215)

        assign_bubbles([dialogue, caption], [bubble])

        assert caption.bubble_box is None
        # layout_box must still be usable -- it falls back to `box`.
        assert caption.layout_box == caption.box

    def test_separate_bubbles_are_claimed_independently(self):
        left = text_block("l", 10, 10, 90, 90)
        right = text_block("r", 210, 10, 290, 90)
        bubbles = [Box(x1=0, y1=0, x2=100, y2=100), Box(x1=200, y1=0, x2=300, y2=100)]
        assign_bubbles([left, right], bubbles)
        assert left.bubble_box is not None
        assert right.bubble_box is not None
        assert left.bubble_box != right.bubble_box
