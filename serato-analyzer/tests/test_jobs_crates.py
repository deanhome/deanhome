import threading
import time
from pathlib import Path

from serato_analyzer.jobs import Job, JobRunner, JobState
from serato_analyzer.platforms import LinuxPlatform, SeratoLibrary
from serato_analyzer.serato.crates import (
    Crate, build_crate, crate_display_name, crate_track_files, list_crates, parse_crate,
)


def wait_until(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_job_runs_and_reports_progress():
    runner = JobRunner()
    events = []
    runner.add_listener(lambda e: events.append((e.kind, e.job.state)))
    job = runner.submit(Job("double", [1, 2, 3], lambda x, j: x * 2))
    assert runner.wait(job, 5)
    assert job.state == JobState.COMPLETED and [r.value for r in job.results] == [2, 4, 6]
    assert sum(1 for k, _ in events if k == "progress") == 3


def test_pause_resume_cancel_and_retry():
    gate = threading.Event()
    seen = []

    def work(x, job):
        seen.append(x)
        if x == 2:
            gate.wait(5)
        if x == 3:
            raise RuntimeError("boom")
        return x

    runner = JobRunner()
    job = runner.submit(Job("w", [1, 2, 3, 4, 5], work))
    assert wait_until(lambda: 2 in seen)
    job.pause()
    gate.set()
    time.sleep(0.2)
    assert job.state == JobState.PAUSED and seen == [1, 2]
    job.resume()
    assert runner.wait(job, 5)
    assert job.state == JobState.COMPLETED
    assert [r.item for r in job.failed] == [3]

    retry = runner.retry_failed(job)
    runner.wait(retry, 5)
    assert retry.total == 1

    slow = runner.submit(Job("slow", range(1000), lambda x, j: time.sleep(0.01)))
    assert wait_until(lambda: slow.done > 2)
    slow.cancel()
    assert runner.wait(slow, 5) and slow.state == JobState.CANCELLED and slow.done < 1000


def test_cancel_while_paused():
    runner = JobRunner()
    job = runner.submit(Job("p", range(100), lambda x, j: time.sleep(0.01)))
    assert wait_until(lambda: job.done > 0)
    job.pause()
    job.cancel()
    assert runner.wait(job, 5) and job.state == JobState.CANCELLED


# ---------------------------------------------------------------------------
# Crates
# ---------------------------------------------------------------------------

def test_crate_roundtrip_and_names():
    data = build_crate(["DJ Music/House/a.mp3", "DJ Music/Café/b.flac"])
    assert parse_crate(data) == ["DJ Music/House/a.mp3", "DJ Music/Café/b.flac"]
    assert crate_display_name(Path("Parties%%Wedding%%Dinner.crate")) == "Parties / Wedding / Dinner"


def test_crates_on_external_drive(tmp_path):
    volume = tmp_path / "DJ Music Drive"
    serato = volume / "_Serato_" / "Subcrates"
    serato.mkdir(parents=True)
    (volume / "House").mkdir()
    (volume / "House" / "a.mp3").write_bytes(b"x")
    (serato / "House.crate").write_bytes(build_crate(["House/a.mp3", "House/missing.mp3"]))
    lib = SeratoLibrary(volume / "_Serato_", volume, False)
    crates = list_crates(lib)
    assert [c.name for c in crates] == ["House"]
    files = crate_track_files(LinuxPlatform(env={}), lib, crates[0])
    assert files == [volume / "House" / "a.mp3", volume / "House" / "missing.mp3"]


def test_crate_with_traversal_paths_skipped(tmp_path):
    lib = SeratoLibrary(tmp_path / "_Serato_", tmp_path, False)
    crate = Crate("x", tmp_path / "x.crate", ["../../etc/passwd", "ok.mp3"])
    assert crate_track_files(LinuxPlatform(env={}), lib, crate) == [tmp_path / "ok.mp3"]


def test_events_carry_state_at_emission():
    runner = JobRunner()
    events = []
    runner.add_listener(events.append)
    job = runner.submit(Job("x", [1], lambda x, j: x))
    runner.wait(job, 5)
    states = [e.state for e in events if e.kind == "state"]
    assert states == [JobState.QUEUED, JobState.RUNNING, JobState.COMPLETED]
    assert [e.finished for e in events if e.kind == "state"] == [False, False, True]
