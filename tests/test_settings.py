"""Focused tests for the task 1.1 runtime configuration boundary."""

from pathlib import Path

import pytest

from mc03.settings import PUBLIC_WEBHOOK_ROUTE, RuntimeSettings
from mc03.storage import ProtectedStorageError, build_protected_storage_paths


def test_default_paths_are_explicit_and_below_one_local_root(local_storage_root: Path) -> None:
    settings = RuntimeSettings(storage_root=local_storage_root)

    assert settings.public_listener.port == 8001
    assert settings.private_portal.port == 8000
    assert settings.protected_storage.root == local_storage_root.resolve()
    assert settings.database_path == local_storage_root.resolve() / "mc03.sqlite3"
    assert settings.raw_source_path == local_storage_root.resolve() / "raw_sources"
    assert settings.artifact_path == local_storage_root.resolve() / "artifacts"
    assert settings.template_path == local_storage_root.resolve() / "templates"
    assert settings.fixed_file_lock_path == local_storage_root.resolve() / "generation.lock"
    assert PUBLIC_WEBHOOK_ROUTE == "/webhooks/viber"


def test_initialize_storage_creates_only_protected_directories(local_storage_root: Path) -> None:
    settings = RuntimeSettings(storage_root=local_storage_root)
    paths = settings.initialize_storage()

    assert all(path.is_dir() for path in paths.directories)
    assert not paths.database.exists()
    assert not paths.fixed_file_lock.exists()


def test_component_path_outside_root_is_rejected(local_storage_root: Path, tmp_path: Path) -> None:
    with pytest.raises(ProtectedStorageError, match="below the protected storage root"):
        RuntimeSettings(
            storage_root=local_storage_root,
            database_path=tmp_path / "outside.sqlite3",
        )


def test_unc_storage_root_is_rejected() -> None:
    with pytest.raises(ProtectedStorageError, match="network-mounted"):
        build_protected_storage_paths(r"\\server\share\mc03")


def test_known_network_mount_is_rejected(tmp_path: Path) -> None:
    network_root = tmp_path / "network-root"
    network_root.mkdir()
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        f"42 1 0:42 / {network_root} rw,relatime - nfs4 server:/export rw\n",
        encoding="utf-8",
    )

    with pytest.raises(ProtectedStorageError, match="network-mounted"):
        build_protected_storage_paths(network_root, mountinfo_path=mountinfo)


def test_settings_reject_unknown_fields(local_storage_root: Path) -> None:
    with pytest.raises(ValueError):
        RuntimeSettings(storage_root=local_storage_root, unsupported_setting=True)
