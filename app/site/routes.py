import logging
import os
import re
from typing import Optional

from fastapi import APIRouter, Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import Client, Payment, Plan, Product, SiteRequest, Subscription
from app.services.onboarding import TRIAL_DAYS, onboard_client
from app.site.i18n import DEFAULT_LANG, SUPPORTED_LANGS, get_t

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# Whitelist of installed site templates. Each must have an `index.html`
# at templates/sites/{name}/index.html
AVAILABLE_TEMPLATES = {"technovlada", "shop_bot"}


def _resolve_lang(lang: Optional[str], cookie: Optional[str]) -> str:
    chosen = lang or cookie or DEFAULT_LANG
    if chosen not in SUPPORTED_LANGS:
        chosen = DEFAULT_LANG
    return chosen


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    lang: Optional[str] = None,
    lang_cookie: Optional[str] = Cookie(default=None, alias="lang"),
) -> HTMLResponse:
    chosen = _resolve_lang(lang, lang_cookie)
    response = templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "t": get_t(chosen),
            "lang": chosen,
            "supported_langs": SUPPORTED_LANGS,
        },
    )
    response.set_cookie("lang", chosen, max_age=60 * 60 * 24 * 365, samesite="lax")
    return response


@router.get("/create-site", response_class=HTMLResponse)
async def create_site_form(
    request: Request,
    lang: Optional[str] = None,
    lang_cookie: Optional[str] = Cookie(default=None, alias="lang"),
) -> HTMLResponse:
    chosen = _resolve_lang(lang, lang_cookie)
    t = get_t(chosen)
    response = templates.TemplateResponse(
        "create_site.html",
        {
            "request": request,
            "t": t,
            "lang": chosen,
            "supported_langs": SUPPORTED_LANGS,
            "submitted": False,
            "error": None,
            "form": {},
        },
    )
    response.set_cookie("lang", chosen, max_age=60 * 60 * 24 * 365, samesite="lax")
    return response


