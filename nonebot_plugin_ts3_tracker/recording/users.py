from __future__ import annotations

from ..config import Ts3TrackerSettings
from ..models import Ts3ServerStatus


def channel_human_stats(
    settings: Ts3TrackerSettings,
    status: Ts3ServerStatus,
    channel_id: str,
) -> tuple[int, set[str]]:
    count = 0
    names: set[str] = set()
    for user in status.users:
        if user.channel_id != channel_id:
            continue
        if settings.is_recording_bot_nickname(user.nickname):
            continue
        count += 1
        if user.nickname:
            names.add(user.nickname)
    return count, names
