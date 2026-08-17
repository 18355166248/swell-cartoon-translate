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
class InputConfig:
    """Which files a run picks up."""

    recursive: bool = True
    """On by default: pointing at a series folder and having every chapter
    translated is the common case, and `skip_output_dirs` makes it safe."""
    min_width: int = 0
    min_bytes: int = 50_000
    min_side: int = 600
    max_aspect: float = 4.0
    skip_output_dirs: bool = True


@dataclass
class OutputConfig:
    """Where results are written, and what to do about earlier runs."""

    layout: str = "mirror"
    """mirror | nested | sibling | flat. See ctt.outputs."""

    copy_skipped: bool = True
    """Copy filtered-out pages into the output unchanged.

    A chapter with holes in it is worse than one with a few untranslated
    pages, and it makes a mis-tuned filter a cosmetic problem rather than a
    lossy one. Our own previous output is never copied.
    """

    overwrite: bool = False
    """Re-translate pages whose output already exists.

    Off by default so a re-run resumes instead of repeating: at roughly 36
    seconds a page, redoing a finished chapter costs hours for no gain.
    """


@dataclass
class Config:
    target_lang: str = "zh-Hans"
    source_lang: str = "auto"
    models_dir: str = ""
    skip_thumbnails: bool = True
    min_page_bytes: int = 50_000
    input: InputConfig = field(default_factory=InputConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

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


def describe(obj: Any = None, path: str = "") -> list[dict[str, Any]]:
    """Flatten a Config into field descriptors for a generated UI.

    Reflected from the dataclasses rather than written out by hand, so the
    settings form cannot drift from the settings that actually exist -- adding
    a field to a dataclass makes it appear in the UI with no further work.
    """
    obj = obj if obj is not None else Config()
    out: list[dict[str, Any]] = []

    for f in fields(obj):
        if f.name in {"source_path", "glossary"}:
            continue
        value = getattr(obj, f.name)
        dotted = f"{path}{f.name}"

        if is_dataclass(value):
            out.extend(describe(value, f"{dotted}."))
            continue

        if isinstance(value, bool):
            kind = "bool"
        elif isinstance(value, int):
            kind = "int"
        elif isinstance(value, float):
            kind = "float"
        elif isinstance(value, list):
            kind = "list"
        else:
            kind = "str"

        out.append({
            "path": dotted,
            "name": f.name,
            "section": dotted.rsplit(".", 1)[0] if "." in dotted else "",
            "type": kind,
            "value": value,
            "doc": FIELD_DOCS.get(dotted, ""),
            "choices": FIELD_CHOICES.get(dotted),
        })
    return out


# Only fields whose correct value is non-obvious. A tooltip on `n_threads`
# would be noise; one on `n_gpu_layers` prevents someone from unknowingly
# handing the GPU to the translator while a game is running.
FIELD_DOCS = {
    "target_lang": "目标语言。zh-Hans 简体，zh-Hant 繁体",
    "source_lang": "auto 表示按整页多数字符判定，不逐句猜",
    "skip_thumbnails": "跳过漫画站附带的小缩略图。实测某话 410 个文件里有 126 个是垃圾图",
    "input.recursive": "递归所有子目录。指向系列文件夹即可一次翻完所有话",
    "input.min_bytes": "小于此体积的文件跳过。缩略图和横幅通常远小于正文页",
    "input.min_side": "宽或高任一小于此值就跳过。长条 webtoon 窄而极高，所以是分别限制两边而不是限制面积",
    "input.max_aspect": "宽/高 超过此值判为横幅、标题卡。只限制横向——竖条漫画常达 20:1，正是要保留的",
    "input.skip_output_dirs": "跳过 _zh / out / translated 这类目录。否则递归会把上次的成品再翻一遍，得到「译文的译文」",
    "input.min_width": "宽度小于此值就跳过，0 = 不启用。仅当该系列扫图宽度一致时才可靠——实测某话正文页宽度从 1001 到 4184 都有，标题横幅反而有 1728 宽",
    "output.layout": "mirror = 输出根目录下保留章节结构；nested = 各章节内建 _zh 子目录；sibling = 生成同级的 章节名_zh 目录；flat = 全部平铺（仅适合单目录）",
    "output.copy_skipped": "把被过滤掉的图原样复制到输出。成品缺页比有几张没翻更糟，也让过滤器调错只是没翻、而不是丢内容",
    "output.overwrite": "重跑时是否重新翻译已有成品。默认关闭 = 断点续跑；一页约 36 秒，整话重来要几小时",
    "detect.threshold": "调低会多检出误报，调高会漏掉小气泡",
    "slicing.max_height": "切片高度硬上限。这是长条 webtoon 不吃爆显存的关键",
    "ocr.languages": "多个引擎按识别置信度择优，代价是每个气泡多跑几次",
    "translate.backends": "按顺序尝试，后面的补前面留下的空缺。nllb 会静默删露骨词汇，成人素材别用",
    "translate.llamacpp.n_gpu_layers": "0 = 完全不碰显卡，玩游戏不受影响。不玩时可调到 20-30 提速",
    "translate.llamacpp.n_threads": "建议取物理核数，不是逻辑核数",
    "typeset.min_size": "小于此字号判定为溢出，标记进 needs_review",
    "typeset.bubble_inset": "气泡内边距占短边比例。手工排版大约留这么多",
    "erase.lama_path": "留空则禁用。气泡是纯色底，纯色填充就够了",
}

FIELD_CHOICES = {
    "target_lang": ["zh-Hans", "zh-Hant"],
    "detect.model": ["int8", "fp32", "small"],
    "typeset.align": ["center", "left", "right"],
    "output.layout": ["mirror", "nested", "sibling", "flat"],
}


def to_toml(config: Config) -> str:
    """Serialise back to ctt.toml.

    Hand-rolled rather than via a library: tomllib is read-only in the stdlib,
    and this document is small and entirely known.
    """
    def fmt(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, list):
            return "[" + ", ".join(f'"{v}"' for v in value) + "]"
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = ["# 由 Web 配置页生成。注释见 README。", ""]
    sections: dict[str, list[str]] = {"": []}

    for field_info in describe(config):
        section = field_info["section"]
        sections.setdefault(section, []).append(
            f'{field_info["name"]} = {fmt(field_info["value"])}'
        )

    lines.extend(sections.pop("", []))
    for section, entries in sections.items():
        lines.append("")
        lines.append(f"[{section}]")
        lines.extend(entries)

    if config.glossary:
        lines.extend(["", "[glossary]"])
        lines.extend(f'{k} = {fmt(v)}' for k, v in config.glossary.items())

    return "\n".join(lines) + "\n"


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
