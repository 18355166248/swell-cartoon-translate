"""Core data types.

Everything downstream of detection speaks in *original image coordinates*.
Slicing (Stage 0) is an implementation detail that must never leak: `slicing`
hands back per-slice offsets and `detect` maps boxes home before anything else
sees them.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class BlockKind(str, Enum):
    """Detector classes we care about.

    v1 translates TEXT_BUBBLE only. TEXT_FREE (sound effects / narration
    painted onto artwork) is detected and persisted so the v2 SFX pass has
    data to work with, but is left untouched in the rendered output.
    """

    TEXT_BUBBLE = "text_bubble"
    TEXT_FREE = "text_free"


class Box(BaseModel):
    """Axis-aligned box in original-image pixel coordinates."""

    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    @property
    def center(self) -> tuple[float, float]:
        return (self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2

    def translated(self, dx: float = 0.0, dy: float = 0.0) -> Box:
        return Box(x1=self.x1 + dx, y1=self.y1 + dy, x2=self.x2 + dx, y2=self.y2 + dy)

    def expanded(self, pad: float, bounds: tuple[int, int] | None = None) -> Box:
        """Grow by `pad` on every side, optionally clamped to (width, height)."""
        x1, y1 = self.x1 - pad, self.y1 - pad
        x2, y2 = self.x2 + pad, self.y2 + pad
        if bounds is not None:
            w, h = bounds
            x1, y1 = max(0.0, x1), max(0.0, y1)
            x2, y2 = min(float(w), x2), min(float(h), y2)
        return Box(x1=x1, y1=y1, x2=x2, y2=y2)

    def to_int(self) -> tuple[int, int, int, int]:
        """Integer (x1, y1, x2, y2) for slicing numpy arrays."""
        return int(self.x1), int(self.y1), int(round(self.x2)), int(round(self.y2))


class TextStyle(BaseModel):
    """Rendering parameters for one block.

    `auto_size` means the layout engine binary-searches the font size to fill
    the bubble. The editor sets it False once the user picks a size by hand.
    """

    font: str = "SourceHanSansSC"
    size: float = 0.0
    color: tuple[int, int, int] = (0, 0, 0)
    stroke_color: tuple[int, int, int] | None = None
    stroke_width: float = 0.0
    line_spacing: float = 1.15
    align: str = "center"
    vertical: bool = False
    auto_size: bool = True


class Block(BaseModel):
    """One translatable region."""

    id: str
    kind: BlockKind

    box: Box
    """Tight box around the *source* text, in original-image coordinates.

    This is an observation about the input, so nothing in the editor may move
    it: it anchors erasing. Repositioning the translation uses `offset`.
    """

    offset: tuple[float, float] = (0.0, 0.0)
    """Render-time displacement of the translated text, in pixels.

    Kept separate from `box` because the two answer different questions --
    "where is the text I must erase" versus "where should the replacement
    sit". Folding a drag into `box` moves the erase region off the source
    lettering, which reappears as a ghost above the repositioned translation.
    """

    bubble_box: Box | None = None
    """Enclosing speech bubble, when the text sits inside one."""

    polygon: list[tuple[float, float]] | None = None
    """Bubble outline, used for shape-aware line breaking. None -> use box."""

    source_text: str = ""
    source_lang: str | None = None
    source_conf: float = 0.0

    target_text: str = ""
    style: TextStyle = Field(default_factory=TextStyle)

    edited: bool = False
    """User touched this in the editor -- re-running the pipeline must not
    clobber it."""

    @property
    def layout_box(self) -> Box:
        """Region the translated text is allowed to occupy."""
        return self.bubble_box or self.box

    @property
    def translatable(self) -> bool:
        return self.kind is BlockKind.TEXT_BUBBLE and bool(self.source_text.strip())


class Page(BaseModel):
    image_path: str
    width: int
    height: int
    blocks: list[Block] = Field(default_factory=list)

    @property
    def is_long_strip(self) -> bool:
        """Webtoon-style vertical strip: read top-to-bottom, no Z-order."""
        return self.height > self.width * 3


class Project(BaseModel):
    """The `.cttproj` file. Source images are never modified; output is always
    re-composited from original + this document."""

    version: int = 1
    name: str = ""
    source_lang: str = "auto"
    target_lang: str = "zh-Hans"
    glossary: dict[str, str] = Field(default_factory=dict)
    pages: list[Page] = Field(default_factory=list)
