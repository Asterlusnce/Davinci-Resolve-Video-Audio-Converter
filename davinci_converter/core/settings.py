import dataclasses
from dataclasses import dataclass, asdict

from ..constants import (VIDEO_MODES, RESOLUTIONS, QUALITIES, AUDIO_CODECS,
                         SAMPLE_RATES, OUTPUT_TYPES, FILENAME_MODES,
                         EXISTS_OPTIONS)
from .codecs import CODECS, C_LB
from .presets import DEFAULT_PRESET, PRESET_BY_NAME


@dataclass
class ConversionSettings:
    preset: str = DEFAULT_PRESET
    video_mode: str = VIDEO_MODES[0]
    resolution: str = "720p"
    video_codec: str = C_LB
    quality: str = QUALITIES[0]
    convert_audio: bool = True
    audio_codec: str = AUDIO_CODECS[0]
    sample_rate: str = SAMPLE_RATES[0]
    output_type: str = OUTPUT_TYPES[0]
    filename_mode: str = FILENAME_MODES[0]
    custom_suffix: str = "_custom"
    on_exists: str = EXISTS_OPTIONS[0]
    use_gpu: bool = False
    use_hwaccel_decode: bool = False
    dry_run: bool = False
    parallel_jobs: int = 1

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "ConversionSettings":
        data = dict(data)
        # Migrate older configs
        if data.get("preset") not in PRESET_BY_NAME:
            data["preset"] = "Custom"
        if "on_exists" not in data:
            data["on_exists"] = EXISTS_OPTIONS[2] if data.get("overwrite") else EXISTS_OPTIONS[0]
        if data.get("filename_mode") not in FILENAME_MODES:
            data["filename_mode"] = FILENAME_MODES[0]
        if data.get("video_codec") not in CODECS:
            data["video_codec"] = C_LB
        valid = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in valid})


_POLICY = {
    EXISTS_OPTIONS[0]: "ask",
    EXISTS_OPTIONS[1]: "skip",
    EXISTS_OPTIONS[2]: "overwrite",
    EXISTS_OPTIONS[3]: "rename",
}


def exists_policy(s: ConversionSettings) -> str:
    return _POLICY.get(s.on_exists, "ask")