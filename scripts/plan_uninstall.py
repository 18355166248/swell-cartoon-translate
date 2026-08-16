"""Decide which of the candidate packages can safely be uninstalled.

Prints one package name per line on stdout; explanations go to stderr so the
caller can consume the list directly.

Two filters, in order:

1. **Not installed** -- dropped silently. Makes re-running the uninstaller a
   no-op instead of an error.
2. **Still required by something else** -- kept. Six of the nine packages the
   NLLB install pulled in are also declared dependencies of paddlex, and
   removing them blindly breaks the OCR stage.

Filter 2 reads *declared* requirements, which is deliberately conservative:
paddleocr demonstrably ran before any of these were installed, so some of what
it declares it does not actually need at runtime. Erring towards keeping a few
megabytes beats erring towards a broken pipeline.
"""

from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, distributions, version


def normalise(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def installed(name: str) -> bool:
    try:
        version(name)
    except PackageNotFoundError:
        return False
    return True


def dependency_map() -> dict[str, set[str]]:
    """Map package -> installed distributions that declare it as a dependency."""
    users: dict[str, set[str]] = {}
    for dist in distributions():
        name = normalise(dist.metadata["Name"] or "")
        if not name:
            continue
        for requirement in dist.requires or []:
            dep = requirement.split(";")[0].split("[")[0]
            for char in "<>=!~ (":
                dep = dep.split(char)[0]
            dep = normalise(dep)
            if dep:
                users.setdefault(dep, set()).add(name)
    return users


def resolve_kept(candidates: set[str]) -> dict[str, set[str]]:
    """Which candidates must be kept, and who needs them.

    Iterated to a fixed point rather than computed in one pass. A candidate
    that survives becomes a reason to keep *its* dependencies too: Jinja2 is
    held back because fastapi needs it, which in turn means MarkupSafe has to
    stay, even though nothing outside the candidate set names MarkupSafe
    directly. A single pass removes MarkupSafe and leaves a broken Jinja2.
    """
    all_users = dependency_map()
    kept: dict[str, set[str]] = {}

    while True:
        changed = False
        for candidate in candidates:
            if candidate in kept:
                continue
            # Anyone needing this that is not itself being removed.
            blockers = {
                user
                for user in all_users.get(candidate, set())
                if user not in candidates or user in kept
            }
            if blockers:
                kept[candidate] = blockers
                changed = True
        if not changed:
            return kept


def main(argv: list[str]) -> int:
    raw = [a for a in argv if a.strip()]
    if not raw:
        return 0

    # Deduplicate case variants ("Jinja2" and "jinja2" are one package).
    present = {}
    for name in raw:
        key = normalise(name)
        if key not in present and installed(name):
            present[key] = name

    skipped = len(raw) - len(present)
    if skipped:
        print(f"  {skipped} package(s) already absent", file=sys.stderr)

    if not present:
        return 0

    kept = resolve_kept(set(present))

    if kept:
        print("  kept, still required by other packages:", file=sys.stderr)
        for key in sorted(kept):
            print(f"    {present[key]} (needed by {', '.join(sorted(kept[key]))})", file=sys.stderr)

    for key, original in present.items():
        if key not in kept:
            print(original)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
