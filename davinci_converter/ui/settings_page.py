"""Everything on the settings screen. Owns its widgets and converts them to
and from ConversionSettings; the window decides what happens next."""

from dataclasses import replace

from gi.repository import Adw, Gtk, Pango

from ..constants import (VIDEO_MODES, RESOLUTIONS, QUALITIES, AUDIO_CODECS,
                         SAMPLE_RATES, OUTPUT_TYPES, FILENAME_MODES,
                         EXISTS_OPTIONS, PARALLEL_JOB_OPTIONS, VIDEO_EXTS,
                         AUDIO_EXTS, LARGE_ESTIMATE_GB)
from ..core.codecs import VIDEO_CODECS, codec_info
from ..core.estimate import change_text, estimate_total, SizeEstimate
from ..core.media import MediaInfo, describe_media
from ..core.presets import PRESET_NAMES, PRESET_BY_NAME, COMPARE_PRESETS
from ..core.settings import ConversionSettings
from ..utils import format_size, format_time, get_file_size_mb
from .widgets import (esc, make_combo_row, combo_value, set_combo_value,
                      confirm_dialog)


def parallel_label(n: int) -> str:
    return PARALLEL_JOB_OPTIONS[min(max(n, 1), len(PARALLEL_JOB_OPTIONS)) - 1]


# ---- pure text builders (no widgets touched) ----

def estimate_text(est, n_files: int, pending: bool) -> str:
    if not n_files:
        return "Add files to see an estimate"
    if est is None:
        return ("Measuring source files…" if pending
                else "Can't estimate (no duration info for these files)")
    lines = [f"Source: {format_size(est.source_mb)} · {est.counted} file(s) · "
             f"{format_time(est.duration)}"]
    change = change_text(est)
    lines.append(f"Estimated output: ~{format_size(est.est_mb)} "
                 f"(likely {format_size(est.low_mb)}–{format_size(est.high_mb)})"
                 + (f" · {change}" if change else ""))
    bits = []
    if est.video_mbps:
        bits.append(f"~{est.video_mbps:.0f} Mbit/s video")
    if est.audio_mbps:
        bits.append(f"~{est.audio_mbps:.1f} Mbit/s audio")
    if bits:
        lines.append("Estimated bitrate: " + " + ".join(bits) + " (average)")
    note = "Rough estimate — actual size may vary."
    if pending or est.counted < n_files:
        note = "Partial estimate (some files unmeasured). " + note
    lines.append(note)
    return "\n".join(lines)


def compare_rows(s: ConversionSettings, infos: list, n_files: int) -> list:
    """(title, subtitle) for each workflow preset, sized against the current files."""
    rows = []
    for p in COMPARE_PRESETS:
        s2 = replace(s, preset=p.name, **p.overrides)
        est = estimate_total(infos, s2, n_files)
        if est is None:
            continue
        big = est.est_mb > LARGE_ESTIMATE_GB * 1024
        rows.append((p.name,
                     f"~{format_size(est.est_mb)} "
                     f"({format_size(est.low_mb)}–{format_size(est.high_mb)}) · "
                     f"{change_text(est)}" + (" ⚠" if big else "")))
    return rows


def files_summary(files: list, media_info: dict) -> str:
    total_mb = sum(get_file_size_mb(p) for p in files)
    dur = sum(media_info[p].duration for p in files if p in media_info)
    text = f"{len(files)} file(s) selected"
    sub = f"{format_size(total_mb)} source" + (f" · {format_time(dur)} total" if dur else "")
    return f"{text}\n{sub}"


def _file_subtitle(path: str, info) -> str:
    return esc(f"Source · {describe_media(info)}\n{path}")


