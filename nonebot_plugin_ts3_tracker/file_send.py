from __future__ import annotations

import base64
from pathlib import Path

from nonebot import logger, require
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment

require("nonebot_plugin_alconna")
from nonebot_plugin_alconna.uniseg import File, Target, UniMessage
from nonebot_plugin_alconna.uniseg.constraint import SupportAdapter, SupportScope


async def send_group_file(bot: Bot, group_id: str | int, file_path: Path) -> bool:
    """向群聊发送本地文件，兼容 NapCat / OneBot V11。

    优先使用 Alconna UniMessage + File(path=...) 发送，失败时回退路径/base64 消息段与 upload_group_file。
    """
    resolved = file_path.resolve()
    if not resolved.is_file():
        logger.warning("文件不存在，无法发送：{}", resolved)
        return False

    name = resolved.name
    normalized_group_id = int(group_id)
    errors: list[str] = []

    try:
        target = Target(
            str(normalized_group_id),
            scope=SupportScope.qq_client,
            adapter=SupportAdapter.onebot11,
            self_id=bot.self_id,
        )
        await UniMessage(File(path=resolved, name=name)).send(bot=bot, target=target)
        return True
    except Exception as exc:
        errors.append(f"alconna: {exc}")

    try:
        await bot.send_group_msg(
            group_id=normalized_group_id,
            message=Message(MessageSegment("file", {"file": str(resolved), "name": name})),
        )
        return True
    except Exception as exc:
        errors.append(f"file path: {exc}")

    try:
        encoded = base64.b64encode(resolved.read_bytes()).decode()
        await bot.send_group_msg(
            group_id=normalized_group_id,
            message=Message(
                MessageSegment("file", {"file": f"base64://{encoded}", "name": name})
            ),
        )
        return True
    except Exception as exc:
        errors.append(f"file base64: {exc}")

    for upload_file in (False, True):
        try:
            await bot.call_api(
                "upload_group_file",
                group_id=normalized_group_id,
                file=str(resolved),
                name=name,
                upload_file=upload_file,
            )
            return True
        except Exception as exc:
            errors.append(f"upload_group_file(upload_file={upload_file}): {exc}")

    logger.error(
        "发送文件 {} 到群 {} 失败：{}",
        name,
        group_id,
        " | ".join(errors),
    )
    return False
