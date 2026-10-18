from dataclasses import dataclass

from ..constants import VIDEO_MODES, OUTPUT_TYPES
from .codecs import C_LB, C_SQ, C_HQ, C_PR422, C_X264, C_X265


@dataclass(frozen=True)
class Preset:
    name: str
    kind: str          # custom | proxy | master | delivery | audio
    summary: str
    overrides: dict


def _p(name, kind, summary, codec, res, quality="Medium (Balanced)", audio="PCM 16-bit"):
    return Preset(name, kind, summary, dict(
        video_mode=VIDEO_MODES[0], video_codec=codec, resolution=res,
        quality=quality, audio_codec=audio, output_type=OUTPUT_TYPES[0],
        convert_audio=True,
    ))


DEFAULT_PRESET = "Proxy — DNxHR LB 720p"

PRESETS = [
    Preset("Custom", "custom", "Choose every setting yourself.", {}),
    _p("Proxy — DNxHR LB 540p", "proxy",
       "Very small files for weak hardware or heavy 4K/8K timelines.", C_LB, "540p"),
    _p(DEFAULT_PRESET, "proxy",
       "Recommended general-purpose editing proxy.", C_LB, "720p"),
    _p("Proxy — DNxHR LB 1080p", "proxy",
       "Sharper proxy for judging focus and detail while editing.", C_LB, "1080p"),
    _p("Proxy — DNxHR SQ 1080p", "proxy",
       "Higher-quality editing intermediate. Noticeably bigger than LB.", C_SQ, "1080p"),
    _p("Master — DNxHR HQ", "master",
       "Original resolution, maximum quality. ⚠ Very large files.",
       C_HQ, "Original", "Ultra (Slowest, Best)", "PCM 24-bit"),
    _p("Master — ProRes 422", "master",
       "Original resolution ProRes 422 mezzanine. ⚠ Large files.",
       C_PR422, "Original", "Ultra (Slowest, Best)", "PCM 24-bit"),
    _p("YouTube — H.264", "delivery",
       "Final upload: 1080p H.264 + AAC.", C_X264, "1080p", "High (Slower, Better)", "AAC"),
    _p("YouTube — H.265", "delivery",
       "Final upload: 1080p H.265 + AAC. Slow to encode on CPU.",
       C_X265, "1080p", "High (Slower, Better)", "AAC"),
    Preset("Audio Extract Only", "audio", "Audio only, FLAC at 48 kHz.",
           {"output_type": "Audio only", "audio_codec": "FLAC", "sample_rate": "48000"}),
]
PRESET_NAMES = [p.name for p in PRESETS]
PRESET_BY_NAME = {p.name: p for p in PRESETS}
COMPARE_PRESETS = [p for p in PRESETS if p.kind in ("proxy", "master", "delivery")]