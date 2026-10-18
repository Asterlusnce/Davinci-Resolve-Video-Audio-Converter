"""Builds ffmpeg argument lists (never shell strings)."""

import os
from dataclasses import replace

from ..constants import RESOLUTION_HEIGHTS
from ..utils import pick, q_word
from .codecs import codec_info
from .ffmpeg import TOOLS
from .naming import get_audio_codec_name, get_audio_quality
from .settings import ConversionSettings

X26_PRESET = {"Low": "ultrafast", "Medium": "medium", "High": "slow", "Ultra": "veryslow"}
CRF_H264 = {"Low": "28", "Medium": "23", "High": "18", "Ultra": "15"}
CRF_H265 = {"Low": "31", "Medium": "28", "High": "23", "Ultra": "20"}  # x265 CRF runs ~5 higher
NVENC_PRESET = {"Low": "p3", "Medium": "p5", "High": "p6", "Ultra": "p7"}
NVENC_CQ = {"Low": "23", "Medium": "19", "High": "15", "Ultra": "12"}


def scale_filter(resolution: str) -> str:
    """Scale the SHORT side to the target: works for portrait video, keeps
    aspect (-2 = even), never upscales. Evaluated after auto-rotation."""
    t = RESOLUTION_HEIGHTS.get(resolution)
    if not t:
        return ""
    return (f"scale=w=if(gte(iw\\,ih)\\,-2\\,min({t}\\,iw)):"
            f"h=if(gte(iw\\,ih)\\,min({t}\\,ih)\\,-2)")


def video_args(s: ConversionSettings) -> list:
    ci = codec_info(s.video_codec)
    q = q_word(s.quality)
    encoder = ci.hw_encoder if (s.use_gpu and ci.hw_encoder) else ci.sw_encoder
    args = ["-c:v", encoder]
    if ci.family == "dnxhr":
        args += ["-profile:v", ci.profile]
    elif ci.family == "prores":
        args += ["-profile:v", ci.profile, "-vendor", "apl0"]
    elif encoder in ("libx264", "libx265"):
        crf = CRF_H264 if encoder == "libx264" else CRF_H265
        args += ["-preset", pick(X26_PRESET, q), "-crf", pick(crf, q)]
        if encoder == "libx265":        # x265 ignores -loglevel
            args += ["-x265-params", "log-level=error"]
    else:  # nvenc: -cq only takes effect with -rc vbr
        args += ["-preset", pick(NVENC_PRESET, q), "-rc", "vbr",
                 "-cq", pick(NVENC_CQ, q), "-b:v", "0"]
    args += ["-pix_fmt", ci.pix_fmt]
    if ci.family == "h265":
        args += ["-tag:v", "hvc1"]      # QuickTime/Apple friendly tag
    flt = scale_filter(s.resolution)
    if flt:
        args += ["-vf", flt]
    return args


def audio_args(s: ConversionSettings) -> list:
    args = ["-c:a", get_audio_codec_name(s.audio_codec)]
    if s.sample_rate != "Original":
        args += ["-ar", s.sample_rate]
    if s.audio_codec in ("AAC", "MP3"):
        args += ["-b:a", get_audio_quality(s.audio_codec, s.quality)]
    elif s.audio_codec == "FLAC":
        args += ["-compression_level", get_audio_quality(s.audio_codec, s.quality)]
    return args


def build_ffmpeg_cmd(input_file: str, output_file: str, s: ConversionSettings) -> list:
    audio_only = s.output_type == "Audio only"
    copy_video = s.video_mode.startswith("Copy")
    cmd = [TOOLS.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
           "-nostats", "-progress", "pipe:1", "-y"]

    # NVDEC decode. No -hwaccel_output_format: frames return to system memory
    # so every downstream filter/encoder keeps working.
    if s.use_hwaccel_decode and not audio_only and not copy_video:
        cmd += ["-hwaccel", "cuda"]

    cmd += ["-i", input_file]

    if audio_only:
        cmd += ["-vn", "-map", "0:a:0?"]
    else:
        cmd += ["-map", "0:v:0?"]
        if s.output_type == "Video only":
            cmd += ["-an"]
        else:
            cmd += ["-map", "0:a?"]
        cmd += ["-c:v", "copy"] if copy_video else video_args(s)

    if s.output_type != "Video only":
        if s.convert_audio or audio_only:
            cmd += audio_args(s)
        else:
            cmd += ["-c:a", "copy"]

    if os.path.splitext(output_file)[1].lower() in (".mp4", ".m4a"):
        cmd += ["-movflags", "+faststart"]

    cmd.append(output_file)
    return cmd


def fallback_attempts(s: ConversionSettings) -> list:
    """Run order: as configured -> CPU decode -> CPU decode + CPU encode."""
    attempts = [s]
    ci = codec_info(s.video_codec)
    video = s.output_type != "Audio only" and not s.video_mode.startswith("Copy")
    if video and s.use_hwaccel_decode:
        attempts.append(replace(s, use_hwaccel_decode=False))
    if video and s.use_gpu and ci.hw_encoder:
        s3 = replace(s, use_hwaccel_decode=False, use_gpu=False)
        if s3 not in attempts:
            attempts.append(s3)
    return attempts


def is_non_gpu_failure(tail: str) -> bool:
    """Errors a CPU retry cannot fix: don't redo a long encode for these."""
    t = tail.lower()
    return any(m in t for m in ("no space left", "permission denied",
                                "no such file", "read-only file system"))