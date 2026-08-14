"""JD import router exports."""

from fastapi import APIRouter

from app.jd_import.routers.agent import router as agent_router
from app.jd_import.routers.imports import router as crud_router

router = APIRouter()
router.include_router(agent_router)
router.include_router(crud_router)

__all__ = ["router"]
