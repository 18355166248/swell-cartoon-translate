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
    # Pass the current text so tomlkit patches values in place. Without it the
    # document is regenerated and every comment in the file is lost.
    existing = target.read_text(encoding="utf-8") if target.exists() else None
    target.write_text(to_toml(config, existing), encoding="utf-8")
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
    recursive: bool | None = None


def _resolve_inputs(body: JobRequest):
    """Shared by the preview and the real run so they cannot disagree.

    A preview that selects a different set from the run it previews is worse
    than no preview at all.
    """
    from .config import _assign, load
    from .discover import Filters, discover

    config, _ = load()
    for key, value in body.overrides.items():
        _assign(config, key, value)

    recursive = body.recursive if body.recursive is not None else config.input.recursive
    filters = Filters(
        min_width=config.input.min_width,
        min_bytes=config.input.min_bytes,
        min_side=config.input.min_side,
        max_aspect=config.input.max_aspect,
        skip_output_dirs=config.input.skip_output_dirs,
    )

    if body.input_paths:
        from .discover import Candidate

        candidates = [Candidate(path=Path(p), size=Path(p).stat().st_size)
                      for p in body.input_paths]
    elif body.input_dir:
        directory = Path(body.input_dir)
        if not directory.is_dir():
            raise HTTPException(400, f"not a directory: {directory}")
        # The output folder is frequently nested inside the input tree, so a
        # recursive run would otherwise re-translate its own previous results.
        candidates = discover(
            directory,
            recursive=recursive,
            filters=filters,
            exclude_dirs=[body.output_dir],
        )
    else:
        raise HTTPException(400, "give input_dir or input_paths")

    return config, candidates


@app.post("/api/jobs/preview")
def preview_job(body: JobRequest) -> dict:
    """What a run would pick up, and why anything was skipped.

    Worth its own endpoint: with recursion enabled the selection is not
    predictable by eye, and a wrong guess costs hours.
    """
    from .discover import summarise

    _, candidates = _resolve_inputs(body)
    included = [c for c in candidates if c.included]
    if body.limit:
        included = included[: body.limit]

    summary = summarise(candidates)
    if body.limit:
        summary["included"] = len(included)
        summary["estimated_seconds"] = len(included) * 36
    return {
        "summary": summary,
        "included": [c.to_dict() for c in included[:200]],
        "skipped": [c.to_dict() for c in candidates if not c.included][:200],
    }


@app.post("/api/jobs")
def create_job(body: JobRequest) -> dict:
    from .jobs import MANAGER, SkippedFile

    if MANAGER.busy:
        raise HTTPException(409, "a job is already running")

    config, candidates = _resolve_inputs(body)
    paths = [c.path for c in candidates if c.included]
    if body.limit:
        paths = paths[: body.limit]

    # Copy filtered-out pages through so the output chapter has no holes.
    # Our own previous output is never copied -- see Candidate.copyable.
    skipped = (
        [
            SkippedFile(path=str(c.path), reason=c.reason)
            for c in candidates
            if not c.included and c.copyable
        ]
        if config.output.copy_skipped
        else []
    )

    if not paths and not skipped:
        raise HTTPException(400, "no images matched")

    def build_pipeline():
        from .cli import dataclass_kwargs
        from .detect import ComicDetector
        from .inpaint import LamaInpainter
        from .ocr import build_router
        from .translate import Glossary, build_chain

        from .runtime import gpu_layers_for, threads_for

        glossary = Glossary(config.glossary)
        # The profile decides both the core count and whether the GPU is used.
        # Set here rather than in ctt.toml so switching profile takes effect
        # without anyone having to know what n_threads or n_gpu_layers should be.
        llamacpp_kwargs = dataclass_kwargs(config.translate.llamacpp)
        llamacpp_kwargs["n_threads"] = threads_for(config.runtime.profile)
        llamacpp_kwargs["n_gpu_layers"] = gpu_layers_for(
            config.runtime.profile, config.runtime.gpu_layers
        )

        return Pipeline(
            detector=ComicDetector(variant=config.detect.model,
                                   cache_dir=config.models_dir or None),
            ocr=build_router(config.ocr.languages),
            translator=build_chain(
                config.translate.backends,
                glossary=glossary,
                llamacpp=llamacpp_kwargs,
                llm=dataclass_kwargs(config.translate.llm),
                nllb=dataclass_kwargs(config.translate.nllb),
            ),
            target_lang=config.target_lang,
            source_lang=config.source_lang,
            glossary=glossary,
            lama=LamaInpainter(config.erase.lama_path) if config.erase.lama_path else None,
            detect_threshold=config.detect.threshold,
        )

    job = MANAGER.submit(
        paths,
        body.output_dir,
        build_pipeline,
        # The selected folder, not the output: relative paths are computed
        # against it so a recursive run keeps its chapter structure.
        input_root=body.input_dir or body.output_dir,
        layout=config.output.layout,
        overwrite=config.output.overwrite,
        skipped=skipped,
        profile=config.runtime.profile,
    )
    return job.to_dict()


