import pytest

from ctt.translate.llm import language_name
from ctt.translate.punctuation import traditional_chars


class TestLanguageName:
    @pytest.mark.parametrize("code,expected", [
        ("zh-Hans", "Simplified Chinese (简体中文)"),
        ("zh-hans", "Simplified Chinese (简体中文)"),
        ("zh-Hant", "Traditional Chinese (繁體中文)"),
        ("en", "English"),
    ])
    def test_tags_become_readable_names(self, code, expected):
        # The prompt must name the language; the raw tag produced Traditional
        # output on 4 of 5 bubbles in a measured run.
        assert language_name(code) == expected

    def test_unknown_tag_passes_through(self):
        assert language_name("xx-YY") == "xx-YY"


class TestTraditionalDetection:
    def test_flags_traditional_output_for_a_simplified_target(self):
        text = "你應該滿足我們所有人的欲求，而不僅僅是你母親的。"
        assert traditional_chars(text, "zh-Hans")

    def test_clean_simplified_is_not_flagged(self):
        text = "你应该满足我们所有人的欲求，而不仅仅是你母亲的。"
        assert traditional_chars(text, "zh-Hans") == []

    @pytest.mark.parametrize("text", [
        "利亚姆，好好干她！",       # 利 and 亚 exist in both scripts
        "我不知道还能坚持多久。",
        "宝贝，怎么了？",
        "谢谢你，亲爱的！",
    ])
    def test_shared_characters_never_false_positive(self, text):
        # The single most important property: a character common to both
        # scripts must not be in the table, or every line gets flagged.
        assert traditional_chars(text, "zh-Hans") == []

    def test_ignored_when_target_is_traditional(self):
        text = "你應該滿足我們所有人的欲求。"
        assert traditional_chars(text, "zh-Hant") == []

    def test_ignored_for_non_chinese_targets(self):
        assert traditional_chars("這個", "en") == []

    def test_empty(self):
        assert traditional_chars("", "zh-Hans") == []
