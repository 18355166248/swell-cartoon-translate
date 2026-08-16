"""Pipeline orchestration.

Stage order and the reasoning behind it:

    0 slice     long strips -> chunks, before any model sees the page
    1 detect    bubbles + text boxes, per chunk, mapped back to page coords
    2 mask      glyph pixels (classical CV, no model)
    3 ocr       per-language routing, on crops only
    4 translate whole page in one batch, in reading order
    5 erase     flat fill, LaMa only where there is real texture
    6 typeset   shape-aware layout with Chinese line-breaking rules

Translation is deliberately a *network/subprocess* call rather than an
in-process model. On a 4GB card a translation model cannot coexist with the
detector and inpainter, and the contention is what makes comparable tools
stutter. Keeping it out of process means the vision stages always run at full
speed.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from . import inpaint
from .translate import Glossary
from .translate.punctuation import residual_latin, traditional_chars
from .types import Block, BlockKind, Page, Project
from .typeset import render_page

log = logging.getLogger(__name__)


CREDIT_MARKERS = (
    "raws", "translator", "proofreader", "redrawer", "typesetter",
    "scanlation", "scans", "cleaner", "editor:", "uploaded", "patreon",
    "discord", "read more", "join us",
)


def looks_like_credits(text: str) -> bool:
    """Does this block look like a scanlation credits panel rather than dialogue?

    Groups paste a staff-roll panel at the top of most chapters, and the
    detector classifies it as a text bubble because that is exactly what it
    looks like. Translating it produces confident nonsense in the middle of
    the page.

    Flagged for review rather than dropped: the markers below also appear in
    real dialogue ("the editor called"), and silently deleting a line the
    reader needed is worse than showing them one they can delete.
    """
    lowered = text.lower()
    hits = sum(1 for marker in CREDIT_MARKERS if marker in lowered)
    return hits >= 2


@dataclass
class StageTimings:
    seconds: dict[str, float] = field(default_factory=dict)

    def record(self, stage: str, elapsed: float) -> None:
        self.seconds[stage] = self.seconds.get(stage, 0.0) + elapsed

    @property
    def total(self) -> float:
        return sum(self.seconds.values())

    def report(self) -> str:
        if not self.seconds:
            return "no stages run"
        width = max(len(s) for s in self.seconds)
        lines = [
            f"  {stage:<{width}}  {value:6.2f}s  {100 * value / self.total:5.1f}%"
            for stage, value in sorted(self.seconds.items(), key=lambda kv: -kv[1])
        ]
        return "\n".join(lines) + f"\n  {'total':<{width}}  {self.total:6.2f}s"


@dataclass
class PageResult:
    page: Page
    image: np.ndarray
    timings: StageTimings
    overflowed: list[Block] = field(default_factory=list)
    glossary_violations: list[tuple[str, str, str]] = field(default_factory=list)
    """(block id, term, expected translation) for human review."""

    target_lang: str = "zh-Hans"

    @property
    def needs_review(self) -> list[Block]:
        """Blocks a human should look at before this page ships."""
        low_confidence = [
            b for b in self.page.blocks
            if b.translatable and 0 < b.source_conf < 0.6
        ]
        credits = [b for b in self.page.blocks if b.translatable and looks_like_credits(b.source_text)]
        # Words the translator would not render into the target language even
        # after a retry pass. A 7B model reliably fails on some slang
        # ("GONNA GET WILD" survived two attempts), and a stray English phrase
        # in a Chinese bubble is exactly what a human should catch in the editor.
        untranslated = [
            b for b in self.page.blocks
            if b.translatable and residual_latin(b.target_text, self.target_lang)
        ]
        wrong_script = [
            b for b in self.page.blocks
            if b.translatable and traditional_chars(b.target_text, self.target_lang)
        ]
        return list(
            {
                id(b): b
                for b in [*self.overflowed, *low_confidence, *credits, *untranslated, *wrong_script]
            }.values()
        )


class Pipeline:
    def __init__(
        self,
        detector,
        ocr,
        translator,
        target_lang: str = "zh-Hans",
        source_lang: str = "auto",
        glossary: Glossary | None = None,
        lama: inpaint.LamaInpainter | None = None,
        detect_threshold: float = 0.35,
    ):
        self.detector = detector
        self.ocr = ocr
        self.translator = translator
        self.target_lang = target_lang
        self.source_lang = source_lang
        self.glossary = glossary or Glossary()
        self.lama = lama
        self.detect_threshold = detect_threshold

    def run_page(
        self,
        image_path: str | Path,
        on_stage: Callable[[str], None] | None = None,
    ) -> PageResult:
        """Translate one page.

        `on_stage` is called as each stage begins. Stages are opaque blocking
        calls, so this is the finest progress the pipeline can honestly
        report -- there is no way to be 40% through an ONNX inference.
        """
        image_path = Path(image_path)
        image = cv2.imread(str(image_path))
        if image is None:
            raise FileNotFoundError(f"could not read {image_path}")

        timings = StageTimings()
        height, width = image.shape[:2]
        report = on_stage or (lambda _: None)

        report("detect")
        with _timed(timings, "detect"):
            blocks = self.detector.detect(image, threshold=self.detect_threshold)

        report("ocr")
        with _timed(timings, "ocr"):
            self.ocr.run(image, blocks)

        translatable = [b for b in blocks if b.translatable]
        report("translate")
        with _timed(timings, "translate"):
            self._translate(translatable)

        report("erase")
        with _timed(timings, "erase"):
            erased, erase_stats = inpaint.erase(image, blocks, lama=self.lama)

        report("typeset")
        with _timed(timings, "typeset"):
            rendered, overflowed = render_page(erased, translatable)

        log.info("erase: %s", erase_stats)

        violations = [
            (block.id, term, expected)
            for block in translatable
            for term, expected in self.glossary.violations(block.source_text, block.target_text)
        ]

        page = Page(
            image_path=str(image_path),
            width=width,
            height=height,
            blocks=blocks,
        )
        return PageResult(
            page=page,
            image=rendered,
            timings=timings,
            overflowed=overflowed,
            glossary_violations=violations,
            target_lang=self.target_lang,
        )

    def _translate(self, blocks: list[Block]) -> None:
        """Translate a page's dialogue as one ordered batch.

        Blocks are already in reading order from detection, so the backend
        sees the conversation as a conversation -- which is what lets it
        resolve pronouns and hold a consistent register.
        """
        pending = [b for b in blocks if not b.edited]
        if not pending:
            return

        source = self.source_lang
        if source == "auto":
            # Majority script across the page beats per-line guessing: one
            # misread bubble should not switch the whole page's source model.
            scripts = [b.source_lang for b in pending if b.source_lang]
            source = max(set(scripts), key=scripts.count) if scripts else "en"

        texts = [b.source_text for b in pending]
        results = self.translator.translate(texts, source, self.target_lang)
        for block, translated in zip(pending, results):
            block.target_text = translated

    def run(self, image_paths: list[str | Path], project_name: str = "") -> tuple[Project, list[PageResult]]:
        project = Project(
            name=project_name,
            source_lang=self.source_lang,
            target_lang=self.target_lang,
            glossary=self.glossary.entries,
        )
        results: list[PageResult] = []
        for path in image_paths:
            result = self.run_page(path)
            project.pages.append(result.page)
            results.append(result)
        return project, results


class _timed:
    def __init__(self, timings: StageTimings, stage: str):
        self.timings = timings
        self.stage = stage

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.timings.record(self.stage, time.perf_counter() - self.start)
        return False
