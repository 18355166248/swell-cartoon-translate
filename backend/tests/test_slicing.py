import numpy as np
import pytest

from ctt.slicing import find_gutters, plan_slices, row_ink_fraction


def strip(height: int, width: int = 720) -> np.ndarray:
    """Blank white strip."""
    return np.full((height, width, 3), 255, dtype=np.uint8)


def paint_art(img: np.ndarray, y0: int, y1: int, seed: int = 0) -> None:
    """Fill a band with texture, imitating drawn artwork.

    Artwork must be *textured*, not flat-filled. `row_ink_fraction` measures
    deviation from each row's own median, so a flat-filled row reads as
    ink-free no matter its colour -- which is correct (a cut through a solid
    region carries no text either) but makes flat fills useless as a stand-in
    for panels in these tests.
    """
    rng = np.random.default_rng(seed)
    img[y0:y1] = rng.integers(0, 255, (y1 - y0, img.shape[1], 3), dtype=np.uint8)


def paint_text(img: np.ndarray, y0: int, y1: int) -> None:
    """Thin vertical strokes across a band, imitating a line of text.

    Kept sparse on purpose: real text inks a few percent of a row. The metric
    is symmetric about the median and so saturates at 0.5 -- a band that is
    *mostly* black would report the white pixels as the deviation instead.
    """
    img[y0:y1, 100:600:24] = 0


class TestRowInkFraction:
    def test_blank_rows_read_as_zero_ink(self):
        assert row_ink_fraction(strip(50)).max() == 0.0

    def test_text_rows_read_as_ink(self):
        img = strip(50)
        paint_text(img, 20, 30)
        ink = row_ink_fraction(img)
        assert ink[25] > 0.002, "a line of text must exceed the gutter threshold"
        assert ink[5] == 0.0

    def test_vertical_gradient_is_not_mistaken_for_ink(self):
        # Each row is a flat tone; only the tone changes down the strip.
        # A plain std test also passes here, but this pins the intent.
        img = np.zeros((256, 720, 3), dtype=np.uint8)
        for y in range(256):
            img[y, :, :] = y
        assert row_ink_fraction(img).max() == 0.0

    def test_jpeg_noise_stays_under_threshold(self):
        rng = np.random.default_rng(0)
        img = strip(50).astype(np.int16)
        img += rng.integers(-8, 9, img.shape)
        img = np.clip(img, 0, 255).astype(np.uint8)
        assert row_ink_fraction(img).max() <= 0.002

    def test_edge_watermark_does_not_disqualify_a_gutter(self):
        img = strip(50)
        img[:, :6] = 0  # border stripe inside the 2% trim
        assert row_ink_fraction(img).max() == 0.0


class TestFindGutters:
    def test_finds_blank_band_between_panels(self):
        img = strip(300)
        paint_art(img, 0, 100, seed=1)
        paint_art(img, 140, 300, seed=2)
        assert find_gutters(img) == [(100, 140)]

    def test_rejects_runs_shorter_than_min_run(self):
        img = strip(300)
        paint_art(img, 0, 100, seed=1)
        paint_art(img, 103, 300, seed=2)  # only a 3px gap
        assert find_gutters(img, min_run=8) == []

    def test_no_gutters_in_full_bleed_art(self):
        rng = np.random.default_rng(1)
        img = rng.integers(0, 255, (300, 720, 3), dtype=np.uint8)
        assert find_gutters(img) == []


class TestPlanSlices:
    def test_short_image_is_one_slice(self):
        slices = plan_slices(strip(2000))
        assert len(slices) == 1
        assert (slices[0].y0, slices[0].y1, slices[0].forced) == (0, 2000, False)

    @pytest.mark.parametrize("height", [2501, 5000, 10000, 13859, 30000])
    def test_no_slice_ever_exceeds_max_height(self, height):
        # The invariant the whole module exists to guarantee.
        slices = plan_slices(strip(height))
        assert slices, "must always produce at least one slice"
        assert max(s.height for s in slices) <= 2500

    @pytest.mark.parametrize("height", [2501, 5000, 13859])
    def test_slices_cover_every_row(self, height):
        covered = np.zeros(height, dtype=bool)
        for s in plan_slices(strip(height)):
            covered[s.y0 : s.y1] = True
        assert covered.all()

    def test_cuts_land_on_gutters_when_available(self):
        img = strip(6000)
        paint_art(img, 0, 6000, seed=3)
        for y in (1900, 3900):
            img[y : y + 40] = 255  # gutters near the 2000px target
        slices = plan_slices(img)
        assert [s.y1 for s in slices[:2]] == [1920, 3920]
        assert not any(s.forced for s in slices)

    def test_forced_cuts_overlap_so_dedup_can_recover(self):
        rng = np.random.default_rng(2)
        img = rng.integers(0, 255, (6000, 720, 3), dtype=np.uint8)
        slices = plan_slices(img)
        assert all(s.forced for s in slices[:-1]), "no gutters -> all blind cuts"
        for prev, nxt in zip(slices, slices[1:]):
            assert nxt.y0 < prev.y1, "blind cuts must overlap"

    def test_prefers_the_gutter_nearest_the_target_height(self):
        img = strip(9000)
        paint_art(img, 0, 9000, seed=4)
        for y in (1000, 2000, 2400):
            img[y : y + 20] = 255
        # The 800..2500 window holds all three; 2000 is the target.
        assert plan_slices(img)[0].y1 == 2010
