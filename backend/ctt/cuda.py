"""Make the CUDA build of llama.cpp loadable, without a CUDA Toolkit install.

The `cu124` wheel ships `ggml-cuda.dll` but not the CUDA runtime it links
against, so on a machine with only a driver it fails at import with a bare
"Could not find module ... or one of its dependencies" -- naming neither the
dependency nor the fact that CUDA is what is missing.

The runtime is available as pip packages (`nvidia-cuda-runtime-cu12`,
`nvidia-cublas-cu12`), which drop their DLLs under `site-packages/nvidia/*/bin`.
Adding those directories to the DLL search path is *not* enough on its own:
`ggml-cuda.dll` imports `cublas64_12.dll`, which in turn needs
`cublasLt64_12.dll`, and that transitive lookup does not consult the added
directories. Preloading them explicitly, dependency-first, does work -- once a
DLL is resident, later resolutions find it by name.

Import this before `llama_cpp`. It is a no-op on the CPU-only build.
"""

from __future__ import annotations

import ctypes
import importlib.util
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

# Dependency order matters: cublas needs cublasLt already loaded.
_PRELOAD = ("cudart64_12.dll", "cublasLt64_12.dll", "cublas64_12.dll")

_prepared: bool | None = None


def _nvidia_dll_dirs() -> list[Path]:
    spec = importlib.util.find_spec("nvidia")
    if not spec or not spec.submodule_search_locations:
        return []
    root = Path(spec.submodule_search_locations[0])
    return sorted({p.parent for p in root.rglob("*.dll")})


def prepare() -> bool:
    """Put the CUDA runtime where llama.cpp can find it.

    Returns True if the runtime was loaded. False just means this will run on
    the CPU -- which is the normal case for the CPU-only wheel and not an error.
    Cached: the DLLs only need loading once per process.
    """
    global _prepared
    if _prepared is not None:
        return _prepared

    directories = _nvidia_dll_dirs()
    if not directories:
        _prepared = False
        return False

    for directory in directories:
        try:
            os.add_dll_directory(str(directory))
        except (OSError, AttributeError):
            pass

    loaded = 0
    for name in _PRELOAD:
        path = next((d / name for d in directories if (d / name).exists()), None)
        if path is None:
            log.debug("CUDA runtime component not found: %s", name)
            continue
        try:
            ctypes.CDLL(str(path))
            loaded += 1
        except OSError as exc:
            log.warning("could not preload %s: %s", name, exc)

    _prepared = loaded == len(_PRELOAD)
    if _prepared:
        log.info("CUDA runtime ready (%d libraries preloaded)", loaded)
    return _prepared


def available() -> bool:
    """Whether this llama.cpp build can actually offload to a GPU."""
    prepare()
    try:
        import llama_cpp

        return bool(llama_cpp.llama_supports_gpu_offload())
    except Exception:  # noqa: BLE001 - a CPU-only build is a normal outcome
        return False


def free_vram_mb() -> int:
    """Free VRAM in MB, or -1 if it cannot be determined.

    Used to decide how many layers are safe to offload: the card is shared
    with whatever else is on screen, so the answer changes minute to minute.
    """
    import subprocess

    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        return int(out.stdout.strip().splitlines()[0])
    except Exception:  # noqa: BLE001
        return -1
