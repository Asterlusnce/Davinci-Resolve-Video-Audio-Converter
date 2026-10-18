import json
import subprocess
from dataclasses import dataclass
from typing import Optional

from ..utils import format_size, format_time, get_file_size_mb
from .ffmpeg import TOOLS


@dataclass
class MediaInfo:
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    vcodec: str = ""
    size_mb: float = 0.0

    @property
    def ok(self) -> bool:
        return self.duration > 0


def probe_media(path: str) -> MediaInfo:
    """One ffprobe call per file: duration plus first video stream's size/fps/codec."""
    info = MediaInfo(size_mb=get_file_size_mb(path))
    try:
        res = subprocess.run(
            [TOOLS.ffprobe, "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate",
             "-of", "json", path],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(res.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return info
    try:
        info.duration = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        info.duration = 0.0
    for st in data.get("streams") or []:
        if st.get("codec_type") == "video" and st.get("width"):
            info.width = int(st.get("width") or 0)
            info.height = int(st.get("height") or 0)
            info.vcodec = st.get("codec_name") or ""
            num, _, den = (st.get("avg_frame_rate") or "0/1").partition("/")
            try:
                info.fps = float(num) / float(den) if float(den) else 0.0
            except ValueError:
                info.fps = 0.0
            break
    return info


CODEC_PRETTY = {"h264": "H.264", "hevc": "H.265", "prores": "ProRes", "dnxhd": "DNxHD/HR",
                "vp9": "VP9", "av1": "AV1", "mpeg4": "MPEG-4"}


def describe_media(info: Optional[MediaInfo]) -> str:
    if info is None:
        return "Reading file info…"
    bits = []
    if info.vcodec:
        bits.append(CODEC_PRETTY.get(info.vcodec, info.vcodec.upper()))
    if info.width:
        bits.append(f"{info.width}×{info.height}")
    if info.fps:
        bits.append(f"{info.fps:.2f}".rstrip("0").rstrip(".") + " fps")
    bits.append(format_size(info.size_mb))
    if info.duration:
        bits.append(format_time(info.duration))
    return " · ".join(bits)