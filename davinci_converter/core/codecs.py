from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CodecInfo:
    label: str                      # shown in UI and stored in config
    family: str                     # dnxhr | prores | h264 | h265
    sw_encoder: str
    hw_encoder: Optional[str] = None
    profile: Optional[str] = None
    pix_fmt: str = "yuv420p"
    intra: bool = False             # fixed-bitrate intermediate codec
    ref_mbps: float = 0.0           # intra: Mbit/s at 1080p / 29.97 fps
    quality_mbps: Optional[dict] = None     # CRF codecs: Mbit/s @1080p by quality
    quality_mbps_hw: Optional[dict] = None  # same, for NVENC
    role_label: str = ""
    name_role: str = ""
    name_tag: str = ""
    heavy: bool = False
    short_desc: str = ""


_GPU_NOTE = " Uses the GPU encoder when 'Use GPU (NVENC)' is on."

CODEC_LIST = [
    CodecInfo(
        "DNxHR LB (Proxy - Recommended)", "dnxhr", "dnxhd", profile="dnxhr_lb",
        pix_fmt="yuv422p", intra=True, ref_mbps=45,
        role_label="Proxy", name_role="proxy", name_tag="DNxHR_LB",
        short_desc="Lightweight editing proxy. Recommended for most Resolve proxy workflows.",
    ),
    CodecInfo(
        "DNxHR SQ", "dnxhr", "dnxhd", profile="dnxhr_sq",
        pix_fmt="yuv422p", intra=True, ref_mbps=145,
        role_label="Intermediate (higher-quality proxy)", name_role="proxy",
        name_tag="DNxHR_SQ",
        short_desc="Higher-quality editing intermediate. Larger files than LB.",
    ),
    CodecInfo(
        "DNxHR HQ ⚠ Heavy", "dnxhr", "dnxhd", profile="dnxhr_hq",
        pix_fmt="yuv422p", intra=True, ref_mbps=220, heavy=True,
        role_label="Master / mezzanine", name_role="master", name_tag="DNxHR_HQ",
        short_desc="High-quality mezzanine format. Very large files. "
                   "Usually unnecessary for ordinary proxy editing.",
    ),
    CodecInfo(
        "ProRes Proxy", "prores", "prores_ks", profile="0",
        pix_fmt="yuv422p10le", intra=True, ref_mbps=45,
        role_label="Proxy", name_role="proxy", name_tag="ProRes_Proxy",
        short_desc="Lightweight ProRes editing proxy.",
    ),
    CodecInfo(
        "ProRes 422 ⚠ Heavy", "prores", "prores_ks", profile="2",
        pix_fmt="yuv422p10le", intra=True, ref_mbps=147, heavy=True,
        role_label="Master / mezzanine", name_role="master", name_tag="ProRes_422",
        short_desc="High-quality intermediate. Large files.",
    ),
    CodecInfo(
        "H.264 (Software)", "h264", "libx264", pix_fmt="yuv420p",
        quality_mbps={"Low": 2.5, "Medium": 6, "High": 12, "Ultra": 20},
        role_label="Delivery / export", name_role="export", name_tag="h264",
        short_desc="Delivery/source codec. Small files, but generally less "
                   "convenient for intensive editing.",
    ),
    CodecInfo(
        "H.264 (NVENC)", "h264", "libx264", hw_encoder="h264_nvenc",
        pix_fmt="yuv420p",
        quality_mbps={"Low": 2.5, "Medium": 6, "High": 12, "Ultra": 20},
        quality_mbps_hw={"Low": 3, "Medium": 7, "High": 14, "Ultra": 22},
        role_label="Delivery / export", name_role="export", name_tag="h264",
        short_desc="Delivery/source codec, fast on NVIDIA GPUs. Small files, but "
                   "less convenient for editing." + _GPU_NOTE,
    ),
    CodecInfo(
        "H.265 (Software)", "h265", "libx265", pix_fmt="yuv420p",
        quality_mbps={"Low": 1.5, "Medium": 3.5, "High": 7, "Ultra": 12},
        role_label="Delivery / export", name_role="export", name_tag="h265",
        short_desc="Delivery/source codec. Smallest files, but slow to encode and "
                   "hard to edit with.",
    ),
    CodecInfo(
        "H.265 (NVENC)", "h265", "libx265", hw_encoder="hevc_nvenc",
        pix_fmt="yuv420p",
        quality_mbps={"Low": 1.5, "Medium": 3.5, "High": 7, "Ultra": 12},
        quality_mbps_hw={"Low": 2, "Medium": 4.5, "High": 9, "Ultra": 14},
        role_label="Delivery / export", name_role="export", name_tag="h265",
        short_desc="Delivery/source codec, fast on NVIDIA GPUs. Small files, but "
                   "less convenient for editing." + _GPU_NOTE,
    ),
]
CODECS = {c.label: c for c in CODEC_LIST}
VIDEO_CODECS = [c.label for c in CODEC_LIST]

C_LB, C_SQ, C_HQ = VIDEO_CODECS[0], VIDEO_CODECS[1], VIDEO_CODECS[2]
C_PRPROXY, C_PR422 = VIDEO_CODECS[3], VIDEO_CODECS[4]
C_X264, C_X265 = "H.264 (Software)", "H.265 (Software)"


def codec_info(label: str) -> CodecInfo:
    return CODECS.get(label) or CODEC_LIST[0]