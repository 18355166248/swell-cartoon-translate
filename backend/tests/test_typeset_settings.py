"""`[typeset]` has to actually reach the render.

Every field in that config section used to be dead: the insets were module
constants, `min_size` was a default argument, and blocks were built with a
bare `TextStyle`. The values matched those defaults, so output looked right
and the section silently did nothing. These tests are what stops it from
going inert again -- each one changes a setting and asserts the render moved.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

from ctt.cli import typeset_settings
from ctt.config import TypesetConfig
from ctt.types import Block, BlockKind, Box, TextStyle
from ctt.typeset import Typeset, render_page, settings
from ctt.typeset.preview import render_samples

LONG = "看到母亲和儿子像动物一样交配，真是太性感了……"


def page(text: str = LONG) -> tuple[np.ndarray, list[Block]]:
    image = np.full((300, 400, 3), 255, np.uint8)
    box = Box(x1=40, y1=40, x2=360, y2=260)
    block = Block(
        id="b",
        kind=BlockKind.TEXT_BUBBLE,
        box=box,
        bubble_box=box,
        target_text=text,
        style=TextStyle(),
    )
    return image, [block]


def fitted_size(ts: Typeset, text: str = LONG) -> float:
    image, blocks = page(text)
    with settings.using(ts):
        render_page(image, blocks)
    return blocks[0].style.size


def pixels(ts: Typeset) -> str:
    image, blocks = page()
    with settings.using(ts):
        rendered, _ = render_page(image, blocks)
    return hashlib.md5(rendered.tobytes()).hexdigest()


# --------------------------------------------------------------- 生效验证 ---

def test_bubble_inset_shrinks_the_fitted_size():
    """A bigger margin leaves less room, so the search has to settle lower."""
    assert fitted_size(Typeset(bubble_inset=0.30)) < fitted_size(Typeset(bubble_inset=0.05))


def test_line_spacing_shrinks_the_fitted_size():
    assert fitted_size(Typeset(line_spacing=2.0)) < fitted_size(Typeset(line_spacing=1.0))


def test_min_size_floors_the_search_and_flags_overflow():
    """Below the floor the engine stops shrinking and reports overflow instead
    of quietly clipping the dialogue."""
    image, blocks = page()
    with settings.using(Typeset(min_size=60)):
        _, overflowed = render_page(image, blocks)
    assert blocks[0].style.size == 60
    assert overflowed == blocks


def test_font_reaches_the_block():
    image, blocks = page()
    with settings.using(Typeset(font="SourceHanSerifSC")):
        render_page(image, blocks)
    assert blocks[0].style.font == "SourceHanSerifSC"


def test_align_changes_the_pixels():
    """Alignment cannot move the fitted size, only where the glyphs land, so
    the pixels are the only evidence available."""
    assert pixels(Typeset(align="left")) != pixels(Typeset(align="center"))


def test_config_section_maps_onto_the_settings():
    """The converter is the seam where a renamed config field would go
    unnoticed."""
    ts = typeset_settings(TypesetConfig(
        font="SourceHanSerifSC",
        line_spacing=1.4,
        align="left",
        min_size=12,
        bubble_inset=0.2,
        free_text_inset=0.03,
    ))
    assert ts == Typeset(
        font="SourceHanSerifSC",
        line_spacing=1.4,
        align="left",
        min_size=12,
        bubble_inset=0.2,
        free_text_inset=0.03,
    )


# -------------------------------------------------------------- 边界与隔离 ---

def test_hand_edited_blocks_keep_their_style():
    """`edited` already means "a human touched this" -- it is what stops a
    re-run from overwriting a corrected translation. A hand-picked font has to
    survive for the same reason."""
    image, blocks = page()
    blocks[0].style.font = "Heiti"
    blocks[0].edited = True
    with settings.using(Typeset(font="SourceHanSerifSC")):
        render_page(image, blocks)
    assert blocks[0].style.font == "Heiti"


def test_settings_are_restored_afterwards():
    """A preview renders with values the user has not saved. They must not
    survive into the next real page."""
    before = settings.active()
    with settings.using(Typeset(font="SourceHanSerifSC", min_size=40)):
        assert settings.active().min_size == 40
    assert settings.active() is before


def test_settings_are_restored_after_a_failure():
    before = settings.active()
    with pytest.raises(RuntimeError):
        with settings.using(Typeset(min_size=40)):
            raise RuntimeError("boom")
    assert settings.active() is before


# --------------------------------------------------------------- 预览渲染 ---

def test_preview_uses_the_real_engine():
    """Facts, not just a picture: the fitted size and the overflow flag are
    what these settings control."""
    strip, facts = render_samples(Typeset(), width=200, height=160)
    assert strip.shape == (160, 200 * 5, 3)
    assert len(facts) == 5
    assert all(f["size"] > 0 for f in facts)
    # Sample texts are ordered short to long, so the fitted sizes must fall.
    assert facts[0]["size"] > facts[-2]["size"]


def test_preview_reflects_a_changed_inset():
    _, wide = render_samples(Typeset(bubble_inset=0.05), width=200, height=160)
    _, tight = render_samples(Typeset(bubble_inset=0.30), width=200, height=160)
    assert [f["size"] for f in tight] < [f["size"] for f in wide]


def test_preview_accepts_custom_text():
    _, facts = render_samples(Typeset(), texts=[("1", "你好世界")])
    assert len(facts) == 1
    assert facts[0]["text"] == "你好世界"


# --------------------------------------------------------------- 保存回环 ---

def test_saving_from_the_typeset_tab_round_trips(tmp_path):
    """The tab writes the same five paths the preview reads. If a name drifts
    the save silently writes a dead key, which is exactly what the config
    endpoint's unknown-field check exists to catch."""
    from fastapi.testclient import TestClient

    from ctt.config import load
    from ctt.server import app

    path = tmp_path / "ctt.toml"
    path.write_text(
        "# 排版\n[typeset]\nfont = \"SourceHanSansSC\"  # 行内注释\nmin_size = 9\n",
        encoding="utf-8",
    )

    response = TestClient(app).put("/api/config", json={
        "fields": {
            "typeset.font": "SourceHanSerifSC",
            "typeset.line_spacing": 1.35,
            "typeset.align": "left",
            "typeset.min_size": 14,
            "typeset.bubble_inset": 0.18,
        },
        "path": str(path),
    })
    assert response.status_code == 200, response.text

    saved = typeset_settings(load(path)[0].typeset)
    assert saved == Typeset(
        font="SourceHanSerifSC",
        line_spacing=1.35,
        align="left",
        min_size=14,
        bubble_inset=0.18,
    )

    # Comments must survive: ctt.toml is committed and carries the measured
    # numbers that explain why each value is what it is.
    text = path.read_text(encoding="utf-8")
    assert "# 排版" in text
    assert "# 行内注释" in text


