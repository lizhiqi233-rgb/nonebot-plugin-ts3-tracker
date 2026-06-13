from __future__ import annotations

import asyncio
import os
import platform
import sys
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
        _ensure_sidecar_runnable(self._sidecar_path)

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

        output_dir = session.wav_path.parent
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as exc:
            raise PermissionError(
                f"cannot create recording output directory {output_dir}"
            ) from exc

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


def _ensure_sidecar_runnable(sidecar_path: Path) -> None:
    if sys.platform == "win32":
        return
    if os.access(sidecar_path, os.X_OK):
        return
    if os.access(sidecar_path, os.R_OK):
        raise PermissionError(
            f"recorder sidecar is not executable: {sidecar_path} "
            f"(run: chmod +x {sidecar_path})"
        )
    raise PermissionError(f"recorder sidecar is not readable: {sidecar_path}")


SIDECAR_BINARY_NAME = "ts3-recorder-sidecar"


def _sidecar_platform_dir() -> str:
    machine = platform.machine().casefold()
    if sys.platform == "win32":
        return "windows-x86_64"
    if machine in {"aarch64", "arm64"}:
        return "linux-aarch64"
    return "linux-x86_64"


def resolve_sidecar_path(configured_path: str, plugin_dir: Path) -> Path:
    if configured_path.strip():
        return Path(configured_path).expanduser()

    sidecar_root = plugin_dir / "recorder_sidecar"
    default_candidate = (
        sidecar_root / "bin" / _sidecar_platform_dir() / SIDECAR_BINARY_NAME
    )
    candidates = [
        default_candidate,
        sidecar_root / "bin" / SIDECAR_BINARY_NAME,
        sidecar_root / "bin" / f"{SIDECAR_BINARY_NAME}.exe",
        sidecar_root / "target" / "release" / f"{SIDECAR_BINARY_NAME}.exe",
        sidecar_root / "target" / "release" / SIDECAR_BINARY_NAME,
        sidecar_root / "target" / "debug" / f"{SIDECAR_BINARY_NAME}.exe",
        sidecar_root / "target" / "debug" / SIDECAR_BINARY_NAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return default_candidate


def resolve_identity_entries(raw: str, identities_dir: Path) -> list[str]:
    entries: list[str] = []
    for item in _split_lines(raw):
        path = Path(item).expanduser()
        if path.is_file():
            entries.append(str(path.resolve()))
            continue
        candidate = identities_dir / item
        if candidate.is_file():
            entries.append(str(candidate.resolve()))
            continue
        if path.is_absolute() or "/" in item or "\\" in item:
            logger.warning("TS3 recording identity path not found: {}", item)
            continue
        entries.append(item)
    if not entries and identities_dir.is_dir():
        for path in sorted(identities_dir.glob("*")):
            if path.is_file():
                entries.append(str(path.resolve()))
    return entries


def _split_lines(raw: str) -> list[str]:
    normalized = raw.replace("\r", "\n").replace(";", "\n").replace(",", "\n")
    return [item.strip() for item in normalized.split("\n") if item.strip()]
