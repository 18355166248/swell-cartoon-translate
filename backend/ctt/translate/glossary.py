"""Project-level terminology.

Character names and forms of address drifting between pages is the most
visible tell of machine-translated comics -- the same person becomes 哥哥 on
one page and 大哥 on the next. The glossary pins them.

Applied on both sides of the call: as a hint the backend can use, and as a
post-pass that rewrites anything it got wrong anyway. Traditional NMT engines
ignore hints entirely, so the post-pass is what actually enforces consistency.
"""

from __future__ import annotations

import re


class Glossary:
    def __init__(self, entries: dict[str, str] | None = None):
        self.entries = dict(entries or {})
        self._pattern: re.Pattern[str] | None = None
        self._rebuild()

    def _rebuild(self) -> None:
        if not self.entries:
            self._pattern = None
            return
        # Longest first, so "Yeong-sik" wins over "Yeong" where both are keys.
        keys = sorted(self.entries, key=len, reverse=True)
        self._pattern = re.compile("|".join(re.escape(k) for k in keys), re.IGNORECASE)

    def __len__(self) -> int:
        return len(self.entries)

    def add(self, source: str, target: str) -> None:
        self.entries[source] = target
        self._rebuild()

    def relevant_to(self, texts: list[str]) -> dict[str, str]:
        """Entries that actually occur in this batch.

        Sending the whole glossary with every request wastes tokens and
        distracts the model; a page mentioning two characters needs two terms.
        """
        if self._pattern is None:
            return {}
        joined = "\n".join(texts)
        found = {m.group(0) for m in self._pattern.finditer(joined)}
        matched: dict[str, str] = {}
        for key, value in self.entries.items():
            if any(hit.lower() == key.lower() for hit in found):
                matched[key] = value
        return matched

    def violations(self, source: str, translated: str) -> list[tuple[str, str]]:
        """Terms present in `source` whose agreed translation is missing.

        Reports rather than rewrites. The backend rendered the term *some*
        way, and we cannot tell which span it chose -- substituting blind
        would corrupt otherwise-correct text, and a wrong repair is harder to
        spot than a wrong translation. The pipeline surfaces these so the
        editor can flag the block for a human.
        """
        if self._pattern is None or not translated:
            return []
        return [
            (key, value)
            for key, value in self.entries.items()
            if re.search(re.escape(key), source, re.IGNORECASE) and value not in translated
        ]

    def as_prompt_hint(self, texts: list[str]) -> str:
        """Glossary fragment for LLM backends that accept instructions."""
        relevant = self.relevant_to(texts)
        if not relevant:
            return ""
        pairs = "\n".join(f"  {k} -> {v}" for k, v in relevant.items())
        return f"Use these fixed translations for names and terms:\n{pairs}\n"
