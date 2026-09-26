"""Photos, agent runs and extractions, with RLS.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "shelfsense_app"
CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
CURRENT_ROLE = "NULLIF(current_setting('app.role', true), '')"

# Table → roles allowed to write. ``system`` is the worker; field agents upload photos.
WRITERS: dict[str, tuple[str, ...]] = {
    "photos": ("owner", "manager", "field_agent", "system"),
    "agent_runs": ("owner", "manager", "system"),
    "extractions": ("owner", "manager", "system"),
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
    photo_status = sa.Enum("queued", "processing", "done", "failed", name="photo_status")
    run_status = sa.Enum("queued", "running", "succeeded", "failed", name="run_status")

    op.create_table(
        "photos",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "shelf_id", sa.Uuid(), sa.ForeignKey("shelves.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("uploaded_by", sa.String(128), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False, unique=True),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("status", photo_status, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_photos_tenant_id", "photos", ["tenant_id"])
    op.create_index("ix_photos_shelf_id", "photos", ["shelf_id"])
    op.create_index("ix_photos_status", "photos", ["status"])

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", run_status, nullable=False),
        sa.Column(
            "photo_id", sa.Uuid(), sa.ForeignKey("photos.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_runs_tenant_id", "agent_runs", ["tenant_id"])
    op.create_index("ix_agent_runs_photo_id", "agent_runs", ["photo_id"])
    op.create_index("ix_agent_runs_kind_started", "agent_runs", ["kind", "started_at"])

    op.create_table(
        "extractions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "photo_id",
            sa.Uuid(),
            sa.ForeignKey("photos.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("planogram_version", sa.Integer(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("overall_confidence", sa.Float(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_extractions_tenant_id", "extractions", ["tenant_id"])
    op.create_index("ix_extractions_run_id", "extractions", ["run_id"])

    for table, writers in WRITERS.items():
        _rls(table, writers)


def downgrade() -> None:
    """Revert."""
    op.drop_table("extractions")
    op.drop_table("agent_runs")
    op.drop_table("photos")
    op.execute("DROP TYPE IF EXISTS run_status")
    op.execute("DROP TYPE IF EXISTS photo_status")
