import asyncio
import logging
import os
import re
import secrets
from typing import Optional

from fastapi import APIRouter, Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import Client, ClientSettings, Order, Payment, Plan, Product, SiteRequest, Subscription
from app.services.onboarding import TRIAL_DAYS, onboard_client
from app.services.railway_api import deploy_shop_bot
from app.site.i18n import DEFAULT_LANG, SUPPORTED_LANGS, get_t

logger = logging.getLogger(__name__)


def _check_dashboard_token(client: "Client", token: Optional[str]) -> None:  # noqa: F821
    """Raise 403 if dashboard_token is set on client but provided token doesn't match."""
    if not client.dashboard_token:
        return  # backward compat: old client without token
    if not secrets.compare_digest(token or "", client.dashboard_token):
        raise HTTPException(status_code=403, detail="Invalid or missing dashboard token")


def _clean(s: Optional[str]) -> Optional[str]:
    """Strip surrogate code points that PostgreSQL / Jinja2 cannot encode."""
    if not s:
        return s
    return s.encode('utf-16', 'surrogatepass').decode('utf-16', 'ignore')


def get_client_slug_from_host(host: str, platform_domain: str) -> Optional[str]:
    """Return the client slug if *host* is a client subdomain of *platform_domain*.

    Examples (platform_domain = "shopplatform.app"):
        apelsin.shopplatform.app        → "apelsin"
        shopplatform.app                → None  (root platform)
        www.shopplatform.app            → None  (reserved)
        127.0.0.1 / localhost           → None  (local dev)
        some-other-domain.com           → None  (not our platform)
        apelsin.shopplatform.app:443    → "apelsin"  (port stripped)
    """
    if not host or not platform_domain:
        return None
    host = host.split(":")[0].lower()
    platform_domain = platform_domain.lower()
    suffix = f".{platform_domain}"
    if not host.endswith(suffix):
        return None
    subdomain = host[: -len(suffix)]
    # Must be a single-level subdomain, non-empty, not a reserved word
    if not subdomain or "." in subdomain or subdomain in ("www", "api", "static", "mail"):
        return None
    return subdomain


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


@router.get("/templates", response_class=HTMLResponse)
async def templates_select(
    request: Request,
    plan: Optional[str] = None,
    lang: Optional[str] = None,
    lang_cookie: Optional[str] = Cookie(default=None, alias="lang"),
) -> HTMLResponse:
    chosen = _resolve_lang(lang, lang_cookie)
    t = get_t(chosen)
    response = templates.TemplateResponse(
        "templates_select.html",
        {
            "request": request,
            "t": t,
            "lang": chosen,
            "supported_langs": SUPPORTED_LANGS,
            "plan": plan or "",
        },
    )
    response.set_cookie("lang", chosen, max_age=60 * 60 * 24 * 365, samesite="lax")
    return response


