"""HTTP API for inbound integrations (e.g. tech_bot payment requests)."""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.bot.payments import format_payment_message, payment_actions_kb
from app.config import settings
from app.db import AsyncSessionLocal
from app.models import Client, Payment, PaymentRequest, Plan, Subscription
from app.payments import PROVIDERS, get_provider, get_provider_strict
from app.services.client_domain import get_client_domain
from app.services.client_limits import get_client_limits
from app.services.subscription_status import get_client_subscription_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/client-status/{slug}")
async def client_status(slug: str) -> dict:
    """Return the effective subscription status for a client."""
    async with AsyncSessionLocal() as session:
        result = await get_client_subscription_status(session, slug)
    if result is None:
        raise HTTPException(status_code=404, detail="client not found")
    return {
        "slug": result.slug,
        "status": result.status,
        "expires_at": result.expires_at.isoformat() if result.expires_at else None,
    }


@router.get("/client-domain/{slug}")
async def client_domain(slug: str) -> dict:
    """Return the latest domain attached to a client."""
    async with AsyncSessionLocal() as session:
        # Verify the client exists for proper 404 vs no-domain distinction
        client_exists = (
            await session.execute(select(Client).where(Client.slug == slug))
        ).scalar_one_or_none()
        if client_exists is None:
            raise HTTPException(status_code=404, detail="client not found")
        info = await get_client_domain(session, slug)
    if info is None:
        return {
            "domain": None,
            "status": "none",
            "expires_at": None,
            "dns_connected": False,
        }
    return {
        "domain": info.domain,
        "status": info.status,
        "expires_at": info.expires_at.isoformat() if info.expires_at else None,
        "dns_connected": info.dns_connected,
    }


@router.get("/client-payments/{slug}")
async def client_payments(slug: str) -> dict:
    """Return the payment history for a client (newest first)."""
    async with AsyncSessionLocal() as session:
        client = (
            await session.execute(select(Client).where(Client.slug == slug))
        ).scalar_one_or_none()
        if client is None:
            raise HTTPException(status_code=404, detail="client not found")

        rows = (
            await session.execute(
                select(Payment)
                .where(Payment.client_id == client.id)
                .order_by(Payment.created_at.desc(), Payment.id.desc())
            )
        ).scalars().all()

    items = [
        {
            "payment_type": p.payment_type,
            "amount": float(p.amount) if p.amount is not None else None,
            "currency": p.currency,
            "status": p.status,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "paid_at": p.paid_at.isoformat() if p.paid_at else None,
        }
        for p in rows
    ]
    return {"slug": client.slug, "count": len(items), "items": items}


@router.get("/client-limits/{slug}")
async def client_limits(slug: str) -> dict:
    """Return plan limits + current usage for a client."""
    async with AsyncSessionLocal() as session:
        result = await get_client_limits(session, slug)
    if result is None:
        raise HTTPException(status_code=404, detail="client not found")
    return {
        "slug": slug,
        "plan_name": result.plan_name,
        "products_limit": result.products_limit,
        "products_used": result.products_used,
        "images_per_product_limit": result.images_per_product_limit,
        "domains_limit": result.domains_limit,
        "users_limit": result.users_limit,
        "users_used": result.users_used,
        "analytics_enabled": result.analytics_enabled,
    }


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
    # Amount is optional: for subscription payments the platform is the source
    # of truth and resolves the price from the client's plan in the DB. Any
    # value sent by tech_bot is treated as a hint only and overridden if it
    # disagrees with the plan price (logged as a warning).
    amount: Optional[float] = Field(default=None, gt=0)
    currency: Optional[str] = Field(default=None, max_length=8)
    provider: Optional[str] = Field(default=None, max_length=32)


def _resolve_plan_price(plan: Optional[Plan]) -> Optional[Decimal]:
    if plan is None:
        return None
    raw = plan.price if plan.price is not None else plan.price_monthly
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    return value if value > 0 else None


