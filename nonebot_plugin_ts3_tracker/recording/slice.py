from __future__ import annotations

import json
import wave
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .paths import normalize_output_basename

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
    with source_path.open("rb") as reader:
        reader.seek(start_offset)
        pcm_data = reader.read(slice_bytes)
    if len(pcm_data) != slice_bytes:
        raise SliceError("failed to read requested audio tail from source recording")

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


@dataclass(slots=True)
class SliceCommandArgs:
    duration_minutes: int
    output_basename: str | None = None
    channel_filter: str | None = None


def _consume_until_flag(tokens: list[str], start_index: int) -> tuple[str, int]:
    parts: list[str] = []
    index = start_index
    while index < len(tokens) and not tokens[index].startswith("-"):
        parts.append(tokens[index])
        index += 1
    return " ".join(parts).strip(), index


def parse_slice_command_args(
    raw: str,
    *,
    default_minutes: int,
) -> SliceCommandArgs | str:
    remainder = raw.removeprefix("切片").strip()
    minutes = default_minutes
    basename: str | None = None
    channel_filter: str | None = None

    if not remainder:
        return SliceCommandArgs(minutes, basename, channel_filter)

    tokens = remainder.split()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "-sm":
            if index + 1 >= len(tokens):
                return "-sm 需要指定分钟数，例如：/ts 切片 -sm 3 测试"
            minutes_token = tokens[index + 1]
            if not minutes_token.isdigit():
                return "切片分钟数必须是正整数，例如：/ts 切片 -sm 3 测试"
            minutes = int(minutes_token)
            index += 2
            basename, index = _consume_until_flag(tokens, index)
            continue
        if token == "-s":
            if index + 1 >= len(tokens):
                return "-s 需要指定分钟数，例如：/ts 切片 -s 3"
            minutes_token = tokens[index + 1]
            if not minutes_token.isdigit():
                return "切片分钟数必须是正整数，例如：/ts 切片 -s 3"
            minutes = int(minutes_token)
            index += 2
            continue
        if token == "-m":
            if index + 1 >= len(tokens):
                return "-m 需要指定保存文件名，例如：/ts 切片 -m 测试"
            basename, index = _consume_until_flag(tokens, index + 1)
            if not basename:
                return "-m 需要指定保存文件名，例如：/ts 切片 -m 测试"
            continue
        if token == "-c":
            if index + 1 >= len(tokens):
                return "-c 需要指定频道，例如：/ts 切片 -c Lobby"
            channel_filter, index = _consume_until_flag(tokens, index + 1)
            if not channel_filter:
                return "-c 需要指定频道（ID 或名称），例如：/ts 切片 -c Lobby"
            continue
        return (
            "未知参数。用法：/ts 切片 -sm <分钟> <文件名>、"
            "/ts 切片 -s <分钟>、/ts 切片 -m <文件名>、/ts 切片 -c <频道>"
        )

    if minutes <= 0:
        return "切片分钟数必须是正整数。"
    if basename is not None and not normalize_output_basename(basename):
        return "保存文件名无效，请使用不含路径分隔符的名称。"

    return SliceCommandArgs(minutes, basename, channel_filter)
