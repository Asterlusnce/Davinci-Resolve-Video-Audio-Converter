import datetime
import shlex

from .ffmpeg_cmd import build_ffmpeg_cmd
from .naming import get_output_path
from .settings import ConversionSettings


def build_plan_text(files: list, s: ConversionSettings, output_folder: str) -> str:
    lines = [
        "=" * 41, "  DAVINCI CONVERTER - DRY RUN", "=" * 41,
        f"Date: {datetime.datetime.now()}",
        f"Total files: {len(files)}", "",
        "Settings:",
        f"  Preset: {s.preset}",
        f"  Video: {s.video_codec} | {s.resolution} | {s.quality}",
        f"  Audio: {s.audio_codec} | {s.sample_rate}",
        f"  Output: {s.output_type}",
        f"  GPU encode: {s.use_gpu} | GPU decode (NVDEC): {s.use_hwaccel_decode}",
        "", "=" * 41, "Commands (copy-pasteable, quoted for the shell):", "=" * 41, "",
    ]
    for f in files:
        out = get_output_path(f, output_folder, s)
        lines += [f"# File: {f.rsplit('/', 1)[-1]}", f"# Output: {out}",
                  shlex.join(build_ffmpeg_cmd(f, out, s)), ""]
    return "\n".join(lines)