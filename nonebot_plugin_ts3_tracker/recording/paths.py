from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


_INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_channel_name(name: str) -> str:
    cleaned = _INVALID_PATH_CHARS.sub("_", name.strip())
    cleaned = cleaned.strip(" .")
    return cleaned or "unnamed"


def build_channel_directory(output_dir: Path, channel_id: str, channel_name: str) -> Path:
    safe_name = sanitize_channel_name(channel_name)
    return output_dir / f"{channel_id}_{safe_name}"


def build_session_filename(started_at: datetime) -> str:
    return started_at.strftime("%Y-%m-%d_%H%M%S.wav")


def build_session_paths(
    output_dir: Path,
    channel_id: str,
    channel_name: str,
    started_at: datetime,
) -> tuple[Path, Path]:
    channel_dir = build_channel_directory(output_dir, channel_id, channel_name)
    wav_path = channel_dir / build_session_filename(started_at)
    meta_path = wav_path.with_suffix(".json")
    return wav_path, meta_path
