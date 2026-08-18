import threading
import time
from pathlib import Path

import pytest

from ctt.jobs import JobManager


class FakePipeline:
    """Stands in for the real pipeline; no models, no images."""

    def __init__(self, per_page: float = 0.0, fail: bool = False):
        self.per_page = per_page
        self.fail = fail
        self.translator = type("T", (), {"name": "fake"})()
        self.source_lang = "en"
        self.target_lang = "zh-Hans"
        self.glossary = type("G", (), {"entries": {}})()

    def run_page(self, path, on_stage=None):
        if self.fail:
            raise RuntimeError("boom")
        for stage in ("detect", "ocr", "translate"):
            if on_stage:
                on_stage(stage)
        time.sleep(self.per_page)
        from ctt.types import Page

        return type("R", (), {
            "page": Page(image_path=str(path), width=10, height=10, blocks=[]),
            "image": _blank(),
            "needs_review": [],
        })()


def _blank():
    import numpy as np

    return np.zeros((4, 4, 3), dtype="uint8")


@pytest.fixture
def pages(tmp_path):
    paths = []
    for i in range(3):
        p = tmp_path / f"page{i}.jpg"
        p.write_bytes(b"x")
        paths.append(str(p))
    return paths


def wait_until(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TestSubmit:
    def test_submit_returns_promptly(self, pages, tmp_path):
        """The regression this file exists for.

        `submit` used to call the lock-taking `busy` property while already
        holding that same non-reentrant lock, so the HTTP request that
        started a job never returned at all.
        """
        manager = JobManager()
        done = threading.Event()

        def go():
            manager.submit(pages, str(tmp_path / "out"), lambda: FakePipeline())
            done.set()

        threading.Thread(target=go, daemon=True).start()
        assert done.wait(timeout=5.0), "submit deadlocked"

    def test_job_runs_to_completion(self, pages, tmp_path):
        manager = JobManager()
        job = manager.submit(pages, str(tmp_path / "out"), lambda: FakePipeline())
        assert wait_until(lambda: job.status == "done")
        assert len(job.results) == 3
        assert job.project_path

    def test_busy_is_false_once_finished(self, pages, tmp_path):
        manager = JobManager()
        job = manager.submit(pages, str(tmp_path / "out"), lambda: FakePipeline())
        assert wait_until(lambda: job.status == "done")
        assert not manager.busy

    def test_second_job_is_refused_while_one_runs(self, pages, tmp_path):
        manager = JobManager()
        manager.submit(pages, str(tmp_path / "o1"), lambda: FakePipeline(per_page=0.3))
        with pytest.raises(RuntimeError, match="already running"):
            manager.submit(pages, str(tmp_path / "o2"), lambda: FakePipeline())

    def test_a_new_job_is_allowed_after_the_first_ends(self, pages, tmp_path):
        manager = JobManager()
        first = manager.submit(pages, str(tmp_path / "o1"), lambda: FakePipeline())
        assert wait_until(lambda: first.status == "done")
        second = manager.submit(pages, str(tmp_path / "o2"), lambda: FakePipeline())
        assert wait_until(lambda: second.status == "done")


class TestFailureAndCancel:
    def test_failure_is_recorded_not_raised(self, pages, tmp_path):
        manager = JobManager()
        job = manager.submit(pages, str(tmp_path / "out"), lambda: FakePipeline(fail=True))
        assert wait_until(lambda: job.status == "failed")
        assert "boom" in job.error
        assert not manager.busy, "a failed job must release the slot"

    def test_cancel_stops_between_pages(self, pages, tmp_path):
        manager = JobManager()
        job = manager.submit(pages, str(tmp_path / "out"), lambda: FakePipeline(per_page=0.4))
        assert wait_until(lambda: len(job.results) >= 1)
        assert manager.cancel(job.id)
        assert wait_until(lambda: job.status == "cancelled")
        assert len(job.results) < 3

    def test_cancelled_job_still_writes_its_project(self, pages, tmp_path):
        manager = JobManager()
        job = manager.submit(pages, str(tmp_path / "out"), lambda: FakePipeline(per_page=0.4))
        assert wait_until(lambda: len(job.results) >= 1)
        manager.cancel(job.id)
        assert wait_until(lambda: job.status == "cancelled")
        # A terminal status must imply the outputs are already on disk. The UI
        # navigates to the results tab the moment it sees one, so publishing
        # the status before writing the project sends it somewhere empty.
        assert job.project_path
        from pathlib import Path

        assert Path(job.project_path).is_file()

    def test_cancelling_a_finished_job_is_a_noop(self, pages, tmp_path):
        manager = JobManager()
        job = manager.submit(pages, str(tmp_path / "out"), lambda: FakePipeline())
        assert wait_until(lambda: job.status == "done")
        assert manager.cancel(job.id) is False


class TestOutputLayout:
    def test_mirror_layout_keeps_chapter_folders(self, tmp_path):
        root = tmp_path / "series"
        for chapter in ("ch1", "ch2"):
            (root / chapter).mkdir(parents=True)
            (root / chapter / "p1.jpg").write_bytes(b"x")

        manager = JobManager()
        out = tmp_path / "out"
        job = manager.submit(
            [str(root / "ch1" / "p1.jpg"), str(root / "ch2" / "p1.jpg")],
            str(out), lambda: FakePipeline(), input_root=str(root), layout="mirror",
        )
        assert wait_until(lambda: job.status == "done")

        assert (out / "ch1" / "p1_zh.jpg").is_file()
        assert (out / "ch2" / "p1_zh.jpg").is_file()

    def test_pages_from_different_chapters_do_not_overwrite(self, tmp_path):
        # Identical filenames across chapters are the norm, and flattening
        # them silently loses every page but the last.
        root = tmp_path / "series"
        for chapter in ("ch1", "ch2"):
            (root / chapter).mkdir(parents=True)
            (root / chapter / "page.jpg").write_bytes(b"x")

        manager = JobManager()
        out = tmp_path / "out"
        job = manager.submit(
            [str(root / "ch1" / "page.jpg"), str(root / "ch2" / "page.jpg")],
            str(out), lambda: FakePipeline(), input_root=str(root), layout="mirror",
        )
        assert wait_until(lambda: job.status == "done")
        assert len(list(out.rglob("*_zh.jpg"))) == 2


class TestCopySkipped:
    def _setup(self, tmp_path):
        from ctt.jobs import SkippedFile

        root = tmp_path / "series" / "ch1"
        root.mkdir(parents=True)
        (root / "p1.jpg").write_bytes(b"page")
        (root / "cover.jpg").write_bytes(b"cover-bytes")
        return root, [SkippedFile(path=str(root / "cover.jpg"), reason="文件过小")]

    def test_skipped_files_are_copied_into_the_output(self, tmp_path):
        root, skipped = self._setup(tmp_path)
        out = tmp_path / "out"
        manager = JobManager()
        job = manager.submit(
            [str(root / "p1.jpg")], str(out), lambda: FakePipeline(),
            input_root=str(root.parent), layout="mirror", skipped=skipped,
        )
        assert wait_until(lambda: job.status == "done")

        # A chapter with holes is worse than one with untranslated pages.
        copied = out / "ch1" / "cover.jpg"
        assert copied.is_file()
        assert copied.read_bytes() == b"cover-bytes"
        assert job.copied == 1

    def test_copied_file_sits_beside_the_translated_pages(self, tmp_path):
        root, skipped = self._setup(tmp_path)
        out = tmp_path / "out"
        manager = JobManager()
        job = manager.submit(
            [str(root / "p1.jpg")], str(out), lambda: FakePipeline(),
            input_root=str(root.parent), layout="mirror", skipped=skipped,
        )
        assert wait_until(lambda: job.status == "done")
        assert (out / "ch1" / "cover.jpg").parent == (out / "ch1" / "p1_zh.jpg").parent

    def test_copying_is_idempotent(self, tmp_path):
        root, skipped = self._setup(tmp_path)
        out = tmp_path / "out"
        for _ in range(2):
            manager = JobManager()
            job = manager.submit(
                [str(root / "p1.jpg")], str(out), lambda: FakePipeline(),
                input_root=str(root.parent), layout="mirror",
                skipped=[type(skipped[0])(path=s.path, reason=s.reason) for s in skipped],
            )
            assert wait_until(lambda: job.status == "done")
        # Second run finds the copy already in place and does nothing.
        assert job.copied == 0


class TestResume:
    def test_existing_output_is_not_retranslated(self, tmp_path):
        root = tmp_path / "ch1"
        root.mkdir()
        for i in range(3):
            (root / f"p{i}.jpg").write_bytes(b"x")
        out = tmp_path / "out"
        pages = [str(root / f"p{i}.jpg") for i in range(3)]

        manager = JobManager()
        first = manager.submit(pages, str(out), lambda: FakePipeline(),
                               input_root=str(root), layout="mirror")
        assert wait_until(lambda: first.status == "done")
        assert first.reused == 0

        # At ~36s a page, repeating a finished chapter costs hours for nothing.
        second = manager.submit(pages, str(out), lambda: FakePipeline(),
                                input_root=str(root), layout="mirror")
        assert wait_until(lambda: second.status == "done")
        assert second.reused == 3

    def test_overwrite_forces_a_retranslation(self, tmp_path):
        root = tmp_path / "ch1"
        root.mkdir()
        (root / "p0.jpg").write_bytes(b"x")
        out = tmp_path / "out"
        pages = [str(root / "p0.jpg")]

        manager = JobManager()
        first = manager.submit(pages, str(out), lambda: FakePipeline(),
                               input_root=str(root), layout="mirror")
        assert wait_until(lambda: first.status == "done")

        second = manager.submit(pages, str(out), lambda: FakePipeline(),
                                input_root=str(root), layout="mirror", overwrite=True)
        assert wait_until(lambda: second.status == "done")
        assert second.reused == 0

    def test_resumed_project_still_lists_every_page(self, tmp_path):
        # Otherwise the results tab shows only the pages this run touched.
        root = tmp_path / "ch1"
        root.mkdir()
        for i in range(2):
            (root / f"p{i}.jpg").write_bytes(b"x")
        out = tmp_path / "out"
        pages = [str(root / f"p{i}.jpg") for i in range(2)]

        manager = JobManager()
        first = manager.submit(pages, str(out), lambda: FakePipeline(),
                               input_root=str(root), layout="mirror")
        assert wait_until(lambda: first.status == "done")

        second = manager.submit(pages, str(out), lambda: FakePipeline(),
                                input_root=str(root), layout="mirror")
        assert wait_until(lambda: second.status == "done")

        from ctt.types import Project

        project = Project.model_validate_json(
            Path(second.project_path).read_text("utf-8")
        )
        assert len(project.pages) == 2


class TestProgress:
    def test_eta_is_none_before_any_page_completes(self, pages, tmp_path):
        manager = JobManager()
        job = manager.submit(pages, str(tmp_path / "out"), lambda: FakePipeline(per_page=0.5))
        # Extrapolating from zero samples would be invention, not an estimate.
        assert job.eta is None

    def test_eta_appears_once_there_is_a_sample(self, pages, tmp_path):
        manager = JobManager()
        job = manager.submit(pages, str(tmp_path / "out"), lambda: FakePipeline(per_page=0.3))
        assert wait_until(lambda: len(job.results) >= 1 and job.status == "running")
        assert job.eta is not None and job.eta > 0

    def test_dict_shape_matches_what_the_ui_reads(self, pages, tmp_path):
        manager = JobManager()
        job = manager.submit(pages, str(tmp_path / "out"), lambda: FakePipeline())
        assert wait_until(lambda: job.status == "done")
        body = job.to_dict()
        for key in ("id", "status", "total", "completed", "stage", "stages",
                    "elapsed", "eta", "results", "log", "project_path"):
            assert key in body


class TestRelease:
    """The GPU profile holds ~2GB of VRAM, so a finished job must let go of it.

    Waiting for the garbage collector is not good enough: switching profile to
    play a game only frees the card if the weights are actually dropped.
    """

    def test_translator_is_closed_when_a_job_finishes(self, pages, tmp_path):
        closed = []

        class ClosablePipeline(FakePipeline):
            def __init__(self):
                super().__init__()
                chain = type("Chain", (), {})()
                chain.name = "fake"
                backend = type("Backend", (), {})()
                backend.close = lambda: closed.append(True)
                chain.backends = [backend]
                self.translator = chain

        manager = JobManager()
        job = manager.submit(pages, str(tmp_path / "out"), ClosablePipeline)
        assert wait_until(lambda: job.status == "done")
        assert closed, "the translator was never released"

    def test_translator_is_closed_when_a_job_fails(self, pages, tmp_path):
        closed = []

        class FailingPipeline(FakePipeline):
            def __init__(self):
                super().__init__(fail=True)
                chain = type("Chain", (), {})()
                chain.name = "fake"
                backend = type("Backend", (), {})()
                backend.close = lambda: closed.append(True)
                chain.backends = [backend]
                self.translator = chain

        manager = JobManager()
        job = manager.submit(pages, str(tmp_path / "out"), FailingPipeline)
        assert wait_until(lambda: job.status == "failed")
        assert closed, "a failed job must still release the card"

    def test_a_close_that_raises_does_not_fail_the_job(self, pages, tmp_path):
        class BadClosePipeline(FakePipeline):
            def __init__(self):
                super().__init__()
                chain = type("Chain", (), {})()
                chain.name = "fake"
                backend = type("Backend", (), {})()
                def boom():
                    raise RuntimeError("close blew up")
                backend.close = boom
                chain.backends = [backend]
                self.translator = chain

        manager = JobManager()
        job = manager.submit(pages, str(tmp_path / "out"), BadClosePipeline)
        assert wait_until(lambda: job.status == "done"), "cleanup must not mark it failed"

    def test_release_survives_a_pipeline_that_never_built(self, tmp_path):
        # build_pipeline() itself can raise; the finally block still runs.
        def explode():
            raise RuntimeError("no models")

        manager = JobManager()
        job = manager.submit(["x.jpg"], str(tmp_path / "out"), explode)
        assert wait_until(lambda: job.status == "failed")
        assert "no models" in job.error
