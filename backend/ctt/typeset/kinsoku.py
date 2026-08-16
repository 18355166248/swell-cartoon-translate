"""Chinese line-breaking rules (禁则处理).

Getting these right is most of what separates typeset-looking output from
obviously-machine output. Two rules cover the cases that actually occur in
comic dialogue:

* 行首禁则 -- a line may not *begin* with closing punctuation. Chinese readers
  parse a leading 。or ）as a typo instantly.
* 行尾禁则 -- a line may not *end* with opening punctuation, which would strand
  a 「 or （ away from what it opens.

Both are repaired by 追い出し ("push out"): move the break earlier so the
offending character travels down to the next line together with its neighbour.
Moving the break earlier only ever makes the current line narrower, so a repair
can never turn a fitting line into an overflowing one -- which is why we prefer
it over 追い込み (squeezing the character onto the current line).
"""

from __future__ import annotations

# May not start a line: closing brackets, trailing punctuation, marks that
# attach to the preceding character.
LINE_START_FORBIDDEN = frozenset(
    "。，、．：；！？）］｝〉》」』】〕⟩"
    ")]}>"
    "…～ー·・%‰℃°"
    "”’"
)

# May not end a line: opening brackets and quotes.
LINE_END_FORBIDDEN = frozenset(
    "（［｛〈《「『【〔⟨"
    "([{<"
    "“‘#¥$£"
)


def is_breakable(text: str, index: int) -> bool:
    """Can a line break be placed before `text[index]`?

    `index` must be a real interior position; 0 and len(text) are not breaks.
    """
    if index <= 0 or index >= len(text):
        return False
    if text[index] in LINE_START_FORBIDDEN:
        return False
    if text[index - 1] in LINE_END_FORBIDDEN:
        return False
    return True


def adjust_break(text: str, index: int, min_index: int = 1) -> int:
    """Move a candidate break earlier until it satisfies both rules.

    Returns the adjusted index, or `index` unchanged if no legal position
    exists at or above `min_index` -- an unbreakable run (a long ellipsis, a
    wall of punctuation) must be allowed to overflow rather than loop forever.
    """
    candidate = index
    while candidate > min_index and not is_breakable(text, candidate):
        candidate -= 1
    return candidate if is_breakable(text, candidate) else index


def hangable(char: str) -> bool:
    """Whether a character may hang past the right margin (悬挂标点).

    Full-width terminal punctuation carries its own trailing whitespace, so
    letting it overhang keeps the visual right edge flush. Used by the layout
    engine when measuring a line's effective width.
    """
    return char in "。，、．：；！？"
