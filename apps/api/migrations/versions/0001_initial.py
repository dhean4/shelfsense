"""Initial schema: tenants, users, stores, shelves, skus, planograms; RLS on every tenant table.

Revision ID: 0001
Revises:
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "shelfsense_app"

# Tables whose rows belong to exactly one tenant. Order matters for FK creation.
TENANT_TABLES = ("users", "stores", "shelves", "skus", "planograms", "planogram_slots")

# NULLIF guards against an empty string left by a misbehaving client; an unset or empty
# setting therefore matches no tenant and the query sees nothing (fail closed).
CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
CURRENT_ROLE = "NULLIF(current_setting('app.role', true), '')"


def upgrade() -> None:
    """Apply."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    user_role = sa.Enum("owner", "manager", "field_agent", "reviewer", name="user_role")

    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("external_org_id", sa.String(128), nullable=True, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "external_id", name="uq_users_tenant_external"),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])

    op.create_table(
        "stores",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("address", sa.String(500), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_stores_tenant_name"),
    )
    op.create_index("ix_stores_tenant_id", "stores", ["tenant_id"])

    op.create_table(
        "shelves",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "store_id", sa.Uuid(), sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("store_id", "label", name="uq_shelves_store_label"),
    )
    op.create_index("ix_shelves_tenant_id", "shelves", ["tenant_id"])
    op.create_index("ix_shelves_store_id", "shelves", ["store_id"])

    op.create_table(
        "skus",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("brand", sa.String(100), nullable=False),
        sa.Column("barcode", sa.String(32), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("unit_price_kobo", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "barcode", name="uq_skus_tenant_barcode"),
    )
    op.create_index("ix_skus_tenant_id", "skus", ["tenant_id"])
    op.create_index("ix_skus_category", "skus", ["category"])

    op.create_table(
        "planograms",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "shelf_id",
            sa.Uuid(),
            sa.ForeignKey("shelves.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_planograms_tenant_id", "planograms", ["tenant_id"])

    op.create_table(
        "planogram_slots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "planogram_id",
            sa.Uuid(),
            sa.ForeignKey("planograms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sku_id", sa.Uuid(), sa.ForeignKey("skus.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("expected_facings", sa.Integer(), nullable=False),
        sa.Column("min_facings", sa.Integer(), nullable=False),
        sa.UniqueConstraint("planogram_id", "position", name="uq_planogram_slots_position"),
    )
    op.create_index("ix_planogram_slots_tenant_id", "planogram_slots", ["tenant_id"])
    op.create_index("ix_planogram_slots_planogram_id", "planogram_slots", ["planogram_id"])
    op.create_index("ix_planogram_slots_sku_id", "planogram_slots", ["sku_id"])

    # --- application role and privileges ----------------------------------------------
    # The role itself is created by infra/postgres/init.sql (dev) or the operator (prod);
    # a migration must not own a password.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
    )

    # --- row-level security -----------------------------------------------------------
    # FORCE makes the policies apply to the table owner too; only superusers bypass.
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_self ON tenants FOR SELECT TO {APP_ROLE} "
        f"USING (id = {CURRENT_TENANT})"
    )

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_read ON {table} FOR SELECT TO {APP_ROLE} "
            f"USING (tenant_id = {CURRENT_TENANT})"
        )
        # Writes additionally require a writing role; the API checks this first and
        # returns 403, the policy is defence in depth.
        op.execute(
            f"CREATE POLICY tenant_write ON {table} FOR ALL TO {APP_ROLE} "
            f"USING (tenant_id = {CURRENT_TENANT} AND {CURRENT_ROLE} IN ('owner', 'manager')) "
            f"WITH CHECK (tenant_id = {CURRENT_TENANT} AND {CURRENT_ROLE} IN ('owner', 'manager'))"
        )

    # Resolves an identity-provider organisation to a tenant before any tenant context
    # exists. SECURITY DEFINER runs as the owner and so bypasses RLS for this one lookup.
    op.execute(
        """
        CREATE FUNCTION resolve_tenant_by_org(org text) RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
        $$ SELECT id FROM tenants WHERE external_org_id = org $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION resolve_tenant_by_org(text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION resolve_tenant_by_org(text) TO {APP_ROLE}")


def downgrade() -> None:
    """Revert."""
    op.execute("DROP FUNCTION IF EXISTS resolve_tenant_by_org(text)")
    for table in reversed(TENANT_TABLES):
        op.drop_table(table)
    op.drop_table("tenants")
    op.execute("DROP TYPE IF EXISTS user_role")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {APP_ROLE}"
    )
