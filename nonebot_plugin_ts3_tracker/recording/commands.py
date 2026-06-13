from __future__ import annotations


def parse_record_command_args(raw: str) -> str | None:
    remainder = raw.removeprefix("录制").strip()
    return remainder or None


def parse_stop_record_command_args(raw: str) -> str | None:
    remainder = raw.removeprefix("停止录制").strip()
    return remainder or None
