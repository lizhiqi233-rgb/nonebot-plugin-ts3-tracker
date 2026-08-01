import asyncio
import struct
import wave
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from nonebot_plugin_ts3_tracker import file_send
from nonebot_plugin_ts3_tracker.config import Ts3TrackerSettings
from nonebot_plugin_ts3_tracker.models import Ts3OnlineUser, Ts3ServerStatus
from nonebot_plugin_ts3_tracker.recording.manager import RecordingManager
from nonebot_plugin_ts3_tracker.recording.session import ChannelRecordingSession
from nonebot_plugin_ts3_tracker.recording.sidecar import (
    SidecarLauncher,
    _materialize_identity,
    _unlink_if_exists,
    resolve_identity_entries,
)
from nonebot_plugin_ts3_tracker.recording.slice import (
    parse_slice_command_args,
    slice_wav_tail,
)


def _settings(**overrides: object) -> Ts3TrackerSettings:
    values: dict[str, object] = {
        "server_host": "localhost",
        "recording_enabled": True,
        "recording_channels": "1",
        "recording_identities": "identity",
        "recording_min_human_count": 2,
        "recording_stop_grace_seconds": 0,
    }
    values.update(overrides)
    return Ts3TrackerSettings(**values)


def _status(nickname: str) -> Ts3ServerStatus:
    return Ts3ServerStatus(
        server_name="Server",
        server_host="localhost",
        server_port=9987,
        channels=[("1", "Lobby")],
        users=[
            Ts3OnlineUser(
                nickname=nickname,
                channel_id="1",
                channel_name="Lobby",
                client_id="1",
                database_id="1",
                unique_id=f"uid-{nickname}",
                connected_duration_seconds=1,
                away=False,
            )
        ],
    )


def _session(tmp_path: Path, participants: set[str]) -> ChannelRecordingSession:
    return ChannelRecordingSession(
        channel_id="1",
        channel_name="Lobby",
        identity="identity",
        wav_path=tmp_path / "recording.wav",
        metadata_path=tmp_path / "recording.json",
        started_at=datetime(2026, 7, 31, 12, 0, 0),
        nickname="RecBot-Lobby",
        participant_names=participants,
    )


def test_inline_identity_is_materialized_without_exposing_secret(
    tmp_path: Path,
) -> None:
    identity = "inline/identity-test-value"

    assert resolve_identity_entries(identity, tmp_path) == [identity]
    argument, temporary_path = _materialize_identity(identity)
    try:
        assert identity not in argument
        assert temporary_path is not None
        assert temporary_path.read_text(encoding="utf-8") == identity
    finally:
        if temporary_path is not None:
            _unlink_if_exists(temporary_path)
    assert temporary_path is not None
    assert not temporary_path.exists()


class _FakeStdin:
    def __init__(self, process: "_FakeProcess") -> None:
        self.process = process

    def write(self, data: bytes) -> None:
        self.data = data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.process.finish(0)


class _FakeProcess:
    def __init__(self, output_path: Path) -> None:
        self.returncode: int | None = None
        self.stderr = asyncio.StreamReader()
        self._done = asyncio.Event()
        self.stdin = _FakeStdin(self)
        output_path.write_bytes(b"R" * 44)
        self.stderr.feed_data(b"READY channel_id=1 output=test\n")

    async def wait(self) -> int:
        await self._done.wait()
        assert self.returncode is not None
        return self.returncode

    def finish(self, code: int) -> None:
        if self.returncode is not None:
            return
        self.returncode = code
        self.stderr.feed_eof()
        self._done.set()

    def kill(self) -> None:
        self.finish(-9)

    def send_signal(self, _signal_number: int) -> None:
        self.finish(0)