@router.post("/create-site", response_class=HTMLResponse)
async def create_site_submit(
    request: Request,
    business_name: str = Form(""),
    telegram: str = Form(""),
    site_type: str = Form(""),
    plan: str = Form(""),
    comment: str = Form(""),
    lang_cookie: Optional[str] = Cookie(default=None, alias="lang"),
) -> HTMLResponse:
    chosen = _resolve_lang(None, lang_cookie)
    t = get_t(chosen)

    business_name = business_name.strip()[:255]
    telegram = telegram.strip()[:128]
    site_type = site_type.strip()[:64]
    plan = plan.strip()[:64]
    comment = (comment or "").strip()[:2000] or None

    def _form_error(message: str, status: int = 400) -> HTMLResponse:
        return templates.TemplateResponse(
            "create_site.html",
            {
                "request": request,
                "t": t,
                "lang": chosen,
                "supported_langs": SUPPORTED_LANGS,
                "submitted": False,
                "error": message,
                "form": {
                    "business_name": business_name,
                    "telegram": telegram,
                    "site_type": site_type,
                    "plan": plan,
                    "comment": comment or "",
                },
            },
            status_code=status,
        )

    if not business_name or not telegram or not site_type or not plan:
        return _form_error(t["create_site"]["error_required"])

    # Persist a SiteRequest as audit log (best-effort, non-blocking failure).
    try:
        async with AsyncSessionLocal() as session:
            req = SiteRequest(
                business_name=business_name,
                telegram=telegram,
                site_type=site_type,
                plan=plan,
                comment=comment,
                status="provisioned",
            )
            session.add(req)
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("SiteRequest audit log failed: %s", exc)

    # Atomic self-service onboarding ------------------------------------------
    template_name = site_type if site_type in {"technovlada", "shop_bot"} else "technovlada"

    try:
        async with AsyncSessionLocal() as session:
            # 1. Resolve plan: match the dropdown label by first word.
            plan_token = (plan.split() or [""])[0].lower()
            plan_row: Optional[Plan] = None
            if plan_token:
                stmt_p = (
                    select(Plan)
                    .where(Plan.active.is_(True))
                    .where(Plan.name.ilike(f"{plan_token}%"))
                    .order_by(Plan.id.asc())
                    .limit(1)
                )
                plan_row = (await session.execute(stmt_p)).scalar_one_or_none()
            if plan_row is None:
                stmt_any = (
                    select(Plan)
                    .where(Plan.active.is_(True))
                    .order_by(Plan.id.asc())
                    .limit(1)
                )
                plan_row = (await session.execute(stmt_any)).scalar_one_or_none()
            if plan_row is None:
                return _form_error(t["create_site"]["error_no_plan"])

            # 2. Generate unique slug from business_name
            slug = await _allocate_slug(session, business_name)

            # 3. Create Client + flush + onboard, all-or-nothing
            client = Client(
                business_name=business_name,
                slug=slug,
                template_name=template_name,
                domain_status="pending",
                status="active",
            )
            session.add(client)
            try:
                await session.flush()
                result = await onboard_client(
                    session, client, plan_row, trial_days=TRIAL_DAYS
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    except Exception as exc:  # noqa: BLE001
        logger.exception("Onboarding failed: %s", exc)
        return _form_error(t["create_site"]["error_provision"], status=500)

    logger.info(
        "Self-service onboarding OK: client_id=%s slug=%s plan=%s",
        result.client_id, result.slug, result.plan_name,
    )

    # Notify admins (best-effort)
    bot = getattr(request.app.state, "bot", None)
    if bot is not None and settings.admin_ids:
        text = (
            "\U0001f195 <b>Новый клиент (self-service)</b>\n"
            f"\U0001f3e2 <b>{business_name}</b>\n"
            f"\U0001f310 <code>{result.slug}</code>\n"
            f"\U0001f4e6 Тариф: {result.plan_name}\n"
            f"\U0001f4c5 Trial до: {result.trial_expires_at:%Y-%m-%d}\n"
            f"\u2709\ufe0f Telegram: <code>{telegram}</code>"
        )
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(admin_id, text, parse_mode="HTML")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to notify admin %s: %s", admin_id, exc)

    return RedirectResponse(
        url=f"/onboarding-success/{result.slug}", status_code=303
    )


_SLUG_CLEAN_RE = re.compile(r"[^a-z0-9]+")


async def _allocate_slug(session, business_name: str) -> str:
    base = (business_name or "").strip().lower()
    base = _SLUG_CLEAN_RE.sub("-", base).strip("-")
    if not base:
        base = "client"
    base = base[:55]

    candidate = base
    suffix = 2
    while True:
        existing = (
            await session.execute(select(Client.id).where(Client.slug == candidate))
        ).scalar_one_or_none()
        if existing is None:
            return candidate
        candidate = f"{base}-{suffix}"[:60]
        suffix += 1
        if suffix > 9999:  # extremely unlikely
            raise RuntimeError("could not allocate unique slug")


@router.get("/onboarding-success/{slug}", response_class=HTMLResponse)
async def onboarding_success(
    request: Request,
    slug: str,
    lang: Optional[str] = None,
    lang_cookie: Optional[str] = Cookie(default=None, alias="lang"),
) -> HTMLResponse:
    chosen = _resolve_lang(lang, lang_cookie)
    t = get_t(chosen)

    async with AsyncSessionLocal() as session:
        stmt = (
            select(Client)
            .where(Client.slug == slug)
            .options(selectinload(Client.subscriptions).selectinload(Subscription.plan))
        )
        client = (await session.execute(stmt)).scalar_one_or_none()
        if client is None:
            return templates.TemplateResponse(
                "404.html",
                {
                    "request": request,
                    "t": t,
                    "lang": chosen,
                    "supported_langs": SUPPORTED_LANGS,
                    "slug": slug,
                },
                status_code=404,
            )

        # Pick the most recent subscription
        sub = max(client.subscriptions, key=lambda s: s.id) if client.subscriptions else None
        plan_name = sub.plan.name if (sub and sub.plan) else "—"
        trial_expires = (
            sub.expires_at.strftime("%Y-%m-%d %H:%M UTC")
            if (sub and sub.expires_at) else "—"
        )

        platform_bot_username = getattr(
            request.app.state, "platform_bot_username", None
        )
        cms_url = (
            f"https://t.me/{platform_bot_username}"
            if platform_bot_username else None
        )

        site_url = str(request.base_url).rstrip("/") + f"/site/{client.slug}"

        data = {
            "business_name": client.business_name,
            "site_url": site_url,
            "bot_username": client.bot_username,
            "template": client.template_name,
            "plan": plan_name,
            "trial_expires_at": trial_expires,
            "cms_url": cms_url,
        }

    return templates.TemplateResponse(
        "onboarding_success.html",
        {
            "request": request,
            "t": t,
            "lang": chosen,
            "supported_langs": SUPPORTED_LANGS,
            "data": data,
        },
    )


@router.get("/health")
async def health() -> dict:
    return {"status": "healthy"}


@router.get("/payment/{payment_id}", response_class=HTMLResponse)
async def payment_page(
    request: Request,
    payment_id: int,
    lang: Optional[str] = None,
    lang_cookie: Optional[str] = Cookie(default=None, alias="lang"),
) -> HTMLResponse:
    chosen = _resolve_lang(lang, lang_cookie)
    t = get_t(chosen)

    async with AsyncSessionLocal() as session:
        payment = await session.get(Payment, payment_id)
        if payment is None:
            return templates.TemplateResponse(
                "404.html",
                {
                    "request": request,
                    "t": t,
                    "lang": chosen,
                    "supported_langs": SUPPORTED_LANGS,
                    "slug": f"payment #{payment_id}",
                },
                status_code=404,
            )

        client = await session.get(Client, payment.client_id)

        # If subscription payment, surface plan + new expiry on the page.
        plan_name: Optional[str] = None
        new_expiry_human: Optional[str] = None
        sub_status: Optional[str] = None
        if payment.payment_type == "subscription" and client is not None:
            sub = None
            if payment.subscription_id is not None:
                sub = await session.get(Subscription, payment.subscription_id)
            if sub is None:
                sub = await session.scalar(
                    select(Subscription)
                    .where(Subscription.client_id == client.id)
                    .order_by(Subscription.id.desc())
                    .limit(1)
                )
            if sub is not None:
                if sub.expires_at:
                    new_expiry_human = sub.expires_at.strftime("%d.%m.%Y")
                sub_status = sub.status
                if sub.plan_id:
                    plan = await session.get(Plan, sub.plan_id)
                    plan_name = plan.name if plan else None

        ctx_payment = {
            "id": payment.id,
            "client_name": client.business_name if client else "—",
            "client_slug": client.slug if client else None,
            "payment_type": payment.payment_type,
            "provider": payment.provider,
            "amount": float(payment.amount),
            "currency": payment.currency,
            "status": payment.status,
            "invoice_id": payment.invoice_id,
            "created_at": payment.created_at,
            "paid_at": payment.paid_at,
            "plan_name": plan_name,
            "new_expiry_human": new_expiry_human,
            "subscription_status": sub_status,
        }

    return templates.TemplateResponse(
        "payment.html",
        {
            "request": request,
            "t": t,
            "lang": chosen,
            "supported_langs": SUPPORTED_LANGS,
            "payment": ctx_payment,
        },
    )


@router.get("/dashboard/{slug}", response_class=HTMLResponse)
async def client_dashboard(
    request: Request,
    slug: str,
    lang: Optional[str] = None,
    lang_cookie: Optional[str] = Cookie(default=None, alias="lang"),
) -> HTMLResponse:
    chosen = _resolve_lang(lang, lang_cookie)
    t = get_t(chosen)

    async with AsyncSessionLocal() as session:
        client = await session.scalar(
            select(Client)
            .where(Client.slug == slug)
            .options(selectinload(Client.subscriptions).selectinload(Subscription.plan))
        )

        if client is None:
            return templates.TemplateResponse(
                "404.html",
                {
                    "request": request,
                    "t": t,
                    "lang": chosen,
                    "supported_langs": SUPPORTED_LANGS,
                    "slug": slug,
                },
                status_code=404,
            )

        # Pick the most relevant subscription: active/trial first, latest by id
        subs = sorted(
            client.subscriptions,
            key=lambda s: (
                0 if s.status in ("active", "trial") else 1,
                -s.id,
            ),
        )
        sub = subs[0] if subs else None
        plan = sub.plan if sub else None

        ctx = {
            "request": request,
            "t": t,
            "lang": chosen,
            "supported_langs": SUPPORTED_LANGS,
            "client": {
                "business_name": client.business_name,
                "slug": client.slug,
                "status": client.status,
                "bot_connected": bool(client.telegram_bot_token),
                "admin_telegram_id": client.admin_telegram_id,
                "created_at": client.created_at,
            },
            "subscription": {
                "status": sub.status if sub else None,
                "expires_at": sub.expires_at if sub else None,
            } if sub else None,
            "plan": {
                "name": plan.name,
                "price_monthly": plan.price_monthly,
                "can_buyout": plan.can_buyout,
                "buyout_months": plan.buyout_months,
            } if plan else None,
            "domain": {
                "host": f"{client.slug}.saasplatform.app",
                "status": "pending",
            },
        }

    return templates.TemplateResponse("dashboard.html", ctx)


@router.get("/site/{slug}", response_class=HTMLResponse)
async def client_site(
    request: Request,
    slug: str,
    lang: Optional[str] = None,
    lang_cookie: Optional[str] = Cookie(default=None, alias="lang"),
) -> HTMLResponse:
    chosen = _resolve_lang(lang, lang_cookie)
    t = get_t(chosen)

    async with AsyncSessionLocal() as session:
        client = await session.scalar(select(Client).where(Client.slug == slug))

        if client is None:
            return templates.TemplateResponse(
                "404.html",
                {
                    "request": request,
                    "t": t,
                    "lang": chosen,
                    "supported_langs": SUPPORTED_LANGS,
                    "slug": slug,
                },
                status_code=404,
            )

        products_rows = (
            await session.scalars(
                select(Product)
                .where(Product.client_id == client.id)
                .order_by(Product.is_available.desc(), Product.id.desc())
            )
        ).all()
        products = [
            {
                "id": p.id,
                "category": p.category,
                "name": p.name,
                "description": p.description,
                "price": float(p.price) if p.price is not None else 0.0,
                "image_url": p.image_url,
                "is_available": p.is_available,
            }
            for p in products_rows
        ]
        client_data = {
            "id": client.id,
            "business_name": client.business_name,
            "slug": client.slug,
            "telegram_id": client.admin_telegram_id,
            "template_name": client.template_name,
        }

    template_name = (client_data["template_name"] or "").strip() or "technovlada"

    # Validate template exists on disk and is whitelisted
    template_path = os.path.join("templates", "sites", template_name, "index.html")
    if template_name not in AVAILABLE_TEMPLATES or not os.path.exists(template_path):
        return templates.TemplateResponse(
            "site_template_error.html",
            {
                "request": request,
                "t": t,
                "lang": chosen,
                "supported_langs": SUPPORTED_LANGS,
                "slug": slug,
                "business_name": client_data["business_name"],
                "template_name": template_name,
                "available": sorted(AVAILABLE_TEMPLATES),
            },
            status_code=500,
        )

    return templates.TemplateResponse(
        f"sites/{template_name}/index.html",
        {
            "request": request,
            "t": t,
            "lang": chosen,
            "client": client_data,
            "products": products,
        },
    )


# ---------------------------------------------------------------------------
# Site order endpoint
# ---------------------------------------------------------------------------

class SiteOrderRequest(BaseModel):
    name: str
    phone: str
    city: str = ""
    comment: str = ""
    items: list[dict]


@router.post("/site/{slug}/order")
async def site_order(
    slug: str,
    data: SiteOrderRequest,
    request: Request,
) -> dict:
    async with AsyncSessionLocal() as session:
        client = await session.scalar(
            select(Client).where(Client.slug == slug)
        )
        if client is None:
            raise HTTPException(status_code=404, detail="client not found")

    # Notify client admin via Telegram
    bot = getattr(request.app.state, "bot", None)
    if bot and client.admin_telegram_id:
        lines = []
        for item in data.items:
            name  = item.get("name", "?")
            qty   = item.get("qty", 1)
            price = item.get("price", 0)
            lines.append(f"\u2022 {name} \u00d7 {qty} \u2014 {price} \u0433\u0440\u043d")
        items_text = "\n".join(lines)
        total = sum(
            item.get("price", 0) * item.get("qty", 1)
            for item in data.items
        )
        try:
            await bot.send_message(
                client.admin_telegram_id,
                f"\U0001f6d2 \u041d\u043e\u0432\u0435 \u0437\u0430\u043c\u043e\u0432\u043b\u0435\u043d\u043d\u044f!\n\n"
                f"\U0001f464 {data.name}\n"
                f"\U0001f4de {data.phone}\n"
                f"\U0001f3d9 {data.city or '\u2014'}\n\n"
                f"\U0001f4e6 \u0422\u043e\u0432\u0430\u0440\u0438:\n{items_text}\n\n"
                f"\U0001f4b0 \u0420\u0430\u0437\u043e\u043c: {total} \u0433\u0440\u043d\n"
                f"\U0001f4ac {data.comment or '\u2014'}"
            )
        except Exception as e:
            logger.warning("Failed to notify client %s: %s", slug, e)

    return {"ok": True}
