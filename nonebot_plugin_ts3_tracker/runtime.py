from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import nonebot
from nonebot import logger, require
from nonebot.adapters.onebot.v11 import Bot

require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as store

from .config import Ts3TrackerSettings
from .storage_paths import (
    ensure_storage_layout,
    resolve_identities_dir,
    resolve_recordings_dir,
    resolve_slices_dir,
)
from .models import Ts3OnlineUser, Ts3ServerStatus
from .query import Ts3QueryError
from .recording import RecordingManager
from .recording.retention import RetentionTarget
from .service import Ts3TrackerService
from .storage import GroupNotifyStore, SnapshotStore, TrackedClientSnapshot

MessageSender = Callable[[str, str, str], Awaitable[bool]]
NowFactory = Callable[[], datetime]


@dataclass(slots=True)
class NotificationDiff:
    joined: list[TrackedClientSnapshot]
    left: list[TrackedClientSnapshot]


class Ts3TrackerRuntime:
    def __init__(
        self,
        settings: Ts3TrackerSettings,
        service: Ts3TrackerService,
        *,
        store_backend: SnapshotStore | None = None,
        message_sender: MessageSender | None = None,
        now_factory: NowFactory | None = None,
    ) -> None:
        self.settings = settings
        self.service = service
        self._store = store_backend or SnapshotStore(self._build_snapshot_file())
        self._group_store = GroupNotifyStore(self._build_group_notify_file())
        self._message_sender = message_sender or self._send_message
        self._now_factory = now_factory or datetime.now
        self._snapshot: dict[str, TrackedClientSnapshot] = {}
        self._group_notify_overrides: dict[str, bool] = {}
        self._snapshot_lock = asyncio.Lock()
        self._group_notify_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._poll_task: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._recording_manager = RecordingManager(
            settings, Path(__file__).resolve().parent
        )
        self.service._duration_provider = self.get_online_duration_seconds

    async def startup(self) -> None:
        self._stop_event.clear()
        ensure_storage_layout(self.settings)
        try:
            self._snapshot = self._store.load()
        except Exception as exc:
            logger.error("failed to load ts3 snapshot store: {}", exc)
            self._snapshot = {}

        try:
            self._group_notify_overrides = self._group_store.load()
        except Exception as exc:
            logger.error("failed to load ts3 group notify store: {}", exc)
            self._group_notify_overrides = {}

        if not self.settings.notification_enabled:
            logger.info("TS3 通知轮询已关闭。")
        else:
            mode_text = (
                "仅进服"
                if self.settings.notification_push_mode == "join_only"
                else "进退服"
            )
            logger.info(
                "TS3 通知轮询已启动，推送模式：{}，轮询间隔 {} 秒，通知群：{}，通知私聊：{}，群白名单模式：{}。",
                mode_text,
                self.settings.poll_interval_seconds,
                ",".join(self.get_effective_notify_groups()) or "-",
                self.settings.notify_target_users or "-",
                "开启" if self.settings.group_whitelist_enabled else "关闭",
            )

        if self.settings.recording_enabled:
            logger.info(
                "TS3 频道录音已开启，目标频道：{}，identity 数量：{}，录音目录：{}，identity 目录：{}。",
                ",".join(self.settings.get_recording_channels()) or "-",
                self._recording_manager.identity_count,
                resolve_recordings_dir(self.settings),
                resolve_identities_dir(),
            )
        else:
            logger.info("TS3 频道录音已关闭。")

        if self._is_retention_cleanup_enabled():
            logger.info(
                "TS3 录音文件定时清理已开启，完整录音保留 {} 天，切片保留 {} 天，间隔 {} 小时。",
                self.settings.recording_retention_days or "不清理",
                self.settings.recording_slice_retention_days or "不清理",
                self.settings.recording_cleanup_interval_hours,
            )
            await self.run_retention_cleanup_once()
            self._ensure_cleanup_task()
        else:
            logger.info("TS3 录音文件定时清理已关闭（retention_days 均为 0）。")

        if self.settings.notification_enabled or self.settings.recording_enabled:
            await self.sync_once(notify=not self.settings.startup_silent)
            self._ensure_poll_task()

    async def shutdown(self) -> None:
        self._stop_event.set()
        await self._recording_manager.shutdown()
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    async def sync_once(self, *, notify: bool) -> NotificationDiff:
        missing_fields = self.service.get_missing_required_fields()
        if missing_fields:
            if self.settings.notification_enabled or self.settings.recording_enabled:
                logger.warning(
                    "TS3 轮询跳过，配置不完整：{}",
                    "、".join(missing_fields),
                )
            return NotificationDiff(joined=[], left=[])

        try:
            status = await self.service.fetch_status()
        except Ts3QueryError as exc:
            logger.warning("TS3 轮询失败：{}", exc)
            return NotificationDiff(joined=[], left=[])
        except Exception as exc:  # pragma: no cover
            logger.exception("TS3 轮询发生未预期错误：{}", exc)
            return NotificationDiff(joined=[], left=[])

        current = self._build_snapshot(status)
        async with self._snapshot_lock:
            diff = self._calculate_diff(self._snapshot, current)
            self._snapshot = current
            try:
                self._store.save(self._snapshot)
            except Exception as exc:
                logger.error("保存 TS3 快照失败：{}", exc)

        if notify:
            await self._dispatch_notifications(status, diff)
        elif diff.joined or diff.left:
            logger.info(
                "TS3 首次同步完成，不发送通知。进入：{}，离开：{}。",
                "、".join(item.nickname for item in diff.joined) or "无",
                "、".join(item.nickname for item in diff.left) or "无",
            )

        if self.settings.recording_enabled:
            await self._recording_manager.sync(status, now=self._now_factory())

        return diff

    def _ensure_poll_task(self) -> None:
        if self._poll_task is not None and not self._poll_task.done():
            return
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.sync_once(notify=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover
                logger.error("TS3 轮询循环异常：{}", exc)

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.settings.poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue

    def _is_retention_cleanup_enabled(self) -> bool:
        return (
            self.settings.recording_retention_days > 0
            or self.settings.recording_slice_retention_days > 0
        )

    def _ensure_cleanup_task(self) -> None:
        if self._cleanup_task is not None and not self._cleanup_task.done():
            return
        self._cleanup_task = asyncio.create_task(self._retention_cleanup_loop())

    async def _retention_cleanup_loop(self) -> None:
        interval_seconds = self.settings.recording_cleanup_interval_hours * 3600
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=interval_seconds,
                )
            except asyncio.TimeoutError:
                pass
            else:
                break

            if self._stop_event.is_set():
                break

            try:
                await self.run_retention_cleanup_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover
                logger.error("TS3 录音文件定时清理异常：{}", exc)

    async def run_retention_cleanup_once(self) -> None:
        from .recording.retention import format_cleanup_result

        result = await self._recording_manager.run_retention_cleanup(
            now=self._now_factory()
        )
        if not result.has_changes:
            return

        message = format_cleanup_result(result)
        if result.total_deleted_files > 0:
            logger.info(
                "TS3 录音文件定时清理完成，删除 {} 个文件。",
                result.total_deleted_files,
            )
        if result.errors:
            logger.warning("{}", message)

    async def cleanup_recordings_manual(
        self,
        *,
        target: RetentionTarget,
    ) -> str:
        from .recording.retention import format_cleanup_result

        if target == RetentionTarget.RECORDINGS and self.settings.recording_retention_days <= 0:
            return (
                "未配置完整录音保留天数，请设置 "
                "TS3_TRACKER__RECORDING_RETENTION_DAYS>0。"
            )
        if target == RetentionTarget.SLICES and self.settings.recording_slice_retention_days <= 0:
            return (
                "未配置切片保留天数，请设置 "
                "TS3_TRACKER__RECORDING_SLICE_RETENTION_DAYS>0。"
            )
        if target == RetentionTarget.ALL and not self._is_retention_cleanup_enabled():
            return (
                "未配置录音文件保留策略，请设置 "
                "TS3_TRACKER__RECORDING_RETENTION_DAYS 或 "
                "TS3_TRACKER__RECORDING_SLICE_RETENTION_DAYS。"
            )

        result = await self._recording_manager.run_retention_cleanup(
            now=self._now_factory(),
            target=target,
        )
        return format_cleanup_result(result)

    def _build_snapshot(
        self, status: Ts3ServerStatus
    ) -> dict[str, TrackedClientSnapshot]:
        snapshots: dict[str, TrackedClientSnapshot] = {}
        now_text = self._format_now()
        for user in status.users:
            if self.settings.is_recording_bot_nickname(user.nickname):
                continue
            key = self._user_key(user)
            previous = self._snapshot.get(key)
            snapshots[key] = TrackedClientSnapshot(
                nickname=user.nickname,
                unique_id=user.unique_id,
                channel_id=user.channel_id,
                channel_name=user.channel_name,
                connected_duration_seconds=user.connected_duration_seconds,
                away=user.away,
                first_seen_at=(
                    previous.first_seen_at if previous and previous.first_seen_at else now_text
                ),
            )
        return snapshots

    def _calculate_diff(
        self,
        previous: dict[str, TrackedClientSnapshot],
        current: dict[str, TrackedClientSnapshot],
    ) -> NotificationDiff:
        joined = [current[key] for key in current.keys() - previous.keys()]
        left = [previous[key] for key in previous.keys() - current.keys()]
        joined.sort(key=lambda item: item.nickname.casefold())
        left.sort(key=lambda item: item.nickname.casefold())
        return NotificationDiff(joined=joined, left=left)

    async def _dispatch_notifications(
        self, status: Ts3ServerStatus, diff: NotificationDiff
    ) -> None:
        messages: list[str] = []
        if diff.joined:
            logger.info(
                "{} 进入了服务器。",
                "、".join(item.nickname for item in diff.joined),
            )
            messages.append(self._format_join_message(status, diff.joined))
        if diff.left and self.settings.notification_push_mode == "full":
            logger.info(
                "{} 退出了服务器。",
                "、".join(item.nickname for item in diff.left),
            )
            messages.append(self._format_leave_message(status, diff.left))
        if not messages:
            return

        targets = [
            ("group", target)
            for target in self.get_effective_notify_groups()
        ]
        targets.extend(
            ("private", target)
            for target in self.settings.parse_targets(self.settings.notify_target_users)
        )
        if not targets:
            logger.warning("检测到 TS3 变化，但没有可用的通知目标。")
            return

        for message in messages:
            for target_type, target in targets:
                ok = await self._message_sender(target_type, target, message)
                if not ok:
                    logger.warning(
                        "发送 TS3 通知失败，目标类型：{}，目标：{}。",
                        target_type,
                        target,
                    )

    def _format_join_message(
        self, status: Ts3ServerStatus, snapshots: list[TrackedClientSnapshot]
    ) -> str:
        lines = [
            f"{snapshot.nickname} 进入了 TS 服务器" for snapshot in snapshots
        ]
        lines.append(f"在线列表：{self._format_online_list(status)}")
        return "\n".join(lines)

    def _format_leave_message(
        self, status: Ts3ServerStatus, snapshots: list[TrackedClientSnapshot]
    ) -> str:
        lines = [
            "📤 用户下线通知",
        ]
        for snapshot in snapshots:
            duration_text = self._format_online_duration(snapshot)
            lines.append(f"🧾 昵称：{snapshot.nickname}")
            lines.append(f"🟢 上线时间：{snapshot.first_seen_at or self._format_now()}")
            lines.append(f"🔴 下线时间：{self._format_now()}")
            lines.append(f"⏱️ 在线时长：{duration_text}")
        lines.append(f"👥 当前在线人数：{self._count_broadcast_users(status)}")
        lines.append(f"📜 在线列表：{self._format_online_list(status)}")
        return "\n".join(lines)

    async def _send_message(self, target_type: str, target: str, message: str) -> bool:
        bot = self._select_bot()
        if bot is None:
            logger.warning("没有可用的 OneBot V11 机器人，无法发送 TS3 主动通知。")
            return False

        try:
            normalized_target = int(target) if target.isdigit() else target
            if target_type == "group":
                await bot.send_group_msg(group_id=normalized_target, message=message)
            else:
                await bot.send_private_msg(user_id=normalized_target, message=message)
            return True
        except Exception as exc:
            logger.error("发送 TS3 主动{}消息失败：{}", target_type, exc)
            return False

    def _select_bot(self) -> Bot | None:
        bots = nonebot.get_bots()
        for bot in bots.values():
            if isinstance(bot, Bot):
                return bot
        return None

    def _build_snapshot_file(self) -> Path:
        if self.settings.data_dir:
            return Path(self.settings.data_dir) / "snapshot.json"
        return store.get_plugin_data_file("snapshot.json")

    def _build_group_notify_file(self) -> Path:
        if self.settings.data_dir:
            return Path(self.settings.data_dir) / "group_notify.json"
        return store.get_plugin_data_file("group_notify.json")

    def get_effective_notify_groups(self) -> list[str]:
        configured_groups = self.settings.get_notify_groups()
        disabled_groups = {
            group_id
            for group_id, enabled in self._group_notify_overrides.items()
            if not enabled
        }
        ordered_groups: list[str] = []
        for group_id in configured_groups:
            if group_id in disabled_groups or group_id in ordered_groups:
                continue
            ordered_groups.append(group_id)
        for group_id, enabled in self._group_notify_overrides.items():
            if not enabled or group_id in ordered_groups:
                continue
            ordered_groups.append(group_id)
        return self.settings.filter_groups_by_whitelist(ordered_groups)

    def is_group_notify_enabled(self, group_id: str | int) -> bool:
        return str(group_id) in set(self.get_effective_notify_groups())

    async def set_group_notify_enabled(
        self, group_id: str | int, enabled: bool
    ) -> bool:
        normalized_group_id = str(group_id)
        async with self._group_notify_lock:
            current = self._group_notify_overrides.get(normalized_group_id)
            self._group_notify_overrides[normalized_group_id] = enabled
            self._group_store.save(self._group_notify_overrides)
        return current != enabled

    def _user_key(self, user: Ts3OnlineUser) -> str:
        if user.unique_id:
            return f"uid:{user.unique_id}"
        if user.database_id:
            return f"db:{user.database_id}"
        if user.client_id:
            return f"clid:{user.client_id}"
        return f"name:{user.nickname}"

    def _format_now(self) -> str:
        return self._now_factory().strftime("%Y-%m-%d %H:%M:%S")

    def _format_duration(self, seconds: int) -> str:
        total = max(0, seconds)
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}小时{minutes}分{secs}秒"
        if minutes:
            return f"{minutes}分{secs}秒"
        return f"{secs}秒"

    def _format_online_duration(self, snapshot: TrackedClientSnapshot) -> str:
        if snapshot.first_seen_at:
            try:
                started_at = datetime.strptime(snapshot.first_seen_at, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                started_at = None
            else:
                seconds = int((self._now_factory() - started_at).total_seconds())
                return self._format_duration(seconds)
        return self._format_duration(snapshot.connected_duration_seconds)

    def _format_online_list(self, status: Ts3ServerStatus) -> str:
        users = [
            user
            for user in status.users
            if not self.settings.is_recording_bot_nickname(user.nickname)
        ]
        if not users:
            return "暂无在线用户"
        return ", ".join(user.nickname for user in users)

    def _count_broadcast_users(self, status: Ts3ServerStatus) -> int:
        return sum(
            1
            for user in status.users
            if not self.settings.is_recording_bot_nickname(user.nickname)
        )

    def build_recording_status_message(self) -> str:
        channels = self.settings.get_recording_channels()
        sessions = self._recording_manager.get_active_sessions()
        lines = [
            "TS3 频道录音状态",
            f"监控频道：{', '.join(channels) if channels else '-'}",
            f"可用 identity：{self._recording_manager.identity_count}",
            f"录音目录：{resolve_recordings_dir(self.settings)}",
            f"切片目录：{resolve_slices_dir(self.settings)}",
            f"identity 目录：{resolve_identities_dir()}",
            f"完整录音保留：{self._format_retention_days(self.settings.recording_retention_days)}",
            f"切片保留：{self._format_retention_days(self.settings.recording_slice_retention_days)}",
            f"定时清理间隔：{self.settings.recording_cleanup_interval_hours} 小时",
        ]
        if not sessions:
            lines.append("当前无进行中的录音会话。")
        else:
            lines.append("进行中的录音：")
            for session in sessions:
                test_tag = (
                    " [测试]"
                    if self._recording_manager.is_test_session(session.channel_id)
                    else ""
                )
                lines.append(
                    f"- {session.channel_name} ({session.channel_id}){test_tag} -> "
                    f"{session.wav_path.name}"
                )
        return "\n".join(lines)

    def _format_retention_days(self, days: int) -> str:
        if days <= 0:
            return "不自动清理"
        return f"{days} 天"

    async def force_start_recordings(self, *, channel: str | None = None) -> str:
        if not self.settings.recording_enabled:
            return "当前未开启 TS3 频道录音，请设置 TS3_TRACKER__RECORDING_ENABLED=true。"

        missing_fields = self.service.get_missing_required_fields()
        if missing_fields:
            return "TS3 配置不完整，请先填写：" + "、".join(missing_fields)

        try:
            status = await self.service.fetch_status()
        except Exception as exc:
            logger.warning("TS3 force record failed to fetch status: {}", exc)
            return f"TS3 查询失败，无法启动测试录音：{exc}"

        started, messages = await self._recording_manager.force_start_sessions(
            status,
            channel_filter=channel,
            started_at=self._now_factory(),
        )
        if not started and messages:
            return "\n".join(messages)

        lines = ["TS3 测试录音已启动（不受最低人数限制，轮询不会自动停录）："]
        for session in started:
            lines.append(
                f"- {session.channel_name} ({session.channel_id}) -> {session.wav_path}"
            )
        lines.extend(messages)
        return "\n".join(lines)

    async def stop_recordings(self, *, channel: str | None = None) -> str:
        if not self.settings.recording_enabled:
            return "当前未开启 TS3 频道录音，请设置 TS3_TRACKER__RECORDING_ENABLED=true。"

        stopped, messages = await self._recording_manager.stop_active_sessions(
            channel_filter=channel,
            ended_at=self._now_factory(),
        )
        if not stopped and messages:
            return "\n".join(messages)

        lines = ["TS3 录音已停止："]
        for session, was_test in stopped:
            test_tag = " [测试]" if was_test else ""
            lines.append(
                f"- {session.channel_name} ({session.channel_id}){test_tag} -> "
                f"{session.wav_path}"
            )
        lines.extend(messages)
        note = (
            "说明：非测试录音若在停录后仍满足最低人数，下一轮轮询可能自动重新开始。"
        )
        if stopped:
            lines.append(note)
        return "\n".join(lines)

    async def slice_recordings(
        self,
        *,
        duration_minutes: int | None = None,
        channel: str | None = None,
    ) -> str:
        if not self.settings.recording_enabled:
            return "当前未开启 TS3 频道录音，请设置 TS3_TRACKER__RECORDING_ENABLED=true。"

        minutes = (
            duration_minutes
            if duration_minutes is not None
            else self.settings.recording_slice_default_minutes
        )
        if minutes <= 0:
            return "切片分钟数必须是正整数。"

        results, errors = await self._recording_manager.slice_active_sessions(
            duration_minutes=minutes,
            channel_filter=channel,
            triggered_at=self._now_factory(),
        )
        if not results and errors:
            return "\n".join(errors)

        lines = [f"TS3 录音切片完成（请求最近 {minutes} 分钟）"]
        for result in results:
            actual_text = self._format_duration(int(result.actual_seconds))
            requested_text = self._format_duration(result.requested_seconds)
            if int(result.actual_seconds) < result.requested_seconds:
                duration_note = f"实际 {actual_text}（可用内容不足 {requested_text}）"
            else:
                duration_note = f"时长 {actual_text}"
            lines.append(
                f"- {result.channel_name} ({result.channel_id})：{duration_note}"
            )
        lines.extend(errors)
        return "\n".join(lines)

    def get_online_duration_seconds(self, user: Ts3OnlineUser) -> int | None:
        key = self._user_key(user)
        snapshot = self._snapshot.get(key)
        if snapshot is not None:
            if snapshot.first_seen_at:
                try:
                    started_at = datetime.strptime(
                        snapshot.first_seen_at, "%Y-%m-%d %H:%M:%S"
                    )
                except ValueError:
                    pass
                else:
                    return max(0, int((self._now_factory() - started_at).total_seconds()))
            if snapshot.connected_duration_seconds > 0:
                return snapshot.connected_duration_seconds
        if user.connected_duration_seconds > 0:
            return user.connected_duration_seconds
        return None
