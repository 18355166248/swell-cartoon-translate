"""Where downloaded model weights live.

Everything this project downloads goes under one directory so uninstalling is
a single delete. The default is inside the repo rather than the user's global
Hugging Face cache, which is shared with unrelated tools -- on the machine
this was developed against, that cache already held a 1.6GB checkpoint from
another project, and a naive "clear the model cache" cleanup would have taken
it out too.

Override with CTT_MODELS_DIR if the repo lives on a small disk.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT = Path(__file__).resolve().parents[1] / "models"


def models_dir() -> Path:
    path = Path(os.environ.get("CTT_MODELS_DIR", _DEFAULT))
    path.mkdir(parents=True, exist_ok=True)
    return path


def hf_cache_dir() -> Path:
    """Cache root for anything pulled from the Hugging Face Hub."""
    path = models_dir() / "hf"
    path.mkdir(parents=True, exist_ok=True)
    return path
