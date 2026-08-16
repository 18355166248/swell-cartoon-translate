import pytest

from ctt.translate.punctuation import residual_latin


class TestResidualLatin:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("哦哦哦shit！你的臀部动得越来越快", ["shit"]),
            ("现在是我的turn，准备好，因为这gonna很疯狂！", ["turn", "gonna"]),
            # Comic source is all-caps, so a skipped word arrives in caps too.
            # An earlier lowercase-only rule missed this one entirely.
            ("OH SHIT! 你的臀部动得越来越快了……", ["OH", "SHIT"]),
        ],
    )
    def test_flags_untranslated_words(self, text, expected):
        assert residual_latin(text, "zh-Hans") == expected

    @pytest.mark.parametrize(
        "text",
        [
            "谢谢你那硬棒，亲爱的！",
            "我们必须民主地做出决定。",
            "哦哦哦哦哦哦！你的臀部移动得越来越快……",
        ],
    )
    def test_clean_output_is_not_flagged(self, text):
        assert residual_latin(text, "zh-Hans") == []

    @pytest.mark.parametrize("scream", ["AAAAIIIIIEEEE", "AAAAAAA", "OOOOOOH", "MMMMM"])
    def test_drawn_out_cries_are_not_words(self, scream):
        assert residual_latin(f"{scream}！我不行了", "zh-Hans") == []

    def test_short_interjections_are_still_words(self):
        # "OH" is only two letters, so the letter-diversity rule must not
        # reach it -- it is a word the model failed to translate.
        assert residual_latin("OH！我不行了", "zh-Hans") == ["OH"]

    def test_pure_latin_line_is_ignored(self):
        # No CJK at all means the line was never translated; that is the
        # chain's problem to report, not a residual-word finding.
        assert residual_latin("SOUND EFFECT", "zh-Hans") == []

    def test_non_chinese_targets_are_ignored(self):
        assert residual_latin("哦哦哦shit！", "en") == []

    def test_empty_text(self):
        assert residual_latin("", "zh-Hans") == []
