from __future__ import annotations

from typing import Literal

from nonebot.compat import BaseModel, field_validator


class Ts3TrackerSettings(BaseModel):
    server_host: str = ""
    server_port: int = 9987
    serverquery_port: int = 10011
    serverquery_username: str = ""
    serverquery_password: str = ""
    debug: bool = False
    command_prefix_required: bool = True
    query_timeout_seconds: float = 10.0
    notification_enabled: bool = False
    notify_target_groups: str = ""
    notify_target_users: str = ""
    group_whitelist_enabled: bool = False
    group_whitelist_groups: str = ""
    poll_interval_seconds: int = 5
    startup_silent: bool = True
    data_dir: str = ""
    # full：进退服均通知；join_only：仅进服通知（换频道不产生事件，因用户唯一键不变）
    notification_push_mode: Literal["full", "join_only"] = "full"
    recording_enabled: bool = False
    recording_channels: str = ""
    recording_identities: str = ""
    recording_output_dir: str = ""
    recording_sidecar_path: str = ""
    recording_server_password: str = ""
    recording_channel_password: str = ""
    recording_nickname_prefix: str = "RecBot"
    recording_min_session_seconds: int = 5
    recording_min_human_count: int = 2
    recording_stop_grace_seconds: int = 300
    recording_slice_default_minutes: int = 5
    # 0 表示不自动清理；按目录日期 YYYY-MM-DD 判定过期
    recording_retention_days: int = 7
    recording_slice_retention_days: int = 7
    recording_cleanup_interval_hours: int = 24

    @field_validator(
        "server_host",
        "serverquery_username",
        "serverquery_password",
        "notify_target_groups",
        "notify_target_users",
        "group_whitelist_groups",
        "data_dir",
        "recording_channels",
        "recording_identities",
        "recording_output_dir",
        "recording_sidecar_path",
        "recording_server_password",
        "recording_channel_password",
        "recording_nickname_prefix",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: object) -> str:
        return str(value).strip()

    @field_validator("poll_interval_seconds")
    @classmethod
    def validate_positive_int(cls, value: int) -> int:
        return max(1, value)

    @field_validator("recording_min_session_seconds")
    @classmethod
    def validate_recording_min_session(cls, value: int) -> int:
        return max(0, value)

    @field_validator("recording_min_human_count")
    @classmethod
    def validate_recording_min_human_count(cls, value: int) -> int:
        return max(1, value)

    @field_validator("recording_stop_grace_seconds")
    @classmethod
    def validate_recording_stop_grace(cls, value: int) -> int:
        return max(0, value)

    @field_validator("recording_slice_default_minutes")
    @classmethod
    def validate_recording_slice_default_minutes(cls, value: int) -> int:
        return max(1, value)

    @field_validator("recording_retention_days", "recording_slice_retention_days")
    @classmethod
    def validate_recording_retention_days(cls, value: int) -> int:
        return max(0, value)

    @field_validator("recording_cleanup_interval_hours")
    @classmethod
    def validate_recording_cleanup_interval_hours(cls, value: int) -> int:
        return max(1, value)

    @field_validator("query_timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: float) -> float:
        return max(1.0, value)

    def parse_targets(self, raw: str) -> list[str]:
        normalized = raw.replace("\r", "\n").replace(";", "\n").replace(",", "\n")
        targets = [item.strip() for item in normalized.split("\n")]
        return [item for item in targets if item]

    def is_group_allowed(self, group_id: str | int | None) -> bool:
        if group_id is None or not self.group_whitelist_enabled:
            return True
        return str(group_id) in set(self.parse_targets(self.group_whitelist_groups))

    def get_effective_notify_groups(self) -> list[str]:
        notify_groups = self.get_notify_groups()
        return self.filter_groups_by_whitelist(notify_groups)

    def get_notify_groups(self) -> list[str]:
        return self.parse_targets(self.notify_target_groups)

    def get_recording_channels(self) -> list[str]:
        return self.parse_targets(self.recording_channels)

    def filter_groups_by_whitelist(self, group_ids: list[str]) -> list[str]:
        if not self.group_whitelist_enabled:
            return group_ids
        whitelist = set(self.parse_targets(self.group_whitelist_groups))
        return [group_id for group_id in group_ids if group_id in whitelist]

    def is_recording_bot_nickname(self, nickname: str) -> bool:
        prefix = self.recording_nickname_prefix.casefold()
        if not prefix:
            return False
        return nickname.casefold().startswith(prefix)


class Config(BaseModel):
    ts3_tracker: Ts3TrackerSettings = Ts3TrackerSettings()
