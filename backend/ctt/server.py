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

from .translate import Glossary
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
