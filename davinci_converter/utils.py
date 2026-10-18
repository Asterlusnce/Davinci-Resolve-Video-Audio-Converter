"""Pure helpers with no GTK dependency."""

import os
import shutil
from typing import Optional

from .constants import VIDEO_EXTS, AUDIO_EXTS


def q_word(quality: str) -> str:
    """'Medium (Balanced)' -> 'Medium'."""
    return quality.split(" ")[0]


def pick(table: dict, q: str):
    return table.get(q, table["Medium"])


def format_time(seconds: float) -> str:
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_size(mb: float) -> str:
    if mb >= 1024 * 1024:
        return f"{mb / 1048576:.2f} TB"
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{mb:.0f} MB"


def get_file_size_mb(path: str) -> float:
    try:
        return os.path.getsize(path) / 1048576
    except OSError:
        return 0.0


def get_free_mb(folder: str) -> Optional[float]:
    path = os.path.abspath(folder)
    while path and not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent
    try:
        return shutil.disk_usage(path).free / 1048576
    except OSError:
        return None


def is_media_file(path: str) -> bool:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return ext in VIDEO_EXTS or ext in AUDIO_EXTS