@router.get("/create-site", response_class=HTMLResponse)
async def create_site_form(
    request: Request,
    plan: Optional[str] = None,
    template: Optional[str] = None,
    lang: Optional[str] = None,
    lang_cookie: Optional[str] = Cookie(default=None, alias="lang"),
) -> HTMLResponse:
    chosen = _resolve_lang(lang, lang_cookie)
    t = get_t(chosen)
    # Map plan slug (starter/pro/full) to the full option string for pre-selection
    plan_matched = ""
    if plan:
        plan_lower = plan.lower()
        for opt in t["create_site"]["plan_options"]:
            if opt.lower().startswith(plan_lower):
                plan_matched = opt
                break
    response = templates.TemplateResponse(
        "create_site.html",
        {
            "request": request,
            "t": t,
            "lang": chosen,
            "supported_langs": SUPPORTED_LANGS,
            "submitted": False,
            "error": None,
            "form": {
                "plan": plan_matched,
                "site_type": template or "",
            },
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
    bot_token: str = Form(""),
    admin_telegram_id: str = Form(""),
    lang_cookie: Optional[str] = Cookie(default=None, alias="lang"),
) -> HTMLResponse:
    chosen = _resolve_lang(None, lang_cookie)
    t = get_t(chosen)

    business_name = _clean(business_name.strip()[:255]) or ""
    telegram = telegram.strip()[:128]
    bot_token = bot_token.strip()[:255]
    admin_telegram_id = admin_telegram_id.strip()[:20]
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
                    "bot_token": bot_token,
                    "admin_telegram_id": admin_telegram_id,
                },
            },
            status_code=status,
        )

    if not business_name or not site_type or not plan:
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
    template_name = site_type if site_type in {"technovlada", "shop_bot", "premium_store"} else "technovlada"

    logger.info(
        "create_site_submit: business_name=%s site_type=%r template_name=%r bot_token_len=%s",
        business_name, site_type, template_name, len(bot_token) if bot_token else 0,
    )

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
                admin_telegram_id=int(admin_telegram_id) if admin_telegram_id.isdigit() else None,
                dashboard_token=secrets.token_urlsafe(24),
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

    # Auto-deploy only for premium_store template
    deploy_result = None
    railway_url = None
    logger.info("Deploy check: template_name=%r bot_token_bool=%r", template_name, bool(bot_token.strip()) if bot_token else False)
    if template_name == "premium_store" and bot_token:
        logger.info("Starting Railway deploy for template=%s slug=%s", template_name, slug)
        try:
            from app.config import settings as app_settings
            deploy_result = await deploy_shop_bot(
                client_name=business_name,
                slug=slug,
                bot_token=bot_token,
                admin_ids=admin_telegram_id if admin_telegram_id.isdigit() else "",
                cloudinary_cloud=os.getenv("CLOUDINARY_CLOUD_NAME", ""),
                cloudinary_key=os.getenv("CLOUDINARY_API_KEY", ""),
                cloudinary_secret=os.getenv("CLOUDINARY_API_SECRET", ""),
                saas_platform_url=str(request.base_url).rstrip("/"),
                template_name=template_name,
            )
            railway_url = deploy_result.get("url")
            # Save bot token and Railway URL to client record
            async with AsyncSessionLocal() as update_session:
                client_upd = await update_session.get(Client, client.id)
                if client_upd:
                    client_upd.telegram_bot_token = bot_token
                    if railway_url:
                        client_upd.domain_status = "active"
                        client_upd.bot_admin_ids = railway_url
                    await update_session.commit()
        except Exception as exc:
            logger.warning("Railway deploy failed: %s", exc, exc_info=True)

    # Get bot username from token
    if bot_token.strip() and deploy_result:
        try:
            from aiogram import Bot
            tmp_bot = Bot(token=bot_token.strip())
            bot_info = await tmp_bot.get_me()
            await tmp_bot.session.close()
            async with AsyncSessionLocal() as update_session:
                client_upd = await update_session.get(Client, client.id)
                if client_upd:
                    client_upd.bot_username = bot_info.username
                    await update_session.commit()
        except Exception as exc:
            logger.warning("Failed to get bot username: %s", exc)

    # Notify client via their own bot
    if railway_url and admin_telegram_id.strip().isdigit() and bot_token.strip():
        try:
            from aiogram import Bot
            client_bot = Bot(token=bot_token.strip())
            await client_bot.send_message(
                int(admin_telegram_id.strip()),
                f"\U0001f389 <b>\u0412\u0430\u0448 \u043c\u0430\u0433\u0430\u0437\u0438\u043d \u0433\u043e\u0442\u043e\u0432\u0438\u0439!</b>\n\n"
                f"\U0001f3ea <b>{business_name}</b>\n\n"
                f"\U0001f310 \u0421\u0430\u0439\u0442: {railway_url}\n\n"
                f"\U0001f916 \u0412\u0456\u0434\u043a\u0440\u0438\u0439\u0442\u0435 \u043c\u0435\u043d\u044e \u0431\u043e\u0442\u0430 \u0442\u0430 \u043f\u043e\u0447\u043d\u0456\u0442\u044c \u0434\u043e\u0434\u0430\u0432\u0430\u0442\u0438 \u0442\u043e\u0432\u0430\u0440\u0438!\n\n"
                f"\U0001f4e6 \u041a\u043d\u043e\u043f\u043a\u0438 \u0431\u043e\u0442\u0430:\n"
                f"\u2022 \u0422\u043e\u0432\u0430\u0440\u0438 \u2014 \u0434\u043e\u0434\u0430\u0442\u0438 \u0442\u0430 \u0440\u0435\u0434\u0430\u0433\u0443\u0432\u0430\u0442\u0438 \u0442\u043e\u0432\u0430\u0440\u0438\n"
                f"\u2022 \u0421\u0430\u0439\u0442 \u2014 \u043d\u0430\u043b\u0430\u0448\u0442\u0443\u0432\u0430\u043d\u043d\u044f \u043d\u0430\u0437\u0432\u0438 \u0442\u0430 \u043a\u043e\u043d\u0442\u0430\u043a\u0442\u0456\u0432\n"
                f"\u2022 \u0417\u0430\u043c\u043e\u0432\u043b\u0435\u043d\u043d\u044f \u2014 \u043f\u0435\u0440\u0435\u0433\u043b\u044f\u0434 \u043d\u043e\u0432\u0438\u0445 \u0437\u0430\u043c\u043e\u0432\u043b\u0435\u043d\u044c",
                parse_mode="HTML"
            )
            await client_bot.session.close()
        except Exception as exc:
            logger.warning("Failed to notify client %s via their bot: %s", admin_telegram_id, exc)

    return RedirectResponse(
        url=f"/onboarding-success/{result.slug}", status_code=303
    )