class SettingsPage:
    def __init__(self, win):
        self.win = win
        self._applying = False
        self._file_rows: dict = {}
        self._compare_children: list = []
        self._chooser = None            # keep FileChooserNative alive
        self.widget = self._build()

    # ================= build =================

    def _build(self):
        scroller = Gtk.ScrolledWindow(vexpand=True)
        page = Adw.PreferencesPage()
        scroller.set_child(page)

        # 1 · Input
        files_group = Adw.PreferencesGroup(title="1 · Input")
        page.add(files_group)
        pick_btn = Gtk.Button(label="Select Files…", valign=Gtk.Align.CENTER)
        pick_btn.add_css_class("suggested-action")
        pick_btn.connect("clicked", self._on_pick_files)
        clear_btn = Gtk.Button(label="Clear", valign=Gtk.Align.CENTER)
        clear_btn.connect("clicked", lambda _b: self.win.clear_files())
        btn_box = Gtk.Box(spacing=6)
        btn_box.append(pick_btn)
        btn_box.append(clear_btn)
        self.hint_row = Adw.ActionRow(
            title="No files selected",
            subtitle="Drag files onto this window, or select them. "
                     "H.264/H.265 camera files are your usual source.")
        self.hint_row.add_suffix(btn_box)
        files_group.add(self.hint_row)
        self.files_listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.files_listbox.add_css_class("boxed-list")
        self.files_listbox.set_visible(False)
        files_group.add(self.files_listbox)

        # 2 · Preset
        preset_group = Adw.PreferencesGroup(
            title="2 · Preset",
            description="Creates Resolve-compatible media files. This app does not register "
                        "them inside DaVinci Resolve — link or import them yourself "
                        "(e.g. Media Pool → right-click → Proxy Media → Link Proxy Media…; "
                        "wording varies by Resolve version).")
        page.add(preset_group)
        self.preset_row = make_combo_row("Workflow Preset", PRESET_NAMES)
        self.preset_row.connect("notify::selected", self._on_preset_changed)
        self.role_row = Adw.ActionRow(title="This creates")
        preset_group.add(self.preset_row)
        preset_group.add(self.role_row)

        video_group = Adw.PreferencesGroup(title="Video")
        page.add(video_group)
        self.video_mode_row = make_combo_row("Video Mode", VIDEO_MODES)
        self.resolution_row = make_combo_row(
            "Resolution", RESOLUTIONS,
            subtitle="Scales the short side; smaller files are never upscaled")
        self.video_codec_row = make_combo_row("Video Codec", VIDEO_CODECS)
        self.quality_row = make_combo_row(
            "Quality", QUALITIES,
            subtitle="Affects H.264/H.265 and compressed audio. "
                     "DNxHR/ProRes bitrate is fixed by profile.")
        for r in (self.video_mode_row, self.resolution_row, self.video_codec_row, self.quality_row):
            r.connect("notify::selected", self._on_field_changed)
            video_group.add(r)

        audio_group = Adw.PreferencesGroup(title="Audio")
        page.add(audio_group)
        self.convert_audio_row = Adw.SwitchRow(
            title="Convert Audio", active=True,
            subtitle="Off = copy the original audio untouched (use 'Video only' to drop it)")
        self.convert_audio_row.connect("notify::active", self._on_field_changed)
        self.audio_codec_row = make_combo_row("Audio Codec", AUDIO_CODECS)
        self.audio_codec_row.connect("notify::selected", self._on_field_changed)
        self.sample_rate_row = make_combo_row("Sample Rate", SAMPLE_RATES)
        self.sample_rate_row.connect("notify::selected", self._on_field_changed)
        for r in (self.convert_audio_row, self.audio_codec_row, self.sample_rate_row):
            audio_group.add(r)

        # 3 · Output
        output_group = Adw.PreferencesGroup(title="3 · Output")
        page.add(output_group)
        self.output_type_row = make_combo_row("Output Type", OUTPUT_TYPES)
        self.output_type_row.connect("notify::selected", self._on_field_changed)
        output_group.add(self.output_type_row)

        self.folder_row = Adw.ActionRow(title="Output Folder", subtitle=esc(self.win.output_folder))
        folder_btn = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        folder_btn.connect("clicked", self._on_pick_folder)
        open_folder_btn = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER)
        open_folder_btn.set_tooltip_text("Open output folder")
        open_folder_btn.connect("clicked", lambda _b: self.win.open_path(self.win.output_folder))
        self.folder_row.add_suffix(open_folder_btn)
        self.folder_row.add_suffix(folder_btn)
        output_group.add(self.folder_row)

        self.filename_mode_row = make_combo_row("Filename Handling", FILENAME_MODES)
        self.filename_mode_row.connect("notify::selected", self._on_other_changed)
        output_group.add(self.filename_mode_row)
        self.custom_suffix_row = Adw.EntryRow(title="Custom Suffix", text="_custom")
        self.custom_suffix_row.set_visible(False)
        self.custom_suffix_row.connect("changed", self._on_other_changed)
        output_group.add(self.custom_suffix_row)
        self.on_exists_row = make_combo_row("If Output Exists", EXISTS_OPTIONS)
        output_group.add(self.on_exists_row)

        # 4 · Estimated size
        est_group = Adw.PreferencesGroup(title="4 · Estimated size")
        page.add(est_group)
        self.estimate_row = Adw.ActionRow(title="Estimated output (rough)",
                                          subtitle="Add files to see an estimate")
        est_group.add(self.estimate_row)
        self.warning_row = Adw.ActionRow(title="Heads up")
        self.warning_row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
        self.warning_row.add_css_class("warning")
        self.warning_row.set_visible(False)
        est_group.add(self.warning_row)
        self.compare_row = Adw.ExpanderRow(
            title="Compare presets for these files",
            subtitle="Estimated size of every workflow preset")
        est_group.add(self.compare_row)

        # 5 · Hardware acceleration
        self.hw_group = Adw.PreferencesGroup(title="5 · Hardware acceleration")
        page.add(self.hw_group)
        self.use_gpu_row = Adw.SwitchRow(
            title="Use GPU (NVENC)", subtitle="Hardware-encode H.264/H.265 output",
            active=self.win.has_nvenc)
        self.use_gpu_row.set_sensitive(self.win.has_nvenc)
        self.use_gpu_row.connect("notify::active", self._on_other_changed)
        self.hwaccel_decode_row = Adw.SwitchRow(
            title="Use GPU Decoding (NVDEC)", active=self.win.has_nvdec,
            subtitle=("Decode the source on GPU to cut CPU load — works with any output codec. "
                      "Falls back to CPU automatically if it fails." if self.win.has_nvdec else
                      "Not detected — this ffmpeg build has no CUDA/NVDEC hwaccel"))
        self.hwaccel_decode_row.set_sensitive(self.win.has_nvdec)
        self.hwaccel_decode_row.connect("notify::active", self._on_other_changed)
        self.parallel_row = make_combo_row(
            "Parallel Conversions", PARALLEL_JOB_OPTIONS,
            subtitle="Run more than one ffmpeg job at once (uses more CPU/RAM)")
        for r in (self.use_gpu_row, self.hwaccel_decode_row, self.parallel_row):
            self.hw_group.add(r)
        self.set_hw_availability(self.win.has_nvenc, self.win.has_nvdec)

        # Advanced
        adv_group = Adw.PreferencesGroup(title="Advanced")
        page.add(adv_group)
        self.ffmpeg_path_row = Adw.EntryRow(
            title="FFmpeg path (blank = search PATH)", show_apply_button=True,
            text=self.win.config.get("ffmpeg_path", ""))
        self.ffmpeg_path_row.connect("apply", lambda r: self.win.apply_ffmpeg_path(r.get_text().strip()))
        self.dry_run_row = Adw.SwitchRow(
            title="Dry Run",
            subtitle="Write the ffmpeg commands to a plan file instead of running them",
            active=False)
        self.cmd_row = Adw.ExpanderRow(title="Show FFmpeg command",
                                       subtitle="Exact command for the first file")
        self.cmd_label = Gtk.Label(xalign=0, wrap=True, selectable=True,
                                   wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.cmd_label.add_css_class("monospace")
        for m in ("top", "bottom", "start", "end"):
            getattr(self.cmd_label, f"set_margin_{m}")(10)
        self.cmd_row.add_row(self.cmd_label)
        for r in (self.ffmpeg_path_row, self.dry_run_row, self.cmd_row):
            adv_group.add(r)

        reset_group = Adw.PreferencesGroup()
        page.add(reset_group)
        reset_btn = Gtk.Button(label="Reset to Defaults")
        reset_btn.connect("clicked", self._on_reset)
        reset_group.add(reset_btn)

        # 6 · Queue actions
        action_group = Adw.PreferencesGroup(title="6 · Queue")
        page.add(action_group)
        add_btn = Gtk.Button(label="Add to Queue")
        add_btn.connect("clicked", lambda _b: self.win.request_enqueue(start=False))
        convert_btn = Gtk.Button(label="Convert Now")
        convert_btn.add_css_class("suggested-action")
        convert_btn.add_css_class("pill")
        convert_btn.connect("clicked", lambda _b: self.win.request_enqueue(start=True))
        box = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=12, margin_bottom=24)
        box.append(add_btn)
        box.append(convert_btn)
        action_group.add(box)

        return scroller

    # ================= reading / writing settings =================

    def default_settings(self) -> ConversionSettings:
        return ConversionSettings(use_gpu=self.win.has_nvenc,
                                  use_hwaccel_decode=self.win.has_nvdec)

    def gather(self) -> ConversionSettings:
        parallel_str = combo_value(self.parallel_row)
        return ConversionSettings(
            preset=combo_value(self.preset_row) or "Custom",
            video_mode=combo_value(self.video_mode_row),
            resolution=combo_value(self.resolution_row),
            video_codec=combo_value(self.video_codec_row),
            quality=combo_value(self.quality_row),
            convert_audio=self.convert_audio_row.get_active(),
            audio_codec=combo_value(self.audio_codec_row),
            sample_rate=combo_value(self.sample_rate_row),
            output_type=combo_value(self.output_type_row),
            filename_mode=combo_value(self.filename_mode_row),
            custom_suffix=self.custom_suffix_row.get_text(),
            on_exists=combo_value(self.on_exists_row),
            use_gpu=self.use_gpu_row.get_active(),
            use_hwaccel_decode=self.hwaccel_decode_row.get_active(),
            dry_run=self.dry_run_row.get_active(),
            parallel_jobs=int(parallel_str.split(" ")[0]) if parallel_str else 1,
        )

    def apply(self, s: ConversionSettings) -> None:
        """Push settings into the widgets. Used by restore, reset and presets."""
        self._applying = True
        try:
            set_combo_value(self.preset_row, s.preset)
            set_combo_value(self.video_mode_row, s.video_mode)
            set_combo_value(self.resolution_row, s.resolution)
            set_combo_value(self.video_codec_row, s.video_codec)
            set_combo_value(self.quality_row, s.quality)
            self.convert_audio_row.set_active(s.convert_audio)
            set_combo_value(self.audio_codec_row, s.audio_codec)
            set_combo_value(self.sample_rate_row, s.sample_rate)
            set_combo_value(self.output_type_row, s.output_type)
            set_combo_value(self.filename_mode_row, s.filename_mode)
            self.custom_suffix_row.set_text(s.custom_suffix)
            set_combo_value(self.on_exists_row, s.on_exists)
            self.use_gpu_row.set_active(s.use_gpu and self.win.has_nvenc)
            self.hwaccel_decode_row.set_active(s.use_hwaccel_decode and self.win.has_nvdec)
            self.dry_run_row.set_active(s.dry_run)
            set_combo_value(self.parallel_row, parallel_label(s.parallel_jobs))
        finally:
            self._applying = False
        self.win.refresh_derived()

    def set_folder_label(self, folder: str) -> None:
        self.folder_row.set_subtitle(esc(folder))

    def set_gpu_switch(self, active: bool) -> None:
        self.use_gpu_row.set_active(active)

    # ================= handlers =================

    def _on_preset_changed(self, row, _pspec):
        if self._applying:
            return
        preset = PRESET_BY_NAME.get(combo_value(row))
        if preset and preset.overrides:
            self.apply(replace(self.gather(), preset=preset.name, **preset.overrides))
        else:
            self.win.refresh_derived()

    def _on_field_changed(self, *_args):
        """A field was changed by hand: we're no longer exactly on a preset."""
        if self._applying or not self.win.ready:
            return
        if combo_value(self.preset_row) != "Custom":
            self._applying = True
            set_combo_value(self.preset_row, "Custom")
            self._applying = False
        self.win.refresh_derived()

    def _on_other_changed(self, *_args):
        if not self._applying and self.win.ready:
            self.win.refresh_derived()

    def _on_reset(self, _btn):
        self.apply(self.default_settings())
        self.win.show_toast("Settings reset to defaults")

    def _on_pick_files(self, _btn):
        dialog = Gtk.FileChooserNative.new(
            "Select Media Files", self.win, Gtk.FileChooserAction.OPEN, "Select", "Cancel")
        dialog.set_select_multiple(True)
        for name, exts in (("Video Files", VIDEO_EXTS), ("Audio Files", AUDIO_EXTS)):
            flt = Gtk.FileFilter(name=name)
            for ext in exts:
                flt.add_pattern(f"*.{ext}")
                flt.add_pattern(f"*.{ext.upper()}")
            dialog.add_filter(flt)
        all_filter = Gtk.FileFilter(name="All Files")
        all_filter.add_pattern("*")
        dialog.add_filter(all_filter)

        def on_response(d, response):
            if response == Gtk.ResponseType.ACCEPT:
                self.win.add_input_files([f.get_path() for f in d.get_files() if f.get_path()])
            self._chooser = None
        dialog.connect("response", on_response)
        self._chooser = dialog
        dialog.show()

    def _on_pick_folder(self, _btn):
        dialog = Gtk.FileChooserNative.new(
            "Select Output Folder", self.win, Gtk.FileChooserAction.SELECT_FOLDER,
            "Select", "Cancel")

        def on_response(d, response):
            if response == Gtk.ResponseType.ACCEPT:
                f = d.get_file()
                folder = f.get_path() if f else None
                if folder:
                    self.win.set_output_folder(folder)
            self._chooser = None
        dialog.connect("response", on_response)
        self._chooser = dialog
        dialog.show()

    # ================= display updates (called by the window) =================

    def set_hw_availability(self, has_nvenc: bool, has_nvdec: bool) -> None:
        nvenc = "✅ NVENC available" if has_nvenc else "⚠️ NVENC not usable — CPU encoding"
        nvdec = "✅ NVDEC available" if has_nvdec else "NVDEC not detected"
        self.hw_group.set_description(f"{nvenc} · {nvdec}. Everything works CPU-only too.")
        self.use_gpu_row.set_sensitive(has_nvenc)
        self.hwaccel_decode_row.set_sensitive(has_nvdec)

    def update_controls(self, s: ConversionSettings, has_nvenc: bool, has_nvdec: bool) -> None:
        """Enable/disable rows and set helper subtitles for the current settings."""
        ci = codec_info(s.video_codec)
        is_copy = s.video_mode.startswith("Copy")
        audio_only = s.output_type == "Audio only"
        video_only = s.output_type == "Video only"
        video_active = not audio_only and not is_copy

        self.video_mode_row.set_sensitive(not audio_only)
        self.resolution_row.set_sensitive(video_active)
        self.video_codec_row.set_sensitive(video_active)
        self.convert_audio_row.set_sensitive(not video_only and not audio_only)
        audio_on = not video_only and (s.convert_audio or audio_only)
        self.audio_codec_row.set_sensitive(audio_on)
        self.sample_rate_row.set_sensitive(audio_on)
        self.use_gpu_row.set_sensitive(bool(has_nvenc and ci.hw_encoder and video_active))
        self.hwaccel_decode_row.set_sensitive(bool(has_nvdec and video_active))
        self.custom_suffix_row.set_visible(s.filename_mode.startswith("Custom"))

        self.video_codec_row.set_subtitle(ci.short_desc)
        preset = PRESET_BY_NAME.get(s.preset)
        self.preset_row.set_subtitle(preset.summary if preset else "")
        if audio_only:
            role = "Audio file (not video media)"
        elif is_copy:
            role = "Stream copy — original codec, not a proxy"
        else:
            role = ci.role_label
        self.role_row.set_subtitle(esc(role))

    def show_estimate(self, text: str) -> None:
        self.estimate_row.set_subtitle(esc(text))

    def show_warnings(self, texts: list) -> None:
        if not texts:
            self.warning_row.set_visible(False)
            return
        self.warning_row.set_subtitle(esc("\n".join(texts)))
        self.warning_row.set_visible(True)

    def show_compare(self, rows: list, has_infos: bool) -> None:
        for child in self._compare_children:
            self.compare_row.remove(child)
        self._compare_children = []
        self.compare_row.set_sensitive(has_infos)
        for title, subtitle in rows:
            row = Adw.ActionRow(title=esc(title), subtitle=esc(subtitle))
            self.compare_row.add_row(row)
            self._compare_children.append(row)

    def show_command(self, text: str) -> None:
        self.cmd_label.set_label(text)

    def show_files(self, files: list, media_info: dict) -> None:
        """Rebuild the file list and summary from scratch."""
        while True:
            row = self.files_listbox.get_row_at_index(0)
            if row is None:
                break
            self.files_listbox.remove(row)
        self._file_rows = {}

        if not files:
            self.hint_row.set_title("No files selected")
            self.hint_row.set_subtitle("Drag files onto this window, or select them. "
                                       "H.264/H.265 camera files are your usual source.")
            self.files_listbox.set_visible(False)
            return

        self.files_listbox.set_visible(True)
        for path in files:
            row = Adw.ActionRow(title=esc(path.rsplit("/", 1)[-1]))
            remove_btn = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
            remove_btn.add_css_class("flat")
            remove_btn.connect("clicked", lambda _b, p=path: self.win.remove_file(p))
            row.add_suffix(remove_btn)
            row.set_subtitle(_file_subtitle(path, media_info.get(path)))
            self.files_listbox.append(row)
            self._file_rows[path] = row
        self.show_files_summary(files, media_info)

    def update_file_row(self, path: str, info: MediaInfo) -> None:
        row = self._file_rows.get(path)
        if row:
            row.set_subtitle(_file_subtitle(path, info))

    def show_files_summary(self, files: list, media_info: dict) -> None:
        if not files:
            return
        n_text, _, sub = files_summary(files, media_info).partition("\n")
        self.hint_row.set_title(n_text)
        self.hint_row.set_subtitle(sub)