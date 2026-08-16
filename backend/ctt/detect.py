"""Stage 1 -- text and bubble detection.

Wraps `ogkalu/comic-text-and-bubble-detector`, an RT-DETR-v2 r50vd fine-tuned
on ~11k manga / webtoon / manhua / western comic pages (Apache-2.0). It emits
three classes -- bubble, text_bubble, text_free -- which is exactly the split
the pipeline needs: the balloon bounds layout, the text inside it is what gets
translated, and free-floating text is the v2 sound-effects pass.

Detection always runs per *slice* (see `ctt.slicing`). Boxes are mapped back
to original-image coordinates here, so nothing downstream is aware slicing
happened.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

from . import geometry, slicing
from .types import Block, BlockKind, Box

log = logging.getLogger(__name__)

REPO_ID = "ogkalu/comic-text-and-bubble-detector"

MODEL_FILES = {
    "int8": "detector_int8.onnx",   # 44MB -- the default; ample for 4GB cards
    "fp32": "detector.onnx",        # 168MB
    "small": "detector-v4-s_int8.onnx",  # 11MB
}

INPUT_SIZE = 640
"""Training resolution from preprocessor_config.json."""

# id2label from the model's config.json.
LABELS = {0: "bubble", 1: "text_bubble", 2: "text_free"}


@dataclass
class Detection:
    box: Box
    label: str
    score: float


class ComicDetector:
    def __init__(
        self,
        variant: str = "int8",
        model_path: str | None = None,
        providers: list[str] | None = None,
        cache_dir: str | None = None,
    ):
        self.model_path = model_path or self._download(variant, cache_dir)
        self.providers = providers or self._default_providers()
        self._session = None

    @staticmethod
    def _default_providers() -> list[str]:
        """Prefer whatever accelerator is actually installed.

        DirectML is listed ahead of CUDA on purpose for Windows machines: it
        needs no CUDA toolkit install and works on any DX12 GPU, which is a
        far smaller setup burden for the same speedup on this workload.
        """
        import onnxruntime as ort

        available = set(ort.get_available_providers())
        preferred = ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"]
        chosen = [p for p in preferred if p in available]
        return chosen or ["CPUExecutionProvider"]

    @staticmethod
    def _download(variant: str, cache_dir: str | None) -> str:
        from huggingface_hub import hf_hub_download

        filename = MODEL_FILES.get(variant)
        if filename is None:
            raise ValueError(f"unknown variant {variant!r}; pick from {sorted(MODEL_FILES)}")
        return hf_hub_download(REPO_ID, filename, cache_dir=cache_dir)

    @property
    def session(self):
        if self._session is None:
            import onnxruntime as ort

            self._session = ort.InferenceSession(self.model_path, providers=self.providers)
            log.info("detector on %s", self._session.get_providers()[0])
        return self._session

    def _preprocess(self, image: np.ndarray) -> np.ndarray:
        """Resize to 640x640 and rescale to [0, 1].

        No mean/std normalisation: the checkpoint's preprocessor_config sets
        `do_normalize: false`, and applying ImageNet statistics anyway shifts
        the input distribution enough to cost real recall.
        """
        resized = cv2.resize(image, (INPUT_SIZE, INPUT_SIZE), interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        return np.transpose(rgb, (2, 0, 1))[None]

    def detect_image(self, image: np.ndarray, threshold: float = 0.35) -> list[Detection]:
        """Run the detector on one image, no slicing."""
        height, width = image.shape[:2]
        inputs = {
            "images": self._preprocess(image),
            # (width, height) -- NOT (height, width). RT-DETR's exported
            # postprocessor multiplies its normalised cxcywh boxes by this to
            # get pixels, and the two orderings both produce plausible-looking
            # boxes on a non-square page: they land inside the image at
            # sensible sizes, just in the wrong places, with confidences as
            # high as 0.98. Measured on assets/english.jpg, (w, h) recovers
            # both speech balloons and (h, w) recovers neither.
            "orig_target_sizes": np.array([[width, height]], dtype=np.int64),
        }
        labels, boxes, scores = self.session.run(None, inputs)

        results: list[Detection] = []
        for label, box, score in zip(labels[0], boxes[0], scores[0]):
            if score < threshold:
                continue
            x1, y1, x2, y2 = (float(v) for v in box)
            results.append(
                Detection(
                    box=Box(
                        x1=max(0.0, x1), y1=max(0.0, y1),
                        x2=min(float(width), x2), y2=min(float(height), y2),
                    ),
                    label=LABELS.get(int(label), str(label)),
                    score=float(score),
                )
            )
        return results

    def detect(self, image: np.ndarray, threshold: float = 0.35, **slice_kwargs) -> list[Block]:
        """Detect across a full page, slicing long strips first.

        Returns text blocks with their enclosing bubble attached, in reading
        order.
        """
        blocks: list[Block] = []
        bubbles: list[Box] = []

        for index, (piece, crop) in enumerate(slicing.iter_slices(image, **slice_kwargs)):
            for detection in self.detect_image(crop, threshold):
                box = detection.box.translated(dy=piece.y0)
                if detection.label == "bubble":
                    bubbles.append(box)
                    continue
                blocks.append(
                    Block(
                        id=f"{index}-{len(blocks)}",
                        kind=BlockKind.TEXT_BUBBLE
                        if detection.label == "text_bubble"
                        else BlockKind.TEXT_FREE,
                        box=box,
                        source_conf=detection.score,
                    )
                )

        blocks = geometry.dedup_blocks(blocks)
        bubbles = _dedup_boxes(bubbles)
        geometry.assign_bubbles(blocks, bubbles)

        page_is_strip = image.shape[0] > image.shape[1] * 3
        ordered = geometry.reading_order(blocks, long_strip=page_is_strip)
        for position, block in enumerate(ordered):
            block.id = f"b{position:03d}"
        return ordered


def _dedup_boxes(boxes: list[Box], iou_threshold: float = 0.5) -> list[Box]:
    """Merge bubble boxes duplicated across overlapping slices."""
    kept: list[Box] = []
    for box in sorted(boxes, key=lambda b: b.area, reverse=True):
        for i, existing in enumerate(kept):
            if geometry.iou(existing, box) >= iou_threshold:
                kept[i] = geometry.merge(existing, box)
                break
        else:
            kept.append(box)
    return kept
