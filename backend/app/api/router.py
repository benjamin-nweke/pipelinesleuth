"""
Main API Router grouping all endpoint sub-routers
"""
from fastapi import APIRouter
from backend.app.api.projects import router as projects_router
from backend.app.api.investigations import router as investigations_router
from backend.app.api.dashboard import router as dashboard_router
from backend.app.api.settings import router as settings_router
from backend.app.api.demos import router as demos_router
from backend.app.api.reports import router as reports_router

api_router = APIRouter()
api_router.include_router(dashboard_router)
api_router.include_router(projects_router)
api_router.include_router(investigations_router)
api_router.include_router(settings_router)
api_router.include_router(demos_router)
api_router.include_router(reports_router)

