"""Client limits resolution.

Combines plan-level limits with current usage counters.

User model does not exist yet — ``users_used`` is approximated from
``Client.admin_telegram_id`` (1 if present, else 0).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Client, Plan, Product, Subscription


@dataclass
class ClientLimits:
    plan_name: Optional[str]
    products_limit: Optional[int]
    products_used: int
    images_per_product_limit: Optional[int]
    domains_limit: Optional[int]
    users_limit: Optional[int]
    users_used: int
    analytics_enabled: bool


async def _resolve_plan(session: AsyncSession, client: Client) -> Optional[Plan]:
    """Pick the effective plan for a client.

    Priority:
    1. ``Client.plan_id`` (direct link to plan).
    2. Latest subscription's plan.
    """
    if client.plan_id is not None:
        plan = await session.get(Plan, client.plan_id)
        if plan is not None:
            return plan

    sub = (
        await session.execute(
            select(Subscription)
            .where(Subscription.client_id == client.id)
            .order_by(Subscription.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if sub is None or sub.plan_id is None:
        return None
    return await session.get(Plan, sub.plan_id)


async def get_client_limits(
    session: AsyncSession, client_slug: str
) -> Optional[ClientLimits]:
    client = (
        await session.execute(select(Client).where(Client.slug == client_slug))
    ).scalar_one_or_none()
    if client is None:
        return None

    plan = await _resolve_plan(session, client)

    products_used = (
        await session.execute(
            select(func.count(Product.id)).where(Product.client_id == client.id)
        )
    ).scalar_one() or 0

    # No users table yet — count the admin contact as the only user.
    users_used = 1 if client.admin_telegram_id else 0

    return ClientLimits(
        plan_name=plan.name if plan else None,
        products_limit=plan.products_limit if plan else None,
        products_used=int(products_used),
        images_per_product_limit=plan.images_per_product_limit if plan else None,
        domains_limit=plan.domains_limit if plan else None,
        users_limit=plan.users_limit if plan else None,
        users_used=users_used,
        analytics_enabled=bool(plan.analytics_enabled) if plan else False,
    )
