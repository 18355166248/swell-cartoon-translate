"""Where each translated page is written.

Flattening a recursive run into one directory destroys the thing that made it
worth running: a series folder holds chapters, and the output has to stay
readable chapter by chapter. Every layout here preserves that grouping.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path


class Layout(str, Enum):
    MIRROR = "mirror"
    """`<output>/<chapter>/page_zh.jpg` -- one output root, structure kept."""

    NESTED = "nested"
    """`<source chapter>/_zh/page.jpg` -- results beside the pages they came
    from, which is convenient for a reader pointed at the series folder."""

    SIBLING = "sibling"
    """`<source chapter>_zh/page.jpg` -- a parallel chapter folder."""

    FLAT = "flat"
    """`<output>/page_zh.jpg`. Only sensible for a single directory."""


SUFFIX = "_zh"


def destination(
    source: Path,
    input_root: Path,
    output_root: Path,
    layout: Layout | str = Layout.MIRROR,
) -> Path:
    """Where the translated copy of `source` belongs.

    `input_root` is the directory the user selected; the path of `source`
    relative to it is what gets preserved.
    """
    layout = Layout(layout)
    source, input_root, output_root = Path(source), Path(input_root), Path(output_root)

    try:
        relative = source.resolve().relative_to(input_root.resolve())
    except ValueError:
        # Explicit file lists can point outside the root; keep the name only.
        relative = Path(source.name)

    if layout is Layout.FLAT:
        return output_root / f"{source.stem}{SUFFIX}{source.suffix}"

    if layout is Layout.MIRROR:
        # The suffix goes on the file, not the folder: the folder is already
        # distinguished by living under the output root.
        return output_root / relative.parent / f"{source.stem}{SUFFIX}{source.suffix}"

    if layout is Layout.NESTED:
        return source.parent / SUFFIX / source.name

    # SIBLING
    return source.parent.with_name(f"{source.parent.name}{SUFFIX}") / source.name


def copy_destination(
    source: Path,
    input_root: Path,
    output_root: Path,
    layout: Layout | str = Layout.MIRROR,
) -> Path:
    """Where a *skipped* file is copied.

    Keeps the original filename -- an untranslated page is the original, and
    naming it `_zh` would claim otherwise. It still has to land in the same
    folder as its translated neighbours so reading order survives.
    """
    layout = Layout(layout)
    translated = destination(source, input_root, output_root, layout)
    return translated.parent / source.name


def output_roots(
    input_root: Path,
    output_root: Path,
    layout: Layout | str = Layout.MIRROR,
) -> list[Path]:
    """Directories a run will write into.

    Discovery excludes these so a re-run never treats last run's output as
    source material. For the in-tree layouts that is not a single directory,
    hence the list.
    """
    layout = Layout(layout)
    if layout in (Layout.MIRROR, Layout.FLAT):
        return [Path(output_root)]
    # NESTED and SIBLING scatter output through the input tree; the name-based
    # rule in `discover` is what catches those.
    return [Path(output_root)]
