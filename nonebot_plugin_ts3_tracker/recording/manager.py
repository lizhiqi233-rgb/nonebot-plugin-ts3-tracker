from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from nonebot import logger

from ..config import Ts3TrackerSettings
from ..models import Ts3ServerStatus
from ..storage_paths import resolve_identities_dir, resolve_recordings_dir, resolve_slices_dir
from .paths import build_session_paths, build_slice_paths
from .session import ChannelRecordingSession
from .sidecar import SidecarLauncher, resolve_identity_entries, resolve_sidecar_path
from .slice import (
    SliceError,
    SliceResult,
    session_matches_channel_filter,
    slice_wav_tail,
    write_slice_metadata,
)


class RecordingManager:
    def __init__(self, settings: Ts3TrackerSettings, plugin_dir: Path) -> None:
        self.settings = settings
        self._plugin_dir = plugin_dir
        self._identities_dir = resolve_identities_dir()
        self._launcher = SidecarLauncher(
            resolve_sidecar_path(settings.recording_sidecar_path, plugin_dir)
        )
        self._identities = resolve_identity_entries(
            settings.recording_identities, self._identities_dir
        )
        self._sessions: dict[str, ChannelRecordingSession] = {}
        self._test_sessions: set[str] = set()
        self._identity_pool: list[str] = []
        self._pending_stops: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    @property
    def is_enabled(self) -> bool:
        return self.settings.recording_enabled

    @property
    def identity_count(self) -> int:
        return len(self._identities)

    def get_active_sessions(self) -> list[ChannelRecordingSession]:
        return list(self._sessions.values())

    def is_test_session(self, channel_id: str) -> bool:
        return channel_id in self._test_sessions

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
        min_humans = self.settings.recording_min_human_count

        async with self._lock:
            self._refresh_identity_pool()

            for channel_id in list(self._sessions.keys()):
                if channel_id not in monitored:
                    self._pending_stops.pop(channel_id, None)
                    await self._stop_session(channel_id, ended_at=current_time)
                    continue

                human_count = self._count_human_users(status, channel_id)
                session = self._sessions[channel_id]
                session.participant_names = self._participant_names(
                    status, channel_id
                )

                if channel_id in self._test_sessions:
                    continue

                if human_count >= min_humans:
                    if channel_id in self._pending_stops:
                        logger.info(
                            "TS3 recording grace cancelled for channel {} ({}), "
                            "{} human user(s) present",
                            channel_id,
                            session.channel_name,
                            human_count,
                        )
                        self._pending_stops.pop(channel_id, None)
                    continue

                grace_until = self._pending_stops.get(channel_id)
                if grace_until is None:
                    grace_seconds = self.settings.recording_stop_grace_seconds
                    grace_until = current_time + timedelta(seconds=grace_seconds)
                    self._pending_stops[channel_id] = grace_until
                    logger.info(
                        "TS3 recording grace started for channel {} ({}), "
                        "{} human user(s), stop scheduled at {}",
                        channel_id,
                        session.channel_name,
                        human_count,
                        grace_until.strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    continue

                if current_time >= grace_until:
                    self._pending_stops.pop(channel_id, None)
                    await self._stop_session(channel_id, ended_at=current_time)

            for channel_id in sorted(monitored.keys()):
                if channel_id in self._sessions:
                    continue
                self._pending_stops.pop(channel_id, None)
                if self._count_human_users(status, channel_id) < min_humans:
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
            self._pending_stops.clear()
            ended_at = datetime.now()
            for channel_id in list(self._sessions.keys()):
                await self._stop_session(channel_id, ended_at=ended_at)

    async def slice_active_sessions(
        self,
        *,
        duration_minutes: int,
        channel_filter: str | None = None,
        triggered_at: datetime | None = None,
    ) -> tuple[list[SliceResult], list[str]]:
        if not self.is_enabled:
            return [], ["当前未开启 TS3 频道录音。"]

        current_time = triggered_at or datetime.now()
        duration_seconds = duration_minutes * 60
        slices_dir = resolve_slices_dir(self.settings)

        async with self._lock:
            sessions = list(self._sessions.values())

        if not sessions:
            return [], ["当前没有进行中的录音可切片。"]

        if channel_filter is not None:
            sessions = [
                session
                for session in sessions
                if session_matches_channel_filter(
                    channel_id=session.channel_id,
                    channel_name=session.channel_name,
                    channel_filter=channel_filter,
                )
            ]
            if not sessions:
                active = ", ".join(
                    f"{item.channel_name}({item.channel_id})"
                    for item in self._sessions.values()
                )
                return [], [
                    f"未找到匹配的进行中录音频道：{channel_filter}。当前录音：{active}"
                ]

        results: list[SliceResult] = []
        errors: list[str] = []
        for session in sessions:
            wav_path, metadata_path = build_slice_paths(
                slices_dir,
                session.channel_id,
                session.channel_name,
                current_time,
                duration_minutes,
            )
            try:
                actual_seconds = await asyncio.to_thread(
                    slice_wav_tail,
                    session.wav_path,
                    wav_path,
                    duration_seconds=duration_seconds,
                )
            except SliceError as exc:
                errors.append(
                    f"{session.channel_name} ({session.channel_id})：{exc}"
                )
                continue

            result = SliceResult(
                channel_id=session.channel_id,
                channel_name=session.channel_name,
                source_path=session.wav_path,
                output_path=wav_path,
                metadata_path=metadata_path,
                requested_seconds=duration_seconds,
                actual_seconds=actual_seconds,
                participant_names=set(session.participant_names),
            )
            await asyncio.to_thread(
                write_slice_metadata,
                result,
                triggered_at=current_time,
            )
            results.append(result)
            logger.info(
                "TS3 recording slice saved for channel {} ({}), {:.0f}s -> {}",
                session.channel_id,
                session.channel_name,
                actual_seconds,
                wav_path,
            )

        return results, errors

    async def force_start_sessions(
        self,
        status: Ts3ServerStatus,
        *,
        channel_filter: str | None = None,
        started_at: datetime | None = None,
    ) -> tuple[list[ChannelRecordingSession], list[str]]:
        if not self.is_enabled:
            return [], ["当前未开启 TS3 频道录音。"]

        missing = self._missing_recording_config()
        if missing:
            return [], ["TS3 录音配置不完整：" + "、".join(missing)]

        if not self._launcher.is_available:
            return [], [
                "未找到 recorder sidecar 二进制，请先编译或配置 TS3_TRACKER__RECORDING_SIDECAR_PATH。"
            ]

        current_time = started_at or datetime.now()
        monitored = self._resolve_monitored_channels(status)
        if not monitored:
            return [], ["未解析到可录制的监控频道，请检查 TS3_TRACKER__RECORDING_CHANNELS。"]

        targets = dict(monitored)
        if channel_filter is not None:
            targets = {
                channel_id: channel_name
                for channel_id, channel_name in monitored.items()
                if session_matches_channel_filter(
                    channel_id=channel_id,
                    channel_name=channel_name,
                    channel_filter=channel_filter,
                )
            }
            if not targets:
                configured = ", ".join(
                    f"{name}({channel_id})" for channel_id, name in monitored.items()
                )
                return [], [
                    f"未找到匹配的监控频道：{channel_filter}。可录制频道：{configured}"
                ]

        started: list[ChannelRecordingSession] = []
        messages: list[str] = []

        async with self._lock:
            self._refresh_identity_pool()
            for channel_id in sorted(targets.keys()):
                channel_name = targets[channel_id]
                if channel_id in self._sessions:
                    messages.append(f"{channel_name} ({channel_id}) 已在录音中。")
                    continue

                self._pending_stops.pop(channel_id, None)
                self._test_sessions.add(channel_id)
                await self._start_session(
                    channel_id=channel_id,
                    channel_name=channel_name,
                    status=status,
                    started_at=current_time,
                )
                session = self._sessions.get(channel_id)
                if session is None:
                    self._test_sessions.discard(channel_id)
                    messages.append(
                        f"{channel_name} ({channel_id}) 启动失败，请查看 NoneBot 日志。"
                    )
                    continue

                started.append(session)
                logger.info(
                    "TS3 manual test recording started for channel {} ({}) -> {}",
                    channel_id,
                    channel_name,
                    session.wav_path,
                )

        return started, messages

    async def stop_active_sessions(
        self,
        *,
        channel_filter: str | None = None,
        ended_at: datetime | None = None,
    ) -> tuple[list[tuple[ChannelRecordingSession, bool]], list[str]]:
        if not self.is_enabled:
            return [], ["当前未开启 TS3 频道录音。"]

        current_time = ended_at or datetime.now()

        async with self._lock:
            sessions = list(self._sessions.values())
            if not sessions:
                return [], ["当前没有进行中的录音。"]

            if channel_filter is not None:
                sessions = [
                    session
                    for session in sessions
                    if session_matches_channel_filter(
                        channel_id=session.channel_id,
                        channel_name=session.channel_name,
                        channel_filter=channel_filter,
                    )
                ]
                if not sessions:
                    active = ", ".join(
                        f"{item.channel_name}({item.channel_id})"
                        for item in self._sessions.values()
                    )
                    return [], [
                        f"未找到匹配的进行中录音频道：{channel_filter}。当前录音：{active}"
                    ]

            stopped: list[tuple[ChannelRecordingSession, bool]] = []
            for session in sessions:
                if session.channel_id not in self._sessions:
                    continue
                was_test = session.channel_id in self._test_sessions
                self._pending_stops.pop(session.channel_id, None)
                stopped.append((session, was_test))
                await self._stop_session(session.channel_id, ended_at=current_time)
                logger.info(
                    "TS3 recording stopped manually for channel {} ({})",
                    session.channel_id,
                    session.channel_name,
                )

        return stopped, []

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
        count = 0
        for user in status.users:
            if user.channel_id != channel_id:
                continue
            if self.settings.is_recording_bot_nickname(user.nickname):
                continue
            count += 1
        return count

    def _participant_names(self, status: Ts3ServerStatus, channel_id: str) -> set[str]:
        names: set[str] = set()
        for user in status.users:
            if user.channel_id != channel_id:
                continue
            if self.settings.is_recording_bot_nickname(user.nickname):
                continue
            if user.nickname:
                names.add(user.nickname)
        return names

    def _refresh_identity_pool(self) -> None:
        in_use = {session.identity for session in self._sessions.values()}
        self._identity_pool = [
            identity for identity in self._identities if identity not in in_use
        ]

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
        self._test_sessions.discard(channel_id)
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
        return resolve_recordings_dir(self.settings)
