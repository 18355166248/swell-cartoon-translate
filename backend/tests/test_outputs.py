from pathlib import Path

import pytest

from ctt.outputs import Layout, copy_destination, destination

ROOT = Path("/series")
OUT = Path("/out")


class TestMirror:
    def test_preserves_chapter_structure(self):
        # The whole point of recursion: a series folder must not collapse
        # into one flat pile of pages.
        result = destination(ROOT / "ch1" / "p1.jpg", ROOT, OUT, Layout.MIRROR)
        assert result == OUT / "ch1" / "p1_zh.jpg"

    def test_handles_nesting_several_levels_deep(self):
        result = destination(ROOT / "vol1" / "ch3" / "p7.png", ROOT, OUT, Layout.MIRROR)
        assert result == OUT / "vol1" / "ch3" / "p7_zh.png"

    def test_file_directly_in_the_root(self):
        assert destination(ROOT / "p1.jpg", ROOT, OUT, Layout.MIRROR) == OUT / "p1_zh.jpg"

    def test_two_chapters_do_not_collide(self):
        a = destination(ROOT / "ch1" / "p1.jpg", ROOT, OUT, Layout.MIRROR)
        b = destination(ROOT / "ch2" / "p1.jpg", ROOT, OUT, Layout.MIRROR)
        assert a != b


class TestOtherLayouts:
    def test_nested_puts_output_inside_the_chapter(self):
        result = destination(ROOT / "ch1" / "p1.jpg", ROOT, OUT, Layout.NESTED)
        assert result == ROOT / "ch1" / "_zh" / "p1.jpg"

    def test_sibling_makes_a_parallel_chapter_folder(self):
        result = destination(ROOT / "ch1" / "p1.jpg", ROOT, OUT, Layout.SIBLING)
        assert result == ROOT / "ch1_zh" / "p1.jpg"

    def test_flat_collapses_everything(self):
        result = destination(ROOT / "ch1" / "p1.jpg", ROOT, OUT, Layout.FLAT)
        assert result == OUT / "p1_zh.jpg"

    def test_flat_collides_across_chapters(self):
        # Documented consequence, which is why it is not the default.
        a = destination(ROOT / "ch1" / "p1.jpg", ROOT, OUT, Layout.FLAT)
        b = destination(ROOT / "ch2" / "p1.jpg", ROOT, OUT, Layout.FLAT)
        assert a == b

    def test_accepts_a_plain_string(self):
        assert destination(ROOT / "a.jpg", ROOT, OUT, "mirror") == OUT / "a_zh.jpg"

    def test_rejects_an_unknown_layout(self):
        with pytest.raises(ValueError):
            destination(ROOT / "a.jpg", ROOT, OUT, "sideways")


class TestCopyDestination:
    def test_keeps_the_original_name(self):
        # An untranslated page *is* the original; naming it _zh would lie.
        result = copy_destination(ROOT / "ch1" / "cover.jpg", ROOT, OUT, Layout.MIRROR)
        assert result == OUT / "ch1" / "cover.jpg"

    def test_lands_beside_its_translated_neighbours(self):
        # Reading order breaks if skipped pages go somewhere else.
        translated = destination(ROOT / "ch1" / "p2.jpg", ROOT, OUT, Layout.MIRROR)
        copied = copy_destination(ROOT / "ch1" / "cover.jpg", ROOT, OUT, Layout.MIRROR)
        assert copied.parent == translated.parent

    def test_follows_the_nested_layout_too(self):
        result = copy_destination(ROOT / "ch1" / "cover.jpg", ROOT, OUT, Layout.NESTED)
        assert result == ROOT / "ch1" / "_zh" / "cover.jpg"


class TestOutsideRoot:
    def test_a_path_outside_the_root_keeps_only_its_name(self):
        # Explicit file lists can point anywhere; the alternative is a
        # ValueError that kills the whole run.
        result = destination(Path("/elsewhere/x.jpg"), ROOT, OUT, Layout.MIRROR)
        assert result == OUT / "x_zh.jpg"