_SLUG_CLEAN_RE = re.compile(r"[^a-z0-9]+")


async def _allocate_slug(session, business_name: str) -> str:
    TRANSLIT = {
        'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','є':'ye','ё':'yo',
        'ж':'zh','з':'z','и':'i','і':'i','ї':'yi','й':'y','к':'k','л':'l',
        'м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u',
        'ф':'f','х':'kh','ц':'ts','ч':'ch','ш':'sh','щ':'shch','ъ':'',
        'ы':'y','ь':'','э':'e','ю':'yu','я':'ya',
        'А':'a','Б':'b','В':'v','Г':'g','Д':'d','Е':'e','Є':'ye','Ё':'yo',
        'Ж':'zh','З':'z','И':'i','І':'i','Ї':'yi','Й':'y','К':'k','Л':'l',
        'М':'m','Н':'n','О':'o','П':'p','Р':'r','С':'s','Т':'t','У':'u',
        'Ф':'f','Х':'kh','Ц':'ts','Ч':'ch','Ш':'sh','Щ':'shch','Ъ':'',
        'Ы':'y','Ь':'','Э':'e','Ю':'yu','Я':'ya',
    }

    base = (business_name or "").strip()

    # Transliterate Cyrillic
    result = ""
    for char in base:
        result += TRANSLIT.get(char, char)

    # Clean: lowercase, replace spaces and special chars with dash
    import re
    result = result.lower()
    result = re.sub(r"[^a-z0-9]+", "-", result)
    result = result.strip("-")

    if not result:
        result = "client"

    result = result[:55]

    candidate = result
    suffix = 2
    while True:
        existing = (
            await session.execute(select(Client.id).where(Client.slug == candidate))
        ).scalar_one_or_none()
        if existing is None:
            return candidate
        candidate = f"{result}-{suffix}"[:60]
        suffix += 1
        if suffix > 9999:
            raise RuntimeError("could not allocate unique slug")


