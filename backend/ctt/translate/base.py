"""Stage 4 -- the translation interface and its fallback chain.

Two design points carry most of the quality gain over per-bubble machine
translation:

* **Whole-page batches.** Every bubble on a slice goes out in one call, in
  reading order. A translator that can see "who are you?" immediately before
  "your father" resolves the pronoun; one fed isolated strings cannot.
* **A glossary applied across pages.** Character names and forms of address
  drift between chapters otherwise, which is the single most visible tell of
  machine-translated comics.

Backends are ranked and tried in order. This matters for the target material:
LLM-backed services refuse a nontrivial share of adult comic dialogue and
return empty strings rather than errors, so a chain that ends in a local model
is the difference between a usable page and a page of blank bubbles.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from .punctuation import normalise

log = logging.getLogger(__name__)


class TranslationError(Exception):
    """Backend failed in a way the chain should fall through on."""


@runtime_checkable
class Translator(Protocol):
    name: str

    def translate(self, texts: list[str], source: str, target: str) -> list[str]:
        """Translate a batch, returning one result per input, same order.

        Implementations must return a list of the same length as `texts`.
        An entry may be empty when the backend declined that specific line;
        `TranslatorChain` treats empties as gaps and refills them.
        """
        ...


def _missing(texts: list[str], results: list[str]) -> list[int]:
    """Indices where a non-empty source produced nothing usable."""
    return [
        i
        for i, source in enumerate(texts)
        if source.strip() and (i >= len(results) or not results[i].strip())
    ]


class TranslatorChain:
    """Try each backend in order, filling gaps left by the previous one.

    Partial results are kept rather than discarded. A backend that translates
    nine of ten lines and refuses the tenth has done nine lines of useful
    work; only the refused line falls through to the next backend.
    """

    def __init__(self, backends: list[Translator]):
        if not backends:
            raise ValueError("TranslatorChain needs at least one backend")
        self.backends = backends

    @property
    def name(self) -> str:
        return " -> ".join(b.name for b in self.backends)

    def translate(self, texts: list[str], source: str, target: str) -> list[str]:
        results = [""] * len(texts)
        pending = [i for i, t in enumerate(texts) if t.strip()]

        for backend in self.backends:
            if not pending:
                break
            batch = [texts[i] for i in pending]
            try:
                produced = backend.translate(batch, source, target)
            except Exception as exc:  # noqa: BLE001 - any failure falls through
                log.warning("translator %s failed: %s", backend.name, exc)
                continue

            if len(produced) != len(batch):
                log.warning(
                    "translator %s returned %d results for %d inputs; discarding",
                    backend.name, len(produced), len(batch),
                )
                continue

            for slot, text in zip(pending, produced):
                if text and text.strip():
                    results[slot] = text.strip()

            still_missing = _missing(texts, results)
            if len(still_missing) < len(pending):
                log.info(
                    "translator %s filled %d/%d",
                    backend.name, len(pending) - len(still_missing), len(pending),
                )
            pending = still_missing

        if pending:
            log.warning("%d line(s) untranslated after every backend", len(pending))

        # Applied here rather than per-backend so every tier gets it: all of
        # them emit ASCII punctuation in Chinese output at some rate.
        return [normalise(text, target) for text in results]
