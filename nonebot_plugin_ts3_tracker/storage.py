from __future__ import annotations

import json
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


class SnapshotStore:
    def __init__(self, data_file: Path) -> None:
        self._data_file = data_file

    def load(self) -> dict[str, TrackedClientSnapshot]:
        if not self._data_file.exists():
            return {}

        raw = json.loads(self._data_file.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("snapshot file root node must be an object")

        snapshots: dict[str, TrackedClientSnapshot] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            snapshots[key] = TrackedClientSnapshot(**value)
        return snapshots

    def save(self, snapshots: dict[str, TrackedClientSnapshot]) -> None:
        self._data_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            key: asdict(snapshot)
            for key, snapshot in sorted(snapshots.items(), key=lambda item: item[0])
        }
        self._data_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


class GroupNotifyStore:
    def __init__(self, data_file: Path) -> None:
        self._data_file = data_file

    def load(self) -> dict[str, bool]:
        if not self._data_file.exists():
            return {}

        raw = json.loads(self._data_file.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("group notify file root node must be an object")

        groups: dict[str, bool] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or not isinstance(value, bool):
                continue
            groups[key] = value
        return groups

    def save(self, groups: dict[str, bool]) -> None:
        self._data_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            key: value for key, value in sorted(groups.items(), key=lambda item: item[0])
        }
        self._data_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
