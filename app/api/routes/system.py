from fastapi import APIRouter


router = APIRouter(tags=["система"])


@router.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