def sanitize_str(s) -> str:
    if not s:
        return ""
    result = ""
    for char in str(s):
        code = ord(char)
        if code > 0xFFFF:
            result += f"&#{code};"
        elif 0xD800 <= code <= 0xDFFF:
            result += ""
        else:
            result += char
    return result


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

        # Use client's own bot if available
        if client.bot_username:
            cms_url = f"https://t.me/{client.bot_username}"
        else:
            platform_bot_username = getattr(
                request.app.state, "platform_bot_username", None
            )
            cms_url = (
                f"https://t.me/{platform_bot_username}"
                if platform_bot_username else None
            )

        # Use Railway URL if available, otherwise fallback to platform URL
        railway_url = client.bot_admin_ids if client.bot_admin_ids and client.bot_admin_ids.startswith("http") else None
        site_url = railway_url or (str(request.base_url).rstrip("/") + f"/site/{client.slug}")

    _token_suffix = f"?token={client.dashboard_token}" if client.dashboard_token else ""
    dashboard_url = str(request.base_url).rstrip("/") + f"/dashboard/{client.slug}{_token_suffix}"

    data = {
        "business_name": _clean(client.business_name) or "",
        "site_url": _clean(site_url) or "",
        "bot_username": _clean(client.bot_username),
        "template": _clean(client.template_name) or "",
        "plan": _clean(plan_name) or "",
        "trial_expires_at": _clean(trial_expires) or "",
        "cms_url": _clean(cms_url) if cms_url else None,
        "dashboard_url": dashboard_url,
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


# ---------------------------------------------------------------------------
# Products web-admin
# ---------------------------------------------------------------------------

@router.get("/dashboard/{slug}/products", response_class=HTMLResponse)
async def dashboard_products(
    request: Request,
    slug: str,
    token: Optional[str] = None,
    edit_id: Optional[int] = None,
    success: Optional[str] = None,
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
                {"request": request, "t": t, "lang": chosen,
                 "supported_langs": SUPPORTED_LANGS, "slug": slug},
                status_code=404,
            )

        _check_dashboard_token(client, token)

        products = (
            await session.execute(
                select(Product)
                .where(Product.client_id == client.id)
                .order_by(Product.id.desc())
            )
        ).scalars().all()

        edit_product = None
        if edit_id:
            ep = await session.get(Product, edit_id)
            if ep and ep.client_id == client.id:
                edit_product = ep

    return templates.TemplateResponse(
        "dashboard_products.html",
        {
            "request": request,
            "t": t,
            "lang": chosen,
            "supported_langs": SUPPORTED_LANGS,
            "client": {"business_name": client.business_name, "slug": client.slug},
            "products": products,
            "edit_product": edit_product,
            "success": success,
        },
    )


@router.post("/dashboard/{slug}/products")
async def dashboard_products_add(
    request: Request,
    slug: str,
    token: Optional[str] = None,
    name: str = Form(...),
    price: str = Form("0"),
    category: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    image_url: Optional[str] = Form(None),
    brand: Optional[str] = Form(None),
    old_price: Optional[str] = Form(None),
    specs: Optional[str] = Form(None),
    group_name: Optional[str] = Form(None),
) -> RedirectResponse:
    async with AsyncSessionLocal() as session:
        client = await session.scalar(select(Client).where(Client.slug == slug))
        if client is None:
            raise HTTPException(status_code=404)
        _check_dashboard_token(client, token)
        try:
            price_val = float(price.replace(",", ".")) if price else 0.0
        except ValueError:
            price_val = 0.0
        try:
            old_price_val = float(old_price.replace(",", ".")) if old_price else None
        except ValueError:
            old_price_val = None
        product = Product(
            client_id=client.id,
            name=name.strip(),
            price=price_val,
            category=category.strip() if category else None,
            description=description.strip() if description else None,
            image_url=image_url.strip() if image_url else None,
            brand=brand.strip() if brand else None,
            old_price=old_price_val,
            specs=specs.strip() if specs else None,
            group_name=group_name.strip() if group_name else None,
        )
        session.add(product)
        await session.commit()
    _tp = f"&token={token}" if token else ""
    return RedirectResponse(f"/dashboard/{slug}/products?success=added{_tp}", status_code=303)


@router.post("/dashboard/{slug}/products/{product_id}/delete")
async def dashboard_products_delete(
    request: Request,
    slug: str,
    product_id: int,
    token: Optional[str] = None,
) -> RedirectResponse:
    async with AsyncSessionLocal() as session:
        client = await session.scalar(select(Client).where(Client.slug == slug))
        if client is None:
            raise HTTPException(status_code=404)
        _check_dashboard_token(client, token)
        product = await session.get(Product, product_id)
        if product and product.client_id == client.id:
            await session.delete(product)
            await session.commit()
    _tp = f"&token={token}" if token else ""
    return RedirectResponse(f"/dashboard/{slug}/products?success=deleted{_tp}", status_code=303)


@router.post("/dashboard/{slug}/products/{product_id}/toggle")
async def dashboard_products_toggle(
    request: Request,
    slug: str,
    product_id: int,
    token: Optional[str] = None,
) -> RedirectResponse:
    async with AsyncSessionLocal() as session:
        client = await session.scalar(select(Client).where(Client.slug == slug))
        if client is None:
            raise HTTPException(status_code=404)
        _check_dashboard_token(client, token)
        product = await session.get(Product, product_id)
        if product and product.client_id == client.id:
            product.is_available = not product.is_available
            await session.commit()
    _tp = f"?token={token}" if token else ""
    return RedirectResponse(f"/dashboard/{slug}/products{_tp}", status_code=303)


@router.post("/dashboard/{slug}/products/{product_id}/edit")
async def dashboard_products_edit(
    request: Request,
    slug: str,
    product_id: int,
    token: Optional[str] = None,
    name: str = Form(...),
    price: str = Form("0"),
    category: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    image_url: Optional[str] = Form(None),
    brand: Optional[str] = Form(None),
    old_price: Optional[str] = Form(None),
    specs: Optional[str] = Form(None),
    group_name: Optional[str] = Form(None),
) -> RedirectResponse:
    async with AsyncSessionLocal() as session:
        client = await session.scalar(select(Client).where(Client.slug == slug))
        if client is None:
            raise HTTPException(status_code=404)
        _check_dashboard_token(client, token)
        product = await session.get(Product, product_id)
        if product and product.client_id == client.id:
            try:
                price_val = float(price.replace(",", ".")) if price else 0.0
            except ValueError:
                price_val = float(product.price)
            try:
                old_price_val = float(old_price.replace(",", ".")) if old_price else None
            except ValueError:
                old_price_val = None
            product.name = name.strip()
            product.price = price_val
            product.category = category.strip() if category else None
            product.description = description.strip() if description else None
            product.image_url = image_url.strip() if image_url else None
            product.brand = brand.strip() if brand else None
            product.old_price = old_price_val
            product.specs = specs.strip() if specs else None
            product.group_name = group_name.strip() if group_name else None
            await session.commit()
    _tp = f"&token={token}" if token else ""
    return RedirectResponse(f"/dashboard/{slug}/products?success=updated{_tp}", status_code=303)


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
    token: Optional[str] = None,
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

        _check_dashboard_token(client, token)

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
                "host": f"{client.slug}.shopplatform.app",
                "status": "pending",
            },
        }

        products_count = await session.scalar(
            select(func.count()).where(Product.client_id == client.id)
        ) or 0
        ctx["products_count"] = products_count

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
        client_settings = await session.scalar(
            select(ClientSettings).where(ClientSettings.client_id == client.id)
        )
        products = [
            {
                "id": p.id,
                "group_name": p.group_name,
                "category": p.category,
                "name": p.name,
                "description": p.description,
                "brand": p.brand,
                "price": float(p.price) if p.price is not None else 0.0,
                "old_price": float(p.old_price) if p.old_price is not None else None,
                "specs": p.specs,
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
            "theme_name": (client_settings.theme_name if client_settings else None) or "light_red",
            "shop_title": (client_settings.shop_title if client_settings else None) or client.business_name,
            "phone": client_settings.phone if client_settings else None,
            "address": client_settings.address if client_settings else None,
            "telegram_url": client_settings.telegram_url if client_settings else None,
            "instagram_url": client_settings.instagram_url if client_settings else None,
            "logo_url": client_settings.logo_url if client_settings else None,
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
# Product detail page
# ---------------------------------------------------------------------------


@router.get("/site/{slug}/product/{product_id}", response_class=HTMLResponse)
async def client_site_product(
    request: Request,
    slug: str,
    product_id: int,
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
                {"request": request, "t": t, "lang": chosen,
                 "supported_langs": SUPPORTED_LANGS, "slug": slug},
                status_code=404,
            )

        product = await session.scalar(
            select(Product)
            .where(Product.id == product_id)
            .where(Product.client_id == client.id)
        )
        if product is None:
            return templates.TemplateResponse(
                "404.html",
                {"request": request, "t": t, "lang": chosen,
                 "supported_langs": SUPPORTED_LANGS, "slug": slug},
                status_code=404,
            )
        client_settings = await session.scalar(
            select(ClientSettings).where(ClientSettings.client_id == client.id)
        )

    client_data = {
        "id": client.id,
        "business_name": client.business_name,
        "slug": client.slug,
        "template_name": client.template_name,
        "theme_name": (client_settings.theme_name if client_settings else None) or "light_red",
        "shop_title": (client_settings.shop_title if client_settings else None) or client.business_name,
        "phone": client_settings.phone if client_settings else None,
        "address": client_settings.address if client_settings else None,
        "telegram_url": client_settings.telegram_url if client_settings else None,
        "instagram_url": client_settings.instagram_url if client_settings else None,
        "logo_url": client_settings.logo_url if client_settings else None,
    }
    product_data = {
        "id": product.id,
        "group_name": _clean(product.group_name),
        "name": _clean(product.name),
        "category": _clean(product.category),
        "description": _clean(product.description),
        "brand": _clean(product.brand),
        "price": float(product.price) if product.price is not None else 0.0,
        "old_price": float(product.old_price) if product.old_price is not None else None,
        "specs": _clean(product.specs),
        "image_url": product.image_url,
        "is_available": product.is_available,
    }

    template_name = (client_data["template_name"] or "").strip() or "technovlada"
    product_tpl = f"sites/{template_name}/product.html"
    if not os.path.exists(os.path.join("templates", product_tpl)):
        return RedirectResponse(url=f"/site/{slug}")

    return templates.TemplateResponse(
        product_tpl,
        {
            "request": request,
            "t": t,
            "lang": chosen,
            "client": client_data,
            "product": product_data,
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

    # Save order to database
    import json as _json
    async with AsyncSessionLocal() as session:
        _total = sum(
            float(item.get("price", 0)) * int(item.get("qty", 1))
            for item in data.items
        )
        order_obj = Order(
            client_id=client.id,
            customer_name=(_clean(data.name) or "")[:255],
            customer_phone=data.phone[:64],
            customer_city=(data.city[:255] if data.city else None),
            comment=(data.comment or None),
            items_json=_json.dumps(data.items, ensure_ascii=False),
            total=_total,
            status="new",
        )
        session.add(order_obj)
        await session.commit()

    # Notify client admin via Telegram
    bot = getattr(request.app.state, "bot", None)
    if bot and client.admin_telegram_id:
        lines = []
        for item in data.items:
            name = item.get("name", "?")
            qty = item.get("qty", 1)
            price = item.get("price", 0)
            lines.append(f"• {name} × {qty} — {price} грн")
        items_text = "\n".join(lines)
        total = sum(
            item.get("price", 0) * item.get("qty", 1)
            for item in data.items
        )
        msg = (
            "🛒 Нове замовлення!\n\n"
            f"👤 {data.name}\n"
            f"📞 {data.phone}\n"
            f"🏙 {data.city or '—'}\n\n"
            f"📦 Товари:\n{items_text}\n\n"
            f"💰 Разом: {total} грн\n"
            f"💬 {data.comment or '—'}"
        )
        try:
            await bot.send_message(client.admin_telegram_id, msg)
        except Exception as e:
            logger.warning("Failed to notify client %s: %s", slug, e)

    return {"ok": True}
