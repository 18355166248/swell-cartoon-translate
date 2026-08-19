"""Command-line entry point.

    ctt translate assets/*.jpg -o out/
    ctt detect assets/en4.jpg --visualise      # no OCR/translation needed
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import cv2

from .config import CONFIG_NAME
from .translate import Glossary

if TYPE_CHECKING:
    from .typeset import Typeset


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

MIN_PAGE_BYTES = 50_000
"""Files below this are treated as thumbnails, not pages.

Comic rips routinely ship a parallel set of ~6KB 60x60 thumbnails alongside
the pages -- one real chapter directory here held 284 pages and 126 thumbs.
They are valid JPEGs, so nothing downstream rejects them; they just burn a
detector pass each and pad the project with empty pages.
"""


def _numeric_key(path: Path) -> tuple:
    """Sort page-7 before page-10.

    Plain lexicographic order interleaves chapters wrongly, and page order is
    what gives the translator its conversational context.
    """
    parts = re.split(r"(\d+)", path.stem)
    return tuple(int(p) if p.isdigit() else p for p in parts)


def dataclass_kwargs(obj) -> dict:
    """Dataclass -> kwargs, dropping empty strings.

    An empty string in the config means "unset" (no explicit model path, no
    forced device). Passing it through would override the backend's own
    default with a falsy value.
    """
    import dataclasses

    return {
        f.name: getattr(obj, f.name)
        for f in dataclasses.fields(obj)
        if getattr(obj, f.name) != ""
    }


def typeset_settings(cfg) -> "Typeset":
    """`[typeset]` config -> the settings object the render path reads.

    A conversion rather than passing the config straight through, so that
    `ctt.typeset` never imports `ctt.config`: layout is exercised by tests and
    by the settings preview with values that never came from a file.
    """
    from .typeset import Typeset

    return Typeset(
        font=cfg.font,
        line_spacing=cfg.line_spacing,
        align=cfg.align,
        min_size=cfg.min_size,
        bubble_inset=cfg.bubble_inset,
        free_text_inset=cfg.free_text_inset,
    )


def _expand(patterns: list[str], skip_thumbnails: bool = True,
            min_bytes: int = MIN_PAGE_BYTES) -> list[Path]:
    """Expand globs, since Windows shells do not."""
    paths: list[Path] = []
    for pattern in patterns:
        path = Path(pattern)
        if path.exists() and path.is_file():
            paths.append(path)
        elif path.is_dir():
            paths.extend(p for p in path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
        else:
            paths.extend(Path().glob(pattern))

    if skip_thumbnails:
        kept = [p for p in paths if p.stat().st_size >= min_bytes]
        if len(kept) < len(paths):
            print(f"skipping {len(paths) - len(kept)} file(s) under "
                  f"{min_bytes // 1000}KB (thumbnails)", file=sys.stderr)
        paths = kept

    return sorted(set(paths), key=_numeric_key)


def cmd_detect(args: argparse.Namespace) -> int:
    from .detect import ComicDetector
    from .types import BlockKind

    detector = ComicDetector(variant=args.model, cache_dir=args.cache_dir)
    for path in _expand(args.images):
        image = cv2.imread(str(path))
        if image is None:
            print(f"skip {path}: unreadable", file=sys.stderr)
            continue

        blocks = detector.detect(image, threshold=args.threshold)
        bubble = sum(1 for b in blocks if b.kind is BlockKind.TEXT_BUBBLE)
        free = sum(1 for b in blocks if b.kind is BlockKind.TEXT_FREE)
        print(f"{path.name}: {image.shape[1]}x{image.shape[0]}  text_bubble={bubble} text_free={free}")

        if args.visualise:
            vis = image.copy()
            for block in blocks:
                colour = (0, 200, 0) if block.kind is BlockKind.TEXT_BUBBLE else (0, 120, 255)
                x1, y1, x2, y2 = block.box.to_int()
                cv2.rectangle(vis, (x1, y1), (x2, y2), colour, 2)
                if block.bubble_box:
                    bx1, by1, bx2, by2 = block.bubble_box.to_int()
                    cv2.rectangle(vis, (bx1, by1), (bx2, by2), (255, 0, 0), 2)
            out = Path(args.output or ".") / f"{path.stem}_detect.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(out), vis)
            print(f"  -> {out}")
    return 0


def _load_config(args: argparse.Namespace):
    """Config file plus environment, with CLI flags layered on top.

    A CLI flag only overrides when it was actually given: argparse defaults
    are None here precisely so an unset flag cannot silently beat the config
    file. Anything with a real default lives in `Config`, not in argparse.
    """
    from .config import load

    config, warnings = load(getattr(args, "config", None))
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    overrides = {
        "target": "target_lang",
        "source": "source_lang",
        "model": "detect.model",
        "threshold": "detect.threshold",
        "translators": "translate.backends",
        "ocr_languages": "ocr.languages",
        "lama": "erase.lama_path",
        "cache_dir": "models_dir",
    }
    from .config import _assign

    for flag, dotted in overrides.items():
        value = getattr(args, flag, None)
        if value is not None:
            _assign(config, dotted, value)

    return config


def cmd_config(args: argparse.Namespace) -> int:
    """Print the settings that would actually be used."""
    import dataclasses

    config = _load_config(args)
    origin = config.source_path or "built-in defaults (no ctt.toml found)"
    print(f"# effective configuration\n# source: {origin}\n")

    def show(obj, indent=0):
        for f in dataclasses.fields(obj):
            if f.name == "source_path":
                continue
            value = getattr(obj, f.name)
            if dataclasses.is_dataclass(value):
                print(f"{'  ' * indent}[{f.name}]")
                show(value, indent + 1)
            else:
                print(f"{'  ' * indent}{f.name} = {value!r}")

    show(config)
    return 0


def cmd_translate(args: argparse.Namespace) -> int:
    from .detect import ComicDetector
    from .inpaint import LamaInpainter
    from .ocr import build_router
    from .pipeline import Pipeline
    from .translate import build_chain

    config = _load_config(args)

    paths = _expand(args.images, skip_thumbnails=config.skip_thumbnails,
                    min_bytes=config.min_page_bytes)
    if not paths:
        print("no input images matched", file=sys.stderr)
        return 1

    entries = dict(config.glossary)
    if args.glossary:
        entries.update(json.loads(Path(args.glossary).read_text("utf-8")))
    glossary = Glossary(entries)

    pipeline = Pipeline(
        detector=ComicDetector(variant=config.detect.model,
                               cache_dir=config.models_dir or None),
        ocr=build_router(config.ocr.languages),
        translator=build_chain(
            config.translate.backends,
            glossary=glossary,
            llamacpp=dataclass_kwargs(config.translate.llamacpp),
            llm=dataclass_kwargs(config.translate.llm),
            nllb=dataclass_kwargs(config.translate.nllb),
        ),
        target_lang=config.target_lang,
        source_lang=config.source_lang,
        glossary=glossary,
        lama=LamaInpainter(config.erase.lama_path) if config.erase.lama_path else None,
        detect_threshold=config.detect.threshold,
        typeset=typeset_settings(config.typeset),
    )
    if config.source_path:
        print(f"config     : {config.source_path}")
    print(f"translators: {pipeline.translator.name}")
    print(f"ocr        : {pipeline.ocr.name}")

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    project, results = pipeline.run(paths, project_name=args.name or output.name)

    for path, result in zip(paths, results):
        destination = output / f"{path.stem}_zh{path.suffix}"
        cv2.imwrite(str(destination), result.image)
        print(f"\n{path.name} -> {destination.name}")
        print(result.timings.report())

        review = result.needs_review
        if review:
            print(f"  needs review: {len(review)} block(s)")
            for block in review[:5]:
                print(f"    {block.id}: {block.source_text[:40]!r} -> {block.target_text[:40]!r}")
        for block_id, term, expected in result.glossary_violations:
            print(f"  glossary: {block_id} lost {term!r} (expected {expected!r})")

    project_file = output / "project.cttproj"
    project_file.write_text(project.model_dump_json(indent=2), encoding="utf-8")
    print(f"\nproject -> {project_file}")
    return 0


def _add_override_flags(parser: argparse.ArgumentParser) -> None:
    """Per-run overrides, shared by `translate` and `config`.

    `config` takes them too so you can preview exactly what a given command
    line would resolve to before committing to a long run.

    All default to None: see the note in `main` about why a real argparse
    default would silently outrank ctt.toml.
    """
    parser.add_argument("--target", default=None, help="target language, e.g. zh-Hans")
    parser.add_argument("--source", default=None, help="source language, or auto")
    parser.add_argument("--translators", nargs="+", default=None,
                        help="backend order, e.g. llamacpp deepl nllb")
    parser.add_argument("--ocr-languages", nargs="+", default=None,
                        help="e.g. latin korean japanese")
    parser.add_argument("--lama", default=None,
                        help="path to a LaMa ONNX for textured backgrounds")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ctt", description="Comic translation pipeline")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-c", "--config", default=None,
                        help=f"path to {CONFIG_NAME} (default: search upward from cwd)")
    # Every flag below defaults to None so that "not given" is distinguishable
    # from "given the same value as the default". Real defaults live in
    # ctt/config.py, so an argparse default would silently outrank ctt.toml.
    parser.add_argument("--cache-dir", default=None, help="where to cache model weights")
    parser.add_argument("--model", default=None, choices=["int8", "fp32", "small"])
    parser.add_argument("--threshold", type=float, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    config_cmd = sub.add_parser("config", help="show the settings that would be used")
    _add_override_flags(config_cmd)
    config_cmd.set_defaults(func=cmd_config)

    detect = sub.add_parser("detect", help="detect only; no OCR or translation")
    detect.add_argument("images", nargs="+")
    detect.add_argument("-o", "--output", default=".")
    detect.add_argument("--visualise", action="store_true")
    detect.set_defaults(func=cmd_detect)

    translate = sub.add_parser("translate", help="run the full pipeline")
    translate.add_argument("images", nargs="+")
    translate.add_argument("-o", "--output", default="out")
    translate.add_argument("--name", default="")
    translate.add_argument("--glossary", default=None,
                           help="JSON file of term -> translation, merged over [glossary]")
    _add_override_flags(translate)
    translate.set_defaults(func=cmd_translate)

    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
