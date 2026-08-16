import pytest
from PIL import Image

from ctt.discover import Filters, discover, summarise


def make_image(path, width=1200, height=1600, kb=80):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (width, height), (128, 90, 60)).save(path, quality=95)
    # Pad to a plausible file size so byte filters behave realistically.
    if path.stat().st_size < kb * 1024:
        with open(path, "ab") as f:
            f.write(b"\x00" * (kb * 1024 - path.stat().st_size))
    return path


@pytest.fixture
def tree(tmp_path):
    make_image(tmp_path / "series" / "ch1" / "page-1.jpg")
    make_image(tmp_path / "series" / "ch1" / "page-2.jpg")
    make_image(tmp_path / "series" / "ch2" / "page-1.jpg")
    return tmp_path / "series"


class TestRecursion:
    def test_non_recursive_sees_only_the_top_level(self, tree):
        assert discover(tree, recursive=False) == []

    def test_recursive_finds_every_chapter(self, tree):
        found = [c for c in discover(tree, recursive=True) if c.included]
        assert len(found) == 3

    def test_pages_are_ordered_naturally(self, tmp_path):
        for n in (1, 2, 10, 11):
            make_image(tmp_path / f"page-{n}.jpg")
        names = [c.path.name for c in discover(tmp_path) if c.included]
        assert names == ["page-1.jpg", "page-2.jpg", "page-10.jpg", "page-11.jpg"]

    def test_non_images_are_ignored_entirely(self, tmp_path):
        make_image(tmp_path / "page-1.jpg")
        (tmp_path / "notes.txt").write_text("hi")
        (tmp_path / "project.cttproj").write_text("{}")
        # Not merely excluded -- they should not appear as candidates at all.
        assert [c.path.name for c in discover(tmp_path)] == ["page-1.jpg"]

    def test_a_series_folder_looks_empty_without_recursion(self, tree):
        # The report that prompted this feature: pointing at the series folder
        # found nothing, because every page lives one level down in a chapter.
        assert discover(tree, recursive=False) == []
        assert len([c for c in discover(tree, recursive=True) if c.included]) == 3

    def test_chapters_stay_grouped_in_order(self, tmp_path):
        # Reading order has to survive recursion: ch2's pages must not
        # interleave with ch10's just because the page numbers collide.
        for chapter in ("ch2", "ch10"):
            for n in (1, 2):
                make_image(tmp_path / chapter / f"page-{n}.jpg")
        order = [
            str(c.path.relative_to(tmp_path)).replace("\\", "/")
            for c in discover(tmp_path, recursive=True)
            if c.included
        ]
        assert [p.split("/")[0] for p in order] == ["ch2", "ch2", "ch10", "ch10"]


class TestOutputGuard:
    def test_named_output_directory_is_skipped(self, tree):
        make_image(tree / "ch1" / "_zh" / "page-1_zh.jpg")
        results = discover(tree, recursive=True)
        skipped = [c for c in results if not c.included]
        assert any("_zh" in c.path.parts for c in skipped)
        assert all("_zh" not in c.path.parts for c in results if c.included)

    @pytest.mark.parametrize("name", ["_zh", "out", "output", "translated", "汉化"])
    def test_common_output_names(self, tree, name):
        make_image(tree / "ch1" / name / "done.jpg")
        included = [c for c in discover(tree, recursive=True) if c.included]
        assert all(name not in c.path.parts for c in included)

    def test_explicit_exclude_dir_wins_regardless_of_name(self, tree):
        target = tree / "ch1" / "renders"
        make_image(target / "page-1.jpg")
        included = [
            c for c in discover(tree, recursive=True, exclude_dirs=[target]) if c.included
        ]
        assert all("renders" not in c.path.parts for c in included)

    def test_without_the_guard_output_is_re_translated(self, tree):
        # The failure this guard exists to prevent: translating a translation.
        make_image(tree / "ch1" / "_zh" / "page-1_zh.jpg")
        included = [
            c for c in discover(
                tree, recursive=True, filters=Filters(skip_output_dirs=False)
            ) if c.included
        ]
        assert any("_zh" in c.path.parts for c in included)

    def test_output_inside_the_input_tree_is_excluded_by_path(self, tree):
        # Measured on the real library: a recursive pass over the series
        # folder saw 579 images, 289 of which were the previous run's output
        # sitting in a nested _zh folder.
        destination = tree / "ch1" / "_zh"
        for n in (1, 2):
            make_image(destination / f"page-{n}_zh.jpg")
        results = discover(tree, recursive=True, exclude_dirs=[destination])
        assert len([c for c in results if c.included]) == 3
        assert len([c for c in results if not c.included]) == 2


class TestFilters:
    def test_small_files_are_skipped(self, tmp_path):
        make_image(tmp_path / "banner.jpg", 1728, 512, kb=20)
        make_image(tmp_path / "page.jpg", kb=200)
        included = [c.path.name for c in discover(tmp_path) if c.included]
        assert included == ["page.jpg"]

    def test_thumbnails_are_skipped_by_dimension(self, tmp_path):
        make_image(tmp_path / "thumb.jpg", 200, 200, kb=200)
        make_image(tmp_path / "page.jpg", kb=200)
        included = [c.path.name for c in discover(tmp_path) if c.included]
        assert included == ["page.jpg"]

    def test_wide_banners_are_skipped_by_aspect(self, tmp_path):
        make_image(tmp_path / "banner.jpg", 4000, 700, kb=200)
        included = [c.path.name for c in discover(tmp_path) if c.included]
        assert included == []

    def test_tall_webtoon_strips_are_kept(self, tmp_path):
        # The mirror case, and the reason max_aspect is one-sided: a vertical
        # strip is extremely tall and must never be mistaken for a banner.
        make_image(tmp_path / "strip.jpg", 720, 13859, kb=500)
        included = [c.path.name for c in discover(tmp_path) if c.included]
        assert included == ["strip.jpg"]

    def test_unreadable_files_are_reported_not_raised(self, tmp_path):
        broken = tmp_path / "broken.jpg"
        broken.write_bytes(b"\x00" * 80_000)
        results = discover(tmp_path)
        assert len(results) == 1
        assert "无法读取" in results[0].reason

    def test_every_exclusion_carries_a_reason(self, tmp_path):
        make_image(tmp_path / "thumb.jpg", 200, 200, kb=200)
        make_image(tmp_path / "small.jpg", kb=10)
        for candidate in discover(tmp_path):
            if not candidate.included:
                assert candidate.reason.strip()


class TestSummary:
    def test_counts_and_grouping(self, tree):
        make_image(tree / "ch1" / "thumb.jpg", 100, 100, kb=200)
        summary = summarise(discover(tree, recursive=True))
        assert summary["total"] == 4
        assert summary["included"] == 3
        assert summary["skipped"] == 1
        assert len(summary["folders"]) == 2

    def test_estimate_scales_with_included_count(self, tree):
        summary = summarise(discover(tree, recursive=True))
        assert summary["estimated_seconds"] == 3 * 36

    def test_reasons_collapse_varying_numbers(self, tmp_path):
        make_image(tmp_path / "a.jpg", 100, 100, kb=200)
        make_image(tmp_path / "b.jpg", 300, 300, kb=200)
        summary = summarise(discover(tmp_path))
        # "尺寸过小 100×100" and "尺寸过小 300×300" are one reason, not two.
        assert summary["reasons"] == {"尺寸过小": 2}
