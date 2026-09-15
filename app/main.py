"""FastAPI 应用入口。

Phase 13：挂载统一 API 层（/api/chat、/api/chat/stream、/api/documents、/api/conversations）。
Phase 14：托管前端静态页面（/ 客服 UI，/admin 知识库管理）。
"""

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.config import get_settings
from app.exceptions import NotFoundError, ValidationError
from app.logging_config import setup_logging

settings = get_settings()
setup_logging(settings.log_level)

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
)
app.include_router(api_router, prefix="/api")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
def health() -> dict[str, str]:
    """健康检查端点。"""
    logger.debug("health check requested")
    return {"status": "ok", "service": settings.app_name}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """客服前端页面。"""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/admin", include_in_schema=False)
def admin() -> FileResponse:
    """知识库管理页面。"""
    return FileResponse(STATIC_DIR / "admin.html")


@app.exception_handler(ValidationError)
async def validation_exception_handler(request: Request, exc: ValidationError) -> JSONResponse:
    logger.warning("validation error: %s", exc)
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(NotFoundError)
async def not_found_exception_handler(request: Request, exc: NotFoundError) -> JSONResponse:
    logger.warning("not found: %s", exc)
    return JSONResponse(status_code=404, content={"detail": str(exc)})
