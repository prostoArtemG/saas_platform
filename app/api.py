"""HTTP API for inbound integrations (e.g. tech_bot payment requests)."""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.bot.payments import format_payment_message, payment_actions_kb
from app.config import settings
from app.db import AsyncSessionLocal
from app.models import Client, Payment, PaymentRequest, Subscription
from app.payments import get_provider

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


# ---------------------------------------------------------------------------
# Universal payment layer (provider-agnostic)
# ---------------------------------------------------------------------------


class CreatePaymentLinkIn(BaseModel):
    client_slug: str = Field(..., min_length=1, max_length=64)
    payment_type: Literal["subscription", "domain"] = "subscription"
    amount: float = Field(..., gt=0)
    currency: str = Field(default="USD", max_length=8)
    provider: Optional[str] = Field(default=None, max_length=32)


@router.post("/create-payment-link")
async def create_payment_link(
    payload: CreatePaymentLinkIn,
    request: Request,
) -> dict:
    """Create a Payment row + invoice on the chosen provider, return payment_url."""
    try:
        amount = Decimal(str(payload.amount))
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=400, detail="invalid amount")

    provider = get_provider(payload.provider)

    async with AsyncSessionLocal() as session:
        client = await session.scalar(
            select(Client).where(Client.slug == payload.client_slug)
        )
        if client is None:
            raise HTTPException(status_code=404, detail="client not found")

        subscription_id: Optional[int] = None
        if payload.payment_type == "subscription":
            sub = await session.scalar(
                select(Subscription)
                .where(Subscription.client_id == client.id)
                .order_by(Subscription.id.desc())
                .limit(1)
            )
            subscription_id = sub.id if sub else None

        # 1. Insert pending Payment to get an id
        payment = Payment(
            client_id=client.id,
            subscription_id=subscription_id,
            payment_type=payload.payment_type,
            provider=provider.name,
            amount=amount,
            currency=payload.currency,
            status="pending",
        )
        session.add(payment)
        await session.flush()

        # 2. Create invoice on provider side
        invoice = await provider.create_invoice(
            payment_id=payment.id,
            amount=float(amount),
            currency=payload.currency,
            description=f"{payload.payment_type} for {client.slug}",
        )
        payment.invoice_id = invoice.invoice_id
        payment.payment_url = invoice.payment_url
        await session.commit()

        return {
            "id": payment.id,
            "status": payment.status,
            "provider": payment.provider,
            "invoice_id": payment.invoice_id,
            "payment_url": payment.payment_url,
            "amount": float(payment.amount),
            "currency": payment.currency,
        }


class MockWebhookIn(BaseModel):
    payment_id: int
    status: Literal["paid", "failed", "cancelled"] = "paid"


@router.post("/payment-webhook/mock")
async def payment_webhook_mock(
    payload: MockWebhookIn,
    request: Request,
) -> dict:
    """Test webhook: mark payment as paid/failed/cancelled, extend subscription, notify."""
    async with AsyncSessionLocal() as session:
        payment = await session.get(Payment, payload.payment_id)
        if payment is None:
            raise HTTPException(status_code=404, detail="payment not found")
        if payment.status != "pending":
            return {
                "id": payment.id,
                "status": payment.status,
                "message": "already processed",
            }

        client = await session.get(Client, payment.client_id)

        result = {"id": payment.id, "status": payload.status}

        if payload.status == "paid":
            payment.status = "paid"
            payment.paid_at = datetime.now(timezone.utc)

            # Extend subscription if applicable
            if payment.payment_type == "subscription":
                sub = None
                if payment.subscription_id is not None:
                    sub = await session.get(Subscription, payment.subscription_id)
                if sub is None and client is not None:
                    sub = await session.scalar(
                        select(Subscription)
                        .where(Subscription.client_id == client.id)
                        .order_by(Subscription.id.desc())
                        .limit(1)
                    )
                if sub is not None:
                    now = datetime.now(timezone.utc)
                    base = sub.expires_at if sub.expires_at and sub.expires_at > now else now
                    sub.expires_at = base + timedelta(days=30)
                    sub.status = "active"
                    payment.subscription_id = sub.id
                    result["expires_at"] = sub.expires_at.isoformat()
            elif payment.payment_type == "domain" and client is not None:
                client.domain_status = "active"
                result["domain_status"] = "active"
        else:
            payment.status = payload.status

        await session.commit()

        # Snapshot for notifications outside the session
        snapshot = {
            "id": payment.id,
            "type": payment.payment_type,
            "provider": payment.provider,
            "amount": float(payment.amount),
            "currency": payment.currency,
            "status": payment.status,
            "client_name": client.business_name if client else "—",
            "client_slug": client.slug if client else "—",
            "client_admin_id": client.admin_telegram_id if client else None,
        }

    # Notify admins + client (best-effort)
    bot = getattr(request.app.state, "bot", None)
    if bot is not None and snapshot["status"] == "paid":
        admin_text = (
            "💰 <b>Платёж получен</b>\n"
            f"#<code>{snapshot['id']}</code>\n"
            f"• Клиент: <b>{snapshot['client_name']}</b> "
            f"(<code>{snapshot['client_slug']}</code>)\n"
            f"• Тип: {snapshot['type']}\n"
            f"• Провайдер: {snapshot['provider']}\n"
            f"• Сумма: <b>{snapshot['amount']} {snapshot['currency']}</b>"
        )
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(admin_id, admin_text, parse_mode="HTML")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Notify admin %s failed: %s", admin_id, exc)

        if snapshot["client_admin_id"]:
            client_text = (
                "✅ <b>Оплата получена, дякуємо!</b>\n"
                f"• Тип: {snapshot['type']}\n"
                f"• Сумма: <b>{snapshot['amount']} {snapshot['currency']}</b>\n"
                f"Підписка активна."
            )
            try:
                await bot.send_message(
                    snapshot["client_admin_id"], client_text, parse_mode="HTML"
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Notify client %s failed: %s", snapshot["client_admin_id"], exc
                )

    return result
