"""Extraction reviews (labelled examples) and the golden set.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "shelfsense_app"
CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
CURRENT_ROLE = "NULLIF(current_setting('app.role', true), '')"

WRITERS: dict[str, tuple[str, ...]] = {
    "extraction_reviews": ("owner", "manager", "reviewer", "system"),
    "golden_cases": ("owner", "manager", "reviewer", "system"),
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
    verdict = sa.Enum("correct", "corrected", "unusable", name="review_verdict")

    op.create_table(
        "extraction_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "extraction_id",
            sa.Uuid(),
            sa.ForeignKey("extractions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "photo_id", sa.Uuid(), sa.ForeignKey("photos.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("reviewer", sa.String(128), nullable=False),
        sa.Column("verdict", verdict, nullable=False),
        sa.Column("corrected", postgresql.JSONB(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_extraction_reviews_tenant_id", "extraction_reviews", ["tenant_id"])
    op.create_index("ix_extraction_reviews_extraction_id", "extraction_reviews", ["extraction_id"])
    op.create_index("ix_extraction_reviews_photo_id", "extraction_reviews", ["photo_id"])

    op.create_table(
        "golden_cases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "review_id",
            sa.Uuid(),
            sa.ForeignKey("extraction_reviews.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
        ),
        sa.Column(
            "photo_id", sa.Uuid(), sa.ForeignKey("photos.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("planogram", postgresql.JSONB(), nullable=False),
        sa.Column("expected", postgresql.JSONB(), nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.String(64)), nullable=False, server_default="{}"),
        sa.Column("promoted_by", sa.String(128), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_golden_cases_tenant_id", "golden_cases", ["tenant_id"])
    op.create_index("ix_golden_cases_photo_id", "golden_cases", ["photo_id"])

    for table, writers in WRITERS.items():
        _rls(table, writers)


def downgrade() -> None:
    """Revert."""
    op.drop_table("golden_cases")
    op.drop_table("extraction_reviews")
    op.execute("DROP TYPE IF EXISTS review_verdict")