@router.post("/create-payment-link")
async def create_payment_link(
    payload: CreatePaymentLinkIn,
    request: Request,
) -> dict:
    """Create a Payment row + invoice on the chosen provider, return payment_url.

    For subscription payments, amount/currency are resolved server-side from
    the client's Plan row. Any caller-supplied amount is informational only.
    For domain payments (no plan binding), the caller-supplied amount is used.
    """
    provider = get_provider(payload.provider)

    async with AsyncSessionLocal() as session:
        client = await session.scalar(
            select(Client).where(Client.slug == payload.client_slug)
        )
        if client is None:
            raise HTTPException(status_code=404, detail="client not found")

        subscription_id: Optional[int] = None
        plan: Optional[Plan] = None
        if payload.payment_type == "subscription":
            sub = await session.scalar(
                select(Subscription)
                .where(Subscription.client_id == client.id)
                .order_by(Subscription.id.desc())
                .limit(1)
            )
            subscription_id = sub.id if sub else None
            plan_id = (sub.plan_id if sub else None) or client.plan_id
            if plan_id is not None:
                plan = await session.get(Plan, plan_id)

        # ----- Resolve amount + currency (server is source of truth for plans) -----
        amount: Optional[Decimal] = None
        currency: str = (payload.currency or "USD").upper()

        if payload.payment_type == "subscription":
            plan_price = _resolve_plan_price(plan)
            if plan_price is None:
                raise HTTPException(
                    status_code=409,
                    detail="client has no plan with a configured price",
                )
            amount = plan_price
            currency = (plan.currency if plan and plan.currency else currency).upper()

            # Caller hint sanity-check
            if payload.amount is not None:
                try:
                    hinted = Decimal(str(payload.amount))
                except (InvalidOperation, ValueError):
                    hinted = None
                if hinted is not None and hinted != amount:
                    logger.warning(
                        "create-payment-link: ignoring caller amount=%s for client=%s; "
                        "using plan price=%s (plan=%s)",
                        hinted, client.slug, amount, getattr(plan, "name", None),
                    )
        else:
            # Domain (or other non-plan-bound) payments: caller must send amount.
            if payload.amount is None:
                raise HTTPException(status_code=400, detail="amount is required")
            try:
                amount = Decimal(str(payload.amount))
            except (InvalidOperation, ValueError):
                raise HTTPException(status_code=400, detail="invalid amount")
            if amount <= 0:
                raise HTTPException(status_code=400, detail="invalid amount")

        # 1. Insert pending Payment to get an id
        payment = Payment(
            client_id=client.id,
            subscription_id=subscription_id,
            payment_type=payload.payment_type,
            provider=provider.name,
            amount=amount,
            currency=currency,
            status="pending",
        )
        session.add(payment)
        await session.flush()

        # 2. Create invoice on provider side
        return_url = settings.payment_return_url or None
        webhook_url = None
        if settings.payment_webhook_base_url:
            webhook_url = (
                settings.payment_webhook_base_url.rstrip("/")
                + f"/api/payment-webhook/{provider.name}"
            )
        try:
            invoice = await provider.create_invoice(
                payment_id=payment.id,
                amount=float(amount),
                currency=currency,
                description=f"{payload.payment_type} for {client.slug}",
                return_url=return_url,
                webhook_url=webhook_url,
            )
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            logger.exception("create_invoice failed via %s: %s", provider.name, exc)
            raise HTTPException(
                status_code=502,
                detail=f"provider {provider.name} create_invoice failed",
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


async def _finalize_payment(
    *,
    payment: Payment,
    new_status: str,
    session,
) -> dict:
    """Apply a normalized webhook status to a Payment row.

    Idempotent: if `payment.status` is already non-pending, this is a no-op.
    On "paid": extend subscription by 30 days OR activate the client domain.
    Returns a snapshot dict suitable for notifications (call BEFORE session close).
    """
    extra: dict = {}
    if payment.status != "pending":
        return {"already_processed": True}

    if new_status == "paid":
        payment.status = "paid"
        payment.paid_at = datetime.now(timezone.utc)
        client = await session.get(Client, payment.client_id)

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
                extra["expires_at"] = sub.expires_at.isoformat()
                extra["new_expiry_human"] = sub.expires_at.strftime("%d.%m.%Y")
                extra["subscription_status"] = sub.status
                plan = await session.get(Plan, sub.plan_id) if sub.plan_id else None
                extra["plan_name"] = plan.name if plan else None
        elif payment.payment_type == "domain" and client is not None:
            client.domain_status = "active"
            extra["domain_status"] = "active"
    elif new_status in ("failed", "cancelled"):
        payment.status = new_status
    else:
        # "pending" / unknown — keep as-is
        return {"ignored_status": new_status}

    return extra


async def _notify_payment_finalized(bot, snapshot: dict) -> None:
    """Best-effort admin + client notifications after a payment is finalized.

    - Admin notification goes through the SaaS platform bot to ``settings.admin_ids``.
    - Client notification goes (a) via the platform bot to ``client.admin_telegram_id``
      if known, AND (b) via the client's own "tech_bot" (if ``telegram_bot_token``
      is set) to the same admin id, so the message lands in the client's own bot
      chat.
    """
    if bot is None or snapshot.get("status") != "paid":
        return

    new_expiry = snapshot.get("new_expiry_human") or "—"
    plan_name = snapshot.get("plan_name") or "—"
    sub_status = snapshot.get("subscription_status") or "active"

    admin_text = (
        "💳 <b>Оплата підтверджена</b>\n"
        f"🏢 Client: <b>{snapshot['client_name']}</b> "
        f"(<code>{snapshot['client_slug']}</code>)\n"
        f"📦 Plan: <b>{plan_name}</b>\n"
        f"📅 New expiry: <b>{new_expiry}</b>\n"
        f"💰 Сума: {snapshot['amount']} {snapshot['currency']}\n"
        f"🔌 Provider: {snapshot['provider']} • #<code>{snapshot['id']}</code>"
    )
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, admin_text, parse_mode="HTML")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Notify admin %s failed: %s", admin_id, exc)

    client_text = (
        "✅ <b>Оплата отримана</b>\n"
        f"Підписку продовжено до <b>{new_expiry}</b>.\n"
        f"📦 Тариф: {plan_name}\n"
        f"✅ Статус: {sub_status}"
    )

    client_admin_id = snapshot.get("client_admin_id")
    if client_admin_id:
        try:
            await bot.send_message(client_admin_id, client_text, parse_mode="HTML")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Notify client (platform bot) %s failed: %s", client_admin_id, exc
            )

        # Best-effort: also send via the client's own tech_bot, if connected.
        tech_bot_token = snapshot.get("client_bot_token")
        if tech_bot_token:
            try:
                # Lazy import to avoid heavy aiogram setup if never used.
                from aiogram import Bot as _Bot
                from aiogram.client.default import DefaultBotProperties
                tech_bot = _Bot(
                    token=tech_bot_token,
                    default=DefaultBotProperties(parse_mode="HTML"),
                )
                try:
                    await tech_bot.send_message(client_admin_id, client_text)
                finally:
                    await tech_bot.session.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Notify client (tech_bot) failed: %s", exc)


