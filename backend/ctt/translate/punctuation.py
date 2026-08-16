"""Normalise translated Chinese punctuation for comic lettering.

Machine translators routinely emit ASCII punctuation inside Chinese output
("你不喜欢这些选择吗?" rather than "...吗？"). It is not a rendering bug and no
font fixes it -- the model genuinely produced U+003F.

This matters more in comics than in body text. Chinese full-width punctuation
carries its own side-bearing, so a half-width comma between two full-width
glyphs leaves a visibly tight gap, and the line-breaking rules in `kinsoku`
key off the full-width codepoints -- a half-width "?" is not in
LINE_START_FORBIDDEN and will happily start a line.

Only applied to Chinese targets, and only outside Latin runs: "Wi-Fi 6, OK?"
embedded in a Chinese line should keep its ASCII punctuation.
"""

from __future__ import annotations

import re

# Half-width -> full-width, for punctuation that has a distinct CJK form.
FULLWIDTH = {
    ",": "，",
    ".": "。",
    "?": "？",
    "!": "！",
    ":": "：",
    ";": "；",
    "(": "（",
    ")": "）",
    "<": "《",
    ">": "》",
}

# A run of Latin/digits that should keep ASCII punctuation inside it.
_LATIN_RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9\s,.:;!?()'\"-]*[A-Za-z0-9]")

_CJK = re.compile(r"[㐀-鿿　-〿＀-￯]")


def _has_cjk(text: str) -> bool:
    return bool(_CJK.search(text))


# Runs of ASCII dots are an ellipsis, not a sequence of full stops. Mapping
# them per character turns "wait..." into "wait。。。", which is a glaring
# typographic error in Chinese -- the correct form is the two-cell "……".
_ELLIPSIS = re.compile(r"\.{2,}|。{2,}|…{3,}")


def to_fullwidth(text: str) -> str:
    """Convert ASCII punctuation to its CJK form, leaving Latin runs alone."""
    if not text or not _has_cjk(text):
        return text

    text = _ELLIPSIS.sub("……", text)
    protected: list[tuple[int, int]] = [m.span() for m in _LATIN_RUN.finditer(text)]

    def inside_latin(index: int) -> bool:
        return any(start <= index < end for start, end in protected)

    out = []
    for i, char in enumerate(text):
        replacement = FULLWIDTH.get(char)
        if replacement and not inside_latin(i):
            out.append(replacement)
        else:
            out.append(char)
    return "".join(out)


def collapse_spaces(text: str) -> str:
    """Drop spaces that only exist because the source was space-delimited.

    Chinese does not space between characters. Translators often leave the
    source's spacing around punctuation, which reads as a typo. Spaces
    *between* Latin words are preserved.
    """
    # Space adjacent to a CJK character on either side is noise.
    text = re.sub(r"(?<=[㐀-鿿　-〿＀-￯])\s+", "", text)
    text = re.sub(r"\s+(?=[㐀-鿿　-〿＀-￯])", "", text)
    return text.strip()


def normalise(text: str, target_lang: str) -> str:
    """Full clean-up pass for one translated line."""
    if not text or not target_lang.lower().startswith("zh"):
        return text
    return collapse_spaces(to_fullwidth(text))


_LATIN_WORD = re.compile(r"(?<![A-Za-z])[A-Za-z]{2,}(?![A-Za-z])")


def _is_scream(word: str) -> bool:
    """Distinguish "AAAAIIIIIEEEE" from "SHIT".

    Case cannot make this call. Comic source lettering is uppercase
    throughout, so a word the model failed to translate arrives in caps just
    like a scream does -- an earlier lowercase-only rule missed "OH SHIT!"
    entirely while catching "gonna".

    Letter diversity separates them: a drawn-out cry reuses very few letters
    across many characters, whereas real words spend a new letter almost every
    position.
    """
    if len(word) < 4:
        return False
    return len(set(word.lower())) / len(word) < 0.5


# Characters used in Traditional Chinese whose Simplified form is a different
# codepoint. Restricted to unambiguous, high-frequency cases -- a character
# shared by both scripts (利, 亚, 好) must never appear here, or every line
# gets flagged. Any real Chinese sentence contains several of these, so a
# short list detects a script slip reliably.
_TRADITIONAL_ONLY = frozenset(
    "們個這說對時會來過學國樣為麼東車馬鳥魚貝見語話讀買賣錢銀長門問間開關"
    "陽電雲龍鳳麗業樂藥醫應該還進遠邊運達選隨險際雙發豐頭題顏願風飛養馬"
    "點營總結經給紅綠級網練習題課誰讓認識議論證訪評語調談請講謝謹謝"
    "萬與並產嚴麼廣慶憶憲擁據擔壞歡權歲歷歸殺氣沒滿漢潔災爲牽狀獨獲"
    "現實寫寬將專屬島嶺帳幣廠開彈徑復愛態慣憑戲戰擊據擾攝敵數斷"
)


def traditional_chars(text: str, target_lang: str) -> list[str]:
    """Traditional characters found in output that should be Simplified.

    A measured run emitted Traditional on 4 of 5 bubbles despite a zh-Hans
    target -- the prompt was handed the raw tag "zh-Hans", which a 7B model
    does not reliably read as 简体. Naming the language in the prompt fixes
    most of it; this check is the guarantee, so a slip is flagged for review
    instead of shipping mixed scripts.
    """
    if target_lang.strip().lower() not in {"zh-hans", "zh"}:
        return []
    return sorted({c for c in text if c in _TRADITIONAL_ONLY})


def residual_latin(text: str, target_lang: str) -> list[str]:
    """Latin words left untranslated inside otherwise-Chinese output.

    Small instruct models drop the occasional interjection or slang term --
    measured output included "哦哦哦shit！", "这gonna很疯狂" and "OH SHIT! 你的
    臀部...". Prompt rules reduce this but do not eliminate it, so callers use
    this to decide which lines deserve a second pass.
    """
    if not target_lang.lower().startswith("zh") or not _has_cjk(text):
        return []
    return [w for w in _LATIN_WORD.findall(text) if not _is_scream(w)]
