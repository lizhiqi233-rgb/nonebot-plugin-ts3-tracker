from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from nonebot import get_driver, get_plugin_config, logger, on_command, on_regex
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from .config import Config
from .recording.commands import parse_record_command_args, parse_stop_record_command_args
from .recording.retention import parse_cleanup_command_args
from .recording.slice import parse_slice_command_args
from .runtime import Ts3TrackerRuntime
from .service import Ts3TrackerService

MATCHER_PRIORITY = 10

plugin_config = get_plugin_config(Config).ts3_tracker
service = Ts3TrackerService(plugin_config)
runtime = Ts3TrackerRuntime(plugin_config, service)

__plugin_meta__ = PluginMetadata(
    name="TS3 Tracker",
    description="查询 TeamSpeak 3 服务器在线状态与频道在线成员。",
    usage=(
        "/ts 或 /上号：查看当前在线频道\n"
        "/tsinfo：查看TS服务器详细信息\n"
        "/tsnotify on：开启本群进退服通知\n"
        "/tsnotify off：关闭本群进退服通知\n"
        "/tsrecord：查看频道录音状态\n"
        "/ts 切片 [分钟数] [频道]：截取进行中录音的最近片段（默认分钟数见配置）\n"
        "/ts 录制 [频道]：手动启动测试录音（忽略最低人数，轮询不会自动停录）\n"
        "/ts 停止录制 [频道]：停止进行中的录音\n"
        "/ts 清理 [录音|切片]：按保留策略立即清理过期录音文件\n"
        "可选：配置 command_prefix_required=false 后，可直接发送上号/ts/tsinfo\n\n"
        "可选：开启轮询后发送 TS3 进服/退服通知（notification_push_mode=join_only 时仅进服；换频道不通知）\n"
        "可选：recording_enabled=true 后，对 recording_channels 中有人频道自动录音\n"
        "可选：recording_retention_days / recording_slice_retention_days 开启过期录音定时清理"
    ),
    type="application",
    homepage="https://github.com/moeneri/nonebot-plugin-ts3-tracker",
    config=Config,
    supported_adapters={"~onebot.v11"},
)


def _ensure_group_allowed(event: MessageEvent) -> str | None:
    if not isinstance(event, GroupMessageEvent):
        return None
    if plugin_config.is_group_allowed(event.group_id):
        return None
    return "当前群未开启 TS3 查询白名单权限。"


def _plain_command_enabled() -> bool:
    return not plugin_config.command_prefix_required


def _require_group_event(event: MessageEvent) -> GroupMessageEvent | None:
    return event if isinstance(event, GroupMessageEvent) else None


async def _handle_query(
    event: MessageEvent,
    *,
    detailed: bool,
    finish: Callable[[str], Awaitable[None]],
) -> None:
    denied_message = _ensure_group_allowed(event)
    if denied_message is not None:
        await finish(denied_message)

    group_id = getattr(event, "group_id", None)
    logger.info(
        "群号 {} 查询了服务器信息。",
        group_id if group_id is not None else event.get_session_id(),
    )
    message = (
        await service.build_detail_message()
        if detailed
        else await service.build_basic_message()
    )
    await finish(message)


async def _handle_notify_switch(
    event: MessageEvent,
    *,
    enabled: bool,
    finish: Callable[[str], Awaitable[None]],
) -> None:
    group_event = _require_group_event(event)
    if group_event is None:
        return await finish("请在群聊中使用 /tsnotify on 或 /tsnotify off。")

    denied_message = _ensure_group_allowed(group_event)
    if denied_message is not None:
        return await finish(denied_message)

    if not plugin_config.notification_enabled:
        return await finish(
            "当前未开启 TS3 轮询通知，请先在配置中设置 TS3_TRACKER__NOTIFICATION_ENABLED=true。"
        )

    changed = await runtime.set_group_notify_enabled(group_event.group_id, enabled)
    if enabled:
        logger.info("群号 {} 开启了 TS3 进退服通知。", group_event.group_id)
        if changed:
            return await finish("已开启本群 TS3 进退服通知。")
        return await finish("本群 TS3 进退服通知已经是开启状态。")

    logger.info("群号 {} 关闭了 TS3 进退服通知。", group_event.group_id)
    if changed:
        return await finish("已关闭本群 TS3 进退服通知。")
    return await finish("本群 TS3 进退服通知已经是关闭状态。")


