"""Stage 3 -- OCR with per-language routing.

The material this project targets is mixed-script *within a single page*:
`assets/en2.jpg` has Spanish dialogue with Korean sound effects, `en4.jpg` has
English dialogue with Korean sound effects. No single recognizer covers that
well, so engines are registered per script and chosen by confidence.

Recognition runs on the cropped text box only, never the full page. Crops are
a few hundred pixels wide, which is why this stage stays cheap even on a strip
that is 14000px tall.
"""

from __future__ import annotations

import logging
from typing import Protocol

import cv2
import numpy as np

from ..types import Block

log = logging.getLogger(__name__)


class OCREngine(Protocol):
    name: str
    languages: tuple[str, ...]

    def recognize(self, crop: np.ndarray) -> tuple[str, float]:
        """Return (text, confidence) for a single cropped text region."""
        ...


class OCRRouter:
    """Runs candidate engines over a crop and keeps the best-scoring result.

    Trying several engines costs more than trusting a language hint, but the
    hint is usually wrong here: detection gives boxes, not scripts, and a page
    routinely mixes two. Confidence-based selection needs no hint at all.

    Once a page has settled on a winner for its dialogue, that engine is tried
    first for later blocks, so the common case converges to one call per box.
    """

    def __init__(self, engines: list[OCREngine], min_confidence: float = 0.3):
        if not engines:
            raise ValueError("OCRRouter needs at least one engine")
        self.engines = engines
        self.min_confidence = min_confidence
        self._preferred: OCREngine | None = None

    @property
    def name(self) -> str:
        return "+".join(e.name for e in self.engines)

    def _ordered(self) -> list[OCREngine]:
        if self._preferred is None:
            return self.engines
        return [self._preferred] + [e for e in self.engines if e is not self._preferred]

    def recognize(self, crop: np.ndarray) -> tuple[str, float, str]:
        """Return (text, confidence, engine_name) for one crop."""
        best_text, best_conf, best_engine = "", 0.0, ""

        for engine in self._ordered():
            try:
                text, confidence = engine.recognize(crop)
            except Exception as exc:  # noqa: BLE001
                log.warning("OCR engine %s failed: %s", engine.name, exc)
                continue

            if text.strip() and confidence > best_conf:
                best_text, best_conf, best_engine = text.strip(), confidence, engine.name
                self._preferred = engine
                # A confident read is not going to be improved on; stop early.
                if confidence >= 0.9:
                    break

        return best_text, best_conf, best_engine

    def run(self, image: np.ndarray, blocks: list[Block]) -> None:
        """Fill `source_text` / `source_conf` on every translatable block."""
        for block in blocks:
            if block.kind.value != "text_bubble":
                continue
            x1, y1, x2, y2 = block.box.to_int()
            crop = image[max(0, y1) : y2, max(0, x1) : x2]
            if crop.size == 0:
                continue
            text, confidence, engine = self.recognize(_downscale(crop))
            block.source_text = text
            block.source_conf = confidence
            if engine:
                block.source_lang = _guess_script(text)


OCR_MAX_SIDE = 1000
"""Longest side a crop is scaled to before recognition.

A guard against pathological crops, not a general speedup: PP-OCR's cost
scales with crop area, so a full-width narration box on a high-resolution
scan can be several thousand pixels wide while its lettering is legible at a
fraction of that. Measured on this project's assets the cap does not trigger
(balloons there are ~350px) and recognition output is byte-identical with it
on or off -- it only bounds the worst case.
"""


def _downscale(crop: np.ndarray, max_side: int = OCR_MAX_SIDE) -> np.ndarray:
    height, width = crop.shape[:2]
    longest = max(height, width)
    if longest <= max_side:
        return crop
    scale = max_side / longest
    return cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)


def _guess_script(text: str) -> str | None:
    """Coarse script detection from codepoint ranges.

    Used to pick the translator's source language. Deliberately crude -- the
    distinction that matters downstream is Korean vs Japanese vs Latin, and
    codepoint ranges settle that without another dependency.
    """
    if not text:
        return None
    if any("가" <= c <= "힣" for c in text):
        return "ko"
    if any("぀" <= c <= "ヿ" for c in text):
        return "ja"
    if any("一" <= c <= "鿿" for c in text):
        return "zh"
    return "en"


def build_router(languages: list[str] | None = None, **kwargs) -> OCRRouter:
    """Assemble engines for the requested scripts, skipping unavailable ones."""
    from .paddle import PaddleEngine

    wanted = languages or ["latin", "korean"]
    engines: list[OCREngine] = []

    for language in wanted:
        try:
            if language == "japanese":
                from .mangaocr import MangaOCREngine

                engines.append(MangaOCREngine(**kwargs.get("mangaocr", {})))
            else:
                engines.append(PaddleEngine(language=language, **kwargs.get("paddle", {})))
        except Exception as exc:  # noqa: BLE001
            log.info("OCR engine for %r unavailable: %s", language, exc)

    if not engines:
        raise RuntimeError(
            f"No OCR engine could be configured from {wanted}. "
            "Install paddleocr (pip install paddleocr paddlepaddle) "
            "or manga-ocr for Japanese."
        )
    return OCRRouter(engines)
