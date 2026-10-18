"""Static option lists and thresholds shared by core and UI."""

APP_ID = "sh.asterlusnce.davinciconverter"
APP_TITLE = "DaVinci Converter"

VIDEO_MODES = ["Re-encode", "Copy (no re-encode)"]
RESOLUTIONS = ["Original", "1080p", "720p", "540p", "360p", "240p", "144p"]
RESOLUTION_HEIGHTS = {"1080p": 1080, "720p": 720, "540p": 540,
                      "360p": 360, "240p": 240, "144p": 144}
QUALITIES = [
    "Medium (Balanced)",
    "Low (Fast, Smaller)",
    "High (Slower, Better)",
    "Ultra (Slowest, Best)",
]
AUDIO_CODECS = ["PCM 16-bit", "PCM 24-bit", "FLAC", "AAC", "MP3"]
SAMPLE_RATES = ["Original", "48000", "44100"]
OUTPUT_TYPES = ["Video + Audio", "Video only", "Audio only"]
FILENAME_MODES = [
    "Workflow naming (recommended)",
    "Add suffix (_converted)",
    "Custom suffix",
    "Same filename",
]
EXISTS_OPTIONS = ["Ask me", "Skip", "Overwrite", "Keep both (auto-number)"]
PARALLEL_JOB_OPTIONS = ["1 (Safest)", "2", "3", "4"]

VIDEO_EXTS = ("mp4", "mkv", "mov", "avi", "webm", "flv", "m4v", "mpg", "mpeg",
              "wmv", "3gp", "ogv", "mts", "m2ts", "ts")
AUDIO_EXTS = ("mp3", "wav", "flac", "aac", "m4a", "ogg", "opus", "wma", "ape",
              "alac", "aiff")

# Warning thresholds
LARGE_ESTIMATE_GB = 20      # warn when the estimated output exceeds this
LARGE_RATIO = 8             # ...or is this many times bigger than the source
LONG_DURATION_S = 30 * 60   # "very long video" for heavy codecs
FREE_SPACE_MARGIN = 0.9     # warn if estimate > 90% of free disk space