@lru_cache(maxsize=512)
def _thumbnail_bytes(path: str, size: int, mtime: float) -> bytes:
    """Downscaled JPEG of an output page.

    `mtime` is part of the key rather than the body: it makes the cache
    self-invalidating when a page is re-rendered, without anyone having to
    remember to clear it.
    """
    image = cv2.imread(path)
    if image is None:
        raise HTTPException(404, f"cannot read {path}")
    height, width = image.shape[:2]
    scale = size / max(height, width)
    if scale < 1:
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 78])[1].tobytes()


@app.get("/api/thumbnail")
def thumbnail(path: str, size: int = 240) -> Response:
    """Thumbnail for a finished page.

    Serves by absolute path because the grid has to show pages as a running
    job produces them, before any project file exists. Reads are confined to
    directories this backend is actually working in -- a local tool still
    should not be a general file-read endpoint for anything that can reach it.
    """
    from .jobs import MANAGER

    target = Path(path).resolve()

    allowed: list[Path] = []
    for job in MANAGER.list():
        allowed.append(Path(job.output_dir).resolve())
        allowed.append(Path(job.input_root or job.output_dir).resolve())
    project_path = STATE.get("project_path")
    if project_path:
        allowed.append(Path(str(project_path)).resolve().parent)
    for page in _project().pages:
        allowed.append(Path(page.image_path).resolve().parent)

    if not any(root == target or root in target.parents for root in allowed):
        raise HTTPException(403, "path is outside the directories in use")
    if not target.is_file():
        raise HTTPException(404, f"no such file: {target}")

    body = _thumbnail_bytes(str(target), max(64, min(size, 1024)), target.stat().st_mtime)
    return Response(body, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/api/runtime/profiles")
def runtime_profiles() -> dict:
    """Available CPU profiles, with the thread count each would use."""
    from .runtime import describe, physical_cores

    return {"cores": physical_cores(), "profiles": describe()}


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


class PickRequest(BaseModel):
    initial: str = ""
    title: str = "选择漫画目录"


@app.post("/api/pick-folder")
def pick_folder_endpoint(body: PickRequest) -> dict:
    """Open the OS folder chooser on the machine running the backend.

    Legitimate here only because this is a local-first tool: the backend and
    the browser are the same machine, so "the server's desktop" is the user's
    desktop. The in-page browser stays as the fallback for when no dialog can
    be shown (headless, remote, or a locked-down environment).
    """
    from .picker import pick_folder

    chosen = pick_folder(title=body.title, initial=body.initial)
    if chosen is None:
        raise HTTPException(
            501, "本机无法弹出文件夹对话框，请用页面内的目录浏览器"
        )
    return {"path": chosen, "cancelled": chosen == ""}


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
                    # Recursive counts too, so a series folder does not look
                    # empty just because its pages live one level down.
                    nested = sum(
                        1 for f in child.rglob("*")
                        if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES
                    )
                except OSError:
                    images = nested = 0
                entries.append({
                    "name": child.name,
                    "path": str(child),
                    "images": images,
                    "nested_images": nested,
                })
    except PermissionError:
        raise HTTPException(403, f"permission denied: {target}")

    own_images = sum(
        1 for f in target.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES
    )
    nested_total = sum(
        1 for f in target.rglob("*")
        if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES
    )
    return {
        "path": str(target),
        "parent": str(target.parent) if target.parent != target else None,
        "images": own_images,
        "nested_images": nested_total,
        "entries": entries,
    }
