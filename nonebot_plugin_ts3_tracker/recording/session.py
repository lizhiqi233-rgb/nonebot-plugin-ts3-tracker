from __future__ import annotations

import asyncio
import contextlib
import json
import signal
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class ChannelRecordingSession:
    channel_id: str
    channel_name: str
    identity: str
    wav_path: Path
    metadata_path: Path
    started_at: datetime
    nickname: str
    process: asyncio.subprocess.Process | None = None
    watch_task: asyncio.Task[None] | None = field(default=None, repr=False)
    participant_names: set[str] = field(default_factory=set)

    def to_metadata_payload(
        self,
        *,
        ended_at: datetime,
        exit_code: int | None,
    ) -> dict[str, object]:
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "started_at": self.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            "ended_at": ended_at.strftime("%Y-%m-%d %H:%M:%S"),
            "output": str(self.wav_path),
            "nickname": self.nickname,
            "participants": sorted(self.participant_names),
            "exit_code": exit_code,
        }

    def write_metadata(
        self,
        *,
        ended_at: datetime,
        exit_code: int | None,
    ) -> None:
        payload = self.to_metadata_payload(
            ended_at=ended_at,
            exit_code=exit_code,
        )
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        self.metadata_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def terminate(self) -> int | None:
        process = self.process
        if process is None:
            await self._wait_for_watcher()
            return None

        if process.returncode is None:
            # Prefer graceful stop so the sidecar can finalize WAV and disconnect.
            if process.stdin is not None:
                with contextlib.suppress(Exception):
                    process.stdin.write(b"STOP\n")
                    await process.stdin.drain()
                    process.stdin.close()

            if sys.platform != "win32":
                with contextlib.suppress(ProcessLookupError):
                    process.send_signal(signal.SIGTERM)

            try:
                await asyncio.wait_for(process.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()

        await self._wait_for_watcher()
        return process.returncode

    async def _wait_for_watcher(self) -> None:
        task = self.watch_task
        if task is None or task is asyncio.current_task():
            return
        await asyncio.gather(task, return_exceptions=True)
        if self.watch_task is task:
            self.watch_task = None
