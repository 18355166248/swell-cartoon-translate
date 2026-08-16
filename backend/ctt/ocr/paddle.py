"""PaddleOCR engine -- covers Latin scripts and Korean.

PP-OCRv5 recognises 100+ languages including Korean, which is what this
project's source material needs. Both `latin` and `korean` models are small
enough to sit alongside the detector on a 4GB card.
"""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

# PaddleOCR's own language keys.
#
# Note "latin" maps to "en", not to Paddle's own `latin` bundle: PaddleOCR 3.x
# ships no PP-OCRv5 weights under that key and raises "No models are available
# for lang='latin'". The English model covers the Latin scripts this project
# sees (English, Spanish) with the v5 recogniser.
LANGUAGE_KEYS = {
    "latin": "en",
    "english": "en",
    "spanish": "es",
    "korean": "korean",
    "japanese": "japan",
    "chinese": "ch",
}


class PaddleEngine:
    def __init__(self, language: str = "latin", use_gpu: bool = False, **kwargs):
        self.language = language
        self.languages = (language,)
        self.name = f"paddle-{language}"
        self._key = LANGUAGE_KEYS.get(language, language)
        self._use_gpu = use_gpu
        self._kwargs = kwargs
        self._ocr = None

    def _ensure_loaded(self):
        if self._ocr is not None:
            return self._ocr
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise RuntimeError("paddleocr is not installed") from exc

        options = dict(
            lang=self._key,
            # Balloon-finding is already done, and far better, by the comic
            # detector upstream. These three stages only add models to load
            # and time per crop.
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            **self._kwargs,
        )

        try:
            # oneDNN off by default. With it enabled, paddle 3.3.1 aborts
            # inside the Korean recogniser with "ConvertPirAttribute2Runtime
            # Attribute not support [pir::ArrayAttribute<pir::DoubleAttribute>]".
            # The English model happens not to trip it, so the failure looks
            # language-specific rather than like the backend bug it is.
            self._ocr = PaddleOCR(**options, enable_mkldnn=False)
        except TypeError:
            # Older/newer builds without the flag.
            self._ocr = PaddleOCR(**options)
        return self._ocr

    def recognize(self, crop: np.ndarray) -> tuple[str, float]:
        ocr = self._ensure_loaded()
        result = ocr.predict(crop)
        if not result:
            return "", 0.0

        page = result[0]
        texts = page.get("rec_texts") or []
        scores = page.get("rec_scores") or []
        if not texts:
            return "", 0.0

        # Lines come back in reading order; comic dialogue is one utterance
        # split across several, so joining with spaces reconstructs it. The
        # translator re-wraps anyway, so the original breaks carry no meaning.
        text = " ".join(t.strip() for t in texts if t.strip())
        confidence = float(np.mean(scores)) if scores else 0.0
        return text, confidence
