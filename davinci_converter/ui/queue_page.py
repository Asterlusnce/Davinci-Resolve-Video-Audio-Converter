"""The queue / progress screen. Renders jobs; all actions go back to the window."""

from dataclasses import dataclass
from types import SimpleNamespace

from gi.repository import Gtk, Pango

from ..core.jobs import (Job, QUEUED, RUNNING, DONE, FAILED, SKIPPED, CANCELLED,
                         TERMINAL)
from ..utils import format_time
from .widgets import esc

STATUS_ICONS = {
    QUEUED: ("document-open-recent-symbolic", []),
    RUNNING: ("media-playback-start-symbolic", ["accent"]),
    DONE: ("emblem-ok-symbolic", ["success"]),
    FAILED: ("dialog-error-symbolic", ["error"]),
    SKIPPED: ("go-next-symbolic", ["dim-label"]),
    CANCELLED: ("process-stop-symbolic", ["warning"]),
}


@dataclass(eq=False)
class JobRowWidgets:
    row: object
    icon: object
    bar: object
    up: object
    down: object
    again: object
    cancel: object
    remove: object
    info: object


class QueuePage:
    def __init__(self, win):
        self.win = win
        self.rows: dict = {}
        self.widget = self._build()

    def _build(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)

        self.title = Gtk.Label(label="Queue", xalign=0)
        self.title.add_css_class("title-2")
        box.append(self.title)

        self.progress_bar = Gtk.ProgressBar(show_text=True)
        box.append(self.progress_bar)
        self.progress_status = Gtk.Label(label="", xalign=0, wrap=True)
        box.append(self.progress_status)
        self.progress_eta = Gtk.Label(label="", xalign=0)
        self.progress_eta.add_css_class("dim-label")
        box.append(self.progress_eta)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        sc = Gtk.ScrolledWindow(vexpand=True, min_content_height=200)
        sc.set_child(self.listbox)
        box.append(sc)

        btns = Gtk.Box(spacing=8)
        self.start_btn = Gtk.Button(label="Start Queue")
        self.start_btn.add_css_class("suggested-action")
        self.start_btn.connect("clicked", lambda _b: self.win.start_queue())
        retry_btn = Gtk.Button(label="Retry Failed")
        retry_btn.connect("clicked", lambda _b: self.win.retry_failed())
        clear_btn = Gtk.Button(label="Clear Finished")
        clear_btn.connect("clicked", lambda _b: self.win.clear_finished())
        cancel_btn = Gtk.Button(label="Cancel All")
        cancel_btn.add_css_class("destructive-action")
        cancel_btn.connect("clicked", lambda _b: self.win.cancel_all())
        for b in (self.start_btn, retry_btn, clear_btn, cancel_btn):
            btns.append(b)
        box.append(btns)

        expander = Gtk.Expander(label="ffmpeg log")
        self.log_buffer = Gtk.TextBuffer()
        self.log_view = Gtk.TextView(buffer=self.log_buffer, editable=False, monospace=True,
                                     wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.log_end_mark = self.log_buffer.create_mark(
            "end", self.log_buffer.get_end_iter(), False)
        log_sc = Gtk.ScrolledWindow(min_content_height=140)
        log_sc.add_css_class("card")
        log_sc.set_child(self.log_view)
        expander.set_child(log_sc)
        box.append(expander)
        return box

    # ---------- log ----------

    def append_log(self, text: str) -> None:
        self.log_buffer.insert(self.log_buffer.get_end_iter(), text + "\n")
        self.log_view.scroll_to_mark(self.log_end_mark, 0.0, False, 0.0, 0.0)

    # ---------- rows ----------

    def add_job(self, job: Job) -> None:
        row = Adw.ActionRow() if False else _new_row()
        icon = Gtk.Image()
        row.add_prefix(icon)
        bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER)
        bar.set_size_request(100, -1)

        def btn(icon_name, tip, cb):
            b = Gtk.Button(icon_name=icon_name, valign=Gtk.Align.CENTER)
            b.add_css_class("flat")
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda _b: cb(job))
            return b

        w = JobRowWidgets(
            row=row, icon=icon, bar=bar,
            up=btn("go-up-symbolic", "Move up", lambda j: self.win.move_job(j, -1)),
            down=btn("go-down-symbolic", "Move down", lambda j: self.win.move_job(j, +1)),
            again=btn("view-refresh-symbolic", "Retry / run again", self.win.run_again),
            cancel=btn("process-stop-symbolic", "Cancel this file", self.win.cancel_job),
            remove=btn("user-trash-symbolic", "Remove from queue", self.win.remove_job),
            info=btn("dialog-information-symbolic", "Details & ffmpeg command",
                     self.win.show_job_details),
        )
        row.add_suffix(bar)
        for b in (w.up, w.down, w.again, w.cancel, w.info, w.remove):
            row.add_suffix(b)
        self.rows[job] = w
        self.listbox.append(row)
        self.update_job(job)

    def update_job(self, job: Job) -> None:
        w = self.rows.get(job)
        if not w:
            return
        icon_name, css = STATUS_ICONS[job.status]
        w.icon.set_from_icon_name(icon_name)
        w.icon.set_css_classes(css)
        w.row.set_title(esc(job.input_path.rsplit("/", 1)[-1]))
        status = {QUEUED: "Queued", RUNNING: f"{int(job.progress * 100)}%"}.get(job.status, "")
        if job.message:
            status = f"{status} · {job.message}" if status else job.message
        w.row.set_subtitle(esc(f"→ {job.output_path}\n{status}"))
        w.bar.set_visible(job.status == RUNNING)
        w.bar.set_fraction(job.progress)
        w.up.set_visible(job.status == QUEUED)
        w.down.set_visible(job.status == QUEUED)
        w.cancel.set_visible(job.status == RUNNING)
        w.remove.set_visible(job.status != RUNNING)
        w.again.set_visible(job.status in TERMINAL)

    def rebuild_order(self, jobs: list) -> None:
        for w in self.rows.values():
            if w.row.get_parent() is not None:
                self.listbox.remove(w.row)
        for j in jobs:
            self.listbox.append(self.rows[j].row)

    def remove_job(self, job: Job) -> None:
        w = self.rows.pop(job, None)
        if w is not None:
            self.listbox.remove(w.row)

    # ---------- overall progress ----------

    def update_overall(self, jobs: list, active_workers: int, batch_started) -> None:
        n = len(jobs)
        self.title.set_label(f"Queue — {n} file(s)")
        if not n:
            self.progress_bar.set_fraction(0)
            self.progress_bar.set_text("")
            self.progress_status.set_label("Queue is empty")
            self.progress_eta.set_label("")
            self.start_btn.set_sensitive(False)
            return
        frac = sum(1.0 if j.status in TERMINAL else (j.progress if j.status == RUNNING else 0.0)
                   for j in jobs) / n
        done = sum(1 for j in jobs if j.status in TERMINAL)
        self.progress_bar.set_fraction(frac)
        self.progress_bar.set_text(f"{done}/{n}")
        running = [j for j in jobs if j.status == RUNNING]
        if running:
            self.progress_status.set_label("Now: " + ", ".join(
                f"{j.input_path.rsplit('/', 1)[-1]} ({int(j.progress * 100)}%)"
                for j in running))
        else:
            queued = sum(1 for j in jobs if j.status == QUEUED)
            self.progress_status.set_label(f"Idle — {queued} waiting" if queued else "All done")
        if running and batch_started and frac > 0.02:
            import time
            elapsed = time.monotonic() - batch_started
            self.progress_eta.set_label(
                f"Estimated time remaining: {format_time(elapsed * (1 - frac) / frac)}")
        else:
            self.progress_eta.set_label("")
        self.start_btn.set_sensitive(active_workers == 0 and any(j.status == QUEUED for j in jobs))