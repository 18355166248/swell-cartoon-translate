"""Project configuration.

Before this existed, settings lived in three places at once: CLI flags, five
environment variables, and constants hardcoded across eight modules. Changing
the bubble padding or the OCR crop cap meant editing source.

Precedence, highest first:

    1. CLI flag          -- one-off overrides for a single run
    2. environment       -- secrets and machine-specific paths
    3. ctt.toml          -- the project's actual settings
    4. dataclass default -- what ships

Secrets are deliberately environment-only. `ctt.toml` is meant to be committed,
and an API key in a committed file is a leak waiting to happen; `DEEPL_API_KEY`
and `CTT_LLM_KEY` have no config-file equivalent.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

CONFIG_NAME = "ctt.toml"


@dataclass
class DetectConfig:
    model: str = "int8"
    """int8 | fp32 | small. int8 measured identical to fp32 on this material."""
    threshold: float = 0.35


@dataclass
class SliceConfig:
    """Long-strip slicing. See ctt.slicing for why these bounds matter."""

    target_height: int = 2000
    max_height: int = 2500
    min_height: int = 800
    overlap_ratio: float = 0.15
    max_ink: float = 0.002
    min_gutter_run: int = 8


@dataclass
class OCRConfig:
    languages: list[str] = field(default_factory=lambda: ["latin"])
    max_side: int = 1000
    min_confidence: float = 0.3


@dataclass
class LlamaCppConfig:
    repo_id: str = "mradermacher/Qwen2.5-7B-Instruct-abliterated-v2-GGUF"
    filename: str = "Qwen2.5-7B-Instruct-abliterated-v2.Q4_K_M.gguf"
    model_path: str = ""
    """Explicit GGUF path. Empty means download `filename` from `repo_id`."""
    n_ctx: int = 4096
    n_gpu_layers: int = 0
    """0 keeps the GPU completely free. Raise only when nothing else needs it."""
    n_threads: int = 6
    temperature: float = 0.3
    max_tokens: int = 1024


@dataclass
class LLMConfig:
    base_url: str = "http://localhost:11434/v1"
    model: str = "qwen2.5:14b"
    temperature: float = 0.3
    timeout: float = 300.0


@dataclass
class NLLBConfig:
    model: str = "facebook/nllb-200-distilled-600M"
    batch_size: int = 16
    device: str = ""


@dataclass
class TranslateConfig:
    backends: list[str] = field(default_factory=lambda: ["llamacpp"])
    """Tried in order; each fills gaps the previous one left."""
    llamacpp: LlamaCppConfig = field(default_factory=LlamaCppConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    nllb: NLLBConfig = field(default_factory=NLLBConfig)


@dataclass
class TypesetConfig:
    font: str = "SourceHanSansSC"
    line_spacing: float = 1.15
    align: str = "center"
    min_size: int = 9
    bubble_inset: float = 0.10
    free_text_inset: float = 0.02


@dataclass
class EraseConfig:
    lama_path: str = ""
    """ONNX LaMa for textured backgrounds. Empty disables it -- flat fill
    handles speech balloons, which is all v1 touches."""
    pad: int = 32


@dataclass
class Config:
    target_lang: str = "zh-Hans"
    source_lang: str = "auto"
    models_dir: str = ""
    skip_thumbnails: bool = True
    min_page_bytes: int = 50_000

    detect: DetectConfig = field(default_factory=DetectConfig)
    slicing: SliceConfig = field(default_factory=SliceConfig)
    ocr: OCRConfig = field(default_factory=OCRConfig)
    translate: TranslateConfig = field(default_factory=TranslateConfig)
    typeset: TypesetConfig = field(default_factory=TypesetConfig)
    erase: EraseConfig = field(default_factory=EraseConfig)
    glossary: dict[str, str] = field(default_factory=dict)

    source_path: Path | None = None
    """Where this was loaded from; None means built-in defaults only."""


# Environment overrides: env var -> dotted config path.
ENV_OVERRIDES = {
    "CTT_MODELS_DIR": "models_dir",
    "CTT_GGUF": "translate.llamacpp.model_path",
    "CTT_LLM_URL": "translate.llm.base_url",
    "CTT_TARGET_LANG": "target_lang",
}


def _assign(root: Any, dotted: str, value: Any) -> None:
    *parents, leaf = dotted.split(".")
    target = root
    for name in parents:
        target = getattr(target, name)
    current = getattr(target, leaf)
    # Keep the declared type; TOML and env both hand us strings for ints.
    if isinstance(current, bool):
        value = str(value).strip().lower() in {"1", "true", "yes", "on"} if isinstance(value, str) else bool(value)
    elif isinstance(current, int) and not isinstance(value, bool):
        value = int(value)
    elif isinstance(current, float):
        value = float(value)
    setattr(target, leaf, value)


def _merge(target: Any, data: dict[str, Any], path: str = "") -> list[str]:
    """Apply a parsed TOML table onto a dataclass. Returns unknown keys."""
    unknown: list[str] = []
    known = {f.name for f in fields(target)}

    for key, value in data.items():
        where = f"{path}{key}"
        if key not in known:
            unknown.append(where)
            continue
        current = getattr(target, key)
        if is_dataclass(current) and isinstance(value, dict):
            unknown.extend(_merge(current, value, f"{where}."))
        else:
            _assign(target, key, value)
    return unknown


def find_config(start: Path | None = None) -> Path | None:
    """Search upward from `start` for ctt.toml."""
    here = (start or Path.cwd()).resolve()
    for directory in [here, *here.parents]:
        candidate = directory / CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def load(path: str | Path | None = None, use_env: bool = True) -> tuple[Config, list[str]]:
    """Build a Config from file plus environment.

    Returns (config, warnings). Unknown keys are reported rather than ignored:
    a typo in a config file is otherwise invisible, and the user sees settings
    silently not applying.
    """
    config = Config()
    warnings: list[str] = []

    resolved = Path(path) if path else find_config()
    if resolved and resolved.is_file():
        try:
            data = tomllib.loads(resolved.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"{resolved}: {exc}") from exc
        unknown = _merge(config, data)
        config.source_path = resolved
        warnings += [f"{resolved.name}: unknown setting {key!r}" for key in unknown]
    elif path:
        raise FileNotFoundError(f"config not found: {path}")

    if use_env:
        for variable, dotted in ENV_OVERRIDES.items():
            value = os.environ.get(variable)
            if value:
                _assign(config, dotted, value)

    return config, warnings
