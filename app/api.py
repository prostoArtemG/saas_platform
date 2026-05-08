"""HTTP API for inbound integrations (e.g. tech_bot payment requests)."""
import logging
from decimal import Decimal, InvalidOperation
from typing import Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.bot.payments import format_payment_message, payment_actions_kb
from app.config import settings
from app.db import AsyncSessionLocal
from app.models import Client, PaymentRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])


class PaymentRequestIn(BaseModel):
    client_slug: str = Field(..., min_length=1, max_length=64)
    type: Literal["subscription", "domain"] = "subscription"
    amount: Optional[float] = None
    currency: Optional[str] = Field(default=None, max_length=8)
    external_id: Optional[str] = Field(default=None, max_length=128)
    note: Optional[str] = Field(default=None, max_length=2000)


@router.post("/payment-request")
async def receive_payment_request(
    payload: PaymentRequestIn,
    request: Request,
    x_webhook_secret: Optional[str] = Header(default=None, alias="X-Webhook-Secret"),
) -> dict:
    # Optional shared-secret check (skipped if PAYMENT_WEBHOOK_SECRET is empty)
    if settings.payment_webhook_secret:
        if x_webhook_secret != settings.payment_webhook_secret:
            raise HTTPException(status_code=401, detail="invalid webhook secret")

    # Verify client exists (best-effort: still record the request even if not found)
    async with AsyncSessionLocal() as session:
        client = await session.scalar(
            select(Client).where(Client.slug == payload.client_slug)
        )
        if client is None:
            raise HTTPException(status_code=404, detail="client not found")

        amount: Optional[Decimal] = None
        if payload.amount is not None:
            try:
                amount = Decimal(str(payload.amount))
            except (InvalidOperation, ValueError):
                raise HTTPException(status_code=400, detail="invalid amount")

        req = PaymentRequest(
            client_slug=payload.client_slug,
            type=payload.type,
            amount=amount,
            currency=payload.currency,
            external_id=payload.external_id,
            note=payload.note,
            status="pending",
        )
        session.add(req)
        await session.commit()
        await session.refresh(req)
        req_id = req.id
        # Build message snapshot before session closes
        message_text = format_payment_message(req)

    # Notify admins
    bot = getattr(request.app.state, "bot", None)
    notified = 0
    if bot is not None and settings.admin_ids:
        kb = payment_actions_kb(req_id)
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(
                    admin_id, message_text, parse_mode="HTML", reply_markup=kb
                )
                notified += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to notify admin %s: %s", admin_id, exc)

    return {"id": req_id, "status": "pending", "notified": notified}
