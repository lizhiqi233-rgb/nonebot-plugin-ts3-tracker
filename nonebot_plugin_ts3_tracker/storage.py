from __future__ import annotations

import contextlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(slots=True)
class TrackedClientSnapshot:
    nickname: str
    unique_id: str
    channel_id: str
    channel_name: str
    connected_duration_seconds: int
    away: bool
    first_seen_at: str = ""


def _parse_snapshot(
    key: str,
    payload: object,
) -> TrackedClientSnapshot:
    if not isinstance(payload, dict):
        raise ValueError(f"snapshot {key!r} must be an object")

    string_fields = (
        "nickname",
        "unique_id",
        "channel_id",
        "channel_name",
    )
    parsed_strings: dict[str, str] = {}
    for field_name in string_fields:
        value = payload.get(field_name)
        if not isinstance(value, str):
            raise ValueError(f"snapshot {key!r} field {field_name!r} must be a string")
        parsed_strings[field_name] = value

    duration = payload.get("connected_duration_seconds")
    if type(duration) is not int or duration < 0:
        raise ValueError(
            f"snapshot {key!r} field 'connected_duration_seconds' "
            "must be a non-negative integer"
        )
    away = payload.get("away")
    if type(away) is not bool:
        raise ValueError(f"snapshot {key!r} field 'away' must be a boolean")
    first_seen_at = payload.get("first_seen_at", "")
    if not isinstance(first_seen_at, str):
        raise ValueError(f"snapshot {key!r} field 'first_seen_at' must be a string")

    return TrackedClientSnapshot(
        **parsed_strings,
        connected_duration_seconds=duration,
        away=away,
        first_seen_at=first_seen_at,
    )


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temp_path.write_text(text, encoding="utf-8")
        os.replace(temp_path, path)
    finally:
        with contextlib.suppress(OSError):
            if temp_path.exists():
                temp_path.unlink()


class SnapshotStore:
    def __init__(self, data_file: Path) -> None:
        self._data_file = data_file
        self._last_saved_text: str | None = None

    def load(self) -> dict[str, TrackedClientSnapshot]:
        if not self._data_file.exists():
            self._last_saved_text = None
            return {}

        raw_text = self._data_file.read_text(encoding="utf-8")
        raw = json.loads(raw_text)
        if not isinstance(raw, dict):
            raise ValueError("snapshot file root node must be an object")

        snapshots: dict[str, TrackedClientSnapshot] = {}
        for key, value in raw.items():
            if not isinstance(key, str):
                raise ValueError("snapshot keys must be strings")
            snapshots[key] = _parse_snapshot(key, value)

        self._last_saved_text = raw_text
        return snapshots

    def save(self, snapshots: dict[str, TrackedClientSnapshot]) -> bool:
        text = self._serialize(snapshots)
        if text == self._last_saved_text:
            return False
        _atomic_write_text(self._data_file, text)
        self._last_saved_text = text
        return True

    def _serialize(self, snapshots: dict[str, TrackedClientSnapshot]) -> str:
        payload = {
            key: asdict(snapshot)
            for key, snapshot in sorted(snapshots.items(), key=lambda item: item[0])
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


class GroupNotifyStore:
    def __init__(self, data_file: Path) -> None:
        self._data_file = data_file
        self._last_saved_text: str | None = None

    def load(self) -> dict[str, bool]:
        if not self._data_file.exists():
            self._last_saved_text = None
            return {}

        raw_text = self._data_file.read_text(encoding="utf-8")
        raw = json.loads(raw_text)
        if not isinstance(raw, dict):
            raise ValueError("group notify file root node must be an object")

        groups: dict[str, bool] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or type(value) is not bool:
                raise ValueError(
                    "group notify entries must map string keys to booleans"
                )
            groups[key] = value

        self._last_saved_text = raw_text
        return groups

    def save(self, groups: dict[str, bool]) -> bool:
        text = self._serialize(groups)
        if text == self._last_saved_text:
            return False
        _atomic_write_text(self._data_file, text)
        self._last_saved_text = text
        return True

    def _serialize(self, groups: dict[str, bool]) -> str:
        payload = {
            key: value
            for key, value in sorted(groups.items(), key=lambda item: item[0])
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
