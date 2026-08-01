from __future__ import annotations

import asyncio
import contextlib
import os
import platform
import sys
import tempfile
from pathlib import Path

from nonebot import logger

from ..parsing import parse_delimited_list
from .session import ChannelRecordingSession

SERVER_PASSWORD_ENV = "TS3_RECORDER_SERVER_PASSWORD"
CHANNEL_PASSWORD_ENV = "TS3_RECORDER_CHANNEL_PASSWORD"

READY_TIMEOUT_SECONDS = 15.0
OUTPUT_READY_TIMEOUT_SECONDS = 2.0
WAV_HEADER_BYTES = 44


class SidecarLauncher:
    def __init__(self, sidecar_path: Path, *, debug: bool = False) -> None:
        self._sidecar_path = sidecar_path
        self._debug = debug

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
            raise FileNotFoundError(f"recorder sidecar not found: {self._sidecar_path}")
        _ensure_sidecar_runnable(self._sidecar_path)

        output_dir = session.wav_path.parent
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as exc:
            raise PermissionError(
                f"cannot create recording output directory {output_dir}"
            ) from exc
        identity_arg, temporary_identity = await asyncio.to_thread(
            _materialize_identity,
            session.identity,
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
            identity_arg,
            "--nickname",
            session.nickname,
            "--output",
            str(session.wav_path),
        ]

        env = os.environ.copy()
        # Pass secrets via env so they do not appear in process argv.
        if server_password:
            env[SERVER_PASSWORD_ENV] = server_password
        else:
            env.pop(SERVER_PASSWORD_ENV, None)
        if channel_password:
            env[CHANNEL_PASSWORD_ENV] = channel_password
        else:
            env.pop(CHANNEL_PASSWORD_ENV, None)

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._sidecar_path.parent),
                env=env,
            )
            session.process = process
            ready_result: asyncio.Future[str | None] = (
                asyncio.get_running_loop().create_future()
            )
            session.watch_task = asyncio.create_task(
                self._watch_process(session, ready_result)
            )

            try:
                error = await asyncio.wait_for(
                    asyncio.shield(ready_result),
                    timeout=READY_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError as exc:
                await session.terminate()
                raise TimeoutError(
                    f"recorder did not become ready within "
                    f"{READY_TIMEOUT_SECONDS:.0f} seconds"
                ) from exc

            if error is not None:
                await session.terminate()
                raise RuntimeError(error)

            try:
                await self._wait_for_output_file(session)
            except Exception:
                await session.terminate()
                raise
        finally:
            if temporary_identity is not None:
                await asyncio.to_thread(_unlink_if_exists, temporary_identity)

    async def _wait_for_output_file(
        self,
        session: ChannelRecordingSession,
    ) -> None:
        process = session.process
        if process is None:
            raise RuntimeError("recorder process was not created")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + OUTPUT_READY_TIMEOUT_SECONDS
        while loop.time() < deadline:
            if await asyncio.to_thread(_wav_header_ready, session.wav_path):
                return
            if process.returncode is not None:
                break
            await asyncio.sleep(0.05)

        if process.returncode is not None:
            raise RuntimeError(
                f"recorder exited with code {process.returncode} "
                f"before creating {session.wav_path}"
            )
        raise TimeoutError(
            f"recorder did not create {session.wav_path} within "
            f"{OUTPUT_READY_TIMEOUT_SECONDS:.0f} seconds after READY"
        )

    async def _watch_process(
        self,
        session: ChannelRecordingSession,
        ready_result: asyncio.Future[str | None],
    ) -> None:
        process = session.process
        if process is None:
            if not ready_result.done():
                ready_result.set_result("recorder process was not created")
            return

        assert process.stderr is not None
        ready = False
        diagnostics: list[str] = []
        try:
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                if text.startswith("READY "):
                    ready = True
                    if not ready_result.done():
                        ready_result.set_result(None)
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
                diagnostics.append(text)
                del diagnostics[:-3]
                log = logger.info if self._debug else logger.debug
                log("TS3 recorder [{}]: {}", session.channel_id, text)
        except Exception as exc:
            diagnostics.append(f"stderr read failed: {exc}")
            with contextlib.suppress(ProcessLookupError):
                process.kill()

        return_code = await process.wait()
        if not ready_result.done():
            detail = f": {diagnostics[-1]}" if diagnostics else ""
            ready_result.set_result(
                f"recorder exited with code {return_code} before READY{detail}"
            )
        if return_code != 0:
            logger.warning(
                "TS3 recorder exited with code {} for channel {} ({}){}",
                return_code,
                session.channel_id,
                session.channel_name,
                "" if ready else " before READY",
            )


def _materialize_identity(identity: str) -> tuple[str, Path | None]:
    identity_path = Path(identity).expanduser()
    if _is_file(identity_path):
        return str(identity_path.resolve()), None

    descriptor, raw_path = tempfile.mkstemp(
        prefix="ts3-recorder-identity-",
        suffix=".txt",
    )
    temporary_path = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            file.write(identity)
        with contextlib.suppress(OSError):
            os.chmod(temporary_path, 0o600)
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        _unlink_if_exists(temporary_path)
        raise
    return str(temporary_path), temporary_path


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _wav_header_ready(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= WAV_HEADER_BYTES
    except OSError:
        return False


def _unlink_if_exists(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


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
    for item in parse_delimited_list(raw):
        path = Path(item).expanduser()
        if _is_file(path):
            entries.append(str(path.resolve()))
            continue
        candidate = identities_dir / item
        if _is_file(candidate):
            entries.append(str(candidate.resolve()))
            continue
        if path.is_absolute():
            logger.warning("TS3 recording identity path not found")
            continue
        entries.append(item)
    if not entries and identities_dir.is_dir():
        for path in sorted(identities_dir.glob("*")):
            if _is_file(path):
                entries.append(str(path.resolve()))
    return entries