def _payment_snapshot(payment: Payment, client: Optional[Client]) -> dict:
    return {
        "id": payment.id,
        "type": payment.payment_type,
        "provider": payment.provider,
        "amount": float(payment.amount),
        "currency": payment.currency,
        "status": payment.status,
        "client_name": client.business_name if client else "—",
        "client_slug": client.slug if client else "—",
        "client_admin_id": client.admin_telegram_id if client else None,
        "client_bot_token": client.telegram_bot_token if client else None,
    }


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

        extra = await _finalize_payment(
            payment=payment, new_status=payload.status, session=session
        )
        client = await session.get(Client, payment.client_id)
        await session.commit()
        snapshot = _payment_snapshot(payment, client)
        snapshot.update(extra or {})

    await _notify_payment_finalized(getattr(request.app.state, "bot", None), snapshot)
    result = {"id": snapshot["id"], "status": snapshot["status"]}
    result.update(extra or {})
    return result


# ---------------------------------------------------------------------------
# Generic provider webhook: POST /api/payment-webhook/{provider}
# ---------------------------------------------------------------------------

@router.post("/payment-webhook/{provider_name}")
async def payment_webhook_generic(
    provider_name: str,
    request: Request,
) -> dict:
    """Universal webhook endpoint for real providers (mono, liqpay, ...).

    Flow:
      1. Resolve provider; 404 if not registered.
      2. Read JSON or form body, capture headers.
      3. provider.verify_webhook(...) — reject on failure.
      4. provider.parse_webhook(...) -> ParsedWebhook(invoice_id, status).
      5. Locate Payment by invoice_id (idempotent).
      6. _finalize_payment + notify.
    """
    if provider_name == "mock":
        # /payment-webhook/mock has its own typed schema above
        raise HTTPException(status_code=404, detail="use /payment-webhook/mock")

    provider = get_provider_strict(provider_name)
    if provider is None:
        raise HTTPException(
            status_code=404, detail=f"provider '{provider_name}' not registered"
        )

    # Read body as JSON; fall back to form (LiqPay sends form-encoded data+signature).
    headers = dict(request.headers)
    payload: Any
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        try:
            form = await request.form()
            payload = dict(form)
        except Exception:  # noqa: BLE001
            payload = None

    logger.info(
        "webhook IN provider=%s headers=%s payload=%s",
        provider_name,
        {k: v for k, v in headers.items() if k.lower() in ("x-sign", "x-token", "content-type", "user-agent")},
        payload,
    )

    if payload is None:
        raise HTTPException(status_code=400, detail="empty or invalid body")

    if not await provider.verify_webhook(payload, headers):
        logger.warning("webhook verify failed for provider=%s", provider_name)
        raise HTTPException(status_code=401, detail="webhook signature invalid")

    parsed = await provider.parse_webhook(payload, headers)
    if parsed is None or not parsed.invoice_id:
        raise HTTPException(status_code=400, detail="cannot parse webhook payload")

    async with AsyncSessionLocal() as session:
        payment = await session.scalar(
            select(Payment).where(Payment.invoice_id == parsed.invoice_id)
        )
        if payment is None:
            logger.warning(
                "webhook %s: no payment for invoice_id=%s",
                provider_name, parsed.invoice_id,
            )
            # Return 200 so the provider stops retrying (we logged it).
            return {"ok": False, "reason": "unknown invoice"}

        if payment.status != "pending":
            return {
                "id": payment.id,
                "status": payment.status,
                "message": "already processed",
            }

        extra = await _finalize_payment(
            payment=payment, new_status=parsed.status, session=session
        )
        client = await session.get(Client, payment.client_id)
        await session.commit()
        snapshot = _payment_snapshot(payment, client)
        snapshot.update(extra or {})

    await _notify_payment_finalized(getattr(request.app.state, "bot", None), snapshot)

    logger.info(
        "webhook %s: payment id=%s -> status=%s (raw=%s)",
        provider_name, snapshot["id"], snapshot["status"], parsed.raw_status,
    )
    result = {
        "id": snapshot["id"],
        "status": snapshot["status"],
        "provider": provider_name,
    }
    result.update(extra or {})
    return result
