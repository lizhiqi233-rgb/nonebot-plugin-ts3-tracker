from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

MANAGED_RECORDING_SUFFIXES = {".json", ".wav"}


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
    unmanaged_skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total_deleted_files(self) -> int:
        return self.recordings_deleted_files + self.slices_deleted_files

    @property
    def has_changes(self) -> bool:
        return (
            self.total_deleted_files > 0
            or self.unmanaged_skipped > 0
            or bool(self.errors)
        )


def build_protection_snapshot(
    session_paths: list[tuple[Path, Path]],
) -> RetentionProtectionSnapshot:
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
    if result.unmanaged_skipped:
        lines.append(f"- 保留非插件文件或链接：{result.unmanaged_skipped} 个")
    if result.errors:
        lines.append("部分清理失败：")
        lines.extend(f"- {error}" for error in result.errors)
    elif result.total_deleted_files == 0 and not result.unmanaged_skipped:
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

    try:
        recordings_root = _assert_safe_cleanup_root(recordings_dir)
        slices_root = _assert_safe_cleanup_root(slices_dir)
    except ValueError as exc:
        result.errors.append(str(exc))
        return result
    if _cleanup_roots_overlap(recordings_root, slices_root):
        result.errors.append(
            "完整录音与切片清理目录不能相同或互相包含："
            f"{recordings_root} / {slices_root}"
        )
        return result

    if target in {RetentionTarget.ALL, RetentionTarget.RECORDINGS}:
        if recording_retention_days <= 0:
            if target == RetentionTarget.RECORDINGS:
                result.errors.append(
                    "未配置 recording_retention_days，无法清理完整录音。"
                )
        else:
            _cleanup_dated_root(
                recordings_root,
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
                result.errors.append(
                    "未配置 recording_slice_retention_days，无法清理切片。"
                )
        else:
            _cleanup_dated_root(
                slices_root,
                retention_days=slice_retention_days,
                now=now,
                protection=protection,
                deleted_files=result,
                deleted_dirs_attr="slices_deleted_dirs",
                files_attr="slices_deleted_files",
                errors=result.errors,
            )

    return result


def _cleanup_roots_overlap(first: Path, second: Path) -> bool:
    return _is_same_or_under(first, second) or _is_same_or_under(second, first)


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
    if not root_dir.is_dir():
        return

    deleted_file_count = getattr(deleted_files, files_attr)
    deleted_dir_count = getattr(deleted_files, deleted_dirs_attr)

    try:
        date_dirs = sorted(root_dir.iterdir())
    except OSError as exc:
        errors.append(f"无法读取清理目录 {root_dir}: {exc}")
        return

    for date_dir in date_dirs:
        if not date_dir.is_dir():
            continue

        try:
            folder_date = datetime.strptime(date_dir.name, "%Y-%m-%d").date()
        except ValueError:
            continue

        age_days = (now.date() - folder_date).days
        if age_days <= retention_days:
            continue

        removed_files, removed_dirs, protected, unmanaged = (
            _delete_tree_respecting_protected(
                date_dir,
                protection=protection,
                errors=errors,
            )
        )
        deleted_file_count += removed_files
        deleted_dir_count += removed_dirs
        deleted_files.protected_skipped += protected
        deleted_files.unmanaged_skipped += unmanaged

        try:
            if date_dir.exists() and not any(date_dir.iterdir()):
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
    errors: list[str],
) -> tuple[int, int, int, int]:
    removed_files = 0
    removed_dirs = 0
    protected_skipped = 0
    unmanaged_skipped = 0

    try:
        paths = sorted(root.rglob("*"), reverse=True)
    except OSError as exc:
        errors.append(f"无法遍历清理目录 {root}: {exc}")
        return removed_files, removed_dirs, protected_skipped, unmanaged_skipped

    for path in paths:
        resolved = _resolve_path(path)
        if _is_protected(resolved, protection):
            protected_skipped += 1
            continue
        try:
            if path.is_symlink():
                unmanaged_skipped += 1
            elif path.is_dir():
                if any(path.iterdir()):
                    continue
                path.rmdir()
                removed_dirs += 1
            elif path.is_file():
                if path.suffix.casefold() not in MANAGED_RECORDING_SUFFIXES:
                    unmanaged_skipped += 1
                    continue
                path.unlink(missing_ok=True)
                removed_files += 1
        except OSError as exc:
            errors.append(f"无法删除 {path}: {exc}")

    return removed_files, removed_dirs, protected_skipped, unmanaged_skipped


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
        return False


def _resolve_path(path: Path) -> Path:
    return path.resolve(strict=False)


def _assert_safe_cleanup_root(root_dir: Path) -> Path:
    resolved = _resolve_path(root_dir)
    if resolved == Path(resolved.anchor):
        raise ValueError(f"拒绝清理根目录：{resolved}")
    if len(resolved.parts) < 2:
        raise ValueError(f"清理目录层级过浅，已拒绝：{resolved}")
    return resolved
