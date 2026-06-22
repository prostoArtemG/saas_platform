"""Auth routes: register, login, logout, account page."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import Client, Plan, Subscription, User
from app.site.routes import get_public_site_url

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# ---------------------------------------------------------------------------
# Password hashing — uses bcrypt directly (passlib 1.7.4 is incompatible with
# bcrypt >= 4.0 because bcrypt.__about__ was removed; avoid passlib entirely).
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    try:
        import bcrypt as _bcrypt  # type: ignore
        return _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")
    except ImportError:
        logger.error("bcrypt not installed — password hashing unavailable")
        raise


def verify_password(password: str, hashed: str) -> bool:
    try:
        import bcrypt as _bcrypt  # type: ignore
        return _bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

async def get_current_user(request: Request) -> Optional[User]:
    """Return the authenticated User or None."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    async with AsyncSessionLocal() as session:
        return await session.get(User, user_id)


def _is_admin(user: Optional[User]) -> bool:
    if user is None:
        return False
    if user.role == "admin":
        return True
    if user.email and settings.admin_emails:
        return user.email.lower() in [e.lower() for e in settings.admin_emails]
    return False


def _render_error(request: Request, template: str, error: str, **ctx):
    return templates.TemplateResponse(
        template,
        {"request": request, "error": error, **ctx},
        status_code=400,
    )


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    user = await get_current_user(request)
    if user:
        return RedirectResponse("/account", status_code=302)
    return templates.TemplateResponse("auth_register.html", {"request": request})


@router.post("/register", response_class=HTMLResponse)
async def register_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    name: str = Form(""),
):
    email = email.strip().lower()
    name = name.strip()[:128]

    if not email or not password:
        return _render_error(request, "auth_register.html",
                             "Email та пароль обов'язкові.", email=email, name=name)
    if len(password) < 6:
        return _render_error(request, "auth_register.html",
                             "Пароль має бути не менше 6 символів.", email=email, name=name)
    if "@" not in email or "." not in email.split("@")[-1]:
        return _render_error(request, "auth_register.html",
                             "Введіть коректний email.", email=email, name=name)

    try:
        async with AsyncSessionLocal() as session:
            existing = await session.scalar(select(User).where(User.email == email))
            if existing:
                return _render_error(request, "auth_register.html",
                                     "Цей email вже зареєстровано. Увійдіть.", email=email, name=name)

            user = User(
                email=email,
                password_hash=hash_password(password),
                name=name or None,
                role="client",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

        request.session["user_id"] = user.id
        logger.info("New user registered: email=%s id=%s", email, user.id)
        return RedirectResponse("/account", status_code=302)

    except Exception:
        logger.exception("Register failed for email=%s", email)
        return _render_error(request, "auth_register.html",
                             "Помилка сервера. Спробуйте пізніше або зверніться до підтримки.",
                             email=email, name=name)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = await get_current_user(request)
    if user:
        return RedirectResponse("/account", status_code=302)
    next_url = request.query_params.get("next", "")
    return templates.TemplateResponse("auth_login.html", {"request": request, "next": next_url})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next_url: str = Form(""),
):
    email = email.strip().lower()

    if not email or not password:
        return _render_error(request, "auth_login.html",
                             "Введіть email та пароль.", email=email)

    try:
        async with AsyncSessionLocal() as session:
            user = await session.scalar(select(User).where(User.email == email))

        if not user or not user.password_hash or not verify_password(password, user.password_hash):
            return _render_error(request, "auth_login.html",
                                 "Невірний email або пароль.", email=email)

        request.session["user_id"] = user.id

        # Update last_login_at
        async with AsyncSessionLocal() as session:
            from datetime import datetime, timezone
            db_user = await session.get(User, user.id)
            if db_user:
                db_user.last_login_at = datetime.now(timezone.utc)
                await session.commit()

        logger.info("User logged in: email=%s id=%s", email, user.id)
        redirect_to = next_url if next_url and next_url.startswith("/") else "/account"
        return RedirectResponse(redirect_to, status_code=302)

    except Exception:
        logger.exception("Login failed for email=%s", email)
        return _render_error(request, "auth_login.html",
                             "Помилка сервера. Спробуйте пізніше або зверніться до підтримки.",
                             email=email)


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)

@router.get("/logout")
async def logout_get(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# ---------------------------------------------------------------------------
# Account — client's personal cabinet
# ---------------------------------------------------------------------------

@router.get("/account", response_class=HTMLResponse)
async def account_page(request: Request):
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/login?next=/account", status_code=302)

    async with AsyncSessionLocal() as session:
        clients_rows = (
            await session.execute(
                select(Client)
                .where(Client.user_id == user.id)
                .options(
                    selectinload(Client.subscriptions).selectinload(Subscription.plan),
                    selectinload(Client.plan),
                )
                .order_by(Client.created_at.desc())
            )
        ).scalars().all()

    sites = []
    for c in clients_rows:
        # Active subscription
        sub = next(
            (s for s in sorted(c.subscriptions, key=lambda x: x.id, reverse=True)
             if s.status in ("active", "trial")),
            None,
        )
        plan = (sub.plan if sub else None) or c.plan
        sites.append({
            "id": c.id,
            "business_name": c.business_name,
            "slug": c.slug,
            "template_name": c.template_name,
            "bot_mode": c.bot_mode or "shared",
            "status": c.status,
            "deployment_status": c.deployment_status,
            "railway_url": c.railway_url,
            "dashboard_token": c.dashboard_token,
            "bot_username": c.bot_username,
            "plan_name": plan.name if plan else "—",
            "site_url": get_public_site_url(
                c, settings.platform_domain,
                fallback_base=str(request.base_url).rstrip("/"),
            ),
        })

    return templates.TemplateResponse("account.html", {
        "request": request,
        "user": user,
        "is_admin": _is_admin(user),
        "sites": sites,
        "base_url": str(request.base_url).rstrip("/"),
    })


# ---------------------------------------------------------------------------
# Claim site by dashboard_token — link existing site to current account
# ---------------------------------------------------------------------------

@router.post("/account/claim", response_class=HTMLResponse)
async def claim_site(
    request: Request,
    dashboard_token: str = Form(""),
):
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/login?next=/account", status_code=302)

    dashboard_token = dashboard_token.strip()
    if not dashboard_token:
        return RedirectResponse("/account?error=token_empty", status_code=302)

    async with AsyncSessionLocal() as session:
        client = await session.scalar(
            select(Client).where(Client.dashboard_token == dashboard_token)
        )
        if client is None:
            return RedirectResponse("/account?error=token_not_found", status_code=302)
        if client.user_id and client.user_id != user.id:
            return RedirectResponse("/account?error=token_taken", status_code=302)
        client.user_id = user.id
        await session.commit()

    logger.info("User %s claimed client slug=%s via dashboard_token", user.id, client.slug)
    return RedirectResponse("/account?claimed=1", status_code=302)
