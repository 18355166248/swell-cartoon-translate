import pytest

from ctt.translate.punctuation import collapse_spaces, normalise, to_fullwidth


class TestFullwidth:
    @pytest.mark.parametrize("raw,expected", [
        ("你好,世界", "你好，世界"),
        ("真的吗?", "真的吗？"),
        ("住手!", "住手！"),
        ("他说:好", "他说：好"),
        ("结束了.", "结束了。"),
    ])
    def test_ascii_punctuation_becomes_fullwidth(self, raw, expected):
        assert to_fullwidth(raw) == expected

    @pytest.mark.parametrize("raw", [
        "准备好啦...因为很疯狂",
        "准备好啦。。。因为很疯狂",
        "准备好啦…………因为很疯狂",
    ])
    def test_dot_runs_become_one_ellipsis(self, raw):
        # Per-character mapping turned "..." into "。。。", which is a glaring
        # typographic error in Chinese.
        assert to_fullwidth(raw) == "准备好啦……因为很疯狂"

    def test_single_period_is_still_a_full_stop(self):
        assert to_fullwidth("结束了.") == "结束了。"

    def test_punctuation_inside_a_latin_run_stays_ascii(self):
        # The comma sits between two Latin tokens, so it belongs to the
        # English fragment and must not become a full-width comma.
        assert to_fullwidth("标题是 Hello, world 这本书") == "标题是 Hello, world 这本书"

    def test_punctuation_between_chinese_clauses_converts(self):
        # Here the comma separates Chinese clauses even though a Latin run
        # precedes it, so full-width is correct.
        assert to_fullwidth("他用的是 Wi-Fi 6, 很快") == "他用的是 Wi-Fi 6， 很快"

    def test_pure_latin_text_untouched(self):
        assert to_fullwidth("Hello, world.") == "Hello, world."


class TestCollapseSpaces:
    def test_spaces_around_cjk_are_dropped(self):
        assert collapse_spaces("你好 世界") == "你好世界"

    def test_spaces_between_latin_words_survive(self):
        assert collapse_spaces("Hello world") == "Hello world"


class TestNormalise:
    def test_non_chinese_target_is_untouched(self):
        assert normalise("Hello, world...", "en") == "Hello, world..."

    def test_chinese_target_gets_both_passes(self):
        assert normalise("你好 , 世界...", "zh-Hans") == "你好，世界……"

    def test_empty(self):
        assert normalise("", "zh-Hans") == ""
