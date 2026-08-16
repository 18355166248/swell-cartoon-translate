"""Box arithmetic: IoU, cross-slice dedup, reading order."""

from __future__ import annotations

from .types import Block, Box


def iou(a: Box, b: Box) -> float:
    ix1, iy1 = max(a.x1, b.x1), max(a.y1, b.y1)
    ix2, iy2 = min(a.x2, b.x2), min(a.y2, b.y2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0.0:
        return 0.0
    return inter / (a.area + b.area - inter)


def containment(inner: Box, outer: Box) -> float:
    """Fraction of `inner` that lies inside `outer`.

    Preferred over IoU when testing "is this text inside that bubble", since
    a small text box inside a large bubble has low IoU but containment ~1.
    """
    ix1, iy1 = max(inner.x1, outer.x1), max(inner.y1, outer.y1)
    ix2, iy2 = min(inner.x2, outer.x2), min(inner.y2, outer.y2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    return inter / inner.area if inner.area > 0 else 0.0


def merge(a: Box, b: Box) -> Box:
    return Box(x1=min(a.x1, b.x1), y1=min(a.y1, b.y1), x2=max(a.x2, b.x2), y2=max(a.y2, b.y2))


def dedup_blocks(blocks: list[Block], iou_threshold: float = 0.4) -> list[Block]:
    """Collapse duplicate detections produced by overlapping slices.

    Slices overlap by design (see `slicing`), so a bubble straddling a cut is
    detected twice with slightly different boxes. We merge rather than pick a
    winner: a bubble clipped by the slice edge yields a *truncated* box on one
    side, and taking the union recovers the full extent.
    """
    kept: list[Block] = []
    for block in sorted(blocks, key=lambda b: b.box.area, reverse=True):
        for i, existing in enumerate(kept):
            if existing.kind is not block.kind:
                continue
            if iou(existing.box, block.box) < iou_threshold:
                continue
            merged = existing.model_copy(deep=True)
            merged.box = merge(existing.box, block.box)
            if existing.bubble_box and block.bubble_box:
                merged.bubble_box = merge(existing.bubble_box, block.bubble_box)
            else:
                merged.bubble_box = existing.bubble_box or block.bubble_box
            merged.source_conf = max(existing.source_conf, block.source_conf)
            kept[i] = merged
            break
        else:
            kept.append(block)
    return kept


def assign_bubbles(texts: list[Block], bubbles: list[Box], min_containment: float = 0.6) -> None:
    """Attach each text block to the smallest bubble that contains it.

    Smallest-wins matters for nested bubbles (a thought bubble drawn inside a
    panel-wide bubble); the tighter one is the real layout constraint.

    A bubble is then claimed by at most **one** text block. Layout sizes each
    block to fill its whole container, so two blocks sharing a bubble both get
    laid out across the same area and render on top of each other. This is not
    hypothetical: character-name captions ("LIAM", "EMMA") sit right beside the
    balloon they label, and a caption pulled into its neighbour's bubble comes
    out at balloon-filling size straight across the dialogue.

    The block with the highest containment wins the bubble; the losers fall
    back to their own tight box, which is where a caption belongs anyway.
    """
    claims: dict[int, tuple[Block, float]] = {}

    for text in texts:
        best: Box | None = None
        best_index = -1
        for index, bubble in enumerate(bubbles):
            if containment(text.box, bubble) < min_containment:
                continue
            if best is None or bubble.area < best.area:
                best, best_index = bubble, index

        text.bubble_box = best
        if best is None:
            continue

        score = containment(text.box, best)
        holder = claims.get(best_index)
        if holder is None:
            claims[best_index] = (text, score)
        elif score > holder[1]:
            holder[0].bubble_box = None
            claims[best_index] = (text, score)
        else:
            text.bubble_box = None


def reading_order(blocks: list[Block], long_strip: bool, rtl: bool = False) -> list[Block]:
    """Sort blocks into the order a human reads them.

    Long strips (webtoons) are a pure top-to-bottom scroll. Paginated comics
    need Z-order: cluster into rows first, then order within each row.
    `rtl=True` handles Japanese right-to-left pages.
    """
    if not blocks:
        return []

    if long_strip:
        return sorted(blocks, key=lambda b: (b.box.y1, b.box.x1))

    # Cluster into rows. A new row starts when a block's top edge drops below
    # the bottom of the current row's tallest member -- i.e. no vertical
    # overlap, so it cannot be read side-by-side with it.
    ordered = sorted(blocks, key=lambda b: b.box.y1)
    rows: list[list[Block]] = []
    row: list[Block] = [ordered[0]]
    row_bottom = ordered[0].box.y2

    for block in ordered[1:]:
        # Tolerate a little overlap: text baselines rarely align perfectly.
        if block.box.y1 < row_bottom - 0.25 * block.box.height:
            row.append(block)
            row_bottom = max(row_bottom, block.box.y2)
        else:
            rows.append(row)
            row = [block]
            row_bottom = block.box.y2
    rows.append(row)

    result: list[Block] = []
    for r in rows:
        result.extend(sorted(r, key=lambda b: -b.box.x1 if rtl else b.box.x1))
    return result
