from fastapi import APIRouter

from app.api.routes.internal_linking import router as internal_linking_router
from app.api.routes.system import router as system_router


api_router = APIRouter()
api_router.include_router(system_router)
api_router.include_router(internal_linking_router)
