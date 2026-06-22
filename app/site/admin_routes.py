"""Platform owner admin dashboard."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import Client, Plan, Subscription, User
from app.site.auth import _is_admin, get_current_user
from app.site.routes import get_public_site_url

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# Rough MRR lookup (plan name prefix → monthly USD)
_PLAN_MRR: dict[str, float] = {
    "starter": 15.0,
    "pro": 20.0,
    "premium": 30.0,
    "business": 25.0,
    "full": 0.0,   # one-time buyout
    "trial": 0.0,
    "free": 0.0,
}


def _plan_mrr(plan_name: str) -> float:
    p = (plan_name or "").lower()
    for key, val in _PLAN_MRR.items():
        if p.startswith(key):
            return val
    return 0.0


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    user = await get_current_user(request)
    if not _is_admin(user):
        return RedirectResponse("/login?next=/admin", status_code=302)

    async with AsyncSessionLocal() as session:
        # --- Users ---
        total_users = await session.scalar(select(func.count()).select_from(User)) or 0

        # --- Clients ---
        all_clients = (
            await session.execute(
                select(Client)
                .outerjoin(Client.subscriptions)
                .outerjoin(Subscription.plan)
            )
        ).scalars().unique().all()

        # Subscriptions map: client_id → best active sub
        subs_rows = (
            await session.execute(
                select(Subscription).where(
                    Subscription.status.in_(["active", "trial"])
                )
            )
        ).scalars().all()
        sub_map: dict[int, Subscription] = {}
        for s in subs_rows:
            existing = sub_map.get(s.client_id)
            if existing is None or s.id > existing.id:
                sub_map[s.client_id] = s

        # Plan name lookup
        plans_map: dict[int, Plan] = {}
        for p in (await session.execute(select(Plan))).scalars().all():
            plans_map[p.id] = p

        # User lookup
        users_map: dict[int, User] = {}
        for u in (await session.execute(select(User))).scalars().all():
            users_map[u.id] = u

    # --- Compute stats ---
    total_clients = len(all_clients)
    count_active = count_trial = count_expired = count_personal = count_shared = 0
    count_by_plan: dict[str, int] = defaultdict(int)
    count_by_template: dict[str, int] = defaultdict(int)
    mrr = 0.0
    failed_deploys = []

    client_rows = []
    for c in sorted(all_clients, key=lambda x: x.created_at, reverse=True):
        sub = sub_map.get(c.id)
        plan = None
        if sub:
            plan = plans_map.get(sub.plan_id)
        if plan is None and c.plan_id:
            plan = plans_map.get(c.plan_id)

        plan_name = plan.name if plan else "—"
        sub_status = sub.status if sub else "expired"

        if sub_status == "active":
            count_active += 1
            mrr += _plan_mrr(plan_name)
        elif sub_status == "trial":
            count_trial += 1
        else:
            count_expired += 1

        if (c.bot_mode or "shared") == "personal":
            count_personal += 1
        else:
            count_shared += 1

        count_by_plan[plan_name] += 1
        count_by_template[c.template_name or "unknown"] += 1

        if c.deployment_status == "failed" and c.deployment_error:
            failed_deploys.append({
                "slug": c.slug,
                "business_name": c.business_name,
                "error": c.deployment_error[:200],
                "created_at": c.created_at,
            })

        owner = users_map.get(c.user_id) if c.user_id else None
        site_url = get_public_site_url(
            c, settings.platform_domain,
            fallback_base="",
        )
        dashboard_url = f"/dashboard/{c.slug}" + (
            f"?token={c.dashboard_token}" if c.dashboard_token else ""
        )

        client_rows.append({
            "id": c.id,
            "slug": c.slug,
            "business_name": c.business_name,
            "owner_email": owner.email if owner else "—",
            "plan_name": plan_name,
            "bot_mode": c.bot_mode or "shared",
            "sub_status": sub_status,
            "status": c.status,
            "created_at": c.created_at,
            "site_url": site_url,
            "dashboard_url": dashboard_url,
            "deployment_status": c.deployment_status,
            "deployment_error": (c.deployment_error or "")[:120],
            "template_name": c.template_name or "—",
        })

    # Last 10
    recent_clients = client_rows[:10]

    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request,
        "user": user,
        "stats": {
            "total_clients": total_clients,
            "total_users": total_users,
            "active": count_active,
            "trial": count_trial,
            "expired": count_expired,
            "personal": count_personal,
            "shared": count_shared,
            "mrr": round(mrr, 2),
        },
        "recent_clients": recent_clients,
        "all_clients": client_rows,
        "failed_deploys": failed_deploys[:20],
        "by_plan": dict(sorted(count_by_plan.items(), key=lambda x: -x[1])),
        "by_template": dict(sorted(count_by_template.items(), key=lambda x: -x[1])),
    })
