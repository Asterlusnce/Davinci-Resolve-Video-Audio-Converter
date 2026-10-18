"""Job model, the thread-safe queue, and planning/conflict logic. No threads started here."""

import itertools
import os
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Optional

from .naming import get_output_path, unique_path
from .settings import ConversionSettings

QUEUED, RUNNING, DONE, FAILED, SKIPPED, CANCELLED = (
    "queued", "running", "done", "failed", "skipped", "cancelled")
TERMINAL = (DONE, FAILED, SKIPPED, CANCELLED)

_ids = itertools.count(1)


@dataclass(eq=False)
class Job:
    input_path: str
    output_path: str
    settings: ConversionSettings
    id: int = field(default_factory=lambda: next(_ids))
    status: str = QUEUED
    progress: float = 0.0
    message: str = ""
    error_tail: str = ""
    duration: float = 0.0
    last_cmd: list = field(default_factory=list)
    cancel_requested: bool = False
    proc: Optional[subprocess.Popen] = None


class JobQueue:
    """Owns the job list and the lock that guards it. Workers and the UI
    both go through this object."""

    def __init__(self):
        self.jobs: list[Job] = []
        self.lock = threading.Lock()
        self.active_workers = 0

    def snapshot(self) -> list[Job]:
        with self.lock:
            return list(self.jobs)

    def add(self, jobs: list[Job]) -> None:
        with self.lock:
            self.jobs.extend(jobs)

    def active_outputs(self) -> set:
        with self.lock:
            return {os.path.abspath(j.output_path)
                    for j in self.jobs if j.status in (QUEUED, RUNNING)}

    def move(self, job: Job, delta: int) -> bool:
        with self.lock:
            i = self.jobs.index(job)
            j = i + delta
            if not 0 <= j < len(self.jobs) or self.jobs[j].status != QUEUED:
                return False
            self.jobs[i], self.jobs[j] = self.jobs[j], self.jobs[i]
            return True

    def remove(self, job: Job) -> bool:
        with self.lock:
            if job.status == RUNNING or job not in self.jobs:
                return False
            self.jobs.remove(job)
            return True

    def remove_finished(self) -> list[Job]:
        with self.lock:
            finished = [j for j in self.jobs if j.status in TERMINAL]
            self.jobs = [j for j in self.jobs if j.status not in TERMINAL]
            return finished

    def reset(self, job: Job) -> bool:
        """Put a finished job back in the queue. Returns False if it wasn't finished."""
        with self.lock:
            if job.status not in TERMINAL:
                return False
            job.status, job.progress, job.message = QUEUED, 0.0, ""
            job.error_tail, job.cancel_requested = "", False
            return True

    def retry_targets(self) -> list[Job]:
        """Reset every failed/cancelled job and return them."""
        out = []
        with self.lock:
            for j in self.jobs:
                if j.status in (FAILED, CANCELLED):
                    j.status, j.progress, j.message = QUEUED, 0.0, ""
                    j.error_tail, j.cancel_requested = "", False
                    out.append(j)
        return out


def plan_jobs(files: list, s: ConversionSettings, output_folder: str,
              active_outputs: set) -> tuple[list[Job], list[Job]]:
    """Build Jobs. Outputs that would clobber a source, another input of this
    batch, or an active job's output are ALWAYS renamed. Outputs that merely
    exist on disk are returned as 'conflicts' so the user can choose."""
    taken = set(active_outputs)
    sources = {os.path.abspath(f) for f in files}
    planned, conflicts = [], []
    for f in files:
        src = os.path.abspath(f)
        out = os.path.abspath(get_output_path(f, output_folder, s))
        renamed = False
        if out == src or out in sources or out in taken:
            out = unique_path(out, taken | sources)
            renamed = True
        taken.add(out)
        job = Job(src, out, s)
        if renamed:
            job.message = "Renamed to avoid overwriting another file"
        elif os.path.exists(out):
            conflicts.append(job)
        planned.append(job)
    return planned, conflicts


def resolve_conflicts(planned: list[Job], conflicts: list[Job], policy: str,
                      active_outputs: set) -> None:
    """Apply skip / rename / overwrite to outputs that already exist on disk."""
    taken = set(active_outputs) | {j.output_path for j in planned}
    for job in conflicts:
        if policy == "skip":
            job.status = SKIPPED
            job.message = "Output already exists — skipped (use ⟳ to run anyway)"
        elif policy == "rename":
            job.output_path = unique_path(job.output_path, taken)
            taken.add(job.output_path)
            job.message = "Renamed to keep the existing file"
        # "overwrite": ffmpeg runs with -y and replaces the file