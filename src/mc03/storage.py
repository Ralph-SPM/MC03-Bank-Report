"""Protected local-storage path validation for MC03 startup.

Persistence is single-host and local. Every persistence path must resolve at or
below a single protected storage root; this module deliberately fails closed for
paths that resolve outside that root and for known network filesystems and
UNC/remote paths. All validation runs in ``build_protected_storage_paths`` and
completes before ``prepare_protected_storage`` creates any directory, so an
invalid or unsafe path is rejected before any runtime storage directory exists.

Directories are created with owner-only read/write/execute permissions. POSIX
honors this via ``chmod(0o700)``; on Windows the mode bits are advisory, so
owner-only enforcement there relies on the platform's own filesystem ACLs.
"""

from __future__ import annotations

import ctypes
import os
import platform
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

NETWORK_FILESYSTEM_TYPES = frozenset(
    {
        "9p",
        "afs",
        "ceph",
        "cifs",
        "davfs",
        "fuse.sshfs",
        "gcsfuse",
        "lustre",
        "nfs",
        "nfs4",
        "ocfs2",
        "smbfs",
        "sshfs",
    }
)


class ProtectedStorageError(ValueError):
    """Raised when persistence would not be confined to a local protected volume."""


@dataclass(frozen=True, slots=True)
class ProtectedStoragePaths:
    """All persistence paths used by the initial single-host deployment."""

    root: Path
    database: Path
    raw_sources: Path
    artifacts: Path
    templates: Path
    fixed_file_lock: Path

    @property
    def directories(self) -> tuple[Path, ...]:
        return (self.root, self.raw_sources, self.artifacts, self.templates)

    @property
    def files(self) -> tuple[Path, ...]:
        return (self.database, self.fixed_file_lock)


def _unescape_mountinfo(value: str) -> str:
    """Decode the octal escapes used by Linux mountinfo files."""
    return value.replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")


def _mount_entries(
    mountinfo_path: Path = Path("/proc/self/mountinfo"),
) -> Iterable[tuple[Path, str]]:
    """Yield mounted paths and filesystem types from Linux mountinfo."""
    try:
        lines = mountinfo_path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError, UnicodeError):
        return ()

    entries: list[tuple[Path, str]] = []
    for line in lines:
        left, separator, right = line.partition(" - ")
        if not separator:
            continue
        left_fields = left.split()
        right_fields = right.split()
        if len(left_fields) < 5 or not right_fields:
            continue
        mountpoint = Path(_unescape_mountinfo(left_fields[4])).resolve(strict=False)
        entries.append((mountpoint, right_fields[0].lower()))
    return entries


def _nearest_existing_path(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate.resolve(strict=False)


def _is_unc_path(path: Path) -> bool:
    raw = os.fspath(path)
    return raw.startswith("\\\\") or raw.startswith("//")


def _is_windows_remote_drive(path: Path) -> bool:
    """Return whether Windows identifies the path's drive as a remote drive."""
    if platform.system().lower() != "windows":
        return False

    drive, _ = os.path.splitdrive(os.fspath(path))
    if not drive:
        return False

    try:
        drive_type = ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\")
    except (AttributeError, OSError):
        # UNC paths are rejected separately. If the API is unavailable, retain
        # local-path compatibility rather than treating every drive as remote.
        return False
    return cast(int, drive_type) == 4  # DRIVE_REMOTE


def is_network_mounted(path: Path, mountinfo_path: Path = Path("/proc/self/mountinfo")) -> bool:
    """Return whether *path* is on a known network filesystem or remote drive."""
    if _is_unc_path(path) or _is_windows_remote_drive(path):
        return True

    resolved = _nearest_existing_path(path)
    matching_mounts = (
        (mountpoint, filesystem)
        for mountpoint, filesystem in _mount_entries(mountinfo_path)
        if resolved == mountpoint or mountpoint in resolved.parents
    )
    return any(filesystem in NETWORK_FILESYSTEM_TYPES for _, filesystem in matching_mounts)


def _normalize_path(value: Path | str, root: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve(strict=False)


def build_protected_storage_paths(
    storage_root: Path | str,
    *,
    database: Path | str | None = None,
    raw_sources: Path | str | None = None,
    artifacts: Path | str | None = None,
    templates: Path | str | None = None,
    fixed_file_lock: Path | str | None = None,
    mountinfo_path: Path = Path("/proc/self/mountinfo"),
) -> ProtectedStoragePaths:
    """Normalize, validate, and return the complete local storage boundary.

    All paths are resolved before validation. Relative component paths are
    relative to ``storage_root``; missing paths are supported because startup
    creates them only after every path has passed validation.
    """
    raw_root = Path(storage_root).expanduser()
    if _is_unc_path(raw_root):
        raise ProtectedStorageError(
            f"network-mounted storage is not supported for MC03 persistence: {raw_root}"
        )
    root = raw_root.resolve(strict=False)
    values = {
        "database": database if database is not None else root / "mc03.sqlite3",
        "raw_sources": raw_sources if raw_sources is not None else root / "raw_sources",
        "artifacts": artifacts if artifacts is not None else root / "artifacts",
        "templates": templates if templates is not None else root / "templates",
        "fixed_file_lock": fixed_file_lock
        if fixed_file_lock is not None
        else root / "generation.lock",
    }
    paths = ProtectedStoragePaths(
        root=root,
        database=_normalize_path(values["database"], root),
        raw_sources=_normalize_path(values["raw_sources"], root),
        artifacts=_normalize_path(values["artifacts"], root),
        templates=_normalize_path(values["templates"], root),
        fixed_file_lock=_normalize_path(values["fixed_file_lock"], root),
    )

    if _is_unc_path(root) or is_network_mounted(root, mountinfo_path):
        raise ProtectedStorageError(
            f"network-mounted storage is not supported for MC03 persistence: {root}"
        )

    for name, path in (
        ("database", paths.database),
        ("raw_sources", paths.raw_sources),
        ("artifacts", paths.artifacts),
        ("templates", paths.templates),
        ("fixed_file_lock", paths.fixed_file_lock),
    ):
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ProtectedStorageError(
                f"{name} path must remain below the protected storage root: {path}"
            ) from exc
        if is_network_mounted(path, mountinfo_path):
            raise ProtectedStorageError(
                f"network-mounted storage is not supported for MC03 persistence: {path}"
            )

    return paths


def prepare_protected_storage(paths: ProtectedStoragePaths) -> None:
    """Create protected directories after path validation has completed."""
    for directory in paths.directories:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            directory.chmod(0o700)
