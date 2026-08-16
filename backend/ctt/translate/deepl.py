"""DeepL backend -- the recommended primary for this project.

Chosen over LLM-backed services for a specific reason: DeepL is a traditional
NMT engine with no instruction-following content policy, so it processes adult
comic dialogue without refusing. LLM services return empty strings or refusals
on a meaningful share of this material, which surfaces as blank bubbles rather
than as an error the chain can react to.

Its free tier allows 500k characters/month. Comic dialogue is tiny -- a full
chapter is a few thousand characters -- so that ceiling is effectively out of
reach for personal use.
"""

from __future__ import annotations

import os

import httpx

from .base import TranslationError

FREE_ENDPOINT = "https://api-free.deepl.com/v2/translate"
PRO_ENDPOINT = "https://api.deepl.com/v2/translate"

# DeepL spells its target codes differently from the BCP-47 tags used
# elsewhere in the pipeline.
TARGET_CODES = {
    "zh-Hans": "ZH-HANS",
    "zh-Hant": "ZH-HANT",
    "zh": "ZH",
    "en": "EN-US",
    "ja": "JA",
    "ko": "KO",
}

SOURCE_CODES = {
    "en": "EN", "ko": "KO", "ja": "JA", "es": "ES",
    "zh-Hans": "ZH", "zh-Hant": "ZH", "zh": "ZH",
}


class DeepLTranslator:
    name = "deepl"

    def __init__(
        self,
        api_key: str | None = None,
        endpoint: str | None = None,
        timeout: float = 30.0,
    ):
        self.api_key = api_key or os.environ.get("DEEPL_API_KEY", "")
        if not self.api_key:
            raise ValueError("DeepL needs an API key (DEEPL_API_KEY)")
        # Free-tier keys carry a :fx suffix and must use the free host.
        self.endpoint = endpoint or (
            FREE_ENDPOINT if self.api_key.endswith(":fx") else PRO_ENDPOINT
        )
        self.timeout = timeout

    def translate(self, texts: list[str], source: str, target: str) -> list[str]:
        if not texts:
            return []

        payload: list[tuple[str, str]] = [
            ("target_lang", TARGET_CODES.get(target, target.upper())),
            # Comic dialogue is fragmentary; without this DeepL merges lines
            # across the batch and the results stop lining up with the inputs.
            ("split_sentences", "0"),
        ]
        if source and source != "auto":
            payload.append(("source_lang", SOURCE_CODES.get(source, source.upper())))
        payload.extend(("text", t) for t in texts)

        try:
            response = httpx.post(
                self.endpoint,
                data=payload,
                headers={"Authorization": f"DeepL-Auth-Key {self.api_key}"},
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise TranslationError(f"DeepL request failed: {exc}") from exc

        if response.status_code == 456:
            raise TranslationError("DeepL quota exhausted for this billing period")
        if response.status_code != 200:
            raise TranslationError(f"DeepL returned {response.status_code}: {response.text[:200]}")

        translations = response.json().get("translations", [])
        if len(translations) != len(texts):
            raise TranslationError(
                f"DeepL returned {len(translations)} results for {len(texts)} inputs"
            )
        return [t.get("text", "") for t in translations]
