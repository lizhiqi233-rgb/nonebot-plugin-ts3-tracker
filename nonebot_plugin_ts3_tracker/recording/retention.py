from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class RetentionTarget(str, Enum):
    ALL = "all"
    RECORDINGS = "recordings"
    SLICES = "slices"


@dataclass(slots=True)
class RetentionProtectionSnapshot:
    protected_files: set[Path] = field(default_factory=set)
    protected_roots: set[Path] = field(default_factory=set)


@dataclass(slots=True)
class RetentionCleanupResult:
    recordings_deleted_files: int = 0
    recordings_deleted_dirs: int = 0
    slices_deleted_files: int = 0
    slices_deleted_dirs: int = 0
    protected_skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total_deleted_files(self) -> int:
        return self.recordings_deleted_files + self.slices_deleted_files

    @property
    def has_changes(self) -> bool:
        return self.total_deleted_files > 0 or bool(self.errors)


def build_protection_snapshot(session_paths: list[tuple[Path, Path]]) -> RetentionProtectionSnapshot:
    protected_files: set[Path] = set()
    protected_roots: set[Path] = set()

    for wav_path, metadata_path in session_paths:
        for path in (wav_path, metadata_path):
            resolved = _resolve_path(path)
            protected_files.add(resolved)
            protected_roots.add(resolved.parent)

    return RetentionProtectionSnapshot(
        protected_files=protected_files,
        protected_roots=protected_roots,
    )


def parse_cleanup_command_args(raw: str) -> RetentionTarget | str:
    remainder = raw.removeprefix("清理").strip().casefold()
    if not remainder or remainder in {"全部", "all"}:
        return RetentionTarget.ALL
    if remainder in {"录音", "recordings", "recording"}:
        return RetentionTarget.RECORDINGS
    if remainder in {"切片", "slices", "slice"}:
        return RetentionTarget.SLICES
    return "未知清理目标。可用：/ts 清理、/ts 清理 录音、/ts 清理 切片"


def format_cleanup_result(result: RetentionCleanupResult) -> str:
    if result.errors and result.total_deleted_files == 0:
        lines = ["TS3 录音文件清理失败："]
        lines.extend(f"- {error}" for error in result.errors)
        return "\n".join(lines)

    lines = ["TS3 录音文件清理完成："]
    lines.append(
        f"- 完整录音：删除 {result.recordings_deleted_files} 个文件，"
        f"{result.recordings_deleted_dirs} 个目录"
    )
    lines.append(
        f"- 切片：删除 {result.slices_deleted_files} 个文件，"
        f"{result.slices_deleted_dirs} 个目录"
    )
    if result.protected_skipped:
        lines.append(f"- 跳过受保护路径：{result.protected_skipped} 个")
    if result.errors:
        lines.append("部分清理失败：")
        lines.extend(f"- {error}" for error in result.errors)
    elif result.total_deleted_files == 0:
        lines.append("没有需要清理的过期文件。")
    return "\n".join(lines)


def run_retention_cleanup(
    *,
    recordings_dir: Path,
    slices_dir: Path,
    recording_retention_days: int,
    slice_retention_days: int,
    protection: RetentionProtectionSnapshot,
    now: datetime,
    target: RetentionTarget = RetentionTarget.ALL,
) -> RetentionCleanupResult:
    result = RetentionCleanupResult()

    if target in {RetentionTarget.ALL, RetentionTarget.RECORDINGS}:
        if recording_retention_days <= 0:
            if target == RetentionTarget.RECORDINGS:
                result.errors.append("未配置 recording_retention_days，无法清理完整录音。")
        else:
            _cleanup_dated_root(
                recordings_dir,
                retention_days=recording_retention_days,
                now=now,
                protection=protection,
                deleted_files=result,
                deleted_dirs_attr="recordings_deleted_dirs",
                files_attr="recordings_deleted_files",
                errors=result.errors,
            )

    if target in {RetentionTarget.ALL, RetentionTarget.SLICES}:
        if slice_retention_days <= 0:
            if target == RetentionTarget.SLICES:
                result.errors.append("未配置 recording_slice_retention_days，无法清理切片。")
        else:
            _cleanup_dated_root(
                slices_dir,
                retention_days=slice_retention_days,
                now=now,
                protection=protection,
                deleted_files=result,
                deleted_dirs_attr="slices_deleted_dirs",
                files_attr="slices_deleted_files",
                errors=result.errors,
            )

    return result


def _cleanup_dated_root(
    root_dir: Path,
    *,
    retention_days: int,
    now: datetime,
    protection: RetentionProtectionSnapshot,
    deleted_files: RetentionCleanupResult,
    deleted_dirs_attr: str,
    files_attr: str,
    errors: list[str],
) -> None:
    try:
        root_dir = _assert_safe_cleanup_root(root_dir)
    except ValueError as exc:
        errors.append(str(exc))
        return

    if not root_dir.is_dir():
        return

    deleted_file_count = getattr(deleted_files, files_attr)
    deleted_dir_count = getattr(deleted_files, deleted_dirs_attr)

    for date_dir in sorted(root_dir.iterdir()):
        if not date_dir.is_dir():
            continue

        try:
            folder_date = datetime.strptime(date_dir.name, "%Y-%m-%d").date()
        except ValueError:
            continue

        age_days = (now.date() - folder_date).days
        if age_days <= retention_days:
            continue

        removed_files, removed_dirs, protected = _delete_tree_respecting_protected(
            date_dir,
            protection=protection,
        )
        deleted_file_count += removed_files
        deleted_dir_count += removed_dirs
        deleted_files.protected_skipped += protected

        if date_dir.exists() and not any(date_dir.iterdir()):
            try:
                date_dir.rmdir()
                deleted_dir_count += 1
            except OSError as exc:
                errors.append(f"无法删除空目录 {date_dir}: {exc}")

    setattr(deleted_files, files_attr, deleted_file_count)
    setattr(deleted_files, deleted_dirs_attr, deleted_dir_count)


def _delete_tree_respecting_protected(
    root: Path,
    *,
    protection: RetentionProtectionSnapshot,
) -> tuple[int, int, int]:
    removed_files = 0
    removed_dirs = 0
    protected_skipped = 0

    for path in sorted(root.rglob("*"), reverse=True):
        resolved = _resolve_path(path)
        if _is_protected(resolved, protection):
            protected_skipped += 1
            continue
        try:
            if path.is_dir():
                path.rmdir()
                removed_dirs += 1
            elif path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
                removed_files += 1
        except OSError:
            continue

    if root.exists() and root.is_dir() and not any(root.iterdir()):
        try:
            shutil.rmtree(root, ignore_errors=False)
            removed_dirs += 1
        except OSError:
            pass

    return removed_files, removed_dirs, protected_skipped


def _is_protected(path: Path, protection: RetentionProtectionSnapshot) -> bool:
    if path in protection.protected_files:
        return True
    for protected_root in protection.protected_roots:
        if _is_same_or_under(path, protected_root):
            return True
    return False


def _is_same_or_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return path == root


def _resolve_path(path: Path) -> Path:
    return path.resolve(strict=False)


def _assert_safe_cleanup_root(root_dir: Path) -> Path:
    resolved = _resolve_path(root_dir)
    if resolved == resolved.anchor:
        raise ValueError(f"拒绝清理根目录：{resolved}")
    if len(resolved.parts) < 2:
        raise ValueError(f"清理目录层级过浅，已拒绝：{resolved}")
    return resolved
