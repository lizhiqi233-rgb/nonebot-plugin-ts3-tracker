from __future__ import annotations

from pathlib import Path

from nonebot import require

require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as store

from .config import Ts3TrackerSettings

RECORDINGS_DIR_NAME = "recordings"
IDENTITIES_DIR_NAME = "identities"


def get_plugin_data_root(settings: Ts3TrackerSettings) -> Path:
    if settings.data_dir.strip():
        return Path(settings.data_dir).expanduser()
    return store.get_plugin_data_dir()


def get_plugin_config_root() -> Path:
    return store.get_plugin_config_dir()


def resolve_recordings_dir(settings: Ts3TrackerSettings) -> Path:
    if settings.recording_output_dir.strip():
        return Path(settings.recording_output_dir).expanduser()
    return get_plugin_data_root(settings) / RECORDINGS_DIR_NAME


def resolve_identities_dir() -> Path:
    return get_plugin_config_root() / IDENTITIES_DIR_NAME


def ensure_storage_layout(settings: Ts3TrackerSettings) -> None:
    get_plugin_data_root(settings).mkdir(parents=True, exist_ok=True)
    resolve_recordings_dir(settings).mkdir(parents=True, exist_ok=True)
    resolve_identities_dir().mkdir(parents=True, exist_ok=True)
