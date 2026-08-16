"""Font resolution and cached metrics.

Layout binary-searches the font size, so `measure` runs hundreds of times per
block. Both face loading and per-size instances are cached.

Note the `variation` field on FontSpec. A variable font's *default* instance is
not necessarily the one you want -- Noto Sans SC ships with Thin as its
default, so loading it naively renders dialogue in hairline strokes. Comic
lettering wants a medium-to-bold gothic, so the weight is always named
explicitly rather than left to the file's default.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont


@dataclass(frozen=True)
class FontSpec:
    filename: str
    variation: str | None = None
    """Named instance for variable fonts. None for static faces."""


# Logical name -> candidate faces, best first. Bundled files under fonts/ win
# over system ones so output is reproducible across machines.
FONT_CANDIDATES: dict[str, list[FontSpec]] = {
    # Body dialogue. Medium, not Regular: comic lettering reads heavier than
    # book text, and thin strokes disappear against busy artwork.
    "SourceHanSansSC": [
        FontSpec("SourceHanSansSC-Medium.otf"),
        FontSpec("NotoSansSC-VF.ttf", "Medium"),
        FontSpec("msyh.ttc"),
        FontSpec("simhei.ttf"),
    ],
    # Shouting, emphasis.
    "SourceHanSansSC-Bold": [
        FontSpec("SourceHanSansSC-Bold.otf"),
        FontSpec("NotoSansSC-VF.ttf", "Bold"),
        FontSpec("msyhbd.ttc"),
        FontSpec("simhei.ttf"),
    ],
    # Narration boxes, flashbacks.
    "SourceHanSerifSC": [
        FontSpec("SourceHanSerifSC-Regular.otf"),
        FontSpec("NotoSerifSC-VF.ttf", "Regular"),
        FontSpec("simsun.ttc"),
    ],
    "Heiti": [FontSpec("simhei.ttf"), FontSpec("msyh.ttc")],
}

DEFAULT_FONT = "SourceHanSansSC"

_BUNDLED_DIR = Path(__file__).resolve().parents[3] / "fonts"


def _system_font_dirs() -> list[Path]:
    system = platform.system()
    if system == "Windows":
        return [Path("C:/Windows/Fonts")]
    if system == "Darwin":
        return [Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library/Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts"]


@lru_cache(maxsize=64)
def resolve(name: str) -> tuple[Path, str | None]:
    """Locate a font file for a logical name, with its named instance.

    Raises FileNotFoundError rather than silently substituting: a missing CJK
    face renders every glyph as a tofu box, and failing loudly at setup beats
    discovering that in the output image.
    """
    candidates = FONT_CANDIDATES.get(name, [FontSpec(name)])
    search_dirs = [_BUNDLED_DIR, *_system_font_dirs()]

    for spec in candidates:
        direct = Path(spec.filename)
        if direct.is_absolute() and direct.exists():
            return direct, spec.variation
        for directory in search_dirs:
            path = directory / spec.filename
            if path.exists():
                return path, spec.variation
            if directory.exists():
                # Some Linux distros nest faces several levels deep.
                for found in directory.rglob(spec.filename):
                    return found, spec.variation

    raise FileNotFoundError(
        f"No font file found for {name!r}. Tried {[c.filename for c in candidates]} "
        f"under {[str(d) for d in search_dirs]}. Drop a .otf/.ttf into {_BUNDLED_DIR}."
    )


@lru_cache(maxsize=512)
def load(name: str, size: int) -> ImageFont.FreeTypeFont:
    """Load a face at an integer pixel size, with its weight applied."""
    path, variation = resolve(name)
    font = ImageFont.truetype(str(path), size)
    if variation is not None:
        try:
            font.set_variation_by_name(variation)
        except OSError:
            # Static build of a face we expected to be variable. The candidate
            # list already orders by preference, so the default instance is an
            # acceptable stand-in.
            pass
    return font


@lru_cache(maxsize=65536)
def measure(name: str, size: int, text: str) -> float:
    """Advance width of `text` in pixels.

    `getlength` is the advance rather than the ink extent, which is what we
    want: consecutive glyphs are positioned by advance.
    """
    if not text:
        return 0.0
    return load(name, size).getlength(text)


def line_metrics(name: str, size: int) -> tuple[float, float]:
    """(ascent, descent) for a face at a size."""
    return load(name, size).getmetrics()


def available(name: str) -> bool:
    try:
        resolve(name)
    except FileNotFoundError:
        return False
    return True