async def _handle_slice(
    event: MessageEvent,
    arg_text: str,
    *,
    finish: Callable[[str], Awaitable[None]],
) -> None:
    denied_message = _ensure_group_allowed(event)
    if denied_message is not None:
        return await finish(denied_message)

    if not plugin_config.recording_enabled:
        return await finish(
            "当前未开启 TS3 频道录音，请设置 TS3_TRACKER__RECORDING_ENABLED=true。"
        )

    parsed = parse_slice_command_args(
        arg_text,
        default_minutes=plugin_config.recording_slice_default_minutes,
    )
    if isinstance(parsed, str):
        return await finish(parsed)

    duration_minutes, channel = parsed
    if duration_minutes <= 0:
        return await finish("切片分钟数必须是正整数。")

    group_id = getattr(event, "group_id", None)
    logger.info(
        "群号 {} 请求 TS3 录音切片，分钟数 {}，频道 {}。",
        group_id if group_id is not None else event.get_session_id(),
        duration_minutes,
        channel or "全部",
    )
    message = await runtime.slice_recordings(
        duration_minutes=duration_minutes,
        channel=channel,
    )
    return await finish(message)


async def _handle_cleanup(
    event: MessageEvent,
    arg_text: str,
    *,
    finish: Callable[[str], Awaitable[None]],
) -> None:
    denied_message = _ensure_group_allowed(event)
    if denied_message is not None:
        return await finish(denied_message)

    parsed = parse_cleanup_command_args(arg_text)
    if isinstance(parsed, str):
        return await finish(parsed)

    group_id = getattr(event, "group_id", None)
    logger.info(
        "群号 {} 请求 TS3 录音文件清理，目标 {}。",
        group_id if group_id is not None else event.get_session_id(),
        parsed.value,
    )
    message = await runtime.cleanup_recordings_manual(target=parsed)
    return await finish(message)


async def _handle_record(
    event: MessageEvent,
    arg_text: str,
    *,
    finish: Callable[[str], Awaitable[None]],
) -> None:
    denied_message = _ensure_group_allowed(event)
    if denied_message is not None:
        return await finish(denied_message)

    if not plugin_config.recording_enabled:
        return await finish(
            "当前未开启 TS3 频道录音，请设置 TS3_TRACKER__RECORDING_ENABLED=true。"
        )

    channel = parse_record_command_args(arg_text)
    group_id = getattr(event, "group_id", None)
    logger.info(
        "群号 {} 请求 TS3 测试录音，频道 {}。",
        group_id if group_id is not None else event.get_session_id(),
        channel or "全部",
    )
    message = await runtime.force_start_recordings(channel=channel)
    return await finish(message)


async def _handle_stop_record(
    event: MessageEvent,
    arg_text: str,
    *,
    finish: Callable[[str], Awaitable[None]],
) -> None:
    denied_message = _ensure_group_allowed(event)
    if denied_message is not None:
        return await finish(denied_message)

    if not plugin_config.recording_enabled:
        return await finish(
            "当前未开启 TS3 频道录音，请设置 TS3_TRACKER__RECORDING_ENABLED=true。"
        )

    channel = parse_stop_record_command_args(arg_text)
    group_id = getattr(event, "group_id", None)
    logger.info(
        "群号 {} 请求停止 TS3 录音，频道 {}。",
        group_id if group_id is not None else event.get_session_id(),
        channel or "全部",
    )
    message = await runtime.stop_recordings(channel=channel)
    return await finish(message)


