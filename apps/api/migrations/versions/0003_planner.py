"""Inventory levels, actions, tool-call log, notifications; planner columns on agent_runs.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "shelfsense_app"
CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
CURRENT_ROLE = "NULLIF(current_setting('app.role', true), '')"
EVERYONE = ("owner", "manager", "field_agent", "reviewer", "system")

WRITERS: dict[str, tuple[str, ...]] = {
    "inventory_levels": ("owner", "manager", "system"),
    # Reviewers approve/reject in P4; field agents' runs create proposals via the worker.
    "actions": ("owner", "manager", "reviewer", "field_agent", "system"),
    "tool_calls": EVERYONE,
    "notifications": EVERYONE,
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
    action_kind = sa.Enum("reorder", "dispatch", "notify", "escalate", name="action_kind")
    action_status = sa.Enum(
        "proposed", "pending_review", "approved", "rejected", "executed", name="action_status"
    )

    op.add_column("agent_runs", sa.Column("trigger_role", sa.String(32), nullable=True))
    op.add_column(
        "agent_runs",
        sa.Column(
            "extraction_id",
            sa.Uuid(),
            sa.ForeignKey("extractions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("agent_runs", sa.Column("summary", postgresql.JSONB(), nullable=True))
    op.create_index("ix_agent_runs_extraction_id", "agent_runs", ["extraction_id"])

    op.create_table(
        "inventory_levels",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "store_id", sa.Uuid(), sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "sku_id", sa.Uuid(), sa.ForeignKey("skus.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("on_hand", sa.Integer(), nullable=False),
        sa.Column("reorder_point", sa.Integer(), nullable=False),
        sa.Column("case_size", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("store_id", "sku_id", name="uq_inventory_store_sku"),
    )
    op.create_index("ix_inventory_levels_tenant_id", "inventory_levels", ["tenant_id"])
    op.create_index("ix_inventory_levels_store_id", "inventory_levels", ["store_id"])
    op.create_index("ix_inventory_levels_sku_id", "inventory_levels", ["sku_id"])

    op.create_table(
        "actions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "store_id", sa.Uuid(), sa.ForeignKey("stores.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("kind", action_kind, nullable=False),
        sa.Column("status", action_status, nullable=False),
        sa.Column("requires_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("estimated_cost_kobo", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("reviewed_by", sa.String(128), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_actions_tenant_id", "actions", ["tenant_id"])
    op.create_index("ix_actions_run_id", "actions", ["run_id"])
    op.create_index("ix_actions_store_id", "actions", ["store_id"])
    op.create_index("ix_actions_status_created", "actions", ["status", "created_at"])

    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("caller_role", sa.String(32), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_tool_calls_tenant_id", "tool_calls", ["tenant_id"])
    op.create_index("ix_tool_calls_run_id", "tool_calls", ["run_id"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_notifications_tenant_id", "notifications", ["tenant_id"])
    op.create_index("ix_notifications_run_id", "notifications", ["run_id"])

    for table, writers in WRITERS.items():
        _rls(table, writers)


def downgrade() -> None:
    """Revert."""
    op.drop_table("notifications")
    op.drop_table("tool_calls")
    op.drop_table("actions")
    op.drop_table("inventory_levels")
    op.drop_index("ix_agent_runs_extraction_id", table_name="agent_runs")
    op.drop_column("agent_runs", "summary")
    op.drop_column("agent_runs", "extraction_id")
    op.drop_column("agent_runs", "trigger_role")
    op.execute("DROP TYPE IF EXISTS action_status")
    op.execute("DROP TYPE IF EXISTS action_kind")
