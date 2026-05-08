"""Subscription status resolution.

Computes the *effective* status of a client's subscription, taking into
account the stored ``status`` value and the ``expires_at`` timestamp.

Status semantics:
- ``trial``    — stored status is ``trial`` and not past expiration.
- ``active``   — stored status is ``active`` and not past expiration.
- ``expired``  — ``expires_at`` is in the past, regardless of stored status.
- ``inactive`` — no subscription exists for the client.
- otherwise the raw stored status (e.g. ``cancelled``, ``suspended``).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Client, Subscription


@dataclass
class ClientSubscriptionStatus:
    slug: str
    status: str
    expires_at: Optional[datetime]


def _is_expired(expires_at: Optional[datetime]) -> bool:
    if expires_at is None:
        return False
    now = datetime.now(timezone.utc)
    # Normalize naive datetimes (treat as UTC)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at < now


async def get_client_subscription_status(
    session: AsyncSession, client_slug: str
) -> Optional[ClientSubscriptionStatus]:
    """Return the effective subscription status for a client, or None if the
    client does not exist."""
    client = (
        await session.execute(select(Client).where(Client.slug == client_slug))
    ).scalar_one_or_none()
    if client is None:
        return None

    sub = (
        await session.execute(
            select(Subscription)
            .where(Subscription.client_id == client.id)
            .order_by(Subscription.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if sub is None:
        return ClientSubscriptionStatus(
            slug=client.slug, status="inactive", expires_at=None
        )

    if _is_expired(sub.expires_at) and sub.status not in ("cancelled", "suspended"):
        effective = "expired"
    else:
        effective = sub.status

    return ClientSubscriptionStatus(
        slug=client.slug, status=effective, expires_at=sub.expires_at
    )