def test_sidecar_waits_for_ready_and_reaps_watcher(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sidecar_path = tmp_path / "sidecar"
    sidecar_path.write_bytes(b"fake")
    sidecar_path.chmod(0o755)
    session = _session(tmp_path, set())
    session.identity = "inline/secret"
    capture: dict[str, object] = {}

    async def fake_create(*command: str, **_kwargs: object) -> _FakeProcess:
        capture["command"] = command
        output_path = Path(command[command.index("--output") + 1])
        process = _FakeProcess(output_path)
        capture["process"] = process
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    async def scenario() -> None:
        launcher = SidecarLauncher(sidecar_path)
        await launcher.start(
            session,
            host="localhost",
            port=9987,
            server_password="",
            channel_password="",
        )
        command = capture["command"]
        assert isinstance(command, tuple)
        identity_path = Path(command[command.index("--identity") + 1])
        capture["identity_path"] = identity_path
        assert "inline/secret" not in command
        assert session.watch_task is not None
        await session.terminate()
        assert session.watch_task is None

    asyncio.run(scenario())
    identity_path = capture["identity_path"]
    assert isinstance(identity_path, Path)
    assert not identity_path.exists()


def test_slice_is_correct_and_duration_is_bounded(tmp_path: Path) -> None:
    source_path = tmp_path / "source.wav"
    output_path = tmp_path / "tail.wav"
    samples = list(range(100))
    with wave.open(str(source_path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(10)
        writer.writeframes(struct.pack("<" + "h" * len(samples), *samples))

    actual_seconds = slice_wav_tail(
        source_path,
        output_path,
        duration_seconds=3,
        sample_rate=10,
        sample_width=2,
        header_bytes=44,
    )
    with wave.open(str(output_path), "rb") as reader:
        tail = list(
            struct.unpack(
                "<" + "h" * reader.getnframes(),
                reader.readframes(reader.getnframes()),
            )
        )

    assert actual_seconds == 3
    assert tail == samples[-30:]
    assert (
        parse_slice_command_args(
            "切片 -s 61",
            default_minutes=3,
            max_minutes=60,
        )
        == "切片分钟数不能超过 60 分钟。"
    )


def test_zero_grace_stops_immediately_and_participants_accumulate(
    tmp_path: Path,
) -> None:
    manager = RecordingManager(_settings(), tmp_path)
    manager._launcher = SimpleNamespace(is_available=True)
    session = _session(tmp_path, {"Alice"})
    manager._sessions["1"] = session
    stop_calls: list[str] = []

    async def stop_probe(channel_id: str, *, ended_at: datetime) -> None:
        stop_calls.append(channel_id)
        manager._sessions.pop(channel_id, None)

    manager._stop_session = stop_probe
    asyncio.run(manager.sync(_status("Alice"), now=datetime(2026, 7, 31, 12, 0, 5)))
    assert stop_calls == ["1"]

    union_manager = RecordingManager(
        _settings(recording_min_human_count=1),
        tmp_path,
    )
    union_manager._launcher = SimpleNamespace(is_available=True)
    union_session = _session(tmp_path, {"Alice"})
    union_manager._sessions["1"] = union_session
    asyncio.run(
        union_manager.sync(
            _status("Bob"),
            now=datetime(2026, 7, 31, 12, 1, 0),
        )
    )
    assert union_session.participant_names == {"Alice", "Bob"}


def test_missing_wav_does_not_create_metadata(tmp_path: Path) -> None:
    manager = RecordingManager(
        _settings(recording_min_session_seconds=0),
        tmp_path,
    )
    session = _session(tmp_path, set())
    manager._sessions["1"] = session

    asyncio.run(
        manager._stop_session(
            "1",
            ended_at=datetime(2026, 7, 31, 12, 0, 10),
        )
    )

    assert not session.wav_path.exists()
    assert not session.metadata_path.exists()


class _FailingUniMessage:
    send_attempts = 0

    def __init__(self, _value: object) -> None:
        pass

    async def send(self, **_kwargs: object) -> None:
        type(self).send_attempts += 1
        raise RuntimeError("delivery status unknown")


class _FailingBot:
    self_id = "bot"

    def __init__(self) -> None:
        self.fallback_attempts = 0

    async def send_group_msg(self, **_kwargs: object) -> None:
        self.fallback_attempts += 1

    async def call_api(self, _api: str, **_kwargs: object) -> None:
        self.fallback_attempts += 1


def test_failed_send_is_not_retried_through_other_apis(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "slice.wav"
    path.write_bytes(b"audio")
    bot = _FailingBot()
    _FailingUniMessage.send_attempts = 0
    monkeypatch.setattr(file_send, "UniMessage", _FailingUniMessage)

    result = asyncio.run(file_send.send_group_file(bot, 123, path))

    assert result is False
    assert _FailingUniMessage.send_attempts == 1
    assert bot.fallback_attempts == 0
