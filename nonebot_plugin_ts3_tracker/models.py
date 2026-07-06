from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Ts3OnlineUser:
    nickname: str
    channel_id: str
    channel_name: str
    client_id: str
    database_id: str
    unique_id: str
    client_ip: str
    connected_duration_seconds: int
    away: bool


@dataclass(slots=True)
class Ts3ServerStatus:
    server_name: str
    server_host: str
    server_port: int
    online_count: int
    channels: list[tuple[str, str]]
    users: list[Ts3OnlineUser]
