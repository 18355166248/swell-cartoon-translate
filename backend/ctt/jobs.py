"""Background translation jobs.

A page takes roughly 36 seconds end to end, so a job cannot be an HTTP request
that blocks until finished -- the browser would time out long before a chapter
completed. Jobs run on a worker thread and the UI polls for progress.

Everything lives in memory. This is a single-user local tool: a job that does
not survive a backend restart is not a problem worth a database.

Cancellation is checked between pages, not inside them. The pipeline stages
are opaque blocking calls (ONNX inference, llama.cpp generation), so the
honest granularity is one page; killing mid-page would leave a half-written
image.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import cv2

log = logging.getLogger(__name__)

JobStatus = Literal["pending", "running", "done", "failed", "cancelled"]

STAGES = ["detect", "ocr", "translate", "erase", "typeset"]


@dataclass
class PageResultSummary:
    """What the UI needs to show for one finished page."""

    index: int
    name: str
    source_path: str
    output_path: str
    bubbles: int
    review_count: int
    seconds: float


@dataclass
class Job:
    id: str
    input_paths: list[str]
    output_dir: str
    status: JobStatus = "pending"

    page_index: int = 0
    """0-based index of the page being worked on."""
    stage: str = ""
    """Current pipeline stage, for a two-level progress display."""

    started_at: float = 0.0
    finished_at: float = 0.0
    error: str = ""
    log_lines: list[str] = field(default_factory=list)
    results: list[PageResultSummary] = field(default_factory=list)
    project_path: str = ""

    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def total(self) -> int:
        return len(self.input_paths)

    @property
    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return end - self.started_at if self.started_at else 0.0

    @property
    def eta(self) -> float | None:
        """Seconds remaining, from the average of pages actually completed.

        None until a page finishes: extrapolating from zero samples produces a
        confident-looking number that is pure invention.
        """
        done = len(self.results)
        if done == 0 or done >= self.total:
            return None
        return (self.elapsed / done) * (self.total - done)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "total": self.total,
            "completed": len(self.results),
            "page_index": self.page_index,
            "page_name": (
                Path(self.input_paths[self.page_index]).name
                if self.page_index < self.total
                else ""
            ),
            "stage": self.stage,
            "stages": STAGES,
            "elapsed": round(self.elapsed, 1),
            "eta": round(self.eta, 1) if self.eta is not None else None,
            "error": self.error,
            "output_dir": self.output_dir,
            "project_path": self.project_path,
            "log": self.log_lines[-50:],
            "results": [
                {
                    "index": r.index,
                    "name": r.name,
                    "source_path": r.source_path,
                    "output_path": r.output_path,
                    "bubbles": r.bubbles,
                    "review_count": r.review_count,
                    "seconds": round(r.seconds, 1),
                }
                for r in self.results
            ],
        }


class JobManager:
    """Owns running jobs and the pipeline they share.

    Only one job runs at a time. The models are sized for a 4GB card with the
    GPU deliberately left free; running two chapters concurrently would just
    make both slower while multiplying memory use.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._current: str | None = None

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: -j.started_at)

    def _busy_unlocked(self) -> bool:
        """Caller must already hold `_lock`.

        Split out from the `busy` property because `submit` needs this check
        while holding the lock, and `threading.Lock` is not reentrant --
        calling the locking property from inside the locked section
        deadlocks the request thread outright.
        """
        job = self._jobs.get(self._current or "")
        return job is not None and job.status in {"pending", "running"}

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy_unlocked()

    def submit(self, input_paths: list[str], output_dir: str, build_pipeline) -> Job:
        """Queue a job and start it. Raises if one is already running."""
        with self._lock:
            if self._busy_unlocked():
                raise RuntimeError("a job is already running")
            job = Job(
                id=uuid.uuid4().hex[:12],
                input_paths=[str(p) for p in input_paths],
                output_dir=str(output_dir),
            )
            self._jobs[job.id] = job
            self._current = job.id

        thread = threading.Thread(
            target=self._run, args=(job, build_pipeline), daemon=True, name=f"job-{job.id}"
        )
        thread.start()
        return job

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job.status not in {"pending", "running"}:
            return False
        job._cancel.set()
        job.log_lines.append(_stamp("cancellation requested; finishing current page"))
        return True

    def _run(self, job: Job, build_pipeline) -> None:
        job.status = "running"
        job.started_at = time.time()
        job.log_lines.append(_stamp(f"job {job.id}: {job.total} page(s)"))

        try:
            job.stage = "loading models"
            pipeline = build_pipeline()
            job.log_lines.append(_stamp(f"translators: {pipeline.translator.name}"))

            output = Path(job.output_dir)
            output.mkdir(parents=True, exist_ok=True)

            from .types import Project

            project = Project(
                name=output.name,
                source_lang=pipeline.source_lang,
                target_lang=pipeline.target_lang,
                glossary=pipeline.glossary.entries,
            )

            # Status stays "running" until the project file is on disk. Flipping
            # it to a terminal value inside the loop lets a watcher observe
            # "cancelled" while `project_path` is still empty -- the UI then
            # jumps to the results tab with nothing to open.
            cancelled = False

            for index, source in enumerate(job.input_paths):
                if job._cancel.is_set():
                    cancelled = True
                    job.log_lines.append(_stamp("cancelled"))
                    break

                job.page_index = index
                name = Path(source).name
                started = time.time()

                result = pipeline.run_page(source, on_stage=lambda s: setattr(job, "stage", s))

                destination = output / f"{Path(source).stem}_zh.jpg"
                cv2.imwrite(str(destination), result.image, [cv2.IMWRITE_JPEG_QUALITY, 92])
                project.pages.append(result.page)

                review = result.needs_review
                job.results.append(
                    PageResultSummary(
                        index=index,
                        name=name,
                        source_path=source,
                        output_path=str(destination),
                        bubbles=sum(1 for b in result.page.blocks if b.translatable),
                        review_count=len(review),
                        seconds=time.time() - started,
                    )
                )
                job.log_lines.append(
                    _stamp(f"{name}: {len(project.pages[-1].blocks)} blocks, "
                           f"{len(review)} need review, {time.time() - started:.1f}s")
                )

            project_file = output / "project.cttproj"
            project_file.write_text(project.model_dump_json(indent=2), encoding="utf-8")
            job.project_path = str(project_file)

            if cancelled:
                job.status = "cancelled"
            else:
                job.status = "done"
                job.log_lines.append(_stamp(f"done in {job.elapsed:.0f}s"))

        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            job.log_lines.append(_stamp(f"FAILED: {job.error}"))
            log.exception("job %s failed", job.id)
            job.log_lines.extend(traceback.format_exc().splitlines()[-6:])
        finally:
            job.stage = ""
            job.finished_at = time.time()
            with self._lock:
                if self._current == job.id:
                    self._current = None


def _stamp(message: str) -> str:
    return f"{datetime.now(timezone.utc).astimezone():%H:%M:%S}  {message}"


MANAGER = JobManager()
