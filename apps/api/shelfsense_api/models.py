"""SQLAlchemy models. Every tenant-owned table carries ``tenant_id`` and is under RLS.

Schema changes go through Alembic; the integration test ``test_migrations.py`` fails if
these models and the migrations drift apart.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column, relationship

from shelfsense_api.guardrails import ActionKind


class Role(enum.StrEnum):
    """Tenant roles. Ordered from most to least privileged for documentation only."""

    owner = "owner"
    manager = "manager"
    field_agent = "field_agent"
    reviewer = "reviewer"


WRITE_ROLES: frozenset[Role] = frozenset({Role.owner, Role.manager})

ROLE_ENUM = Enum(
    Role,
    name="user_role",
    values_callable=lambda e: [member.value for member in e],
)


class Base(DeclarativeBase):
    """Declarative base shared by every model."""


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TenantScoped:
    """Mixin adding the ``tenant_id`` column that every RLS policy keys on."""

    @declared_attr
    @classmethod
    def tenant_id(cls) -> Mapped[uuid.UUID]:
        """Owning tenant; indexed because every policy and most queries filter on it."""
        return mapped_column(
            ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
        )


class Tenant(Base):
    """A distributor or retailer organisation. Maps to a Clerk organisation."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    external_org_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = _created_at()


class User(TenantScoped, Base):
    """A person in a tenant. ``external_id`` is the identity provider's subject."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_id", name="uq_users_tenant_external"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    external_id: Mapped[str] = mapped_column(String(128))
    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[Role] = mapped_column(ROLE_ENUM)
    created_at: Mapped[datetime] = _created_at()


class Store(TenantScoped, Base):
    """A physical outlet with shelves and (from P5) fridges."""

    __tablename__ = "stores"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_stores_tenant_name"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(500))
    latitude: Mapped[float]
    longitude: Mapped[float]
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    shelves: Mapped[list["Shelf"]] = relationship(
        back_populates="store", cascade="all, delete-orphan"
    )


class Shelf(TenantScoped, Base):
    """A shelf or chiller inside a store, photographed by field agents."""

    __tablename__ = "shelves"
    __table_args__ = (UniqueConstraint("store_id", "label", name="uq_shelves_store_label"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    store: Mapped[Store] = relationship(back_populates="shelves")
    planogram: Mapped["Planogram | None"] = relationship(
        back_populates="shelf", uselist=False, cascade="all, delete-orphan"
    )


class Sku(TenantScoped, Base):
    """A stock-keeping unit the tenant distributes."""

    __tablename__ = "skus"
    __table_args__ = (UniqueConstraint("tenant_id", "barcode", name="uq_skus_tenant_barcode"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    brand: Mapped[str] = mapped_column(String(100))
    barcode: Mapped[str] = mapped_column(String(32))
    category: Mapped[str] = mapped_column(String(64), index=True)
    unit_price_kobo: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Planogram(TenantScoped, Base):
    """What a shelf is supposed to hold. One per shelf; ``version`` bumps on every PUT."""

    __tablename__ = "planograms"

    id: Mapped[uuid.UUID] = _uuid_pk()
    shelf_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shelves.id", ondelete="CASCADE"), unique=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = _updated_at()

    shelf: Mapped[Shelf] = relationship(back_populates="planogram")
    slots: Mapped[list["PlanogramSlot"]] = relationship(
        back_populates="planogram",
        cascade="all, delete-orphan",
        order_by="PlanogramSlot.position",
    )


class PlanogramSlot(TenantScoped, Base):
    """One SKU's expected place and facings on a planogram."""

    __tablename__ = "planogram_slots"
    __table_args__ = (
        UniqueConstraint("planogram_id", "position", name="uq_planogram_slots_position"),
        Index("ix_planogram_slots_sku_id", "sku_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    planogram_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("planograms.id", ondelete="CASCADE"), index=True
    )
    sku_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skus.id", ondelete="RESTRICT"))
    position: Mapped[int] = mapped_column(Integer)
    expected_facings: Mapped[int] = mapped_column(Integer)
    min_facings: Mapped[int] = mapped_column(Integer)

    planogram: Mapped[Planogram] = relationship(back_populates="slots")
    sku: Mapped[Sku] = relationship()


class PhotoStatus(enum.StrEnum):
    """Where a photo is in the pipeline."""

    queued = "queued"
    processing = "processing"
    done = "done"
    failed = "failed"


class RunStatus(enum.StrEnum):
    """Lifecycle of an agent run."""

    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


PHOTO_STATUS_ENUM = Enum(
    PhotoStatus, name="photo_status", values_callable=lambda e: [m.value for m in e]
)
RUN_STATUS_ENUM = Enum(RunStatus, name="run_status", values_callable=lambda e: [m.value for m in e])


class Photo(TenantScoped, Base):
    """An uploaded shelf photo. The bytes live in object storage under ``object_key``."""

    __tablename__ = "photos"

    id: Mapped[uuid.UUID] = _uuid_pk()
    shelf_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shelves.id", ondelete="CASCADE"), index=True
    )
    uploaded_by: Mapped[str] = mapped_column(String(128))
    object_key: Mapped[str] = mapped_column(String(512), unique=True)
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    status: Mapped[PhotoStatus] = mapped_column(PHOTO_STATUS_ENUM, index=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    extraction: Mapped["Extraction | None"] = relationship(
        back_populates="photo", uselist=False, cascade="all, delete-orphan"
    )


class AgentRun(TenantScoped, Base):
    """One agent invocation: tokens, cost, latency, outcome. The unit of observability."""

    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_kind_started", "kind", "started_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[RunStatus] = mapped_column(RUN_STATUS_ENUM)
    photo_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("photos.id", ondelete="SET NULL"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = _created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Planner runs (P3): who triggered it, which extraction it acted on, what it decided.
    trigger_role: Mapped[str | None] = mapped_column(String(32))
    extraction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("extractions.id", ondelete="SET NULL"), index=True
    )
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    tool_calls: Mapped[list["ToolCallLog"]] = relationship(
        back_populates="run", order_by="ToolCallLog.seq", cascade="all, delete-orphan"
    )
    actions: Mapped[list["Action"]] = relationship(
        back_populates="run", order_by="Action.created_at"
    )


class Extraction(TenantScoped, Base):
    """The validated vision output for one photo, plus the derived summary."""

    __tablename__ = "extractions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    photo_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("photos.id", ondelete="CASCADE"), unique=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    planogram_version: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    overall_confidence: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = _created_at()

    photo: Mapped[Photo] = relationship(back_populates="extraction")
    run: Mapped[AgentRun] = relationship(foreign_keys=[run_id])


class ActionStatus(enum.StrEnum):
    """Lifecycle of a planner decision."""

    proposed = "proposed"
    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"
    executed = "executed"


ACTION_KIND_ENUM = Enum(
    ActionKind, name="action_kind", values_callable=lambda e: [m.value for m in e]
)
ACTION_STATUS_ENUM = Enum(
    ActionStatus, name="action_status", values_callable=lambda e: [m.value for m in e]
)


class InventoryLevel(TenantScoped, Base):
    """Stock position of one SKU at one store."""

    __tablename__ = "inventory_levels"
    __table_args__ = (UniqueConstraint("store_id", "sku_id", name="uq_inventory_store_sku"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), index=True
    )
    sku_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skus.id", ondelete="CASCADE"), index=True)
    on_hand: Mapped[int] = mapped_column(Integer)
    reorder_point: Mapped[int] = mapped_column(Integer)
    case_size: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = _updated_at()


class Action(TenantScoped, Base):
    """Something the planner decided to do. Reviewed by humans when guardrails say so."""

    __tablename__ = "actions"
    __table_args__ = (Index("ix_actions_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True
    )
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stores.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[ActionKind] = mapped_column(ACTION_KIND_ENUM)
    status: Mapped[ActionStatus] = mapped_column(ACTION_STATUS_ENUM)
    requires_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    review_reason: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    estimated_cost_kobo: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    confidence: Mapped[float | None] = mapped_column(Float)
    rationale: Mapped[str] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(128))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    run: Mapped[AgentRun | None] = relationship(back_populates="actions")


class ToolCallLog(TenantScoped, Base):
    """Every tool invocation, from the planner or a direct HTTP/MCP call."""

    __tablename__ = "tool_calls"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    tool_name: Mapped[str] = mapped_column(String(64))
    caller_role: Mapped[str] = mapped_column(String(32))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created_at()

    run: Mapped[AgentRun | None] = relationship(back_populates="tool_calls")


class Notification(TenantScoped, Base):
    """A message the system decided to send. Delivery channels are stubs until P9."""

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True
    )
    channel: Mapped[str] = mapped_column(String(16))
    recipient: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = _created_at()


TENANT_TABLES: tuple[str, ...] = (
    "users",
    "stores",
    "shelves",
    "skus",
    "planograms",
    "planogram_slots",
    "photos",
    "agent_runs",
    "extractions",
    "inventory_levels",
    "actions",
    "tool_calls",
    "notifications",
)
