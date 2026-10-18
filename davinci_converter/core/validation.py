from dataclasses import dataclass
from typing import Optional

from ..constants import (RESOLUTION_HEIGHTS, LARGE_ESTIMATE_GB, LARGE_RATIO,
                         LONG_DURATION_S, FREE_SPACE_MARGIN)
from ..utils import format_size, format_time
from .codecs import codec_info
from .estimate import SizeEstimate
from .media import MediaInfo
from .naming import get_output_extension
from .settings import ConversionSettings


@dataclass
class Warn:
    text: str
    confirm: bool = False   # ask for confirmation before queueing


def validate_choices(s: ConversionSettings, infos: list[MediaInfo],
                     est: Optional[SizeEstimate], free_mb: Optional[float]) -> list[Warn]:
    """Warn, never block."""
    warns: list[Warn] = []
    ci = codec_info(s.video_codec)
    audio_only = s.output_type == "Audio only"
    copy = s.video_mode.startswith("Copy")
    encoding_video = not audio_only and not copy
    ext = get_output_extension(s.video_codec, s.output_type, s.audio_codec)
    name = ci.name_tag.replace("_", " ")

    if copy and not audio_only:
        warns.append(Warn("ℹ Stream copy keeps the original video codec — this is not an editing proxy."))

    if encoding_video:
        height = RESOLUTION_HEIGHTS.get(s.resolution)
        total_dur = sum(i.duration for i in infos)

        if ci.heavy:
            warns.append(Warn("⚠ Large intermediate — not normally required for proxy editing."))
            if height:
                warns.append(Warn(
                    f"⚠ {name} at {s.resolution}: a master-grade codec on downscaled video. "
                    "For proxies, DNxHR LB/SQ is far smaller with no practical editing benefit.",
                    confirm=True))
            if total_dur > LONG_DURATION_S:
                extra = f" Estimated total: ~{format_size(est.est_mb)}." if est else ""
                warns.append(Warn(
                    f"⚠ {name} on {format_time(total_dur)} of footage will take a lot of disk space.{extra}",
                    confirm=True))
        if height and height <= 360 and (ci.heavy or ci.name_tag == "DNxHR_SQ"):
            warns.append(Warn(
                f"⚠ {name} at {s.resolution} doesn't make much sense — at this size the extra "
                "bitrate buys nothing visible. DNxHR LB is enough.", confirm=True))
        if height:
            small = sum(1 for i in infos if i.width and min(i.width, i.height) < height)
            if small:
                warns.append(Warn(f"ℹ {small} file(s) are already smaller than {s.resolution}; "
                                  "they won't be upscaled."))

    if est:
        if est.est_mb > LARGE_ESTIMATE_GB * 1024 or (
                est.source_mb and est.est_mb > est.source_mb * LARGE_RATIO and est.est_mb > 2048):
            ratio = f" ({est.est_mb / est.source_mb:.0f}× the source)" if est.source_mb else ""
            warns.append(Warn(f"⚠ Estimated output ~{format_size(est.est_mb)}{ratio} — very large.",
                              confirm=True))
        if free_mb and est.est_mb > free_mb * FREE_SPACE_MARGIN:
            warns.append(Warn(f"⛔ Estimated output ~{format_size(est.est_mb)} but only "
                              f"{format_size(free_mb)} is free in the output folder.", confirm=True))

    audio_encoded = s.output_type != "Video only" and (s.convert_audio or audio_only)
    if audio_encoded and not audio_only:
        if ext == "mp4" and s.audio_codec.startswith("PCM"):
            warns.append(Warn("⚠ PCM audio in MP4 is non-standard — many players and upload "
                              "sites choke on it. Use AAC for H.264/H.265.", confirm=True))
        if ext == "mov" and s.audio_codec == "FLAC":
            warns.append(Warn("⛔ FLAC can't be stored in a .mov — the encode will fail. "
                              "Use PCM (best for Resolve).", confirm=True))
        if ext == "mov" and s.audio_codec == "MP3":
            warns.append(Warn("⚠ MP3 audio in a .mov may not be readable in Resolve. PCM is safest.",
                              confirm=True))
        if ext == "mov" and s.audio_codec == "AAC":
            warns.append(Warn("ℹ Resolve on Linux often can't decode AAC audio. PCM is safer for proxies."))
    return warns