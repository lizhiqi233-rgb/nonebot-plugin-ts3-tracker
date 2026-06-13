from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from nonebot import logger

from ..config import Ts3TrackerSettings
from ..models import Ts3ServerStatus
from .paths import build_session_paths
from .session import ChannelRecordingSession
from .sidecar import SidecarLauncher, resolve_identity_entries, resolve_sidecar_path


class RecordingManager:
    def __init__(self, settings: Ts3TrackerSettings, plugin_dir: Path) -> None:
        self.settings = settings
        self._plugin_dir = plugin_dir
        self._launcher = SidecarLauncher(
            resolve_sidecar_path(settings.recording_sidecar_path, plugin_dir)
        )
        self._identities = resolve_identity_entries(
            settings.recording_identities, plugin_dir
        )
        self._sessions: dict[str, ChannelRecordingSession] = {}
        self._identity_pool: list[str] = []
        self._lock = asyncio.Lock()

    @property
    def is_enabled(self) -> bool:
        return self.settings.recording_enabled

    @property
    def identity_count(self) -> int:
        return len(self._identities)

    def get_active_sessions(self) -> list[ChannelRecordingSession]:
        return list(self._sessions.values())

    async def sync(self, status: Ts3ServerStatus, *, now: datetime | None = None) -> None:
        if not self.is_enabled:
            return

        missing = self._missing_recording_config()
        if missing:
            logger.warning(
                "TS3 recording skipped, incomplete config: {}",
                "、".join(missing),
            )
            return

        if not self._launcher.is_available:
            logger.warning(
                "TS3 recording enabled but sidecar binary is missing: build recorder_sidecar first"
            )
            return

        current_time = now or datetime.now()
        monitored = self._resolve_monitored_channels(status)
        active_targets = {
            channel_id
            for channel_id, channel_name in monitored.items()
            if self._count_human_users(status, channel_id) > 0
        }

        async with self._lock:
            self._identity_pool = list(self._identities)

            for channel_id in list(self._sessions.keys()):
                if channel_id not in active_targets:
                    await self._stop_session(channel_id, ended_at=current_time)

            for channel_id in sorted(active_targets):
                if channel_id in self._sessions:
                    session = self._sessions[channel_id]
                    session.participant_names = self._participant_names(
                        status, channel_id
                    )
                    continue
                channel_name = monitored.get(channel_id, "unnamed")
                await self._start_session(
                    channel_id=channel_id,
                    channel_name=channel_name,
                    status=status,
                    started_at=current_time,
                )

    async def shutdown(self) -> None:
        async with self._lock:
            ended_at = datetime.now()
            for channel_id in list(self._sessions.keys()):
                await self._stop_session(channel_id, ended_at=ended_at)

    def _missing_recording_config(self) -> list[str]:
        missing: list[str] = []
        if not self.settings.server_host:
            missing.append("服务器地址")
        if self.settings.server_port <= 0:
            missing.append("服务器端口")
        if not self.settings.get_recording_channels():
            missing.append("recording_channels")
        if not self._identities:
            missing.append("recording_identities")
        return missing

    def _resolve_monitored_channels(self, status: Ts3ServerStatus) -> dict[str, str]:
        configured = self.settings.get_recording_channels()
        if not configured:
            return {}

        channels_by_id = {channel_id: name for channel_id, name in status.channels}
        channels_by_name = {
            name.casefold(): channel_id for channel_id, name in status.channels
        }

        resolved: dict[str, str] = {}
        for item in configured:
            if item.isdigit() or item.lstrip("-").isdigit():
                channel_id = item
                resolved[channel_id] = channels_by_id.get(channel_id, item)
                continue
            matched_id = channels_by_name.get(item.casefold())
            if matched_id is not None:
                resolved[matched_id] = channels_by_id.get(matched_id, item)
            else:
                logger.warning("TS3 recording channel not found on server: {}", item)
        return resolved

    def _count_human_users(self, status: Ts3ServerStatus, channel_id: str) -> int:
        prefix = self.settings.recording_nickname_prefix.casefold()
        count = 0
        for user in status.users:
            if user.channel_id != channel_id:
                continue
            if prefix and user.nickname.casefold().startswith(prefix):
                continue
            count += 1
        return count

    def _participant_names(self, status: Ts3ServerStatus, channel_id: str) -> set[str]:
        prefix = self.settings.recording_nickname_prefix.casefold()
        names: set[str] = set()
        for user in status.users:
            if user.channel_id != channel_id:
                continue
            if prefix and user.nickname.casefold().startswith(prefix):
                continue
            if user.nickname:
                names.add(user.nickname)
        return names

    def _take_identity(self) -> str | None:
        if not self._identity_pool:
            logger.warning("TS3 recording identity pool exhausted")
            return None
        return self._identity_pool.pop(0)

    async def _start_session(
        self,
        *,
        channel_id: str,
        channel_name: str,
        status: Ts3ServerStatus,
        started_at: datetime,
    ) -> None:
        identity = self._take_identity()
        if identity is None:
            return

        output_dir = self._recording_output_dir()
        wav_path, metadata_path = build_session_paths(
            output_dir, channel_id, channel_name, started_at
        )
        nickname = f"{self.settings.recording_nickname_prefix}-{channel_name}"[:32]

        session = ChannelRecordingSession(
            channel_id=channel_id,
            channel_name=channel_name,
            identity=identity,
            wav_path=wav_path,
            metadata_path=metadata_path,
            started_at=started_at,
            nickname=nickname,
            participant_names=self._participant_names(status, channel_id),
        )

        try:
            await self._launcher.start(
                session,
                host=self.settings.server_host,
                port=self.settings.server_port,
                server_password=self.settings.recording_server_password,
                channel_password=self.settings.recording_channel_password,
            )
        except Exception as exc:
            logger.error(
                "failed to start TS3 recorder for channel {} ({}): {}",
                channel_id,
                channel_name,
                exc,
            )
            self._identity_pool.insert(0, identity)
            return

        self._sessions[channel_id] = session
        logger.info(
            "TS3 recording started for channel {} ({}) -> {}",
            channel_id,
            channel_name,
            wav_path,
        )

    async def _stop_session(self, channel_id: str, *, ended_at: datetime) -> None:
        session = self._sessions.pop(channel_id, None)
        if session is None:
            return

        await session.terminate()
        duration = (ended_at - session.started_at).total_seconds()
        if duration >= self.settings.recording_min_session_seconds:
            session.write_metadata(ended_at=ended_at)
            logger.info(
                "TS3 recording saved for channel {} ({}), duration {:.0f}s -> {}",
                session.channel_id,
                session.channel_name,
                duration,
                session.wav_path,
            )
        elif session.wav_path.exists():
            session.wav_path.unlink(missing_ok=True)
            session.metadata_path.unlink(missing_ok=True)
            logger.info(
                "TS3 recording discarded for channel {} ({}), duration {:.0f}s below minimum",
                session.channel_id,
                session.channel_name,
                duration,
            )

        self._identity_pool.append(session.identity)

    def _recording_output_dir(self) -> Path:
        if self.settings.recording_output_dir:
            return Path(self.settings.recording_output_dir).expanduser()
        return self._plugin_dir / "recordings"
