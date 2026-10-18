"""Worker threads that execute ffmpeg. Knows nothing about GTK: it reports
progress through callbacks, which the UI layer marshals to the main thread."""

import datetime
import logging
import os
import subprocess
import threading
import time
from collections import deque
from typing import Callable, Optional

from ..utils import format_size, get_file_size_mb
from .ffmpeg_cmd import build_ffmpeg_cmd, fallback_attempts, is_non_gpu_failure
from .jobs import (Job, JobQueue, QUEUED, RUNNING, DONE, FAILED, CANCELLED,
                   TERMINAL)
from .media import probe_media
from .naming import partial_path

log = logging.getLogger("davinci-converter")

_STATUS_ICONS = {DONE: "✓", FAILED: "❌", CANCELLED: "⚠"}


class Runner:
    def __init__(self, queue: JobQueue,
                 on_update: Callable[[Job], None],
                 on_log: Callable[[str], None],
                 on_batch_done: Callable[[list], None]):
        self.queue = queue
        self.on_update = on_update
        self.on_log = on_log
        self.on_batch_done = on_batch_done
        self.batch_started: Optional[float] = None
        self.log_lines: list[str] = []

    # ---------- public API (called from the UI thread) ----------

    def start(self, parallel: int) -> int:
        """Spawn workers up to `parallel`. Returns how many were started."""
        q = self.queue
        with q.lock:
            queued = sum(1 for j in q.jobs if j.status == QUEUED)
            busy = sum(1 for j in q.jobs if j.status == RUNNING)
            spawn = max(0, min(parallel, queued + busy) - q.active_workers)
            if spawn and q.active_workers == 0:
                self.batch_started = time.monotonic()
                self.log_lines = []
            q.active_workers += spawn
        for _ in range(spawn):
            threading.Thread(target=self._worker_loop, daemon=True).start()
        return spawn

    def cancel(self, job: Job) -> None:
        proc = None
        with self.queue.lock:
            if job.status == QUEUED:
                job.status, job.message = CANCELLED, "Cancelled before start"
            elif job.status == RUNNING:
                job.cancel_requested = True
                proc = job.proc
        _terminate(proc)
        self.on_update(job)

    def cancel_all(self) -> None:
        for j in self.queue.snapshot():
            if j.status in (QUEUED, RUNNING):
                self.cancel(j)

    # ---------- worker internals ----------

    def _worker_loop(self) -> None:
        while True:
            job = self._claim_or_exit()
            if job is None:
                return
            self.on_update(job)
            try:
                self._run_job(job)
            except Exception as e:      # never let a worker die silently
                log.exception("job crashed: %s", job.input_path)
                self._finish(job, FAILED, f"Internal error: {e}")

    def _claim_or_exit(self) -> Optional[Job]:
        """Atomically take the next queued job, or retire this worker."""
        q = self.queue
        with q.lock:
            for j in q.jobs:
                if j.status == QUEUED:
                    j.status, j.progress, j.cancel_requested = RUNNING, 0.0, False
                    return j
            q.active_workers -= 1
            last = q.active_workers == 0
        if last:
            self.on_batch_done(list(self.log_lines))
        return None

    def _finish(self, job: Job, status: str, message: str) -> None:
        job.status, job.message = status, message
        if status == DONE:
            job.progress = 1.0
        self.on_update(job)
        name = os.path.basename(job.input_path)
        self.on_log(f"{_STATUS_ICONS.get(status, '?')} {name}: {message}")
        self.log_lines.append(
            f"{status.upper()}: {job.input_path} -> {job.output_path} — {message}")

    def _run_job(self, job: Job) -> None:
        s = job.settings
        name = os.path.basename(job.input_path)
        if not os.path.isfile(job.input_path):
            return self._finish(job, FAILED, "Input file missing")
        if not job.duration:
            job.duration = probe_media(job.input_path).duration
        src_mb = get_file_size_mb(job.input_path)
        tmp = partial_path(job.output_path)
        self.on_log(f"▶ {name}")

        ok, last_tail, used = False, "", s
        for i, attempt in enumerate(fallback_attempts(s)):
            if job.cancel_requested:
                break
            if i:
                what = "CPU decode" if attempt.use_gpu == s.use_gpu else "CPU decode + CPU encode"
                self.on_log(f"⚠ {name}: GPU path failed, retrying with {what}…")
                job.progress = 0.0
            _remove_quietly(tmp)
            cmd = build_ffmpeg_cmd(job.input_path, tmp, attempt)
            job.last_cmd = cmd
            rc, tail = self._run_ffmpeg(job, cmd)
            if rc == 0 and os.path.isfile(tmp):
                ok, used = True, attempt
                break
            last_tail = tail
            if tail:
                self.on_log(tail)
            if is_non_gpu_failure(tail):
                break

        if job.cancel_requested:
            _remove_quietly(tmp)
            return self._finish(job, CANCELLED, "Cancelled")
        if not ok:
            _remove_quietly(tmp)
            job.error_tail = last_tail
            reason = (last_tail.splitlines() or ["ffmpeg returned an error"])[-1]
            return self._finish(job, FAILED, reason[:200])

        try:
            os.replace(tmp, job.output_path)
        except OSError as e:
            _remove_quietly(tmp)
            return self._finish(job, FAILED, f"Could not save output: {e}")

        out_mb = get_file_size_mb(job.output_path)
        msg = format_size(out_mb)
        if src_mb:
            msg += f" ({out_mb / src_mb * 100:.0f}% of source)"
        if used.use_gpu != s.use_gpu:
            msg += " · CPU encode fallback"
        elif used.use_hwaccel_decode != s.use_hwaccel_decode:
            msg += " · CPU decode fallback"
        self._finish(job, DONE, msg)

    def _run_ffmpeg(self, job: Job, cmd: list) -> tuple:
        """Progress comes from `-progress pipe:1`. stderr is drained on its own
        thread so neither pipe can fill and deadlock. Returns (rc, stderr_tail)."""
        tail: deque = deque(maxlen=25)
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", bufsize=1)
        except OSError as e:
            return 127, f"Could not start ffmpeg: {e}"
        job.proc = proc
        if job.cancel_requested:        # cancel pressed between claim and Popen
            _terminate(proc)

        def drain():
            for line in proc.stderr:
                line = line.rstrip()
                if line:
                    tail.append(line)
        t = threading.Thread(target=drain, daemon=True)
        t.start()

        for line in proc.stdout:
            key, _, val = line.strip().partition("=")
            if key in ("out_time_us", "out_time_ms") and job.duration > 0:
                try:
                    frac = int(val) / 1e6 / job.duration
                except ValueError:
                    continue
                frac = min(0.999, max(0.0, frac))
                if frac > job.progress + 0.002:
                    job.progress = frac
                    self.on_update(job)
        rc = proc.wait()
        t.join(timeout=2)
        job.proc = None
        return rc, "\n".join(tail)


def _terminate(proc) -> None:
    if proc is None:
        return
    try:
        proc.terminate()
    except OSError:
        return
    threading.Timer(5, lambda: proc.kill() if proc.poll() is None else None).start()


def _remove_quietly(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def write_run_log(folder: str, lines: list, counts: dict) -> str:
    """Write the per-batch log next to the outputs. Returns the path or ''."""
    start = datetime.datetime.now()
    path = os.path.join(folder, f"conversion_log_{start:%Y%m%d_%H%M%S}.txt")
    body = ["DAVINCI CONVERTER LOG", f"Date: {start}", ""] + lines + [
        "",
        f"Done: {counts[DONE]}  Failed: {counts[FAILED]}  "
        f"Skipped: {counts.get('skipped', 0)}  Cancelled: {counts[CANCELLED]}",
    ]
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(body))
        return path
    except OSError:
        return ""