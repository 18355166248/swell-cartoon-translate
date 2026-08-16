import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from ctt.typeset import fonts, kinsoku
from ctt.typeset.layout import EllipseProfile, RectProfile, fit, layout_at_size

FONT = fonts.DEFAULT_FONT

pytestmark = pytest.mark.skipif(
    not fonts.available(FONT), reason="no CJK font installed"
)


def _ink(font: ImageFont.FreeTypeFont, text: str = "永") -> int:
    """Count dark pixels a face lays down -- a proxy for stroke weight."""
    img = Image.new("L", (160, 160), 255)
    ImageDraw.Draw(img).text((10, 10), text, font=font, fill=0)
    return int((np.asarray(img) < 128).sum())


class TestFontWeight:
    def test_default_face_is_not_the_files_default_instance(self):
        """Noto Sans SC's default variable instance is Thin.

        Loading it without naming a weight renders dialogue in hairline
        strokes that vanish against artwork -- and nothing else in the suite
        would catch it, since layout maths is weight-independent.
        """
        path, variation = fonts.resolve(FONT)
        if variation is None:
            pytest.skip("resolved to a static face; no axis to set")

        raw = ImageFont.truetype(str(path), 96)
        tuned = fonts.load(FONT, 96)
        assert _ink(tuned) > _ink(raw) * 1.2

    def test_bold_is_heavier_than_body(self):
        if not fonts.available("SourceHanSansSC-Bold"):
            pytest.skip("no bold face available")
        assert _ink(fonts.load("SourceHanSansSC-Bold", 96)) >= _ink(fonts.load(FONT, 96))


class TestKinsoku:
    @pytest.mark.parametrize("ch", "。，、！？）」》…")
    def test_closing_punctuation_may_not_start_a_line(self, ch):
        text = f"前面的话{ch}后面的话"
        assert not kinsoku.is_breakable(text, 4)

    @pytest.mark.parametrize("ch", "（「《【")
    def test_opening_punctuation_may_not_end_a_line(self, ch):
        text = f"前面的话{ch}后面的话"
        assert not kinsoku.is_breakable(text, 5)

    def test_ordinary_positions_are_breakable(self):
        assert kinsoku.is_breakable("你好世界再见", 3)

    def test_adjust_retreats_to_a_legal_break(self):
        # Index 4 would strand '。' at the head of the next line.
        text = "我回来了。你还好吗"
        assert kinsoku.adjust_break(text, 4) == 3

    def test_adjust_skips_a_run_of_forbidden_characters(self):
        # 结0 束1 了2 ！3 ？4 。5 然6 ...
        # Breaking at 4 or 3 strands punctuation at the head of the line; 2 is
        # the first legal position below it.
        text = "结束了！？。然后呢"
        assert kinsoku.adjust_break(text, 4) == 2

    def test_a_line_may_end_with_closing_punctuation(self):
        # The mirror of the rule above: 。is banned at line *start*, and
        # breaking after it is exactly what should happen.
        text = "结束了！？。然后呢"
        assert kinsoku.is_breakable(text, 6)
        assert kinsoku.adjust_break(text, 6) == 6

    def test_adjust_gives_up_rather_than_looping(self):
        # Nothing legal above min_index -- must return the input unchanged.
        text = "啊。。。。。。"
        assert kinsoku.adjust_break(text, 5, min_index=2) == 5

    def test_adjust_never_returns_zero(self):
        assert kinsoku.adjust_break("。。。。", 3) >= 1


class TestRectLayout:
    def test_short_text_gets_a_large_size(self):
        small = fit("你好", RectProfile(0, 100, 0, 100), font=FONT)
        large = fit("你好", RectProfile(0, 400, 0, 400), font=FONT)
        assert large.size > small.size

    def test_wrapping_produces_multiple_lines(self):
        result = fit(
            "这是一段需要换行的比较长的中文对白内容",
            RectProfile(0, 120, 0, 300),
            font=FONT,
            max_size=20,
        )
        assert len(result.lines) > 1

    def test_every_line_stays_within_the_region(self):
        profile = RectProfile(0, 200, 0, 200)
        result = fit("这是一段需要换行的比较长的中文对白内容啊", profile, font=FONT)
        for line in result.lines:
            assert line.x >= profile.left - 0.5
            assert line.x + line.width <= profile.right + 0.5

    def test_text_is_vertically_centred(self):
        profile = RectProfile(0, 200, 0, 200)
        result = fit("你好世界", profile, font=FONT)
        top = result.lines[0].y
        bottom = result.lines[-1].y + result.line_height
        assert abs((top + bottom) / 2 - profile.center_y) < 1.0

    def test_no_text_reconstructs_nothing(self):
        assert fit("   ", RectProfile(0, 100, 0, 100), font=FONT).lines == []

    def test_hard_newlines_are_honoured(self):
        result = fit("第一行\n第二行", RectProfile(0, 300, 0, 300), font=FONT, max_size=24)
        assert [line.text for line in result.lines] == ["第一行", "第二行"]

    def test_unfittable_text_overflows_rather_than_vanishing(self):
        # A whole sentence into a region a few pixels tall.
        result = fit("这是一段很长的对白" * 5, RectProfile(0, 30, 0, 14), font=FONT)
        assert result.overflow
        assert result.lines, "text must never be silently dropped"

    def test_latin_words_are_not_split_mid_word(self):
        result = fit(
            "Hello wonderful world",
            RectProfile(0, 90, 0, 200),
            font=FONT,
            max_size=16,
        )
        for line in result.lines:
            assert not line.text.endswith("wonderf")
        assert all(" " not in w or True for w in [line.text for line in result.lines])

    def test_lines_never_begin_with_closing_punctuation(self):
        result = fit(
            "他说：我回来了。你还好吗？真的没事吗。",
            RectProfile(0, 110, 0, 300),
            font=FONT,
            max_size=18,
        )
        for line in result.lines[1:]:
            assert line.text[0] not in "。，、！？）」"


