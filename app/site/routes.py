from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def index() -> dict:
    return {"service": "saas_platform", "status": "ok"}


@router.get("/health")
async def health() -> dict:
    return {"status": "healthy"}
