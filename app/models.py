from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    telegram_bot_token: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    bot_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    bot_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    bot_admin_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    admin_telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    template_name: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="technovlada", default="technovlada"
    )
    domain_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="pending", default="pending"
    )
    plan_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="client",
        cascade="all, delete-orphan",
    )
    products: Mapped[list["Product"]] = relationship(
        back_populates="client",
        cascade="all, delete-orphan",
    )
    domains: Mapped[list["Domain"]] = relationship(
        back_populates="client",
        cascade="all, delete-orphan",
    )
    plan: Mapped[Optional["Plan"]] = relationship(back_populates="clients")


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    slug: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True, index=True)
    price: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    currency: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="USD", default="USD"
    )
    products_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    images_per_product_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    domains_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    users_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    analytics_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )
    # Legacy fields (kept for backward compatibility with existing code/data)
    price_monthly: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    can_buyout: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    buyout_months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="plan")
    clients: Mapped[list["Client"]] = relationship(back_populates="plan")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("plans.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    starts_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    client: Mapped["Client"] = relationship(back_populates="subscriptions")
    plan: Mapped["Plan"] = relationship(back_populates="subscriptions")


class SiteRequest(Base):
    __tablename__ = "site_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_name: Mapped[str] = mapped_column(String(255), nullable=False)
    telegram: Mapped[str] = mapped_column(String(128), nullable=False)
    site_type: Mapped[str] = mapped_column(String(64), nullable=False)
    plan: Mapped[str] = mapped_column(String(64), nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="new")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    group_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    image_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    brand: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    old_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    specs: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    client: Mapped["Client"] = relationship(back_populates="products")


class PaymentRequest(Base):
    __tablename__ = "payment_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # subscription | domain
    amount: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="pending", default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subscription_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    payment_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="manual", default="manual"
    )
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="USD", default="USD"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending", default="pending"
    )
    invoice_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    payment_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Domain(Base):
    __tablename__ = "domains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    domain: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="pending", default="pending"
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dns_connected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    client: Mapped["Client"] = relationship(back_populates="domains")


class ClientSettings(Base):
    __tablename__ = "client_settings"

    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), primary_key=True
    )
    language: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="uk", default="uk"
    )
    currency: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="UAH", default="UAH"
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="Europe/Kyiv", default="Europe/Kyiv"
    )
    theme_name: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="light_red", default="light_red"
    )
    shop_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    telegram_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    instagram_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BillingState(Base):
    __tablename__ = "billing_state"

    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="active", default="active"
    )
    trial_days_left: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LimitsSnapshot(Base):
    __tablename__ = "limits_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("plans.id", ondelete="SET NULL"), nullable=True
    )
    products_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    images_per_product_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    domains_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    users_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_phone: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_city: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    items_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    total: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="new", default="new", index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
