from __future__ import annotations

import asyncio
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
    participant_names: set[str] = field(default_factory=set)

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    def to_metadata_payload(self, *, ended_at: datetime) -> dict[str, object]:
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "started_at": self.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            "ended_at": ended_at.strftime("%Y-%m-%d %H:%M:%S"),
            "output": str(self.wav_path),
            "nickname": self.nickname,
            "participants": sorted(self.participant_names),
        }

    def write_metadata(self, *, ended_at: datetime) -> None:
        payload = self.to_metadata_payload(ended_at=ended_at)
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        self.metadata_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def terminate(self) -> int | None:
        if self.process is None:
            return None
        if self.process.returncode is not None:
            return self.process.returncode

        if sys.platform == "win32":
            self.process.terminate()
        else:
            self.process.send_signal(signal.SIGTERM)

        try:
            await asyncio.wait_for(self.process.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()
        return self.process.returncode