ts3_status = on_command(
    "上号",
    aliases={"ts"},
    priority=MATCHER_PRIORITY,
    block=True,
)

ts3_status_info = on_command(
    "tsinfo",
    priority=MATCHER_PRIORITY,
    block=True,
)

ts3_notify = on_command(
    "tsnotify",
    priority=MATCHER_PRIORITY,
    block=True,
)

ts3_record = on_command(
    "tsrecord",
    priority=MATCHER_PRIORITY,
    block=True,
)

ts3_plain_status = on_regex(
    r"^(?:上号|ts)$",
    flags=re.IGNORECASE,
    rule=Rule(_plain_command_enabled),
    priority=MATCHER_PRIORITY,
    block=True,
)

ts3_plain_slice = on_regex(
    r"^(?:上号|ts)\s+切片(?:\s+(?P<args>.+))?$",
    flags=re.IGNORECASE,
    rule=Rule(_plain_command_enabled),
    priority=MATCHER_PRIORITY,
    block=True,
)

ts3_plain_record = on_regex(
    r"^(?:上号|ts)\s+录制(?:\s+(?P<args>.+))?$",
    flags=re.IGNORECASE,
    rule=Rule(_plain_command_enabled),
    priority=MATCHER_PRIORITY,
    block=True,
)

ts3_plain_stop_record = on_regex(
    r"^(?:上号|ts)\s+停止录制(?:\s+(?P<args>.+))?$",
    flags=re.IGNORECASE,
    rule=Rule(_plain_command_enabled),
    priority=MATCHER_PRIORITY,
    block=True,
)

ts3_plain_cleanup = on_regex(
    r"^(?:上号|ts)\s+清理(?:\s+(?P<args>.+))?$",
    flags=re.IGNORECASE,
    rule=Rule(_plain_command_enabled),
    priority=MATCHER_PRIORITY,
    block=True,
)

ts3_plain_status_info = on_regex(
    r"^tsinfo$",
    flags=re.IGNORECASE,
    rule=Rule(_plain_command_enabled),
    priority=MATCHER_PRIORITY,
    block=True,
)

ts3_plain_notify = on_regex(
    r"^tsnotify\s+(on|off)$",
    flags=re.IGNORECASE,
    rule=Rule(_plain_command_enabled),
    priority=MATCHER_PRIORITY,
    block=True,
)


@ts3_status.handle()
async def handle_ts3_status(event: MessageEvent, arg: Message = CommandArg()) -> None:
    arg_text = arg.extract_plain_text().strip()
    if arg_text.startswith("切片"):
        return await _handle_slice(event, arg_text, finish=ts3_status.finish)
    if arg_text.startswith("停止录制"):
        return await _handle_stop_record(event, arg_text, finish=ts3_status.finish)
    if arg_text.startswith("清理"):
        return await _handle_cleanup(event, arg_text, finish=ts3_status.finish)
    if arg_text.startswith("录制"):
        return await _handle_record(event, arg_text, finish=ts3_status.finish)
    if arg_text:
        return await ts3_status.finish(
            "未知子命令。可用：/ts、/ts 切片 [分钟数] [频道]、"
            "/ts 录制 [频道]、/ts 停止录制 [频道]、/ts 清理 [录音|切片]"
        )
    await _handle_query(event, detailed=False, finish=ts3_status.finish)


@ts3_status_info.handle()
async def handle_ts3_status_info(event: MessageEvent) -> None:
    await _handle_query(event, detailed=True, finish=ts3_status_info.finish)


@ts3_plain_status.handle()
async def handle_ts3_plain_status(event: MessageEvent) -> None:
    await _handle_query(event, detailed=False, finish=ts3_plain_status.finish)


