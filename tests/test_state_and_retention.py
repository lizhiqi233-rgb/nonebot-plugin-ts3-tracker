import asyncio
import json
from datetime import datetime
from pathlib import Path

from nonebot_plugin_ts3_tracker.config import Ts3TrackerSettings
from nonebot_plugin_ts3_tracker.query import Ts3QueryClient
from nonebot_plugin_ts3_tracker.recording.retention import (
    RetentionProtectionSnapshot,
    run_retention_cleanup,
)
from nonebot_plugin_ts3_tracker.runtime import Ts3TrackerRuntime
from nonebot_plugin_ts3_tracker.service import Ts3TrackerService
from nonebot_plugin_ts3_tracker.storage import SnapshotStore
from nonebot_plugin_ts3_tracker.storage_paths import get_plugin_data_root


def test_snapshot_rejects_invalid_field_type_and_accepts_legacy_default(
    tmp_path: Path,
) -> None:
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text(
        json.dumps(
            {
                "uid:x": {
                    "nickname": 42,
                    "unique_id": "x",
                    "channel_id": "1",
                    "channel_name": "Lobby",
                    "connected_duration_seconds": 1,
                    "away": False,
                }
            }
        ),
        encoding="utf-8",
    )

    try:
        SnapshotStore(invalid_path).load()
    except ValueError as exc:
        assert "nickname" in str(exc)
    else:
        raise AssertionError("invalid snapshot was accepted")

    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_text(
        json.dumps(
            {
                "uid:y": {
                    "nickname": "Alice",
                    "unique_id": "y",
                    "channel_id": "1",
                    "channel_name": "Lobby",
                    "connected_duration_seconds": 1,
                    "away": False,
                }
            }
        ),
        encoding="utf-8",
    )

    assert SnapshotStore(legacy_path).load()["uid:y"].first_seen_at == ""


def test_retention_rejects_overlapping_roots(tmp_path: Path) -> None:
    shared_root = tmp_path / "shared"
    recording = shared_root / "2026-07-15" / "1_Lobby" / "recording.wav"
    recording.parent.mkdir(parents=True)
    recording.write_bytes(b"audio")

    result = run_retention_cleanup(
        recordings_dir=shared_root,
        slices_dir=shared_root,
        recording_retention_days=30,
        slice_retention_days=7,
        protection=RetentionProtectionSnapshot(),
        now=datetime(2026, 7, 31, 12, 0, 0),
    )

    assert recording.exists()
    assert result.errors


def test_retention_deletes_only_managed_files(tmp_path: Path) -> None:
    recordings_root = tmp_path / "recordings"
    slices_root = tmp_path / "slices"
    channel_root = recordings_root / "2026-07-15" / "1_Lobby"
    channel_root.mkdir(parents=True)
    recording = channel_root / "recording.wav"
    note = channel_root / "note.txt"
    recording.write_bytes(b"audio")
    note.write_text("keep", encoding="utf-8")

    result = run_retention_cleanup(
        recordings_dir=recordings_root,
        slices_dir=slices_root,
        recording_retention_days=7,
        slice_retention_days=7,
        protection=RetentionProtectionSnapshot(),
        now=datetime(2026, 7, 31, 12, 0, 0),
    )

    assert not recording.exists()
    assert note.exists()
    assert result.unmanaged_skipped == 1


class _NotifySettings:
    data_dir = "~"

    def get_notify_groups(self) -> list[str]:
        return ["123"]

    def filter_groups_by_whitelist(self, groups: list[str]) -> list[str]:
        return groups


class _NotifyStore:
    def save(self, _groups: dict[str, bool]) -> bool:
        return True


def test_runtime_uses_one_data_root_and_reports_effective_notify_change() -> None:
    runtime = object.__new__(Ts3TrackerRuntime)
    runtime.settings = _NotifySettings()
    runtime._group_notify_overrides = {}
    runtime._group_notify_lock = asyncio.Lock()
    runtime._group_store = _NotifyStore()

    assert runtime._build_snapshot_file().parent == get_plugin_data_root(
        runtime.settings
    )
    assert runtime.get_effective_notify_groups() == ["123"]
    changed = asyncio.run(runtime.set_group_notify_enabled("123", True))
    assert changed is False
    assert runtime.get_effective_notify_groups() == ["123"]


def test_debug_setting_reaches_query_client() -> None:
    settings = Ts3TrackerSettings(
        server_host="localhost",
        serverquery_username="user",
        serverquery_password="password",
        debug=True,
    )
    service = Ts3TrackerService(settings)

    client = service._get_client()

    assert isinstance(client, Ts3QueryClient)
    assert client.debug is True
