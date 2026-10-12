"""Background job queue (the "analysis queue").

Long operations (scanning, artwork search, applying artwork) run here so the
UI never blocks.  Jobs can be paused, resumed, cancelled and their failed
items retried.  Listeners are called from worker threads; the GUI forwards
them to the main thread with a Qt signal.
"""

from __future__ import annotations

import itertools
import threading
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class JobCancelled(Exception):
    pass


@dataclass
class ItemResult:
    item: Any
    ok: bool
    message: str = ""
    value: Any = None


@dataclass
class JobEvent:
    kind: str           # "state" | "progress"
    job: "Job"
    result: ItemResult | None = None
    #: The job's state when the event was emitted.  Listeners on another
    #: thread see the event later, when ``job.state`` may have moved on.
    state: JobState | None = None

    def __post_init__(self) -> None:
        if self.state is None:
            self.state = self.job.state

    @property
    def finished(self) -> bool:
        return self.kind == "state" and self.state in (JobState.CANCELLED, JobState.COMPLETED, JobState.FAILED)


_ids = itertools.count(1)


class Job:
    def __init__(self, name: str, items: Iterable[Any], func: Callable[[Any, "Job"], Any],
                 kind: str = "generic", describe: Callable[[Any], str] | None = None) -> None:
        self.id = next(_ids)
        self.name = name
        self.kind = kind
        self.items = list(items)
        self.func = func
        self.describe = describe or str
        self.state = JobState.QUEUED
        self.results: list[ItemResult] = []
        self.current: str = ""
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.error: str = ""
        self._resume = threading.Event()
        self._resume.set()
        self._cancel = threading.Event()

    # Progress -----------------------------------------------------------
    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def done(self) -> int:
        return len(self.results)

    @property
    def failed(self) -> list[ItemResult]:
        return [r for r in self.results if not r.ok]

    @property
    def fraction(self) -> float:
        return self.done / self.total if self.total else 1.0

    @property
    def is_finished(self) -> bool:
        return self.state in (JobState.CANCELLED, JobState.COMPLETED, JobState.FAILED)

    # Control ------------------------------------------------------------
    def pause(self) -> None:
        if self.state in (JobState.RUNNING, JobState.QUEUED):
            self._resume.clear()
            self.state = JobState.PAUSED

    def resume(self) -> None:
        if self.state == JobState.PAUSED:
            self.state = JobState.RUNNING if self.started_at else JobState.QUEUED
            self._resume.set()

    def cancel(self) -> None:
        self._cancel.set()
        self._resume.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def checkpoint(self) -> None:
        """Block while paused; raise :class:`JobCancelled` if cancelled."""
        while not self._resume.wait(0.1):
            if self._cancel.is_set():
                break
        if self._cancel.is_set():
            raise JobCancelled()


@dataclass
class JobRunner:
    max_concurrent: int = 2
    jobs: list[Job] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._slots = threading.Semaphore(self.max_concurrent)
        self._listeners: list[Callable[[JobEvent], None]] = []
        self._lock = threading.Lock()
        self._threads: dict[int, threading.Thread] = {}

    def add_listener(self, fn: Callable[[JobEvent], None]) -> None:
        self._listeners.append(fn)

    def _emit(self, event: JobEvent) -> None:
        for fn in list(self._listeners):
            try:
                fn(event)
            except Exception:
                traceback.print_exc()

    def submit(self, job: Job) -> Job:
        with self._lock:
            self.jobs.append(job)
        t = threading.Thread(target=self._run, args=(job,), name=f"job-{job.id}", daemon=True)
        self._threads[job.id] = t
        self._emit(JobEvent("state", job))
        t.start()
        return job

    def _run(self, job: Job) -> None:
        with self._slots:
            try:
                job.checkpoint()
            except JobCancelled:
                job.state = JobState.CANCELLED
                job.finished_at = time.time()
                self._emit(JobEvent("state", job))
                return
            job.started_at = time.time()
            if job.state == JobState.QUEUED:
                job.state = JobState.RUNNING
            self._emit(JobEvent("state", job))
            try:
                for item in job.items:
                    job.checkpoint()
                    job.current = job.describe(item)
                    try:
                        value = job.func(item, job)
                        result = ItemResult(item, True, "" if value is None else str(value), value)
                    except JobCancelled:
                        raise
                    except Exception as exc:
                        result = ItemResult(item, False, f"{type(exc).__name__}: {exc}")
                    job.results.append(result)
                    self._emit(JobEvent("progress", job, result))
                job.state = JobState.COMPLETED
            except JobCancelled:
                job.state = JobState.CANCELLED
            except Exception as exc:  # pragma: no cover - defensive
                job.state = JobState.FAILED
                job.error = str(exc)
            job.current = ""
            job.finished_at = time.time()
            self._emit(JobEvent("state", job))

    def retry_failed(self, job: Job) -> Job | None:
        failed = [r.item for r in job.failed]
        if not failed:
            return None
        return self.submit(Job(f"{job.name} (retry)", failed, job.func, job.kind, job.describe))

    def wait(self, job: Job, timeout: float | None = None) -> bool:
        t = self._threads.get(job.id)
        if t:
            t.join(timeout)
            return not t.is_alive()
        return True

    def active(self) -> list[Job]:
        return [j for j in self.jobs if not j.is_finished]

    def cancel_all(self) -> None:
        for j in self.active():
            j.cancel()
