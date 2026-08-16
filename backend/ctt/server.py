"""FastAPI service backing the web editor.

The editor is where output goes from "mostly right" to shippable, so the API
is built around one idea: **the project document is the source of truth and
the rendered page is disposable**. Every edit mutates blocks and re-renders
from the original image, so nothing degrades across repeated edits the way it
would if we kept painting over a previous render.

Models are loaded once at startup and shared. On a 4GB card that is not an
optimisation, it is a requirement -- reloading the detector per request would
thrash VRAM against whatever else is resident.
"""

from __future__ import annotations

import base64
import logging
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from .config import CONFIG_NAME as CONFIG_FILE
from .pipeline import Pipeline
from .types import Block, Page, Project
from .typeset import layout_block, render_page

log = logging.getLogger(__name__)

app = FastAPI(title="ctt", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATE: dict[str, object] = {"project": Project(), "project_path": None}


class BlockUpdate(BaseModel):
    target_text: str | None = None
    font: str | None = None
    size: float | None = None
    align: str | None = None
    line_spacing: float | None = None
    color: tuple[int, int, int] | None = None
    dx: float | None = None
    dy: float | None = None


class ProjectPath(BaseModel):
    path: str


@lru_cache(maxsize=8)
def _load_image(path: str) -> np.ndarray:
    image = cv2.imread(path)
    if image is None:
        raise HTTPException(404, f"cannot read image {path}")
    return image


def _project() -> Project:
    return STATE["project"]  # type: ignore[return-value]


def _page(index: int) -> Page:
    pages = _project().pages
    if not 0 <= index < len(pages):
        raise HTTPException(404, f"no page {index}")
    return pages[index]


def _find_block(page: Page, block_id: str) -> Block:
    for block in page.blocks:
        if block.id == block_id:
            return block
    raise HTTPException(404, f"no block {block_id}")


@app.get("/api/project")
def get_project() -> Project:
    return _project()


@app.post("/api/project/open")
def open_project(body: ProjectPath) -> Project:
    path = Path(body.path)
    if not path.exists():
        raise HTTPException(404, f"{path} does not exist")
    project = Project.model_validate_json(path.read_text("utf-8"))
    STATE["project"] = project
    STATE["project_path"] = str(path)
    return project


@app.post("/api/project/save")
def save_project(body: ProjectPath | None = None) -> dict:
    target = body.path if body else STATE.get("project_path")
    if not target:
        raise HTTPException(400, "no path given and no project open")
    Path(target).write_text(_project().model_dump_json(indent=2), encoding="utf-8")
    STATE["project_path"] = str(target)
    return {"saved": str(target)}


@app.patch("/api/pages/{index}/blocks/{block_id}")
def update_block(index: int, block_id: str, update: BlockUpdate) -> Block:
    """Apply an edit and mark the block as human-touched.

    The `edited` flag is what stops a later pipeline re-run from overwriting
    the correction -- see `Pipeline._translate`.
    """
    block = _find_block(_page(index), block_id)

    if update.target_text is not None:
        block.target_text = update.target_text
    if update.font is not None:
        block.style.font = update.font
    if update.size is not None:
        # An explicit size pins it; the layout engine stops searching.
        block.style.size = update.size
        block.style.auto_size = False
    if update.align is not None:
        block.style.align = update.align
    if update.line_spacing is not None:
        block.style.line_spacing = update.line_spacing
    if update.color is not None:
        block.style.color = update.color
    if update.dx or update.dy:
        # Accumulate into `offset`, never into `box`. `box` marks where the
        # source lettering is and anchors erasing; moving it would relocate
        # the erase off the original text, which then ghosts back through.
        dx, dy = update.dx or 0.0, update.dy or 0.0
        block.offset = (block.offset[0] + dx, block.offset[1] + dy)

    block.edited = True
    return block


@app.post("/api/pages/{index}/blocks/{block_id}/reset")
def reset_block(index: int, block_id: str) -> Block:
    """Hand a block back to the layout engine."""
    block = _find_block(_page(index), block_id)
    block.style.auto_size = True
    block.offset = (0.0, 0.0)
    block.edited = False
    return block


@app.get("/api/pages/{index}/blocks/{block_id}/fit")
def preview_fit(index: int, block_id: str) -> dict:
    """Lay out one block without rendering -- drives live editor feedback."""
    block = _find_block(_page(index), block_id)
    result = layout_block(block)
    return {
        "size": result.size,
        "overflow": result.overflow,
        "lines": [
            {"text": line.text, "x": line.x, "y": line.y, "width": line.width}
            for line in result.lines
        ],
    }


@app.get("/api/pages/{index}/render")
def render(index: int, original: bool = False, quality: int = 85) -> Response:
    """Re-composite the page from the original image plus current blocks."""
    page = _page(index)
    image = _load_image(page.image_path)

    if original:
        encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])[1]
        return Response(encoded.tobytes(), media_type="image/jpeg")

    from . import inpaint

    translatable = [b for b in page.blocks if b.translatable]
    erased, _ = inpaint.erase(image, page.blocks, trace_polygons=False)
    rendered, _ = render_page(erased, translatable)
    encoded = cv2.imencode(".jpg", rendered, [cv2.IMWRITE_JPEG_QUALITY, quality])[1]
    return Response(encoded.tobytes(), media_type="image/jpeg")


