"""manga-ocr engine -- Japanese only, including vertical text.

Kept separate from Paddle because it is purpose-built for manga: it handles
vertical writing, furigana, and the stylised lettering that general OCR
mangles. Not needed for the current source material (Korean and Latin), but
registered so Japanese pages work without further changes.

It returns no confidence score, so a fixed value is reported. That value is
deliberately below the early-exit threshold in `OCRRouter`, so a genuinely
confident Paddle read can still win on a page that is not actually Japanese.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

ASSUMED_CONFIDENCE = 0.75


class MangaOCREngine:
    name = "manga-ocr"
    languages = ("japanese",)

    def __init__(self, **kwargs):
        try:
            from manga_ocr import MangaOcr
        except ImportError as exc:
            raise RuntimeError("manga-ocr is not installed") from exc
        self._ocr = MangaOcr(**kwargs)

    def recognize(self, crop: np.ndarray) -> tuple[str, float]:
        image = Image.fromarray(crop[:, :, ::-1])  # BGR -> RGB
        text = self._ocr(image)
        return (text.strip(), ASSUMED_CONFIDENCE) if text and text.strip() else ("", 0.0)
