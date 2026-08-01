from __future__ import annotations

from pathlib import Path

from nonebot import logger, require
from nonebot.adapters.onebot.v11 import Bot

require("nonebot_plugin_alconna")
from nonebot_plugin_alconna.uniseg import File, Target, UniMessage  # noqa: E402
from nonebot_plugin_alconna.uniseg.constraint import (  # noqa: E402
    SupportAdapter,
    SupportScope,
)


async def send_group_file(bot: Bot, group_id: str | int, file_path: Path) -> bool:
    """Send a local file to a OneBot V11 group exactly once."""
    resolved = file_path.resolve()
    if not resolved.is_file():
        logger.warning("文件不存在，无法发送：{}", resolved)
        return False

    normalized_group_id = int(group_id)
    try:
        target = Target(
            str(normalized_group_id),
            scope=SupportScope.qq_client,
            adapter=SupportAdapter.onebot11,
            self_id=bot.self_id,
        )
        await UniMessage(File(path=resolved, name=resolved.name)).send(
            bot=bot,
            target=target,
        )
    except Exception as exc:
        logger.error(
            "发送文件 {} 到群 {} 失败；为避免重复发送，不再尝试其他接口：{}",
            resolved.name,
            group_id,
            exc,
        )
        return False
    return True
