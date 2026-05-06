from fastapi import APIRouter, Cookie, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.site.i18n import DEFAULT_LANG, SUPPORTED_LANGS, get_t

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    lang: str | None = None,
    lang_cookie: str | None = Cookie(default=None, alias="lang"),
) -> HTMLResponse:
    chosen = lang or lang_cookie or DEFAULT_LANG
    if chosen not in SUPPORTED_LANGS:
        chosen = DEFAULT_LANG

    response = templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "t": get_t(chosen),
            "lang": chosen,
            "supported_langs": SUPPORTED_LANGS,
        },
    )
    response.set_cookie("lang", chosen, max_age=60 * 60 * 24 * 365, httponly=False, samesite="lax")
    return response


@router.get("/health")
async def health() -> dict:
    return {"status": "healthy"}
