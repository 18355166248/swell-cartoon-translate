"""In-process llama.cpp backend -- the quality tier without a server.

Same role as `llm.OpenAICompatTranslator`, different process model. That one
talks to a server you have to install and keep running; this one loads a GGUF
inside the current process and frees it when the run ends.

That distinction is the whole point on this machine:

* **No background service.** Ollama installs a daemon that starts with Windows
  and holds VRAM while a model is resident. This library exists only while the
  pipeline runs, so it cannot interfere with anything else on the machine.
* **CPU by default.** `n_gpu_layers=0` keeps the 4GB card completely free.
  A chapter's dialogue is a few KB, so even single-digit tokens/second is
  minutes of work, not hours. Raise `n_gpu_layers` only when nothing else
  needs the GPU.

The prompt and the numbered-output parser are shared with the HTTP backend --
they are the part that keeps results aligned with inputs, and they should not
drift between the two tiers.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .base import TranslationError
from .glossary import Glossary
from .llm import SYSTEM_PROMPT, OpenAICompatTranslator, language_name
from .punctuation import residual_latin

log = logging.getLogger(__name__)

DEFAULT_REPO = "mradermacher/Qwen2.5-7B-Instruct-abliterated-v2-GGUF"
DEFAULT_FILE = "Qwen2.5-7B-Instruct-abliterated-v2.Q4_K_M.gguf"


class LlamaCppTranslator:
    """Translate through a locally loaded GGUF model."""

    name = "llamacpp"

    def __init__(
        self,
        model_path: str | None = None,
        repo_id: str = DEFAULT_REPO,
        filename: str = DEFAULT_FILE,
        glossary: Glossary | None = None,
        n_ctx: int = 4096,
        n_gpu_layers: int = 0,
        n_threads: int | None = None,
        # Greedy by default. Translation has a right answer, so sampling only
        # adds variance; it also measured faster (9.6s vs 10.5s on the same
        # input) and makes a re-run reproducible.
        temperature: float = 0.0,
        max_tokens: int = 1024,
        verbose: bool = False,
    ):
        self.model_path = model_path or os.environ.get("CTT_GGUF")
        self.repo_id = repo_id
        self.filename = filename
        self.glossary = glossary or Glossary()
        self.n_ctx = n_ctx
        self.n_gpu_layers = n_gpu_layers
        self.n_threads = n_threads
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.verbose = verbose
        self._llm = None

    # ------------------------------------------------------------------ load

    def _resolve_model(self) -> Path:
        if self.model_path:
            path = Path(self.model_path)
            if not path.exists():
                raise TranslationError(f"GGUF not found: {path}")
            return path

        from ..paths import models_dir

        target = models_dir() / "gguf" / self.filename
        if target.exists():
            return target

        try:
            from huggingface_hub import hf_hub_download
        except ImportError as exc:
            raise TranslationError("huggingface-hub is needed to fetch the GGUF") from exc

        target.parent.mkdir(parents=True, exist_ok=True)
        log.info("downloading %s/%s (~4.7GB, once)", self.repo_id, self.filename)
        downloaded = hf_hub_download(
            repo_id=self.repo_id,
            filename=self.filename,
            local_dir=str(target.parent),
        )
        return Path(downloaded)

    def _ensure_loaded(self):
        if self._llm is not None:
            return self._llm
        try:
            from llama_cpp import Llama
        except ImportError as exc:
            raise TranslationError(
                "llama-cpp-python is not installed. Install the prebuilt CPU wheel:\n"
                "  pip install llama-cpp-python "
                "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu"
            ) from exc

        path = self._resolve_model()
        log.info("loading %s (n_gpu_layers=%d)", path.name, self.n_gpu_layers)
        self._llm = Llama(
            model_path=str(path),
            n_ctx=self.n_ctx,
            n_gpu_layers=self.n_gpu_layers,
            n_threads=self.n_threads,
            verbose=self.verbose,
        )
        return self._llm

    def close(self) -> None:
        """Release the model. Called automatically on context exit."""
        self._llm = None

    def __enter__(self) -> LlamaCppTranslator:
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False

    # ------------------------------------------------------------- translate

    def translate(self, texts: list[str], source: str, target: str) -> list[str]:
        if not texts:
            return []

        llm = self._ensure_loaded()
        hint = self.glossary.as_prompt_hint(texts)
        system = SYSTEM_PROMPT.format(
            target=language_name(target), glossary=f"\n{hint}" if hint else ""
        )
        numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))

        try:
            response = llm.create_chat_completion(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": numbered},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # noqa: BLE001
            raise TranslationError(f"llama.cpp generation failed: {exc}") from exc

        content = response["choices"][0]["message"]["content"] or ""
        # Reuse the HTTP tier's parser: same output contract, same failure modes.
        results = OpenAICompatTranslator._parse(content, len(texts))
        return self._retry_untranslated(llm, texts, results, target)

    def _retry_untranslated(
        self,
        llm,
        texts: list[str],
        results: list[str],
        target: str,
    ) -> list[str]:
        """Re-translate lines that came back with English words still in them.

        A 7B model follows "translate every word" most of the time but not
        always, and the misses are conspicuous -- "哦哦哦shit！" in the middle
        of a Chinese page. Re-asking with the offending words named fixes most
        of them.

        One retry, not a loop: a line the model will not translate twice is not
        going to yield on a third attempt, and the original is kept so the
        bubble is never left empty.
        """
        stragglers = {
            i: words
            for i, text in enumerate(results)
            if text and (words := residual_latin(text, target))
        }
        if not stragglers:
            return results

        log.info("retrying %d line(s) with untranslated words", len(stragglers))
        indices = sorted(stragglers)
        # Re-translate from the *source*, not from the flawed output. Asking the
        # model to repair its own previous answer reliably produced that answer
        # again, verbatim; giving it the English original and naming the words
        # it skipped gives it something to actually do.
        skipped = sorted({w for words in stragglers.values() for w in words})
        name = language_name(target)

        # The retry note goes in the *user* message so the system prompt stays
        # byte-identical to the main call. llama.cpp caches the prompt prefix,
        # and a second system prompt evicts it: measured over five pages,
        # alternating two system prompts cost 29.2s of prompt processing where
        # keeping one cost 10.3s. Same instruction, same model -- only its
        # placement changes.
        listing = "\n".join(f"{n + 1}. {texts[i]}" for n, i in enumerate(indices))
        listing += (
            f"\n\n(Retry: a previous attempt left these words untranslated — "
            f"{', '.join(skipped)}. Every word must appear in {name}, including "
            "interjections, exclamations and slang.)"
        )
        system = SYSTEM_PROMPT.format(
            target=name,
            glossary=f"\n{self.glossary.as_prompt_hint(texts)}"
            if self.glossary.as_prompt_hint(texts)
            else "",
        )

        try:
            response = llm.create_chat_completion(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": listing},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
            )
            fixed = OpenAICompatTranslator._parse(
                response["choices"][0]["message"]["content"] or "", len(indices)
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("retry pass failed, keeping first-pass output: %s", exc)
            return results

        for n, i in enumerate(indices):
            candidate = fixed[n].strip()
            # Only accept an improvement; a retry that reintroduces English or
            # returns nothing must not overwrite a usable line.
            if candidate and len(residual_latin(candidate, target)) < len(stragglers[i]):
                results[i] = candidate
        return results
