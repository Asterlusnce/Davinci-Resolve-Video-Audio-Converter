"""Main window: owns application state and wires the pages, queue and runner together."""

import datetime
import os
import shlex
import threading
from dataclasses import replace

from gi.repository import Adw, Gdk, GLib, Gio, Gtk

from ..config import load_config, save_config
from ..constants import APP_TITLE, FREE_SPACE_MARGIN
from ..core.codecs import codec_info
from ..core.dryrun import build_plan_text
from ..core.estimate import estimate_total
from ..core.ffmpeg import (TOOLS, resolve_tools, check_nvenc_support,
                           check_nvdec_support, check_nvenc_works)
from ..core.ffmpeg_cmd import build_ffmpeg_cmd
from ..core.jobs import (Job, JobQueue, plan_jobs, resolve_conflicts,
                         QUEUED, RUNNING, DONE, FAILED, SKIPPED, CANCELLED,
                         TERMINAL)
from ..core.media import MediaInfo, probe_media
from ..core.naming import get_output_path
from ..core.runner import Runner, write_run_log
from ..core.settings import ConversionSettings, exists_policy
from ..core.validation import validate_choices
from ..utils import format_size, get_free_mb, is_media_file
from .queue_page import QueuePage
from .settings_page import SettingsPage, compare_rows, estimate_text
from .widgets import (confirm_dialog, esc, run_on_main, show_text_window)


def command_preview(files: list, s: ConversionSettings, folder: str) -> str:
    if not files:
        return "(add a file to preview the command)"
    first = files[0]
    out = get_output_path(first, folder, s)
    return shlex.join(build_ffmpeg_cmd(first, out, s))


class ConverterWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_TITLE)
        self.set_default_size(720, 900)

        self.ready = False
        self.config = load_config()
        resolve_tools(self.config.get("ffmpeg_path", ""))
        self.has_nvenc = check_nvenc_support() if TOOLS.found else False
        self.has_nvdec = check_nvdec_support() if TOOLS.found else False

        self.input_files: list = []
        self.media_info: dict = {}          # path -> MediaInfo (main thread only)
        self._probing: set = set()

        default_folder = os.path.join(os.path.expanduser("~"), "converted")
        self.output_folder = self.config.get("output_folder", default_folder)

        self.queue = JobQueue()
        self.runner = Runner(
            self.queue,
            on_update=lambda job: run_on_main(self._on_job_update, job),
            on_log=lambda text: run_on_main(self.queue_page.append_log, text),
            on_batch_done=lambda lines: run_on_main(self._on_batch_finished, lines),
        )

        # ---- chrome ----
        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)
        self.toolbar_view = Adw.ToolbarView()
        self.toast_overlay.set_child(self.toolbar_view)

        header = Adw.HeaderBar()
        self.back_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self.back_btn.set_tooltip_text("Back to settings")
        self.back_btn.connect("clicked", lambda _b: self.show_page("settings"))
        self.back_btn.set_visible(False)
        header.pack_start(self.back_btn)
        self.queue_btn = Gtk.Button(label="Queue")
        self.queue_btn.connect("clicked", lambda _b: self.show_page("queue"))
        self.queue_btn.set_visible(False)
        header.pack_end(self.queue_btn)
        self.toolbar_view.add_top_bar(header)

        # ---- pages ----
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.toolbar_view.set_content(self.stack)
        self.settings_page = SettingsPage(self)
        self.queue_page = QueuePage(self)
        self.stack.add_named(self.settings_page.widget, "settings")
        self.stack.add_named(self.queue_page.widget, "queue")

        drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop_target.connect("drop", self._on_files_dropped)
        self.add_controller(drop_target)

        saved = self.config.get("settings")
        if saved:
            self.settings_page.apply(ConversionSettings.from_json(saved))
        else:
            self.settings_page.apply(self.settings_page.default_settings())

        self.ready = True
        self.refresh_derived()

        if not TOOLS.found:
            GLib.idle_add(self._show_missing_deps_idle)
        else:
            self.verify_nvenc_async()

    # ================= small utilities =================

    def show_toast(self, text: str, timeout: int = 3) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=text, timeout=timeout))

    def open_path(self, path: str) -> None:
        if os.path.exists(path):
            import subprocess
            subprocess.Popen(["xdg-open", path])
        else:
            self.show_toast("Path does not exist yet")

    def show_page(self, name: str) -> None:
        self.stack.set_visible_child_name(name)
        self.back_btn.set_visible(name == "queue")
        self.queue_btn.set_visible(name == "settings" and bool(self.queue.snapshot()))

    def _show_missing_deps_idle(self):
        self.show_missing_deps_dialog()
        return False

    def show_missing_deps_dialog(self) -> None:
        d = Adw.MessageDialog(
            transient_for=self,
            heading="ffmpeg / ffprobe not found",
            body="Install ffmpeg, or set its location under Advanced → FFmpeg path.\n\n"
                 "Arch:  sudo pacman -S ffmpeg\n"
                 "NixOS: add pkgs.ffmpeg-full to your environment",
        )
        d.add_response("ok", "OK")
        d.present()

    def persist_settings(self, s: ConversionSettings) -> None:
        self.config["settings"] = s.to_json()
        self.config["output_folder"] = self.output_folder
        save_config(self.config)

    # ================= hardware =================

    def verify_nvenc_async(self) -> None:
        if not self.has_nvenc:
            return

        def worker():
            if not check_nvenc_works():
                run_on_main(self._nvenc_unusable)
        threading.Thread(target=worker, daemon=True).start()

    def _nvenc_unusable(self) -> None:
        self.has_nvenc = False
        self.settings_page.set_gpu_switch(False)
        self.settings_page.set_hw_availability(self.has_nvenc, self.has_nvdec)
        self.refresh_derived()

    def apply_ffmpeg_path(self, path: str) -> None:
        self.config["ffmpeg_path"] = path
        save_config(self.config)
        resolve_tools(path)
        self.has_nvenc = check_nvenc_support() if TOOLS.found else False
        self.has_nvdec = check_nvdec_support() if TOOLS.found else False
        self.settings_page.set_hw_availability(self.has_nvenc, self.has_nvdec)
        self.media_info.clear()
        self.refresh_files_ui()
        if TOOLS.found:
            self.show_toast(f"Using {TOOLS.ffmpeg}")
            self.verify_nvenc_async()
        else:
            self.show_toast("ffmpeg/ffprobe not found at that path")

    # ================= derived UI state =================

    def current_infos(self) -> list:
        return [self.media_info[p] for p in self.input_files
                if p in self.media_info and self.media_info[p].ok]

    def refresh_derived(self) -> None:
        if not self.ready:
            return
        sp = self.settings_page
        s = sp.gather()
        ci = codec_info(s.video_codec)
        sp.update_controls(s, self.has_nvenc, self.has_nvdec)

        infos = self.current_infos()
        est = estimate_total(infos, s, len(self.input_files)) if infos else None
        pending = any(p not in self.media_info for p in self.input_files)
        sp.show_estimate(estimate_text(est, len(self.input_files), pending))

        warns = validate_choices(s, infos, est, get_free_mb(self.output_folder))
        sp.show_warnings([w.text for w in warns])
        sp.show_compare(compare_rows(s, infos, len(self.input_files)) if infos else [],
                        has_infos=bool(infos))
        sp.show_command(command_preview(self.input_files, s, self.output_folder))
        self.queue_btn.set_visible(self.stack.get_visible_child_name() == "settings"
                                   and bool(self.queue.snapshot()))

    # ================= input files =================

    def _on_files_dropped(self, _target, value, _x, _y):
        try:
            files = value.get_files()
        except AttributeError:
            files = [value] if isinstance(value, Gio.File) else []
        paths = [f.get_path() for f in files if f.get_path() and is_media_file(f.get_path())]
        added = self.add_input_files(paths)
        if added:
            self.show_toast(f"Added {added} file(s)")
        return True

    def add_input_files(self, paths: list) -> int:
        added = 0
        for p in paths:
            p = os.path.abspath(p)
            if p not in self.input_files:
                self.input_files.append(p)
                added += 1
        if added:
            self.refresh_files_ui()
        return added

    def clear_files(self) -> None:
        self.input_files = []
        self.refresh_files_ui()

    def remove_file(self, path: str) -> None:
        if path in self.input_files:
            self.input_files.remove(path)
            self.refresh_files_ui()

    def refresh_files_ui(self) -> None:
        self.settings_page.show_files(self.input_files, self.media_info)
        self.probe_new_files()
        self.refresh_derived()

    def probe_new_files(self) -> None:
        todo = [p for p in self.input_files if p not in self.media_info and p not in self._probing]
        if not todo:
            return
        self._probing.update(todo)

        def worker():
            for p in todo:
                run_on_main(self._probe_done, p, probe_media(p))
        threading.Thread(target=worker, daemon=True).start()

    def _probe_done(self, path: str, info: MediaInfo) -> None:
        self._probing.discard(path)
        self.media_info[path] = info        # failed probes are cached too: no retry loop
        self.settings_page.update_file_row(path, info)
        self.settings_page.show_files_summary(self.input_files, self.media_info)
        self.refresh_derived()

    def set_output_folder(self, folder: str) -> None:
        self.output_folder = folder
        self.settings_page.set_folder_label(folder)
        self.config["output_folder"] = folder
        save_config(self.config)
        self.refresh_derived()

    # ================= enqueue flow =================

    def request_enqueue(self, start: bool) -> None:
        if not self.input_files:
            self.show_toast("Pick some files first!")
            return
        if not TOOLS.found:
            self.show_missing_deps_dialog()
            return

        s = self.settings_page.gather()
        ci = codec_info(s.video_codec)
        if ci.hw_encoder and not self.has_nvenc:
            self.show_toast("NVENC not available — using the software encoder instead")
        s = replace(s, use_gpu=s.use_gpu and self.has_nvenc,
                    use_hwaccel_decode=s.use_hwaccel_decode and self.has_nvdec)
        self.persist_settings(s)

        try:
            os.makedirs(self.output_folder, exist_ok=True)
        except OSError as e:
            self.show_toast(f"Can't create output folder: {e}")
            return
        if not os.access(self.output_folder, os.W_OK):
            self.show_toast(f"Output folder not writable: {self.output_folder}")
            return

        infos = self.current_infos()
        est = estimate_total(infos, s, len(self.input_files)) if infos else None
        serious = [w.text for w in validate_choices(s, infos, est, get_free_mb(self.output_folder))
                   if w.confirm]
        if serious:
            confirm_dialog(self, "Check these choices",
                           "\n\n".join(serious) +
                           "\n\nYou can still continue — this is just a heads-up.",
                           "Continue anyway", lambda: self._after_validation(s, start))
        else:
            self._after_validation(s, start)

    def _after_validation(self, s: ConversionSettings, start: bool) -> None:
        if s.dry_run:
            self.run_dry_run(s)
            return
        planned, conflicts = plan_jobs(list(self.input_files), s, self.output_folder,
                                       self.queue.active_outputs())
        policy = exists_policy(s)
        if conflicts and policy == "ask":
            self.ask_conflict(planned, conflicts, start)
        else:
            self.commit_jobs(planned, conflicts, policy, start)

    def ask_conflict(self, planned: list, conflicts: list, start: bool) -> None:
        names = "\n".join("• " + os.path.basename(j.output_path) for j in conflicts[:6])
        more = f"\n…and {len(conflicts) - 6} more" if len(conflicts) > 6 else ""
        d = Adw.MessageDialog(
            transient_for=self, heading="Output files already exist",
            body=f"{len(conflicts)} of {len(planned)} output file(s) already exist in "
                 f"{self.output_folder}:\n{names}{more}")
        d.add_response("cancel", "Cancel")
        d.add_response("skip", "Skip Existing")
        d.add_response("rename", "Keep Both")
        d.add_response("overwrite", "Overwrite")
        d.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        d.set_response_appearance("overwrite", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_default_response("rename")
        d.set_close_response("cancel")

        def on_response(_d, r):
            if r in ("skip", "rename", "overwrite"):
                self.commit_jobs(planned, conflicts, r, start)
        d.connect("response", on_response)
        d.present()

    def commit_jobs(self, planned: list, conflicts: list, policy: str, start: bool) -> None:
        resolve_conflicts(planned, conflicts, policy, self.queue.active_outputs())
        self.queue.add(planned)
        for job in planned:
            self.queue_page.add_job(job)
        self.input_files = []
        self.refresh_files_ui()
        self.show_page("queue")
        self.update_overall()
        if start:
            self.start_queue()
        else:
            self.show_toast(f"Added {len(planned)} job(s) to the queue")

    def run_dry_run(self, s: ConversionSettings) -> None:
        plan_path = os.path.join(
            self.output_folder,
            f"conversion_plan_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt")
        try:
            with open(plan_path, "w", encoding="utf-8") as fh:
                fh.write(build_plan_text(self.input_files, s, self.output_folder))
        except OSError as e:
            self.show_toast(f"Could not write plan: {e}")
            return
        d = Adw.MessageDialog(transient_for=self, heading="Dry Run — Preview Saved",
                              body=f"Plan written to:\n{plan_path}")
        d.add_response("open", "Open File")
        d.add_response("close", "Close")
        d.connect("response", lambda _d, r: self.open_path(plan_path) if r == "open" else None)
        d.present()

    # ================= queue actions (main thread) =================

    def update_overall(self) -> None:
        self.queue_page.update_overall(self.queue.snapshot(),
                                       self.queue.active_workers,
                                       self.runner.batch_started)
        self.queue_btn.set_visible(self.stack.get_visible_child_name() == "settings"
                                   and bool(self.queue.snapshot()))

    def _on_job_update(self, job: Job) -> None:
        self.queue_page.update_job(job)
        self.update_overall()

    def start_queue(self) -> None:
        self.runner.start(self.settings_page.gather().parallel_jobs)
        self.update_overall()

    def move_job(self, job: Job, delta: int) -> None:
        if self.queue.move(job, delta):
            self.queue_page.rebuild_order(self.queue.snapshot())

    def remove_job(self, job: Job) -> None:
        if self.queue.remove(job):
            self.queue_page.remove_job(job)
            self.update_overall()

    def cancel_job(self, job: Job) -> None:
        self.runner.cancel(job)
        self.update_overall()

    def cancel_all(self) -> None:
        self.runner.cancel_all()
        self.update_overall()

    def run_again(self, job: Job) -> None:
        if self.queue.reset(job):
            self.queue_page.update_job(job)
            self.start_queue()

    def retry_failed(self) -> None:
        targets = self.queue.retry_targets()
        if not targets:
            self.show_toast("Nothing to retry")
            return
        for j in targets:
            self.queue_page.update_job(j)
        self.start_queue()

    def clear_finished(self) -> None:
        for j in self.queue.remove_finished():
            self.queue_page.remove_job(j)
        self.update_overall()

    def show_job_details(self, job: Job) -> None:
        cmd = job.last_cmd or build_ffmpeg_cmd(job.input_path, job.output_path, job.settings)
        text = [f"Source:  {job.input_path}", f"Output:  {job.output_path}",
                f"Status:  {job.status}" + (f" — {job.message}" if job.message else ""),
                f"Preset:  {job.settings.preset}", "", "ffmpeg command:", shlex.join(cmd)]
        if job.error_tail:
            text += ["", "ffmpeg said:", job.error_tail]
        show_text_window(self, os.path.basename(job.input_path), "\n".join(text))

    def _on_batch_finished(self, lines: list) -> None:
        if self.queue.active_workers > 0:      # a retry started new workers meanwhile
            return
        self.update_overall()
        if not lines:
            return
        jobs = self.queue.snapshot()
        counts = {k: sum(1 for j in jobs if j.status == k) for k in TERMINAL}
        log_path = write_run_log(self.output_folder, lines, counts)

        if counts[FAILED] == 0 and counts[CANCELLED] == 0:
            heading, body = "Queue Complete 🎉", f"✅ {counts[DONE]} file(s) created."
        else:
            heading = "Finished with problems"
            body = (f"✅ Done: {counts[DONE]}\n❌ Failed: {counts[FAILED]}\n"
                    f"⚠️ Cancelled: {counts[CANCELLED]}\n⏭ Skipped: {counts[SKIPPED]}")
        d = Adw.MessageDialog(transient_for=self, heading=heading,
                              body=f"{body}\n\n📁 {self.output_folder}")
        d.add_response("open_folder", "Open Folder")
        if log_path:
            d.add_response("view_log", "View Log")
        d.add_response("close", "Close")

        def handle(_d, r):
            if r == "open_folder":
                self.open_path(self.output_folder)
            elif r == "view_log":
                self.open_path(log_path)
        d.connect("response", handle)
        d.present()