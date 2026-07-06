from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from nonebot import logger

from .config import Ts3TrackerSettings
from .models import Ts3OnlineUser, Ts3ServerStatus
from .query import Ts3QueryClient, Ts3QueryError


QueryClientFactory = Callable[[], Ts3QueryClient]
DurationProvider = Callable[[Ts3OnlineUser], int | None]


class Ts3TrackerService:
    def __init__(
        self,
        settings: Ts3TrackerSettings,
        client_factory: QueryClientFactory | None = None,
        duration_provider: DurationProvider | None = None,
    ) -> None:
        self.settings = settings
        self._client_factory = client_factory
        self._duration_provider = duration_provider

    async def build_server_message(self) -> str:
        return await self.build_detail_message()

    async def build_basic_message(self) -> str:
        return await self._build_message(detailed=False)

    async def build_detail_message(self) -> str:
        return await self._build_message(detailed=True)

    async def _build_message(self, *, detailed: bool) -> str:
        missing_fields = self.get_missing_required_fields()
        if missing_fields:
            return "TS3 配置不完整，请先填写：" + "、".join(missing_fields)

        try:
            status = await self.fetch_status()
        except Ts3QueryError as exc:
            logger.warning("TS3 query failed: {}", exc)
            return f"TS3 查询失败：{exc}"
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.exception("Unexpected TS3 query error: {}", exc)
            return "TS3 查询失败：发生了未预期错误，请查看 NoneBot 日志。"

        return self.format_server_status(status, detailed=detailed)

    async def fetch_status(self) -> Ts3ServerStatus:
        return await self._build_client().fetch_status()

    def get_missing_required_fields(self) -> list[str]:
        missing: list[str] = []
        if not self.settings.server_host:
            missing.append("服务器地址")
        if self.settings.server_port <= 0:
            missing.append("服务器端口")
        if not self.settings.serverquery_username:
            missing.append("ServerQuery 账号")
        if not self.settings.serverquery_password:
            missing.append("ServerQuery 密码")
        return missing

    def format_server_status(
        self, status: Ts3ServerStatus, *, detailed: bool = True
    ) -> str:
        lines: list[str] = []
        if detailed:
            lines.extend(
                [
                    f"服务器地址：{status.server_host}:{status.server_port}",
                    f"服务器端口：{status.server_port}",
                    f"服务器名称：{status.server_name or '-'}",
                    "服务器频道：",
                ]
            )

        for channel_name, users in self.group_users_by_channel(
            status,
            show_duration=detailed,
            include_empty_channels=detailed,
        ):
            display_name = channel_name or "未命名频道"
            if users:
                lines.append(f"{display_name}: {', '.join(users)}")
            else:
                lines.append(display_name)

        if not lines and not detailed:
            return "暂无在线用户"

        return "\n".join(lines)

    def group_users_by_channel(
        self,
        status: Ts3ServerStatus,
        *,
        show_duration: bool,
        include_empty_channels: bool,
    ) -> list[tuple[str, list[str]]]:
        grouped: dict[str, dict[str, object]] = {}
        if include_empty_channels:
            for channel_id, channel_name in status.channels:
                grouped.setdefault(channel_id, {"name": channel_name, "users": []})

        for user in status.users:
            if self.settings.is_recording_bot_nickname(user.nickname):
                continue
            channel_id = user.channel_id or "__unknown__"
            channel_entry = grouped.setdefault(
                channel_id,
                {"name": user.channel_name or "未命名频道", "users": []},
            )
            users = channel_entry["users"]
            assert isinstance(users, list)
            users.append(self._format_user_display(user, show_duration=show_duration))

        ordered_groups: list[tuple[str, list[str]]] = []
        for channel_id, _ in status.channels:
            channel_entry = grouped.pop(channel_id, None)
            if channel_entry is None:
                continue
            if not include_empty_channels and not channel_entry["users"]:
                continue
            ordered_groups.append(
                (str(channel_entry["name"]), list(channel_entry["users"]))
            )

        for channel_entry in grouped.values():
            if not include_empty_channels and not channel_entry["users"]:
                continue
            ordered_groups.append(
                (str(channel_entry["name"]), list(channel_entry["users"]))
            )

        return ordered_groups

    def _build_client(self) -> Ts3QueryClient:
        if self._client_factory is not None:
            return self._client_factory()

        return Ts3QueryClient(
            host=self.settings.server_host,
            server_port=self.settings.server_port,
            query_port=self.settings.serverquery_port,
            username=self.settings.serverquery_username,
            password=self.settings.serverquery_password,
            timeout=self.settings.query_timeout_seconds,
        )

    def _format_user_display(
        self, user: Ts3OnlineUser, *, show_duration: bool
    ) -> str:
        if not show_duration:
            return user.nickname
        duration = self._get_user_duration_seconds(user)
        if duration is None:
            return user.nickname
        return f"{user.nickname}({self._format_duration(duration)})"

    def _get_user_duration_seconds(self, user: Ts3OnlineUser) -> int | None:
        if self._duration_provider is not None:
            duration = self._duration_provider(user)
            if duration is not None:
                return max(0, duration)
        if user.connected_duration_seconds > 0:
            return user.connected_duration_seconds
        return None

    def _format_duration(self, seconds: int) -> str:
        total = max(0, seconds)
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}小时{minutes}分{secs}秒"
        if minutes:
            return f"{minutes}分{secs}秒"
        return f"{secs}秒"
