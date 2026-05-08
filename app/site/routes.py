import logging
import os
from typing import Optional

from fastapi import APIRouter, Cookie, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import Client, Payment, Plan, Product, SiteRequest, Subscription
from app.site.i18n import DEFAULT_LANG, SUPPORTED_LANGS, get_t

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# Whitelist of installed site templates. Each must have an `index.html`
# at templates/sites/{name}/index.html
AVAILABLE_TEMPLATES = {"technovlada"}


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

    if not business_name or not telegram or not site_type or not plan:
        return templates.TemplateResponse(
            "create_site.html",
            {
                "request": request,
                "t": t,
                "lang": chosen,
                "supported_langs": SUPPORTED_LANGS,
                "submitted": False,
                "error": t["create_site"]["error_required"],
                "form": {
                    "business_name": business_name,
                    "telegram": telegram,
                    "site_type": site_type,
                    "plan": plan,
                    "comment": comment or "",
                },
            },
            status_code=400,
        )

    # 1. Save to DB
    async with AsyncSessionLocal() as session:
        req = SiteRequest(
            business_name=business_name,
            telegram=telegram,
            site_type=site_type,
            plan=plan,
            comment=comment,
            status="new",
        )
        session.add(req)
        await session.commit()
        await session.refresh(req)
        req_id = req.id

    # 2. Notify admins in Telegram (best-effort) with inline action buttons
    bot = getattr(request.app.state, "bot", None)
    if bot is not None and settings.admin_ids:
        from app.bot.site_request import request_actions_kb

        text = (
            "🆕 <b>Новая заявка</b>\n"
            f"#<code>{req_id}</code>\n"
            f"• Бизнес: <b>{business_name}</b>\n"
            f"• Telegram: <code>{telegram}</code>\n"
            f"• Тариф: {plan}\n"
            f"• Тип сайта: {site_type}\n"
            + (f"• Комментарий: {comment}\n" if comment else "")
        )
        kb = request_actions_kb(req_id)
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(
                    admin_id, text, parse_mode="HTML", reply_markup=kb
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to notify admin %s: %s", admin_id, exc)

    return templates.TemplateResponse(
        "create_site.html",
        {
            "request": request,
            "t": t,
            "lang": chosen,
            "supported_langs": SUPPORTED_LANGS,
            "submitted": True,
            "error": None,
            "form": {},
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
