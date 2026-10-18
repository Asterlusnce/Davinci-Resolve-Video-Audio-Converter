import os
from typing import Optional

from .codecs import codec_info
from .settings import ConversionSettings


def get_audio_codec_name(audio_codec: str) -> str:
    return {
        "PCM 16-bit": "pcm_s16le", "PCM 24-bit": "pcm_s24le",
        "FLAC": "flac", "AAC": "aac", "MP3": "libmp3lame",
    }[audio_codec]


def get_audio_quality(audio_codec: str, quality: str) -> Optional[str]:
    from ..utils import pick, q_word
    q = q_word(quality)
    if audio_codec in ("AAC", "MP3"):
        return pick({"Low": "128k", "Medium": "192k", "High": "256k", "Ultra": "320k"}, q)
    if audio_codec == "FLAC":
        return pick({"Low": "5", "Medium": "8", "High": "10", "Ultra": "12"}, q)
    return None


def get_output_extension(video_codec: str, output_type: str, audio_codec: str) -> str:
    if output_type == "Audio only":
        if audio_codec.startswith("PCM"):
            return "wav"
        return {"FLAC": "flac", "AAC": "m4a", "MP3": "mp3"}.get(audio_codec, "flac")
    if codec_info(video_codec).family in ("dnxhr", "prores"):
        return "mov"
    return "mp4"


def workflow_suffix(s: ConversionSettings) -> str:
    """original.mp4 -> original_proxy_720p_DNxHR_LB.mov, _master_DNxHR_HQ, _youtube_h264 ..."""
    if s.video_mode.startswith("Copy"):
        return "_copy"
    ci = codec_info(s.video_codec)
    role = ci.name_role
    if ci.family in ("h264", "h265") and s.preset.startswith("YouTube"):
        role = "youtube"
    parts = [role]
    if s.resolution != "Original" and not (role == "youtube" and s.resolution == "1080p"):
        parts.append(s.resolution)
    parts.append(ci.name_tag)
    return "_" + "_".join(parts)


def get_suffix(s: ConversionSettings) -> str:
    mode = s.filename_mode
    if mode.startswith("Same"):
        return ""
    if mode.startswith("Custom"):
        return s.custom_suffix
    if s.output_type == "Audio only":
        return "_audio"
    if mode.startswith("Add suffix"):
        return "_converted"
    return workflow_suffix(s)


def get_output_path(input_file: str, output_folder: str, s: ConversionSettings) -> str:
    stem = os.path.splitext(os.path.basename(input_file))[0]
    ext = get_output_extension(s.video_codec, s.output_type, s.audio_codec)
    return os.path.join(output_folder, f"{stem}{get_suffix(s)}.{ext}")


def unique_path(path: str, taken: set) -> str:
    root, ext = os.path.splitext(path)
    n = 1
    while True:
        cand = f"{root}_{n}{ext}"
        if cand not in taken and not os.path.exists(cand):
            return cand
        n += 1


def partial_path(path: str) -> str:
    """ffmpeg writes here; renamed only on success so a cancelled encode
    never leaves a half-written file for Resolve to pick up."""
    root, ext = os.path.splitext(path)
    return f"{root}.partial{ext}"