# ----------------------------------------------------------------- 并发隔离 ---

def test_settings_do_not_leak_across_threads():
    """The obvious reasoning -- one job at a time, so nothing to race with --
    is wrong. The preview endpoint is a second render, and FastAPI runs sync
    endpoints in a threadpool while the job thread is mid-typeset. With a plain
    module global, dragging a slider would retypeset a real page with values
    the user never saved."""
    import threading
    import time

    seen: list[str] = []
    started = threading.Event()
    release = threading.Event()

    def job_thread():
        with settings.using(Typeset(font="JobFont")):
            started.set()
            release.wait(2)
            seen.append(settings.active().font)

    worker = threading.Thread(target=job_thread)
    worker.start()
    started.wait(2)
    with settings.using(Typeset(font="PreviewFont")):
        assert settings.active().font == "PreviewFont"
        release.set()
    worker.join(2)

    assert seen == ["JobFont"]


def test_a_preview_request_cannot_disturb_a_render_in_flight():
    """The same thing through the endpoint, which is how it would actually
    happen."""
    import threading

    from fastapi.testclient import TestClient

    from ctt.server import app

    client = TestClient(app)
    observed: list[float] = []
    started = threading.Event()
    done = threading.Event()

    def rendering_thread():
        with settings.using(Typeset(min_size=9)):
            started.set()
            done.wait(5)
            observed.append(settings.active().min_size)

    worker = threading.Thread(target=rendering_thread)
    worker.start()
    started.wait(2)
    assert client.post("/api/typeset/preview", json={"min_size": 55}).status_code == 200
    done.set()
    worker.join(5)

    assert observed == [9]


# ------------------------------------------------------------- 编辑器一致性 ---

def test_editor_render_matches_the_batch_render(tmp_path, monkeypatch):
    """The results tab re-runs the same engine. If it does not activate the
    config, a non-default ctt.toml makes the page you re-render -- or export --
    typeset differently from the one the run produced, and silently so."""
    import cv2
    from fastapi.testclient import TestClient

    from ctt import server as server_mod
    from ctt.config import load
    from ctt.types import Page as PageModel
    from ctt.types import Project

    config = tmp_path / "ctt.toml"
    config.write_text(
        "\n".join([
            "[typeset]",
            'font = "SourceHanSerifSC"',
            "bubble_inset = 0.28",
            "min_size = 20",
        ]),
        encoding="utf-8",
    )
    # `saved_typeset()` finds ctt.toml by walking up from the cwd.
    monkeypatch.chdir(tmp_path)

    source = tmp_path / "page.png"
    image, blocks = page()
    cv2.imwrite(str(source), image)

    monkeypatch.setitem(server_mod.STATE, "project", Project(
        name="t",
        pages=[PageModel(image_path=str(source), width=400, height=300, blocks=blocks)],
    ))

    response = TestClient(server_mod.app).get("/api/pages/0/blocks/b/fit")
    assert response.status_code == 200, response.text

    # The size the batch pipeline would settle on with the same ctt.toml.
    expected = fitted_size(typeset_settings(load(config)[0].typeset))
    assert response.json()["size"] == expected
    # And distinguishable from the default, or the test proves nothing.
    assert expected != fitted_size(Typeset())
