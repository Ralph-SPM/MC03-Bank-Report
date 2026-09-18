"""Immutable Campaign_Configuration version store keyed by exact bytes/hash.

Stores validated configuration versions as append-only evidence. A version is
uniquely identified by ``(campaign_code, version)`` and by ``Config_Hash``;
once stored, its canonical bytes and hash are never updated. Re-adding an
identical version is idempotent; re-adding the same identity with different
bytes is rejected as an attempted immutable alteration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from mc03.domain.errors import ImmutableMutationError
from mc03.domain.identifiers import ConfigHash
from mc03.persistence.models import CampaignConfigurationVersionModel
from mc03.services.config_parser import ParsedConfiguration


@dataclass(frozen=True, slots=True)
class StoredConfigurationVersion:
    """Immutable stored configuration-version evidence."""

    configuration_id: str
    campaign_code: str
    version: str
    canonical_bytes: bytes
    config_hash: ConfigHash
    validated_at: datetime


class ConfigurationVersionStore:
    """Append-only repository for validated configuration versions."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(
        self,
        parsed: ParsedConfiguration,
        *,
        validated_at: datetime | None = None,
    ) -> StoredConfigurationVersion:
        """Store a validated version, or return the identical existing version.

        Rejects an attempt to store the same ``(campaign_code, version)`` with
        different canonical bytes, preserving the earlier immutable evidence.
        """
        configuration = parsed.configuration
        existing = self._find(configuration.campaign_code, configuration.version)
        if existing is not None:
            if existing.canonical_bytes != parsed.canonical_bytes:
                raise ImmutableMutationError(
                    "campaign_configuration_versions",
                    f"{configuration.campaign_code}:{configuration.version}",
                )
            return self._to_value(existing)

        model = CampaignConfigurationVersionModel(
            campaign_code=configuration.campaign_code,
            version=configuration.version,
            canonical_bytes=parsed.canonical_bytes,
            config_hash=str(parsed.config_hash),
            parsed_json=json.dumps(
                configuration.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            validated_at=validated_at or datetime.now(UTC),
        )
        self.session.add(model)
        self.session.flush()
        return self._to_value(model)

    def get(self, campaign_code: str, version: str) -> StoredConfigurationVersion | None:
        """Return a stored configuration version by identity, if present."""
        model = self._find(campaign_code, version)
        return self._to_value(model) if model is not None else None

    def get_by_hash(self, config_hash: ConfigHash | str) -> StoredConfigurationVersion | None:
        """Return a stored configuration version by its exact ``Config_Hash``."""
        model = self.session.scalar(
            select(CampaignConfigurationVersionModel).where(
                CampaignConfigurationVersionModel.config_hash == str(config_hash)
            )
        )
        return self._to_value(model) if model is not None else None

    def _find(
        self, campaign_code: str, version: str
    ) -> CampaignConfigurationVersionModel | None:
        return self.session.scalar(
            select(CampaignConfigurationVersionModel).where(
                CampaignConfigurationVersionModel.campaign_code == campaign_code,
                CampaignConfigurationVersionModel.version == version,
            )
        )

    @staticmethod
    def _to_value(
        model: CampaignConfigurationVersionModel,
    ) -> StoredConfigurationVersion:
        return StoredConfigurationVersion(
            configuration_id=model.configuration_id,
            campaign_code=model.campaign_code,
            version=model.version,
            canonical_bytes=model.canonical_bytes,
            config_hash=ConfigHash(model.config_hash),
            validated_at=model.validated_at,
        )


__all__ = ["ConfigurationVersionStore", "StoredConfigurationVersion"]
