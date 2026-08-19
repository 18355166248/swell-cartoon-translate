"""The typeset settings a render is currently using.

`[typeset]` in ctt.toml used to be inert. The fields existed on the config
dataclass and every one of them went unread: insets were module constants in
`render`, `min_size` was a default argument of `fit`, and freshly detected
blocks got a bare `TextStyle()`. Because those defaults happened to equal the
values written in ctt.toml, runs looked correct and nobody noticed the section
did nothing.

The preview tab is what makes it matter -- without this you could drag a
setting, watch the preview move, run the job and get the old output.

Settings are ambient rather than threaded through every call because the path
from `render_page` down to `fit` is four frames deep and passes through
`profile_for_block`, which has no config argument to spare.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

from ..types import Block, TextStyle


@dataclass(frozen=True)
class Typeset:
    """Mirrors `config.TypesetConfig`, minus the config machinery.

    Kept separate so `ctt.typeset` does not import `ctt.config`: the layout
    code is used by tests and by the preview with settings that never came
    from a file.
    """

    font: str = "SourceHanSansSC"
    line_spacing: float = 1.15
    align: str = "center"
    min_size: int = 9

    # Margin for the *ellipse* path, as a fraction of the box's smaller side.
    # Detector boxes hug the balloon outline, and text set flush against a
    # drawn border reads as cramped. A tenth is roughly what hand-typesetters
    # leave.
    bubble_inset: float = 0.10

    free_text_inset: float = 0.02

    # Margin for the *polygon* path, and not configurable, because the two
    # paths are not comparable. The ellipse path insets the bounding box and
    # then inscribes an ellipse in it, so it is conservative twice over; the
    # polygon is the balloon's true boundary and this erosion is the only
    # margin it gets. Reusing the ellipse figure here shrinks the usable
    # region enough to drop the fitted size by a third.
    polygon_inset: float = 0.05


DEFAULT = Typeset()

# A ContextVar and not a module global. The obvious reasoning -- "the job
# manager admits one job at a time, so there is no second render to race with"
# -- is wrong: the preview endpoint is a second render, and FastAPI runs sync
# endpoints in a threadpool while the job thread is inside its typeset stage.
# With a plain global, dragging a slider mid-run would retypeset a real page
# with unsaved preview values. Each thread gets its own context, so the job
# and the preview cannot see each other's settings.
_ACTIVE: ContextVar[Typeset] = ContextVar("ctt_typeset_settings", default=DEFAULT)


def active() -> Typeset:
    """The settings this thread should render with."""
    return _ACTIVE.get()


@contextmanager
def using(settings: Typeset) -> Iterator[Typeset]:
    """Make `settings` active for the duration of the block, on this thread.

    Restoring on the way out is what keeps a preview request -- which renders
    with whatever the user is currently dragging -- from leaking into the next
    page this thread renders.
    """
    token = _ACTIVE.set(settings)
    try:
        yield settings
    finally:
        _ACTIVE.reset(token)


def apply_style(block: Block) -> None:
    """Stamp the active font/spacing/alignment onto a block.

    Skips blocks the user has touched. `edited` already carries exactly that
    meaning elsewhere -- it is what stops a pipeline re-run from overwriting a
    hand-corrected translation -- so a hand-picked font survives a re-render
    for the same reason and by the same flag.
    """
    if block.edited:
        return
    current = active()
    style = block.style
    style.font = current.font
    style.line_spacing = current.line_spacing
    style.align = current.align


def default_style() -> TextStyle:
    """A `TextStyle` carrying the active settings."""
    current = active()
    return TextStyle(
        font=current.font,
        line_spacing=current.line_spacing,
        align=current.align,
    )
