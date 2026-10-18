"""Output-size estimation. Ballpark only: real size depends on content.
DNxHR/ProRes are near constant-bitrate so they're fairly predictable;
CRF-based H.264/H.265 are not."""

from dataclasses import dataclass
from typing import Optional

from ..utils import pick, q_word
from .codecs import codec_info
from .media import MediaInfo
from .naming import get_audio_quality
from .settings import ConversionSettings

BASE_PIXELS_1080P = 1920 * 1080
BASE_FPS = 29.97

PCM_KBPS = {"PCM 16-bit": 1536, "PCM 24-bit": 2304}          # 48 kHz stereo
FLAC_APPROX_KBPS = {"Low": 700, "Medium": 850, "High": 950, "Ultra": 1000}
COPIED_AUDIO_MBPS = 0.192                                    # guess when audio is copied


def output_pixels(resolution: str, info: MediaInfo) -> float:
    """Pixels of the encoded frame. Never upscales (matches scale_filter())."""
    from ..constants import RESOLUTION_HEIGHTS
    w, h = (info.width, info.height) if info.width and info.height else (1920, 1080)
    target = RESOLUTION_HEIGHTS.get(resolution)
    if not target:
        return w * h
    short = min(w, h)
    if short <= target:
        return w * h
    return w * h * (target / short) ** 2


def estimate_audio_mbps(s: ConversionSettings) -> float:
    if not s.convert_audio and s.output_type != "Audio only":
        return COPIED_AUDIO_MBPS
    if s.audio_codec in PCM_KBPS:
        return PCM_KBPS[s.audio_codec] / 1000
    q = q_word(s.quality)
    if s.audio_codec == "FLAC":
        return pick(FLAC_APPROX_KBPS, q) / 1000
    kbps = get_audio_quality(s.audio_codec, s.quality)
    return int(kbps.rstrip("k")) / 1000 if kbps else 0.0


def estimate_rates(info: MediaInfo, s: ConversionSettings) -> Optional[tuple]:
    """(video_mbps, audio_mbps) for one file, or None if it can't be estimated."""
    if not info.ok:
        return None
    audio = 0.0 if s.output_type == "Video only" else estimate_audio_mbps(s)
    if s.output_type == "Audio only":
        return 0.0, audio
    if s.video_mode.startswith("Copy"):
        return info.size_mb * 8 / info.duration, 0.0   # output ≈ source
    ci = codec_info(s.video_codec)
    ratio = output_pixels(s.resolution, info) / BASE_PIXELS_1080P
    if ci.intra:
        fps = info.fps if info.fps > 0 else BASE_FPS
        video = ci.ref_mbps * ratio * min(max(fps / BASE_FPS, 0.5), 4.0)
    else:
        table = (ci.quality_mbps_hw
                 if (s.use_gpu and ci.hw_encoder and ci.quality_mbps_hw)
                 else ci.quality_mbps)
        video = pick(table, q_word(s.quality)) * ratio
    return video, audio


def spread_for(s: ConversionSettings) -> tuple:
    """(low, high) multipliers around the estimate: how unsure we are."""
    if s.output_type == "Audio only":
        return 0.9, 1.1
    if s.video_mode.startswith("Copy"):
        return 0.95, 1.05
    if codec_info(s.video_codec).intra:
        return 0.85, 1.2        # near-constant bitrate
    return 0.5, 2.0             # CRF: strongly content-dependent


@dataclass
class SizeEstimate:
    est_mb: float
    low_mb: float
    high_mb: float
    source_mb: float
    duration: float
    video_mbit: float
    audio_mbit: float
    counted: int
    total: int

    @property
    def video_mbps(self) -> float:
        return self.video_mbit / self.duration if self.duration else 0.0

    @property
    def audio_mbps(self) -> float:
        return self.audio_mbit / self.duration if self.duration else 0.0

    @property
    def change_pct(self) -> Optional[float]:
        return (self.est_mb / self.source_mb - 1) * 100 if self.source_mb else None


def estimate_total(infos: list, s: ConversionSettings,
                   total_files: Optional[int] = None) -> Optional[SizeEstimate]:
    """estimated_size = duration × bitrate / 8, summed per file."""
    est = vid = aud = src = dur = 0.0
    counted = 0
    for info in infos:
        rates = estimate_rates(info, s)
        if rates is None:
            continue
        v, a = rates
        est += (v + a) * info.duration / 8
        vid += v * info.duration
        aud += a * info.duration
        src += info.size_mb
        dur += info.duration
        counted += 1
    if not counted:
        return None
    lo, hi = spread_for(s)
    return SizeEstimate(est, est * lo, est * hi, src, dur, vid, aud,
                        counted, total_files if total_files is not None else len(infos))


def change_text(est: SizeEstimate) -> str:
    pct = est.change_pct
    if pct is None:
        return ""
    if abs(pct) < 5:
        return "≈ same as source"
    return f"{pct:+.0f}% vs source"