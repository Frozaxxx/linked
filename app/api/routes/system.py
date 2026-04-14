from fastapi import APIRouter


router = APIRouter(tags=["СЃРёСЃС‚РµРјР°"])


@router.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
