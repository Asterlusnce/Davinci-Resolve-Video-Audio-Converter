"""ffmpeg/ffprobe discovery and hardware capability checks."""

import logging
import os
import shutil
import subprocess
from types import SimpleNamespace

log = logging.getLogger("davinci-converter")

TOOLS = SimpleNamespace(ffmpeg="ffmpeg", ffprobe="ffprobe", found=False)


def _is_exe(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def resolve_tools(custom: str = "") -> None:
    """Find ffmpeg/ffprobe: configured path > $DAVINCI_CONVERTER_FFMPEG > PATH."""
    custom = (custom or os.environ.get("DAVINCI_CONVERTER_FFMPEG", "")).strip()
    ffmpeg = None
    if custom:
        p = os.path.expanduser(custom)
        if os.path.isdir(p):
            p = os.path.join(p, "ffmpeg")
        if _is_exe(p):
            ffmpeg = p
    ffmpeg = ffmpeg or shutil.which("ffmpeg")

    ffprobe = None
    if ffmpeg:
        sibling = os.path.join(os.path.dirname(ffmpeg), "ffprobe")
        if _is_exe(sibling):
            ffprobe = sibling
    ffprobe = ffprobe or shutil.which("ffprobe")

    TOOLS.ffmpeg = ffmpeg or "ffmpeg"
    TOOLS.ffprobe = ffprobe or "ffprobe"
    TOOLS.found = bool(ffmpeg and ffprobe)


def _ffmpeg_list(arg: str) -> str:
    try:
        return subprocess.run(
            [TOOLS.ffmpeg, "-hide_banner", arg],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def check_nvenc_support() -> bool:
    """Does this ffmpeg build include NVENC encoders? (Build support only.)"""
    return "h264_nvenc" in _ffmpeg_list("-encoders")


def check_nvenc_works() -> bool:
    """Tiny real encode, so a machine without an NVIDIA GPU isn't misdetected."""
    try:
        r = subprocess.run(
            [TOOLS.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
             "-i", "color=c=black:s=256x256:d=0.2:r=25",
             "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, text=True, timeout=20,
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def check_nvdec_support() -> bool:
    """Does ffmpeg advertise the 'cuda' hwaccel (NVDEC)?"""
    return "cuda" in _ffmpeg_list("-hwaccels").lower()