@app.post("/api/pages/{index}/export")
def export_page(index: int, body: ProjectPath) -> dict:
    page = _page(index)
    image = _load_image(page.image_path)

    from . import inpaint

    translatable = [b for b in page.blocks if b.translatable]
    erased, _ = inpaint.erase(image, page.blocks, trace_polygons=False)
    rendered, _ = render_page(erased, translatable)

    destination = Path(body.path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(destination), rendered)
    return {"exported": str(destination)}


@app.get("/api/glossary")
def get_glossary() -> dict[str, str]:
    return _project().glossary


@app.put("/api/glossary")
def put_glossary(entries: dict[str, str]) -> dict[str, str]:
    _project().glossary = entries
    return entries


@app.get("/api/health")
def health() -> dict:
    project = _project()
    return {
        "ok": True,
        "pages": len(project.pages),
        "blocks": sum(len(p.blocks) for p in project.pages),
        "project_path": STATE.get("project_path"),
    }


# --------------------------------------------------------------------- 配置 ---


@app.get("/api/config")
def get_config() -> dict:
    """Current settings plus enough metadata to generate the form."""
    from .config import describe, find_config, load

    config, warnings = load()
    return {
        "source": str(config.source_path) if config.source_path else None,
        "default_path": str(find_config() or Path.cwd() / CONFIG_FILE),
        "fields": describe(config),
        "glossary": config.glossary,
        "warnings": warnings,
    }


class ConfigUpdate(BaseModel):
    fields: dict[str, object] = {}
    """Dotted path -> value, e.g. {"translate.llamacpp.n_threads": 8}."""
    glossary: dict[str, str] | None = None
    path: str | None = None


@app.put("/api/config")
def put_config(body: ConfigUpdate) -> dict:
    from .config import _assign, describe, load, to_toml

    config, _ = load(body.path)
    known = {f["path"] for f in describe(config)}

    unknown = [key for key in body.fields if key not in known]
    if unknown:
        raise HTTPException(400, f"unknown setting(s): {', '.join(sorted(unknown))}")

    for key, value in body.fields.items():
        _assign(config, key, value)
    if body.glossary is not None:
        config.glossary = body.glossary

    target = Path(body.path or config.source_path or (Path.cwd() / CONFIG_FILE))
    target.write_text(to_toml(config), encoding="utf-8")
    return {"saved": str(target), "fields": describe(config), "glossary": config.glossary}


# --------------------------------------------------------------------- 任务 ---


class JobRequest(BaseModel):
    input_dir: str | None = None
    input_paths: list[str] | None = None
    output_dir: str
    overrides: dict[str, object] = {}
    """Per-run config overrides; not written back to ctt.toml."""
    limit: int | None = None
    """Translate only the first N pages -- for checking quality before
    committing to a whole chapter."""


@app.post("/api/jobs")
def create_job(body: JobRequest) -> dict:
    from .cli import IMAGE_SUFFIXES, MIN_PAGE_BYTES, _numeric_key
    from .config import _assign, load
    from .jobs import MANAGER

    if MANAGER.busy:
        raise HTTPException(409, "a job is already running")

    if body.input_paths:
        paths = [Path(p) for p in body.input_paths]
    elif body.input_dir:
        directory = Path(body.input_dir)
        if not directory.is_dir():
            raise HTTPException(400, f"not a directory: {directory}")
        paths = [p for p in directory.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES]
    else:
        raise HTTPException(400, "give input_dir or input_paths")

    config, _ = load()
    for key, value in body.overrides.items():
        _assign(config, key, value)

    if config.skip_thumbnails:
        paths = [p for p in paths if p.stat().st_size >= config.min_page_bytes]
    paths = sorted(paths, key=_numeric_key)
    if body.limit:
        paths = paths[: body.limit]

    if not paths:
        raise HTTPException(400, "no images matched")

    def build_pipeline():
        from .cli import dataclass_kwargs
        from .detect import ComicDetector
        from .inpaint import LamaInpainter
        from .ocr import build_router
        from .translate import Glossary, build_chain

        glossary = Glossary(config.glossary)
        return Pipeline(
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
        )

    job = MANAGER.submit(paths, body.output_dir, build_pipeline)
    return job.to_dict()


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    from .jobs import MANAGER

    return [j.to_dict() for j in MANAGER.list()]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    from .jobs import MANAGER

    job = MANAGER.get(job_id)
    if not job:
        raise HTTPException(404, f"no job {job_id}")
    return job.to_dict()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    from .jobs import MANAGER

    if not MANAGER.cancel(job_id):
        raise HTTPException(400, "job is not cancellable")
    return {"cancelled": job_id}


@app.get("/api/browse")
def browse(path: str = "") -> dict:
    """List directories and image counts, for the folder picker.

    A browser cannot hand the backend a real filesystem path, and this tool is
    local-only, so the picker is served from the backend instead.
    """
    from .cli import IMAGE_SUFFIXES

    target = Path(path) if path else Path.home()
    if not target.is_dir():
        raise HTTPException(400, f"not a directory: {target}")

    entries = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if child.name.startswith("."):
                continue
            if child.is_dir():
                try:
                    images = sum(
                        1 for f in child.iterdir()
                        if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES
                    )
                except OSError:
                    images = 0
                entries.append({"name": child.name, "path": str(child), "images": images})
    except PermissionError:
        raise HTTPException(403, f"permission denied: {target}")

    own_images = sum(
        1 for f in target.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES
    )
    return {
        "path": str(target),
        "parent": str(target.parent) if target.parent != target else None,
        "images": own_images,
        "entries": entries,
    }
