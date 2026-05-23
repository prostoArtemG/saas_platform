"""Atomic client onboarding service.

After a Client row is created, this provisions everything else (subscription,
domain placeholder, settings, billing state, limits snapshot) inside a single
transaction. If anything fails the transaction is rolled back and no partial
client is left in the DB.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BillingState,
    Client,
    ClientSettings,
    Domain,
    LimitsSnapshot,
    Plan,
    Subscription,
)

TRIAL_DAYS = 7
DEFAULT_LANGUAGE = "uk"
DEFAULT_CURRENCY = "UAH"
DEFAULT_TIMEZONE = "Europe/Kyiv"


@dataclass
class OnboardingResult:
    client_id: int
    slug: str
    business_name: str
    plan_name: str
    subscription_status: str
    trial_starts_at: datetime
    trial_expires_at: datetime
    products_limit: Optional[int]
    images_per_product_limit: Optional[int]
    domains_limit: Optional[int]
    users_limit: Optional[int]
    language: str
    currency: str
    timezone: str
    billing_status: str
    trial_days_left: int


async def onboard_client(
    session: AsyncSession,
    client: Client,
    plan: Plan,
    *,
    trial_days: int = TRIAL_DAYS,
) -> OnboardingResult:
    """Provision subscription/domain/settings/billing/limits for a freshly
    created Client. The caller is responsible for committing the transaction
    (or rolling back on exception).

    The Client must already be flushed (have an ``id``).
    """
    if client.id is None:
        raise ValueError("client.id is required (call session.flush() first)")

    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=trial_days)

    # 1. Subscription (trial)
    sub = Subscription(
        client_id=client.id,
        plan_id=plan.id,
        status="trial",
        starts_at=now,
        expires_at=expires,
    )
    session.add(sub)

    # Link client to plan for fast lookups
    client.plan_id = plan.id

    # 2. Domain placeholder
    domain = Domain(
        client_id=client.id,
        domain=f"{client.slug}.shopplatform.app",
        status="not_connected",
        dns_connected=False,
    )
    session.add(domain)

    # 3. Settings
    settings = ClientSettings(
        client_id=client.id,
        language=DEFAULT_LANGUAGE,
        currency=DEFAULT_CURRENCY,
        timezone=DEFAULT_TIMEZONE,
    )
    session.add(settings)

    # 4. Billing state
    billing = BillingState(
        client_id=client.id,
        status="active",
        trial_days_left=trial_days,
    )
    session.add(billing)

    # 5. Limits snapshot
    snapshot = LimitsSnapshot(
        client_id=client.id,
        plan_id=plan.id,
        products_limit=plan.products_limit,
        images_per_product_limit=plan.images_per_product_limit,
        domains_limit=plan.domains_limit,
        users_limit=plan.users_limit,
    )
    session.add(snapshot)

    await session.flush()

    return OnboardingResult(
        client_id=client.id,
        slug=client.slug,
        business_name=client.business_name,
        plan_name=plan.name,
        subscription_status=sub.status,
        trial_starts_at=now,
        trial_expires_at=expires,
        products_limit=plan.products_limit,
        images_per_product_limit=plan.images_per_product_limit,
        domains_limit=plan.domains_limit,
        users_limit=plan.users_limit,
        language=settings.language,
        currency=settings.currency,
        timezone=settings.timezone,
        billing_status=billing.status,
        trial_days_left=billing.trial_days_left,
    )
