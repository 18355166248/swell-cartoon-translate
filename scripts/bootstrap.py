"""Font check and model download, shared by setup.ps1 and setup.sh.

Lives in a real file rather than inside a shell here-string: embedding Python
in PowerShell here-strings is fragile (the closing delimiter has its own
column rules, and `from` reads as a PowerShell keyword when it misparses), and
a real file can be run and tested on its own:

    python scripts/bootstrap.py --check-fonts
    python scripts/bootstrap.py --models
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def check_fonts() -> bool:
    from ctt.typeset import fonts

    if fonts.available(fonts.DEFAULT_FONT):
        path = fonts.resolve(fonts.DEFAULT_FONT)
        print(f"  found: {path}")
        return True
    print(f"  no CJK font found; drop a .otf/.ttf into {ROOT / 'fonts'}")
    return False


def fetch_models(skip_llm: bool = False) -> bool:
    """Download whatever the configured pipeline needs.

    Both downloads are resumable and skip existing files, so re-running after
    a failed or interrupted setup costs nothing.
    """
    logging.basicConfig(level=logging.INFO, format="  %(message)s")
    ok = True

    try:
        from ctt.detect import ComicDetector

        print("  detector (42MB)...")
        ComicDetector()
        print("  detector ready")
    except Exception as exc:  # noqa: BLE001
        print(f"  detector FAILED: {exc}", file=sys.stderr)
        ok = False

    if skip_llm:
        return ok

    try:
        from ctt.config import load

        config, _ = load()
        if "llamacpp" not in config.translate.backends:
            print("  llamacpp not in [translate].backends; skipping GGUF")
            return ok

        from ctt.translate.llamacpp import LlamaCppTranslator

        print("  translation model (4.4GB, be patient)...")
        path = LlamaCppTranslator(
            repo_id=config.translate.llamacpp.repo_id,
            filename=config.translate.llamacpp.filename,
        )._resolve_model()
        print(f"  model ready: {path.name}")
    except Exception as exc:  # noqa: BLE001
        print(f"  translation model FAILED: {exc}", file=sys.stderr)
        ok = False

    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Setup helper")
    parser.add_argument("--check-fonts", action="store_true")
    parser.add_argument("--models", action="store_true")
    parser.add_argument("--skip-llm", action="store_true",
                        help="fetch the detector but not the 4.4GB GGUF")
    args = parser.parse_args(argv)

    if not (args.check_fonts or args.models):
        parser.error("nothing to do; pass --check-fonts and/or --models")

    ok = True
    if args.check_fonts:
        ok &= check_fonts()
    if args.models:
        ok &= fetch_models(skip_llm=args.skip_llm)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
