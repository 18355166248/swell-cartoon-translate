import pytest

from ctt.typeset import fonts
from ctt.typeset.layout import RectProfile, fit

FONT = fonts.DEFAULT_FONT

pytestmark = pytest.mark.skipif(not fonts.available(FONT), reason="no CJK font installed")


def joined(result) -> list[str]:
    return [line.text for line in result.lines]


class TestLatinWordBreaking:
    def test_latin_word_embedded_in_chinese_is_not_split(self):
        # Regression: "看到母亲和儿子像动物一样交配，真是太sexy了……" rendered as
        # "...真是太se" / "xy了……". Retreating to the last space cannot help --
        # a Latin word inside Chinese has no spaces around it.
        result = fit(
            "看到母亲和儿子像动物一样交配，真是太sexy了",
            RectProfile(0, 190, 0, 300),
            font=FONT,
            max_size=22,
        )
        for line in joined(result):
            assert not line.endswith("se"), f"split mid-word: {joined(result)}"
            assert not line.startswith("xy"), f"split mid-word: {joined(result)}"

    @pytest.mark.parametrize("word", ["sexy", "gonna", "wild", "OK", "Wi"])
    def test_no_line_starts_or_ends_mid_word(self, word):
        result = fit(
            f"这是一段比较长的中文对白内容真是太{word}了对吧",
            RectProfile(0, 150, 0, 400),
            font=FONT,
            max_size=20,
        )
        lines = joined(result)
        for i, line in enumerate(lines[:-1]):
            # If a line ends with letters, the next must not begin with them.
            if line and line[-1].isascii() and line[-1].isalpha():
                nxt = lines[i + 1]
                assert not (nxt and nxt[0].isascii() and nxt[0].isalpha()), (
                    f"word split across lines: {lines}"
                )

    def test_spaced_latin_still_breaks_at_spaces(self):
        result = fit(
            "Hello wonderful world again",
            RectProfile(0, 100, 0, 300),
            font=FONT,
            max_size=16,
        )
        for line in joined(result):
            assert not line.endswith("wonderf")

    def test_word_longer_than_the_line_still_breaks(self):
        # It has to go somewhere; the alternative is an infinite loop.
        result = fit(
            "supercalifragilisticexpialidocious的意思",
            RectProfile(0, 60, 0, 400),
            font=FONT,
            max_size=14,
        )
        assert len(result.lines) > 1
        assert all(line.text for line in result.lines)
