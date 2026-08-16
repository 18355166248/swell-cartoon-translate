"""Stage 5 -- erasing source text.

The headline performance decision of the pipeline lives here.

Generative inpainting (LaMa) is what comparable tools reach for by default,
and it is the wrong tool for the common case. Text inside a speech balloon
sits on a flat ground; reconstructing that ground is a median-colour fill, not
a learned prior. The fill is roughly two orders of magnitude faster, uses no
VRAM, and is *exactly* correct rather than approximately so.

LaMa stays available for text painted over artwork, where there is real
texture to reconstruct. Even then it runs on a padded crop around each mask
rather than the full page -- a 200x100 region instead of a 720x13859 strip.
Since v1 skips sound effects, the fallback rarely fires at all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import mask as mask_mod
from .types import Block, Box

log = logging.getLogger(__name__)

LAMA_PAD = 32
"""Context in pixels handed to LaMa around a mask's bounding box."""

LAMA_MAX_SIDE = 512
"""Crops are downscaled past this before inference and scaled back after."""


@dataclass
class EraseStats:
    flat_fills: int = 0
    lama_calls: int = 0
    skipped: int = 0
    polygons: list = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"flat fills: {self.flat_fills}, LaMa calls: {self.lama_calls}, "
            f"skipped: {self.skipped}"
        )


class LamaInpainter:
    """ONNX LaMa, loaded lazily.

    Constructing this does not touch the GPU; the session is built on first
    use, so a run that never needs the fallback never pays for it.
    """

    def __init__(self, model_path: str, providers: list[str] | None = None):
        self.model_path = model_path
        self.providers = providers or ["CUDAExecutionProvider", "CPUExecutionProvider"]
        self._session = None

    @property
    def session(self):
        if self._session is None:
            import onnxruntime as ort

            self._session = ort.InferenceSession(self.model_path, providers=self.providers)
            log.info("LaMa session on %s", self._session.get_providers()[0])
        return self._session

    def __call__(self, crop: np.ndarray, crop_mask: np.ndarray) -> np.ndarray:
        """Inpaint a single BGR crop given a 0/255 mask."""
        h, w = crop.shape[:2]
        scale = min(1.0, LAMA_MAX_SIDE / max(h, w))
        if scale < 1.0:
            small = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            small_mask = cv2.resize(crop_mask, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
        else:
            small, small_mask = crop, crop_mask

        # LaMa expects side lengths that are multiples of 8.
        ph, pw = (-small.shape[0]) % 8, (-small.shape[1]) % 8
        if ph or pw:
            small = cv2.copyMakeBorder(small, 0, ph, 0, pw, cv2.BORDER_REFLECT)
            small_mask = cv2.copyMakeBorder(small_mask, 0, ph, 0, pw, cv2.BORDER_CONSTANT, value=0)

        image_in = cv2.cvtColor(small, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        image_in = np.transpose(image_in, (2, 0, 1))[None]
        mask_in = (small_mask > 127).astype(np.float32)[None, None]

        inputs = {i.name: v for i, v in zip(self.session.get_inputs(), (image_in, mask_in))}
        output = self.session.run(None, inputs)[0]

        result = np.transpose(output[0], (1, 2, 0))
        if result.max() <= 1.0 + 1e-6:
            result = result * 255.0
        result = np.clip(result, 0, 255).astype(np.uint8)
        result = cv2.cvtColor(result, cv2.COLOR_RGB2BGR)

        result = result[: small.shape[0] - ph or None, : small.shape[1] - pw or None]
        if scale < 1.0:
            result = cv2.resize(result, (w, h), interpolation=cv2.INTER_LANCZOS4)
        return result


def flat_fill(image: np.ndarray, box: Box, text: np.ndarray, color: np.ndarray) -> None:
    """Paint `color` over the masked pixels of `box`, in place.

    Feathered at the rim so the patch does not leave a hard edge against the
    surrounding ground.
    """
    x1, y1, x2, y2 = box.to_int()
    h, w = image.shape[:2]
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
    region = image[y1:y2, x1:x2]
    if region.size == 0 or text.size == 0:
        return

    alpha = cv2.GaussianBlur(text.astype(np.float32) / 255.0, (5, 5), 0)[..., None]
    patch = np.broadcast_to(color.astype(np.float32), region.shape)
    image[y1:y2, x1:x2] = (region * (1 - alpha) + patch * alpha).astype(np.uint8)


def erase(
    image: np.ndarray,
    blocks: list[Block],
    lama: LamaInpainter | None = None,
    trace_polygons: bool = True,
) -> tuple[np.ndarray, EraseStats]:
    """Remove source lettering for every translatable block.

    Returns a new image plus counters. Blocks are also given a traced
    `polygon` where one could be found, so typesetting can wrap against the
    real balloon shape.
    """
    out = image.copy()
    stats = EraseStats()

    for block in blocks:
        if not block.translatable:
            stats.skipped += 1
            continue

        # Confine erasing to the balloon. Without this a text box whose
        # corners overhang the outline gets its overhang painted with the
        # balloon's fill colour, leaving a white rectangle across the artwork.
        interior = (
            mask_mod.bubble_interior(image, block.bubble_box, block.box)
            if block.bubble_box is not None
            else None
        )

        text, _, _ = mask_mod.text_mask(image, block.box, limit=interior)
        if text.size == 0 or not text.any():
            stats.skipped += 1
            continue

        if mask_mod.looks_flat(image, block.box, text):
            colour = mask_mod.background_color(image, block.box, text)
            flat_fill(out, block.box, text, colour)
            stats.flat_fills += 1
        elif lama is not None:
            _lama_patch(out, block.box, text, lama)
            stats.lama_calls += 1
        else:
            # No fallback configured. A flat fill on textured ground is
            # visible, but leaving source text under the translation is worse.
            colour = mask_mod.background_color(image, block.box, text)
            flat_fill(out, block.box, text, colour)
            stats.flat_fills += 1

        if trace_polygons and block.bubble_box is not None:
            block.polygon = mask_mod.bubble_polygon(image, block.bubble_box, block.box)
            if block.polygon:
                stats.polygons.append(block.id)

    return out, stats


def _lama_patch(image: np.ndarray, box: Box, text: np.ndarray, lama: LamaInpainter) -> None:
    """Run LaMa on a padded crop around `box` and paste the result back."""
    h, w = image.shape[:2]
    padded = box.expanded(LAMA_PAD, bounds=(w, h))
    px1, py1, px2, py2 = padded.to_int()
    bx1, by1, _, _ = box.to_int()

    crop = image[py1:py2, px1:px2]
    if crop.size == 0:
        return

    crop_mask = np.zeros(crop.shape[:2], np.uint8)
    oy, ox = by1 - py1, bx1 - px1
    mh, mw = text.shape[:2]
    oy, ox = max(0, oy), max(0, ox)
    mh, mw = min(mh, crop_mask.shape[0] - oy), min(mw, crop_mask.shape[1] - ox)
    if mh <= 0 or mw <= 0:
        return
    crop_mask[oy : oy + mh, ox : ox + mw] = text[:mh, :mw]

    image[py1:py2, px1:px2] = lama(crop, crop_mask)
