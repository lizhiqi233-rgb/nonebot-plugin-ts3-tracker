from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence

from nonebot import logger

from .models import Ts3OnlineUser, Ts3ServerStatus


class Ts3QueryError(Exception):
    """Raised when a ServerQuery request fails."""


ESCAPE_MAP = {
    "\\": "\\\\",
    "/": "\\/",
    " ": "\\s",
    "|": "\\p",
    "\a": "\\a",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\v": "\\v",
}

UNESCAPE_MAP = {
    "\\\\": "\\",
    "\\/": "/",
    "\\s": " ",
    "\\p": "|",
    "\\a": "\a",
    "\\b": "\b",
    "\\f": "\f",
    "\\n": "\n",
    "\\r": "\r",
    "\\t": "\t",
    "\\v": "\v",
}


class Ts3QueryClient:
    def __init__(
        self,
        host: str,
        server_port: int,
        username: str,
        password: str,
        query_port: int = 10011,
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.server_port = server_port
        self.query_port = query_port
        self.username = username
        self.password = password
        self.timeout = timeout

    async def fetch_status(self) -> Ts3ServerStatus:
        try:
            return await asyncio.wait_for(self._fetch_status_inner(), timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            raise Ts3QueryError(f"TS3 查询超时（{self.timeout:.0f} 秒）") from exc

    async def _fetch_status_inner(self) -> Ts3ServerStatus:
        try:
            reader, writer = await asyncio.open_connection(self.host, self.query_port)
        except Exception as exc:  # pragma: no cover - network dependent
            raise Ts3QueryError(
                f"无法连接到 ServerQuery：{self.host}:{self.query_port} ({exc})"
            ) from exc

        try:
            await self._consume_welcome(reader)
            await self._execute(
                reader,
                writer,
                f"login {self._escape(self.username)} {self._escape(self.password)}",
                "login",
            )
            await self._execute(reader, writer, f"use port={self.server_port}", "use")
            serverinfo_records = await self._execute(
                reader, writer, "serverinfo", "serverinfo"
            )
            channel_records = await self._execute(
                reader, writer, "channellist", "channellist"
            )
            client_records = await self._execute(
                reader,
                writer,
                "clientlist -uid -away -ip -times",
                "clientlist",
            )
            await self._write_line(writer, "quit")
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

        serverinfo = serverinfo_records[0] if serverinfo_records else {}
        channels = {
            channel.get("cid", ""): channel.get("channel_name", "")
            for channel in channel_records
        }
        channel_order = [
            (channel.get("cid", ""), channel.get("channel_name", ""))
            for channel in channel_records
            if channel.get("cid", "")
        ]

        users: list[Ts3OnlineUser] = []
        for client in client_records:
            if client.get("client_type") == "1":
                continue

            users.append(
                Ts3OnlineUser(
                    nickname=client.get("client_nickname", ""),
                    channel_id=client.get("cid", ""),
                    channel_name=channels.get(client.get("cid", ""), ""),
                    client_id=client.get("clid", ""),
                    database_id=client.get("client_database_id", ""),
                    unique_id=client.get("client_unique_identifier", ""),
                    client_ip=client.get("connection_client_ip", ""),
                    connected_duration_seconds=max(
                        0,
                        self._safe_int(client.get("connection_connected_time"), 0) // 1000,
                    ),
                    away=client.get("client_away", "0") == "1",
                )
            )

        users.sort(key=lambda item: item.nickname.casefold())
        server_port = self._safe_int(
            serverinfo.get("virtualserver_port"), self.server_port
        )

        return Ts3ServerStatus(
            server_name=serverinfo.get("virtualserver_name", ""),
            server_host=self.host,
            server_port=server_port,
            online_count=len(users),
            channels=channel_order,
            users=users,
        )

    async def _execute(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        command: str,
        action: str,
    ) -> list[dict[str, str]]:
        await self._write_line(writer, command)
        lines = await self._read_response(reader)
        return self._parse_response(lines, action)

    async def _write_line(self, writer: asyncio.StreamWriter, line: str) -> None:
        writer.write(f"{line}\n".encode("utf-8"))
        await writer.drain()

    async def _consume_welcome(self, reader: asyncio.StreamReader) -> None:
        while True:
            raw_line = await reader.readline()
            if not raw_line:
                logger.warning("TS3 ServerQuery welcome banner ended unexpectedly")
                return

            line = raw_line.decode("utf-8", errors="replace").strip("\r\n")
            if not line:
                continue
            if line.startswith("error "):
                return
            if line == "TS3":
                continue
            if "TeamSpeak 3 ServerQuery interface" in line:
                return

            logger.debug("TS3 ServerQuery welcome line: {}", line)

    async def _read_response(self, reader: asyncio.StreamReader) -> list[str]:
        lines: list[str] = []
        while True:
            try:
                raw_line = await reader.readline()
            except Exception as exc:
                raise Ts3QueryError(f"ServerQuery 读取响应失败：{exc}") from exc

            if not raw_line:
                if lines:
                    return lines
                raise Ts3QueryError("ServerQuery 连接已关闭")

            line = raw_line.decode("utf-8", errors="replace").strip("\r\n")
            if not line:
                continue

            lines.append(line)
            if line.startswith("error "):
                return lines

    def _parse_response(
        self, lines: Sequence[str], action: str
    ) -> list[dict[str, str]]:
        if not lines:
            return []

        error_line = lines[-1]
        if not error_line.startswith("error "):
            raise Ts3QueryError(f"{action} 失败：响应格式异常，缺少 error 行")

        error_info = self._parse_record(error_line.removeprefix("error "))
        try:
            error_id = int(error_info.get("id", "-1"))
        except (TypeError, ValueError) as exc:
            raise Ts3QueryError(
                f"{action} 失败：响应格式异常，error id 无法解析"
            ) from exc

        if error_id != 0:
            error_msg = error_info.get("msg", "unknown")
            raise Ts3QueryError(f"{action} 失败：{error_msg} (id={error_id})")

        if len(lines) == 1:
            return []

        data = "\n".join(lines[:-1]).strip()
        if not data:
            return []

        records: list[dict[str, str]] = []
        for raw_record in data.split("|"):
            record = raw_record.strip()
            if record:
                records.append(self._parse_record(record))
        return records

    def _parse_record(self, payload: str) -> dict[str, str]:
        record: dict[str, str] = {}
        for token in payload.split(" "):
            if not token:
                continue
            if "=" not in token:
                record[token] = ""
                continue
            key, value = token.split("=", 1)
            record[key] = self._unescape(value)
        return record

    def _escape(self, value: str) -> str:
        return "".join(ESCAPE_MAP.get(char, char) for char in value)

    def _unescape(self, value: str) -> str:
        chars: list[str] = []
        index = 0
        while index < len(value):
            if value[index] != "\\" or index + 1 >= len(value):
                chars.append(value[index])
                index += 1
                continue

            escaped = value[index : index + 2]
            chars.append(UNESCAPE_MAP.get(escaped, escaped[1]))
            index += 2

        return "".join(chars)

    def _safe_int(self, value: object, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
