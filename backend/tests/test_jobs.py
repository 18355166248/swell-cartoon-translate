import threading
import time

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
        # Pages already done must be openable in the editor.
        assert job.project_path

    def test_cancelling_a_finished_job_is_a_noop(self, pages, tmp_path):
        manager = JobManager()
        job = manager.submit(pages, str(tmp_path / "out"), lambda: FakePipeline())
        assert wait_until(lambda: job.status == "done")
        assert manager.cancel(job.id) is False


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
