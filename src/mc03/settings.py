"""Typed runtime configuration and the MC03 startup boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from mc03.storage import (
    ProtectedStorageError,
    ProtectedStoragePaths,
    build_protected_storage_paths,
    prepare_protected_storage,
)

PUBLIC_WEBHOOK_ROUTE = "/webhooks/viber"


class ServiceEndpoint(BaseModel):
    """Bind address for one independently supervised service."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    host: str = "127.0.0.1"
    port: PositiveInt = Field(default=8000, le=65535)


class RuntimeSettings(BaseSettings):
    """Validated settings loaded at process startup.

    The persistence paths are intentionally flat and explicit so deployment
    configuration cannot hide database, source, artifact, template, or lock
    locations inside an opaque application setting.
    """

    model_config = SettingsConfigDict(
        env_prefix="MC03_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        case_sensitive=False,
    )

    environment: Literal["development", "test", "production"] = "development"
    public_listener: ServiceEndpoint = Field(
        default_factory=lambda: ServiceEndpoint(host="127.0.0.1", port=8001)
    )
    private_portal: ServiceEndpoint = Field(
        default_factory=lambda: ServiceEndpoint(host="127.0.0.1", port=8000)
    )
    runner_id: str = Field(default="local-runner", min_length=1, max_length=128)

    storage_root: Path = Path("var/mc03")
    database_path: Path | None = None
    raw_source_path: Path | None = None
    artifact_path: Path | None = None
    template_path: Path | None = None
    fixed_file_lock_path: Path | None = None
    stitch_api_key: str | None = None
    groq_api_key: str | None = None
    groq_model: str | None = None
    groq_batch_processing: str | None = None
    groq_request_timeout: str | None = None

    def __init__(self, **values: Any) -> None:
        """Preserve the storage-boundary exception at the startup API."""
        try:
            super().__init__(**values)
        except ValidationError as exc:
            message = str(exc)
            if "protected storage root" in message or "network-mounted" in message:
                raise ProtectedStorageError(message) from exc
            raise

    @model_validator(mode="after")
    def validate_runtime_boundary(self) -> RuntimeSettings:
        paths = build_protected_storage_paths(
            self.storage_root,
            database=self.database_path,
            raw_sources=self.raw_source_path,
            artifacts=self.artifact_path,
            templates=self.template_path,
            fixed_file_lock=self.fixed_file_lock_path,
        )
        object.__setattr__(self, "storage_root", paths.root)
        object.__setattr__(self, "database_path", paths.database)
        object.__setattr__(self, "raw_source_path", paths.raw_sources)
        object.__setattr__(self, "artifact_path", paths.artifacts)
        object.__setattr__(self, "template_path", paths.templates)
        object.__setattr__(self, "fixed_file_lock_path", paths.fixed_file_lock)
        return self

    @property
    def demo_bind(self) -> ServiceEndpoint:
        """Return the single loopback bind target for the RCBC demo portal.

        The on-demand demo binds exclusively to ``127.0.0.1:8000`` and does not
        mount the parent spec's public Viber webhook listener. This accessor
        names that boundary explicitly so the web layer never binds to a
        non-loopback interface or an unexpected port.
        """
        return self.private_portal

    @property
    def protected_storage(self) -> ProtectedStoragePaths:
        """Return the complete validated local-storage path set."""
        # The model validator has already normalized every path. Rebuilding
        # keeps this property defensive if a caller bypasses model validation.
        return build_protected_storage_paths(
            self.storage_root,
            database=self.database_path,
            raw_sources=self.raw_source_path,
            artifacts=self.artifact_path,
            templates=self.template_path,
            fixed_file_lock=self.fixed_file_lock_path,
        )

    def initialize_storage(self) -> ProtectedStoragePaths:
        """Validate again, then create the protected local directories."""
        paths = self.protected_storage
        prepare_protected_storage(paths)
        return paths


def load_runtime_settings() -> RuntimeSettings:
    """Load and validate process settings before service startup."""
    return RuntimeSettings()
