from __future__ import annotations

import asyncio
from pathlib import Path

from nonebot import logger

from .session import ChannelRecordingSession


class SidecarLauncher:
    def __init__(self, sidecar_path: Path) -> None:
        self._sidecar_path = sidecar_path

    @property
    def is_available(self) -> bool:
        return self._sidecar_path.is_file()

    async def start(
        self,
        session: ChannelRecordingSession,
        *,
        host: str,
        port: int,
        server_password: str,
        channel_password: str,
    ) -> None:
        if not self.is_available:
            raise FileNotFoundError(
                f"recorder sidecar not found: {self._sidecar_path}"
            )

        command = [
            str(self._sidecar_path),
            "--host",
            host,
            "--port",
            str(port),
            "--channel-id",
            session.channel_id,
            "--channel-name",
            session.channel_name,
            "--identity",
            session.identity,
            "--nickname",
            session.nickname,
            "--output",
            str(session.wav_path),
        ]
        if server_password:
            command.extend(["--password", server_password])
        if channel_password:
            command.extend(["--channel-password", channel_password])

        session.wav_path.parent.mkdir(parents=True, exist_ok=True)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._sidecar_path.parent),
        )
        session.process = process
        asyncio.create_task(self._watch_process(session))

    async def _watch_process(self, session: ChannelRecordingSession) -> None:
        process = session.process
        if process is None:
            return

        assert process.stderr is not None
        ready = False
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            if text.startswith("READY "):
                ready = True
                logger.info(
                    "TS3 recorder ready for channel {} ({}) -> {}",
                    session.channel_id,
                    session.channel_name,
                    session.wav_path,
                )
                continue
            if text.startswith("DONE "):
                logger.info(
                    "TS3 recorder finished for channel {} ({})",
                    session.channel_id,
                    session.channel_name,
                )
                continue
            logger.debug("TS3 recorder [{}]: {}", session.channel_id, text)

        return_code = await process.wait()
        if return_code not in (0, None) and ready:
            logger.warning(
                "TS3 recorder exited with code {} for channel {} ({})",
                return_code,
                session.channel_id,
                session.channel_name,
            )


def resolve_sidecar_path(configured_path: str, plugin_dir: Path) -> Path:
    if configured_path.strip():
        return Path(configured_path).expanduser()

    candidates = [
        plugin_dir / "recorder_sidecar" / "target" / "release" / "ts3-recorder-sidecar.exe",
        plugin_dir / "recorder_sidecar" / "target" / "release" / "ts3-recorder-sidecar",
        plugin_dir / "recorder_sidecar" / "target" / "debug" / "ts3-recorder-sidecar.exe",
        plugin_dir / "recorder_sidecar" / "target" / "debug" / "ts3-recorder-sidecar",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def resolve_identity_entries(raw: str, plugin_dir: Path) -> list[str]:
    entries: list[str] = []
    for item in _split_lines(raw):
        path = Path(item).expanduser()
        if path.is_file():
            entries.append(str(path))
            continue
        if path.is_absolute() or "/" in item or "\\" in item:
            logger.warning("TS3 recording identity path not found: {}", item)
            continue
        entries.append(item)
    if not entries and (plugin_dir / "identities").is_dir():
        for path in sorted((plugin_dir / "identities").glob("*")):
            if path.is_file():
                entries.append(str(path))
    return entries


def _split_lines(raw: str) -> list[str]:
    normalized = raw.replace("\r", "\n").replace(";", "\n").replace(",", "\n")
    return [item.strip() for item in normalized.split("\n") if item.strip()]
