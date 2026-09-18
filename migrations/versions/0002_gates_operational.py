"""Add operational configuration and production-gate evidence tables."""

from __future__ import annotations

from alembic import op

from mc03.persistence.immutability import install_database_guards
from mc03.persistence.models import (
    GateEvidenceModel,
    OperationalConfigurationSnapshotModel,
    OperationalConfigurationVersionModel,
)

revision = "0002_gates_operational"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None

_TABLES = (
    OperationalConfigurationVersionModel.__table__,
    OperationalConfigurationSnapshotModel.__table__,
    GateEvidenceModel.__table__,
)


def upgrade() -> None:
    """Create operational-config and gate-evidence tables plus append-only guards."""
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)
    install_database_guards(bind)


def downgrade() -> None:
    """Drop operational-config and gate-evidence tables for dev/test databases."""
    bind = op.get_bind()
    for trigger in (
        "prevent_gate_evidence_update",
        "prevent_gate_evidence_delete",
        "prevent_operational_configuration_versions_update",
        "prevent_operational_configuration_versions_delete",
    ):
        bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
