"""Client domain lookup helper."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Client, Domain


@dataclass
class ClientDomainInfo:
    domain: str
    status: str
    expires_at: Optional[datetime]
    dns_connected: bool


async def get_client_domain(
    session: AsyncSession, client_slug: str
) -> Optional[ClientDomainInfo]:
    """Return the latest domain attached to a client, or None.

    Returns ``None`` when:
    - the client does not exist, or
    - the client has no domains yet.
    """
    client = (
        await session.execute(select(Client).where(Client.slug == client_slug))
    ).scalar_one_or_none()
    if client is None:
        return None

    domain = (
        await session.execute(
            select(Domain)
            .where(Domain.client_id == client.id)
            .order_by(Domain.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if domain is None:
        return None

    return ClientDomainInfo(
        domain=domain.domain,
        status=domain.status,
        expires_at=domain.expires_at,
        dns_connected=domain.dns_connected,
    )