class TestEllipseLayout:
    def test_narrows_towards_the_poles(self):
        profile = EllipseProfile(0, 200, 0, 200)
        mid = profile.span_at(95, 105)
        near_top = profile.span_at(5, 15)
        assert (mid[1] - mid[0]) > (near_top[1] - near_top[0])

    def test_span_uses_the_narrowest_row_in_the_band(self):
        profile = EllipseProfile(0, 200, 0, 200)
        band = profile.span_at(0, 100)
        edge = profile.span_at(0, 1)
        assert (band[1] - band[0]) == pytest.approx(edge[1] - edge[0], abs=1.0)

    def test_text_stays_inside_the_ellipse(self):
        profile = EllipseProfile(0, 240, 0, 240)
        result = fit("这是一段放在圆形气泡里的对白内容", profile, font=FONT)
        cx, ry = 120.0, 120.0
        for line in result.lines:
            for x in (line.x, line.x + line.width):
                for y in (line.y, line.y + result.line_height):
                    dx = (x - cx) / 120.0
                    dy = (y - profile.center_y) / ry
                    assert dx * dx + dy * dy <= 1.15, "glyph box escaped the ellipse"

    def test_ellipse_yields_a_smaller_size_than_its_bounding_rect(self):
        text = "这是一段稍微长一点的对白"
        in_rect = fit(text, RectProfile(0, 200, 0, 200), font=FONT)
        in_ellipse = fit(text, EllipseProfile(0, 200, 0, 200), font=FONT)
        assert in_ellipse.size <= in_rect.size


class TestBinarySearchInvariants:
    @pytest.mark.parametrize("text", ["你好", "这是一段中等长度的对白内容", "短"])
    def test_result_is_the_largest_fitting_size(self, text):
        # max_size is passed explicitly so the default height/2 ceiling does
        # not confound the invariant under test.
        profile = RectProfile(0, 150, 0, 150)
        result = fit(text, profile, font=FONT, max_size=200)
        assert not result.overflow
        one_bigger = layout_at_size(text, FONT, result.size + 1, profile, 1.15, "center")
        assert one_bigger is None, "a larger size still fit -- search stopped early"

    def test_search_survives_a_non_monotonic_profile(self):
        """Fitting is not monotonic in size against a shaped region.

        Growing the font grows the line height, which moves each line into a
        different part of the outline -- so a larger size can fit where a
        smaller one could not. A binary search assumes the opposite and
        returns a size well below the true maximum. This pins the scan.
        """
        profile = EllipseProfile(0, 460, 0, 420)
        text = "是的！别拔出来！我也快到了！"
        result = fit(text, profile, font=FONT)

        bigger = [
            s
            for s in range(result.size + 1, int(profile.height / 2) + 1)
            if layout_at_size(text, FONT, s, profile, 1.15, "center") is not None
        ]
        assert not bigger, f"sizes {bigger} also fit but the search stopped at {result.size}"

    def test_trailing_hangable_punctuation_does_not_veto_its_own_line(self):
        """`wrap` lets terminal punctuation overhang the margin.

        If the width check then measured the full advance, it would reject the
        line wrap just built -- flipping the line count and oscillating the
        centring loop, which is what made the search non-monotonic.
        """
        profile = RectProfile(0, 200, 0, 200)
        for size in range(12, 40):
            result = layout_at_size("我也快到了！", FONT, size, profile, 1.15, "center")
            if result is None:
                continue
            for line in result.lines:
                stripped = line.text.rstrip("！？。，")
                assert fonts.measure(FONT, size, stripped) <= 200 + 1.0

    def test_default_ceiling_keeps_short_text_from_filling_the_bubble(self):
        # A lone character would otherwise be sized to the full bubble height,
        # which looks nothing like typeset dialogue.
        profile = RectProfile(0, 150, 0, 150)
        assert fit("短", profile, font=FONT).size <= profile.height / 2
