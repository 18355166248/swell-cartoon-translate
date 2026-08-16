"""Stage 4 -- translation."""

from __future__ import annotations

import logging

from .base import TranslationError, Translator, TranslatorChain
from .glossary import Glossary

log = logging.getLogger(__name__)

__all__ = [
    "Glossary",
    "TranslationError",
    "Translator",
    "TranslatorChain",
    "build_chain",
]


def build_chain(
    backends: list[str] | None = None,
    glossary: Glossary | None = None,
    **kwargs,
) -> TranslatorChain:
    """Assemble the fallback chain, skipping backends that cannot start.

    A missing API key or an unreachable local server is a configuration state,
    not an error: the chain simply drops that tier and carries on with the
    rest. Only an empty chain is fatal.
    """
    # `llamacpp` leads by default: it is the only tier that both sees page
    # context and has no content policy, which is what this project's source
    # material needs. NLLB is kept last as a floor, but note it drops explicit
    # vocabulary outright rather than translating it -- see README.
    names = backends or ["llamacpp", "deepl", "llm", "nllb"]
    built: list[Translator] = []

    for name in names:
        try:
            if name == "deepl":
                from .deepl import DeepLTranslator

                built.append(DeepLTranslator(**kwargs.get("deepl", {})))
            elif name == "llamacpp":
                from .llamacpp import LlamaCppTranslator

                built.append(LlamaCppTranslator(glossary=glossary, **kwargs.get("llamacpp", {})))
            elif name == "llm":
                from .llm import OpenAICompatTranslator

                built.append(OpenAICompatTranslator(glossary=glossary, **kwargs.get("llm", {})))
            elif name == "nllb":
                from .nllb import NLLBTranslator

                built.append(NLLBTranslator(**kwargs.get("nllb", {})))
            else:
                log.warning("unknown translator %r, skipping", name)
        except Exception as exc:  # noqa: BLE001
            log.info("translator %r unavailable: %s", name, exc)

    if not built:
        raise RuntimeError(
            f"No translation backend could be configured from {names}. "
            "Set DEEPL_API_KEY, run a local OpenAI-compatible server, "
            "or install transformers+torch for the offline NLLB tier."
        )
    return TranslatorChain(built)
