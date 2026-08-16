"""NLLB-200 backend -- the offline floor.

Last in the chain and the only tier with no network dependency and no content
policy of any kind. Quality is well below the other two -- it translates each
line in isolation, so pronouns and register drift -- but it always answers,
which is what a fallback is for.

The 600M distilled checkpoint is about 2.5GB in fp32 and fits the 4GB card
comfortably. It is loaded lazily so a run that never reaches this tier never
pays for it.
"""

from __future__ import annotations

import logging

from .base import TranslationError

log = logging.getLogger(__name__)

# NLLB uses FLORES-200 codes.
LANG_CODES = {
    "en": "eng_Latn",
    "ko": "kor_Hang",
    "ja": "jpn_Jpan",
    "es": "spa_Latn",
    "fr": "fra_Latn",
    "de": "deu_Latn",
    "zh-Hans": "zho_Hans",
    "zh-Hant": "zho_Hant",
    "zh": "zho_Hans",
}

DEFAULT_MODEL = "facebook/nllb-200-distilled-600M"


class NLLBTranslator:
    name = "nllb"

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: str | None = None,
        batch_size: int = 16,
        cache_dir: str | None = None,
    ):
        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size
        self.cache_dir = cache_dir
        self._tokenizer = None
        self._model = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        except ImportError as exc:
            raise TranslationError(
                "NLLB backend needs `transformers` and `torch` installed"
            ) from exc

        if self.device is None:
            # CPU on purpose when there is no CUDA build: this tier must never
            # contend with the detector for the 4GB card, which is the whole
            # reason translation sits outside the vision pipeline.
            self.device = "cuda" if torch.cuda.is_available() else "cpu"

        from ..paths import hf_cache_dir

        cache = self.cache_dir or str(hf_cache_dir())
        log.info("loading %s on %s (cache: %s)", self.model_name, self.device, cache)
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name, cache_dir=cache)
        self._model = AutoModelForSeq2SeqLM.from_pretrained(
            self.model_name, cache_dir=cache
        ).to(self.device)
        self._model.eval()

    def translate(self, texts: list[str], source: str, target: str) -> list[str]:
        if not texts:
            return []
        self._ensure_loaded()

        import torch

        source_code = LANG_CODES.get(source)
        target_code = LANG_CODES.get(target, "zho_Hans")
        if source_code is None:
            # NLLB has no language detection; assume the most likely source
            # for this project's material rather than failing outright.
            source_code = LANG_CODES["en"]
            log.warning("unknown source %r for NLLB; assuming %s", source, source_code)

        self._tokenizer.src_lang = source_code
        target_id = self._tokenizer.convert_tokens_to_ids(target_code)

        results: list[str] = []
        for start in range(0, len(texts), self.batch_size):
            chunk = texts[start : start + self.batch_size]
            encoded = self._tokenizer(chunk, return_tensors="pt", padding=True, truncation=True, max_length=512)
            encoded = {k: v.to(self.device) for k, v in encoded.items()}
            with torch.inference_mode():
                generated = self._model.generate(
                    **encoded,
                    forced_bos_token_id=target_id,
                    max_new_tokens=256,
                    num_beams=4,
                )
            results.extend(self._tokenizer.batch_decode(generated, skip_special_tokens=True))

        return results
