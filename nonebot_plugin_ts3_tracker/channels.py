from __future__ import annotations

from nonebot import logger

from .models import Ts3ServerStatus


def is_channel_id_token(token: str) -> bool:
    return token.isdigit() or token.lstrip("-").isdigit()


def channel_matches_token(
    *,
    channel_id: str,
    channel_name: str,
    token: str,
) -> bool:
    normalized = token.strip()
    if not normalized:
        return True
    if is_channel_id_token(normalized):
        return channel_id == normalized
    return channel_name.casefold() == normalized.casefold()


def resolve_channel_tokens(
    tokens: list[str],
    status: Ts3ServerStatus,
    *,
    log_missing: bool = True,
    preferred_ids: set[str] | None = None,
) -> dict[str, str]:
    channels_by_id = {channel_id: name for channel_id, name in status.channels}
    channels_by_name: dict[str, list[str]] = {}
    for channel_id, name in status.channels:
        channels_by_name.setdefault(name.casefold(), []).append(channel_id)

    preferred = preferred_ids or set()
    resolved: dict[str, str] = {}
    for item in tokens:
        if is_channel_id_token(item):
            channel_name = channels_by_id.get(item)
            if channel_name is not None:
                resolved[item] = channel_name
            elif log_missing:
                logger.warning("TS3 recording channel id not found on server: {}", item)
            continue
        candidates = channels_by_name.get(item.casefold(), [])
        if not candidates:
            if log_missing:
                logger.warning("TS3 recording channel not found on server: {}", item)
            continue
        matched_id = next(
            (channel_id for channel_id in candidates if channel_id in preferred),
            candidates[0],
        )
        resolved[matched_id] = channels_by_id.get(matched_id, item)
    return resolved
