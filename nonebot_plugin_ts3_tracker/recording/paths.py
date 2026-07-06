from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


_INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_channel_name(name: str) -> str:
    cleaned = _INVALID_PATH_CHARS.sub("_", name.strip())
    cleaned = cleaned.strip(" .")
    return cleaned or "unnamed"


def build_channel_segment(channel_id: str, channel_name: str) -> str:
    safe_name = sanitize_channel_name(channel_name)
    return f"{channel_id}_{safe_name}"


def build_dated_audio_paths(
    root_dir: Path,
    channel_id: str,
    channel_name: str,
    moment: datetime,
    filename: str,
) -> tuple[Path, Path]:
    target_dir = (
        root_dir
        / moment.strftime("%Y-%m-%d")
        / build_channel_segment(channel_id, channel_name)
    )
    wav_path = target_dir / filename
    meta_path = wav_path.with_suffix(".json")
    return wav_path, meta_path


def build_session_paths(
    output_dir: Path,
    channel_id: str,
    channel_name: str,
    started_at: datetime,
) -> tuple[Path, Path]:
    filename = f"{started_at.strftime('%H%M%S')}.wav"
    return build_dated_audio_paths(
        output_dir,
        channel_id,
        channel_name,
        started_at,
        filename,
    )


def build_slice_paths(
    slices_dir: Path,
    channel_id: str,
    channel_name: str,
    triggered_at: datetime,
    duration_minutes: int,
) -> tuple[Path, Path]:
    time_part = triggered_at.strftime("%H%M%S")
    filename = f"{time_part}_slice_{duration_minutes}m.wav"
    return build_dated_audio_paths(
        slices_dir,
        channel_id,
        channel_name,
        triggered_at,
        filename,
    )
