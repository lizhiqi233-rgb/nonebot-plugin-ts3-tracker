from __future__ import annotations

import json
import wave
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Must stay in sync with recorder_sidecar/src/main.rs (48 kHz mono 16-bit PCM).
RECORDER_SAMPLE_RATE = 48_000
RECORDER_CHANNELS = 1
RECORDER_SAMPLE_WIDTH = 2
RECORDER_HEADER_BYTES = 44


@dataclass(slots=True)
class SliceResult:
    channel_id: str
    channel_name: str
    source_path: Path
    output_path: Path
    metadata_path: Path
    requested_seconds: int
    actual_seconds: float
    participant_names: set[str] = field(default_factory=set)


class SliceError(Exception):
    """Raised when a recording slice cannot be produced."""


def slice_wav_tail(
    source_path: Path,
    output_path: Path,
    *,
    duration_seconds: int,
    sample_rate: int = RECORDER_SAMPLE_RATE,
    sample_width: int = RECORDER_SAMPLE_WIDTH,
    header_bytes: int = RECORDER_HEADER_BYTES,
) -> float:
    if duration_seconds <= 0:
        raise SliceError("slice duration must be positive")

    if not source_path.is_file():
        raise SliceError(f"source recording not found: {source_path}")

    file_size = source_path.stat().st_size
    if file_size <= header_bytes:
        raise SliceError("source recording has no audio data yet")

    pcm_size = file_size - header_bytes
    bytes_per_second = sample_rate * sample_width * RECORDER_CHANNELS
    requested_bytes = duration_seconds * bytes_per_second
    slice_bytes = min(pcm_size, requested_bytes)
    if slice_bytes <= 0:
        raise SliceError("source recording has no readable audio data")

    start_offset = header_bytes + pcm_size - slice_bytes
    pcm_data = source_path.read_bytes()[start_offset : start_offset + slice_bytes]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise SliceError(f"slice output already exists: {output_path}")

    with wave.open(str(output_path), "wb") as writer:
        writer.setnchannels(RECORDER_CHANNELS)
        writer.setsampwidth(sample_width)
        writer.setframerate(sample_rate)
        writer.writeframes(pcm_data)

    return slice_bytes / bytes_per_second


def write_slice_metadata(
    result: SliceResult,
    *,
    triggered_at: datetime,
) -> None:
    payload = {
        "channel_id": result.channel_id,
        "channel_name": result.channel_name,
        "triggered_at": triggered_at.strftime("%Y-%m-%d %H:%M:%S"),
        "requested_seconds": result.requested_seconds,
        "actual_seconds": round(result.actual_seconds, 3),
        "source": str(result.source_path),
        "output": str(result.output_path),
        "participants": sorted(result.participant_names),
    }
    result.metadata_path.parent.mkdir(parents=True, exist_ok=True)
    result.metadata_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_slice_command_args(
    raw: str,
    *,
    default_minutes: int,
) -> tuple[int, str | None] | str:
    remainder = raw.removeprefix("切片").strip()
    if not remainder:
        return default_minutes, None

    parts = remainder.split()
    if len(parts) == 1:
        token = parts[0]
        if token.isdigit():
            return int(token), None
        return default_minutes, token

    minutes_token = parts[0]
    if not minutes_token.isdigit():
        return "切片分钟数必须是正整数，例如：/ts 切片 5 Lobby"
    channel = " ".join(parts[1:]).strip()
    if not channel:
        return int(minutes_token), None
    return int(minutes_token), channel


def session_matches_channel_filter(
    *,
    channel_id: str,
    channel_name: str,
    channel_filter: str,
) -> bool:
    normalized = channel_filter.strip()
    if not normalized:
        return True
    if normalized.isdigit() or normalized.lstrip("-").isdigit():
        return channel_id == normalized
    return channel_name.casefold() == normalized.casefold()
