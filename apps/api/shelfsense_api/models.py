"""SQLAlchemy models. Every tenant-owned table carries ``tenant_id`` and is under RLS.

Schema changes go through Alembic; the integration test ``test_migrations.py`` fails if
these models and the migrations drift apart.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column, relationship


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


TENANT_TABLES: tuple[str, ...] = (
    "users",
    "stores",
    "shelves",
    "skus",
    "planograms",
    "planogram_slots",
)