@ts3_plain_slice.handle()
async def handle_ts3_plain_slice(event: MessageEvent) -> None:
    match = event.get_plaintext().strip()
    args_match = re.match(
        r"^(?:上号|ts)\s+切片(?:\s+(?P<args>.+))?$",
        match,
        flags=re.IGNORECASE,
    )
    arg_text = "切片"
    if args_match and args_match.group("args"):
        arg_text = f"切片 {args_match.group('args').strip()}"
    await _handle_slice(event, arg_text, finish=ts3_plain_slice.finish)


@ts3_plain_record.handle()
async def handle_ts3_plain_record(event: MessageEvent) -> None:
    match = event.get_plaintext().strip()
    args_match = re.match(
        r"^(?:上号|ts)\s+录制(?:\s+(?P<args>.+))?$",
        match,
        flags=re.IGNORECASE,
    )
    arg_text = "录制"
    if args_match and args_match.group("args"):
        arg_text = f"录制 {args_match.group('args').strip()}"
    await _handle_record(event, arg_text, finish=ts3_plain_record.finish)


@ts3_plain_stop_record.handle()
async def handle_ts3_plain_stop_record(event: MessageEvent) -> None:
    match = event.get_plaintext().strip()
    args_match = re.match(
        r"^(?:上号|ts)\s+停止录制(?:\s+(?P<args>.+))?$",
        match,
        flags=re.IGNORECASE,
    )
    arg_text = "停止录制"
    if args_match and args_match.group("args"):
        arg_text = f"停止录制 {args_match.group('args').strip()}"
    await _handle_stop_record(event, arg_text, finish=ts3_plain_stop_record.finish)


@ts3_plain_cleanup.handle()
async def handle_ts3_plain_cleanup(event: MessageEvent) -> None:
    match = event.get_plaintext().strip()
    args_match = re.match(
        r"^(?:上号|ts)\s+清理(?:\s+(?P<args>.+))?$",
        match,
        flags=re.IGNORECASE,
    )
    arg_text = "清理"
    if args_match and args_match.group("args"):
        arg_text = f"清理 {args_match.group('args').strip()}"
    await _handle_cleanup(event, arg_text, finish=ts3_plain_cleanup.finish)


@ts3_plain_status_info.handle()
async def handle_ts3_plain_status_info(event: MessageEvent) -> None:
    await _handle_query(
        event, detailed=True, finish=ts3_plain_status_info.finish
    )


@ts3_notify.handle()
async def handle_ts3_notify(event: MessageEvent, arg: Message = CommandArg()) -> None:
    action = arg.extract_plain_text().strip().lower()
    if action == "on":
        return await _handle_notify_switch(event, enabled=True, finish=ts3_notify.finish)
    if action == "off":
        return await _handle_notify_switch(
            event, enabled=False, finish=ts3_notify.finish
        )
    return await ts3_notify.finish(
        "用法：/tsnotify on 开启本群进退服通知\n/tsnotify off 关闭本群进退服通知"
    )


@ts3_plain_notify.handle()
async def handle_ts3_plain_notify(event: MessageEvent) -> None:
    action = event.get_plaintext().strip().split()[-1].lower()
    if action == "on":
        return await _handle_notify_switch(
            event, enabled=True, finish=ts3_plain_notify.finish
        )
    return await _handle_notify_switch(
        event, enabled=False, finish=ts3_plain_notify.finish
    )


@ts3_record.handle()
async def handle_ts3_record(event: MessageEvent) -> None:
    denied_message = _ensure_group_allowed(event)
    if denied_message is not None:
        return await ts3_record.finish(denied_message)

    if not plugin_config.recording_enabled:
        return await ts3_record.finish(
            "当前未开启 TS3 频道录音，请设置 TS3_TRACKER__RECORDING_ENABLED=true。"
        )

    return await ts3_record.finish(runtime.build_recording_status_message())


driver = get_driver()
driver.on_startup(runtime.startup)
driver.on_shutdown(runtime.shutdown)
