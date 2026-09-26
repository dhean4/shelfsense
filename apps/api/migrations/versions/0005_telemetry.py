"""Devices, telemetry time series (BRIN), anomalies; slug → tenant lookup for ingest.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "shelfsense_app"
CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
CURRENT_ROLE = "NULLIF(current_setting('app.role', true), '')"

WRITERS: dict[str, tuple[str, ...]] = {
    "devices": ("owner", "manager", "system"),
    "telemetry": ("owner", "manager", "system"),
    "anomalies": ("owner", "manager", "system"),
}


def _rls(table: str, writers: tuple[str, ...]) -> None:
    roles = ", ".join(f"'{r}'" for r in writers)
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_read ON {table} FOR SELECT TO {APP_ROLE} "
        f"USING (tenant_id = {CURRENT_TENANT})"
    )
    op.execute(
        f"CREATE POLICY tenant_write ON {table} FOR ALL TO {APP_ROLE} "
        f"USING (tenant_id = {CURRENT_TENANT} AND {CURRENT_ROLE} IN ({roles})) "
        f"WITH CHECK (tenant_id = {CURRENT_TENANT} AND {CURRENT_ROLE} IN ({roles}))"
    )


def upgrade() -> None:
    """Apply."""
    device_kind = sa.Enum("fridge", "vehicle", name="device_kind")
    anomaly_status = sa.Enum("open", "resolved", name="anomaly_status")

    op.create_table(
        "devices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "store_id", sa.Uuid(), sa.ForeignKey("stores.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("kind", device_kind, nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "external_id", name="uq_devices_tenant_external"),
    )
    op.create_index("ix_devices_tenant_id", "devices", ["tenant_id"])
    op.create_index("ix_devices_store_id", "devices", ["store_id"])

    # Plain Postgres for the time series (ADR-0001): a BRIN index on recorded_at keeps
    # range scans cheap on an append-only table without a TimescaleDB dependency.
    op.create_table(
        "telemetry",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "device_id", sa.Uuid(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("temperature_c", sa.Float(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("battery_pct", sa.Float(), nullable=True),
        sa.Column(
            "ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_telemetry_tenant_id", "telemetry", ["tenant_id"])
    op.create_index("ix_telemetry_device_recorded", "telemetry", ["device_id", "recorded_at"])
    op.create_index(
        "brin_telemetry_recorded_at", "telemetry", ["recorded_at"], postgresql_using="brin"
    )

    op.create_table(
        "anomalies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "device_id", sa.Uuid(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", anomaly_status, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("peak_temperature_c", sa.Float(), nullable=True),
        sa.Column(
            "run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_anomalies_tenant_id", "anomalies", ["tenant_id"])
    op.create_index("ix_anomalies_device_id", "anomalies", ["device_id"])
    op.create_index("ix_anomalies_status", "anomalies", ["status"])

    for table, writers in WRITERS.items():
        _rls(table, writers)

    # The MQTT ingester knows a tenant by its slug (from the topic) before it has a tenant
    # context. Same pattern as resolve_tenant_by_org (ADR-0002).
    op.execute(
        """
        CREATE FUNCTION resolve_tenant_by_slug(slug_in text) RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
        $$ SELECT id FROM tenants WHERE slug = slug_in $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION resolve_tenant_by_slug(text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION resolve_tenant_by_slug(text) TO {APP_ROLE}")


def downgrade() -> None:
    """Revert."""
    op.execute("DROP FUNCTION IF EXISTS resolve_tenant_by_slug(text)")
    op.drop_table("anomalies")
    op.drop_table("telemetry")
    op.drop_table("devices")
    op.execute("DROP TYPE IF EXISTS anomaly_status")
    op.execute("DROP TYPE IF EXISTS device_kind")
