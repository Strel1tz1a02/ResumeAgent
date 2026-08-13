"""FastAPI application entry point."""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

# Fix for Windows: Use ProactorEventLoop for subprocess support (Playwright)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.ai_chat import close_ai_chat, register_adapter, start_ai_chat
from app.config import settings
from app.database import db
from app.experience import ExperienceAdapter
from app.experience.routers import ai_chat_router as experience_ai_chat_router
from app.experience.routers import experiences_router
from app.jd_import import router as jd_import_router
from app.pdf import close_pdf_renderer
from app.routers import (
    applications_router,
    config_router,
    enrichment_router,
    health_router,
    jobs_router,
    resume_wizard_router,
    resumes_router,
)

logger = logging.getLogger(__name__)

_experience_adapter_registered = False


def _register_business_adapters() -> None:
    """在聊天运行库启动前注册唯一的生产业务 Adapter。"""
    global _experience_adapter_registered
    if not _experience_adapter_registered:
        register_adapter(ExperienceAdapter())
        _experience_adapter_registered = True


def _configure_application_logging() -> None:
    """Set application log level from configuration."""
    numeric_level = getattr(logging, settings.log_level, logging.INFO)
    logging.getLogger("app").setLevel(numeric_level)


_configure_application_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 应用的“生命周期管理函数”，负责在服务启动时初始化资源（yeild 前），在服务关闭时释放资源（yeild 后）"""
    # Startup
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    # Import a legacy TinyDB database into SQLite if present (idempotent).
    # Fail-fast on error: starting with an empty DB would look like data loss.
    from app.scripts.migrate_tinydb_to_sqlite import migrate as migrate_tinydb

    result = await migrate_tinydb()
    if result.get("status") == "migrated":
        logger.info("Startup data migration: %s", result)
    # Fold any legacy plaintext API keys into the encrypted store (idempotent,
    # non-clobbering), then strip them from config.json.
    from app.config import migrate_legacy_keys

    migrate_legacy_keys()
    _register_business_adapters()
    await start_ai_chat()
    # PDF renderer uses lazy initialization - will initialize on first use
    # await init_pdf_renderer()
    yield
    # Shutdown - wrap each cleanup in try-except to ensure all resources are released
    try:
        await close_ai_chat()
    except Exception as e:
        logger.error(f"Error closing AI Chat: {e}")

    try:
        await close_pdf_renderer()
    except Exception as e:
        logger.error(f"Error closing PDF renderer: {e}")

    try:
        await db.close()
    except Exception as e:
        logger.error(f"Error closing database: {e}")


app = FastAPI(
    title="Resume Matcher API",
    description="AI-powered resume tailoring for job descriptions",
    version=__version__,
    lifespan=lifespan,
)

# CORS middleware - origins configurable via CORS_ORIGINS env var
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.effective_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health_router, prefix="/api/v1")
app.include_router(config_router, prefix="/api/v1")
app.include_router(resumes_router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")
app.include_router(enrichment_router, prefix="/api/v1")
app.include_router(applications_router, prefix="/api/v1")
app.include_router(resume_wizard_router, prefix="/api/v1")
app.include_router(experiences_router, prefix="/api/v1")
app.include_router(experience_ai_chat_router, prefix="/api/v1")
app.include_router(jd_import_router, prefix="/api/v1")


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Resume Matcher API",
        "version": __version__,
        "docs": "/docs",
    }


def main():
    """Entry point for the project.scripts console script."""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
    )


if __name__ == "__main__":
    main()
