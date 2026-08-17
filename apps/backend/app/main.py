"""FastAPI application entry point."""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

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
from app.jd_import import JDImportAdapter
from app.jd_import import router as jd_import_router
from app.jd_import.agent.model import LangChainJDImportModel
from app.jd_import.graph import JDImportGraphDependencies
from app.jd_import.sources import PlaywrightMCPSourceProvider, UrlPolicy
from app.pdf import close_pdf_renderer
from app.resume_generation import router as resume_generation_router
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

_business_adapters_registered = False


def _register_business_adapters() -> None:
    """在聊天运行库启动前注册唯一的生产业务 Adapter。"""
    global _business_adapters_registered
    if not _business_adapters_registered:
        register_adapter(ExperienceAdapter())
        policy = UrlPolicy()
        register_adapter(JDImportAdapter(JDImportGraphDependencies(
            model=LangChainJDImportModel(),
            page_sources=PlaywrightMCPSourceProvider(
                settings.playwright_mcp_url,
                egress_secured=settings.playwright_mcp_egress_secured,
                policy=policy,
                timeout_seconds=settings.playwright_mcp_timeout_seconds,
                max_chars=settings.playwright_mcp_max_chars,
            ),
            url_policy=policy,
        )))
        _business_adapters_registered = True


def _configure_application_logging() -> None:
    """配置应用日志级别，并把后端日志滚动写入 data/logs。"""
    numeric_level = getattr(logging, settings.log_level, logging.INFO)
    app_logger = logging.getLogger("app")
    app_logger.setLevel(numeric_level)

    if any(
        getattr(handler, "_resume_matcher_file_handler", False)
        for handler in app_logger.handlers
    ):
        return

    logs_dir = settings.data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        logs_dir / "backend.log",
        maxBytes=20 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        )
    )
    file_handler._resume_matcher_file_handler = True  # type: ignore[attr-defined]
    app_logger.addHandler(file_handler)


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
app.include_router(resume_generation_router, prefix="/api/v1")


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
