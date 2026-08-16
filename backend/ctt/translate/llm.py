"""OpenAI-compatible backend -- the quality tier.

Points at any server speaking the OpenAI chat API. The intended target on this
hardware is a local llama.cpp / Ollama server, which matters for two reasons:

* It is the only tier that sees whole-page context and a glossary, so it is
  the only one that resolves pronouns and keeps forms of address stable.
* It runs *out of process*. The 4GB card in this machine cannot hold a
  translation model alongside the detector and inpainter, and the contention
  is what made the previous toolchain stutter. A separate server -- even one
  spilling into system RAM at a few tokens/second -- keeps the vision stages
  at full speed. A chapter's dialogue is only a few KB, so a slow generator is
  still seconds of work, not minutes.

Numbering the lines and requiring numbered output is what keeps results
aligned with inputs; free-form output silently drops or merges lines.
"""

from __future__ import annotations

import json
import os
import re

import httpx

from .base import TranslationError
from .glossary import Glossary

# Prompts must name the language, not identify it. Passing the BCP-47 tag
# "zh-Hans" straight into the prompt produced Traditional characters on 4 of 5
# bubbles in a measured run: the tag is a machine identifier, and a 7B model
# does not reliably decode it into "write 简体". Spelling it out fixes that.
LANGUAGE_NAMES = {
    "zh-hans": "Simplified Chinese (简体中文)",
    "zh-hant": "Traditional Chinese (繁體中文)",
    "zh": "Simplified Chinese (简体中文)",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "ru": "Russian",
}


def language_name(code: str) -> str:
    """Human-readable name for a language tag, for use inside prompts."""
    return LANGUAGE_NAMES.get(code.strip().lower(), code)


SYSTEM_PROMPT = """You are a comic translator. You will be given numbered lines of dialogue from a single comic page, in reading order.

Rules:
- Translate every line into {target}.
- Return ONLY a JSON object mapping each number to its translation, e.g. {{"1": "...", "2": "..."}}.
- Keep each line's length close to the original; these go back into speech bubbles.
- Use the surrounding lines for context: resolve pronouns and keep each speaker's register consistent.
- Preserve the tone, including informal or coarse register. Do not soften, censor, or summarise.
- Translate EVERY word, including interjections, exclamations and slang. Never leave
  a source-language word untranslated inside the output.
- Use {target} characters only. Do not mix in another Chinese script variant.
- If a line is pure sound effect or unreadable, return it unchanged.
{glossary}"""
# The two rules above about untranslated words and script variants are not
# padding. Measured on a 10-page run with a local Qwen2.5 7B: without them the
# model left "shit", "gonna get wild" and "Honey" in the Chinese output, and
# one whole page came back in Traditional characters despite a zh-Hans target.


class OpenAICompatTranslator:
    name = "llm"

    def __init__(
        self,
        base_url: str | None = None,
        model: str = "qwen2.5:14b",
        api_key: str | None = None,
        glossary: Glossary | None = None,
        timeout: float = 300.0,
        temperature: float = 0.3,
    ):
        self.base_url = (base_url or os.environ.get("CTT_LLM_URL", "http://localhost:11434/v1")).rstrip("/")
        self.model = model
        self.api_key = api_key or os.environ.get("CTT_LLM_KEY", "no-key")
        self.glossary = glossary or Glossary()
        self.timeout = timeout
        self.temperature = temperature

    def translate(self, texts: list[str], source: str, target: str) -> list[str]:
        if not texts:
            return []

        numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
        hint = self.glossary.as_prompt_hint(texts)
        system = SYSTEM_PROMPT.format(
            target=language_name(target),
            glossary=f"\n{hint}" if hint else "",
        )

        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": numbered},
                    ],
                    "temperature": self.temperature,
                    "response_format": {"type": "json_object"},
                },
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise TranslationError(f"LLM request failed: {exc}") from exc

        if response.status_code != 200:
            raise TranslationError(f"LLM returned {response.status_code}: {response.text[:200]}")

        content = response.json()["choices"][0]["message"]["content"]
        return self._parse(content, len(texts))

    @staticmethod
    def _parse(content: str, expected: int) -> list[str]:
        """Read numbered output back into positional order.

        Falls back to line parsing because `response_format` is advisory on
        most local servers -- llama.cpp and Ollama both honour it only when
        the model cooperates.
        """
        results = [""] * expected

        try:
            data = json.loads(content)
            if isinstance(data, dict):
                for key, value in data.items():
                    match = re.search(r"\d+", str(key))
                    if match:
                        index = int(match.group()) - 1
                        if 0 <= index < expected and isinstance(value, str):
                            results[index] = value.strip()
                if any(results):
                    return results
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

        for line in content.splitlines():
            match = re.match(r"\s*\"?(\d+)\"?\s*[.:)\]]\s*(.+)", line)
            if match:
                index = int(match.group(1)) - 1
                if 0 <= index < expected:
                    results[index] = match.group(2).strip().strip('",')

        if not any(results):
            raise TranslationError("could not parse numbered output from LLM response")
        return results
