"""Find the pages to translate, and say why anything was skipped.

Recursion makes selection non-obvious: point at a series folder and you pick
up chapters, but also output folders, cover art, banners and whatever else
the download left behind. Guessing wrong costs hours of GPU-free CPU time, so
this module is built to be previewed before it is run -- every exclusion
carries a reason the UI can show.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

OUTPUT_DIR_NAMES = {"_zh", "_out", "out", "output", "translated", "汉化"}
"""Directory names that hold *our own* output.

Without this, a recursive run over a series folder re-translates the pages it
produced on the previous run -- and because the translation is already
Chinese, the result is a translation of a translation.
"""


@dataclass
class Candidate:
    path: Path
    width: int = 0
    height: int = 0
    size: int = 0
    reason: str = ""
    """Empty means included."""
    copyable: bool = True
    """Whether a skipped file should still be copied to the output.

    True for files that are part of the chapter but carry no dialogue worth
    translating -- covers, banners, credit cards. Copying them keeps the
    output readable end to end, and means a filter that guesses wrong costs
    an untranslated page rather than a missing one.

    False for files that are not source material at all: our own previous
    output. Copying those would duplicate them into the new run.
    """

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "name": self.path.name,
            "parent": str(self.path.parent),
            "width": self.width,
            "height": self.height,
            "size": self.size,
            "reason": self.reason,
            "copyable": self.copyable,
        }

    @property
    def included(self) -> bool:
        return not self.reason


@dataclass
class Filters:
    min_width: int = 0
    """Smallest allowed width, in pixels. 0 disables the check.

    Useful when a series is scanned at a consistent width, since anything
    narrower is then chrome rather than a page. Off by default because that
    consistency cannot be assumed: measured across one real chapter the pages
    ranged from 1001px to 4184px wide, and its title banner was 1728px --
    wider than the narrowest genuine page. Width is a good filter only when
    you have looked at the distribution first.
    """
    min_bytes: int = 50_000
    min_side: int = 600
    """Smallest allowed width or height.

    Thumbnails and UI chrome are small in *both* dimensions; a legitimate
    webtoon strip is narrow but very tall, so this must be a floor on each
    side independently rather than on total area.
    """
    max_aspect: float = 4.0
    """Reject extreme letterboxes -- banners and title cards.

    Deliberately one-sided: it applies to width/height only. Vertical strips
    routinely run 20:1 the other way and are exactly what we want to keep.
    """
    skip_output_dirs: bool = True


def _read_dimensions(path: Path) -> tuple[int, int]:
    """Width and height from the header alone; no decode."""
    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.size
    except Exception:  # noqa: BLE001 - unreadable files are reported, not raised
        return (0, 0)


def _numeric_key(path: Path) -> tuple:
    """Sort page-7 before page-10, and keep chapters grouped."""
    import re

    parts = re.split(r"(\d+)", path.as_posix())
    return tuple(int(p) if p.isdigit() else p for p in parts)


def discover(
    root: Path | str,
    recursive: bool = False,
    filters: Filters | None = None,
    exclude_dirs: list[Path] | None = None,
) -> list[Candidate]:
    """List every image under `root`, each marked included or skipped.

    Returns candidates in reading order, including the excluded ones, so a
    caller can show the user exactly what a run would and would not touch.
    """
    root = Path(root)
    filters = filters or Filters()
    excluded_roots = [Path(p).resolve() for p in (exclude_dirs or [])]

    paths = (
        [p for p in root.rglob("*") if p.is_file()]
        if recursive
        else [p for p in root.iterdir() if p.is_file()]
    )

    candidates: list[Candidate] = []
    for path in sorted(paths, key=_numeric_key):
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue

        candidate = Candidate(path=path, size=path.stat().st_size)
        resolved = path.resolve()

        # Ordered cheapest-first: a path check beats a stat, which beats
        # opening the file to read its header.
        if any(
            resolved == root_dir or root_dir in resolved.parents
            for root_dir in excluded_roots
        ):
            candidate.reason = "在输出目录内"
            candidate.copyable = False
        elif filters.skip_output_dirs and any(
            part.lower() in OUTPUT_DIR_NAMES for part in path.parts
        ):
            candidate.reason = "疑似输出目录"
            candidate.copyable = False
        elif candidate.size < filters.min_bytes:
            candidate.reason = f"文件过小 {candidate.size // 1024}KB"
        else:
            width, height = _read_dimensions(path)
            candidate.width, candidate.height = width, height
            if width == 0:
                candidate.reason = "无法读取"
                candidate.copyable = False
            elif filters.min_width and width < filters.min_width:
                candidate.reason = f"宽度不足 {width}px"
            elif min(width, height) < filters.min_side:
                candidate.reason = f"尺寸过小 {width}×{height}"
            elif height > 0 and width / height > filters.max_aspect:
                candidate.reason = f"过于扁平 {width}×{height}"

        candidates.append(candidate)

    return candidates


def summarise(candidates: list[Candidate]) -> dict:
    """Counts and grouped reasons, for the preview panel."""
    included = [c for c in candidates if c.included]
    skipped = [c for c in candidates if not c.included]

    reasons: dict[str, int] = {}
    for candidate in skipped:
        # Collapse the varying numbers so the summary stays short.
        key = candidate.reason.split(" ")[0]
        reasons[key] = reasons.get(key, 0) + 1

    folders: dict[str, int] = {}
    for candidate in included:
        parent = str(candidate.path.parent)
        folders[parent] = folders.get(parent, 0) + 1

    return {
        "total": len(candidates),
        "included": len(included),
        "skipped": len(skipped),
        "reasons": reasons,
        "folders": [{"path": k, "count": v} for k, v in sorted(folders.items())],
        "estimated_seconds": len(included) * 36,
    }
